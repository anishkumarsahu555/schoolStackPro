from datetime import datetime

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q
from django.utils.decorators import method_decorator
from django.utils.html import escape
from django.views.decorators.csrf import csrf_exempt
from django_datatables_view.base_datatable_view import BaseDatatableView

from managementApp.leave_utils import add_leave_log, sync_leave_to_attendance
from managementApp.models import LeaveApplication, LeaveType
from utils.custom_decorators import check_groups
from utils.custom_response import ErrorResponse, SuccessResponse
from utils.logger import logger


def _session_payload(request):
    current = request.session.get('current_session', {})
    return current.get('Id'), current.get('SchoolID')


def _status_badge(status_value):
    status = (status_value or '').strip().lower()
    color_map = {
        'pending': 'orange',
        'approved': 'green',
        'rejected': 'red',
        'cancelled': 'grey',
    }
    color = color_map.get(status, 'blue')
    label = status.capitalize() if status else 'N/A'
    return f'<span class="ui tiny {color} label">{escape(label)}</span>'


@login_required
def get_leave_type_list_api(request):
    session_id, _ = _session_payload(request)
    role = (request.GET.get('role') or '').strip().lower()

    queryset = LeaveType.objects.filter(
        isDeleted=False,
        isActive=True,
        sessionID_id=session_id,
    ).order_by('name')

    if role == 'teacher':
        queryset = queryset.filter(applicableFor__in=['both', 'teacher'])
    elif role == 'student':
        queryset = queryset.filter(applicableFor__in=['both', 'student'])

    data = [{
        'ID': row.pk,
        'Name': row.name,
        'Code': row.code or '',
        'ApplicableFor': row.applicableFor,
        'RequiresApproval': row.requiresApproval,
    } for row in queryset]
    return SuccessResponse('Leave types loaded successfully.', data=data).to_json_response()


@method_decorator(login_required, name='dispatch')
@method_decorator(check_groups('Admin', 'Owner'), name='dispatch')
class LeaveTypeListJson(BaseDatatableView):
    order_columns = ['name', 'code', 'applicableFor', 'requiresApproval', 'isActive', 'datetime', 'id']

    def get_initial_queryset(self):
        session_id, _ = _session_payload(self.request)
        return LeaveType.objects.filter(
            isDeleted=False,
            sessionID_id=session_id,
        ).order_by('-datetime')

    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(name__icontains=search)
                | Q(code__icontains=search)
                | Q(applicableFor__icontains=search)
                | Q(lastEditedBy__icontains=search)
            )
        return qs

    def prepare_results(self, qs):
        rows = []
        for item in qs:
            action = (
                f'<button data-inverted="" data-tooltip="Edit" data-position="left center" data-variation="mini" '
                f'style="font-size:10px;" class="ui circular facebook icon button green" '
                f'onclick="editLeaveType({item.pk})"><i class="pen icon"></i></button>'
                f'<button data-inverted="" data-tooltip="Delete" data-position="left center" data-variation="mini" '
                f'style="font-size:10px;" class="ui circular youtube icon button" '
                f'onclick="deleteLeaveType({item.pk})"><i class="trash alternate icon"></i></button>'
            )
            rows.append([
                escape(item.name),
                escape(item.code or 'N/A'),
                escape(item.get_applicableFor_display()),
                f'<span class="ui tiny {"green" if item.requiresApproval else "grey"} label">{"Yes" if item.requiresApproval else "No"}</span>',
                f'<span class="ui tiny {"green" if item.isActive else "red"} label">{"Active" if item.isActive else "Inactive"}</span>',
                escape(item.datetime.strftime('%d-%m-%Y %I:%M %p') if item.datetime else 'N/A'),
                action,
            ])
        return rows


@transaction.atomic
@csrf_exempt
@login_required
@check_groups('Admin', 'Owner')
def add_leave_type_api(request):
    if request.method != 'POST':
        return ErrorResponse('Method not allowed.', status_code=405).to_json_response()
    try:
        session_id, school_id = _session_payload(request)
        name = (request.POST.get('name') or '').strip()
        code = (request.POST.get('code') or '').strip()
        applicable_for = (request.POST.get('applicableFor') or 'both').strip().lower()
        requires_approval = (request.POST.get('requiresApproval') or 'true').strip().lower() == 'true'
        is_active = (request.POST.get('isActive') or 'true').strip().lower() == 'true'

        if not name:
            return ErrorResponse('Leave type name is required.', extra={'color': 'red'}).to_json_response()
        if applicable_for not in {'both', 'teacher', 'student'}:
            return ErrorResponse('Invalid applicable role.', extra={'color': 'red'}).to_json_response()

        if LeaveType.objects.filter(
            isDeleted=False,
            sessionID_id=session_id,
            name__iexact=name,
        ).exists():
            return ErrorResponse('Leave type already exists.', extra={'color': 'orange'}).to_json_response()

        LeaveType.objects.create(
            name=name,
            code=code or None,
            applicableFor=applicable_for,
            requiresApproval=requires_approval,
            isActive=is_active,
            sessionID_id=session_id,
            schoolID_id=school_id,
            lastEditedBy=request.user.username,
        )
        return SuccessResponse('Leave type added successfully.', extra={'color': 'green'}).to_json_response()
    except Exception as exc:
        logger.error(f'Error in add_leave_type_api: {exc}')
        return ErrorResponse('Unable to add leave type.', extra={'color': 'red'}).to_json_response()


@login_required
@check_groups('Admin', 'Owner')
def get_leave_type_detail(request):
    try:
        session_id, _ = _session_payload(request)
        leave_type_id = request.GET.get('id')
        obj = LeaveType.objects.get(
            pk=int(leave_type_id),
            isDeleted=False,
            sessionID_id=session_id,
        )
        data = {
            'id': obj.pk,
            'name': obj.name,
            'code': obj.code or '',
            'applicableFor': obj.applicableFor,
            'requiresApproval': obj.requiresApproval,
            'isActive': obj.isActive,
        }
        return SuccessResponse('Leave type loaded.', data=data).to_json_response()
    except Exception:
        return ErrorResponse('Leave type not found.', extra={'color': 'red'}).to_json_response()


@transaction.atomic
@csrf_exempt
@login_required
@check_groups('Admin', 'Owner')
def update_leave_type_api(request):
    if request.method != 'POST':
        return ErrorResponse('Method not allowed.', status_code=405).to_json_response()
    try:
        session_id, _ = _session_payload(request)
        edit_id = request.POST.get('editID')
        name = (request.POST.get('name') or '').strip()
        code = (request.POST.get('code') or '').strip()
        applicable_for = (request.POST.get('applicableFor') or 'both').strip().lower()
        requires_approval = (request.POST.get('requiresApproval') or 'true').strip().lower() == 'true'
        is_active = (request.POST.get('isActive') or 'true').strip().lower() == 'true'

        obj = LeaveType.objects.get(pk=int(edit_id), isDeleted=False, sessionID_id=session_id)
        if not name:
            return ErrorResponse('Leave type name is required.', extra={'color': 'red'}).to_json_response()
        if applicable_for not in {'both', 'teacher', 'student'}:
            return ErrorResponse('Invalid applicable role.', extra={'color': 'red'}).to_json_response()

        duplicate_qs = LeaveType.objects.filter(
            isDeleted=False,
            sessionID_id=session_id,
            name__iexact=name,
        ).exclude(pk=obj.pk)
        if duplicate_qs.exists():
            return ErrorResponse('Leave type already exists.', extra={'color': 'orange'}).to_json_response()

        obj.name = name
        obj.code = code or None
        obj.applicableFor = applicable_for
        obj.requiresApproval = requires_approval
        obj.isActive = is_active
        obj.lastEditedBy = request.user.username
        obj.save()

        return SuccessResponse('Leave type updated successfully.', extra={'color': 'green'}).to_json_response()
    except Exception as exc:
        logger.error(f'Error in update_leave_type_api: {exc}')
        return ErrorResponse('Unable to update leave type.', extra={'color': 'red'}).to_json_response()


@transaction.atomic
@csrf_exempt
@login_required
@check_groups('Admin', 'Owner')
def delete_leave_type(request):
    if request.method != 'POST':
        return ErrorResponse('Method not allowed.', status_code=405).to_json_response()
    try:
        session_id, _ = _session_payload(request)
        row_id = request.POST.get('id') or request.POST.get('dataID')
        obj = LeaveType.objects.get(pk=int(row_id), isDeleted=False, sessionID_id=session_id)
        obj.isDeleted = True
        obj.isActive = False
        obj.lastEditedBy = request.user.username
        obj.save()
        return SuccessResponse('Leave type deleted successfully.', extra={'color': 'green'}).to_json_response()
    except Exception:
        return ErrorResponse('Unable to delete leave type.', extra={'color': 'red'}).to_json_response()


@method_decorator(login_required, name='dispatch')
@method_decorator(check_groups('Admin', 'Owner'), name='dispatch')
class LeaveApplicationListJson(BaseDatatableView):
    order_columns = [
        'applicantRole',
        'teacherID__name',
        'leaveTypeID__name',
        'startDate',
        'endDate',
        'totalDays',
        'status',
        'id',
        'reason',
        'datetime',
        'id',
    ]

    def get_initial_queryset(self):
        session_id, _ = _session_payload(self.request)
        queryset = LeaveApplication.objects.select_related(
            'leaveTypeID', 'teacherID', 'studentID', 'applicantUserID'
        ).filter(
            isDeleted=False,
            sessionID_id=session_id,
        ).order_by('-datetime')

        role_filter = (self.request.GET.get('role') or '').strip().lower()
        status_filter = (self.request.GET.get('status') or '').strip().lower()
        if role_filter in {'teacher', 'student'}:
            queryset = queryset.filter(applicantRole=role_filter)
        if status_filter in {'pending', 'approved', 'rejected', 'cancelled'}:
            queryset = queryset.filter(status=status_filter)
        return queryset

    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(teacherID__name__icontains=search)
                | Q(studentID__name__icontains=search)
                | Q(leaveTypeID__name__icontains=search)
                | Q(reason__icontains=search)
                | Q(status__icontains=search)
            )
        return qs

    def prepare_results(self, qs):
        rows = []
        for item in qs:
            applicant_name = 'N/A'
            if item.applicantRole == 'teacher' and item.teacherID:
                applicant_name = item.teacherID.name or 'N/A'
            elif item.applicantRole == 'student' and item.studentID:
                applicant_name = item.studentID.name or 'N/A'

            attachment = '-'
            if item.attachment:
                attachment = f'<a href="{escape(item.attachment.url)}" target="_blank" class="ui tiny basic blue button">View</a>'

            action = '<span class="ui tiny grey label">Closed</span>'
            if item.status == 'pending':
                action = (
                    f'<button class="ui mini green button" onclick="reviewLeave({item.pk},\'approved\')">Approve</button>'
                    f'<button class="ui mini red button" onclick="reviewLeave({item.pk},\'rejected\')">Reject</button>'
                )

            rows.append([
                escape(item.get_applicantRole_display()),
                escape(applicant_name),
                escape(item.leaveTypeID.name if item.leaveTypeID else 'N/A'),
                escape(item.startDate.strftime('%d-%m-%Y') if item.startDate else 'N/A'),
                escape(item.endDate.strftime('%d-%m-%Y') if item.endDate else 'N/A'),
                escape(item.totalDays or 0),
                _status_badge(item.status),
                attachment,
                escape(item.reason or ''),
                escape(item.datetime.strftime('%d-%m-%Y %I:%M %p') if item.datetime else 'N/A'),
                action,
            ])
        return rows


@transaction.atomic
@csrf_exempt
@login_required
@check_groups('Admin', 'Owner')
def review_leave_application_api(request):
    if request.method != 'POST':
        return ErrorResponse('Method not allowed.', status_code=405).to_json_response()

    try:
        session_id, school_id = _session_payload(request)
        leave_id = request.POST.get('id')
        status_value = (request.POST.get('status') or '').strip().lower()
        remark = (request.POST.get('remark') or '').strip()
        if status_value not in {'approved', 'rejected'}:
            return ErrorResponse('Invalid review action.', extra={'color': 'red'}).to_json_response()

        leave_obj = LeaveApplication.objects.select_related('leaveTypeID').get(
            pk=int(leave_id),
            isDeleted=False,
            sessionID_id=session_id,
        )
        if leave_obj.status != 'pending':
            return ErrorResponse('Only pending requests can be reviewed.', extra={'color': 'orange'}).to_json_response()

        leave_obj.status = status_value
        leave_obj.actionRemark = remark
        leave_obj.actionByUserID_id = request.user.id
        leave_obj.actionOn = datetime.now()
        leave_obj.lastEditedBy = request.user.username
        leave_obj.save()

        add_leave_log(
            leave_obj=leave_obj,
            action=status_value,
            remark=remark,
            user_id=request.user.id,
            school_id=school_id,
            session_id=session_id,
            actor_label=request.user.username,
        )

        if status_value == 'approved':
            sync_leave_to_attendance(leave_obj)

        return SuccessResponse(f'Leave {status_value} successfully.', extra={'color': 'green'}).to_json_response()
    except Exception as exc:
        logger.error(f'Error in review_leave_application_api: {exc}')
        return ErrorResponse('Unable to review leave request.', extra={'color': 'red'}).to_json_response()
