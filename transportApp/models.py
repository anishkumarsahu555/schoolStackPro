from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from stdimage import StdImageField

from homeApp.model_mixins import SchoolScopedModel
from financeApp.models import StudentCharge
from managementApp.models import Student, TeacherDetail
from utils.utils import UPLOAD_TO_PATTERNS


class TransportAuditModel(SchoolScopedModel):
    lastEditedBy = models.CharField(max_length=500, blank=True, null=True)
    updatedByUserID = models.ForeignKey(User, blank=True, null=True, on_delete=models.SET_NULL, related_name='+')

    class Meta:
        abstract = True


class TransportRoute(TransportAuditModel):
    routeCode = models.CharField(max_length=50)
    routeName = models.CharField(max_length=200)
    startPoint = models.CharField(max_length=250, blank=True, null=True)
    endPoint = models.CharField(max_length=250, blank=True, null=True)
    description = models.TextField(blank=True, null=True, default='')
    isActive = models.BooleanField(default=True)

    def __str__(self):
        return f'{self.routeCode} - {self.routeName}'

    class Meta:
        verbose_name_plural = 'a) Transport Routes'
        indexes = [
            models.Index(fields=['schoolID', 'sessionID', 'isDeleted', 'isActive'], name='tr_route_scope_idx'),
            models.Index(fields=['schoolID', 'routeCode', 'isDeleted'], name='tr_route_code_idx'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['schoolID', 'sessionID', 'routeCode'],
                condition=Q(isDeleted=False),
                name='transport_route_unique_active_code',
            ),
        ]


class TransportStop(TransportAuditModel):
    routeID = models.ForeignKey(TransportRoute, on_delete=models.CASCADE, related_name='stops')
    stopName = models.CharField(max_length=200)
    pickupTime = models.TimeField(blank=True, null=True)
    dropTime = models.TimeField(blank=True, null=True)
    monthlyFee = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    displayOrder = models.PositiveIntegerField(default=0)
    isActive = models.BooleanField(default=True)

    def __str__(self):
        return f'{self.routeID.routeName} - {self.stopName}'

    class Meta:
        verbose_name_plural = 'b) Transport Stops'
        indexes = [
            models.Index(fields=['routeID', 'isDeleted', 'displayOrder'], name='ts_route_order_idx'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['routeID', 'stopName'],
                condition=Q(isDeleted=False),
                name='transport_stop_unique_route_name',
            ),
        ]


class TransportDriver(TransportAuditModel):
    name = models.CharField(max_length=200)
    photo = StdImageField(
        upload_to=UPLOAD_TO_PATTERNS,
        variations={
            'thumbnail': (100, 100, True),
            'medium': (250, 250),
        },
        delete_orphans=True,
        blank=True,
    )
    phoneNumber = models.CharField(max_length=30, blank=True, null=True)
    licenseNumber = models.CharField(max_length=100, blank=True, null=True)
    licenseExpiryDate = models.DateField(blank=True, null=True)
    address = models.TextField(blank=True, null=True)
    isActive = models.BooleanField(default=True)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name_plural = 'c) Transport Drivers'
        indexes = [
            models.Index(fields=['schoolID', 'sessionID', 'isDeleted', 'isActive'], name='tdriver_scope_idx'),
        ]


class TransportFeeMapping(TransportAuditModel):
    ASSIGNEE_TYPE_CHOICES = (
        ('student', 'Student'),
        ('teacher', 'Teacher'),
        ('both', 'Student & Staff'),
    )
    FEE_MODE_CHOICES = (
        ('student_fee', 'Student Fee'),
        ('free', 'Free'),
        ('payroll_deduction', 'Payroll Deduction'),
        ('staff_receivable', 'Staff Receivable'),
        ('informational', 'Informational Only'),
    )

    routeID = models.ForeignKey(TransportRoute, on_delete=models.CASCADE, related_name='feeMappings')
    stopID = models.ForeignKey(TransportStop, blank=True, null=True, on_delete=models.CASCADE, related_name='feeMappings')
    assigneeType = models.CharField(max_length=20, choices=ASSIGNEE_TYPE_CHOICES, default='student')
    feeMode = models.CharField(max_length=30, choices=FEE_MODE_CHOICES, default='student_fee')
    monthlyFee = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    effectiveFrom = models.DateField(blank=True, null=True)
    effectiveTo = models.DateField(blank=True, null=True)
    notes = models.TextField(blank=True, null=True)
    isActive = models.BooleanField(default=True)

    def clean(self):
        if self.stopID_id and self.stopID.routeID_id != self.routeID_id:
            raise ValidationError('Fee mapping stop must belong to the selected route.')

    def __str__(self):
        stop = self.stopID.stopName if self.stopID else 'Route default'
        return f'{self.routeID.routeName} - {stop} - {self.monthlyFee}'

    class Meta:
        verbose_name_plural = 'f) Transport Fee Mappings'
        indexes = [
            models.Index(fields=['schoolID', 'sessionID', 'routeID', 'isDeleted', 'isActive'], name='tfee_scope_idx'),
            models.Index(fields=['stopID', 'isDeleted', 'isActive'], name='tfee_stop_idx'),
        ]


class TransportVehicle(TransportAuditModel):
    VEHICLE_TYPE_CHOICES = (
        ('bus', 'Bus'),
        ('van', 'Van'),
        ('car', 'Car'),
        ('auto', 'Auto'),
        ('other', 'Other'),
    )

    vehicleNumber = models.CharField(max_length=50)
    vehicleType = models.CharField(max_length=20, choices=VEHICLE_TYPE_CHOICES, default='bus')
    capacity = models.PositiveIntegerField(default=0)
    driverID = models.ForeignKey(TransportDriver, blank=True, null=True, on_delete=models.SET_NULL, related_name='vehicles')
    routeID = models.ForeignKey(TransportRoute, blank=True, null=True, on_delete=models.SET_NULL, related_name='vehicles')
    registrationExpiryDate = models.DateField(blank=True, null=True)
    insuranceExpiryDate = models.DateField(blank=True, null=True)
    pollutionExpiryDate = models.DateField(blank=True, null=True)
    isActive = models.BooleanField(default=True)

    def __str__(self):
        return self.vehicleNumber

    class Meta:
        verbose_name_plural = 'd) Transport Vehicles'
        indexes = [
            models.Index(fields=['schoolID', 'sessionID', 'isDeleted', 'isActive'], name='tveh_scope_idx'),
            models.Index(fields=['schoolID', 'vehicleNumber', 'isDeleted'], name='tveh_number_idx'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['schoolID', 'sessionID', 'vehicleNumber'],
                condition=Q(isDeleted=False),
                name='transport_vehicle_unique_active_number',
            ),
        ]


class TransportAssignment(TransportAuditModel):
    ASSIGNEE_TYPE_CHOICES = (
        ('student', 'Student'),
        ('teacher', 'Teacher'),
    )
    TRIP_TYPE_CHOICES = (
        ('pickup', 'Pickup'),
        ('drop', 'Drop'),
        ('both', 'Pickup & Drop'),
    )
    FEE_MODE_CHOICES = (
        ('student_fee', 'Student Fee'),
        ('free', 'Free'),
        ('payroll_deduction', 'Payroll Deduction'),
        ('staff_receivable', 'Staff Receivable'),
        ('informational', 'Informational Only'),
    )

    assigneeType = models.CharField(max_length=20, choices=ASSIGNEE_TYPE_CHOICES)
    studentID = models.ForeignKey(Student, blank=True, null=True, on_delete=models.CASCADE, related_name='transportAssignments')
    teacherID = models.ForeignKey(TeacherDetail, blank=True, null=True, on_delete=models.CASCADE, related_name='transportAssignments')
    routeID = models.ForeignKey(TransportRoute, on_delete=models.PROTECT, related_name='assignments')
    pickupStopID = models.ForeignKey(TransportStop, blank=True, null=True, on_delete=models.PROTECT, related_name='pickupAssignments')
    dropStopID = models.ForeignKey(TransportStop, blank=True, null=True, on_delete=models.PROTECT, related_name='dropAssignments')
    vehicleID = models.ForeignKey(TransportVehicle, blank=True, null=True, on_delete=models.SET_NULL, related_name='assignments')
    tripType = models.CharField(max_length=20, choices=TRIP_TYPE_CHOICES, default='both')
    feeMode = models.CharField(max_length=30, choices=FEE_MODE_CHOICES, default='student_fee')
    monthlyFee = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    startDate = models.DateField(blank=True, null=True)
    endDate = models.DateField(blank=True, null=True)
    isActive = models.BooleanField(default=True)

    def clean(self):
        if self.assigneeType == 'student' and not self.studentID_id:
            raise ValidationError('Student assignment requires a student.')
        if self.assigneeType == 'teacher' and not self.teacherID_id:
            raise ValidationError('Teacher assignment requires a teacher.')
        if self.studentID_id and self.teacherID_id:
            raise ValidationError('Transport assignment can link to only one assignee.')
        if self.pickupStopID_id and self.pickupStopID.routeID_id != self.routeID_id:
            raise ValidationError('Pickup stop must belong to the selected route.')
        if self.dropStopID_id and self.dropStopID.routeID_id != self.routeID_id:
            raise ValidationError('Drop stop must belong to the selected route.')

    @property
    def assignee_name(self):
        if self.assigneeType == 'student' and self.studentID:
            return self.studentID.name
        if self.assigneeType == 'teacher' and self.teacherID:
            return self.teacherID.name
        return 'N/A'

    def __str__(self):
        return f'{self.get_assigneeType_display()} - {self.assignee_name}'

    class Meta:
        verbose_name_plural = 'e) Transport Assignments'
        indexes = [
            models.Index(fields=['schoolID', 'sessionID', 'assigneeType', 'isDeleted', 'isActive'], name='tassign_scope_idx'),
            models.Index(fields=['studentID', 'isDeleted', 'isActive'], name='tassign_student_idx'),
            models.Index(fields=['teacherID', 'isDeleted', 'isActive'], name='tassign_teacher_idx'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['studentID', 'sessionID'],
                condition=Q(isDeleted=False, isActive=True, studentID__isnull=False),
                name='transport_unique_active_student_session',
            ),
            models.UniqueConstraint(
                fields=['teacherID', 'sessionID'],
                condition=Q(isDeleted=False, isActive=True, teacherID__isnull=False),
                name='transport_unique_active_teacher_session',
            ),
        ]


class TransportFeeRecord(TransportAuditModel):
    STATUS_CHOICES = (
        ('pending', 'Pending'),
        ('partial', 'Partial'),
        ('paid', 'Paid'),
        ('waived', 'Waived'),
        ('cancelled', 'Cancelled'),
    )

    assignmentID = models.ForeignKey(TransportAssignment, on_delete=models.PROTECT, related_name='feeRecords')
    financeChargeID = models.ForeignKey(StudentCharge, blank=True, null=True, on_delete=models.SET_NULL, related_name='transportFeeRecords')
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
        if self.status == 'waived':
            self.paidAmount = Decimal('0.00')
            self.balanceAmount = Decimal('0.00')
            return
        if self.status == 'cancelled':
            self.balanceAmount = Decimal('0.00')
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
            raise ValidationError('Transport fee amounts cannot be negative.')
        if not 1 <= int(self.feeMonth or 0) <= 12:
            raise ValidationError('Fee month must be between 1 and 12.')
        if self.status not in {'waived', 'cancelled'} and self.paidAmount > self.netAmount:
            raise ValidationError('Paid amount cannot exceed net transport fee.')

    def save(self, *args, **kwargs):
        self.recalculate()
        super().save(*args, **kwargs)

    @property
    def assignee_name(self):
        return self.assignmentID.assignee_name if self.assignmentID_id else 'N/A'

    def __str__(self):
        return f'{self.assignee_name} - {self.feeMonth}/{self.feeYear}'

    class Meta:
        verbose_name_plural = 'g) Transport Fee Records'
        indexes = [
            models.Index(fields=['schoolID', 'sessionID', 'feeYear', 'feeMonth', 'status'], name='tfr_period_status_idx'),
            models.Index(fields=['assignmentID', 'feeYear', 'feeMonth', 'isDeleted'], name='tfr_assign_period_idx'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['assignmentID', 'feeYear', 'feeMonth'],
                condition=Q(isDeleted=False),
                name='transport_unique_fee_record_assignment_period',
            ),
        ]
