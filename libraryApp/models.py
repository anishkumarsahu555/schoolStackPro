from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from stdimage import StdImageField

from homeApp.model_mixins import SchoolScopedModel
from managementApp.models import Student, TeacherDetail
from utils.utils import UPLOAD_TO_PATTERNS


class LibraryAuditModel(SchoolScopedModel):
    lastEditedBy = models.CharField(max_length=500, blank=True, null=True)
    updatedByUserID = models.ForeignKey(User, blank=True, null=True, on_delete=models.SET_NULL, related_name='+')

    class Meta:
        abstract = True


class LibraryCategory(LibraryAuditModel):
    name = models.CharField(max_length=200)
    code = models.CharField(max_length=50, blank=True, null=True)
    parent = models.ForeignKey('self', blank=True, null=True, on_delete=models.SET_NULL, related_name='children')
    description = models.TextField(blank=True, null=True)
    isActive = models.BooleanField(default=True)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name_plural = 'a) Library Categories'
        indexes = [
            models.Index(fields=['schoolID', 'sessionID', 'isDeleted', 'isActive'], name='lib_cat_scope_idx'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['schoolID', 'sessionID', 'name'],
                condition=Q(isDeleted=False),
                name='library_category_unique_active_name',
            ),
        ]


class LibraryAuthor(LibraryAuditModel):
    name = models.CharField(max_length=250)
    country = models.CharField(max_length=120, blank=True, null=True)
    bio = models.TextField(blank=True, null=True)
    isActive = models.BooleanField(default=True)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name_plural = 'b) Library Authors'
        indexes = [
            models.Index(fields=['schoolID', 'sessionID', 'isDeleted', 'isActive'], name='lib_author_scope_idx'),
        ]


class LibraryPublisher(LibraryAuditModel):
    name = models.CharField(max_length=250)
    phoneNumber = models.CharField(max_length=30, blank=True, null=True)
    email = models.CharField(max_length=250, blank=True, null=True)
    address = models.TextField(blank=True, null=True)
    isActive = models.BooleanField(default=True)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name_plural = 'c) Library Publishers'
        indexes = [
            models.Index(fields=['schoolID', 'sessionID', 'isDeleted', 'isActive'], name='lib_pub_scope_idx'),
        ]


class LibraryBook(LibraryAuditModel):
    title = models.CharField(max_length=300)
    subtitle = models.CharField(max_length=300, blank=True, null=True)
    isbn = models.CharField(max_length=80, blank=True, null=True)
    category = models.ForeignKey(LibraryCategory, blank=True, null=True, on_delete=models.SET_NULL, related_name='books')
    publisher = models.ForeignKey(LibraryPublisher, blank=True, null=True, on_delete=models.SET_NULL, related_name='books')
    authors = models.ManyToManyField(LibraryAuthor, through='LibraryBookAuthor', related_name='books', blank=True)
    edition = models.CharField(max_length=100, blank=True, null=True)
    language = models.CharField(max_length=100, blank=True, null=True)
    publicationYear = models.PositiveIntegerField(blank=True, null=True)
    shelfLocation = models.CharField(max_length=150, blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    coverImage = StdImageField(
        upload_to=UPLOAD_TO_PATTERNS,
        variations={'thumbnail': (100, 140, True), 'medium': (260, 360)},
        delete_orphans=True,
        blank=True,
    )
    isActive = models.BooleanField(default=True)

    def __str__(self):
        return self.title

    class Meta:
        verbose_name_plural = 'd) Library Books'
        indexes = [
            models.Index(fields=['schoolID', 'sessionID', 'isDeleted', 'isActive'], name='lib_book_scope_idx'),
            models.Index(fields=['schoolID', 'isbn', 'isDeleted'], name='lib_book_isbn_idx'),
        ]


class LibraryBookAuthor(models.Model):
    book = models.ForeignKey(LibraryBook, on_delete=models.CASCADE)
    author = models.ForeignKey(LibraryAuthor, on_delete=models.CASCADE)

    class Meta:
        verbose_name_plural = 'e) Library Book Authors'
        constraints = [
            models.UniqueConstraint(fields=['book', 'author'], name='library_book_author_unique'),
        ]


class LibraryBookCopy(LibraryAuditModel):
    STATUS_CHOICES = (
        ('available', 'Available'),
        ('issued', 'Issued'),
        ('reserved', 'Reserved'),
        ('lost', 'Lost'),
        ('damaged', 'Damaged'),
        ('withdrawn', 'Withdrawn'),
    )
    CONDITION_CHOICES = (
        ('good', 'Good'),
        ('fair', 'Fair'),
        ('damaged', 'Damaged'),
        ('lost', 'Lost'),
        ('withdrawn', 'Withdrawn'),
    )

    book = models.ForeignKey(LibraryBook, on_delete=models.CASCADE, related_name='copies')
    accessionNumber = models.CharField(max_length=100)
    barcodeValue = models.CharField(max_length=150, blank=True, null=True)
    qrCodeValue = models.CharField(max_length=150, blank=True, null=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='available')
    condition = models.CharField(max_length=20, choices=CONDITION_CHOICES, default='good')
    purchaseDate = models.DateField(blank=True, null=True)
    purchasePrice = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    notes = models.TextField(blank=True, null=True)
    isActive = models.BooleanField(default=True)

    def __str__(self):
        return f'{self.accessionNumber} - {self.book.title}'

    class Meta:
        verbose_name_plural = 'f) Library Book Copies'
        indexes = [
            models.Index(fields=['schoolID', 'sessionID', 'status', 'isDeleted'], name='lib_copy_status_idx'),
            models.Index(fields=['book', 'status', 'isDeleted'], name='lib_copy_book_status_idx'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['schoolID', 'sessionID', 'accessionNumber'],
                condition=Q(isDeleted=False),
                name='library_copy_unique_accession',
            ),
        ]


class LibraryMember(LibraryAuditModel):
    MEMBER_TYPE_CHOICES = (
        ('student', 'Student'),
        ('staff', 'Staff'),
    )

    memberType = models.CharField(max_length=20, choices=MEMBER_TYPE_CHOICES)
    student = models.ForeignKey(Student, blank=True, null=True, on_delete=models.CASCADE, related_name='libraryMemberships')
    staff = models.ForeignKey(TeacherDetail, blank=True, null=True, on_delete=models.CASCADE, related_name='libraryMemberships')
    memberCode = models.CharField(max_length=100)
    joinDate = models.DateField(default=date.today)
    maxBooksAllowed = models.PositiveIntegerField(default=2)
    fineLimit = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    isActive = models.BooleanField(default=True)

    def clean(self):
        if self.memberType == 'student' and not self.student_id:
            raise ValidationError('Student member requires a student.')
        if self.memberType == 'staff' and not self.staff_id:
            raise ValidationError('Staff member requires a staff record.')
        if self.student_id and self.staff_id:
            raise ValidationError('A library member can link to either student or staff, not both.')

    @property
    def display_name(self):
        if self.student_id:
            return self.student.name
        if self.staff_id:
            return self.staff.name
        return self.memberCode

    def __str__(self):
        return f'{self.memberCode} - {self.display_name}'

    class Meta:
        verbose_name_plural = 'g) Library Members'
        indexes = [
            models.Index(fields=['schoolID', 'sessionID', 'memberType', 'isDeleted', 'isActive'], name='lib_mem_scope_idx'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['schoolID', 'sessionID', 'memberCode'],
                condition=Q(isDeleted=False),
                name='library_member_unique_code',
            ),
        ]


class LibraryMemberCardDesign(LibraryAuditModel):
    name = models.CharField(max_length=200, blank=True, null=True, default='Default Library Card Design')
    isActive = models.BooleanField(default=True)
    cardWidthMm = models.DecimalField(max_digits=5, decimal_places=1, default=54)
    cardHeightMm = models.DecimalField(max_digits=5, decimal_places=1, default=86)
    orientation = models.CharField(max_length=20, blank=True, null=True, default='portrait')
    headerConfig = models.JSONField(default=dict, blank=True)
    fieldsConfig = models.JSONField(default=list, blank=True)
    styleConfig = models.JSONField(default=dict, blank=True)
    footerConfig = models.JSONField(default=dict, blank=True)
    librarianSignature = models.ImageField(upload_to='library/member_card/signatures/', blank=True, null=True)
    backgroundImage = models.ImageField(upload_to='library/member_card/backgrounds/', blank=True, null=True)

    def __str__(self):
        return self.name or 'Default Library Card Design'

    class Meta:
        verbose_name_plural = 'k) Library Member Card Designs'
        indexes = [
            models.Index(fields=['schoolID', 'sessionID', 'isActive', 'isDeleted'], name='lib_card_design_idx'),
        ]


class LibraryIssue(LibraryAuditModel):
    STATUS_CHOICES = (
        ('issued', 'Issued'),
        ('returned', 'Returned'),
        ('lost', 'Lost'),
        ('damaged', 'Damaged'),
    )

    member = models.ForeignKey(LibraryMember, on_delete=models.PROTECT, related_name='issues')
    copy = models.ForeignKey(LibraryBookCopy, on_delete=models.PROTECT, related_name='issues')
    issueDate = models.DateField(default=date.today)
    dueDate = models.DateField()
    returnDate = models.DateField(blank=True, null=True)
    renewalCount = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='issued')
    returnCondition = models.CharField(max_length=20, blank=True, null=True)
    overdueDays = models.PositiveIntegerField(default=0)
    fineAmount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    notes = models.TextField(blank=True, null=True)

    def clean(self):
        if self.dueDate and self.issueDate and self.dueDate < self.issueDate:
            raise ValidationError('Due date cannot be before issue date.')

    def __str__(self):
        return f'{self.copy.accessionNumber} issued to {self.member.memberCode}'

    class Meta:
        verbose_name_plural = 'h) Library Issues'
        indexes = [
            models.Index(fields=['schoolID', 'sessionID', 'status', 'dueDate', 'isDeleted'], name='lib_issue_status_idx'),
            models.Index(fields=['member', 'status', 'isDeleted'], name='lib_issue_member_idx'),
            models.Index(fields=['copy', 'status', 'isDeleted'], name='lib_issue_copy_idx'),
            models.Index(fields=['schoolID', 'sessionID', 'issueDate', 'isDeleted'], name='lib_issue_date_idx'),
        ]


class LibraryReservation(LibraryAuditModel):
    STATUS_CHOICES = (
        ('pending', 'Pending'),
        ('fulfilled', 'Fulfilled'),
        ('cancelled', 'Cancelled'),
        ('expired', 'Expired'),
    )

    member = models.ForeignKey(LibraryMember, on_delete=models.CASCADE, related_name='reservations')
    book = models.ForeignKey(LibraryBook, on_delete=models.CASCADE, related_name='reservations')
    reservationDate = models.DateField(default=date.today)
    expiryDate = models.DateField(blank=True, null=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    notes = models.TextField(blank=True, null=True)

    def __str__(self):
        return f'{self.member.memberCode} reserved {self.book.title}'

    class Meta:
        verbose_name_plural = 'i) Library Reservations'
        indexes = [
            models.Index(fields=['schoolID', 'sessionID', 'status', 'isDeleted'], name='lib_res_status_idx'),
        ]


class LibraryFine(LibraryAuditModel):
    STATUS_CHOICES = (
        ('pending', 'Pending'),
        ('paid', 'Paid'),
        ('waived', 'Waived'),
        ('cancelled', 'Cancelled'),
    )
    REASON_CHOICES = (
        ('overdue', 'Overdue'),
        ('lost', 'Lost Book'),
        ('damaged', 'Damaged Book'),
        ('manual', 'Manual'),
    )

    issue = models.ForeignKey(LibraryIssue, blank=True, null=True, on_delete=models.SET_NULL, related_name='fines')
    member = models.ForeignKey(LibraryMember, on_delete=models.CASCADE, related_name='fines')
    reason = models.CharField(max_length=20, choices=REASON_CHOICES, default='overdue')
    amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    paidAmount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    paidDate = models.DateField(blank=True, null=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    notes = models.TextField(blank=True, null=True)

    @property
    def balance(self):
        return max(self.amount - self.paidAmount, Decimal('0.00'))

    def __str__(self):
        return f'{self.member.memberCode} - {self.reason} - {self.amount}'

    class Meta:
        verbose_name_plural = 'j) Library Fines'
        indexes = [
            models.Index(fields=['schoolID', 'sessionID', 'status', 'isDeleted'], name='lib_fine_status_idx'),
        ]


class LibrarySetting(LibraryAuditModel):
    defaultIssueDays = models.PositiveIntegerField(default=14)
    finePerDay = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal('1.00'))
    graceDays = models.PositiveIntegerField(default=0)
    maxRenewals = models.PositiveIntegerField(default=2)
    defaultMaxBooksAllowed = models.PositiveIntegerField(default=2)
    reservationExpiryDays = models.PositiveIntegerField(default=3)
    lostBookFine = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    damagedBookFine = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))

    def __str__(self):
        return f'Library settings {self.schoolID_id}/{self.sessionID_id}'

    class Meta:
        verbose_name_plural = 'k) Library Settings'
        constraints = [
            models.UniqueConstraint(
                fields=['schoolID', 'sessionID'],
                condition=Q(isDeleted=False),
                name='library_setting_unique_scope',
            ),
        ]
