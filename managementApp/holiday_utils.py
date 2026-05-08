from datetime import datetime, timedelta

from managementApp.models import SchoolHoliday, Student, StudentAttendance, TeacherAttendance, TeacherDetail
from managementApp.signals import pre_save_with_user
from managementApp.leave_utils import ATTENDANCE_STATUS_HOLIDAY, attendance_status_from_values


def date_range(start, end):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def holiday_note(holiday_obj):
    title = holiday_obj.title or 'School Holiday'
    holiday_type = holiday_obj.get_holidayType_display() if holiday_obj.holidayType else 'Holiday'
    return f'{holiday_type}: {title}'


def _holiday_applies_to_students(holiday_obj):
    return holiday_obj.appliesTo in ('both', 'students')


def _holiday_applies_to_teachers(holiday_obj):
    return holiday_obj.appliesTo in ('both', 'teachers')


def _sync_attendance_row_to_holiday(attendance_obj, *, holiday_obj, note, created_by_sync, user_id=None):
    if attendance_obj.sourceHoliday_id != holiday_obj.id:
        attendance_obj.holidaySyncPreviousIsPresent = attendance_obj.isPresent
        attendance_obj.holidaySyncPreviousIsHoliday = attendance_obj.isHoliday
        attendance_obj.holidaySyncPreviousAbsentReason = attendance_obj.absentReason
        attendance_obj.holidaySyncPreviousAttendanceStatus = attendance_obj.attendanceStatus
        attendance_obj.holidaySyncPreviousLeaveDurationType = attendance_obj.leaveDurationType

    attendance_obj.isPresent = False
    attendance_obj.isHoliday = True
    attendance_obj.absentReason = note
    attendance_obj.attendanceStatus = ATTENDANCE_STATUS_HOLIDAY
    attendance_obj.leaveDurationType = None
    attendance_obj.sourceHoliday = holiday_obj
    attendance_obj.holidaySyncCreatedAttendance = created_by_sync
    pre_save_with_user.send(sender=attendance_obj.__class__, instance=attendance_obj, user=user_id)


def sync_holiday_to_attendance(holiday_obj, *, user_id=None):
    note = holiday_note(holiday_obj)

    if _holiday_applies_to_students(holiday_obj):
        students = Student.objects.filter(
            isDeleted=False,
            sessionID_id=holiday_obj.sessionID_id,
        ).select_related('standardID')
        for day in date_range(holiday_obj.startDate, holiday_obj.endDate):
            for student in students:
                attendance_obj = StudentAttendance.objects.filter(
                    isDeleted=False,
                    sessionID_id=holiday_obj.sessionID_id,
                    studentID_id=student.id,
                    attendanceDate__date=day,
                    bySubject=False,
                ).order_by('id').first()
                created_by_sync = False
                if attendance_obj is None:
                    attendance_obj = StudentAttendance(
                        studentID_id=student.id,
                        standardID_id=student.standardID_id,
                        sessionID_id=holiday_obj.sessionID_id,
                        schoolID_id=holiday_obj.schoolID_id,
                        attendanceDate=datetime(day.year, day.month, day.day),
                        bySubject=False,
                        isDeleted=False,
                    )
                    created_by_sync = True
                _sync_attendance_row_to_holiday(
                    attendance_obj,
                    holiday_obj=holiday_obj,
                    note=note,
                    created_by_sync=created_by_sync,
                    user_id=user_id,
                )

    if _holiday_applies_to_teachers(holiday_obj):
        teachers = TeacherDetail.objects.filter(
            isDeleted=False,
            sessionID_id=holiday_obj.sessionID_id,
        )
        for day in date_range(holiday_obj.startDate, holiday_obj.endDate):
            for teacher in teachers:
                attendance_obj = TeacherAttendance.objects.filter(
                    isDeleted=False,
                    sessionID_id=holiday_obj.sessionID_id,
                    teacherID_id=teacher.id,
                    attendanceDate__date=day,
                ).order_by('id').first()
                created_by_sync = False
                if attendance_obj is None:
                    attendance_obj = TeacherAttendance(
                        teacherID_id=teacher.id,
                        sessionID_id=holiday_obj.sessionID_id,
                        schoolID_id=holiday_obj.schoolID_id,
                        attendanceDate=datetime(day.year, day.month, day.day),
                        isDeleted=False,
                    )
                    created_by_sync = True
                _sync_attendance_row_to_holiday(
                    attendance_obj,
                    holiday_obj=holiday_obj,
                    note=note,
                    created_by_sync=created_by_sync,
                    user_id=user_id,
                )


def revert_holiday_attendance_sync(holiday_obj, *, user_id=None):
    for model in (StudentAttendance, TeacherAttendance):
        for attendance_obj in model.objects.filter(sourceHoliday_id=holiday_obj.id):
            if attendance_obj.holidaySyncCreatedAttendance:
                attendance_obj.isDeleted = True
                attendance_obj.sourceHoliday = None
                attendance_obj.save(update_fields=['isDeleted', 'sourceHoliday', 'lastUpdatedOn'])
                continue

            previous_status = attendance_obj.holidaySyncPreviousAttendanceStatus
            attendance_obj.isPresent = bool(attendance_obj.holidaySyncPreviousIsPresent)
            attendance_obj.isHoliday = bool(attendance_obj.holidaySyncPreviousIsHoliday)
            attendance_obj.absentReason = attendance_obj.holidaySyncPreviousAbsentReason or ''
            attendance_obj.leaveDurationType = attendance_obj.holidaySyncPreviousLeaveDurationType
            attendance_obj.attendanceStatus = attendance_status_from_values(
                is_present=attendance_obj.isPresent,
                absent_reason=attendance_obj.absentReason,
                is_holiday=attendance_obj.isHoliday,
                attendance_status=previous_status,
            )
            attendance_obj.sourceHoliday = None
            attendance_obj.holidaySyncCreatedAttendance = False
            attendance_obj.holidaySyncPreviousIsPresent = None
            attendance_obj.holidaySyncPreviousIsHoliday = None
            attendance_obj.holidaySyncPreviousAbsentReason = None
            attendance_obj.holidaySyncPreviousAttendanceStatus = None
            attendance_obj.holidaySyncPreviousLeaveDurationType = None
            pre_save_with_user.send(sender=model, instance=attendance_obj, user=user_id)


def resync_holiday_to_attendance(holiday_obj, *, user_id=None):
    revert_holiday_attendance_sync(holiday_obj, user_id=user_id)
    sync_holiday_to_attendance(holiday_obj, user_id=user_id)


def holiday_for_date(*, session_id, target_date, applies_to):
    valid_applies = ['both', applies_to]
    return SchoolHoliday.objects.filter(
        isDeleted=False,
        sessionID_id=session_id,
        appliesTo__in=valid_applies,
        startDate__lte=target_date,
        endDate__gte=target_date,
    ).order_by('startDate', 'id').first()
