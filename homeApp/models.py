from django.db import models
from stdimage import StdImageField
from django.contrib.auth.models import User
from utils.utils import UPLOAD_TO_PATTERNS


class SchoolOwner(models.Model):
    name = models.CharField(max_length=500, blank=True, null=True)
    email = models.CharField(max_length=500, blank=True, null=True)
    password = models.CharField(max_length=500, blank=True, null=True)
    phoneNumber = models.CharField(max_length=15, blank=True, null=True)
    username = models.CharField(max_length=500, blank=True, null=True)
    userID = models.ForeignKey(User, blank=True, null=True, on_delete=models.CASCADE)
    isActive = models.BooleanField(default=True)
    userGroup = models.CharField(max_length=500, blank=True, null=True)
    datetime = models.DateTimeField(auto_now_add=True, auto_now=False)
    lastUpdatedOn = models.DateTimeField(auto_now_add=False, auto_now=True)
    lastEditedBy = models.CharField(max_length=500, blank=True, null=True)
    isDeleted = models.BooleanField(default=False)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name_plural = 'a) School Owner.'


class SchoolDetail(models.Model):
    ownerID = models.ForeignKey(SchoolOwner, blank=True, null=True, on_delete=models.CASCADE)
    schoolName = models.CharField(max_length=500, blank=True, null=True)
    name = models.CharField(max_length=500, blank=True, null=True)
    logo = StdImageField(upload_to=UPLOAD_TO_PATTERNS,
                         variations={
                             'thumbnail': (100, 100, True),
                             'medium': (250, 250),
                         },
                         delete_orphans=True,
                         blank=True, )
    address = models.TextField()
    city = models.CharField(max_length=500, blank=True, null=True)
    state = models.CharField(max_length=500, blank=True, null=True)
    country = models.CharField(max_length=500, blank=True, null=True)
    pinCode = models.CharField(max_length=15, blank=True, null=True)
    phoneNumber = models.CharField(max_length=15, blank=True, null=True)
    email = models.CharField(max_length=500, blank=True, null=True)
    website = models.CharField(max_length=500, blank=True, null=True)
    datetime = models.DateTimeField(auto_now_add=True, auto_now=False)
    lastUpdatedOn = models.DateTimeField(auto_now_add=False, auto_now=True)
    lastEditedBy = models.CharField(max_length=500, blank=True, null=True)
    isDeleted = models.BooleanField(default=False)

    def __str__(self):
        return self.schoolName

    class Meta:
        verbose_name_plural = 'b) School Detail.'


class SchoolSocialLink(models.Model):
    schoolID = models.ForeignKey(SchoolDetail, blank=True, null=True, on_delete=models.CASCADE)
    facebook = models.CharField(max_length=500, blank=True, null=True, default='N/A')
    twitter = models.CharField(max_length=500, blank=True, null=True, default='N/A')
    googlePlus = models.CharField(max_length=500, blank=True, null=True, default='N/A')
    instagram = models.CharField(max_length=500, blank=True, null=True, default='N/A')
    datetime = models.DateTimeField(auto_now_add=True, auto_now=False)
    lastUpdatedOn = models.DateTimeField(auto_now_add=False, auto_now=True)
    lastEditedBy = models.CharField(max_length=500, blank=True, null=True)
    isDeleted = models.BooleanField(default=False)

    def __str__(self):
        return str(self.schoolID.name)

    class Meta:
        verbose_name_plural = 'c) School Social Links.'


class SchoolSession(models.Model):
    schoolID = models.ForeignKey(SchoolDetail, blank=True, null=True, on_delete=models.CASCADE)
    sessionYear = models.CharField(max_length=500, blank=True, null=True)
    isCurrent = models.BooleanField(default=False)
    datetime = models.DateTimeField(auto_now_add=True, auto_now=False)
    lastUpdatedOn = models.DateTimeField(auto_now_add=False, auto_now=True)
    lastEditedBy = models.CharField(max_length=500, blank=True, null=True)
    isDeleted = models.BooleanField(default=False)

    def __str__(self):
        return self.sessionYear

    class Meta:
        verbose_name_plural = 'd) School Session.'
