from django.contrib.auth.models import User
from django.db import models
from stdimage import StdImageField

from homeApp.models import SchoolDetail, SchoolSession
from utils.utils import UPLOAD_TO_PATTERNS


# Create your models here.


class TeacherDetail(models.Model):
    sessionID = models.ForeignKey(SchoolSession, blank=True, null=True, on_delete=models.CASCADE)
    schoolID = models.ForeignKey(SchoolDetail, blank=True, null=True, on_delete=models.CASCADE)
    name = models.CharField(max_length=500, blank=True, null=True)
    dob = models.DateField(blank=True, null=True)
    aadhar = models.CharField(max_length=500, blank=True, null=True)
    gender = models.CharField(max_length=500, blank=True, null=True)
    bloodGroup = models.CharField(max_length=500, blank=True, null=True)
    presentAddress = models.TextField(blank=True, null=True)
    presentPinCode = models.CharField(max_length=500, blank=True, null=True)
    presentCity = models.CharField(max_length=500, blank=True, null=True)
    presentState = models.CharField(max_length=500, blank=True, null=True)
    presentCountry = models.CharField(max_length=500, blank=True, null=True)
    permanentAddress = models.TextField(blank=True, null=True)
    permanentPinCode = models.CharField(max_length=500, blank=True, null=True)
    permanentCity = models.CharField(max_length=500, blank=True, null=True)
    permanentState = models.CharField(max_length=500, blank=True, null=True)
    permanentCountry = models.CharField(max_length=500, blank=True, null=True)
    phoneNumber = models.CharField(max_length=15, blank=True, null=True)
    email = models.CharField(max_length=500, blank=True, null=True)
    photo = StdImageField(upload_to=UPLOAD_TO_PATTERNS,
                          variations={
                              'thumbnail': (100, 100, True),
                              'medium': (250, 250),
                          },
                          delete_orphans=True,
                          blank=True, )
    username = models.CharField(max_length=500, blank=True, null=True)
    password = models.CharField(max_length=500, blank=True, null=True)
    userID = models.ForeignKey(User, blank=True, null=True, on_delete=models.CASCADE)
    dateOfJoining = models.DateField(blank=True, null=True)
    dateOfLeaving = models.DateField(blank=True, null=True)
    currentPosition = models.CharField(max_length=500, blank=True, null=True)
    staffType = models.CharField(max_length=500, blank=True, null=True)
    employeeCode = models.CharField(max_length=500, blank=True, null=True)
    qualification = models.CharField(max_length=500, blank=True, null=True)
    additionalDetails = models.TextField(blank=True, null=True)
    datetime = models.DateTimeField(auto_now_add=True, auto_now=False)
    lastUpdatedOn = models.DateTimeField(auto_now_add=False, auto_now=True)
    isActive = models.CharField(max_length=200, blank=True, null=True, default='Yes')
    salary = models.FloatField(default=0.0)
    isDeleted = models.BooleanField(default=False)
    lastEditedBy = models.CharField(max_length=500, blank=True, null=True)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name_plural = 'g) Teachers Details'
        indexes = [
            models.Index(fields=['userID', 'isDeleted', 'datetime'], name='td_user_del_dt_idx'),
            models.Index(fields=['sessionID', 'isDeleted'], name='td_session_del_idx'),
        ]


class Standard(models.Model):
    name = models.CharField(max_length=500, blank=True, null=True)
    classLocation = models.CharField(max_length=500, blank=True, null=True, default='No Data')
    schoolID = models.ForeignKey(SchoolDetail, blank=True, null=True, on_delete=models.CASCADE)
    sessionID = models.ForeignKey(SchoolSession, blank=True, null=True, on_delete=models.CASCADE)
    hasSection = models.CharField(max_length=500, blank=True, null=True)
    startingRoll = models.CharField(max_length=500, blank=True, null=True)
    endingRoll = models.CharField(max_length=500, blank=True, null=True)
    section = models.CharField(max_length=500, blank=True, null=True)
    classTeacher = models.ForeignKey(TeacherDetail, blank=True, null=True, on_delete=models.CASCADE)
    datetime = models.DateTimeField(auto_now_add=True, auto_now=False)
    lastUpdatedOn = models.DateTimeField(auto_now_add=False, auto_now=True)
    isDeleted = models.BooleanField(default=False)
    lastEditedBy = models.CharField(max_length=500, blank=True, null=True)

    def __str__(self):
        return self.name + self.sessionID.sessionYear

    class Meta:
        verbose_name_plural = 'h) Standard Details'
        indexes = [
            models.Index(fields=['sessionID', 'isDeleted'], name='std_session_del_idx'),
            models.Index(fields=['sessionID', 'classTeacher', 'isDeleted'], name='std_sess_cls_del_idx'),
        ]


# class Section(models.Model):
#     standardID = models.ForeignKey(Standard, blank=True, null=True, on_delete=models.CASCADE)
#     schoolID = models.ForeignKey(SchoolDetail, blank=True, null=True, on_delete=models.CASCADE)
#     sessionID = models.ForeignKey(SchoolSession, blank=True, null=True, on_delete=models.CASCADE)
#     name = models.CharField(max_length=500, blank=True, null=True)
#     startingRoll = models.CharField(max_length=500, blank=True, null=True)
#     endingRoll = models.CharField(max_length=500, blank=True, null=True)
#     datetime = models.DateTimeField(auto_now_add=True, auto_now=False)
#     lastUpdatedOn = models.DateTimeField(auto_now_add=False, auto_now=True)
#     isDeleted = models.BooleanField(default=False)
#     lastEditedBy = models.CharField(max_length=500, blank=True, null=True)
#
#     def __str__(self):
#         return self.name
#
#     class Meta:
#         verbose_name_plural = 'i) Section Details'


# class AssignTeacherToClassOrSection(models.Model):
#     standardID = models.ForeignKey(Standard, blank=True, null=True, on_delete=models.CASCADE)
#     sessionID = models.ForeignKey(SchoolSession, blank=True, null=True, on_delete=models.CASCADE)
#     schoolID = models.ForeignKey(SchoolDetail, blank=True, null=True, on_delete=models.CASCADE)
#     sectionID = models.ForeignKey(Section, blank=True, null=True, on_delete=models.CASCADE)
#     classTeacher = models.ForeignKey(TeacherDetail, blank=True, null=True, on_delete=models.CASCADE)
#     datetime = models.DateTimeField(auto_now_add=True, auto_now=False)
#     lastUpdatedOn = models.DateTimeField(auto_now_add=False, auto_now=True)
#     isAssign = models.BooleanField(default=True)
#     lastEditedBy = models.CharField(max_length=500, blank=True, null=True)
#
#     class Meta:
#         verbose_name_plural = 'j)Assign Teacher To Class Or Section'


class Subjects(models.Model):
    name = models.CharField(max_length=500, blank=True, null=True)
    schoolID = models.ForeignKey(SchoolDetail, blank=True, null=True, on_delete=models.CASCADE)
    sessionID = models.ForeignKey(SchoolSession, blank=True, null=True, on_delete=models.CASCADE)
    datetime = models.DateTimeField(auto_now_add=True, auto_now=False)
    lastUpdatedOn = models.DateTimeField(auto_now_add=False, auto_now=True)
    isDeleted = models.BooleanField(default=False)
    lastEditedBy = models.CharField(max_length=500, blank=True, null=True)

    def __str__(self):
        return self.name + self.sessionID.sessionYear

    class Meta:
        verbose_name_plural = 'i) Subject Details'


class AssignSubjectsToClass(models.Model):
    standardID = models.ForeignKey(Standard, blank=True, null=True, on_delete=models.CASCADE)
    subjectID = models.ForeignKey(Subjects, blank=True, null=True, on_delete=models.CASCADE)
    schoolID = models.ForeignKey(SchoolDetail, blank=True, null=True, on_delete=models.CASCADE)
    sessionID = models.ForeignKey(SchoolSession, blank=True, null=True, on_delete=models.CASCADE)
    datetime = models.DateTimeField(auto_now_add=True, auto_now=False)
    lastUpdatedOn = models.DateTimeField(auto_now_add=False, auto_now=True)
    isDeleted = models.BooleanField(default=False)
    lastEditedBy = models.CharField(max_length=500, blank=True, null=True)

    def __str__(self):
        return self.standardID.name + ' - ' + self.sessionID.sessionYear

    class Meta:
        verbose_name_plural = 'j) Assign Subjects To Class Details'
        indexes = [
            models.Index(fields=['sessionID', 'standardID', 'isDeleted'], name='astc_sess_std_del_idx'),
        ]


class AssignSubjectsToTeacher(models.Model):
    assignedSubjectID = models.ForeignKey(AssignSubjectsToClass, blank=True, null=True, on_delete=models.CASCADE)
    teacherID = models.ForeignKey(TeacherDetail, blank=True, null=True, on_delete=models.CASCADE)
    schoolID = models.ForeignKey(SchoolDetail, blank=True, null=True, on_delete=models.CASCADE)
    sessionID = models.ForeignKey(SchoolSession, blank=True, null=True, on_delete=models.CASCADE)
    subjectBranch = models.CharField(max_length=500, blank=True, null=True)
    datetime = models.DateTimeField(auto_now_add=True, auto_now=False)
    lastUpdatedOn = models.DateTimeField(auto_now_add=False, auto_now=True)
    isDeleted = models.BooleanField(default=False)
    lastEditedBy = models.CharField(max_length=500, blank=True, null=True)

    def __str__(self):
        return self.teacherID.name + ' - ' + self.sessionID.sessionYear

    class Meta:
        verbose_name_plural = 'j) Assign Subjects To Class Details'
        indexes = [
            models.Index(fields=['sessionID', 'teacherID', 'isDeleted'], name='ast_sess_tchr_del_idx'),
            models.Index(fields=['teacherID', 'isDeleted', 'datetime'], name='ast_tchr_del_dt_idx'),
        ]


class Parent(models.Model):
    # ================= EXISTING FIELDS (UNCHANGED) =================
    fatherName = models.CharField(max_length=500, blank=True, null=True)
    motherName = models.CharField(max_length=500, blank=True, null=True)
    email = models.CharField(max_length=500, blank=True, null=True)
    phoneNumber = models.CharField(max_length=500, blank=True, null=True)
    profession = models.CharField(max_length=500, blank=True, null=True)
    schoolID = models.ForeignKey(SchoolDetail, blank=True, null=True, on_delete=models.CASCADE)
    sessionID = models.ForeignKey(SchoolSession, blank=True, null=True, on_delete=models.CASCADE)
    datetime = models.DateTimeField(auto_now_add=True, auto_now=False)
    lastUpdatedOn = models.DateTimeField(auto_now_add=False, auto_now=True)
    isDeleted = models.BooleanField(default=False)
    lastEditedBy = models.CharField(max_length=500, blank=True, null=True)

    # ================= NEW FIELDS (FROM PDF) =================
    fatherOccupation = models.CharField(max_length=500, blank=True, null=True)
    motherOccupation = models.CharField(max_length=500, blank=True, null=True)

    fatherAddress = models.TextField(blank=True, null=True)
    motherAddress = models.TextField(blank=True, null=True)

    fatherPhone = models.CharField(max_length=20, blank=True, null=True)
    motherPhone = models.CharField(max_length=20, blank=True, null=True)

    guardianName = models.CharField(max_length=500, blank=True, null=True)
    guardianOccupation = models.CharField(max_length=500, blank=True, null=True)
    guardianPhone = models.CharField(max_length=20, blank=True, null=True)

    fatherEmail = models.CharField(max_length=500, blank=True, null=True)
    motherEmail = models.CharField(max_length=500, blank=True, null=True)
    # ================= NEW FIELDS (FROM PDF) =================
    familyType = models.CharField(
        max_length=50,
        choices=[
            ('Single Parent', 'Single Parent'),
            ('Nuclear Family', 'Nuclear Family'),
            ('Joint Family', 'Joint Family'),
        ],
        blank=True,
        null=True
    )

    totalFamilyMembers = models.PositiveIntegerField(blank=True, null=True)
    annualIncome = models.FloatField(default=0.0)

    def __str__(self):
        return self.fatherName + ' - ' + self.sessionID.sessionYear

    class Meta:
        verbose_name_plural = 'j)Parent Details'

class Student(models.Model):
    # ================= EXISTING FIELDS (UNCHANGED) =================
    sessionID = models.ForeignKey(SchoolSession, blank=True, null=True, on_delete=models.CASCADE)
    schoolID = models.ForeignKey(SchoolDetail, blank=True, null=True, on_delete=models.CASCADE)
    parentID = models.ForeignKey(Parent, blank=True, null=True, on_delete=models.CASCADE)
    standardID = models.ForeignKey(Standard, blank=True, null=True, on_delete=models.CASCADE)
    roll = models.CharField(max_length=500, blank=True, null=True)
    registrationCode = models.CharField(max_length=500, blank=True, null=True)
    name = models.CharField(max_length=500, blank=True, null=True)
    dob = models.DateField(blank=True, null=True)
    aadhar = models.CharField(max_length=500, blank=True, null=True)
    gender = models.CharField(max_length=500, blank=True, null=True)
    bloodGroup = models.CharField(max_length=500, blank=True, null=True)
    presentAddress = models.TextField(blank=True, null=True)
    presentPinCode = models.CharField(max_length=500, blank=True, null=True)
    presentCity = models.CharField(max_length=500, blank=True, null=True)
    presentState = models.CharField(max_length=500, blank=True, null=True)
    presentCountry = models.CharField(max_length=500, blank=True, null=True)
    permanentAddress = models.TextField(blank=True, null=True)
    permanentPinCode = models.CharField(max_length=500, blank=True, null=True)
    permanentCity = models.CharField(max_length=500, blank=True, null=True)
    permanentState = models.CharField(max_length=500, blank=True, null=True)
    permanentCountry = models.CharField(max_length=500, blank=True, null=True)
    phoneNumber = models.CharField(max_length=15, blank=True, null=True)
    email = models.CharField(max_length=500, blank=True, null=True)
    photo = StdImageField(
        upload_to=UPLOAD_TO_PATTERNS,
        variations={
            'thumbnail': (100, 100, True),
            'medium': (250, 250),
        },
        delete_orphans=True,
        blank=True,
    )
    username = models.CharField(max_length=500, blank=True, null=True)
    password = models.CharField(max_length=500, blank=True, null=True)
    userID = models.ForeignKey(User, blank=True, null=True, on_delete=models.CASCADE)
    dateOfJoining = models.DateField(blank=True, null=True)
    dateOfLeaving = models.DateField(blank=True, null=True)
    additionalDetails = models.TextField(blank=True, null=True)
    datetime = models.DateTimeField(auto_now_add=True, auto_now=False)
    lastUpdatedOn = models.DateTimeField(auto_now_add=False, auto_now=True)
    isActive = models.CharField(max_length=200, blank=True, null=True, default='Yes')
    admissionFee = models.FloatField(default=0.0)
    tuitionFee = models.FloatField(default=0.0)
    isDeleted = models.BooleanField(default=False)
    lastEditedBy = models.CharField(max_length=500, blank=True, null=True)

    # ================= NEW FIELDS (FROM PDF) =================
    idMark = models.CharField(max_length=500, blank=True, null=True)

    caste = models.CharField(
        max_length=50,
        choices=[('ST','ST'),('SC','SC'),('GEN','GEN'),('OBC','OBC'),('OTH','OTH')],
        blank=True,
        null=True
    )
    languageKnown = models.TextField(
        blank=True,
        null=True,
        help_text="Languages known by the student (comma separated)"
    )
    tribe = models.CharField(max_length=500, blank=True, null=True)
    religion = models.CharField(max_length=500, blank=True, null=True)

    penNumber = models.CharField(max_length=100, blank=True, null=True)

    isStayingWithParents = models.BooleanField(default=True)

    lastSchoolName = models.CharField(max_length=500, blank=True, null=True)
    lastSchoolAddress = models.TextField(blank=True, null=True)
    lastClass = models.CharField(max_length=100, blank=True, null=True)
    lastResult = models.CharField(
        max_length=10,
        choices=[('PASS','PASS'),('FAIL','FAIL')],
        blank=True,
        null=True
    )
    lastDivision = models.CharField(max_length=100, blank=True, null=True)
    lastRollNo = models.CharField(max_length=100, blank=True, null=True)

    motherTongue = models.CharField(max_length=200, blank=True, null=True)
    otherLanguages = models.TextField(blank=True, null=True)
    hobbies = models.TextField(blank=True, null=True)
    aimInLife = models.TextField(blank=True, null=True)
    milOption = models.CharField(max_length=500, blank=True, null=True)

    familyCode = models.CharField(max_length=200, blank=True, null=True)
    siblingsCount = models.PositiveIntegerField(default=0)

    januaryTuitionFee = models.FloatField(default=0.0)
    miscFee = models.FloatField(default=0.0)
    totalFee = models.FloatField(default=0.0)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name_plural = 'g) Student Details'
        indexes = [
            models.Index(fields=['userID', 'isDeleted', 'datetime'], name='stu_user_del_dt_idx'),
            models.Index(fields=['sessionID', 'isDeleted', 'standardID'], name='stu_sess_del_std_idx'),
            models.Index(fields=['sessionID', 'standardID', 'isDeleted'], name='stu_sess_std_del_idx'),
        ]


class Exam(models.Model):
    name = models.CharField(max_length=500, blank=True, null=True)
    schoolID = models.ForeignKey(SchoolDetail, blank=True, null=True, on_delete=models.CASCADE)
    sessionID = models.ForeignKey(SchoolSession, blank=True, null=True, on_delete=models.CASCADE)
    datetime = models.DateTimeField(auto_now_add=True, auto_now=False)
    lastUpdatedOn = models.DateTimeField(auto_now_add=False, auto_now=True)
    isDeleted = models.BooleanField(default=False)
    lastEditedBy = models.CharField(max_length=500, blank=True, null=True)

    def __str__(self):
        return self.name + self.sessionID.sessionYear

    class Meta:
        verbose_name_plural = 'h) Exam Details'


class AssignExamToClass(models.Model):
    standardID = models.ForeignKey(Standard, blank=True, null=True, on_delete=models.CASCADE)
    examID = models.ForeignKey(Exam, blank=True, null=True, on_delete=models.CASCADE)
    fullMarks = models.FloatField(max_length=500, blank=True, null=True)
    passMarks = models.FloatField(max_length=500, blank=True, null=True)
    startDate = models.DateField(blank=True, null=True)
    endDate = models.DateField(blank=True, null=True)
    schoolID = models.ForeignKey(SchoolDetail, blank=True, null=True, on_delete=models.CASCADE)
    sessionID = models.ForeignKey(SchoolSession, blank=True, null=True, on_delete=models.CASCADE)
    datetime = models.DateTimeField(auto_now_add=True, auto_now=False)
    lastUpdatedOn = models.DateTimeField(auto_now_add=False, auto_now=True)
    isDeleted = models.BooleanField(default=False)
    lastEditedBy = models.CharField(max_length=500, blank=True, null=True)

    def __str__(self):
        return self.examID.name + ' - ' + self.sessionID.sessionYear

    class Meta:
        verbose_name_plural = 'j) Assign Exam To Class Details'
        indexes = [
            models.Index(fields=['sessionID', 'standardID', 'isDeleted', 'startDate'], name='aec_sess_std_date_idx'),
        ]


class ExamTimeTable(models.Model):
    schoolID = models.ForeignKey(SchoolDetail, blank=True, null=True, on_delete=models.CASCADE)
    sessionID = models.ForeignKey(SchoolSession, blank=True, null=True, on_delete=models.CASCADE)
    standardID = models.ForeignKey(Standard, blank=True, null=True, on_delete=models.CASCADE)
    examID = models.ForeignKey(Exam, blank=True, null=True, on_delete=models.CASCADE)
    subjectID = models.ForeignKey(Subjects, blank=True, null=True, on_delete=models.CASCADE)
    examDate = models.DateField(blank=True, null=True)
    startTime = models.TimeField(blank=True, null=True)
    endTime = models.TimeField(blank=True, null=True)
    roomNo = models.CharField(max_length=200, blank=True, null=True)
    note = models.TextField(blank=True, null=True, default='')
    datetime = models.DateTimeField(auto_now_add=True, auto_now=False)
    lastUpdatedOn = models.DateTimeField(auto_now_add=False, auto_now=True)
    isDeleted = models.BooleanField(default=False)
    lastEditedBy = models.CharField(max_length=500, blank=True, null=True)

    class Meta:
        verbose_name_plural = 'k) Exam Time Table'
        constraints = [
            models.CheckConstraint(
                check=models.Q(startTime__lt=models.F('endTime')),
                name='exam_timetable_start_before_end'
            ),
            models.UniqueConstraint(
                fields=['sessionID', 'standardID', 'examID', 'subjectID', 'examDate'],
                condition=models.Q(isDeleted=False),
                name='exam_timetable_unique_active_entry'
            ),
        ]


class StudentAttendance(models.Model):
    isPresent = models.BooleanField(default=False)
    isHoliday = models.BooleanField(default=False)
    bySubject = models.BooleanField(default=False)
    studentID = models.ForeignKey(Student, blank=True, null=True, on_delete=models.CASCADE)
    standardID = models.ForeignKey(Standard, blank=True, null=True, on_delete=models.CASCADE)
    subjectID = models.ForeignKey(Subjects, blank=True, null=True, on_delete=models.CASCADE)
    sessionID = models.ForeignKey(SchoolSession, blank=True, null=True, on_delete=models.CASCADE)
    schoolID = models.ForeignKey(SchoolDetail, blank=True, null=True, on_delete=models.CASCADE)
    attendanceDate = models.DateTimeField(blank=True, null=True)
    absentReason = models.CharField(max_length=500, blank=True, null=True, default='')
    datetime = models.DateTimeField(auto_now_add=True, auto_now=False)
    lastUpdatedOn = models.DateTimeField(auto_now_add=False, auto_now=True)
    isDeleted = models.BooleanField(default=False)
    lastEditedBy = models.CharField(max_length=500, blank=True, null=True)

    class Meta:
        verbose_name_plural = 'p) Student Attendance'
        indexes = [
            models.Index(fields=['sessionID', 'studentID', 'isDeleted', 'isHoliday'], name='sa_sess_stu_del_hol_idx'),
            models.Index(fields=['sessionID', 'standardID', 'isDeleted', 'attendanceDate'], name='sa_sess_std_dt_idx'),
        ]


class TeacherAttendance(models.Model):
    isPresent = models.BooleanField(default=False)
    isHoliday = models.BooleanField(default=False)
    teacherID = models.ForeignKey(TeacherDetail, blank=True, null=True, on_delete=models.CASCADE)
    sessionID = models.ForeignKey(SchoolSession, blank=True, null=True, on_delete=models.CASCADE)
    schoolID = models.ForeignKey(SchoolDetail, blank=True, null=True, on_delete=models.CASCADE)
    attendanceDate = models.DateTimeField(blank=True, null=True)
    absentReason = models.CharField(max_length=500, blank=True, null=True, default='')
    datetime = models.DateTimeField(auto_now_add=True, auto_now=False)
    lastUpdatedOn = models.DateTimeField(auto_now_add=False, auto_now=True)
    isDeleted = models.BooleanField(default=False)
    lastEditedBy = models.CharField(max_length=500, blank=True, null=True)

    class Meta:
        verbose_name_plural = 'q) Teacher Attendance'
        indexes = [
            models.Index(fields=['sessionID', 'teacherID', 'isDeleted', 'attendanceDate'], name='ta_sess_tchr_dt_idx'),
        ]


class StudentFee(models.Model):
    schoolID = models.ForeignKey(SchoolDetail, blank=True, null=True, on_delete=models.CASCADE)
    sessionID = models.ForeignKey(SchoolSession, blank=True, null=True, on_delete=models.CASCADE)
    studentID = models.ForeignKey(Student, blank=True, null=True, on_delete=models.CASCADE)
    standardID = models.ForeignKey(Standard, blank=True, null=True, on_delete=models.CASCADE)
    month = models.CharField(max_length=100, blank=True, null=True)
    note = models.TextField(blank=True, null=True, default='')
    amount = models.FloatField(default=0.0)
    payDate = models.DateField(blank=True, null=True)
    isPaid = models.BooleanField(default=False)
    datetime = models.DateTimeField(auto_now_add=True, auto_now=False)
    lastUpdatedOn = models.DateTimeField(auto_now_add=False, auto_now=True)
    isDeleted = models.BooleanField(default=False)
    lastEditedBy = models.CharField(max_length=500, blank=True, null=True)

    class Meta:
        verbose_name_plural = 'r) Student Fee'
        indexes = [
            models.Index(fields=['sessionID', 'studentID', 'standardID', 'isDeleted'], name='sf_sess_stu_std_del_idx'),
            models.Index(fields=['sessionID', 'studentID', 'isPaid', 'isDeleted'], name='sf_sess_stu_paid_idx'),
        ]


class MarkOfStudentsByExam(models.Model):
    schoolID = models.ForeignKey(SchoolDetail, blank=True, null=True, on_delete=models.CASCADE)
    sessionID = models.ForeignKey(SchoolSession, blank=True, null=True, on_delete=models.CASCADE)
    examID = models.ForeignKey(AssignExamToClass, blank=True, null=True, on_delete=models.CASCADE)
    studentID = models.ForeignKey(Student, blank=True, null=True, on_delete=models.CASCADE)
    standardID = models.ForeignKey(Standard, blank=True, null=True, on_delete=models.CASCADE)
    subjectID = models.ForeignKey(AssignSubjectsToClass, blank=True, null=True, on_delete=models.CASCADE)
    mark = models.FloatField(default=0.0)
    note = models.TextField(blank=True, null=True, default='')
    datetime = models.DateTimeField(auto_now_add=True, auto_now=False)
    lastUpdatedOn = models.DateTimeField(auto_now_add=False, auto_now=True)
    isDeleted = models.BooleanField(default=False)
    lastEditedBy = models.CharField(max_length=500, blank=True, null=True)

    class Meta:
        verbose_name_plural = 's) Mark Of Students By Exam'
        indexes = [
            models.Index(fields=['sessionID', 'studentID', 'examID', 'isDeleted'], name='mse_sess_stu_exm_idx'),
            models.Index(fields=['sessionID', 'standardID', 'isDeleted'], name='mse_sess_std_del_idx'),
        ]


class EventType(models.Model):
    AUDIENCE_CHOICES = (
        ('general', 'General'),
        ('teacherapp', 'Teacher App'),
        ('studentapp', 'Student App'),
        ('managementapp', 'Management App'),
        ('all_apps', 'All Apps'),
    )
    schoolID = models.ForeignKey(SchoolDetail, blank=True, null=True, on_delete=models.CASCADE)
    sessionID = models.ForeignKey(SchoolSession, blank=True, null=True, on_delete=models.CASCADE)
    name = models.CharField(max_length=500, blank=True, null=True)
    audience = models.CharField(max_length=100, choices=AUDIENCE_CHOICES, default='general')
    description = models.TextField(blank=True, null=True, default='')
    datetime = models.DateTimeField(auto_now_add=True, auto_now=False)
    lastUpdatedOn = models.DateTimeField(auto_now_add=False, auto_now=True)
    isDeleted = models.BooleanField(default=False)
    lastEditedBy = models.CharField(max_length=500, blank=True, null=True)

    class Meta:
        verbose_name_plural = 't) Events Type'
        indexes = [
            models.Index(fields=['sessionID', 'audience', 'isDeleted'], name='evt_type_sess_aud_idx'),
        ]


class Event(models.Model):
    schoolID = models.ForeignKey(SchoolDetail, blank=True, null=True, on_delete=models.CASCADE)
    sessionID = models.ForeignKey(SchoolSession, blank=True, null=True, on_delete=models.CASCADE)
    eventID = models.ForeignKey(EventType, blank=True, null=True, on_delete=models.CASCADE)
    title = models.CharField(max_length=500, blank=True, null=True)
    message = models.TextField(blank=True, null=True)
    startDate = models.DateField(blank=True, null=True)
    endDate = models.DateField(blank=True, null=True)
    datetime = models.DateTimeField(auto_now_add=True, auto_now=False)
    lastUpdatedOn = models.DateTimeField(auto_now_add=False, auto_now=True)
    isDeleted = models.BooleanField(default=False)

    def __str__(self):
        return self.title

    class Meta:
        verbose_name_plural = 'v) Event'
        indexes = [
            models.Index(fields=['sessionID', 'isDeleted', 'startDate'], name='evt_sess_del_start_idx'),
            models.Index(fields=['sessionID', 'isDeleted', 'datetime'], name='evt_sess_del_dt_idx'),
        ]


class StudentIdCardRecord(models.Model):
    ACTION_CHOICES = (
        ('issue', 'Issue'),
        ('reissue', 'Re-Issue'),
        ('print', 'Print'),
    )

    studentID = models.ForeignKey(Student, blank=True, null=True, on_delete=models.CASCADE)
    schoolID = models.ForeignKey(SchoolDetail, blank=True, null=True, on_delete=models.CASCADE)
    sessionID = models.ForeignKey(SchoolSession, blank=True, null=True, on_delete=models.CASCADE)
    actionType = models.CharField(max_length=50, choices=ACTION_CHOICES, default='print')
    validTill = models.DateField(blank=True, null=True)
    remark = models.CharField(max_length=500, blank=True, null=True, default='')
    datetime = models.DateTimeField(auto_now_add=True, auto_now=False)
    lastUpdatedOn = models.DateTimeField(auto_now_add=False, auto_now=True)
    isDeleted = models.BooleanField(default=False)
    lastEditedBy = models.CharField(max_length=500, blank=True, null=True)

    class Meta:
        verbose_name_plural = 'w) Student ID Card Records'
        indexes = [
            models.Index(fields=['sessionID', 'studentID', 'isDeleted', 'datetime'], name='sid_sess_stu_del_idx'),
        ]


class LeaveType(models.Model):
    APPLICABLE_FOR_CHOICES = (
        ('both', 'Both'),
        ('teacher', 'Teacher'),
        ('student', 'Student'),
    )

    schoolID = models.ForeignKey(SchoolDetail, blank=True, null=True, on_delete=models.CASCADE)
    sessionID = models.ForeignKey(SchoolSession, blank=True, null=True, on_delete=models.CASCADE)
    name = models.CharField(max_length=200)
    code = models.CharField(max_length=50, blank=True, null=True)
    applicableFor = models.CharField(max_length=20, choices=APPLICABLE_FOR_CHOICES, default='both')
    requiresApproval = models.BooleanField(default=True)
    isActive = models.BooleanField(default=True)
    datetime = models.DateTimeField(auto_now_add=True, auto_now=False)
    lastUpdatedOn = models.DateTimeField(auto_now_add=False, auto_now=True)
    isDeleted = models.BooleanField(default=False)
    lastEditedBy = models.CharField(max_length=500, blank=True, null=True)

    class Meta:
        verbose_name_plural = 'x) Leave Types'
        indexes = [
            models.Index(fields=['sessionID', 'isDeleted', 'isActive'], name='lt_sess_del_act_idx'),
        ]


class LeaveApplication(models.Model):
    ROLE_CHOICES = (
        ('teacher', 'Teacher'),
        ('student', 'Student'),
    )
    STATUS_CHOICES = (
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
        ('cancelled', 'Cancelled'),
    )

    schoolID = models.ForeignKey(SchoolDetail, blank=True, null=True, on_delete=models.CASCADE)
    sessionID = models.ForeignKey(SchoolSession, blank=True, null=True, on_delete=models.CASCADE)
    leaveTypeID = models.ForeignKey(LeaveType, blank=True, null=True, on_delete=models.SET_NULL)
    applicantUserID = models.ForeignKey(User, blank=True, null=True, on_delete=models.SET_NULL)
    teacherID = models.ForeignKey(TeacherDetail, blank=True, null=True, on_delete=models.CASCADE)
    studentID = models.ForeignKey(Student, blank=True, null=True, on_delete=models.CASCADE)
    applicantRole = models.CharField(max_length=20, choices=ROLE_CHOICES)
    startDate = models.DateField()
    endDate = models.DateField()
    totalDays = models.PositiveIntegerField(default=1)
    reason = models.TextField(blank=True, null=True)
    attachment = models.FileField(upload_to=UPLOAD_TO_PATTERNS, blank=True, null=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    actionRemark = models.TextField(blank=True, null=True)
    actionByUserID = models.ForeignKey(User, blank=True, null=True, on_delete=models.SET_NULL, related_name='leave_actions')
    actionOn = models.DateTimeField(blank=True, null=True)
    datetime = models.DateTimeField(auto_now_add=True, auto_now=False)
    lastUpdatedOn = models.DateTimeField(auto_now_add=False, auto_now=True)
    isDeleted = models.BooleanField(default=False)
    lastEditedBy = models.CharField(max_length=500, blank=True, null=True)

    class Meta:
        verbose_name_plural = 'y) Leave Applications'
        constraints = [
            models.CheckConstraint(
                check=models.Q(startDate__lte=models.F('endDate')),
                name='leave_application_start_before_end'
            ),
        ]
        indexes = [
            models.Index(fields=['sessionID', 'applicantRole', 'status', 'isDeleted'], name='la_sess_role_stat_idx'),
            models.Index(fields=['sessionID', 'teacherID', 'status', 'isDeleted'], name='la_sess_tchr_stat_idx'),
            models.Index(fields=['sessionID', 'studentID', 'status', 'isDeleted'], name='la_sess_stu_stat_idx'),
        ]


class LeaveActionLog(models.Model):
    ACTION_CHOICES = (
        ('created', 'Created'),
        ('updated', 'Updated'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
        ('cancelled', 'Cancelled'),
    )

    schoolID = models.ForeignKey(SchoolDetail, blank=True, null=True, on_delete=models.CASCADE)
    sessionID = models.ForeignKey(SchoolSession, blank=True, null=True, on_delete=models.CASCADE)
    leaveID = models.ForeignKey(LeaveApplication, on_delete=models.CASCADE)
    action = models.CharField(max_length=30, choices=ACTION_CHOICES)
    remark = models.TextField(blank=True, null=True)
    actionByUserID = models.ForeignKey(User, blank=True, null=True, on_delete=models.SET_NULL)
    datetime = models.DateTimeField(auto_now_add=True, auto_now=False)
    lastUpdatedOn = models.DateTimeField(auto_now_add=False, auto_now=True)
    isDeleted = models.BooleanField(default=False)
    lastEditedBy = models.CharField(max_length=500, blank=True, null=True)

    class Meta:
        verbose_name_plural = 'z) Leave Action Logs'
        indexes = [
            models.Index(fields=['sessionID', 'leaveID', 'isDeleted', 'datetime'], name='lal_sess_leave_dt_idx'),
        ]
