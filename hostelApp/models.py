from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from financeApp.models import StudentCharge
from homeApp.model_mixins import SchoolScopedModel
from managementApp.models import Student, TeacherDetail


class HostelAuditModel(SchoolScopedModel):
    lastEditedBy = models.CharField(max_length=500, blank=True, null=True)
    updatedByUserID = models.ForeignKey(User, blank=True, null=True, on_delete=models.SET_NULL, related_name='+')

    class Meta:
        abstract = True


class HostelBuilding(HostelAuditModel):
    buildingCode = models.CharField(max_length=50)
    buildingName = models.CharField(max_length=200)
    wardenName = models.CharField(max_length=200, blank=True, null=True)
    wardenPhone = models.CharField(max_length=30, blank=True, null=True)
    address = models.TextField(blank=True, null=True)
    isActive = models.BooleanField(default=True)

    def __str__(self):
        return f'{self.buildingCode} - {self.buildingName}'

    class Meta:
        verbose_name_plural = 'a) Hostel Buildings'
        indexes = [
            models.Index(fields=['schoolID', 'sessionID', 'isDeleted', 'isActive'], name='hostel_build_scope_idx'),
            models.Index(fields=['schoolID', 'buildingCode', 'isDeleted'], name='hostel_build_code_idx'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['schoolID', 'sessionID', 'buildingCode'],
                condition=Q(isDeleted=False),
                name='hostel_building_unique_active_code',
            ),
        ]


class HostelFloor(HostelAuditModel):
    buildingID = models.ForeignKey(HostelBuilding, on_delete=models.CASCADE, related_name='floors')
    floorName = models.CharField(max_length=120)
    displayOrder = models.PositiveIntegerField(default=0)
    isActive = models.BooleanField(default=True)

    def __str__(self):
        return f'{self.buildingID.buildingName} - {self.floorName}'

    class Meta:
        verbose_name_plural = 'b) Hostel Floors'
        indexes = [
            models.Index(fields=['buildingID', 'isDeleted', 'displayOrder'], name='hostel_floor_order_idx'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['buildingID', 'floorName'],
                condition=Q(isDeleted=False),
                name='hostel_floor_unique_building_name',
            ),
        ]


class HostelRoomType(HostelAuditModel):
    name = models.CharField(max_length=160)
    capacity = models.PositiveIntegerField(default=1)
    defaultMonthlyFee = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    description = models.TextField(blank=True, null=True)
    isActive = models.BooleanField(default=True)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name_plural = 'c) Hostel Room Types'
        indexes = [
            models.Index(fields=['schoolID', 'sessionID', 'isDeleted', 'isActive'], name='hostel_rtype_scope_idx'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['schoolID', 'sessionID', 'name'],
                condition=Q(isDeleted=False),
                name='hostel_room_type_unique_name',
            ),
        ]


class HostelRoom(HostelAuditModel):
    buildingID = models.ForeignKey(HostelBuilding, on_delete=models.PROTECT, related_name='rooms')
    floorID = models.ForeignKey(HostelFloor, blank=True, null=True, on_delete=models.SET_NULL, related_name='rooms')
    roomTypeID = models.ForeignKey(HostelRoomType, blank=True, null=True, on_delete=models.SET_NULL, related_name='rooms')
    roomNumber = models.CharField(max_length=80)
    capacity = models.PositiveIntegerField(default=1)
    monthlyFee = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    notes = models.TextField(blank=True, null=True)
    isActive = models.BooleanField(default=True)

    def clean(self):
        if self.floorID_id and self.floorID.buildingID_id != self.buildingID_id:
            raise ValidationError('Selected floor must belong to the selected hostel building.')

    def __str__(self):
        return f'{self.buildingID.buildingName} - {self.roomNumber}'

    class Meta:
        verbose_name_plural = 'd) Hostel Rooms'
        indexes = [
            models.Index(fields=['schoolID', 'sessionID', 'buildingID', 'isDeleted', 'isActive'], name='hostel_room_scope_idx'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['buildingID', 'roomNumber'],
                condition=Q(isDeleted=False),
                name='hostel_room_unique_building_number',
            ),
        ]


class HostelBed(HostelAuditModel):
    STATUS_CHOICES = (
        ('available', 'Available'),
        ('occupied', 'Occupied'),
        ('reserved', 'Reserved'),
        ('maintenance', 'Maintenance'),
    )

    roomID = models.ForeignKey(HostelRoom, on_delete=models.CASCADE, related_name='beds')
    bedNumber = models.CharField(max_length=80)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='available')
    isActive = models.BooleanField(default=True)

    def __str__(self):
        return f'{self.roomID} - {self.bedNumber}'

    class Meta:
        verbose_name_plural = 'e) Hostel Beds'
        indexes = [
            models.Index(fields=['roomID', 'status', 'isDeleted', 'isActive'], name='hostel_bed_room_status_idx'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['roomID', 'bedNumber'],
                condition=Q(isDeleted=False),
                name='hostel_bed_unique_room_number',
            ),
        ]


class HostelAdmission(HostelAuditModel):
    RESIDENT_TYPE_CHOICES = (
        ('student', 'Student'),
        ('teacher', 'Teacher'),
    )
    STATUS_CHOICES = (
        ('applied', 'Applied'),
        ('approved', 'Approved'),
        ('waitlisted', 'Waitlisted'),
        ('rejected', 'Rejected'),
        ('admitted', 'Admitted'),
        ('cancelled', 'Cancelled'),
    )

    residentType = models.CharField(max_length=20, choices=RESIDENT_TYPE_CHOICES, default='student')
    studentID = models.ForeignKey(Student, blank=True, null=True, on_delete=models.PROTECT, related_name='hostelAdmissions')
    teacherID = models.ForeignKey(TeacherDetail, blank=True, null=True, on_delete=models.PROTECT, related_name='hostelAdmissions')
    applicationNo = models.CharField(max_length=100)
    applicationDate = models.DateField()
    preferredRoomTypeID = models.ForeignKey(HostelRoomType, blank=True, null=True, on_delete=models.SET_NULL, related_name='admissions')
    guardianConsent = models.BooleanField(default=False)
    emergencyContactName = models.CharField(max_length=200, blank=True, null=True)
    emergencyContactPhone = models.CharField(max_length=30, blank=True, null=True)
    medicalNotes = models.TextField(blank=True, null=True)
    admissionFee = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='applied')
    approvedDate = models.DateField(blank=True, null=True)
    admissionDate = models.DateField(blank=True, null=True)
    notes = models.TextField(blank=True, null=True)
    isActive = models.BooleanField(default=True)

    def clean(self):
        if self.residentType == 'student' and not self.studentID_id:
            raise ValidationError('Student admission requires a student.')
        if self.residentType == 'teacher' and not self.teacherID_id:
            raise ValidationError('Teacher admission requires a teacher.')
        if self.studentID_id and self.teacherID_id:
            raise ValidationError('Hostel admission can link to only one resident.')
        if self.status in {'approved', 'admitted'} and not self.guardianConsent:
            raise ValidationError('Consent is required before approving hostel admission.')

    @property
    def resident_name(self):
        if self.residentType == 'student' and self.studentID_id:
            return self.studentID.name
        if self.residentType == 'teacher' and self.teacherID_id:
            return self.teacherID.name
        return 'N/A'

    def __str__(self):
        return f'{self.applicationNo} - {self.resident_name}'

    class Meta:
        verbose_name_plural = 'f) Hostel Admissions'
        indexes = [
            models.Index(fields=['schoolID', 'sessionID', 'status', 'isDeleted', 'isActive'], name='hostel_adm_status_idx'),
            models.Index(fields=['studentID', 'sessionID', 'isDeleted'], name='hostel_adm_student_idx'),
            models.Index(fields=['teacherID', 'sessionID', 'isDeleted'], name='hostel_adm_teacher_idx'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['schoolID', 'sessionID', 'applicationNo'],
                condition=Q(isDeleted=False),
                name='hostel_admission_unique_application_no',
            ),
            models.UniqueConstraint(
                fields=['studentID', 'sessionID'],
                condition=Q(isDeleted=False, isActive=True, residentType='student', status__in=['applied', 'approved', 'waitlisted', 'admitted'], studentID__isnull=False),
                name='hostel_unique_active_student_admission',
            ),
            models.UniqueConstraint(
                fields=['teacherID', 'sessionID'],
                condition=Q(isDeleted=False, isActive=True, residentType='teacher', status__in=['applied', 'approved', 'waitlisted', 'admitted'], teacherID__isnull=False),
                name='hostel_unique_active_teacher_admission',
            ),
        ]


class HostelFeeMapping(HostelAuditModel):
    buildingID = models.ForeignKey(HostelBuilding, blank=True, null=True, on_delete=models.CASCADE, related_name='feeMappings')
    roomTypeID = models.ForeignKey(HostelRoomType, blank=True, null=True, on_delete=models.CASCADE, related_name='feeMappings')
    roomID = models.ForeignKey(HostelRoom, blank=True, null=True, on_delete=models.CASCADE, related_name='feeMappings')
    monthlyFee = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    effectiveFrom = models.DateField(blank=True, null=True)
    effectiveTo = models.DateField(blank=True, null=True)
    notes = models.TextField(blank=True, null=True)
    isActive = models.BooleanField(default=True)

    def clean(self):
        if self.roomID_id and self.buildingID_id and self.roomID.buildingID_id != self.buildingID_id:
            raise ValidationError('Fee mapping room must belong to selected building.')
        if self.roomID_id and self.roomTypeID_id and self.roomID.roomTypeID_id != self.roomTypeID_id:
            raise ValidationError('Fee mapping room must match selected room type.')

    def __str__(self):
        label = self.roomID or self.roomTypeID or self.buildingID or 'Hostel default'
        return f'{label} - {self.monthlyFee}'

    class Meta:
        verbose_name_plural = 'g) Hostel Fee Mappings'
        indexes = [
            models.Index(fields=['schoolID', 'sessionID', 'isDeleted', 'isActive'], name='hostel_fee_map_scope_idx'),
        ]


class HostelAssignment(HostelAuditModel):
    RESIDENT_TYPE_CHOICES = (
        ('student', 'Student'),
        ('teacher', 'Teacher'),
    )
    FEE_MODE_CHOICES = (
        ('student_fee', 'Student Fee'),
        ('payroll_deduction', 'Payroll Deduction'),
        ('staff_receivable', 'Staff Receivable'),
        ('free', 'Free'),
        ('informational', 'Informational Only'),
    )

    admissionID = models.ForeignKey(HostelAdmission, blank=True, null=True, on_delete=models.PROTECT, related_name='assignments')
    residentType = models.CharField(max_length=20, choices=RESIDENT_TYPE_CHOICES, default='student')
    studentID = models.ForeignKey(Student, blank=True, null=True, on_delete=models.PROTECT, related_name='hostelAssignments')
    teacherID = models.ForeignKey(TeacherDetail, blank=True, null=True, on_delete=models.PROTECT, related_name='hostelAssignments')
    buildingID = models.ForeignKey(HostelBuilding, on_delete=models.PROTECT, related_name='assignments')
    roomID = models.ForeignKey(HostelRoom, on_delete=models.PROTECT, related_name='assignments')
    bedID = models.ForeignKey(HostelBed, on_delete=models.PROTECT, related_name='assignments')
    feeMode = models.CharField(max_length=30, choices=FEE_MODE_CHOICES, default='student_fee')
    monthlyFee = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    startDate = models.DateField(blank=True, null=True)
    endDate = models.DateField(blank=True, null=True)
    isActive = models.BooleanField(default=True)

    def clean(self):
        if self.residentType == 'student' and not self.studentID_id:
            raise ValidationError('Student assignment requires a student.')
        if self.residentType == 'teacher' and not self.teacherID_id:
            raise ValidationError('Teacher assignment requires a teacher.')
        if self.studentID_id and self.teacherID_id:
            raise ValidationError('Hostel assignment can link to only one resident.')
        if self.residentType == 'student' and self.feeMode in {'payroll_deduction', 'staff_receivable'}:
            raise ValidationError('Student hostel assignment cannot use staff fee mode.')
        if self.residentType == 'teacher' and self.feeMode == 'student_fee':
            raise ValidationError('Teacher hostel assignment cannot use student fee mode.')
        if self.roomID_id and self.roomID.buildingID_id != self.buildingID_id:
            raise ValidationError('Selected room must belong to selected building.')
        if self.bedID_id and self.bedID.roomID_id != self.roomID_id:
            raise ValidationError('Selected bed must belong to selected room.')
        if self.admissionID_id:
            if self.admissionID.residentType != self.residentType:
                raise ValidationError('Admission resident type must match assignment resident type.')
            if self.residentType == 'student' and self.admissionID.studentID_id != self.studentID_id:
                raise ValidationError('Admission student must match assigned student.')
            if self.residentType == 'teacher' and self.admissionID.teacherID_id != self.teacherID_id:
                raise ValidationError('Admission teacher must match assigned teacher.')
            if self.admissionID.status in {'rejected', 'cancelled'}:
                raise ValidationError('Rejected or cancelled hostel applications cannot be allocated a bed.')

    @property
    def resident_name(self):
        if self.residentType == 'student' and self.studentID_id:
            return self.studentID.name
        if self.residentType == 'teacher' and self.teacherID_id:
            return self.teacherID.name
        return 'N/A'

    @property
    def student_name(self):
        return self.resident_name

    def __str__(self):
        return f'{self.resident_name} - {self.bedID}'

    class Meta:
        verbose_name_plural = 'h) Hostel Assignments'
        indexes = [
            models.Index(fields=['schoolID', 'sessionID', 'isDeleted', 'isActive'], name='hostel_assign_scope_idx'),
            models.Index(fields=['studentID', 'sessionID', 'isDeleted', 'isActive'], name='hostel_assign_student_idx'),
            models.Index(fields=['teacherID', 'sessionID', 'isDeleted', 'isActive'], name='hostel_assign_teacher_idx'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['studentID', 'sessionID'],
                condition=Q(isDeleted=False, isActive=True, residentType='student', studentID__isnull=False),
                name='hostel_unique_active_student_assignment',
            ),
            models.UniqueConstraint(
                fields=['teacherID', 'sessionID'],
                condition=Q(isDeleted=False, isActive=True, residentType='teacher', teacherID__isnull=False),
                name='hostel_unique_active_teacher_assignment',
            ),
            models.UniqueConstraint(
                fields=['bedID', 'sessionID'],
                condition=Q(isDeleted=False, isActive=True),
                name='hostel_unique_active_bed_assignment',
            ),
        ]


class HostelFeeRecord(HostelAuditModel):
    STATUS_CHOICES = (
        ('pending', 'Pending'),
        ('partial', 'Partial'),
        ('paid', 'Paid'),
        ('waived', 'Waived'),
        ('cancelled', 'Cancelled'),
    )

    assignmentID = models.ForeignKey(HostelAssignment, on_delete=models.PROTECT, related_name='feeRecords')
    financeChargeID = models.ForeignKey(StudentCharge, blank=True, null=True, on_delete=models.SET_NULL, related_name='hostelFeeRecords')
    feeMonth = models.PositiveSmallIntegerField()
    feeYear = models.PositiveIntegerField()
    periodStartDate = models.DateField()
    periodEndDate = models.DateField()
    dueDate = models.DateField(blank=True, null=True)
    grossAmount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    discountAmount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    fineAmount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    netAmount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    paidAmount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    balanceAmount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    paymentDate = models.DateField(blank=True, null=True)
    paymentMode = models.CharField(max_length=80, blank=True, null=True)
    referenceNo = models.CharField(max_length=120, blank=True, null=True)
    notes = models.TextField(blank=True, null=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')

    def recalculate(self):
        self.netAmount = (self.grossAmount or Decimal('0.00')) - (self.discountAmount or Decimal('0.00')) + (self.fineAmount or Decimal('0.00'))
        if self.status in {'waived', 'cancelled'}:
            self.balanceAmount = Decimal('0.00')
            if self.status == 'waived':
                self.paidAmount = Decimal('0.00')
            return
        self.balanceAmount = max((self.netAmount or Decimal('0.00')) - (self.paidAmount or Decimal('0.00')), Decimal('0.00'))
        if self.netAmount <= 0:
            self.status = 'waived'
        elif self.paidAmount >= self.netAmount:
            self.status = 'paid'
        elif self.paidAmount > 0:
            self.status = 'partial'
        else:
            self.status = 'pending'

    def clean(self):
        values = [self.grossAmount, self.discountAmount, self.fineAmount, self.netAmount, self.paidAmount, self.balanceAmount]
        if any(value < 0 for value in values):
            raise ValidationError('Hostel fee amounts cannot be negative.')
        if not 1 <= int(self.feeMonth or 0) <= 12:
            raise ValidationError('Fee month must be between 1 and 12.')
        if self.status not in {'waived', 'cancelled'} and self.paidAmount > self.netAmount:
            raise ValidationError('Paid amount cannot exceed net hostel fee.')

    def save(self, *args, **kwargs):
        self.recalculate()
        super().save(*args, **kwargs)

    @property
    def student_name(self):
        return self.resident_name

    @property
    def resident_name(self):
        return self.assignmentID.resident_name if self.assignmentID_id else 'N/A'

    def __str__(self):
        return f'{self.resident_name} - {self.feeMonth}/{self.feeYear}'

    class Meta:
        verbose_name_plural = 'i) Hostel Fee Records'
        indexes = [
            models.Index(fields=['schoolID', 'sessionID', 'feeYear', 'feeMonth', 'status'], name='hostel_fee_period_idx'),
            models.Index(fields=['assignmentID', 'feeYear', 'feeMonth', 'isDeleted'], name='hostel_fee_assign_idx'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['assignmentID', 'feeYear', 'feeMonth'],
                condition=Q(isDeleted=False),
                name='hostel_unique_fee_record_assignment_period',
            ),
        ]
