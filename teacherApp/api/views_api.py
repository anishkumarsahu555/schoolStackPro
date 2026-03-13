from datetime import datetime, timedelta
import json

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q, Sum, F
from django.http import JsonResponse
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.html import escape, strip_tags
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django_datatables_view.base_datatable_view import BaseDatatableView

from homeApp.models import SchoolSession
from homeApp.session_utils import get_session_month_sequence
from managementApp.models import (
    Student,
    TeacherDetail,
    AssignSubjectsToTeacher,
    AssignSubjectsToClass,
    AssignExamToClass,
    ExamComponentType,
    ExamSubjectComponentRule,
    PassPolicy,
    MarkOfStudentsByExam,
    StudentExamComponentMark,
    Event,
    StudentFee,
    Standard,
    TeacherAttendance,
    LeaveApplication,
    TermTeacherRemark,
    ProgressReport,
)
from managementApp.reporting import build_report_cards_for_student, upsert_progress_report_snapshot
from teacherApp.models import SubjectNote, SubjectNoteVersion
from managementApp.signals import pre_save_with_user
from utils.conts import MONTHS_LIST
from utils.custom_decorators import check_groups
from utils.custom_response import SuccessResponse, ErrorResponse
from utils.image_utils import safe_image_url, avatar_image_html
from utils.json_validator import validate_input


def _safe_image_url(image_field, fallback_path='images/default_avatar.svg'):
    return safe_image_url(image_field, fallback_path=fallback_path)


def _avatar_image_html(image_field):
    return avatar_image_html(image_field)


def _teacher_daily_status_priority(status):
    ranking = {'absent': 1, 'leave': 2, 'present': 3}
    return ranking.get(status, 0)


def _teacher_status_from_row(is_present, reason):
    if is_present:
        return 'present'
    if (reason or '').strip().lower().startswith('approved leave'):
        return 'leave'
    return 'absent'


def _compact_fee_month(month_value, year_value):
    short_month = month_value
    if month_value:
        try:
            short_month = datetime.strptime(month_value, '%B').strftime('%b')
        except ValueError:
            short_month = month_value
    if short_month and year_value:
        return f'{short_month}-{year_value}'
    return short_month or 'N/A'


def _session_month_rows(session_id):
    session_obj = SchoolSession.objects.filter(pk=session_id, isDeleted=False).first() if session_id else None
    return get_session_month_sequence(session_obj)


def _restrict_fee_queryset_to_session_months(qs, session_id):
    session_month_rows = _session_month_rows(session_id)
    if not session_month_rows:
        return qs.none()

    ym_filter = Q()
    month_name_filter = Q()
    for month_name, year_value, month_no, _, _ in session_month_rows:
        ym_filter |= Q(feeYear=year_value, feeMonth=month_no)
        month_name_filter |= Q(month__iexact=month_name)

    legacy_filter = (Q(feeYear__isnull=True) | Q(feeMonth__isnull=True)) & month_name_filter
    return qs.filter(ym_filter | legacy_filter)


@method_decorator(login_required, name='dispatch')
@method_decorator(check_groups('Teaching'), name='dispatch')
class TeacherStudentsListJson(BaseDatatableView):
    order_columns = ['name', 'roll', 'standardID__name', 'gender', 'phoneNumber', 'email', 'action']

    def get_initial_queryset(self):
        queryset = Student.objects.select_related('standardID').filter(
            isDeleted=False,
        )

        current_session = self.request.session.get('current_session', {})
        current_session_id = current_session.get('Id')
        if current_session_id:
            queryset = queryset.filter(sessionID_id=current_session_id)

        selected_standard = self.request.GET.get('standard', '').strip()
        if not selected_standard.isdigit():
            return queryset.none()

        queryset = queryset.filter(standardID_id=int(selected_standard))

        return queryset

    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(name__icontains=search)
                | Q(roll__icontains=search)
                | Q(standardID__name__icontains=search)
                | Q(standardID__section__icontains=search)
                | Q(gender__icontains=search)
                | Q(phoneNumber__icontains=search)
                | Q(email__icontains=search)
            )
        return qs

    def prepare_results(self, qs):
        data = []
        for item in qs:
            class_name = 'N/A'
            if item.standardID:
                class_name = item.standardID.name or 'N/A'
                if item.standardID.section:
                    class_name = f'{class_name} - {item.standardID.section}'

            view_url = reverse('teacherApp:teacher_student_detail', kwargs={'id': item.pk})
            action = (
                f'<a href="{view_url}" data-inverted="" data-tooltip="View Detail" '
                f'data-position="left center" data-variation="mini" style="font-size:10px;" '
                f'class="ui circular facebook icon button purple">'
                f'<i class="eye icon"></i></a>'
            )

            data.append([
                escape(item.name or 'N/A'),
                escape(item.roll or 'N/A'),
                escape(class_name),
                escape(item.gender or 'N/A'),
                escape(item.phoneNumber or 'N/A'),
                escape(item.email or 'N/A'),
                action,
            ])

        return data


@method_decorator(login_required, name='dispatch')
@method_decorator(check_groups('Teaching'), name='dispatch')
class TeacherAssignedSubjectsListJson(BaseDatatableView):
    order_columns = ['assignedSubjectID__standardID__name', 'assignedSubjectID__standardID__section',
                     'assignedSubjectID__subjectID__name', 'subjectBranch', 'datetime']

    def get_initial_queryset(self):
        teacher = TeacherDetail.objects.filter(
            userID_id=self.request.user.id,
            isDeleted=False,
        ).order_by('-datetime').first()
        if not teacher:
            return AssignSubjectsToTeacher.objects.none()

        queryset = AssignSubjectsToTeacher.objects.select_related(
            'assignedSubjectID',
            'assignedSubjectID__standardID',
            'assignedSubjectID__subjectID',
        ).filter(
            isDeleted=False,
            teacherID_id=teacher.id,
            assignedSubjectID__isDeleted=False,
            assignedSubjectID__standardID__isDeleted=False,
            assignedSubjectID__subjectID__isDeleted=False,
        )

        current_session = self.request.session.get('current_session', {})
        current_session_id = current_session.get('Id') or teacher.sessionID_id
        if current_session_id:
            queryset = queryset.filter(sessionID_id=current_session_id)

        return queryset

    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(assignedSubjectID__standardID__name__icontains=search)
                | Q(assignedSubjectID__standardID__section__icontains=search)
                | Q(assignedSubjectID__subjectID__name__icontains=search)
                | Q(subjectBranch__icontains=search)
            )
        return qs

    def prepare_results(self, qs):
        data = []
        for item in qs:
            class_name = item.assignedSubjectID.standardID.name or 'N/A'
            section = item.assignedSubjectID.standardID.section or 'N/A'
            subject_name = item.assignedSubjectID.subjectID.name or 'N/A'
            branch = item.subjectBranch or 'N/A'
            added_on = item.datetime.strftime('%d-%m-%Y %I:%M %p') if item.datetime else 'N/A'

            data.append([
                escape(class_name),
                escape(section),
                escape(subject_name),
                escape(branch),
                escape(added_on),
            ])
        return data


@method_decorator(login_required, name='dispatch')
@method_decorator(check_groups('Teaching'), name='dispatch')
class TeacherEventListJson(BaseDatatableView):
    order_columns = ['eventID__name', 'eventID__audience', 'title', 'startDate', 'endDate', 'message', 'datetime']

    def get_initial_queryset(self):
        queryset = Event.objects.select_related('eventID').filter(
            isDeleted=False
        ).filter(
            Q(eventID__isnull=True) | Q(eventID__audience__in=['general', 'teacherapp', 'all_apps'])
        )
        current_session = self.request.session.get('current_session', {})
        current_session_id = current_session.get('Id')
        if current_session_id:
            queryset = queryset.filter(sessionID_id=current_session_id)
        return queryset

    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(eventID__name__icontains=search)
                | Q(eventID__audience__icontains=search)
                | Q(title__icontains=search)
                | Q(startDate__icontains=search)
                | Q(endDate__icontains=search)
                | Q(message__icontains=search)
                | Q(datetime__icontains=search)
            )
        return qs

    def prepare_results(self, qs):
        data = []
        for item in qs:
            event_type = item.eventID.name if item.eventID and item.eventID.name else 'General Announcement'
            audience = item.eventID.get_audience_display() if item.eventID else 'General'
            data.append([
                escape(event_type),
                escape(audience),
                escape(item.title or 'N/A'),
                escape(item.startDate.strftime('%d-%m-%Y') if item.startDate else 'N/A'),
                escape(item.endDate.strftime('%d-%m-%Y') if item.endDate else 'N/A'),
                escape(item.message or 'N/A'),
                escape(item.datetime.strftime('%d-%m-%Y %I:%M %p') if item.datetime else 'N/A'),
            ])
        return data


@login_required
@check_groups('Teaching')
def get_assigned_class_list_api(request):
    teacher, assigned_class_ids = _get_teacher_and_assigned_class_ids(request)
    if not teacher:
        return JsonResponse({'success': True, 'data': []}, safe=False)
    current_session_id = request.session.get('current_session', {}).get('Id') or teacher.sessionID_id

    classes = Standard.objects.filter(
        isDeleted=False,
        sessionID_id=current_session_id,
        id__in=assigned_class_ids,
    ).order_by('name', 'section')

    data = []
    for std in classes:
        std_name = std.name or 'N/A'
        if std.section:
            std_name = f'{std_name} - {std.section}'
        data.append({'ID': std.pk, 'Name': std_name})

    return JsonResponse({'success': True, 'data': data}, safe=False)


@method_decorator(login_required, name='dispatch')
@method_decorator(check_groups('Teaching'), name='dispatch')
class TeacherAssignedClassFeeListJson(BaseDatatableView):
    order_columns = ['name', 'roll', 'standardID__name', 'admissionFee', 'tuitionFee', 'miscFee', 'totalFee']

    def get_initial_queryset(self):
        teacher, assigned_class_ids = _get_teacher_and_assigned_class_ids(self.request)
        if not teacher:
            return Student.objects.none()

        current_session_id = self.request.session.get('current_session', {}).get('Id') or teacher.sessionID_id

        queryset = Student.objects.select_related('standardID').filter(
            isDeleted=False,
            sessionID_id=current_session_id,
            standardID_id__in=assigned_class_ids,
        )

        selected_standard = self.request.GET.get('standard', '').strip()
        if not selected_standard.isdigit():
            return queryset.none()

        selected_standard_id = int(selected_standard)
        if selected_standard_id not in set(assigned_class_ids):
            return queryset.none()

        return queryset.filter(standardID_id=selected_standard_id)

    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(name__icontains=search)
                | Q(roll__icontains=search)
                | Q(standardID__name__icontains=search)
                | Q(standardID__section__icontains=search)
                | Q(admissionFee__icontains=search)
                | Q(tuitionFee__icontains=search)
                | Q(miscFee__icontains=search)
                | Q(totalFee__icontains=search)
            )
        return qs

    def prepare_results(self, qs):
        current_session_id = self.request.session.get('current_session', {}).get('Id')
        data = []
        for item in qs:
            class_name = 'N/A'
            if item.standardID:
                class_name = item.standardID.name or 'N/A'
                if item.standardID.section:
                    class_name = f'{class_name} - {item.standardID.section}'

            fee_qs = StudentFee.objects.filter(
                isDeleted=False,
                studentID_id=item.pk,
            )
            if current_session_id:
                fee_qs = fee_qs.filter(sessionID_id=current_session_id)

            paid_amount = fee_qs.filter(isPaid=True).aggregate(total=Sum('amount')).get('total') or 0
            paid_months = fee_qs.filter(isPaid=True).values_list('month', flat=True).distinct().count()

            total_fee = item.totalFee or 0
            pending_amount = total_fee - paid_amount
            if pending_amount < 0:
                pending_amount = 0

            view_url = reverse('teacherApp:teacher_student_detail', kwargs={'id': item.pk})
            action = (
                f'<a href="{view_url}" data-inverted="" data-tooltip="View Detail" '
                f'data-position="left center" data-variation="mini" style="font-size:10px;" '
                f'class="ui circular facebook icon button purple">'
                f'<i class="eye icon"></i></a>'
            )

            data.append([
                escape(item.name or 'N/A'),
                escape(item.roll or 'N/A'),
                escape(class_name),
                escape(f'₹{(item.admissionFee or 0):.2f}'),
                escape(f'₹{(item.tuitionFee or 0):.2f}'),
                escape(f'₹{(item.miscFee or 0):.2f}'),
                escape(f'₹{total_fee:.2f}'),
                escape(f'₹{paid_amount:.2f}'),
                escape(f'₹{pending_amount:.2f}'),
                escape(str(paid_months)),
                action,
            ])

        return data


def _get_teacher_and_assigned_class_ids(request):
    teacher = TeacherDetail.objects.filter(
        userID_id=request.user.id,
        isDeleted=False,
    ).order_by('-datetime').first()
    if not teacher:
        return None, []

    current_session_id = request.session.get('current_session', {}).get('Id') or teacher.sessionID_id
    class_teacher_ids = list(Standard.objects.filter(
        isDeleted=False,
        sessionID_id=current_session_id,
        classTeacher_id=teacher.id,
    ).values_list('id', flat=True))
    subject_assigned_class_ids = list(AssignSubjectsToTeacher.objects.filter(
        isDeleted=False,
        teacherID_id=teacher.id,
        sessionID_id=current_session_id,
        assignedSubjectID__isDeleted=False,
    ).values_list('assignedSubjectID__standardID_id', flat=True).distinct())
    class_ids = sorted({cid for cid in (class_teacher_ids + subject_assigned_class_ids) if cid})
    return teacher, class_ids


def _teacher_current_ids(request, teacher):
    current_session = request.session.get('current_session', {})
    session_id = current_session.get('Id') or teacher.sessionID_id
    school_id = current_session.get('SchoolID') or teacher.schoolID_id
    return session_id, school_id


def _editor_name(user):
    if not user:
        return 'System'
    display_name = f'{(user.first_name or "").strip()} {(user.last_name or "").strip()}'.strip()
    return display_name or (user.username or 'System')


def _as_bool(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {'1', 'true', 'yes', 'on'}


def _teacher_is_class_teacher_for_standard(teacher_id, session_id, standard_id):
    return Standard.objects.filter(
        id=standard_id,
        isDeleted=False,
        sessionID_id=session_id,
        classTeacher_id=teacher_id,
    ).exists()


def _teacher_can_manage_subject(teacher_id, session_id, standard_id, subject_assign_id):
    if _teacher_is_class_teacher_for_standard(teacher_id, session_id, standard_id):
        return True
    return AssignSubjectsToTeacher.objects.filter(
        isDeleted=False,
        teacherID_id=teacher_id,
        sessionID_id=session_id,
        assignedSubjectID_id=subject_assign_id,
        assignedSubjectID__isDeleted=False,
        assignedSubjectID__standardID_id=standard_id,
    ).exists()


def _component_rules_for_exam_subject(session_id, exam_id, subject_id):
    return list(
        ExamSubjectComponentRule.objects.select_related('componentTypeID').filter(
            isDeleted=False,
            sessionID_id=session_id,
            examID_id=exam_id,
            subjectID_id=subject_id,
        ).order_by('displayOrder', 'id')
    )


def _component_input_html(mark_row_id, student_id, rules, component_mark_map):
    blocks = []
    for rule in rules:
        comp_obj = component_mark_map.get((student_id, rule.id))
        value = ''
        is_absent = False
        is_exempt = False
        note = ''
        if comp_obj:
            value = '' if comp_obj.marksObtained is None else comp_obj.marksObtained
            is_absent = bool(comp_obj.isAbsent)
            is_exempt = bool(comp_obj.isExempt)
            note = comp_obj.note or ''

        blocks.append(
            f'''<div class="component-entry-card">
<div class="component-entry-top">
  <div class="component-entry-title">{escape(rule.componentTypeID.name if rule.componentTypeID else 'Component')} <span>(Max {escape(rule.maxMarks)})</span></div>
  <div class="component-entry-flags">
    <label class="component-flag"><input type="checkbox" id="compabs{mark_row_id}_{rule.id}" {'checked' if is_absent else ''}> Absent</label>
    <label class="component-flag"><input type="checkbox" id="compexm{mark_row_id}_{rule.id}" {'checked' if is_exempt else ''}> Exempt</label>
  </div>
</div>
<div class="component-entry-fields">
  <div class="ui mini input fluid component-entry-mark">
    <input type="number" min="0" step="0.01" placeholder="Marks" id="compmark{mark_row_id}_{rule.id}" value="{escape(value)}">
  </div>
  <div class="ui mini input fluid component-entry-note">
    <input type="text" placeholder="Note" id="compnote{mark_row_id}_{rule.id}" value="{escape(note)}">
  </div>
</div>
</div>'''
        )
    return ''.join(blocks)


@transaction.atomic
@csrf_exempt
@login_required
@check_groups('Teaching')
def teacher_publish_progress_report_api(request):
    if request.method != 'POST':
        return ErrorResponse('Method not allowed.', status_code=405).to_json_response()

    teacher, assigned_class_ids = _get_teacher_and_assigned_class_ids(request)
    if not teacher:
        return ErrorResponse('Teacher profile not found.', extra={'color': 'red'}).to_json_response()

    current_session_id = request.session.get('current_session', {}).get('Id') or teacher.sessionID_id
    current_school_id = request.session.get('current_session', {}).get('SchoolID') or teacher.schoolID_id

    standard = (request.POST.get('standard') or '').strip()
    student = (request.POST.get('student') or '').strip()
    exam = (request.POST.get('exam') or '').strip()
    exam_ids_raw = (request.POST.get('exam_ids') or '').strip()
    status = (request.POST.get('status') or 'published').strip().lower()
    if status not in {'draft', 'reviewed', 'published'}:
        status = 'published'

    if not (standard.isdigit() and student.isdigit()):
        return ErrorResponse('Invalid class/student.', extra={'color': 'red'}).to_json_response()
    if exam and exam != 'all' and not exam.isdigit():
        return ErrorResponse('Invalid exam.', extra={'color': 'red'}).to_json_response()

    standard_id = int(standard)
    if standard_id not in set(assigned_class_ids):
        return ErrorResponse('You can publish reports only for your assigned class.', extra={'color': 'red'}).to_json_response()

    explicit_exam_ids = []
    if exam_ids_raw:
        for token in exam_ids_raw.split(','):
            exam_id_value = token.strip()
            if not exam_id_value:
                continue
            if not exam_id_value.isdigit():
                return ErrorResponse('Invalid visible exam list.', extra={'color': 'red'}).to_json_response()
            explicit_exam_ids.append(int(exam_id_value))
        explicit_exam_ids = sorted(set(explicit_exam_ids))

    student_obj = Student.objects.filter(
        id=int(student),
        isDeleted=False,
        sessionID_id=current_session_id,
        standardID_id=standard_id,
    ).first()
    if not student_obj:
        return ErrorResponse('Student not found.', extra={'color': 'red'}).to_json_response()

    exam_queryset = AssignExamToClass.objects.select_related('examID').filter(
        isDeleted=False,
        sessionID_id=current_session_id,
        standardID_id=standard_id,
    )
    if explicit_exam_ids:
        exam_queryset = exam_queryset.filter(id__in=explicit_exam_ids)
    elif exam and exam != 'all':
        exam_queryset = exam_queryset.filter(id=int(exam))

    if not exam_queryset.exists():
        return ErrorResponse('No exams found for selected filters.', extra={'color': 'red'}).to_json_response()

    selected_exam_ids = list(exam_queryset.values_list('id', flat=True))
    skipped_not_ready = 0
    if status == 'published':
        ready_exam_ids = set(
            ProgressReport.objects.filter(
                isDeleted=False,
                sessionID_id=current_session_id,
                studentID_id=student_obj.id,
                examID_id__in=selected_exam_ids,
                readyToPublish=True,
            ).values_list('examID_id', flat=True)
        )
        eligible_exam_ids = [exam_id for exam_id in selected_exam_ids if exam_id in ready_exam_ids]
        skipped_not_ready = len(selected_exam_ids) - len(eligible_exam_ids)
        if not eligible_exam_ids:
            return ErrorResponse(
                'No selected report is marked Ready to Publish.',
                extra={'color': 'orange'}
            ).to_json_response()
        exam_queryset = exam_queryset.filter(id__in=eligible_exam_ids)

    report_cards = build_report_cards_for_student(
        current_session_id=current_session_id,
        student_obj=student_obj,
        standard_id=standard_id,
        exam_queryset=exam_queryset,
        prefer_published_snapshot=False,
    )
    card_map = {
        int(card.get('exam_assignment_id')): card
        for card in report_cards
        if str(card.get('exam_assignment_id')).isdigit()
    }

    snapshot_count = 0
    for exam_obj in exam_queryset:
        payload = card_map.get(exam_obj.id)
        if not payload:
            continue
        upsert_progress_report_snapshot(
            current_session_id=current_session_id,
            school_id=current_school_id or student_obj.schoolID_id,
            student_id=student_obj.id,
            standard_id=standard_id,
            exam_id=exam_obj.id,
            payload=payload,
            status=status,
            user_obj=request.user,
        )
        snapshot_count += 1

    if snapshot_count == 0:
        return ErrorResponse('No report data available to publish.', extra={'color': 'red'}).to_json_response()

    action_label = 'published' if status == 'published' else 'updated'
    message = f'Progress report {action_label} successfully.'
    if skipped_not_ready > 0:
        message += f' Skipped {skipped_not_ready} not-ready report(s).'
    return SuccessResponse(
        message,
        data={'snapshotsSaved': snapshot_count},
        extra={'color': 'green'}
    ).to_json_response()


@transaction.atomic
@csrf_exempt
@login_required
@check_groups('Teaching')
def teacher_set_progress_report_ready_state_api(request):
    if request.method != 'POST':
        return ErrorResponse('Method not allowed.', status_code=405).to_json_response()

    teacher, assigned_class_ids = _get_teacher_and_assigned_class_ids(request)
    if not teacher:
        return ErrorResponse('Teacher profile not found.', extra={'color': 'red'}).to_json_response()

    standard = (request.POST.get('standard') or '').strip()
    student = (request.POST.get('student') or '').strip()
    exam = (request.POST.get('exam') or '').strip()
    ready_raw = (request.POST.get('ready') or '').strip().lower()
    if not (standard.isdigit() and student.isdigit() and exam.isdigit()):
        return ErrorResponse('Invalid class/student/exam.', extra={'color': 'red'}).to_json_response()

    standard_id = int(standard)
    student_id = int(student)
    exam_id = int(exam)
    if standard_id not in set(assigned_class_ids):
        return ErrorResponse('You can update ready state only for assigned class.', extra={'color': 'red'}).to_json_response()

    ready_value = ready_raw in {'1', 'true', 'yes', 'on'}
    current_session_id, current_school_id = _teacher_current_ids(request, teacher)

    student_obj = Student.objects.filter(
        id=student_id,
        isDeleted=False,
        sessionID_id=current_session_id,
        standardID_id=standard_id,
    ).first()
    if not student_obj:
        return ErrorResponse('Student not found.', extra={'color': 'red'}).to_json_response()

    exam_obj = AssignExamToClass.objects.filter(
        id=exam_id,
        isDeleted=False,
        sessionID_id=current_session_id,
        standardID_id=standard_id,
    ).first()
    if not exam_obj:
        return ErrorResponse('Exam not found for selected class.', extra={'color': 'red'}).to_json_response()

    report_obj = ProgressReport.objects.filter(
        isDeleted=False,
        sessionID_id=current_session_id,
        studentID_id=student_id,
        examID_id=exam_id,
    ).first()
    if not report_obj:
        report_obj = ProgressReport(
            schoolID_id=current_school_id or student_obj.schoolID_id,
            sessionID_id=current_session_id,
            examID_id=exam_id,
            studentID_id=student_id,
            standardID_id=standard_id,
            status='draft',
            readyToPublish=ready_value,
        )
    else:
        report_obj.standardID_id = standard_id
        report_obj.readyToPublish = ready_value

    pre_save_with_user.send(sender=ProgressReport, instance=report_obj, user=request.user.pk)

    return SuccessResponse(
        'Ready to Publish updated successfully.',
        data={'readyToPublish': bool(report_obj.readyToPublish)},
        extra={'color': 'green'}
    ).to_json_response()


@transaction.atomic
@csrf_exempt
@login_required
@check_groups('Teaching')
def teacher_upsert_term_remark_api(request):
    if request.method != 'POST':
        return ErrorResponse('Method not allowed.', status_code=405).to_json_response()

    teacher, assigned_class_ids = _get_teacher_and_assigned_class_ids(request)
    if not teacher:
        return ErrorResponse('Teacher profile not found.', extra={'color': 'red'}).to_json_response()

    standard = (request.POST.get('standard') or '').strip()
    student = (request.POST.get('student') or '').strip()
    exam = (request.POST.get('exam') or '').strip()
    overall_remark = (request.POST.get('overall_remark') or '').strip()
    overall_result = (request.POST.get('overall_result') or '').strip().lower()
    if overall_result not in {'', 'auto', 'pass', 'fail'}:
        return ErrorResponse('Invalid overall result option.', extra={'color': 'red'}).to_json_response()

    if not (standard.isdigit() and student.isdigit() and exam.isdigit()):
        return ErrorResponse('Invalid class/student/exam.', extra={'color': 'red'}).to_json_response()

    standard_id = int(standard)
    student_id = int(student)
    exam_id = int(exam)
    if standard_id not in set(assigned_class_ids):
        return ErrorResponse('You can update remark only for assigned class.', extra={'color': 'red'}).to_json_response()

    current_session_id, current_school_id = _teacher_current_ids(request, teacher)
    student_obj = Student.objects.filter(
        id=student_id,
        isDeleted=False,
        sessionID_id=current_session_id,
        standardID_id=standard_id,
    ).first()
    if not student_obj:
        return ErrorResponse('Student not found.', extra={'color': 'red'}).to_json_response()

    exam_obj = AssignExamToClass.objects.filter(
        id=exam_id,
        isDeleted=False,
        sessionID_id=current_session_id,
        standardID_id=standard_id,
    ).first()
    if not exam_obj:
        return ErrorResponse('Exam not found for selected class.', extra={'color': 'red'}).to_json_response()

    remark_obj = TermTeacherRemark.objects.filter(
        isDeleted=False,
        sessionID_id=current_session_id,
        studentID_id=student_id,
        examID_id=exam_id,
    ).first()
    if not remark_obj:
        remark_obj = TermTeacherRemark(
            schoolID_id=current_school_id,
            sessionID_id=current_session_id,
            examID_id=exam_id,
            studentID_id=student_id,
            standardID_id=standard_id,
        )

    remark_obj.overallRemark = overall_remark
    is_auto_mode = overall_result in {'', 'auto'}
    remark_obj.overallResultDecision = '' if is_auto_mode else overall_result
    remark_obj.resultDecidedByRole = '' if is_auto_mode else 'teacher'
    pre_save_with_user.send(sender=TermTeacherRemark, instance=remark_obj, user=request.user.pk)

    # Keep student-facing published cards in sync when report is already published.
    published_exists = ProgressReport.objects.filter(
        isDeleted=False,
        sessionID_id=current_session_id,
        studentID_id=student_id,
        examID_id=exam_id,
        status='published',
    ).exists()
    if published_exists:
        live_exam_qs = AssignExamToClass.objects.filter(
            isDeleted=False,
            sessionID_id=current_session_id,
            standardID_id=standard_id,
            id=exam_id,
        )
        live_cards = build_report_cards_for_student(
            current_session_id=current_session_id,
            student_obj=student_obj,
            standard_id=standard_id,
            exam_queryset=live_exam_qs,
            prefer_published_snapshot=False,
        )
        payload = next((row for row in live_cards if int(row.get('exam_assignment_id', 0)) == exam_id), None)
        if payload:
            upsert_progress_report_snapshot(
                current_session_id=current_session_id,
                school_id=current_school_id or student_obj.schoolID_id,
                student_id=student_obj.id,
                standard_id=standard_id,
                exam_id=exam_id,
                payload=payload,
                status='published',
                user_obj=request.user,
            )

    return SuccessResponse(
        'Teacher remark saved successfully.',
        data={
            'overallRemark': remark_obj.overallRemark or '',
            'overallResultDecision': remark_obj.overallResultDecision or '',
            'resultDecidedByRole': remark_obj.resultDecidedByRole or '',
        },
        extra={'color': 'green'}
    ).to_json_response()


@login_required
@check_groups('Teaching')
def teacher_get_exam_list_by_class_api(request):
    teacher, assigned_class_ids = _get_teacher_and_assigned_class_ids(request)
    if not teacher:
        return JsonResponse({'success': True, 'data': []}, safe=False)

    standard = (request.GET.get('standard') or '').strip()
    if not standard.isdigit():
        return JsonResponse({'success': True, 'data': []}, safe=False)

    standard_id = int(standard)
    if standard_id not in set(assigned_class_ids):
        return JsonResponse({'success': True, 'data': []}, safe=False)

    current_session_id, _ = _teacher_current_ids(request, teacher)
    rows = AssignExamToClass.objects.select_related('examID').filter(
        isDeleted=False,
        sessionID_id=current_session_id,
        standardID_id=standard_id,
    ).order_by('examID__name')

    data = [{'ID': row.id, 'Name': row.examID.name if row.examID and row.examID.name else 'N/A'} for row in rows]
    return JsonResponse({'success': True, 'data': data}, safe=False)


@login_required
@check_groups('Teaching')
def teacher_get_subjects_to_class_assign_list_with_given_class_api(request):
    teacher, assigned_class_ids = _get_teacher_and_assigned_class_ids(request)
    if not teacher:
        return JsonResponse({'success': True, 'data': []}, safe=False)

    standard = (request.GET.get('standard') or '').strip()
    if not standard.isdigit():
        return JsonResponse({'success': True, 'data': []}, safe=False)

    standard_id = int(standard)
    if standard_id not in set(assigned_class_ids):
        return JsonResponse({'success': True, 'data': []}, safe=False)

    current_session_id, _ = _teacher_current_ids(request, teacher)
    is_class_teacher = _teacher_is_class_teacher_for_standard(teacher.id, current_session_id, standard_id)

    if is_class_teacher:
        subject_rows = AssignSubjectsToClass.objects.filter(
            isDeleted=False,
            sessionID_id=current_session_id,
            standardID_id=standard_id,
        ).values('id', 'subjectID__name').order_by('subjectID__name')
    else:
        subject_rows = AssignSubjectsToTeacher.objects.filter(
            isDeleted=False,
            teacherID_id=teacher.id,
            sessionID_id=current_session_id,
            assignedSubjectID__isDeleted=False,
            assignedSubjectID__standardID_id=standard_id,
        ).values(
            'assignedSubjectID_id',
            'assignedSubjectID__subjectID__name',
        ).order_by('assignedSubjectID__subjectID__name')

    data = []
    if is_class_teacher:
        data = [{'ID': row['id'], 'Name': row['subjectID__name'] or 'N/A'} for row in subject_rows]
    else:
        data = [{'ID': row['assignedSubjectID_id'], 'Name': row['assignedSubjectID__subjectID__name'] or 'N/A'} for row in subject_rows]
    return JsonResponse({'success': True, 'data': data}, safe=False)


@login_required
@check_groups('Teaching')
def teacher_get_exam_component_type_list_api(request):
    teacher, _ = _get_teacher_and_assigned_class_ids(request)
    if not teacher:
        return ErrorResponse('Teacher profile not found.', extra={'color': 'red'}).to_json_response()

    current_session_id, current_school_id = _teacher_current_ids(request, teacher)
    if not current_session_id:
        return ErrorResponse('Session not found.', extra={'color': 'red'}).to_json_response()

    defaults = [
        ('theory', 'Theory', 1),
        ('practical', 'Practical', 2),
        ('internal', 'Internal Assessment', 3),
    ]
    for code, name, order in defaults:
        if not ExamComponentType.objects.filter(
            isDeleted=False,
            sessionID_id=current_session_id,
            schoolID_id=current_school_id,
            code=code,
        ).exists():
            ExamComponentType.objects.create(
                schoolID_id=current_school_id,
                sessionID_id=current_session_id,
                code=code,
                name=name,
                displayOrder=order,
                lastEditedBy=_editor_name(request.user),
                updatedByUserID=request.user,
            )

    rows = ExamComponentType.objects.filter(
        isDeleted=False,
        sessionID_id=current_session_id,
        schoolID_id=current_school_id,
    ).order_by('displayOrder', 'name')
    data = [{
        'id': row.id,
        'name': row.name or 'N/A',
        'code': row.code or '',
        'isScholastic': row.isScholastic,
    } for row in rows]
    return SuccessResponse('Component types loaded.', data=data).to_json_response()


@transaction.atomic
@csrf_exempt
@login_required
@check_groups('Teaching')
def teacher_add_exam_component_type_api(request):
    if request.method != 'POST':
        return ErrorResponse('Method not allowed.', status_code=405).to_json_response()

    teacher, _ = _get_teacher_and_assigned_class_ids(request)
    if not teacher:
        return ErrorResponse('Teacher profile not found.', extra={'color': 'red'}).to_json_response()

    current_session_id, current_school_id = _teacher_current_ids(request, teacher)
    if not current_session_id or not current_school_id:
        return ErrorResponse('Session context not found.', extra={'color': 'red'}).to_json_response()

    name = (request.POST.get('name') or '').strip()
    code = (request.POST.get('code') or '').strip().lower()
    is_scholastic = _as_bool(request.POST.get('isScholastic', True), default=True)

    if not name:
        return ErrorResponse('Component type name is required.', extra={'color': 'red'}).to_json_response()
    if not code:
        return ErrorResponse('Component type code is required.', extra={'color': 'red'}).to_json_response()

    safe_code = ''.join(ch for ch in code if ch.isalnum() or ch in {'_', '-'})
    if not safe_code:
        return ErrorResponse('Component type code can only use letters, numbers, _ or -.', extra={'color': 'red'}).to_json_response()

    exists = ExamComponentType.objects.filter(
        isDeleted=False,
        sessionID_id=current_session_id,
        schoolID_id=current_school_id,
        code=safe_code,
    ).exists()
    if exists:
        return ErrorResponse('This component type code already exists in current session.', extra={'color': 'red'}).to_json_response()

    last_row = ExamComponentType.objects.filter(
        isDeleted=False,
        sessionID_id=current_session_id,
        schoolID_id=current_school_id,
    ).order_by('-displayOrder', '-id').first()
    next_order = (last_row.displayOrder if last_row else 0) + 1

    instance = ExamComponentType(
        schoolID_id=current_school_id,
        sessionID_id=current_session_id,
        name=name,
        code=safe_code,
        isScholastic=is_scholastic,
        isActive=True,
        displayOrder=next_order,
    )
    pre_save_with_user.send(sender=ExamComponentType, instance=instance, user=request.user.pk)
    return SuccessResponse(
        'Component type added successfully.',
        data={
            'id': instance.id,
            'name': instance.name,
            'code': instance.code,
            'isScholastic': instance.isScholastic,
        },
        extra={'color': 'green'}
    ).to_json_response()


@login_required
@check_groups('Teaching')
def teacher_get_exam_subject_component_rules_api(request):
    teacher, assigned_class_ids = _get_teacher_and_assigned_class_ids(request)
    if not teacher:
        return ErrorResponse('Teacher profile not found.', extra={'color': 'red'}).to_json_response()

    current_session_id, current_school_id = _teacher_current_ids(request, teacher)
    standard = (request.GET.get('standard') or '').strip()
    exam = (request.GET.get('exam') or '').strip()
    subject = (request.GET.get('subject') or '').strip()

    if not (standard.isdigit() and exam.isdigit() and subject.isdigit()):
        return ErrorResponse('Invalid class/exam/subject.', extra={'color': 'red'}).to_json_response()

    standard_id = int(standard)
    subject_id = int(subject)
    if standard_id not in set(assigned_class_ids):
        return ErrorResponse('You can access only assigned classes.', extra={'color': 'red'}).to_json_response()
    if not _teacher_can_manage_subject(teacher.id, current_session_id, standard_id, subject_id):
        return ErrorResponse('You can access only assigned subjects.', extra={'color': 'red'}).to_json_response()

    rules = _component_rules_for_exam_subject(current_session_id, int(exam), subject_id)
    pass_policy = PassPolicy.objects.filter(
        isDeleted=False,
        sessionID_id=current_session_id,
        schoolID_id=current_school_id,
        examID_id=int(exam),
    ).first()
    data = {
        'rules': [{
            'id': row.id,
            'componentTypeID': row.componentTypeID_id,
            'componentTypeName': row.componentTypeID.name if row.componentTypeID else 'N/A',
            'maxMarks': row.maxMarks,
            'passMarks': row.passMarks,
            'weightage': row.weightage,
            'isMandatory': row.isMandatory,
            'displayOrder': row.displayOrder,
        } for row in rules],
        'passPolicy': {
            'overallPassMarks': pass_policy.overallPassMarks if pass_policy else None,
            'resultComputationMode': pass_policy.resultComputationMode if pass_policy else 'total_marks',
            'requireComponentPass': pass_policy.requireComponentPass if pass_policy else True,
            'requireSubjectPass': pass_policy.requireSubjectPass if pass_policy else True,
            'requireMandatoryComponents': pass_policy.requireMandatoryComponents if pass_policy else True,
        }
    }
    return SuccessResponse('Component rules loaded.', data=data).to_json_response()


@transaction.atomic
@csrf_exempt
@login_required
@check_groups('Teaching')
def teacher_save_exam_subject_component_rules_api(request):
    if request.method != 'POST':
        return ErrorResponse('Method not allowed.', status_code=405).to_json_response()

    teacher, assigned_class_ids = _get_teacher_and_assigned_class_ids(request)
    if not teacher:
        return ErrorResponse('Teacher profile not found.', extra={'color': 'red'}).to_json_response()

    current_session_id, current_school_id = _teacher_current_ids(request, teacher)
    standard = (request.POST.get('standard') or '').strip()
    exam = (request.POST.get('exam') or '').strip()
    subject = (request.POST.get('subject') or '').strip()
    rules_raw = request.POST.get('rules') or '[]'
    pass_policy_raw = request.POST.get('pass_policy') or '{}'

    if not (standard.isdigit() and exam.isdigit() and subject.isdigit()):
        return ErrorResponse('Invalid class/exam/subject.', extra={'color': 'red'}).to_json_response()

    standard_id = int(standard)
    subject_id = int(subject)
    if standard_id not in set(assigned_class_ids):
        return ErrorResponse('You can edit only assigned classes.', extra={'color': 'red'}).to_json_response()
    if not _teacher_can_manage_subject(teacher.id, current_session_id, standard_id, subject_id):
        return ErrorResponse('You can edit only assigned subjects.', extra={'color': 'red'}).to_json_response()

    try:
        rules_payload = json.loads(rules_raw)
        pass_policy_payload = json.loads(pass_policy_raw)
    except Exception:
        return ErrorResponse('Invalid JSON payload.', extra={'color': 'red'}).to_json_response()

    assign_exam = AssignExamToClass.objects.filter(
        id=int(exam),
        isDeleted=False,
        sessionID_id=current_session_id,
        standardID_id=standard_id,
    ).first()
    assign_subject = AssignSubjectsToClass.objects.filter(
        id=subject_id,
        isDeleted=False,
        sessionID_id=current_session_id,
        standardID_id=standard_id,
    ).first()
    if not assign_exam or not assign_subject:
        return ErrorResponse('Class/exam/subject mapping not found.', extra={'color': 'red'}).to_json_response()

    active_ids = []
    for idx, row in enumerate(rules_payload):
        component_type_id = str(row.get('componentTypeID') or '').strip()
        max_marks = row.get('maxMarks')
        pass_marks = row.get('passMarks')
        weightage = row.get('weightage')
        is_mandatory = _as_bool(row.get('isMandatory', True), default=True)

        if not component_type_id.isdigit():
            return ErrorResponse(f'Invalid component type at row {idx + 1}.', extra={'color': 'red'}).to_json_response()
        try:
            max_marks = float(max_marks)
            pass_marks = float(pass_marks)
            weightage_value = None if weightage in (None, '', 'null') else float(weightage)
        except Exception:
            return ErrorResponse(f'Invalid numeric values at row {idx + 1}.', extra={'color': 'red'}).to_json_response()

        if max_marks <= 0 or pass_marks < 0 or pass_marks > max_marks:
            return ErrorResponse(f'Invalid max/pass marks at row {idx + 1}.', extra={'color': 'red'}).to_json_response()
        if weightage_value is not None and (weightage_value < 0 or weightage_value > 100):
            return ErrorResponse(f'Invalid weightage at row {idx + 1}.', extra={'color': 'red'}).to_json_response()

        component_type = ExamComponentType.objects.filter(
            id=int(component_type_id),
            isDeleted=False,
            sessionID_id=current_session_id,
        ).first()
        if not component_type:
            return ErrorResponse(f'Component type not found at row {idx + 1}.', extra={'color': 'red'}).to_json_response()

        rule_id = row.get('id')
        rule_obj = None
        if str(rule_id).isdigit():
            rule_obj = ExamSubjectComponentRule.objects.filter(
                id=int(rule_id),
                isDeleted=False,
                sessionID_id=current_session_id,
                examID_id=assign_exam.id,
                subjectID_id=assign_subject.id,
            ).first()

        if not rule_obj:
            rule_obj = ExamSubjectComponentRule(
                schoolID_id=current_school_id,
                sessionID_id=current_session_id,
                examID_id=assign_exam.id,
                subjectID_id=assign_subject.id,
            )

        rule_obj.componentTypeID = component_type
        rule_obj.maxMarks = max_marks
        rule_obj.passMarks = pass_marks
        rule_obj.weightage = weightage_value
        rule_obj.isMandatory = is_mandatory
        rule_obj.displayOrder = idx + 1
        pre_save_with_user.send(sender=ExamSubjectComponentRule, instance=rule_obj, user=request.user.pk)
        active_ids.append(rule_obj.id)

    ExamSubjectComponentRule.objects.filter(
        isDeleted=False,
        sessionID_id=current_session_id,
        examID_id=assign_exam.id,
        subjectID_id=assign_subject.id,
    ).exclude(id__in=active_ids).update(isDeleted=True)

    if isinstance(pass_policy_payload, dict):
        pass_policy, _ = PassPolicy.objects.get_or_create(
            isDeleted=False,
            sessionID_id=current_session_id,
            schoolID_id=current_school_id,
            examID_id=assign_exam.id,
            defaults={'overallPassMarks': assign_exam.passMarks},
        )
        overall_pass = pass_policy_payload.get('overallPassMarks')
        if overall_pass in (None, '', 'null'):
            pass_policy.overallPassMarks = assign_exam.passMarks
        else:
            try:
                pass_policy.overallPassMarks = float(overall_pass)
            except Exception:
                return ErrorResponse('Invalid overall pass marks.', extra={'color': 'red'}).to_json_response()

        pass_policy.resultComputationMode = pass_policy_payload.get('resultComputationMode') or 'total_marks'
        pass_policy.requireComponentPass = _as_bool(pass_policy_payload.get('requireComponentPass', True), default=True)
        pass_policy.requireSubjectPass = _as_bool(pass_policy_payload.get('requireSubjectPass', True), default=True)
        pass_policy.requireMandatoryComponents = _as_bool(pass_policy_payload.get('requireMandatoryComponents', True), default=True)
        pre_save_with_user.send(sender=PassPolicy, instance=pass_policy, user=request.user.pk)

    return SuccessResponse('Component rules saved successfully.', extra={'color': 'green'}).to_json_response()


@method_decorator(login_required, name='dispatch')
@method_decorator(check_groups('Teaching'), name='dispatch')
class TeacherMarksOfSubjectsByStudentJson(BaseDatatableView):
    order_columns = ['studentID.photo', 'studentID.name', 'studentID.roll', 'examID.fullMarks', 'examID.passMarks', 'mark', 'note', 'lastEditedBy', 'lastUpdatedOn']

    @transaction.atomic
    def get_initial_queryset(self):
        teacher, assigned_class_ids = _get_teacher_and_assigned_class_ids(self.request)
        if not teacher:
            return MarkOfStudentsByExam.objects.none()

        standard = (self.request.GET.get('standard') or '').strip()
        exam = (self.request.GET.get('exam') or '').strip()
        subject = (self.request.GET.get('subject') or '').strip()
        if not (standard.isdigit() and exam.isdigit() and subject.isdigit()):
            return MarkOfStudentsByExam.objects.none()

        standard_id = int(standard)
        exam_id = int(exam)
        subject_id = int(subject)
        if standard_id not in set(assigned_class_ids):
            return MarkOfStudentsByExam.objects.none()

        current_session_id, current_school_id = _teacher_current_ids(self.request, teacher)
        assign_exam = AssignExamToClass.objects.filter(
            id=exam_id,
            isDeleted=False,
            sessionID_id=current_session_id,
            standardID_id=standard_id,
        ).first()
        assign_subject = AssignSubjectsToClass.objects.filter(
            id=subject_id,
            isDeleted=False,
            sessionID_id=current_session_id,
            standardID_id=standard_id,
        ).first()
        if not assign_exam or not assign_subject:
            return MarkOfStudentsByExam.objects.none()
        if not _teacher_can_manage_subject(teacher.id, current_session_id, standard_id, subject_id):
            return MarkOfStudentsByExam.objects.none()

        students = Student.objects.filter(
            standardID_id=standard_id,
            isDeleted=False,
            sessionID_id=current_session_id,
        )
        for stu in students:
            mark_obj = MarkOfStudentsByExam.objects.filter(
                studentID_id=stu.pk,
                subjectID_id=subject_id,
                examID_id=exam_id,
                standardID_id=standard_id,
                sessionID_id=current_session_id,
                isDeleted=False,
            ).first()
            if mark_obj:
                continue
            instance = MarkOfStudentsByExam(
                schoolID_id=current_school_id,
                sessionID_id=current_session_id,
                studentID_id=stu.pk,
                subjectID_id=subject_id,
                examID_id=exam_id,
                standardID_id=standard_id,
            )
            pre_save_with_user.send(sender=MarkOfStudentsByExam, instance=instance, user=self.request.user.pk)

        return MarkOfStudentsByExam.objects.filter(
            standardID_id=standard_id,
            isDeleted=False,
            sessionID_id=current_session_id,
            examID_id=exam_id,
            subjectID_id=subject_id,
        )

    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(studentID__name__icontains=search)
                | Q(examID__fullMarks__icontains=search)
                | Q(examID__passMarks__icontains=search)
                | Q(mark__icontains=search)
                | Q(note__icontains=search)
                | Q(studentID__roll__icontains=search)
                | Q(lastEditedBy__icontains=search)
                | Q(lastUpdatedOn__icontains=search)
            )
        return qs

    def prepare_results(self, qs):
        exam = self.request.GET.get('exam')
        subject = self.request.GET.get('subject')
        teacher = TeacherDetail.objects.filter(
            userID_id=self.request.user.id,
            isDeleted=False,
        ).order_by('-datetime').first()
        if not teacher:
            return []
        session_id, _ = _teacher_current_ids(self.request, teacher)

        rules = []
        rule_ids = []
        component_mark_map = {}
        if str(exam).isdigit() and str(subject).isdigit():
            rules = _component_rules_for_exam_subject(session_id, int(exam), int(subject))
            rule_ids = [row.id for row in rules]
            if rule_ids:
                student_ids = [item.studentID_id for item in qs]
                comp_rows = StudentExamComponentMark.objects.filter(
                    isDeleted=False,
                    sessionID_id=session_id,
                    examID_id=int(exam),
                    subjectID_id=int(subject),
                    studentID_id__in=student_ids,
                    componentRuleID_id__in=rule_ids,
                )
                component_mark_map = {(row.studentID_id, row.componentRuleID_id): row for row in comp_rows}

        json_data = []
        for item in qs:
            action = '''<button class="ui mini primary button" onclick="pushMark({}, {})">
  Save
</button>'''.format(item.pk, 1 if rules else 0)

            marks_obtained = '''<div class="ui tiny input fluid">
  <input type="number" placeholder="Mark Obtained" name="mark{}" id="mark{}" value = "{}">
</div>
            '''.format(item.pk, item.pk, item.mark)
            full_mark = item.examID.fullMarks
            pass_mark = item.examID.passMarks
            if rules:
                full_mark = round(sum(float(r.maxMarks or 0) for r in rules), 2)
                pass_mark = round(sum(float(r.passMarks or 0) for r in rules), 2)
                marks_obtained = _component_input_html(item.pk, item.studentID_id, rules, component_mark_map) + \
                    f'''<div style="font-size:11px;color:#6b7280;">Total: {escape(item.mark or 0)}</div>'''

            note = '''<div class="ui tiny input fluid">
              <input type="text" placeholder="Note" name="note{}" id="note{}" value = "{}">
            </div>
                        '''.format(item.pk, item.pk, item.note)
            images = _avatar_image_html(item.studentID.photo if item.studentID else None)

            json_data.append([
                images,
                escape(item.studentID.name if item.studentID else 'N/A'),
                escape((item.studentID.roll if item.studentID else None) or 'N/A'),
                full_mark,
                pass_mark,
                marks_obtained,
                note,
                escape(item.lastEditedBy or 'N/A'),
                escape(item.lastUpdatedOn.strftime('%d-%m-%Y %I:%M %p') if item.lastUpdatedOn else 'N/A'),
                action,
            ])
        return json_data


@transaction.atomic
@csrf_exempt
@login_required
@check_groups('Teaching')
def teacher_add_subject_mark_api(request):
    if request.method != 'POST':
        return ErrorResponse('Method not allowed.', status_code=405).to_json_response()

    teacher, assigned_class_ids = _get_teacher_and_assigned_class_ids(request)
    if not teacher:
        return ErrorResponse('Teacher profile not found.', extra={'color': 'red'}).to_json_response()

    row_id = (request.POST.get('id') or '').strip()
    note = request.POST.get('note')
    mark = request.POST.get('mark')
    component_marks_raw = request.POST.get('component_marks') or '[]'
    if not row_id.isdigit():
        return ErrorResponse('Invalid mark row.', extra={'color': 'red'}).to_json_response()

    instance = MarkOfStudentsByExam.objects.filter(
        id=int(row_id),
        isDeleted=False,
    ).first()
    if not instance:
        return ErrorResponse('Mark row not found.', extra={'color': 'red'}).to_json_response()

    current_session_id, _ = _teacher_current_ids(request, teacher)
    if instance.sessionID_id != current_session_id:
        return ErrorResponse('Invalid session.', extra={'color': 'red'}).to_json_response()
    if instance.standardID_id not in set(assigned_class_ids):
        return ErrorResponse('You can save marks only for assigned classes.', extra={'color': 'red'}).to_json_response()
    if not _teacher_can_manage_subject(teacher.id, current_session_id, instance.standardID_id, instance.subjectID_id):
        return ErrorResponse('You can save marks only for assigned subjects.', extra={'color': 'red'}).to_json_response()

    try:
        instance.note = note
        rules = _component_rules_for_exam_subject(
            session_id=instance.sessionID_id,
            exam_id=instance.examID_id,
            subject_id=instance.subjectID_id,
        )
        if rules:
            try:
                component_rows = json.loads(component_marks_raw)
            except Exception:
                return ErrorResponse('Invalid component payload.', extra={'color': 'red'}).to_json_response()

            component_rows_map = {int(row.get('rule_id')): row for row in component_rows if str(row.get('rule_id')).isdigit()}
            total_mark = 0.0
            for rule in rules:
                row = component_rows_map.get(rule.id, {})
                is_absent = _as_bool(row.get('is_absent', False), default=False)
                is_exempt = _as_bool(row.get('is_exempt', False), default=False)
                if is_exempt:
                    is_absent = False
                note_value = (row.get('note') or '').strip()
                marks_value = row.get('mark')

                comp_instance, _ = StudentExamComponentMark.objects.get_or_create(
                    isDeleted=False,
                    sessionID_id=instance.sessionID_id,
                    schoolID_id=instance.schoolID_id,
                    examID_id=instance.examID_id,
                    studentID_id=instance.studentID_id,
                    standardID_id=instance.standardID_id,
                    subjectID_id=instance.subjectID_id,
                    componentRuleID_id=rule.id,
                    defaults={'note': ''},
                )
                comp_instance.isAbsent = is_absent
                comp_instance.isExempt = is_exempt
                comp_instance.note = note_value

                if is_exempt:
                    comp_instance.marksObtained = None
                elif is_absent:
                    comp_instance.marksObtained = 0.0
                elif marks_value in (None, ''):
                    comp_instance.marksObtained = None
                else:
                    numeric_mark = float(marks_value)
                    max_marks = float(rule.maxMarks or 0)
                    if numeric_mark < 0 or numeric_mark > max_marks:
                        label = rule.componentTypeID.name if rule.componentTypeID else 'component'
                        return ErrorResponse(
                            f'Marks for {label} must be between 0 and {max_marks}.',
                            extra={'color': 'red'}
                        ).to_json_response()
                    comp_instance.marksObtained = numeric_mark

                pre_save_with_user.send(sender=StudentExamComponentMark, instance=comp_instance, user=request.user.pk)
                if comp_instance.marksObtained is not None and not comp_instance.isExempt:
                    total_mark += float(comp_instance.marksObtained)

            instance.mark = round(total_mark, 2)
        else:
            instance.mark = float(mark)

        pre_save_with_user.send(sender=MarkOfStudentsByExam, instance=instance, user=request.user.pk)
        return SuccessResponse('Mark added successfully.', extra={'color': 'success'}).to_json_response()
    except Exception:
        return ErrorResponse('Unable to save marks.', extra={'color': 'red'}).to_json_response()


def _teacher_assigned_subject_rows(teacher_id, session_id):
    return AssignSubjectsToTeacher.objects.select_related(
        'assignedSubjectID',
        'assignedSubjectID__standardID',
        'assignedSubjectID__subjectID',
    ).filter(
        isDeleted=False,
        teacherID_id=teacher_id,
        sessionID_id=session_id,
        assignedSubjectID__isDeleted=False,
        assignedSubjectID__standardID__isDeleted=False,
        assignedSubjectID__subjectID__isDeleted=False,
    )


def _teacher_note_context(request):
    teacher = TeacherDetail.objects.filter(
        userID_id=request.user.id,
        isDeleted=False,
    ).order_by('-datetime').first()
    if not teacher:
        return None, None, None
    session_id = request.session.get('current_session', {}).get('Id') or teacher.sessionID_id
    school_id = request.session.get('current_session', {}).get('SchoolID') or teacher.schoolID_id
    return teacher, session_id, school_id


def _assigned_map_by_ast_id(teacher_id, session_id):
    rows = _teacher_assigned_subject_rows(teacher_id=teacher_id, session_id=session_id)
    mapping = {}
    for row in rows:
        if not row.id or not row.assignedSubjectID:
            continue
        mapping[row.id] = row
    return mapping


def _serialize_subject_note(note_obj):
    standard_name = ''
    if note_obj.standardID:
        standard_name = note_obj.standardID.name or ''
        if note_obj.standardID.section:
            standard_name = f'{standard_name} - {note_obj.standardID.section}'
    return {
        'id': note_obj.id,
        'title': note_obj.title or '',
        'contentHtml': note_obj.contentHtml or '',
        'status': note_obj.status or 'draft',
        'publishedAt': note_obj.publishedAt.strftime('%d-%m-%Y %I:%M %p') if note_obj.publishedAt else '',
        'currentVersionNo': note_obj.currentVersionNo or 1,
        'subjectID': note_obj.subjectID_id,
        'subjectName': note_obj.subjectID.name if note_obj.subjectID else '',
        'assignedSubjectTeacherID': note_obj.assignedSubjectTeacherID_id,
        'standardID': note_obj.standardID_id,
        'standardName': standard_name,
        'lastUpdatedOn': note_obj.lastUpdatedOn.strftime('%d-%m-%Y %I:%M %p') if note_obj.lastUpdatedOn else '',
    }


@login_required
@check_groups('Teaching')
def get_teacher_subject_note_bootstrap_api(request):
    teacher, session_id, _ = _teacher_note_context(request)
    if not teacher or not session_id:
        return SuccessResponse('No assignments found for teacher.', data={
            'assignments': [],
            'stats': {'total': 0, 'draft': 0, 'published': 0},
        }).to_json_response()

    rows = _teacher_assigned_subject_rows(teacher_id=teacher.id, session_id=session_id)
    assignments = []
    seen = set()
    for row in rows:
        if not row.id or not row.assignedSubjectID or not row.assignedSubjectID.standardID or not row.assignedSubjectID.subjectID:
            continue
        key = (row.id, row.assignedSubjectID.standardID_id, row.assignedSubjectID.subjectID_id)
        if key in seen:
            continue
        seen.add(key)
        standard = row.assignedSubjectID.standardID
        subject = row.assignedSubjectID.subjectID
        standard_name = standard.name or ''
        if standard.section:
            standard_name = f'{standard_name} - {standard.section}'
        assignments.append({
            'assignedSubjectTeacherID': row.id,
            'standardID': standard.id,
            'standardName': standard_name,
            'subjectID': subject.id,
            'subjectName': subject.name or '',
            'subjectBranch': row.subjectBranch or '',
        })

    note_qs = SubjectNote.objects.filter(
        isDeleted=False,
        sessionID_id=session_id,
        teacherID_id=teacher.id,
    )
    stats = {
        'total': note_qs.count(),
        'draft': note_qs.filter(status='draft').count(),
        'published': note_qs.filter(status='published').count(),
    }
    return SuccessResponse('Teacher notes metadata loaded successfully.', data={
        'assignments': assignments,
        'stats': stats,
    }).to_json_response()


@login_required
@check_groups('Teaching')
def get_teacher_subject_note_list_api(request):
    teacher, session_id, _ = _teacher_note_context(request)
    if not teacher or not session_id:
        return SuccessResponse('No notes found.', data=[]).to_json_response()

    search = (request.GET.get('search') or '').strip()
    status_value = (request.GET.get('status') or '').strip().lower()
    assigned_subject_teacher_id = (request.GET.get('assignedSubjectTeacherID') or '').strip()

    queryset = SubjectNote.objects.select_related(
        'subjectID',
        'standardID',
        'assignedSubjectTeacherID',
    ).filter(
        isDeleted=False,
        sessionID_id=session_id,
        teacherID_id=teacher.id,
    )

    if status_value in {'draft', 'published'}:
        queryset = queryset.filter(status=status_value)
    if assigned_subject_teacher_id.isdigit():
        queryset = queryset.filter(assignedSubjectTeacherID_id=int(assigned_subject_teacher_id))
    if search:
        queryset = queryset.filter(
            Q(title__icontains=search)
            | Q(subjectID__name__icontains=search)
            | Q(standardID__name__icontains=search)
            | Q(standardID__section__icontains=search)
            | Q(contentHtml__icontains=search)
        )

    rows = [_serialize_subject_note(row) for row in queryset.order_by('-lastUpdatedOn')[:300]]
    return SuccessResponse('Teacher notes loaded successfully.', data=rows).to_json_response()


@login_required
@check_groups('Teaching')
def get_teacher_subject_note_detail_api(request):
    teacher, session_id, _ = _teacher_note_context(request)
    note_id = (request.GET.get('id') or '').strip()
    if not teacher or not session_id or not note_id.isdigit():
        return ErrorResponse('Invalid request.', extra={'color': 'red'}).to_json_response()

    note_obj = SubjectNote.objects.select_related(
        'subjectID',
        'standardID',
    ).filter(
        id=int(note_id),
        isDeleted=False,
        sessionID_id=session_id,
        teacherID_id=teacher.id,
    ).first()
    if not note_obj:
        return ErrorResponse('Note not found.', extra={'color': 'red'}).to_json_response()
    return SuccessResponse('Note details loaded successfully.', data=_serialize_subject_note(note_obj)).to_json_response()


@transaction.atomic
@csrf_exempt
@login_required
@check_groups('Teaching')
@validate_input(['title', 'assignedSubjectTeacherID', 'contentHtml'])
def upsert_teacher_subject_note_api(request):
    if request.method != 'POST':
        return ErrorResponse('Method not allowed.', status_code=405).to_json_response()

    teacher, session_id, school_id = _teacher_note_context(request)
    if not teacher or not session_id:
        return ErrorResponse('Teacher profile/session not found.', extra={'color': 'red'}).to_json_response()

    note_id = (request.POST.get('id') or '').strip()
    title = (request.POST.get('title') or '').strip()
    content_html = (request.POST.get('contentHtml') or '').strip()
    status_value = (request.POST.get('status') or 'draft').strip().lower()
    assigned_subject_teacher_id = (request.POST.get('assignedSubjectTeacherID') or '').strip()

    if status_value not in {'draft', 'published'}:
        status_value = 'draft'
    if len(title) < 3:
        return ErrorResponse('Title should contain at least 3 characters.', extra={'color': 'orange'}).to_json_response()
    if len(title) > 500:
        return ErrorResponse('Title cannot exceed 500 characters.', extra={'color': 'orange'}).to_json_response()
    plain_text_content = strip_tags(content_html).strip()
    if not plain_text_content:
        return ErrorResponse('Note content is required.', extra={'color': 'orange'}).to_json_response()
    if not assigned_subject_teacher_id.isdigit():
        return ErrorResponse('Assigned subject is invalid.', extra={'color': 'orange'}).to_json_response()

    assigned_map = _assigned_map_by_ast_id(teacher.id, session_id)
    selected_assignment = assigned_map.get(int(assigned_subject_teacher_id))
    if not selected_assignment:
        return ErrorResponse('Selected subject assignment is invalid.', extra={'color': 'red'}).to_json_response()

    current_ts = timezone.now()
    editor_name = (teacher.name or request.user.get_full_name().strip() or request.user.username or 'N/A')

    if note_id.isdigit():
        note_obj = SubjectNote.objects.filter(
            id=int(note_id),
            isDeleted=False,
            sessionID_id=session_id,
            teacherID_id=teacher.id,
        ).first()
        if not note_obj:
            return ErrorResponse('Note not found for update.', extra={'color': 'red'}).to_json_response()
        note_obj.currentVersionNo = (note_obj.currentVersionNo or 1) + 1
    else:
        note_obj = SubjectNote(
            currentVersionNo=1,
            teacherID_id=teacher.id,
            sessionID_id=session_id,
            schoolID_id=school_id,
        )

    note_obj.assignedSubjectTeacherID_id = selected_assignment.id
    note_obj.standardID_id = selected_assignment.assignedSubjectID.standardID_id
    note_obj.subjectID_id = selected_assignment.assignedSubjectID.subjectID_id
    note_obj.title = title
    note_obj.contentHtml = content_html
    note_obj.status = status_value
    note_obj.publishedAt = current_ts if status_value == 'published' else None
    note_obj.lastEditedBy = editor_name
    note_obj.updatedByUserID_id = request.user.id
    note_obj.save()

    SubjectNoteVersion.objects.create(
        noteID_id=note_obj.id,
        schoolID_id=note_obj.schoolID_id,
        sessionID_id=note_obj.sessionID_id,
        teacherID_id=teacher.id,
        title=note_obj.title,
        contentHtml=note_obj.contentHtml,
        status=note_obj.status,
        versionNo=note_obj.currentVersionNo,
        lastEditedBy=editor_name,
        updatedByUserID_id=request.user.id,
    )

    action_word = 'updated' if note_id.isdigit() else 'created'
    return SuccessResponse(
        f'Note {action_word} successfully.',
        data=_serialize_subject_note(note_obj),
        extra={'color': 'green'},
    ).to_json_response()


@transaction.atomic
@csrf_exempt
@login_required
@check_groups('Teaching')
@validate_input(['id'])
def toggle_teacher_subject_note_publish_api(request):
    if request.method != 'POST':
        return ErrorResponse('Method not allowed.', status_code=405).to_json_response()
    teacher, session_id, _ = _teacher_note_context(request)
    if not teacher or not session_id:
        return ErrorResponse('Teacher profile/session not found.', extra={'color': 'red'}).to_json_response()

    note_id = (request.POST.get('id') or '').strip()
    if not note_id.isdigit():
        return ErrorResponse('Note id is required.', extra={'color': 'orange'}).to_json_response()

    note_obj = SubjectNote.objects.filter(
        id=int(note_id),
        isDeleted=False,
        sessionID_id=session_id,
        teacherID_id=teacher.id,
    ).first()
    if not note_obj:
        return ErrorResponse('Note not found.', extra={'color': 'red'}).to_json_response()

    editor_name = (teacher.name or request.user.get_full_name().strip() or request.user.username or 'N/A')
    if note_obj.status == 'published':
        note_obj.status = 'draft'
        note_obj.publishedAt = None
        message = 'Note moved to draft.'
    else:
        note_obj.status = 'published'
        note_obj.publishedAt = timezone.now()
        message = 'Note published successfully.'

    note_obj.currentVersionNo = (note_obj.currentVersionNo or 1) + 1
    note_obj.lastEditedBy = editor_name
    note_obj.updatedByUserID_id = request.user.id
    note_obj.save()

    SubjectNoteVersion.objects.create(
        noteID_id=note_obj.id,
        schoolID_id=note_obj.schoolID_id,
        sessionID_id=note_obj.sessionID_id,
        teacherID_id=teacher.id,
        title=note_obj.title,
        contentHtml=note_obj.contentHtml,
        status=note_obj.status,
        versionNo=note_obj.currentVersionNo,
        lastEditedBy=editor_name,
        updatedByUserID_id=request.user.id,
    )

    return SuccessResponse(message, data=_serialize_subject_note(note_obj), extra={'color': 'green'}).to_json_response()


@method_decorator(login_required, name='dispatch')
@method_decorator(check_groups('Teaching'), name='dispatch')
class TeacherAssignedClassStudentsJson(BaseDatatableView):
    order_columns = ['name', 'roll', 'standardID__name', 'gender', 'phoneNumber', 'email']

    def get_initial_queryset(self):
        standard = self.request.GET.get("standard")
        if not standard or not standard.isdigit():
            return Student.objects.none()

        teacher, assigned_class_ids = _get_teacher_and_assigned_class_ids(self.request)
        if not teacher or int(standard) not in assigned_class_ids:
            return Student.objects.none()

        current_session_id = self.request.session.get('current_session', {}).get('Id') or teacher.sessionID_id
        return Student.objects.select_related('standardID').filter(
            isDeleted=False,
            standardID_id=int(standard),
            sessionID_id=current_session_id
        ).order_by('roll')

    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(name__icontains=search) | Q(roll__icontains=search)
                | Q(standardID__name__icontains=search)
                | Q(standardID__section__icontains=search)
                | Q(phoneNumber__icontains=search)
                | Q(email__icontains=search)
            )
        return qs

    def prepare_results(self, qs):
        data = []
        for item in qs:
            class_name = 'N/A'
            if item.standardID:
                class_name = item.standardID.name or 'N/A'
                if item.standardID.section:
                    class_name = f'{class_name} - {item.standardID.section}'

            student_name_js = (item.name or 'Student').replace("'", "\\'")
            action = (
                f'<button class="ui mini blue button" onclick="viewFeeDetail({item.pk},{item.standardID_id},\'{student_name_js}\')">'
                f'<i class="eye icon"></i>View Fee Detail</button>'
            )

            data.append([
                escape(item.name or 'N/A'),
                escape(item.roll or 'N/A'),
                escape(class_name),
                escape(item.gender or 'N/A'),
                escape(item.phoneNumber or 'N/A'),
                escape(item.email or 'N/A'),
                action,
            ])
        return data


@login_required
@check_groups('Teaching')
def get_assigned_student_list_by_class_api(request):
    standard = request.GET.get("standard")
    if not standard or not standard.isdigit():
        return JsonResponse({'success': True, 'data': []}, safe=False)

    teacher, assigned_class_ids = _get_teacher_and_assigned_class_ids(request)
    if not teacher or int(standard) not in assigned_class_ids:
        return JsonResponse({'success': True, 'data': []}, safe=False)

    current_session_id = request.session.get('current_session', {}).get('Id') or teacher.sessionID_id
    students = Student.objects.filter(
        isDeleted=False,
        sessionID_id=current_session_id,
        standardID_id=int(standard),
    ).order_by('name')

    data = []
    for s in students:
        name = s.name or 'N/A'
        if s.roll:
            name = f"{name} - Roll {s.roll}"
        data.append({'ID': s.pk, 'Name': name})

    return JsonResponse({'success': True, 'data': data}, safe=False)


@method_decorator(login_required, name='dispatch')
@method_decorator(check_groups('Teaching'), name='dispatch')
class TeacherStudentFeeDetailsByClassJson(BaseDatatableView):
    order_columns = ['photo', 'name', 'roll']

    def get_initial_queryset(self):
        standard = self.request.GET.get("standard")
        if not standard or not standard.isdigit():
            return Student.objects.none()

        teacher, assigned_class_ids = _get_teacher_and_assigned_class_ids(self.request)
        if not teacher or int(standard) not in assigned_class_ids:
            return Student.objects.none()

        current_session_id = self.request.session.get('current_session', {}).get('Id') or teacher.sessionID_id
        return Student.objects.select_related('standardID').filter(
            isDeleted=False,
            standardID_id=int(standard),
            sessionID_id=current_session_id
        ).order_by('roll')

    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(name__icontains=search) | Q(roll__icontains=search)
                | Q(standardID__name__icontains=search)
            )
        return qs

    def prepare_results(self, qs):
        current_session_id = self.request.session.get("current_session", {}).get("Id")
        json_data = []
        for item in qs:
            month_status = {month: 'Due' for month in MONTHS_LIST}
            paid_months = StudentFee.objects.filter(
                studentID_id=item.id,
                isDeleted=False,
                isPaid=True,
                sessionID_id=current_session_id
            ).values_list('month', flat=True)
            for month in paid_months:
                if month in month_status:
                    month_status[month] = 'Paid'

            image_html = _avatar_image_html(item.photo)

            json_data.append([
                image_html,
                escape(item.name or 'N/A'),
                escape(item.roll or 'N/A'),
                month_status['January'],
                month_status['February'],
                month_status['March'],
                month_status['April'],
                month_status['May'],
                month_status['June'],
                month_status['July'],
                month_status['August'],
                month_status['September'],
                month_status['October'],
                month_status['November'],
                month_status['December'],
            ])
        return json_data


@method_decorator(login_required, name='dispatch')
@method_decorator(check_groups('Teaching'), name='dispatch')
class TeacherStudentFeeDetailsByStudentJson(BaseDatatableView):
    order_columns = ['periodStartDate', 'isPaid', 'payDate', 'amount', 'note']

    def get_initial_queryset(self):
        standard = self.request.GET.get("standardByStudent")
        student = self.request.GET.get("student")
        if not standard or not student or not standard.isdigit() or not student.isdigit():
            return StudentFee.objects.none()

        teacher, assigned_class_ids = _get_teacher_and_assigned_class_ids(self.request)
        if not teacher or int(standard) not in assigned_class_ids:
            return StudentFee.objects.none()

        current_session_id = self.request.session.get('current_session', {}).get('Id') or teacher.sessionID_id
        student_exists = Student.objects.filter(
            pk=int(student),
            isDeleted=False,
            standardID_id=int(standard),
            sessionID_id=current_session_id,
        ).exists()
        if not student_exists:
            return StudentFee.objects.none()

        fee_qs = StudentFee.objects.filter(
            isDeleted=False,
            studentID_id=int(student),
            standardID_id=int(standard),
            sessionID_id=current_session_id
        )
        return _restrict_fee_queryset_to_session_months(fee_qs, current_session_id).order_by(
            F('periodStartDate').asc(nulls_last=True),
            'feeYear',
            'feeMonth',
            'id',
        )

    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(month__icontains=search) | Q(note__icontains=search)
                | Q(amount__icontains=search)
            )
        return qs

    def prepare_results(self, qs):
        json_data = []
        for item in qs:
            if item.isPaid:
                status = 'Paid'
                pay_date = item.payDate.strftime('%d-%m-%Y') if item.payDate else 'N/A'
            else:
                status = 'Due'
                pay_date = 'N/A'

            json_data.append([
                escape(_compact_fee_month(item.month, item.feeYear)),
                status,
                pay_date,
                escape(item.amount),
                escape(item.note or ''),
            ])
        return json_data


@login_required
@check_groups('Teaching')
def teacher_self_attendance_history_api(request):
    teacher = TeacherDetail.objects.filter(
        userID_id=request.user.id,
        isDeleted=False,
    ).order_by('-datetime').first()
    if not teacher:
        return JsonResponse({'success': True, 'data': {
            'present': 0, 'absent': 0, 'leave': 0, 'working': 0, 'percentage': 0, 'rows': []
        }}, safe=False)

    current_session_id = request.session.get('current_session', {}).get('Id') or teacher.sessionID_id
    if not current_session_id:
        return JsonResponse({'success': True, 'data': {
            'present': 0, 'absent': 0, 'leave': 0, 'working': 0, 'percentage': 0, 'rows': []
        }}, safe=False)

    start_raw = (request.GET.get('startDate') or '').strip()
    end_raw = (request.GET.get('endDate') or '').strip()
    try:
        start_date = datetime.strptime(start_raw, '%d/%m/%Y').date() if start_raw else datetime.now().date().replace(day=1)
        end_date = datetime.strptime(end_raw, '%d/%m/%Y').date() if end_raw else datetime.now().date()
        if end_date < start_date:
            return JsonResponse({'success': False, 'message': 'End date cannot be before start date.'}, safe=False)
    except ValueError:
        return JsonResponse({'success': False, 'message': 'Invalid date format.'}, safe=False)

    leave_qs = LeaveApplication.objects.select_related('leaveTypeID').filter(
        isDeleted=False,
        sessionID_id=current_session_id,
        applicantRole='teacher',
        teacherID_id=teacher.id,
        status='approved',
        startDate__lte=end_date,
        endDate__gte=start_date,
    )
    for leave in leave_qs:
        leave_type_name = leave.leaveTypeID.name if leave.leaveTypeID else 'Leave'
        leave_reason = f'Approved Leave: {leave_type_name}'
        day = max(start_date, leave.startDate)
        last_day = min(end_date, leave.endDate)
        while day <= last_day:
            exists = TeacherAttendance.objects.filter(
                isDeleted=False,
                sessionID_id=current_session_id,
                teacherID_id=teacher.id,
                attendanceDate__date=day,
            ).exists()
            if not exists:
                attendance_dt = datetime(day.year, day.month, day.day)
                instance = TeacherAttendance(
                    teacherID_id=teacher.id,
                    attendanceDate=attendance_dt,
                    isPresent=False,
                    isDeleted=False,
                    absentReason=leave_reason,
                )
                pre_save_with_user.send(sender=TeacherAttendance, instance=instance, user=request.user.pk)
            day += timedelta(days=1)

    attendance_rows = TeacherAttendance.objects.filter(
        isDeleted=False,
        isHoliday=False,
        sessionID_id=current_session_id,
        teacherID_id=teacher.id,
        attendanceDate__date__gte=start_date,
        attendanceDate__date__lte=end_date,
    ).values('attendanceDate', 'isPresent', 'absentReason').order_by('attendanceDate')

    day_map = {}
    for row in attendance_rows:
        attendance_dt = row.get('attendanceDate')
        if not attendance_dt:
            continue
        day_key = attendance_dt.date()
        status = _teacher_status_from_row(row.get('isPresent'), row.get('absentReason'))
        reason = row.get('absentReason') or ''
        prev = day_map.get(day_key)
        if not prev or _teacher_daily_status_priority(status) >= _teacher_daily_status_priority(prev['status']):
            day_map[day_key] = {'status': status, 'reason': reason}

    present = sum(1 for v in day_map.values() if v['status'] == 'present')
    leave = sum(1 for v in day_map.values() if v['status'] == 'leave')
    absent = sum(1 for v in day_map.values() if v['status'] == 'absent')
    working = present + absent + leave
    percentage = round((present * 100.0 / working), 2) if working else 0

    data_rows = []
    for day_key in sorted(day_map.keys()):
        status = day_map[day_key]['status']
        reason = day_map[day_key]['reason']
        data_rows.append({
            'date': day_key.strftime('%d-%m-%Y'),
            'present': 'Yes' if status == 'present' else 'No',
            'absent': 'Yes' if status == 'absent' else 'No',
            'leave': 'Yes' if status == 'leave' else 'No',
            'reason': reason or '',
            'status': status,
        })

    return JsonResponse({
        'success': True,
        'data': {
            'present': present,
            'absent': absent,
            'leave': leave,
            'working': working,
            'percentage': percentage,
            'rows': data_rows,
        }
    }, safe=False)
