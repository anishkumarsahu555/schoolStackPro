import re
from copy import deepcopy
from datetime import date

from django.db import transaction
from django.db.models import Q

from homeApp.models import SchoolSession
from managementApp.models import (
    AssignExamToClass,
    AssignSubjectsToClass,
    AssignSubjectsToTeacher,
    CoScholasticArea,
    EventType,
    Exam,
    ExamComponentType,
    ExamSubjectComponentRule,
    ExamTimeTable,
    GradingBand,
    GradingPolicy,
    LeaveType,
    Parent,
    PassPolicy,
    Standard,
    Student,
    Subjects,
    TeacherDetail,
)


DEFAULT_IMPORT_OPTIONS = {
    'copy_teachers': True,
    'copy_classes': True,
    'copy_subjects': True,
    'copy_class_subjects': True,
    'copy_teacher_subjects': True,
    'copy_exam_setup': True,
    'copy_grading_setup': True,
    'copy_leave_types': True,
    'copy_event_types': True,
    'copy_students': False,
    'promote_students': False,
    'promotion_overrides': {},
    'selection_overrides': {},
}


IMPORTABLE_MODELS = {
    'copy_teachers': TeacherDetail,
    'copy_classes': Standard,
    'copy_subjects': Subjects,
    'copy_class_subjects': AssignSubjectsToClass,
    'copy_teacher_subjects': AssignSubjectsToTeacher,
    'copy_exam_setup': Exam,
    'copy_grading_setup': GradingPolicy,
    'copy_leave_types': LeaveType,
    'copy_event_types': EventType,
    'copy_students': Student,
}


def merge_import_options(raw_options=None):
    options = deepcopy(DEFAULT_IMPORT_OPTIONS)
    if raw_options:
        for key, value in raw_options.items():
            if key in options:
                if key == 'promotion_overrides':
                    options[key] = value or {}
                elif key == 'selection_overrides':
                    options[key] = value or {}
                else:
                    options[key] = bool(value)

    if not options['copy_classes']:
        options['copy_class_subjects'] = False
    if not options['copy_classes'] or not options['copy_subjects']:
        options['copy_class_subjects'] = False
    if not options['copy_class_subjects'] or not options['copy_teachers']:
        options['copy_teacher_subjects'] = False
    if not options['copy_exam_setup']:
        options['copy_grading_setup'] = False
    if not options['copy_students']:
        options['promote_students'] = False
    return options


def preview_session_import(*, school_id, source_session_id, target_session_id, options=None):
    source_session, target_session = _validate_sessions(
        school_id=school_id,
        source_session_id=source_session_id,
        target_session_id=target_session_id,
    )
    options = merge_import_options(options)
    warnings = _validate_target_session_state(target_session, options)

    counts = {}
    details = {}
    for option_key, model in IMPORTABLE_MODELS.items():
        if not options.get(option_key):
            continue
        queryset = model.objects.filter(
            schoolID_id=school_id,
            sessionID_id=source_session.id,
            isDeleted=False,
        )
        queryset = _filter_preview_queryset(options, option_key, queryset)
        counts[option_key] = queryset.count()
        details[option_key] = _preview_rows_for_option(
            option_key,
            model.objects.filter(
                schoolID_id=school_id,
                sessionID_id=source_session.id,
                isDeleted=False,
            ),
            selected_ids=_selected_ids_for_option(options, option_key),
        )

    if options['copy_exam_setup']:
        exam_to_class_qs = AssignExamToClass.objects.filter(
            schoolID_id=school_id,
            sessionID_id=source_session.id,
            isDeleted=False,
        )
        exam_to_class_qs = _filter_preview_queryset(options, 'copy_exam_to_class', exam_to_class_qs)
        counts['copy_exam_to_class'] = exam_to_class_qs.count()
        details['copy_exam_to_class'] = _preview_rows_for_option(
            'copy_exam_to_class',
            AssignExamToClass.objects.filter(
                schoolID_id=school_id,
                sessionID_id=source_session.id,
                isDeleted=False,
            ),
            selected_ids=_selected_ids_for_option(options, 'copy_exam_to_class'),
        )

        exam_timetable_qs = ExamTimeTable.objects.filter(
            schoolID_id=school_id,
            sessionID_id=source_session.id,
            isDeleted=False,
        )
        exam_timetable_qs = _filter_preview_queryset(options, 'copy_exam_timetable', exam_timetable_qs)
        counts['copy_exam_timetable'] = exam_timetable_qs.count()
        details['copy_exam_timetable'] = _preview_rows_for_option(
            'copy_exam_timetable',
            ExamTimeTable.objects.filter(
                schoolID_id=school_id,
                sessionID_id=source_session.id,
                isDeleted=False,
            ),
            selected_ids=_selected_ids_for_option(options, 'copy_exam_timetable'),
        )

        component_type_qs = ExamComponentType.objects.filter(
            schoolID_id=school_id,
            sessionID_id=source_session.id,
            isDeleted=False,
        )
        component_type_qs = _filter_preview_queryset(options, 'copy_exam_component_types', component_type_qs)
        counts['copy_exam_component_types'] = component_type_qs.count()
        details['copy_exam_component_types'] = _preview_rows_for_option(
            'copy_exam_component_types',
            ExamComponentType.objects.filter(
                schoolID_id=school_id,
                sessionID_id=source_session.id,
                isDeleted=False,
            ),
            selected_ids=_selected_ids_for_option(options, 'copy_exam_component_types'),
        )

        component_rule_qs = ExamSubjectComponentRule.objects.filter(
            schoolID_id=school_id,
            sessionID_id=source_session.id,
            isDeleted=False,
        )
        component_rule_qs = _filter_preview_queryset(options, 'copy_component_rules', component_rule_qs)
        counts['copy_component_rules'] = component_rule_qs.count()
        details['copy_component_rules'] = _preview_rows_for_option(
            'copy_component_rules',
            ExamSubjectComponentRule.objects.filter(
                schoolID_id=school_id,
                sessionID_id=source_session.id,
                isDeleted=False,
            ),
            selected_ids=_selected_ids_for_option(options, 'copy_component_rules'),
        )

        area_qs = CoScholasticArea.objects.filter(
            schoolID_id=school_id,
            sessionID_id=source_session.id,
            isDeleted=False,
        )
        area_qs = _filter_preview_queryset(options, 'copy_co_scholastic_areas', area_qs)
        counts['copy_co_scholastic_areas'] = area_qs.count()
        details['copy_co_scholastic_areas'] = _preview_rows_for_option(
            'copy_co_scholastic_areas',
            CoScholasticArea.objects.filter(
                schoolID_id=school_id,
                sessionID_id=source_session.id,
                isDeleted=False,
            ),
            selected_ids=_selected_ids_for_option(options, 'copy_co_scholastic_areas'),
        )

        pass_policy_qs = PassPolicy.objects.filter(
            schoolID_id=school_id,
            sessionID_id=source_session.id,
            isDeleted=False,
        )
        pass_policy_qs = _filter_preview_queryset(options, 'copy_pass_policies', pass_policy_qs)
        counts['copy_pass_policies'] = pass_policy_qs.count()
        details['copy_pass_policies'] = _preview_rows_for_option(
            'copy_pass_policies',
            PassPolicy.objects.filter(
                schoolID_id=school_id,
                sessionID_id=source_session.id,
                isDeleted=False,
            ),
            selected_ids=_selected_ids_for_option(options, 'copy_pass_policies'),
        )
    if options['copy_grading_setup']:
        grading_band_qs = GradingBand.objects.filter(
            schoolID_id=school_id,
            sessionID_id=source_session.id,
            isDeleted=False,
        )
        grading_band_qs = _filter_preview_queryset(options, 'copy_grading_bands', grading_band_qs)
        counts['copy_grading_bands'] = grading_band_qs.count()
        details['copy_grading_bands'] = _preview_rows_for_option(
            'copy_grading_bands',
            GradingBand.objects.filter(
                schoolID_id=school_id,
                sessionID_id=source_session.id,
                isDeleted=False,
            ),
            selected_ids=_selected_ids_for_option(options, 'copy_grading_bands'),
        )
    if options['copy_students']:
        parent_qs = Parent.objects.filter(
            schoolID_id=school_id,
            sessionID_id=source_session.id,
            isDeleted=False,
        )
        parent_qs = _filter_preview_queryset(options, 'copy_parents', parent_qs)
        counts['copy_parents'] = parent_qs.count()
        details['copy_parents'] = _preview_rows_for_option(
            'copy_parents',
            Parent.objects.filter(
                schoolID_id=school_id,
                sessionID_id=source_session.id,
                isDeleted=False,
            ),
            selected_ids=_selected_ids_for_option(options, 'copy_parents'),
        )
        if options['promote_students']:
            promotion_preview = _build_promotion_preview(
                source_session.id,
                target_session.id,
                options.get('promotion_overrides'),
            )
            warnings.extend(promotion_preview['warnings'])
        else:
            promotion_preview = {'rows': []}
    else:
        promotion_preview = {'rows': []}

    return {
        'sourceSession': _serialize_session(source_session),
        'targetSession': _serialize_session(target_session),
        'options': options,
        'counts': counts,
        'details': details,
        'warnings': _dedupe_list(warnings),
        'promotionPreview': promotion_preview['rows'][:20],
    }


@transaction.atomic
def run_session_import(*, school_id, source_session_id, target_session_id, options=None, acting_user=None):
    source_session, target_session = _validate_sessions(
        school_id=school_id,
        source_session_id=source_session_id,
        target_session_id=target_session_id,
    )
    options = merge_import_options(options)
    warnings = _validate_target_session_state(target_session, options)

    service = SessionImportService(
        school_id=school_id,
        source_session=source_session,
        target_session=target_session,
        options=options,
        acting_user=acting_user,
    )
    result = service.run()
    result['warnings'] = _dedupe_list(warnings + result.get('warnings', []))
    return result


class SessionImportService:
    def __init__(self, *, school_id, source_session, target_session, options, acting_user=None):
        self.school_id = school_id
        self.source_session = source_session
        self.target_session = target_session
        self.options = options
        self.acting_user = acting_user

        self.summary = {
            'copied': {},
            'skipped': {},
            'warnings': [],
        }
        self.teacher_map = {}
        self.standard_map = {}
        self.subject_map = {}
        self.assign_subject_map = {}
        self.exam_map = {}
        self.assign_exam_map = {}
        self.component_type_map = {}
        self.grading_policy_map = {}
        self.component_rule_map = {}
        self.area_map = {}
        self.parent_map = {}
        self.student_map = {}

    def _selected_ids(self, option_key):
        return _selected_ids_for_option(self.options, option_key)

    def _filter_queryset(self, option_key, queryset):
        selected_ids = self._selected_ids(option_key)
        if selected_ids:
            return queryset.filter(id__in=selected_ids)
        return queryset

    def run(self):
        if self.options['copy_teachers']:
            self._copy_teachers()
        if self.options['copy_classes']:
            self._copy_classes()
        if self.options['copy_subjects']:
            self._copy_subjects()
        if self.options['copy_class_subjects']:
            self._copy_assign_subjects_to_class()
        if self.options['copy_teacher_subjects']:
            self._copy_assign_subjects_to_teacher()
        if self.options['copy_exam_setup']:
            self._copy_exams()
            self._copy_assign_exams_to_class()
            self._copy_exam_timetable()
            self._copy_component_types()
            self._copy_co_scholastic_areas()
        if self.options['copy_grading_setup']:
            self._copy_grading_policies()
            self._copy_grading_bands()
            self._copy_pass_policies()
            self._copy_component_rules()
        if self.options['copy_leave_types']:
            self._copy_leave_types()
        if self.options['copy_event_types']:
            self._copy_event_types()
        if self.options['copy_students']:
            self._copy_parents()
            self._copy_students()

        return {
            'sourceSession': _serialize_session(self.source_session),
            'targetSession': _serialize_session(self.target_session),
            'options': self.options,
            'copied': self.summary['copied'],
            'skipped': self.summary['skipped'],
            'warnings': _dedupe_list(self.summary['warnings']),
        }

    def _copy_teachers(self):
        queryset = TeacherDetail.objects.filter(
            schoolID_id=self.school_id,
            sessionID_id=self.source_session.id,
            isDeleted=False,
        ).order_by('id')
        queryset = self._filter_queryset('copy_teachers', queryset)
        for teacher in queryset:
            existing = TeacherDetail.objects.filter(
                schoolID_id=self.school_id,
                sessionID_id=self.target_session.id,
                isDeleted=False,
            )
            if teacher.userID_id:
                existing = existing.filter(userID_id=teacher.userID_id)
            elif teacher.employeeCode:
                existing = existing.filter(_text_match_q('employeeCode', teacher.employeeCode))
            else:
                existing = existing.filter(_text_match_q('name', teacher.name)).filter(_text_match_q('phoneNumber', teacher.phoneNumber))
            target = existing.order_by('id').first()
            if target:
                self.teacher_map[teacher.id] = target.id
                self._mark_skipped('teachers')
                continue

            payload = _clone_payload(
                teacher,
                exclude={'id', 'sessionID', 'schoolID', 'updatedByUserID', 'lastUpdatedOn', 'datetime'},
                overrides={'sessionID': self.target_session, 'schoolID_id': self.school_id},
            )
            target = TeacherDetail.objects.create(**payload)
            self._touch_audit_fields(target)
            self.teacher_map[teacher.id] = target.id
            self._mark_copied('teachers')

    def _copy_classes(self):
        queryset = Standard.objects.filter(
            schoolID_id=self.school_id,
            sessionID_id=self.source_session.id,
            isDeleted=False,
        ).select_related('classTeacher').order_by('id')
        queryset = self._filter_queryset('copy_classes', queryset)
        for standard in queryset:
            teacher_id = self.teacher_map.get(standard.classTeacher_id)
            existing = Standard.objects.filter(
                schoolID_id=self.school_id,
                sessionID_id=self.target_session.id,
                isDeleted=False,
            ).filter(
                _text_match_q('name', standard.name)
            ).filter(
                _text_match_q('section', standard.section)
            ).order_by('id').first()
            if existing:
                self.standard_map[standard.id] = existing.id
                self._mark_skipped('classes')
                continue

            payload = _clone_payload(
                standard,
                exclude={'id', 'sessionID', 'schoolID', 'classTeacher', 'updatedByUserID', 'lastUpdatedOn', 'datetime'},
                overrides={
                    'sessionID': self.target_session,
                    'schoolID_id': self.school_id,
                    'classTeacher_id': teacher_id,
                },
            )
            target = Standard.objects.create(**payload)
            self._touch_audit_fields(target)
            self.standard_map[standard.id] = target.id
            self._mark_copied('classes')

    def _copy_subjects(self):
        queryset = Subjects.objects.filter(
            schoolID_id=self.school_id,
            sessionID_id=self.source_session.id,
            isDeleted=False,
        ).order_by('id')
        queryset = self._filter_queryset('copy_subjects', queryset)
        for subject in queryset:
            existing = Subjects.objects.filter(
                schoolID_id=self.school_id,
                sessionID_id=self.target_session.id,
                isDeleted=False,
            ).filter(_text_match_q('name', subject.name)).order_by('id').first()
            if existing:
                self.subject_map[subject.id] = existing.id
                self._mark_skipped('subjects')
                continue

            payload = _clone_payload(
                subject,
                exclude={'id', 'sessionID', 'schoolID', 'updatedByUserID', 'lastUpdatedOn', 'datetime'},
                overrides={'sessionID': self.target_session, 'schoolID_id': self.school_id},
            )
            target = Subjects.objects.create(**payload)
            self._touch_audit_fields(target)
            self.subject_map[subject.id] = target.id
            self._mark_copied('subjects')

    def _copy_assign_subjects_to_class(self):
        queryset = AssignSubjectsToClass.objects.filter(
            schoolID_id=self.school_id,
            sessionID_id=self.source_session.id,
            isDeleted=False,
        ).order_by('id')
        queryset = self._filter_queryset('copy_class_subjects', queryset)
        for row in queryset:
            standard_id = self.standard_map.get(row.standardID_id)
            subject_id = self.subject_map.get(row.subjectID_id)
            if not standard_id or not subject_id:
                self.summary['warnings'].append('Some class-subject mappings were skipped because matching class or subject was unavailable in the target session.')
                self._mark_skipped('classSubjects')
                continue

            existing = AssignSubjectsToClass.objects.filter(
                schoolID_id=self.school_id,
                sessionID_id=self.target_session.id,
                isDeleted=False,
                standardID_id=standard_id,
                subjectID_id=subject_id,
            ).order_by('id').first()
            if existing:
                self.assign_subject_map[row.id] = existing.id
                self._mark_skipped('classSubjects')
                continue

            payload = _clone_payload(
                row,
                exclude={'id', 'sessionID', 'schoolID', 'standardID', 'subjectID', 'updatedByUserID', 'lastUpdatedOn', 'datetime'},
                overrides={
                    'sessionID': self.target_session,
                    'schoolID_id': self.school_id,
                    'standardID_id': standard_id,
                    'subjectID_id': subject_id,
                },
            )
            target = AssignSubjectsToClass.objects.create(**payload)
            self._touch_audit_fields(target)
            self.assign_subject_map[row.id] = target.id
            self._mark_copied('classSubjects')

    def _copy_assign_subjects_to_teacher(self):
        queryset = AssignSubjectsToTeacher.objects.filter(
            schoolID_id=self.school_id,
            sessionID_id=self.source_session.id,
            isDeleted=False,
        ).order_by('id')
        queryset = self._filter_queryset('copy_teacher_subjects', queryset)
        for row in queryset:
            assigned_subject_id = self.assign_subject_map.get(row.assignedSubjectID_id)
            teacher_id = self.teacher_map.get(row.teacherID_id)
            if not assigned_subject_id or not teacher_id:
                self.summary['warnings'].append('Some teacher subject assignments were skipped because the linked teacher or subject mapping was unavailable in the target session.')
                self._mark_skipped('teacherSubjects')
                continue

            existing = AssignSubjectsToTeacher.objects.filter(
                schoolID_id=self.school_id,
                sessionID_id=self.target_session.id,
                isDeleted=False,
                assignedSubjectID_id=assigned_subject_id,
                teacherID_id=teacher_id,
                subjectBranch=(row.subjectBranch or ''),
            ).order_by('id').first()
            if existing:
                self._mark_skipped('teacherSubjects')
                continue

            payload = _clone_payload(
                row,
                exclude={'id', 'sessionID', 'schoolID', 'assignedSubjectID', 'teacherID', 'updatedByUserID', 'lastUpdatedOn', 'datetime'},
                overrides={
                    'sessionID': self.target_session,
                    'schoolID_id': self.school_id,
                    'assignedSubjectID_id': assigned_subject_id,
                    'teacherID_id': teacher_id,
                },
            )
            target = AssignSubjectsToTeacher.objects.create(**payload)
            self._touch_audit_fields(target)
            self._mark_copied('teacherSubjects')

    def _copy_exams(self):
        queryset = Exam.objects.filter(
            schoolID_id=self.school_id,
            sessionID_id=self.source_session.id,
            isDeleted=False,
        ).order_by('id')
        queryset = self._filter_queryset('copy_exam_setup', queryset)
        for row in queryset:
            existing = Exam.objects.filter(
                schoolID_id=self.school_id,
                sessionID_id=self.target_session.id,
                isDeleted=False,
            ).filter(_text_match_q('name', row.name)).order_by('id').first()
            if existing:
                self.exam_map[row.id] = existing.id
                self._mark_skipped('exams')
                continue

            payload = _clone_payload(
                row,
                exclude={'id', 'sessionID', 'schoolID', 'updatedByUserID', 'lastUpdatedOn', 'datetime'},
                overrides={'sessionID': self.target_session, 'schoolID_id': self.school_id},
            )
            target = Exam.objects.create(**payload)
            self._touch_audit_fields(target)
            self.exam_map[row.id] = target.id
            self._mark_copied('exams')

    def _copy_assign_exams_to_class(self):
        queryset = AssignExamToClass.objects.filter(
            schoolID_id=self.school_id,
            sessionID_id=self.source_session.id,
            isDeleted=False,
        ).order_by('id')
        queryset = self._filter_queryset('copy_exam_to_class', queryset)
        for row in queryset:
            standard_id = self.standard_map.get(row.standardID_id)
            exam_id = self.exam_map.get(row.examID_id)
            if not standard_id or not exam_id:
                self.summary['warnings'].append('Some exam-class mappings were skipped because the linked class or exam was unavailable in the target session.')
                self._mark_skipped('examClasses')
                continue

            existing = AssignExamToClass.objects.filter(
                schoolID_id=self.school_id,
                sessionID_id=self.target_session.id,
                isDeleted=False,
                standardID_id=standard_id,
                examID_id=exam_id,
            ).order_by('id').first()
            if existing:
                self.assign_exam_map[row.id] = existing.id
                self._mark_skipped('examClasses')
                continue

            payload = _clone_payload(
                row,
                exclude={'id', 'sessionID', 'schoolID', 'standardID', 'examID', 'updatedByUserID', 'lastUpdatedOn', 'datetime'},
                overrides={
                    'sessionID': self.target_session,
                    'schoolID_id': self.school_id,
                    'standardID_id': standard_id,
                    'examID_id': exam_id,
                },
            )
            target = AssignExamToClass.objects.create(**payload)
            self._touch_audit_fields(target)
            self.assign_exam_map[row.id] = target.id
            self._mark_copied('examClasses')

    def _copy_exam_timetable(self):
        queryset = ExamTimeTable.objects.filter(
            schoolID_id=self.school_id,
            sessionID_id=self.source_session.id,
            isDeleted=False,
        ).order_by('id')
        queryset = self._filter_queryset('copy_exam_timetable', queryset)
        for row in queryset:
            standard_id = self.standard_map.get(row.standardID_id)
            exam_id = self.exam_map.get(row.examID_id)
            subject_id = self.subject_map.get(row.subjectID_id)
            if not standard_id or not exam_id or not subject_id:
                self.summary['warnings'].append('Some exam timetable rows were skipped because the linked class, exam, or subject was unavailable in the target session.')
                self._mark_skipped('examTimetable')
                continue

            existing = ExamTimeTable.objects.filter(
                schoolID_id=self.school_id,
                sessionID_id=self.target_session.id,
                isDeleted=False,
                standardID_id=standard_id,
                examID_id=exam_id,
                subjectID_id=subject_id,
                examDate=row.examDate,
            ).order_by('id').first()
            if existing:
                self._mark_skipped('examTimetable')
                continue

            payload = _clone_payload(
                row,
                exclude={'id', 'sessionID', 'schoolID', 'standardID', 'examID', 'subjectID', 'updatedByUserID', 'lastUpdatedOn', 'datetime'},
                overrides={
                    'sessionID': self.target_session,
                    'schoolID_id': self.school_id,
                    'standardID_id': standard_id,
                    'examID_id': exam_id,
                    'subjectID_id': subject_id,
                },
            )
            target = ExamTimeTable.objects.create(**payload)
            self._touch_audit_fields(target)
            self._mark_copied('examTimetable')

    def _copy_component_types(self):
        queryset = ExamComponentType.objects.filter(
            schoolID_id=self.school_id,
            sessionID_id=self.source_session.id,
            isDeleted=False,
        ).order_by('id')
        queryset = self._filter_queryset('copy_exam_component_types', queryset)
        for row in queryset:
            existing = ExamComponentType.objects.filter(
                schoolID_id=self.school_id,
                sessionID_id=self.target_session.id,
                isDeleted=False,
            ).filter(_text_match_q('code', row.code)).order_by('id').first()
            if not existing and row.name:
                existing = ExamComponentType.objects.filter(
                    schoolID_id=self.school_id,
                    sessionID_id=self.target_session.id,
                    isDeleted=False,
                ).filter(_text_match_q('name', row.name)).order_by('id').first()
            if existing:
                self.component_type_map[row.id] = existing.id
                self._mark_skipped('examComponentTypes')
                continue

            payload = _clone_payload(
                row,
                exclude={'id', 'sessionID', 'schoolID', 'updatedByUserID', 'lastUpdatedOn', 'datetime'},
                overrides={'sessionID': self.target_session, 'schoolID_id': self.school_id},
            )
            target = ExamComponentType.objects.create(**payload)
            self._touch_audit_fields(target)
            self.component_type_map[row.id] = target.id
            self._mark_copied('examComponentTypes')

    def _copy_grading_policies(self):
        queryset = GradingPolicy.objects.filter(
            schoolID_id=self.school_id,
            sessionID_id=self.source_session.id,
            isDeleted=False,
        ).order_by('id')
        queryset = self._filter_queryset('copy_grading_setup', queryset)
        for row in queryset:
            existing = GradingPolicy.objects.filter(
                schoolID_id=self.school_id,
                sessionID_id=self.target_session.id,
                isDeleted=False,
            ).filter(_text_match_q('name', row.name)).order_by('id').first()
            if existing:
                self.grading_policy_map[row.id] = existing.id
                self._mark_skipped('gradingPolicies')
                continue

            payload = _clone_payload(
                row,
                exclude={'id', 'sessionID', 'schoolID', 'updatedByUserID', 'lastUpdatedOn', 'datetime'},
                overrides={'sessionID': self.target_session, 'schoolID_id': self.school_id},
            )
            target = GradingPolicy.objects.create(**payload)
            self._touch_audit_fields(target)
            self.grading_policy_map[row.id] = target.id
            self._mark_copied('gradingPolicies')

    def _copy_grading_bands(self):
        queryset = GradingBand.objects.filter(
            schoolID_id=self.school_id,
            sessionID_id=self.source_session.id,
            isDeleted=False,
        ).order_by('id')
        queryset = self._filter_queryset('copy_grading_bands', queryset)
        for row in queryset:
            policy_id = self.grading_policy_map.get(row.policyID_id)
            if not policy_id:
                self.summary['warnings'].append('Some grading bands were skipped because their grading policy was unavailable in the target session.')
                self._mark_skipped('gradingBands')
                continue

            existing = GradingBand.objects.filter(
                schoolID_id=self.school_id,
                sessionID_id=self.target_session.id,
                policyID_id=policy_id,
                isDeleted=False,
                gradeLabel=row.gradeLabel,
            ).order_by('id').first()
            if existing:
                self._mark_skipped('gradingBands')
                continue

            payload = _clone_payload(
                row,
                exclude={'id', 'policyID', 'sessionID', 'schoolID', 'updatedByUserID', 'lastUpdatedOn', 'datetime'},
                overrides={'policyID_id': policy_id, 'sessionID': self.target_session, 'schoolID_id': self.school_id},
            )
            target = GradingBand.objects.create(**payload)
            self._touch_audit_fields(target)
            self._mark_copied('gradingBands')

    def _copy_pass_policies(self):
        queryset = PassPolicy.objects.filter(
            schoolID_id=self.school_id,
            sessionID_id=self.source_session.id,
            isDeleted=False,
        ).order_by('id')
        queryset = self._filter_queryset('copy_pass_policies', queryset)
        for row in queryset:
            exam_id = self.assign_exam_map.get(row.examID_id)
            grading_policy_id = self.grading_policy_map.get(row.gradingPolicyID_id)
            if not exam_id:
                self.summary['warnings'].append('Some pass policies were skipped because their linked exam assignment was unavailable in the target session.')
                self._mark_skipped('passPolicies')
                continue

            existing = PassPolicy.objects.filter(
                schoolID_id=self.school_id,
                sessionID_id=self.target_session.id,
                examID_id=exam_id,
                isDeleted=False,
            ).order_by('id').first()
            if existing:
                self._mark_skipped('passPolicies')
                continue

            payload = _clone_payload(
                row,
                exclude={'id', 'sessionID', 'schoolID', 'examID', 'gradingPolicyID', 'updatedByUserID', 'lastUpdatedOn', 'datetime'},
                overrides={
                    'sessionID': self.target_session,
                    'schoolID_id': self.school_id,
                    'examID_id': exam_id,
                    'gradingPolicyID_id': grading_policy_id,
                },
            )
            target = PassPolicy.objects.create(**payload)
            self._touch_audit_fields(target)
            self._mark_copied('passPolicies')

    def _copy_component_rules(self):
        queryset = ExamSubjectComponentRule.objects.filter(
            schoolID_id=self.school_id,
            sessionID_id=self.source_session.id,
            isDeleted=False,
        ).order_by('id')
        queryset = self._filter_queryset('copy_component_rules', queryset)
        for row in queryset:
            exam_id = self.assign_exam_map.get(row.examID_id)
            subject_id = self.assign_subject_map.get(row.subjectID_id)
            component_type_id = self.component_type_map.get(row.componentTypeID_id)
            if not exam_id or not subject_id or not component_type_id:
                self.summary['warnings'].append('Some exam component rules were skipped because linked exam, subject, or component type was unavailable in the target session.')
                self._mark_skipped('componentRules')
                continue

            existing = ExamSubjectComponentRule.objects.filter(
                schoolID_id=self.school_id,
                sessionID_id=self.target_session.id,
                examID_id=exam_id,
                subjectID_id=subject_id,
                componentTypeID_id=component_type_id,
                isDeleted=False,
            ).order_by('id').first()
            if existing:
                self.component_rule_map[row.id] = existing.id
                self._mark_skipped('componentRules')
                continue

            payload = _clone_payload(
                row,
                exclude={'id', 'sessionID', 'schoolID', 'examID', 'subjectID', 'componentTypeID', 'updatedByUserID', 'lastUpdatedOn', 'datetime'},
                overrides={
                    'sessionID': self.target_session,
                    'schoolID_id': self.school_id,
                    'examID_id': exam_id,
                    'subjectID_id': subject_id,
                    'componentTypeID_id': component_type_id,
                },
            )
            target = ExamSubjectComponentRule.objects.create(**payload)
            self._touch_audit_fields(target)
            self.component_rule_map[row.id] = target.id
            self._mark_copied('componentRules')

    def _copy_co_scholastic_areas(self):
        queryset = CoScholasticArea.objects.filter(
            schoolID_id=self.school_id,
            sessionID_id=self.source_session.id,
            isDeleted=False,
        ).order_by('id')
        queryset = self._filter_queryset('copy_co_scholastic_areas', queryset)
        for row in queryset:
            existing = CoScholasticArea.objects.filter(
                schoolID_id=self.school_id,
                sessionID_id=self.target_session.id,
                isDeleted=False,
            ).filter(_text_match_q('code', row.code)).order_by('id').first()
            if not existing and row.name:
                existing = CoScholasticArea.objects.filter(
                    schoolID_id=self.school_id,
                    sessionID_id=self.target_session.id,
                    isDeleted=False,
                ).filter(_text_match_q('name', row.name)).order_by('id').first()
            if existing:
                self.area_map[row.id] = existing.id
                self._mark_skipped('coScholasticAreas')
                continue

            payload = _clone_payload(
                row,
                exclude={'id', 'sessionID', 'schoolID', 'updatedByUserID', 'lastUpdatedOn', 'datetime'},
                overrides={'sessionID': self.target_session, 'schoolID_id': self.school_id},
            )
            target = CoScholasticArea.objects.create(**payload)
            self._touch_audit_fields(target)
            self.area_map[row.id] = target.id
            self._mark_copied('coScholasticAreas')

    def _copy_leave_types(self):
        queryset = LeaveType.objects.filter(
            schoolID_id=self.school_id,
            sessionID_id=self.source_session.id,
            isDeleted=False,
        ).order_by('id')
        queryset = self._filter_queryset('copy_leave_types', queryset)
        for row in queryset:
            existing = LeaveType.objects.filter(
                schoolID_id=self.school_id,
                sessionID_id=self.target_session.id,
                isDeleted=False,
            ).filter(_text_match_q('name', row.name)).order_by('id').first()
            if existing:
                self._mark_skipped('leaveTypes')
                continue

            payload = _clone_payload(
                row,
                exclude={'id', 'sessionID', 'schoolID', 'updatedByUserID', 'lastUpdatedOn', 'datetime'},
                overrides={'sessionID': self.target_session, 'schoolID_id': self.school_id},
            )
            target = LeaveType.objects.create(**payload)
            self._touch_audit_fields(target)
            self._mark_copied('leaveTypes')

    def _copy_event_types(self):
        queryset = EventType.objects.filter(
            schoolID_id=self.school_id,
            sessionID_id=self.source_session.id,
            isDeleted=False,
        ).order_by('id')
        queryset = self._filter_queryset('copy_event_types', queryset)
        for row in queryset:
            existing = EventType.objects.filter(
                schoolID_id=self.school_id,
                sessionID_id=self.target_session.id,
                isDeleted=False,
            ).filter(_text_match_q('name', row.name)).filter(_text_match_q('audience', row.audience)).order_by('id').first()
            if existing:
                self._mark_skipped('eventTypes')
                continue

            payload = _clone_payload(
                row,
                exclude={'id', 'sessionID', 'schoolID', 'updatedByUserID', 'lastUpdatedOn', 'datetime'},
                overrides={'sessionID': self.target_session, 'schoolID_id': self.school_id},
            )
            target = EventType.objects.create(**payload)
            self._touch_audit_fields(target)
            self._mark_copied('eventTypes')

    def _copy_parents(self):
        queryset = Parent.objects.filter(
            schoolID_id=self.school_id,
            sessionID_id=self.source_session.id,
            isDeleted=False,
        ).order_by('id')
        queryset = self._filter_queryset('copy_parents', queryset)
        for row in queryset:
            existing = Parent.objects.filter(
                schoolID_id=self.school_id,
                sessionID_id=self.target_session.id,
                isDeleted=False,
            ).filter(_text_match_q('fatherName', row.fatherName)).filter(_text_match_q('motherName', row.motherName)).filter(_text_match_q('phoneNumber', row.phoneNumber)).order_by('id').first()
            if existing:
                self.parent_map[row.id] = existing.id
                self._mark_skipped('parents')
                continue

            payload = _clone_payload(
                row,
                exclude={'id', 'sessionID', 'schoolID', 'updatedByUserID', 'lastUpdatedOn', 'datetime'},
                overrides={'sessionID': self.target_session, 'schoolID_id': self.school_id},
            )
            target = Parent.objects.create(**payload)
            self._touch_audit_fields(target)
            self.parent_map[row.id] = target.id
            self._mark_copied('parents')

    def _copy_students(self):
        queryset = Student.objects.filter(
            schoolID_id=self.school_id,
            sessionID_id=self.source_session.id,
            isDeleted=False,
        ).select_related('standardID').order_by('id')
        queryset = self._filter_queryset('copy_students', queryset)
        promotion_map = _build_student_target_class_map(
            source_session_id=self.source_session.id,
            target_session_id=self.target_session.id,
            promote_students=self.options['promote_students'],
            promotion_overrides=self.options.get('promotion_overrides'),
        )
        for row in queryset:
            target_standard_id = promotion_map.get(row.standardID_id)
            if row.standardID_id and not target_standard_id:
                self.summary['warnings'].append(f'Some students were skipped because target class mapping was not found for source class "{row.standardID.name if row.standardID else "N/A"}".')
                self._mark_skipped('students')
                continue
            existing = Student.objects.filter(
                schoolID_id=self.school_id,
                sessionID_id=self.target_session.id,
                isDeleted=False,
            )
            if row.userID_id:
                existing = existing.filter(userID_id=row.userID_id)
            elif row.registrationCode:
                existing = existing.filter(_text_match_q('registrationCode', row.registrationCode))
            else:
                existing = existing.filter(_text_match_q('name', row.name)).filter(dob=row.dob)
            target = existing.order_by('id').first()
            if target:
                self.student_map[row.id] = target.id
                self._mark_skipped('students')
                continue

            payload = _clone_payload(
                row,
                exclude={'id', 'sessionID', 'schoolID', 'parentID', 'standardID', 'updatedByUserID', 'lastUpdatedOn', 'datetime'},
                overrides={
                    'sessionID': self.target_session,
                    'schoolID_id': self.school_id,
                    'parentID_id': self.parent_map.get(row.parentID_id),
                    'standardID_id': target_standard_id,
                },
            )
            target = Student.objects.create(**payload)
            self._touch_audit_fields(target)
            self.student_map[row.id] = target.id
            self._mark_copied('students')

    def _touch_audit_fields(self, instance):
        if not self.acting_user:
            return
        editor_name = self.acting_user.get_full_name().strip() or self.acting_user.username
        instance.updatedByUserID_id = self.acting_user.id
        instance.lastEditedBy = editor_name
        instance.save(update_fields=['updatedByUserID', 'lastEditedBy', 'lastUpdatedOn'])

    def _mark_copied(self, key):
        self.summary['copied'][key] = self.summary['copied'].get(key, 0) + 1

    def _mark_skipped(self, key):
        self.summary['skipped'][key] = self.summary['skipped'].get(key, 0) + 1


def _validate_sessions(*, school_id, source_session_id, target_session_id):
    source_session = SchoolSession.objects.filter(
        pk=source_session_id,
        schoolID_id=school_id,
        isDeleted=False,
    ).first()
    target_session = SchoolSession.objects.filter(
        pk=target_session_id,
        schoolID_id=school_id,
        isDeleted=False,
    ).first()

    if not source_session:
        raise ValueError('Source session was not found for the current school.')
    if not target_session:
        raise ValueError('Target session was not found for the current school.')
    if source_session.id == target_session.id:
        raise ValueError('Source session and target session must be different.')
    if not _is_target_session_newer(source_session, target_session):
        raise ValueError('Import is allowed only from an older session into a newer session.')
    if not target_session.isCurrent:
        raise ValueError('Target session must be the current session.')
    previous_session = _previous_session_for_school(school_id, target_session.id)
    if not previous_session:
        raise ValueError('No previous session is available before the current session.')
    if source_session.id != previous_session.id:
        raise ValueError('Source session must be the immediate previous session.')
    return source_session, target_session


def _validate_target_session_state(target_session, options):
    warnings = []
    checks = [
        ('copy_teachers', TeacherDetail, 'teachers'),
        ('copy_classes', Standard, 'classes'),
        ('copy_subjects', Subjects, 'subjects'),
        ('copy_students', Student, 'students'),
        ('copy_exam_setup', Exam, 'exams'),
    ]
    for option_key, model, label in checks:
        if not options.get(option_key):
            continue
        if model.objects.filter(sessionID_id=target_session.id, isDeleted=False).exists():
            warnings.append(f'Target session already has active {label}. Existing rows will be skipped when duplicates are detected.')
    return warnings


def _serialize_session(session_obj):
    return {
        'id': session_obj.id,
        'sessionYear': session_obj.sessionYear,
        'startDate': session_obj.startDate.isoformat() if session_obj.startDate else None,
        'endDate': session_obj.endDate.isoformat() if session_obj.endDate else None,
        'isCurrent': bool(session_obj.isCurrent),
    }


def _clone_payload(instance, *, exclude=None, overrides=None):
    exclude = set(exclude or set())
    overrides = overrides or {}
    payload = {}
    for field in instance._meta.concrete_fields:
        if field.name in exclude:
            continue
        if field.primary_key:
            continue
        if field.is_relation:
            payload[field.attname] = getattr(instance, field.attname)
        else:
            payload[field.name] = getattr(instance, field.name)
    payload.update(overrides)
    return payload


def _normalize_text(value):
    return re.sub(r'[^a-z0-9]+', ' ', (value or '').strip().lower()).strip()


def _text_match_q(field_name, value):
    normalized = (value or '').strip()
    if not normalized:
        return Q(**{f'{field_name}__isnull': True}) | Q(**{field_name: ''})
    return Q(**{f'{field_name}__iexact': normalized})


def _class_rank(standard):
    normalized = _normalize_text(getattr(standard, 'name', ''))
    if not normalized:
        return None

    direct_patterns = [
        (r'\bpre[\s-]*nursery\b', -4),
        (r'\bnursery\b', -3),
        (r'\blkg\b|\blower\s*kg\b', -2),
        (r'\bukg\b|\bupper\s*kg\b|\bprep\b', -1),
        (r'\bplay\b|\bplaygroup\b', -5),
    ]
    for pattern, rank in direct_patterns:
        if re.search(pattern, normalized):
            return rank

    digit_match = re.search(r'\b(?:class|std|standard|grade)?\s*(\d{1,2})\b', normalized)
    if digit_match:
        return int(digit_match.group(1))

    roman_match = re.search(r'\b(?:class|std|standard|grade)?\s*(xii|xi|ix|iv|v?i{0,3}|x)\b', normalized)
    if roman_match:
        roman_value = _roman_to_int(roman_match.group(1).upper())
        if roman_value is not None:
            return roman_value

    word_map = {
        'one': 1,
        'two': 2,
        'three': 3,
        'four': 4,
        'five': 5,
        'six': 6,
        'seven': 7,
        'eight': 8,
        'nine': 9,
        'ten': 10,
        'eleven': 11,
        'twelve': 12,
    }
    for token in normalized.split():
        if token in word_map:
            return word_map[token]
    return None


def _is_target_session_newer(source_session, target_session):
    source_key = _session_order_key(source_session)
    target_key = _session_order_key(target_session)
    return source_key < target_key


def _session_order_key(session_obj):
    start_date = getattr(session_obj, 'startDate', None)
    end_date = getattr(session_obj, 'endDate', None)
    inferred_year = _infer_session_year(getattr(session_obj, 'sessionYear', None))
    anchor_date = start_date or end_date or date(inferred_year or 1900, 1, 1)
    return (anchor_date, inferred_year or anchor_date.year, session_obj.id)


def _infer_session_year(session_year_value):
    if not session_year_value:
        return None
    match = re.search(r'(19|20)\d{2}', str(session_year_value))
    if not match:
        return None
    return int(match.group(0))


def _previous_session_for_school(school_id, current_session_id):
    sessions = list(
        SchoolSession.objects.filter(
            schoolID_id=school_id,
            isDeleted=False,
        ).order_by('startDate', 'datetime', 'id')
    )
    ordered_ids = [item.id for item in sessions]
    if current_session_id not in ordered_ids:
        return None
    current_index = ordered_ids.index(current_session_id)
    if current_index <= 0:
        return None
    return sessions[current_index - 1]


def _roman_to_int(value):
    roman_map = {'I': 1, 'V': 5, 'X': 10}
    total = 0
    prev = 0
    for char in reversed(value):
        current = roman_map.get(char)
        if current is None:
            return None
        if current < prev:
            total -= current
        else:
            total += current
            prev = current
    return total


def _build_promotion_preview(source_session_id, target_session_id, promotion_overrides=None):
    warnings = []
    rows = []
    mapping = _build_student_target_class_map(
        source_session_id=source_session_id,
        target_session_id=target_session_id,
        promote_students=True,
        promotion_overrides=promotion_overrides,
        warnings=warnings,
    )
    source_classes = {
        row.id: row
        for row in Standard.objects.filter(sessionID_id=source_session_id, isDeleted=False).order_by('id')
    }
    target_classes = {
        row.id: row
        for row in Standard.objects.filter(sessionID_id=target_session_id, isDeleted=False).order_by('id')
    }
    for source_id, target_id in mapping.items():
        source_row = source_classes.get(source_id)
        target_row = target_classes.get(target_id)
        rows.append({
            'sourceClass': _class_label(source_row),
            'targetClass': _class_label(target_row),
        })
    return {'warnings': warnings, 'rows': rows}


def _build_student_target_class_map(*, source_session_id, target_session_id, promote_students, promotion_overrides=None, warnings=None):
    warnings = warnings if warnings is not None else []
    promotion_overrides = _normalize_promotion_overrides(promotion_overrides)
    source_classes = list(Standard.objects.filter(sessionID_id=source_session_id, isDeleted=False).order_by('id'))
    target_classes = list(Standard.objects.filter(sessionID_id=target_session_id, isDeleted=False).order_by('id'))
    target_by_exact = {
        (_normalize_text(row.name), _normalize_text(row.section)): row
        for row in target_classes
    }
    target_by_label = {
        _normalize_text(_class_label(row)): row
        for row in target_classes
    }
    if not promote_students:
        return {
            row.id: (target_by_exact.get((_normalize_text(row.name), _normalize_text(row.section))).id if target_by_exact.get((_normalize_text(row.name), _normalize_text(row.section))) else None)
            for row in source_classes
        }
    target_by_rank_section = {
        (_class_rank(row), _normalize_text(row.section)): row
        for row in target_classes
        if _class_rank(row) is not None
    }
    result = {}
    for row in source_classes:
        exact_key = (_normalize_text(row.name), _normalize_text(row.section))
        override_key = _normalize_text(_class_label(row))
        override_target_label = promotion_overrides.get(override_key)
        if override_target_label:
            override_target = target_by_label.get(_normalize_text(override_target_label))
            if override_target:
                result[row.id] = override_target.id
                continue
            result[row.id] = None
            warnings.append(f'Custom promotion target "{override_target_label}" was not found in the target session for "{_class_label(row)}".')
            continue
        current_target = target_by_exact.get(exact_key)
        current_rank = _class_rank(row)
        if not current_target:
            result[row.id] = None
            warnings.append(f'No matching class was found in the target session for "{_class_label(row)}".')
            continue
        if current_rank is None:
            result[row.id] = None
            warnings.append(f'Promotion needs a recognizable class name for "{_class_label(row)}". Use names like Nursery, LKG, UKG, or Class 1-12.')
            continue

        promoted_row = target_by_rank_section.get((current_rank + 1, _normalize_text(row.section)))
        if not promoted_row and _normalize_text(row.section):
            promoted_row = target_by_rank_section.get((current_rank + 1, ''))
        if not promoted_row:
            result[row.id] = None
            warnings.append(f'No next class was found in the target session for "{_class_label(row)}".')
            continue
        result[row.id] = promoted_row.id
    return result


def _normalize_promotion_overrides(overrides):
    normalized = {}
    if not overrides:
        return normalized
    for source_label, target_label in overrides.items():
        source_key = _normalize_text(source_label)
        target_value = (target_label or '').strip()
        if source_key and target_value:
            normalized[source_key] = target_value
    return normalized


def _class_label(standard):
    if not standard:
        return 'N/A'
    name = (standard.name or '').strip() or 'Unnamed Class'
    section = (standard.section or '').strip()
    return f'{name} - {section}' if section else name


def _dedupe_list(items):
    seen = set()
    ordered = []
    for item in items:
        if not item or item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return ordered


def _selected_ids_for_option(options, option_key):
    selection_overrides = (options or {}).get('selection_overrides') or {}
    values = selection_overrides.get(option_key) or []
    selected = []
    for value in values:
        try:
            selected.append(int(value))
        except (TypeError, ValueError):
            continue
    return selected


def _filter_preview_queryset(options, option_key, queryset):
    selected_ids = _selected_ids_for_option(options, option_key)
    if selected_ids:
        return queryset.filter(id__in=selected_ids)
    return queryset


def _preview_rows_for_option(option_key, queryset, selected_ids=None, limit=100):
    selected_ids = set(selected_ids or [])

    def with_selection(rows):
        for row in rows:
            row['selected'] = (row.get('id') in selected_ids) if selected_ids else True
        return rows

    handlers = {
        'copy_teachers': lambda qs: [
            {'id': row.id, 'label': row.name or 'N/A', 'meta': row.employeeCode or row.phoneNumber or ''}
            for row in qs.order_by('name', 'id')[:limit]
        ],
        'copy_classes': lambda qs: [
            {'id': row.id, 'label': _class_label(row), 'meta': row.classLocation or ''}
            for row in qs.order_by('name', 'section', 'id')[:limit]
        ],
        'copy_subjects': lambda qs: [
            {'id': row.id, 'label': row.name or 'N/A', 'meta': ''}
            for row in qs.order_by('name', 'id')[:limit]
        ],
        'copy_class_subjects': lambda qs: [
            {'id': row.id, 'label': f'{_class_label(row.standardID)} -> {row.subjectID.name if row.subjectID else "N/A"}', 'meta': ''}
            for row in qs.select_related('standardID', 'subjectID').order_by('standardID__name', 'subjectID__name', 'id')[:limit]
        ],
        'copy_teacher_subjects': lambda qs: [
            {'id': row.id, 'label': row.teacherID.name if row.teacherID else 'N/A', 'meta': _teacher_subject_meta(row)}
            for row in qs.select_related('teacherID', 'assignedSubjectID__standardID', 'assignedSubjectID__subjectID').order_by('teacherID__name', 'id')[:limit]
        ],
        'copy_exam_setup': lambda qs: [
            {'id': row.id, 'label': row.name or 'N/A', 'meta': ''}
            for row in qs.order_by('name', 'id')[:limit]
        ],
        'copy_exam_to_class': lambda qs: [
            {'id': row.id, 'label': f'{row.examID.name if row.examID else "N/A"} -> {_class_label(row.standardID)}', 'meta': ''}
            for row in qs.select_related('examID', 'standardID').order_by('examID__name', 'standardID__name', 'id')[:limit]
        ],
        'copy_exam_timetable': lambda qs: [
            {'id': row.id, 'label': f'{row.examID.name if row.examID else "N/A"} / {row.subjectID.name if row.subjectID else "N/A"}', 'meta': f'{_class_label(row.standardID)} | {row.examDate.isoformat() if row.examDate else "N/A"}'}
            for row in qs.select_related('examID', 'subjectID', 'standardID').order_by('examDate', 'id')[:limit]
        ],
        'copy_exam_component_types': lambda qs: [
            {'id': row.id, 'label': row.name or 'N/A', 'meta': row.code or ''}
            for row in qs.order_by('displayOrder', 'name', 'id')[:limit]
        ],
        'copy_component_rules': lambda qs: [
            {'id': row.id, 'label': _component_rule_label(row), 'meta': f'Max {row.maxMarks}, Pass {row.passMarks}'}
            for row in qs.select_related('examID__examID', 'subjectID__subjectID', 'componentTypeID').order_by('id')[:limit]
        ],
        'copy_co_scholastic_areas': lambda qs: [
            {'id': row.id, 'label': row.name or 'N/A', 'meta': row.code or ''}
            for row in qs.order_by('displayOrder', 'name', 'id')[:limit]
        ],
        'copy_grading_setup': lambda qs: [
            {'id': row.id, 'label': row.name or 'N/A', 'meta': 'Default' if row.isDefault else ''}
            for row in qs.order_by('name', 'id')[:limit]
        ],
        'copy_grading_bands': lambda qs: [
            {'id': row.id, 'label': row.gradeLabel or 'N/A', 'meta': f'{row.minPercentage}-{row.maxPercentage}%'}
            for row in qs.order_by('policyID__name', 'displayOrder', 'id')[:limit]
        ],
        'copy_pass_policies': lambda qs: [
            {'id': row.id, 'label': _pass_policy_label(row), 'meta': f'Overall pass: {row.overallPassMarks if row.overallPassMarks is not None else "Auto"}'}
            for row in qs.select_related('examID__examID').order_by('id')[:limit]
        ],
        'copy_leave_types': lambda qs: [
            {'id': row.id, 'label': row.name or 'N/A', 'meta': row.applicableFor or ''}
            for row in qs.order_by('name', 'id')[:limit]
        ],
        'copy_event_types': lambda qs: [
            {'id': row.id, 'label': row.name or 'N/A', 'meta': row.audience or ''}
            for row in qs.order_by('name', 'id')[:limit]
        ],
        'copy_students': lambda qs: [
            {'id': row.id, 'label': row.name or 'N/A', 'meta': _class_label(row.standardID)}
            for row in qs.select_related('standardID').order_by('name', 'id')[:limit]
        ],
        'copy_parents': lambda qs: [
            {'id': row.id, 'label': row.fatherName or row.motherName or 'N/A', 'meta': row.phoneNumber or ''}
            for row in qs.order_by('fatherName', 'id')[:limit]
        ],
    }
    handler = handlers.get(option_key, lambda qs: [])
    return with_selection(handler(queryset))


def _teacher_subject_meta(row):
    assigned = getattr(row, 'assignedSubjectID', None)
    if not assigned:
        return row.subjectBranch or ''
    class_name = _class_label(getattr(assigned, 'standardID', None))
    subject_name = assigned.subjectID.name if getattr(assigned, 'subjectID', None) else 'N/A'
    branch = f' | {row.subjectBranch}' if row.subjectBranch else ''
    return f'{class_name} -> {subject_name}{branch}'


def _component_rule_label(row):
    exam_name = row.examID.examID.name if getattr(row, 'examID', None) and getattr(row.examID, 'examID', None) else 'N/A'
    subject_name = row.subjectID.subjectID.name if getattr(row, 'subjectID', None) and getattr(row.subjectID, 'subjectID', None) else 'N/A'
    component_name = row.componentTypeID.name if getattr(row, 'componentTypeID', None) else 'N/A'
    return f'{exam_name} / {subject_name} / {component_name}'


def _pass_policy_label(row):
    return row.examID.examID.name if getattr(row, 'examID', None) and getattr(row.examID, 'examID', None) else 'N/A'
