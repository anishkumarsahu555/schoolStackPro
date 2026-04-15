from django.db import models
from datetime import date
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
    updatedByUserID = models.ForeignKey(User, blank=True, null=True, on_delete=models.SET_NULL, related_name='+')
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
    webPushEnabled = models.BooleanField(default=False)
    webPushStudentAppEnabled = models.BooleanField(default=True)
    webPushTeacherAppEnabled = models.BooleanField(default=True)
    webPushManagementAppEnabled = models.BooleanField(default=True)
    liveClassEnabled = models.BooleanField(default=False)
    liveClassWebhookSecret = models.CharField(max_length=255, blank=True, null=True)
    activationEnabled = models.BooleanField(default=True)
    activationStartDate = models.DateField(blank=True, null=True)
    activationEndDate = models.DateField(blank=True, null=True)
    activationMessage = models.TextField(blank=True, null=True)
    datetime = models.DateTimeField(auto_now_add=True, auto_now=False)
    lastUpdatedOn = models.DateTimeField(auto_now_add=False, auto_now=True)
    lastEditedBy = models.CharField(max_length=500, blank=True, null=True)
    updatedByUserID = models.ForeignKey(User, blank=True, null=True, on_delete=models.SET_NULL, related_name='+')
    isDeleted = models.BooleanField(default=False)

    def __str__(self):
        return self.schoolName

    @property
    def activation_status(self):
        today = date.today()
        if not self.activationEnabled:
            return 'inactive'
        if self.activationStartDate and self.activationStartDate > today:
            return 'scheduled'
        if self.activationEndDate and self.activationEndDate < today:
            return 'expired'
        return 'active'

    @property
    def activation_is_valid(self):
        return self.activation_status == 'active'

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
    updatedByUserID = models.ForeignKey(User, blank=True, null=True, on_delete=models.SET_NULL, related_name='+')
    isDeleted = models.BooleanField(default=False)

    def __str__(self):
        return str(self.schoolID.name)

    class Meta:
        verbose_name_plural = 'c) School Social Links.'


class SchoolSession(models.Model):
    FEE_RESYNC_STATUS_CHOICES = (
        ('idle', 'Idle'),
        ('pending', 'Pending'),
        ('running', 'Running'),
        ('done', 'Done'),
        ('failed', 'Failed'),
    )

    schoolID = models.ForeignKey(SchoolDetail, blank=True, null=True, on_delete=models.CASCADE)
    sessionYear = models.CharField(max_length=500, blank=True, null=True)
    startDate = models.DateField(blank=True, null=True)
    endDate = models.DateField(blank=True, null=True)
    isCurrent = models.BooleanField(default=False)
    feeResyncStatus = models.CharField(max_length=20, choices=FEE_RESYNC_STATUS_CHOICES, default='idle')
    feeResyncRequestedAt = models.DateTimeField(blank=True, null=True)
    feeResyncStartedAt = models.DateTimeField(blank=True, null=True)
    feeResyncFinishedAt = models.DateTimeField(blank=True, null=True)
    feeResyncUpdatedCount = models.PositiveIntegerField(default=0)
    feeResyncCreatedCount = models.PositiveIntegerField(default=0)
    feeResyncError = models.TextField(blank=True, null=True)
    datetime = models.DateTimeField(auto_now_add=True, auto_now=False)
    lastUpdatedOn = models.DateTimeField(auto_now_add=False, auto_now=True)
    lastEditedBy = models.CharField(max_length=500, blank=True, null=True)
    updatedByUserID = models.ForeignKey(User, blank=True, null=True, on_delete=models.SET_NULL, related_name='+')
    isDeleted = models.BooleanField(default=False)

    def __str__(self):
        return self.sessionYear or f"Session #{self.pk}"

    class Meta:
        verbose_name_plural = 'd) School Session.'
        constraints = [
            models.CheckConstraint(
                check=models.Q(startDate__isnull=True) | models.Q(endDate__isnull=True) | models.Q(startDate__lte=models.F('endDate')),
                name='school_session_start_before_end',
            ),
        ]
        indexes = [
            models.Index(fields=['schoolID', 'isDeleted', 'isCurrent'], name='ss_school_cur_del_idx'),
            models.Index(fields=['schoolID', 'startDate', 'endDate'], name='ss_school_date_rng_idx'),
        ]


class WebPushSubscription(models.Model):
    APP_NAME_CHOICES = (
        ('studentapp', 'Student App'),
        ('teacherapp', 'Teacher App'),
        ('managementapp', 'Management App'),
    )

    schoolID = models.ForeignKey(SchoolDetail, on_delete=models.CASCADE)
    userID = models.ForeignKey(User, blank=True, null=True, on_delete=models.SET_NULL)
    appName = models.CharField(max_length=30, choices=APP_NAME_CHOICES)
    endpoint = models.TextField()
    endpointHash = models.CharField(max_length=64, db_index=True)
    authKey = models.TextField()
    p256dhKey = models.TextField()
    isActive = models.BooleanField(default=True)
    datetime = models.DateTimeField(auto_now_add=True, auto_now=False)
    lastUpdatedOn = models.DateTimeField(auto_now_add=False, auto_now=True)

    class Meta:
        verbose_name_plural = 'e) Web Push Subscriptions.'
        indexes = [
            models.Index(fields=['schoolID', 'appName', 'isActive'], name='wps_school_app_active_idx'),
            models.Index(fields=['userID', 'isActive'], name='wps_user_active_idx'),
        ]
