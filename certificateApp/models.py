from django.contrib.auth.models import User
from django.db import models
from stdimage import StdImageField

from homeApp.models import SchoolDetail, SchoolSession
from managementApp.models import Parent, Student, TeacherDetail
from utils.utils import UPLOAD_TO_PATTERNS


class CertificateType(models.Model):
    RECIPIENT_CATEGORY_CHOICES = (
        ('student', 'Student'),
        ('teacher', 'Teacher'),
        ('staff', 'Staff'),
        ('parent', 'Parent'),
        ('school', 'School'),
    )

    schoolID = models.ForeignKey(SchoolDetail, blank=True, null=True, on_delete=models.CASCADE)
    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=140)
    recipientCategory = models.CharField(max_length=20, choices=RECIPIENT_CATEGORY_CHOICES, default='student')
    description = models.TextField(blank=True, null=True)
    defaultTitle = models.CharField(max_length=200, blank=True, null=True)
    defaultSubtitle = models.CharField(max_length=255, blank=True, null=True)
    defaultBodyTemplate = models.TextField(blank=True, null=True)
    defaultFooterText = models.TextField(blank=True, null=True)
    isActive = models.BooleanField(default=True)
    isSystem = models.BooleanField(default=False)
    datetime = models.DateTimeField(auto_now_add=True)
    lastUpdatedOn = models.DateTimeField(auto_now=True)
    lastEditedBy = models.CharField(max_length=500, blank=True, null=True)
    updatedByUserID = models.ForeignKey(User, blank=True, null=True, on_delete=models.SET_NULL, related_name='+')
    isDeleted = models.BooleanField(default=False)

    class Meta:
        verbose_name_plural = 'Certificate Types'
        indexes = [
            models.Index(fields=['schoolID', 'slug', 'isDeleted'], name='cert_type_school_slug_idx'),
            models.Index(fields=['recipientCategory', 'isActive'], name='cert_type_rec_active_idx'),
        ]
        constraints = [
            models.UniqueConstraint(fields=['schoolID', 'slug'], name='cert_type_school_slug_uniq'),
        ]

    def __str__(self):
        return self.name


class CertificateDesign(models.Model):
    DESIGN_MODE_CHOICES = (
        ('html', 'Structured HTML'),
        ('image_overlay', 'Image Overlay'),
    )
    PAGE_SIZE_CHOICES = (
        ('A4', 'A4'),
        ('A5', 'A5'),
        ('LETTER', 'Letter'),
        ('CUSTOM', 'Custom'),
    )
    ORIENTATION_CHOICES = (
        ('portrait', 'Portrait'),
        ('landscape', 'Landscape'),
    )
    ALIGNMENT_CHOICES = (
        ('left', 'Left'),
        ('center', 'Center'),
        ('right', 'Right'),
    )
    BORDER_STYLE_CHOICES = (
        ('none', 'None'),
        ('single', 'Single Border'),
        ('double', 'Double Border'),
        ('ornate', 'Ornate'),
        ('ribbon', 'Ribbon'),
    )
    TEMPLATE_KEY_CHOICES = (
        ('classic_formal', 'Classic Formal'),
        ('modern_clean', 'Modern Clean'),
        ('ceremonial_gold', 'Ceremonial Gold'),
        ('academic_seal', 'Academic Seal'),
        ('heritage_script', 'Heritage Script'),
        ('minimal_duotone', 'Minimal Duotone'),
        ('hand_fill_form', 'Hand-Fill Form'),
        ('prize_day_form', 'Prize Day Form'),
        ('custom', 'Custom'),
    )

    certificateTypeID = models.ForeignKey(CertificateType, on_delete=models.CASCADE)
    schoolID = models.ForeignKey(SchoolDetail, blank=True, null=True, on_delete=models.CASCADE)
    name = models.CharField(max_length=140)
    slug = models.SlugField(max_length=160)
    designMode = models.CharField(max_length=24, choices=DESIGN_MODE_CHOICES, default='html')
    templateKey = models.CharField(max_length=40, choices=TEMPLATE_KEY_CHOICES, default='classic_formal')
    pageSize = models.CharField(max_length=20, choices=PAGE_SIZE_CHOICES, default='A4')
    orientation = models.CharField(max_length=20, choices=ORIENTATION_CHOICES, default='portrait')
    pageWidthMm = models.DecimalField(max_digits=7, decimal_places=2, blank=True, null=True)
    pageHeightMm = models.DecimalField(max_digits=7, decimal_places=2, blank=True, null=True)
    marginTopMm = models.DecimalField(max_digits=6, decimal_places=2, default=12)
    marginRightMm = models.DecimalField(max_digits=6, decimal_places=2, default=12)
    marginBottomMm = models.DecimalField(max_digits=6, decimal_places=2, default=12)
    marginLeftMm = models.DecimalField(max_digits=6, decimal_places=2, default=12)
    titleAlignment = models.CharField(max_length=20, choices=ALIGNMENT_CHOICES, default='center')
    bodyAlignment = models.CharField(max_length=20, choices=ALIGNMENT_CHOICES, default='center')
    borderStyle = models.CharField(max_length=20, choices=BORDER_STYLE_CHOICES, default='single')
    fontFamily = models.CharField(max_length=120, blank=True, null=True, default='Georgia')
    accentColor = models.CharField(max_length=20, blank=True, null=True, default='#1d4ed8')
    textColor = models.CharField(max_length=20, blank=True, null=True, default='#1f2937')
    backgroundColor = models.CharField(max_length=20, blank=True, null=True, default='#ffffff')
    backgroundImage = StdImageField(
        upload_to=UPLOAD_TO_PATTERNS,
        variations={'thumbnail': (160, 160, True), 'medium': (640, 640)},
        delete_orphans=True,
        blank=True,
        null=True,
    )
    customHeaderText = models.CharField(max_length=255, blank=True, null=True)
    customFooterText = models.CharField(max_length=500, blank=True, null=True)
    customCss = models.TextField(blank=True, null=True)
    overlaySchema = models.JSONField(default=list, blank=True)
    showLogo = models.BooleanField(default=True)
    showSignatureLine = models.BooleanField(default=True)
    showSeal = models.BooleanField(default=True)
    isActive = models.BooleanField(default=True)
    isSystem = models.BooleanField(default=False)
    isCustom = models.BooleanField(default=False)
    datetime = models.DateTimeField(auto_now_add=True)
    lastUpdatedOn = models.DateTimeField(auto_now=True)
    lastEditedBy = models.CharField(max_length=500, blank=True, null=True)
    updatedByUserID = models.ForeignKey(User, blank=True, null=True, on_delete=models.SET_NULL, related_name='+')
    isDeleted = models.BooleanField(default=False)

    class Meta:
        verbose_name_plural = 'Certificate Designs'
        indexes = [
            models.Index(fields=['schoolID', 'certificateTypeID', 'isDeleted'], name='cert_design_school_type_idx'),
            models.Index(fields=['isActive', 'isCustom'], name='cert_design_active_custom_idx'),
        ]
        constraints = [
            models.UniqueConstraint(fields=['certificateTypeID', 'schoolID', 'slug'], name='cert_design_type_school_slug_uniq'),
        ]

    def __str__(self):
        return f'{self.certificateTypeID.name} - {self.name}'


class CertificateIssue(models.Model):
    RECIPIENT_CATEGORY_CHOICES = CertificateType.RECIPIENT_CATEGORY_CHOICES

    certificateTypeID = models.ForeignKey(CertificateType, on_delete=models.CASCADE)
    certificateDesignID = models.ForeignKey(CertificateDesign, blank=True, null=True, on_delete=models.SET_NULL)
    schoolID = models.ForeignKey(SchoolDetail, blank=True, null=True, on_delete=models.CASCADE)
    sessionID = models.ForeignKey(SchoolSession, blank=True, null=True, on_delete=models.CASCADE)
    recipientCategory = models.CharField(max_length=20, choices=RECIPIENT_CATEGORY_CHOICES, default='student')
    studentID = models.ForeignKey(Student, blank=True, null=True, on_delete=models.SET_NULL)
    teacherID = models.ForeignKey(TeacherDetail, blank=True, null=True, on_delete=models.SET_NULL)
    parentID = models.ForeignKey(Parent, blank=True, null=True, on_delete=models.SET_NULL)
    issueDate = models.DateField()
    certificateNumber = models.CharField(max_length=120, db_index=True)
    customTitle = models.CharField(max_length=200, blank=True, null=True)
    customSubtitle = models.CharField(max_length=255, blank=True, null=True)
    customBodyText = models.TextField(blank=True, null=True)
    customFooterText = models.TextField(blank=True, null=True)
    contextSnapshot = models.JSONField(default=dict, blank=True)
    htmlSnapshot = models.TextField(blank=True, null=True)
    datetime = models.DateTimeField(auto_now_add=True)
    lastUpdatedOn = models.DateTimeField(auto_now=True)
    lastEditedBy = models.CharField(max_length=500, blank=True, null=True)
    updatedByUserID = models.ForeignKey(User, blank=True, null=True, on_delete=models.SET_NULL, related_name='+')
    isDeleted = models.BooleanField(default=False)

    class Meta:
        verbose_name_plural = 'Certificate Issues'
        indexes = [
            models.Index(fields=['schoolID', 'sessionID', 'recipientCategory'], name='cert_issue_scope_idx'),
            models.Index(fields=['issueDate', 'isDeleted'], name='cert_issue_date_del_idx'),
        ]

    def __str__(self):
        return self.certificateNumber
