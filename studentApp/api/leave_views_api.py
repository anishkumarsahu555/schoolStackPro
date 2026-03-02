from datetime import datetime

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q
from django.utils.decorators import method_decorator
from django.utils.html import escape
from django.views.decorators.csrf import csrf_exempt
from django_datatables_view.base_datatable_view import BaseDatatableView

from managementApp.leave_utils import (
    add_leave_log,
    calculate_total_days,
    has_overlapping_leave,
    sync_leave_to_attendance,
)
from managementApp.models import LeaveApplication, LeaveType, Student
from utils.custom_decorators import check_groups
from utils.custom_response import ErrorResponse, SuccessResponse
from utils.logger import logger


def _parse_date(date_str):
    if not date_str:
        raise ValueError('Date is required.')
    for fmt in ('%d/%m/%Y', '%Y-%m-%d'):
        try:
            return datetime.strptime(date_str, fmt).date()
        except ValueError:
            continue
    raise ValueError('Invalid date format.')


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


def _student_context(request):
    student = Student.objects.filter(
        userID_id=request.user.id,
        isDeleted=False,
    ).order_by('-datetime').first()
    current = request.session.get('current_session', {})
    session_id = current.get('Id') or (student.sessionID_id if student else None)
    school_id = current.get('SchoolID') or (student.schoolID_id if student else None)
    return student, session_id, school_id


@login_required
@check_groups('Student')
def get_student_leave_type_list_api(request):
    student, session_id, _ = _student_context(request)
    if not student or not session_id:
        return SuccessResponse('No leave types available for current session.', data=[]).to_json_response()

    queryset = LeaveType.objects.filter(
        isDeleted=False,
        isActive=True,
        sessionID_id=session_id,
        applicableFor__in=['both', 'student'],
    ).order_by('name')

    data = [{
        'ID': row.pk,
        'Name': row.name,
        'Code': row.code or '',
    } for row in queryset]
    return SuccessResponse('Leave types loaded successfully.', data=data).to_json_response()


@method_decorator(login_required, name='dispatch')
@method_decorator(check_groups('Student'), name='dispatch')
class StudentLeaveApplicationListJson(BaseDatatableView):
    order_columns = [
        'leaveTypeID__name',
        'startDate',
        'endDate',
        'totalDays',
        'status',
        'id',
        'reason',
        'actionRemark',
        'datetime',
        'id',
    ]

    def get_initial_queryset(self):
        student, session_id, _ = _student_context(self.request)
        if not student or not session_id:
            return LeaveApplication.objects.none()
        return LeaveApplication.objects.select_related('leaveTypeID').filter(
            isDeleted=False,
            sessionID_id=session_id,
            applicantRole='student',
            studentID_id=student.id,
        ).order_by('-datetime')

    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(leaveTypeID__name__icontains=search)
                | Q(reason__icontains=search)
                | Q(status__icontains=search)
            )
        return qs

    def prepare_results(self, qs):
        rows = []
        for item in qs:
            attachment = '-'
            if item.attachment:
                attachment = f'<a href="{escape(item.attachment.url)}" target="_blank" class="ui tiny basic blue button">View</a>'

            action = '<span class="ui tiny grey label">Closed</span>'
            if item.status == 'pending':
                action = (
                    f'<button class="ui mini green icon button" onclick="editLeave({item.pk})"><i class="edit icon"></i></button>'
                    f'<button class="ui mini red icon button" onclick="cancelLeave({item.pk})"><i class="ban icon"></i></button>'
                )

            rows.append([
                escape(item.leaveTypeID.name if item.leaveTypeID else 'N/A'),
                escape(item.startDate.strftime('%d-%m-%Y') if item.startDate else 'N/A'),
                escape(item.endDate.strftime('%d-%m-%Y') if item.endDate else 'N/A'),
                escape(item.totalDays or 0),
                _status_badge(item.status),
                attachment,
                escape(item.reason or ''),
                escape(item.actionRemark or ''),
                escape(item.datetime.strftime('%d-%m-%Y %I:%M %p') if item.datetime else 'N/A'),
                action,
            ])
        return rows


@login_required
@check_groups('Student')
def get_student_leave_detail_api(request):
    student, session_id, _ = _student_context(request)
    if not student or not session_id:
        return ErrorResponse('Student profile not found.', extra={'color': 'red'}).to_json_response()
    try:
        row_id = int(request.GET.get('id'))
        leave = LeaveApplication.objects.get(
            pk=row_id,
            isDeleted=False,
            sessionID_id=session_id,
            studentID_id=student.id,
            applicantRole='student',
        )
        return SuccessResponse('Leave details loaded.', data={
            'id': leave.id,
            'leaveTypeID': leave.leaveTypeID_id,
            'startDate': leave.startDate.strftime('%d/%m/%Y') if leave.startDate else '',
            'endDate': leave.endDate.strftime('%d/%m/%Y') if leave.endDate else '',
            'reason': leave.reason or '',
        }).to_json_response()
    except Exception:
        return ErrorResponse('Leave details not found.', extra={'color': 'red'}).to_json_response()


@transaction.atomic
@csrf_exempt
@login_required
@check_groups('Student')
def student_apply_leave_api(request):
    if request.method != 'POST':
        return ErrorResponse('Method not allowed.', status_code=405).to_json_response()
    student, session_id, school_id = _student_context(request)
    if not student or not session_id:
        return ErrorResponse('Student profile/session not found.', extra={'color': 'red'}).to_json_response()
    try:
        leave_type_id = request.POST.get('leaveTypeID')
        start_date = _parse_date((request.POST.get('startDate') or '').strip())
        end_date = _parse_date((request.POST.get('endDate') or '').strip())
        reason = (request.POST.get('reason') or '').strip()
        attachment = request.FILES.get('attachment')

        if end_date < start_date:
            return ErrorResponse('End date cannot be before start date.', extra={'color': 'red'}).to_json_response()
        if not leave_type_id:
            return ErrorResponse('Leave type is required.', extra={'color': 'red'}).to_json_response()

        leave_type = LeaveType.objects.filter(
            pk=int(leave_type_id),
            isDeleted=False,
            isActive=True,
            sessionID_id=session_id,
            applicableFor__in=['both', 'student'],
        ).first()
        if not leave_type:
            return ErrorResponse('Invalid leave type selected.', extra={'color': 'red'}).to_json_response()

        if has_overlapping_leave(
            session_id=session_id,
            role='student',
            start_date=start_date,
            end_date=end_date,
            student_id=student.id,
        ):
            return ErrorResponse('Overlapping leave request already exists.', extra={'color': 'orange'}).to_json_response()

        status_value = 'pending' if leave_type.requiresApproval else 'approved'
        leave = LeaveApplication.objects.create(
            leaveTypeID=leave_type,
            applicantUserID_id=request.user.id,
            studentID_id=student.id,
            applicantRole='student',
            startDate=start_date,
            endDate=end_date,
            totalDays=calculate_total_days(start_date, end_date),
            reason=reason,
            attachment=attachment,
            status=status_value,
            schoolID_id=school_id,
            sessionID_id=session_id,
            lastEditedBy=student.name or request.user.username,
        )

        add_leave_log(
            leave_obj=leave,
            action='created',
            remark=reason,
            user_id=request.user.id,
            school_id=school_id,
            session_id=session_id,
            actor_label=student.name or request.user.username,
        )

        if status_value == 'approved':
            sync_leave_to_attendance(leave)

        return SuccessResponse('Leave application submitted successfully.', extra={'color': 'green'}).to_json_response()
    except ValueError as exc:
        return ErrorResponse(str(exc), extra={'color': 'red'}).to_json_response()
    except Exception as exc:
        logger.error(f'Error in student_apply_leave_api: {exc}')
        return ErrorResponse('Unable to submit leave application.', extra={'color': 'red'}).to_json_response()


@transaction.atomic
@csrf_exempt
@login_required
@check_groups('Student')
def student_update_leave_api(request):
    if request.method != 'POST':
        return ErrorResponse('Method not allowed.', status_code=405).to_json_response()
    student, session_id, school_id = _student_context(request)
    if not student or not session_id:
        return ErrorResponse('Student profile/session not found.', extra={'color': 'red'}).to_json_response()
    try:
        edit_id = int(request.POST.get('editID'))
        leave_type_id = request.POST.get('leaveTypeID')
        start_date = _parse_date((request.POST.get('startDate') or '').strip())
        end_date = _parse_date((request.POST.get('endDate') or '').strip())
        reason = (request.POST.get('reason') or '').strip()
        attachment = request.FILES.get('attachment')

        leave = LeaveApplication.objects.get(
            pk=edit_id,
            isDeleted=False,
            sessionID_id=session_id,
            studentID_id=student.id,
            applicantRole='student',
        )
        if leave.status != 'pending':
            return ErrorResponse('Only pending leave can be updated.', extra={'color': 'orange'}).to_json_response()

        leave_type = LeaveType.objects.filter(
            pk=int(leave_type_id),
            isDeleted=False,
            isActive=True,
            sessionID_id=session_id,
            applicableFor__in=['both', 'student'],
        ).first()
        if not leave_type:
            return ErrorResponse('Invalid leave type selected.', extra={'color': 'red'}).to_json_response()
        if end_date < start_date:
            return ErrorResponse('End date cannot be before start date.', extra={'color': 'red'}).to_json_response()

        if has_overlapping_leave(
            session_id=session_id,
            role='student',
            start_date=start_date,
            end_date=end_date,
            student_id=student.id,
            exclude_id=leave.id,
        ):
            return ErrorResponse('Overlapping leave request already exists.', extra={'color': 'orange'}).to_json_response()

        leave.leaveTypeID = leave_type
        leave.startDate = start_date
        leave.endDate = end_date
        leave.totalDays = calculate_total_days(start_date, end_date)
        leave.reason = reason
        if attachment:
            leave.attachment = attachment
        leave.schoolID_id = school_id
        leave.lastEditedBy = student.name or request.user.username
        leave.save()

        add_leave_log(
            leave_obj=leave,
            action='updated',
            remark=reason,
            user_id=request.user.id,
            school_id=school_id,
            session_id=session_id,
            actor_label=student.name or request.user.username,
        )
        return SuccessResponse('Leave application updated successfully.', extra={'color': 'green'}).to_json_response()
    except ValueError as exc:
        return ErrorResponse(str(exc), extra={'color': 'red'}).to_json_response()
    except Exception as exc:
        logger.error(f'Error in student_update_leave_api: {exc}')
        return ErrorResponse('Unable to update leave application.', extra={'color': 'red'}).to_json_response()


@transaction.atomic
@csrf_exempt
@login_required
@check_groups('Student')
def student_cancel_leave_api(request):
    if request.method != 'POST':
        return ErrorResponse('Method not allowed.', status_code=405).to_json_response()
    student, session_id, school_id = _student_context(request)
    if not student or not session_id:
        return ErrorResponse('Student profile/session not found.', extra={'color': 'red'}).to_json_response()
    try:
        row_id = int(request.POST.get('id') or request.POST.get('dataID'))
        leave = LeaveApplication.objects.get(
            pk=row_id,
            isDeleted=False,
            sessionID_id=session_id,
            studentID_id=student.id,
            applicantRole='student',
        )
        if leave.status != 'pending':
            return ErrorResponse('Only pending leave can be cancelled.', extra={'color': 'orange'}).to_json_response()
        leave.status = 'cancelled'
        leave.actionOn = datetime.now()
        leave.actionByUserID_id = request.user.id
        leave.actionRemark = 'Cancelled by applicant'
        leave.lastEditedBy = student.name or request.user.username
        leave.save()

        add_leave_log(
            leave_obj=leave,
            action='cancelled',
            remark='Cancelled by applicant',
            user_id=request.user.id,
            school_id=school_id,
            session_id=session_id,
            actor_label=student.name or request.user.username,
        )
        return SuccessResponse('Leave cancelled successfully.', extra={'color': 'green'}).to_json_response()
    except Exception as exc:
        logger.error(f'Error in student_cancel_leave_api: {exc}')
        return ErrorResponse('Unable to cancel leave.', extra={'color': 'red'}).to_json_response()
