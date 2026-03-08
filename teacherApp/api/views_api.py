from datetime import datetime, timedelta

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q, Sum
from django.http import JsonResponse
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.html import escape, strip_tags
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django_datatables_view.base_datatable_view import BaseDatatableView

from managementApp.models import (
    Student,
    TeacherDetail,
    AssignSubjectsToTeacher,
    Event,
    StudentFee,
    Standard,
    TeacherAttendance,
    LeaveApplication,
)
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
    class_ids = list(Standard.objects.filter(
        isDeleted=False,
        sessionID_id=current_session_id,
        classTeacher_id=teacher.id,
    ).values_list('id', flat=True))
    return teacher, class_ids


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
    order_columns = ['month', 'isPaid', 'payDate', 'amount', 'note']

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

        return StudentFee.objects.filter(
            isDeleted=False,
            studentID_id=int(student),
            standardID_id=int(standard),
            sessionID_id=current_session_id
        ).order_by('id')

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
                escape(item.month or 'N/A'),
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
