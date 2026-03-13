from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from typing import Dict, List

from django.utils import timezone

from managementApp.models import (
    AssignSubjectsToClass,
    ExamSubjectComponentRule,
    GradingBand,
    MarkOfStudentsByExam,
    PassPolicy,
    ProgressReport,
    ProgressReportSnapshot,
    StudentAttendance,
    StudentExamComponentMark,
    SubjectTeacherRemark,
    TermTeacherRemark,
)


def _default_grade_from_percentage(value):
    if value is None:
        return 'N/A'
    if value >= 90:
        return 'A+'
    if value >= 80:
        return 'A'
    if value >= 70:
        return 'B+'
    if value >= 60:
        return 'B'
    if value >= 50:
        return 'C'
    if value >= 40:
        return 'D'
    return 'F'


def _grade_from_policy(percentage, policy):
    if percentage is None:
        return 'N/A'
    if not policy:
        return _default_grade_from_percentage(percentage)

    bands = list(
        GradingBand.objects.filter(
            isDeleted=False,
            sessionID_id=policy.sessionID_id,
            policyID_id=policy.id,
        ).order_by('displayOrder', 'minPercentage', 'id')
    )
    for band in bands:
        min_value = float(band.minPercentage or 0)
        max_value = float(band.maxPercentage or 0)
        if min_value <= percentage <= max_value:
            return band.gradeLabel or 'N/A'
    return _default_grade_from_percentage(percentage)


def _attendance_summary(session_id, student_id):
    attendance_qs = StudentAttendance.objects.filter(
        isDeleted=False,
        sessionID_id=session_id,
        studentID_id=student_id,
        isHoliday=False,
    )
    total_days = attendance_qs.count()
    present_days = attendance_qs.filter(isPresent=True).count()
    attendance_pct = round((present_days * 100.0 / total_days), 2) if total_days > 0 else None
    return {
        'present_days': present_days,
        'total_days': total_days,
        'attendance_percentage': attendance_pct,
    }


def _load_subject_remarks(session_id, student_id, exam_id):
    rows = SubjectTeacherRemark.objects.filter(
        isDeleted=False,
        sessionID_id=session_id,
        studentID_id=student_id,
        examID_id=exam_id,
    )
    return {row.subjectID_id: row for row in rows}


def _load_term_remark(session_id, student_id, exam_id):
    return TermTeacherRemark.objects.filter(
        isDeleted=False,
        sessionID_id=session_id,
        studentID_id=student_id,
        examID_id=exam_id,
    ).first()


def _load_published_snapshot(session_id, student_id, exam_id):
    report_obj = ProgressReport.objects.filter(
        isDeleted=False,
        sessionID_id=session_id,
        studentID_id=student_id,
        examID_id=exam_id,
        status='published',
    ).first()
    if not report_obj:
        return None

    snapshot = ProgressReportSnapshot.objects.filter(
        isDeleted=False,
        sessionID_id=session_id,
        progressReportID_id=report_obj.id,
        snapshotType='published',
        isCurrent=True,
    ).order_by('-datetime').first()
    if not snapshot or not isinstance(snapshot.payload, dict):
        return None

    payload = snapshot.payload
    exam_date_value = payload.get('exam_date')
    if isinstance(exam_date_value, str):
        try:
            payload['exam_date'] = datetime.fromisoformat(exam_date_value).date()
        except ValueError:
            pass
    return payload


def _json_safe_value(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {k: _json_safe_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe_value(v) for v in value]
    return value


def upsert_progress_report_snapshot(
    current_session_id,
    school_id,
    student_id,
    standard_id,
    exam_id,
    payload,
    status='draft',
    ready_to_publish=None,
    user_obj=None,
):
    report_obj, _ = ProgressReport.objects.get_or_create(
        isDeleted=False,
        sessionID_id=current_session_id,
        schoolID_id=school_id,
        studentID_id=student_id,
        examID_id=exam_id,
        defaults={
            'standardID_id': standard_id,
            'status': status,
            'readyToPublish': bool(ready_to_publish) if ready_to_publish is not None else False,
        },
    )

    report_obj.standardID_id = standard_id
    report_obj.status = status
    if ready_to_publish is not None:
        report_obj.readyToPublish = bool(ready_to_publish)
    if status == 'published':
        report_obj.publishedByUserID = user_obj
        report_obj.publishedAt = timezone.now()
    else:
        report_obj.publishedAt = None
    update_fields = ['standardID', 'status', 'publishedByUserID', 'publishedAt', 'lastUpdatedOn']
    if ready_to_publish is not None:
        update_fields.append('readyToPublish')
    report_obj.save(update_fields=update_fields)

    snapshot_type = 'published' if status == 'published' else 'draft'
    ProgressReportSnapshot.objects.filter(
        isDeleted=False,
        sessionID_id=current_session_id,
        progressReportID_id=report_obj.id,
        snapshotType=snapshot_type,
        isCurrent=True,
    ).update(isCurrent=False)

    snapshot_obj = ProgressReportSnapshot.objects.create(
        progressReportID_id=report_obj.id,
        sessionID_id=current_session_id,
        schoolID_id=school_id,
        snapshotType=snapshot_type,
        payload=_json_safe_value(payload or {}),
        isCurrent=True,
        updatedByUserID=user_obj,
        lastEditedBy=(f'{user_obj.first_name} {user_obj.last_name}'.strip() or user_obj.username) if user_obj else None,
    )
    return report_obj, snapshot_obj


def build_report_cards_for_student(
    current_session_id,
    student_obj,
    standard_id,
    exam_queryset,
    prefer_published_snapshot=True,
):
    exam_ids = [row.id for row in exam_queryset]
    published_exam_ids = set(
        ProgressReport.objects.filter(
            isDeleted=False,
            sessionID_id=current_session_id,
            studentID_id=student_obj.id,
            examID_id__in=exam_ids,
            status='published',
        ).values_list('examID_id', flat=True)
    )
    ready_exam_ids = set(
        ProgressReport.objects.filter(
            isDeleted=False,
            sessionID_id=current_session_id,
            studentID_id=student_obj.id,
            examID_id__in=exam_ids,
            readyToPublish=True,
        ).values_list('examID_id', flat=True)
    )

    class_subjects = list(
        AssignSubjectsToClass.objects.select_related('subjectID').filter(
            isDeleted=False,
            sessionID_id=current_session_id,
            standardID_id=standard_id,
        ).order_by('subjectID__name')
    )

    report_cards = []
    for exam_obj in exam_queryset:
        published_payload = _load_published_snapshot(current_session_id, student_obj.id, exam_obj.id) if prefer_published_snapshot else None
        if published_payload:
            card_payload = dict(published_payload)
            if 'exam_assignment_id' not in card_payload:
                card_payload['exam_assignment_id'] = exam_obj.id
            card_payload['is_published'] = exam_obj.id in published_exam_ids
            card_payload['ready_to_publish'] = exam_obj.id in ready_exam_ids
            report_cards.append(card_payload)
            continue

        card_payload = _build_dynamic_card(current_session_id, student_obj, standard_id, class_subjects, exam_obj)
        card_payload['is_published'] = exam_obj.id in published_exam_ids
        card_payload['ready_to_publish'] = exam_obj.id in ready_exam_ids
        report_cards.append(card_payload)

    return report_cards


def _build_dynamic_card(current_session_id, student_obj, standard_id, class_subjects, exam_obj):
    pass_policy = PassPolicy.objects.select_related('gradingPolicyID').filter(
        isDeleted=False,
        sessionID_id=current_session_id,
        examID_id=exam_obj.id,
    ).first()

    remark_map = _load_subject_remarks(current_session_id, student_obj.id, exam_obj.id)
    term_remark = _load_term_remark(current_session_id, student_obj.id, exam_obj.id)

    component_rules = list(
        ExamSubjectComponentRule.objects.select_related('componentTypeID').filter(
            isDeleted=False,
            sessionID_id=current_session_id,
            examID_id=exam_obj.id,
        ).order_by('subjectID_id', 'displayOrder', 'id')
    )
    rules_by_subject: Dict[int, List[ExamSubjectComponentRule]] = defaultdict(list)
    for rule in component_rules:
        rules_by_subject[rule.subjectID_id].append(rule)

    legacy_marks_qs = MarkOfStudentsByExam.objects.select_related('subjectID', 'subjectID__subjectID').filter(
        isDeleted=False,
        sessionID_id=current_session_id,
        studentID_id=student_obj.id,
        standardID_id=standard_id,
        examID_id=exam_obj.id,
    )
    legacy_mark_map = {m.subjectID_id: m for m in legacy_marks_qs}

    component_marks_qs = StudentExamComponentMark.objects.select_related('componentRuleID', 'componentRuleID__componentTypeID').filter(
        isDeleted=False,
        sessionID_id=current_session_id,
        studentID_id=student_obj.id,
        standardID_id=standard_id,
        examID_id=exam_obj.id,
    )
    component_mark_map = {(m.subjectID_id, m.componentRuleID_id): m for m in component_marks_qs}

    subject_rows = []
    entered_marks_count = 0
    total_obtained = 0.0
    full_marks = 0.0
    pass_marks = 0.0
    any_pending = False
    any_subject_failed = False

    use_weighted = bool(pass_policy and pass_policy.resultComputationMode == 'weighted')

    for ass_sub in class_subjects:
        subject_rule_list = rules_by_subject.get(ass_sub.id, [])
        subject_remark = remark_map.get(ass_sub.id)

        if subject_rule_list:
            subject_components = []
            subject_raw_obtained = 0.0
            subject_raw_full = 0.0
            subject_raw_pass = 0.0
            subject_weighted_obtained = 0.0
            subject_weighted_full = 0.0
            subject_pending = False
            subject_failed = False

            for rule in subject_rule_list:
                mark_obj = component_mark_map.get((ass_sub.id, rule.id))
                max_marks = float(rule.maxMarks or 0)
                pass_mark = float(rule.passMarks or 0)
                weightage = float(rule.weightage or 0)

                status = 'Pending'
                obtained = None
                note = ''
                is_exempt = bool(mark_obj and mark_obj.isExempt)

                if mark_obj:
                    note = mark_obj.note or ''
                    if mark_obj.isExempt:
                        status = 'Exempt'
                        obtained = None
                    elif mark_obj.isAbsent:
                        status = 'Absent'
                        obtained = 0.0
                    elif mark_obj.marksObtained is not None:
                        status = 'Entered'
                        obtained = float(mark_obj.marksObtained)
                    else:
                        status = 'Pending'

                if not is_exempt:
                    subject_raw_full += max_marks
                    subject_raw_pass += pass_mark

                if obtained is not None:
                    subject_raw_obtained += obtained
                    if use_weighted and max_marks > 0 and weightage > 0:
                        subject_weighted_obtained += (obtained / max_marks) * weightage

                if use_weighted and weightage > 0 and not is_exempt:
                    subject_weighted_full += weightage

                if status == 'Pending' and rule.isMandatory and (not pass_policy or pass_policy.requireMandatoryComponents):
                    subject_pending = True
                if (not is_exempt) and obtained is not None and (pass_policy is None or pass_policy.requireComponentPass) and obtained < pass_mark:
                    subject_failed = True

                subject_components.append({
                    'name': rule.componentTypeID.name if rule.componentTypeID else 'Component',
                    'status': status,
                    'obtained': None if obtained is None else round(obtained, 2),
                    'max_marks': round(max_marks, 2),
                    'pass_marks': round(pass_mark, 2),
                    'weightage': None if rule.weightage is None else round(weightage, 2),
                    'note': note,
                })

            if (pass_policy is None or pass_policy.requireSubjectPass) and subject_raw_obtained < subject_raw_pass:
                subject_failed = True

            subject_effective_obtained = subject_weighted_obtained if use_weighted and subject_weighted_full > 0 else subject_raw_obtained
            subject_effective_full = subject_weighted_full if use_weighted and subject_weighted_full > 0 else subject_raw_full
            subject_effective_pass = subject_raw_pass

            if not subject_pending:
                entered_marks_count += 1
            else:
                any_pending = True

            if subject_failed:
                any_subject_failed = True

            total_obtained += subject_effective_obtained
            full_marks += subject_effective_full
            pass_marks += subject_effective_pass

            component_summary = ', '.join([
                f"{c['name']}: {('Exempt' if c['status'] == 'Exempt' else ('Pending' if c['obtained'] is None else c['obtained']))}/{c['max_marks']}"
                for c in subject_components
            ])

            subject_rows.append({
                'subject_name': ass_sub.subjectID.name if ass_sub.subjectID else 'N/A',
                'mark': round(subject_effective_obtained, 2),
                'note': (subject_remark.remark if subject_remark and subject_remark.remark else '') or '-',
                'components': subject_components,
                'component_summary': component_summary or '-',
                'subject_result': 'Pending' if subject_pending else ('Fail' if subject_failed else 'Pass'),
            })
            continue

        # Legacy single-mark fallback
        mark_obj = legacy_mark_map.get(ass_sub.id)
        legacy_subject_full = float(exam_obj.fullMarks or 0)
        legacy_subject_pass = float(exam_obj.passMarks or 0)
        subject_failed = False
        if mark_obj is not None:
            mark_value = float(mark_obj.mark or 0)
            total_obtained += mark_value
            entered_marks_count += 1
            if (pass_policy is None or pass_policy.requireSubjectPass) and mark_value < legacy_subject_pass:
                subject_failed = True
        else:
            mark_value = None
            any_pending = True

        full_marks += legacy_subject_full
        pass_marks += legacy_subject_pass
        if subject_failed:
            any_subject_failed = True

        subject_rows.append({
            'subject_name': ass_sub.subjectID.name if ass_sub.subjectID else 'N/A',
            'mark': None if mark_value is None else round(mark_value, 2),
            'note': (subject_remark.remark if subject_remark and subject_remark.remark else (mark_obj.note if mark_obj and mark_obj.note else '')) or '-',
            'components': [],
            'component_summary': '-',
            'subject_result': 'Pending' if mark_value is None else ('Fail' if subject_failed else 'Pass'),
        })

    if full_marks <= 0:
        full_marks = float(exam_obj.fullMarks or 0)
    if pass_marks <= 0:
        pass_marks = float(
            pass_policy.overallPassMarks
            if pass_policy and pass_policy.overallPassMarks is not None
            else (exam_obj.passMarks or 0)
        )

    percentage = round((total_obtained * 100.0 / full_marks), 2) if full_marks > 0 else None
    grade = _grade_from_policy(percentage, pass_policy.gradingPolicyID if pass_policy else None)

    is_complete = entered_marks_count == len(class_subjects) and len(class_subjects) > 0 and not any_pending

    if not is_complete:
        result = 'Pending'
    else:
        overall_pass_marks = float(pass_policy.overallPassMarks) if pass_policy and pass_policy.overallPassMarks is not None else pass_marks
        has_failed_subject_gate = bool(pass_policy and pass_policy.requireSubjectPass and any_subject_failed)
        if has_failed_subject_gate:
            result = 'Fail'
        elif total_obtained >= overall_pass_marks:
            result = 'Pass'
        else:
            result = 'Fail'

    manual_result = (term_remark.overallResultDecision or '').strip().lower() if term_remark else ''
    if manual_result == 'pass':
        result = 'Pass'
    elif manual_result == 'fail':
        result = 'Fail'

    attendance = _attendance_summary(current_session_id, student_obj.id)

    return {
        'exam_assignment_id': exam_obj.id,
        'exam_name': exam_obj.examID.name if exam_obj.examID else 'N/A',
        'exam_date': exam_obj.startDate,
        'full_marks': round(float(full_marks or 0), 2),
        'pass_marks': round(float(pass_marks or 0), 2),
        'total_obtained': round(float(total_obtained or 0), 2),
        'percentage': percentage,
        'grade': grade,
        'result': result,
        'is_complete': is_complete,
        'entered_marks_count': entered_marks_count,
        'subject_count': len(class_subjects),
        'subject_rows': subject_rows,
        'attendance': attendance,
        'term_remark': {
            'overall_remark': term_remark.overallRemark if term_remark and term_remark.overallRemark else '',
            'strengths': term_remark.strengths if term_remark and term_remark.strengths else '',
            'improvement_areas': term_remark.improvementAreas if term_remark and term_remark.improvementAreas else '',
            'next_steps': term_remark.nextSteps if term_remark and term_remark.nextSteps else '',
            'conduct_grade': term_remark.conductGrade if term_remark and term_remark.conductGrade else '',
            'overall_result_decision': term_remark.overallResultDecision if term_remark and term_remark.overallResultDecision else '',
            'result_decided_by_role': term_remark.resultDecidedByRole if term_remark and term_remark.resultDecidedByRole else '',
        },
        'uses_component_scheme': bool(component_rules),
    }
