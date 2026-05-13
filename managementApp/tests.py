from datetime import date

from django.test import TestCase

from homeApp.models import SchoolDetail, SchoolSession
from managementApp.holiday_utils import holiday_audiences, resync_holidays_for_scope
from managementApp.models import (
    SchoolHoliday,
    Standard,
    Student,
    StudentAttendance,
    TeacherAttendance,
    TeacherDetail,
)


class HolidayAttendanceSyncTests(TestCase):
    def setUp(self):
        self.school = SchoolDetail.objects.create(schoolName='Demo School', address='Demo Address')
        self.session = SchoolSession.objects.create(
            schoolID=self.school,
            sessionYear='2026-2027',
            startDate=date(2026, 4, 1),
            endDate=date(2027, 3, 31),
            isCurrent=True,
        )
        self.teacher = TeacherDetail.objects.create(
            schoolID=self.school,
            sessionID=self.session,
            name='Teacher One',
        )
        self.standard = Standard.objects.create(
            schoolID=self.school,
            sessionID=self.session,
            name='Class 1',
            classTeacher=self.teacher,
        )
        self.student = Student.objects.create(
            schoolID=self.school,
            sessionID=self.session,
            standardID=self.standard,
            name='Student One',
        )

    def _resync(self, holiday):
        resync_holidays_for_scope(
            session_id=self.session.id,
            school_id=self.school.id,
            start_date=holiday.startDate,
            end_date=holiday.endDate,
            audiences=holiday_audiences(holiday.appliesTo),
        )

    def test_deleting_overlapping_general_holiday_keeps_student_only_holiday(self):
        general = SchoolHoliday.objects.create(
            schoolID=self.school,
            sessionID=self.session,
            title='General Holiday',
            holidayType='general',
            appliesTo='both',
            startDate=date(2026, 5, 20),
            endDate=date(2026, 5, 20),
        )
        students_only = SchoolHoliday.objects.create(
            schoolID=self.school,
            sessionID=self.session,
            title='Student Holiday',
            holidayType='general',
            appliesTo='students',
            startDate=date(2026, 5, 20),
            endDate=date(2026, 5, 20),
        )
        self._resync(general)
        self._resync(students_only)

        general.isDeleted = True
        general.save()
        resync_holidays_for_scope(
            session_id=self.session.id,
            school_id=self.school.id,
            start_date=general.startDate,
            end_date=general.endDate,
            audiences=holiday_audiences(general.appliesTo),
        )

        student_row = StudentAttendance.objects.get(
            isDeleted=False,
            sessionID=self.session,
            studentID=self.student,
            attendanceDate__date=students_only.startDate,
        )
        self.assertTrue(student_row.isHoliday)
        self.assertEqual(student_row.sourceHoliday_id, students_only.id)
        self.assertFalse(TeacherAttendance.objects.filter(isDeleted=False, sourceHoliday__isnull=False).exists())

    def test_changing_holiday_from_both_to_students_removes_teacher_holiday(self):
        holiday = SchoolHoliday.objects.create(
            schoolID=self.school,
            sessionID=self.session,
            title='Audience Change',
            holidayType='general',
            appliesTo='both',
            startDate=date(2026, 6, 10),
            endDate=date(2026, 6, 10),
        )
        self._resync(holiday)

        old_applies_to = holiday.appliesTo
        holiday.appliesTo = 'students'
        holiday.save()
        resync_holidays_for_scope(
            session_id=self.session.id,
            school_id=self.school.id,
            start_date=holiday.startDate,
            end_date=holiday.endDate,
            audiences=holiday_audiences(old_applies_to, holiday.appliesTo),
        )

        self.assertTrue(StudentAttendance.objects.filter(isDeleted=False, sourceHoliday=holiday).exists())
        self.assertFalse(TeacherAttendance.objects.filter(isDeleted=False, sourceHoliday=holiday).exists())
