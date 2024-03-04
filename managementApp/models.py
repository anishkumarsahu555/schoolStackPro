from django.contrib.auth.models import User
from django.db import models
from stdimage import StdImageField

from homeApp.models import SchoolDetail, SchoolSession
from utils.utils import UPLOAD_TO_PATTERNS


# Create your models here.


class ComputerOperator(models.Model):
    firstName = models.CharField(max_length=500, blank=True, null=True)
    middleName = models.CharField(max_length=500, blank=True, null=True)
    lastName = models.CharField(max_length=500, blank=True, null=True)
    phoneNumber = models.CharField(max_length=15, blank=True, null=True)
    email = models.CharField(max_length=500, blank=True, null=True)
    address = models.TextField(blank=True, null=True)
    city = models.CharField(max_length=500, blank=True, null=True)
    pinCode = models.CharField(max_length=500, blank=True, null=True)
    state = models.CharField(max_length=500, blank=True, null=True)
    country = models.CharField(max_length=500, blank=True, null=True)
    aadhar = models.CharField(max_length=500, blank=True, null=True)
    DOB = models.DateField(blank=True, null=True)
    qualification = models.CharField(max_length=500, blank=True, null=True)
    joinDate = models.DateField(blank=True, null=True)
    releaveDate = models.DateField(blank=True, null=True)
    username = models.CharField(max_length=500, blank=True, null=True)
    password = models.CharField(max_length=500, blank=True, null=True)
    userID = models.ForeignKey(User, blank=True, null=True, on_delete=models.CASCADE)
    schoolID = models.ForeignKey(SchoolDetail, blank=True, null=True, on_delete=models.CASCADE)
    sessionID = models.ForeignKey(SchoolSession, blank=True, null=True, on_delete=models.CASCADE)
    photo = StdImageField(upload_to=UPLOAD_TO_PATTERNS,
                         variations={
                             'thumbnail': (100, 100, True),
                             'medium': (250, 250),
                         },
                         delete_orphans=True,
                         blank=True, )
    datetime = models.DateTimeField(auto_now_add=True, auto_now=False)
    lastUpdatedOn = models.DateTimeField(auto_now_add=False, auto_now=True)
    isActive = models.BooleanField(default=True)
    isDeleted = models.BooleanField(default=False)
    lastEditedBy = models.CharField(max_length=500, blank=True, null=True)

    def __str__(self):
        return self.firstName

    class Meta:
        verbose_name_plural = 'e) Computer Operator.'


class NonTeachingStaff(models.Model):
    firstName = models.CharField(max_length=500, blank=True, null=True)
    middleName = models.CharField(max_length=500, blank=True, null=True)
    lastName = models.CharField(max_length=500, blank=True, null=True)
    phoneNumber = models.CharField(max_length=15, blank=True, null=True)
    email = models.CharField(max_length=500, blank=True, null=True)
    address = models.TextField(blank=True, null=True)
    city = models.CharField(max_length=500, blank=True, null=True)
    pinCode = models.CharField(max_length=500, blank=True, null=True)
    state = models.CharField(max_length=500, blank=True, null=True)
    country = models.CharField(max_length=500, blank=True, null=True)
    aadhar = models.CharField(max_length=500, blank=True, null=True)
    DOB = models.DateField(blank=True, null=True)
    gender = models.CharField(max_length=500, blank=True, null=True)
    bloodGroup = models.CharField(max_length=500, blank=True, null=True)
    EmployeeCode = models.CharField(max_length=500, blank=True, null=True)
    currentPosition = models.CharField(max_length=500, blank=True, null=True)
    qualification = models.CharField(max_length=500, blank=True, null=True)
    joinDate = models.DateField(blank=True, null=True)
    releaveDate = models.DateField(blank=True, null=True)
    username = models.CharField(max_length=500, blank=True, null=True)
    password = models.CharField(max_length=500, blank=True, null=True)
    userID = models.ForeignKey(User, blank=True, null=True, on_delete=models.CASCADE)
    schoolID = models.ForeignKey(SchoolDetail, blank=True, null=True, on_delete=models.CASCADE)
    sessionID = models.ForeignKey(SchoolSession, blank=True, null=True, on_delete=models.CASCADE)
    photo = StdImageField(upload_to=UPLOAD_TO_PATTERNS,
                          variations={
                              'thumbnail': (100, 100, True),
                              'medium': (250, 250),
                          },
                          delete_orphans=True,
                          blank=True, )
    datetime = models.DateTimeField(auto_now_add=True, auto_now=False)
    lastUpdatedOn = models.DateTimeField(auto_now_add=False, auto_now=True)
    isActive = models.BooleanField(default=True)
    isDeleted = models.BooleanField(default=False)
    lastEditedBy = models.CharField(max_length=500, blank=True, null=True)

    def __str__(self):
        return self.firstName

    class Meta:
        verbose_name_plural = 'f) Non- Teaching Staff.'


class TeacherDetail(models.Model):
    sessionID = models.ForeignKey(SchoolSession, blank=True, null=True, on_delete=models.CASCADE)
    schoolID = models.ForeignKey(SchoolDetail, blank=True, null=True, on_delete=models.CASCADE)
    firstName = models.CharField(max_length=500, blank=True, null=True)
    middleName = models.CharField(max_length=500, blank=True, null=True)
    lastName = models.CharField(max_length=500, blank=True, null=True)
    DOB = models.DateField(blank=True, null=True)
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
    EmployeeCode = models.CharField(max_length=500, blank=True, null=True)
    qualification = models.CharField(max_length=500, blank=True, null=True)
    datetime = models.DateTimeField(auto_now_add=True, auto_now=False)
    lastUpdatedOn = models.DateTimeField(auto_now_add=False, auto_now=True)
    isActive = models.BooleanField(default=True)
    isDeleted = models.BooleanField(default=False)
    lastEditedBy = models.CharField(max_length=500, blank=True, null=True)

    def __str__(self):
        return self.firstName

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
