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


class Parent(models.Model):
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

    def __str__(self):
        return self.fatherName + ' - ' + self.sessionID.sessionYear

    class Meta:
        verbose_name_plural = 'j)Parent Details'


class Student(models.Model):
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
    additionalDetails = models.TextField(blank=True, null=True)
    datetime = models.DateTimeField(auto_now_add=True, auto_now=False)
    lastUpdatedOn = models.DateTimeField(auto_now_add=False, auto_now=True)
    isActive = models.CharField(max_length=200, blank=True, null=True, default='Yes')
    admissionFee = models.FloatField(default=0.0)
    tuitionFee = models.FloatField(default=0.0)
    isDeleted = models.BooleanField(default=False)
    lastEditedBy = models.CharField(max_length=500, blank=True, null=True)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name_plural = 'g) Student Details'


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
