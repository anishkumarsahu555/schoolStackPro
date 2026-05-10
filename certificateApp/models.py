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
        ('hairline', 'Hairline'),
        ('thick', 'Thick Frame'),
        ('inner_line', 'Inner Line'),
        ('triple', 'Triple Frame'),
        ('corner_marks', 'Corner Marks'),
        ('corner_flourish', 'Corner Flourish'),
        ('side_bars', 'Side Bars'),
        ('top_bottom', 'Top & Bottom'),
        ('dotted', 'Dotted'),
        ('dashed', 'Dashed'),
        ('ledger', 'Ledger Rule'),
        ('laurel', 'Laurel'),
        ('plaque', 'Plaque'),
        ('modern_frame', 'Modern Frame'),
        ('classic_frame', 'Classic Frame'),
    )
    TEMPLATE_KEY_CHOICES = (
        ('classic_formal', 'Classic Formal'),
        ('modern_clean', 'Modern Clean'),
        ('ceremonial_gold', 'Ceremonial Gold'),
        ('academic_seal', 'Academic Seal'),
        ('heritage_script', 'Heritage Script'),
        ('minimal_duotone', 'Minimal Duotone'),
        ('split_panel', 'Split Panel'),
        ('ledger_grid', 'Ledger Grid'),
        ('laurel_frame', 'Laurel Frame'),
        ('ribbon_banner', 'Ribbon Banner'),
        ('royal_arc', 'Royal Arc'),
        ('editorial_grid', 'Editorial Grid'),
        ('crest_band', 'Crest Band'),
        ('award_plaque', 'Award Plaque'),
        ('navy_gold_shield', 'Navy Gold Shield'),
        ('black_gold_sweep', 'Black Gold Sweep'),
        ('cobalt_corner_lines', 'Cobalt Corner Lines'),
        ('ivory_gold_arch', 'Ivory Gold Arch'),
        ('sapphire_wave_frame', 'Sapphire Wave Frame'),
        ('emerald_gold_ribbon', 'Emerald Gold Ribbon'),
        ('maroon_gold_gate', 'Maroon Gold Gate'),
        ('charcoal_orbit_frame', 'Charcoal Orbit Frame'),
        ('royal_blue_plaque', 'Royal Blue Plaque'),
        ('pearl_gold_corners', 'Pearl Gold Corners'),
        ('slate_gold_diagonal', 'Slate Gold Diagonal'),
        ('indigo_bottom_flourish', 'Indigo Bottom Flourish'),
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
    assetConfig = models.JSONField(default=dict, blank=True)
    layoutConfig = models.JSONField(default=dict, blank=True)
    themeConfig = models.JSONField(default=dict, blank=True)
    designSchema = models.JSONField(default=dict, blank=True)
    designJson = models.JSONField(default=dict, blank=True)
    mergeSchema = models.JSONField(default=list, blank=True)
    designVersion = models.PositiveIntegerField(default=1)
    basedOnDesignID = models.ForeignKey('self', blank=True, null=True, on_delete=models.SET_NULL, related_name='derivedDesigns')
    customHeaderText = models.CharField(max_length=255, blank=True, null=True)
    customFooterText = models.CharField(max_length=500, blank=True, null=True)
    customCss = models.TextField(blank=True, null=True)
    overlaySchema = models.JSONField(default=list, blank=True)
    showLogo = models.BooleanField(default=True)
    showSignatureLine = models.BooleanField(default=True)
    showSeal = models.BooleanField(default=True)
    isDraft = models.BooleanField(default=False)
    isDefaultForType = models.BooleanField(default=False)
    isActive = models.BooleanField(default=True)
    isSystem = models.BooleanField(default=False)
    isCustom = models.BooleanField(default=False)
    status = models.CharField(
        max_length=20,
        choices=(('draft', 'Draft'), ('published', 'Published'), ('archived', 'Archived')),
        default='published',
    )
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
            models.Index(fields=['schoolID', 'status', 'isDeleted'], name='cert_design_school_status_idx'),
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
    verificationToken = models.CharField(max_length=64, unique=True, db_index=True, blank=True, null=True)
    customTitle = models.CharField(max_length=200, blank=True, null=True)
    customSubtitle = models.CharField(max_length=255, blank=True, null=True)
    customBodyText = models.TextField(blank=True, null=True)
    customFooterText = models.TextField(blank=True, null=True)
    draftName = models.CharField(max_length=200, blank=True, null=True)
    issuePayload = models.JSONField(default=dict, blank=True)
    issueData = models.JSONField(default=dict, blank=True)
    issueStatus = models.CharField(
        max_length=20,
        choices=(('draft', 'Draft'), ('issued', 'Issued'), ('cancelled', 'Cancelled'), ('reissued', 'Reissued')),
        default='issued',
    )
    cancelledOn = models.DateTimeField(blank=True, null=True)
    cancelledByUserID = models.ForeignKey(User, blank=True, null=True, on_delete=models.SET_NULL, related_name='+')
    cancellationReason = models.TextField(blank=True, null=True)
    reissuedFromIssueID = models.ForeignKey('self', blank=True, null=True, on_delete=models.SET_NULL, related_name='reissuedIssues')
    designSnapshot = models.JSONField(default=dict, blank=True)
    renderSnapshot = models.JSONField(default=dict, blank=True)
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
            models.Index(fields=['schoolID', 'issueStatus', 'issueDate'], name='cert_issue_school_status_idx'),
        ]
        constraints = [
            models.UniqueConstraint(fields=['schoolID', 'certificateNumber'], name='cert_issue_school_number_uniq'),
        ]

    def __str__(self):
        return self.certificateNumber


class CertificateSequence(models.Model):
    schoolID = models.ForeignKey(SchoolDetail, blank=True, null=True, on_delete=models.CASCADE)
    sessionID = models.ForeignKey(SchoolSession, blank=True, null=True, on_delete=models.CASCADE)
    certificateTypeID = models.ForeignKey(CertificateType, blank=True, null=True, on_delete=models.CASCADE)
    prefix = models.CharField(max_length=80)
    currentValue = models.PositiveIntegerField(default=0)
    datetime = models.DateTimeField(auto_now_add=True)
    lastUpdatedOn = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = 'Certificate Sequences'
        indexes = [
            models.Index(fields=['schoolID', 'sessionID'], name='cert_seq_school_session_idx'),
        ]
        constraints = [
            models.UniqueConstraint(fields=['schoolID', 'sessionID', 'certificateTypeID', 'prefix'], name='cert_seq_scope_prefix_uniq'),
        ]

    def __str__(self):
        return f'{self.prefix}-{self.currentValue:04d}'
