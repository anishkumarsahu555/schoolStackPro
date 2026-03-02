from django.utils import timezone

from managementApp.models import LeaveActionLog, LeaveApplication, StudentAttendance, TeacherAttendance


def calculate_total_days(start_date, end_date):
    return (end_date - start_date).days + 1


def has_overlapping_leave(*, session_id, role, start_date, end_date, teacher_id=None, student_id=None, exclude_id=None):
    queryset = LeaveApplication.objects.filter(
        isDeleted=False,
        sessionID_id=session_id,
        applicantRole=role,
        status__in=['pending', 'approved'],
        startDate__lte=end_date,
        endDate__gte=start_date,
    )
    if role == 'teacher':
        queryset = queryset.filter(teacherID_id=teacher_id)
    else:
        queryset = queryset.filter(studentID_id=student_id)
    if exclude_id:
        queryset = queryset.exclude(pk=exclude_id)
    return queryset.exists()


def approved_leave_for_date(*, session_id, role, date_value, teacher_id=None, student_id=None):
    if not date_value:
        return None
    queryset = LeaveApplication.objects.select_related('leaveTypeID').filter(
        isDeleted=False,
        sessionID_id=session_id,
        applicantRole=role,
        status='approved',
        startDate__lte=date_value,
        endDate__gte=date_value,
    )
    if role == 'teacher':
        queryset = queryset.filter(teacherID_id=teacher_id)
    else:
        queryset = queryset.filter(studentID_id=student_id)
    return queryset.first()


def approved_leave_map_for_date(*, session_id, role, date_value, ids):
    queryset = LeaveApplication.objects.select_related('leaveTypeID').filter(
        isDeleted=False,
        sessionID_id=session_id,
        applicantRole=role,
        status='approved',
        startDate__lte=date_value,
        endDate__gte=date_value,
    )
    if role == 'teacher':
        queryset = queryset.filter(teacherID_id__in=ids)
        key = 'teacherID_id'
    else:
        queryset = queryset.filter(studentID_id__in=ids)
        key = 'studentID_id'

    data = {}
    for row in queryset:
        mapped_id = getattr(row, key)
        if mapped_id and mapped_id not in data:
            data[mapped_id] = row
    return data


def sync_leave_to_attendance(leave_obj):
    leave_type_name = leave_obj.leaveTypeID.name if leave_obj.leaveTypeID else 'Leave'
    note = f'Approved Leave: {leave_type_name}'
    start = leave_obj.startDate
    end = leave_obj.endDate

    if leave_obj.applicantRole == 'student' and leave_obj.studentID_id:
        StudentAttendance.objects.filter(
            isDeleted=False,
            sessionID_id=leave_obj.sessionID_id,
            studentID_id=leave_obj.studentID_id,
            attendanceDate__date__gte=start,
            attendanceDate__date__lte=end,
            isHoliday=False,
        ).update(isPresent=False, absentReason=note, lastUpdatedOn=timezone.now())

    if leave_obj.applicantRole == 'teacher' and leave_obj.teacherID_id:
        TeacherAttendance.objects.filter(
            isDeleted=False,
            sessionID_id=leave_obj.sessionID_id,
            teacherID_id=leave_obj.teacherID_id,
            attendanceDate__date__gte=start,
            attendanceDate__date__lte=end,
            isHoliday=False,
        ).update(isPresent=False, absentReason=note, lastUpdatedOn=timezone.now())


def add_leave_log(*, leave_obj, action, remark, user_id, school_id, session_id, actor_label='System'):
    return LeaveActionLog.objects.create(
        leaveID=leave_obj,
        action=action,
        remark=remark or '',
        actionByUserID_id=user_id,
        schoolID_id=school_id,
        sessionID_id=session_id,
        lastEditedBy=actor_label,
        updatedByUserID_id=user_id,
    )
