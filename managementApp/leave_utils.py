from datetime import datetime, timedelta
from decimal import Decimal

from django.db.models import Q, Sum
from managementApp.models import LeaveActionLog, LeaveApplication, StudentAttendance, TeacherAttendance

ATTENDANCE_STATUS_PRESENT = 'present'
ATTENDANCE_STATUS_ABSENT = 'absent'
ATTENDANCE_STATUS_LEAVE = 'leave'
ATTENDANCE_STATUS_HOLIDAY = 'holiday'
ATTENDANCE_STATUSES = {
    ATTENDANCE_STATUS_PRESENT,
    ATTENDANCE_STATUS_ABSENT,
    ATTENDANCE_STATUS_LEAVE,
    ATTENDANCE_STATUS_HOLIDAY,
}
LEAVE_DURATION_FULL_DAY = 'full_day'
LEAVE_DURATION_FIRST_HALF = 'first_half'
LEAVE_DURATION_SECOND_HALF = 'second_half'
LEAVE_DURATIONS = {
    LEAVE_DURATION_FULL_DAY,
    LEAVE_DURATION_FIRST_HALF,
    LEAVE_DURATION_SECOND_HALF,
}


def normalize_leave_duration(duration_type):
    duration = (duration_type or LEAVE_DURATION_FULL_DAY).strip().lower()
    return duration if duration in LEAVE_DURATIONS else LEAVE_DURATION_FULL_DAY


def is_half_day_leave(duration_type):
    return normalize_leave_duration(duration_type) in {LEAVE_DURATION_FIRST_HALF, LEAVE_DURATION_SECOND_HALF}


def leave_duration_label(duration_type):
    labels = {
        LEAVE_DURATION_FULL_DAY: 'Full Day',
        LEAVE_DURATION_FIRST_HALF: 'First Half',
        LEAVE_DURATION_SECOND_HALF: 'Second Half',
    }
    return labels.get(normalize_leave_duration(duration_type), 'Full Day')


def calculate_total_days(start_date, end_date, duration_type=LEAVE_DURATION_FULL_DAY):
    if is_half_day_leave(duration_type):
        return Decimal('0.5')
    return Decimal((end_date - start_date).days + 1)


def normalize_quota_days(value):
    if value in (None, ''):
        return None
    quota = Decimal(str(value))
    if quota <= 0:
        return None
    return quota.quantize(Decimal('0.1'))


def get_leave_balance(*, leave_type, session_id, role, teacher_id=None, student_id=None, exclude_id=None):
    quota = normalize_quota_days(leave_type.quotaDays if leave_type else None)
    if quota is None:
        return {
            'limited': False,
            'quota': None,
            'approved': Decimal('0'),
            'pending': Decimal('0'),
            'used': Decimal('0'),
            'available': None,
        }

    queryset = LeaveApplication.objects.filter(
        isDeleted=False,
        sessionID_id=session_id,
        leaveTypeID=leave_type,
        applicantRole=role,
        status__in=['pending', 'approved'],
    )
    if role == 'teacher':
        queryset = queryset.filter(teacherID_id=teacher_id)
    else:
        queryset = queryset.filter(studentID_id=student_id)
    if exclude_id:
        queryset = queryset.exclude(pk=exclude_id)

    approved = queryset.filter(status='approved').aggregate(total=Sum('totalDays')).get('total') or Decimal('0')
    pending = queryset.filter(status='pending').aggregate(total=Sum('totalDays')).get('total') or Decimal('0')
    used = approved + pending
    available = quota - used
    return {
        'limited': True,
        'quota': quota,
        'approved': approved,
        'pending': pending,
        'used': used,
        'available': available,
    }


def validate_leave_balance(*, leave_type, requested_days, session_id, role, teacher_id=None, student_id=None, exclude_id=None):
    balance = get_leave_balance(
        leave_type=leave_type,
        session_id=session_id,
        role=role,
        teacher_id=teacher_id,
        student_id=student_id,
        exclude_id=exclude_id,
    )
    if not balance['limited']:
        return balance
    if requested_days > balance['available']:
        available = max(balance['available'], Decimal('0'))
        raise ValueError(f'Insufficient leave balance. Available: {available} day(s).')
    return balance


def validate_leave_duration_dates(start_date, end_date, duration_type):
    if is_half_day_leave(duration_type) and start_date != end_date:
        raise ValueError('Half-day leave must start and end on the same date.')


def attendance_status_from_values(*, is_present=False, absent_reason='', is_holiday=False, attendance_status=None):
    status = (attendance_status or '').strip().lower()
    if status in ATTENDANCE_STATUSES:
        return status
    if is_holiday:
        return ATTENDANCE_STATUS_HOLIDAY
    if is_present:
        return ATTENDANCE_STATUS_PRESENT
    if (absent_reason or '').strip().lower().startswith('approved leave'):
        return ATTENDANCE_STATUS_LEAVE
    return ATTENDANCE_STATUS_ABSENT


def apply_attendance_status(instance, *, is_present=None, absent_reason=None, attendance_status=None):
    if is_present is not None:
        instance.isPresent = bool(is_present)
    if absent_reason is not None:
        instance.absentReason = absent_reason

    status = attendance_status_from_values(
        is_present=instance.isPresent,
        absent_reason=instance.absentReason,
        is_holiday=instance.isHoliday,
        attendance_status=attendance_status,
    )
    instance.attendanceStatus = status
    if status == ATTENDANCE_STATUS_PRESENT:
        instance.isPresent = True
        instance.absentReason = ''
    elif status in {ATTENDANCE_STATUS_ABSENT, ATTENDANCE_STATUS_LEAVE, ATTENDANCE_STATUS_HOLIDAY}:
        instance.isPresent = False
    return instance


def attendance_status_priority(status):
    ranking = {
        ATTENDANCE_STATUS_ABSENT: 1,
        ATTENDANCE_STATUS_LEAVE: 2,
        ATTENDANCE_STATUS_PRESENT: 3,
        ATTENDANCE_STATUS_HOLIDAY: 4,
    }
    return ranking.get(status, 0)


def has_overlapping_leave(*, session_id, role, start_date, end_date, teacher_id=None, student_id=None, exclude_id=None, duration_type=LEAVE_DURATION_FULL_DAY):
    duration_type = normalize_leave_duration(duration_type)
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
    if is_half_day_leave(duration_type):
        queryset = queryset.filter(
            Q(durationType__isnull=True)
            | Q(durationType=LEAVE_DURATION_FULL_DAY)
            | Q(durationType=duration_type)
        )
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
    return leave_map_for_date(
        session_id=session_id,
        role=role,
        date_value=date_value,
        ids=ids,
        statuses=['approved'],
    )


def pending_leave_map_for_date(*, session_id, role, date_value, ids):
    return leave_map_for_date(
        session_id=session_id,
        role=role,
        date_value=date_value,
        ids=ids,
        statuses=['pending'],
    )


def leave_map_for_date(*, session_id, role, date_value, ids, statuses):
    queryset = LeaveApplication.objects.select_related('leaveTypeID').filter(
        isDeleted=False,
        sessionID_id=session_id,
        applicantRole=role,
        status__in=statuses,
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


def _leave_sync_note(leave_obj):
    return leave_application_note(leave_obj)


def leave_application_note(leave_obj):
    leave_type_name = leave_obj.leaveTypeID.name if leave_obj.leaveTypeID else 'Leave'
    duration_label = leave_duration_label(leave_obj.durationType)
    if normalize_leave_duration(leave_obj.durationType) == LEAVE_DURATION_FULL_DAY:
        return f'Approved Leave: {leave_type_name}'
    return f'Approved Leave: {leave_type_name} ({duration_label})'


def _sync_attendance_row_to_leave(attendance_obj, *, leave_obj, note, created_by_sync=False):
    if attendance_obj.sourceLeaveApplication_id == leave_obj.id:
        attendance_obj.absentReason = note
        attendance_obj.attendanceStatus = ATTENDANCE_STATUS_LEAVE
        attendance_obj.leaveDurationType = normalize_leave_duration(leave_obj.durationType)
        attendance_obj.isPresent = False
        attendance_obj.isDeleted = False
        attendance_obj.save(update_fields=['absentReason', 'attendanceStatus', 'leaveDurationType', 'isPresent', 'isDeleted', 'lastUpdatedOn'])
        return attendance_obj

    attendance_obj.sourceLeaveApplication = leave_obj
    attendance_obj.leaveSyncCreatedAttendance = created_by_sync
    attendance_obj.leaveSyncPreviousIsPresent = None if created_by_sync else attendance_obj.isPresent
    attendance_obj.leaveSyncPreviousAbsentReason = None if created_by_sync else (attendance_obj.absentReason or '')
    attendance_obj.leaveSyncPreviousLeaveDurationType = None if created_by_sync else attendance_obj.leaveDurationType
    attendance_obj.leaveSyncPreviousAttendanceStatus = None if created_by_sync else attendance_status_from_values(
        is_present=attendance_obj.isPresent,
        absent_reason=attendance_obj.absentReason,
        is_holiday=attendance_obj.isHoliday,
        attendance_status=attendance_obj.attendanceStatus,
    )
    attendance_obj.isPresent = False
    attendance_obj.attendanceStatus = ATTENDANCE_STATUS_LEAVE
    attendance_obj.leaveDurationType = normalize_leave_duration(leave_obj.durationType)
    attendance_obj.absentReason = note
    attendance_obj.isDeleted = False
    attendance_obj.save()
    return attendance_obj


def _date_range(start, end):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def sync_leave_to_attendance(leave_obj):
    note = _leave_sync_note(leave_obj)
    start = leave_obj.startDate
    end = leave_obj.endDate

    if leave_obj.applicantRole == 'student' and leave_obj.studentID_id:
        student = leave_obj.studentID
        for day in _date_range(start, end):
            attendance_obj = StudentAttendance.objects.filter(
                isDeleted=False,
                sessionID_id=leave_obj.sessionID_id,
                studentID_id=leave_obj.studentID_id,
                attendanceDate__date=day,
                isHoliday=False,
                bySubject=False,
            ).order_by('id').first()
            created_by_sync = False
            if attendance_obj is None:
                attendance_obj = StudentAttendance(
                    studentID_id=leave_obj.studentID_id,
                    standardID_id=student.standardID_id if student else None,
                    sessionID_id=leave_obj.sessionID_id,
                    schoolID_id=leave_obj.schoolID_id,
                    attendanceDate=datetime(day.year, day.month, day.day),
                    bySubject=False,
                    isHoliday=False,
                    isDeleted=False,
                )
                created_by_sync = True
            _sync_attendance_row_to_leave(
                attendance_obj,
                leave_obj=leave_obj,
                note=note,
                created_by_sync=created_by_sync,
            )

    if leave_obj.applicantRole == 'teacher' and leave_obj.teacherID_id:
        for day in _date_range(start, end):
            attendance_obj = TeacherAttendance.objects.filter(
                isDeleted=False,
                sessionID_id=leave_obj.sessionID_id,
                teacherID_id=leave_obj.teacherID_id,
                attendanceDate__date=day,
                isHoliday=False,
            ).order_by('id').first()
            created_by_sync = False
            if attendance_obj is None:
                attendance_obj = TeacherAttendance(
                    teacherID_id=leave_obj.teacherID_id,
                    sessionID_id=leave_obj.sessionID_id,
                    schoolID_id=leave_obj.schoolID_id,
                    attendanceDate=datetime(day.year, day.month, day.day),
                    isHoliday=False,
                    isDeleted=False,
                )
                created_by_sync = True
            _sync_attendance_row_to_leave(
                attendance_obj,
                leave_obj=leave_obj,
                note=note,
                created_by_sync=created_by_sync,
            )


def revert_leave_attendance_sync(leave_obj):
    targets = []
    if leave_obj.applicantRole == 'student':
        targets.append(StudentAttendance)
    elif leave_obj.applicantRole == 'teacher':
        targets.append(TeacherAttendance)

    for model in targets:
        for attendance_obj in model.objects.filter(sourceLeaveApplication_id=leave_obj.id):
            if attendance_obj.leaveSyncCreatedAttendance:
                attendance_obj.isDeleted = True
                attendance_obj.sourceLeaveApplication = None
                attendance_obj.leaveDurationType = None
                attendance_obj.save(update_fields=['isDeleted', 'sourceLeaveApplication', 'leaveDurationType', 'lastUpdatedOn'])
                continue

            previous_status = attendance_obj.leaveSyncPreviousAttendanceStatus
            previous_is_present = attendance_obj.leaveSyncPreviousIsPresent
            previous_reason = attendance_obj.leaveSyncPreviousAbsentReason
            previous_leave_duration = attendance_obj.leaveSyncPreviousLeaveDurationType

            attendance_obj.isPresent = bool(previous_is_present)
            attendance_obj.absentReason = previous_reason or ''
            attendance_obj.leaveDurationType = previous_leave_duration
            attendance_obj.attendanceStatus = attendance_status_from_values(
                is_present=attendance_obj.isPresent,
                absent_reason=attendance_obj.absentReason,
                is_holiday=attendance_obj.isHoliday,
                attendance_status=previous_status,
            )
            attendance_obj.sourceLeaveApplication = None
            attendance_obj.leaveSyncCreatedAttendance = False
            attendance_obj.leaveSyncPreviousIsPresent = None
            attendance_obj.leaveSyncPreviousAbsentReason = None
            attendance_obj.leaveSyncPreviousAttendanceStatus = None
            attendance_obj.leaveSyncPreviousLeaveDurationType = None
            attendance_obj.save()


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
