from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from homeApp.models import SchoolDetail, SchoolSession
from managementApp.models import Parent, Standard, Student, TeacherDetail


class FinanceAuditModel(models.Model):
    schoolID = models.ForeignKey(SchoolDetail, blank=True, null=True, on_delete=models.CASCADE)
    sessionID = models.ForeignKey(SchoolSession, blank=True, null=True, on_delete=models.CASCADE)
    datetime = models.DateTimeField(auto_now_add=True, auto_now=False)
    lastUpdatedOn = models.DateTimeField(auto_now_add=False, auto_now=True)
    isDeleted = models.BooleanField(default=False)
    lastEditedBy = models.CharField(max_length=500, blank=True, null=True)
    updatedByUserID = models.ForeignKey(User, blank=True, null=True, on_delete=models.SET_NULL, related_name='+')

    class Meta:
        abstract = True


class FinanceAccount(FinanceAuditModel):
    ACCOUNT_TYPE_CHOICES = (
        ('asset', 'Asset'),
        ('liability', 'Liability'),
        ('equity', 'Equity'),
        ('income', 'Income'),
        ('expense', 'Expense'),
    )
    BALANCE_TYPE_CHOICES = (
        ('debit', 'Debit'),
        ('credit', 'Credit'),
    )

    parentAccountID = models.ForeignKey('self', blank=True, null=True, on_delete=models.PROTECT, related_name='childAccounts')
    accountCode = models.CharField(max_length=50)
    accountName = models.CharField(max_length=300)
    accountType = models.CharField(max_length=20, choices=ACCOUNT_TYPE_CHOICES)
    openingBalance = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    openingBalanceType = models.CharField(max_length=10, choices=BALANCE_TYPE_CHOICES, default='debit')
    description = models.TextField(blank=True, null=True, default='')
    isControlAccount = models.BooleanField(default=False)
    isSystemGenerated = models.BooleanField(default=False)
    isActive = models.BooleanField(default=True)

    def __str__(self):
        return f'{self.accountCode} - {self.accountName}'

    class Meta:
        verbose_name_plural = 'a) Finance Accounts'
        indexes = [
            models.Index(fields=['schoolID', 'sessionID', 'accountType', 'isDeleted'], name='fa_school_type_idx'),
            models.Index(fields=['schoolID', 'accountCode', 'isDeleted'], name='fa_school_code_idx'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['schoolID', 'sessionID', 'accountCode'],
                condition=Q(isDeleted=False),
                name='finance_account_unique_active_code',
            ),
        ]


class FinanceParty(FinanceAuditModel):
    PARTY_TYPE_CHOICES = (
        ('student', 'Student'),
        ('parent', 'Parent'),
        ('teacher', 'Teacher'),
        ('staff', 'Staff'),
        ('vendor', 'Vendor'),
        ('other', 'Other'),
    )

    partyType = models.CharField(max_length=20, choices=PARTY_TYPE_CHOICES)
    studentID = models.ForeignKey(Student, blank=True, null=True, on_delete=models.SET_NULL)
    parentID = models.ForeignKey(Parent, blank=True, null=True, on_delete=models.SET_NULL)
    teacherID = models.ForeignKey(TeacherDetail, blank=True, null=True, on_delete=models.SET_NULL)
    displayName = models.CharField(max_length=300)
    phoneNumber = models.CharField(max_length=30, blank=True, null=True)
    email = models.EmailField(blank=True, null=True)
    address = models.TextField(blank=True, null=True, default='')
    taxIdentifier = models.CharField(max_length=100, blank=True, null=True)
    isActive = models.BooleanField(default=True)

    def clean(self):
        linked_targets = [self.studentID_id, self.parentID_id, self.teacherID_id]
        if sum(1 for value in linked_targets if value) > 1:
            raise ValidationError('Finance party can link to only one source profile.')

    def __str__(self):
        return self.displayName

    class Meta:
        verbose_name_plural = 'b) Finance Parties'
        indexes = [
            models.Index(fields=['schoolID', 'sessionID', 'partyType', 'isDeleted'], name='fp_school_type_idx'),
            models.Index(fields=['schoolID', 'displayName', 'isDeleted'], name='fp_school_name_idx'),
        ]


class FinancePaymentMode(FinanceAuditModel):
    MODE_TYPE_CHOICES = (
        ('cash', 'Cash'),
        ('bank', 'Bank'),
        ('upi', 'UPI'),
        ('card', 'Card'),
        ('cheque', 'Cheque'),
        ('online_gateway', 'Online Gateway'),
        ('adjustment', 'Adjustment'),
    )

    code = models.CharField(max_length=50)
    name = models.CharField(max_length=100)
    modeType = models.CharField(max_length=20, choices=MODE_TYPE_CHOICES)
    linkedAccountID = models.ForeignKey(FinanceAccount, blank=True, null=True, on_delete=models.PROTECT)
    isActive = models.BooleanField(default=True)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name_plural = 'c) Finance Payment Modes'
        indexes = [
            models.Index(fields=['schoolID', 'code', 'isDeleted'], name='fpm_school_code_idx'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['schoolID', 'code'],
                condition=Q(isDeleted=False),
                name='finance_payment_mode_unique_active_code',
            ),
        ]


class FinanceConfiguration(FinanceAuditModel):
    receiptTitle = models.CharField(max_length=150, default='Payment Receipt')
    receiptFooterNote = models.TextField(
        blank=True,
        null=True,
        default='This receipt reflects the current finance allocation summary for the recorded payment.',
    )
    defaultCashAccountID = models.ForeignKey(
        FinanceAccount,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name='finance_config_cash_accounts',
    )
    defaultBankAccountID = models.ForeignKey(
        FinanceAccount,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name='finance_config_bank_accounts',
    )
    receiptPrefix = models.CharField(max_length=20, default='RCT')
    voucherPrefix = models.CharField(max_length=20, default='EXP')
    refundPrefix = models.CharField(max_length=20, default='RFD')
    transactionPrefix = models.CharField(max_length=20, default='TXN')
    payrollPrefix = models.CharField(max_length=20, default='PAY')
    sequencePadding = models.PositiveSmallIntegerField(default=5)
    includeDateSegment = models.BooleanField(default=True)
    lastReceiptSequence = models.PositiveIntegerField(default=0)
    lastVoucherSequence = models.PositiveIntegerField(default=0)
    lastRefundSequence = models.PositiveIntegerField(default=0)
    lastTransactionSequence = models.PositiveIntegerField(default=0)
    lastPayrollSequence = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f'Finance Settings - {self.schoolID_id or "N/A"} / {self.sessionID_id or "N/A"}'

    class Meta:
        verbose_name_plural = 'd) Finance Configuration'
        constraints = [
            models.UniqueConstraint(
                fields=['schoolID', 'sessionID'],
                condition=Q(isDeleted=False),
                name='finance_config_unique_school_session',
            ),
        ]


class FeeHead(FinanceAuditModel):
    CATEGORY_CHOICES = (
        ('admission', 'Admission'),
        ('registration', 'Registration'),
        ('tuition', 'Tuition'),
        ('annual', 'Annual'),
        ('exam', 'Exam'),
        ('transport', 'Transport'),
        ('hostel', 'Hostel'),
        ('library', 'Library'),
        ('lab', 'Lab'),
        ('activity', 'Activity'),
        ('misc', 'Misc'),
    )
    RECURRENCE_CHOICES = (
        ('one_time', 'One Time'),
        ('monthly', 'Monthly'),
        ('quarterly', 'Quarterly'),
        ('annual', 'Annual'),
        ('custom', 'Custom'),
    )

    code = models.CharField(max_length=50)
    name = models.CharField(max_length=200)
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default='misc')
    defaultAmount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    isRecurring = models.BooleanField(default=False)
    recurrenceType = models.CharField(max_length=20, choices=RECURRENCE_CHOICES, default='one_time')
    incomeAccountID = models.ForeignKey(FinanceAccount, on_delete=models.PROTECT, related_name='fee_head_income_accounts')
    receivableAccountID = models.ForeignKey(FinanceAccount, on_delete=models.PROTECT, related_name='fee_head_receivable_accounts')
    lateFeeAccountID = models.ForeignKey(FinanceAccount, blank=True, null=True, on_delete=models.PROTECT, related_name='fee_head_late_fee_accounts')
    displayOrder = models.PositiveIntegerField(default=0)
    isActive = models.BooleanField(default=True)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name_plural = 'd) Fee Heads'
        indexes = [
            models.Index(fields=['schoolID', 'sessionID', 'category', 'isDeleted'], name='fh_school_cat_idx'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['schoolID', 'sessionID', 'code'],
                condition=Q(isDeleted=False),
                name='fee_head_unique_active_code',
            ),
        ]


class StudentCharge(FinanceAuditModel):
    CHARGE_TYPE_CHOICES = (
        ('student_fee', 'Student Fee'),
        ('admission_fee', 'Admission Fee'),
        ('misc_income', 'Misc Income'),
        ('refund_recovery', 'Refund Recovery'),
        ('other', 'Other'),
    )
    STATUS_CHOICES = (
        ('draft', 'Draft'),
        ('posted', 'Posted'),
        ('partial', 'Partial'),
        ('paid', 'Paid'),
        ('cancelled', 'Cancelled'),
    )

    studentID = models.ForeignKey(Student, on_delete=models.PROTECT)
    standardID = models.ForeignKey(Standard, blank=True, null=True, on_delete=models.PROTECT)
    partyID = models.ForeignKey(FinanceParty, on_delete=models.PROTECT)
    feeHeadID = models.ForeignKey(FeeHead, blank=True, null=True, on_delete=models.PROTECT)
    chargeType = models.CharField(max_length=30, choices=CHARGE_TYPE_CHOICES, default='student_fee')
    referenceNo = models.CharField(max_length=100, blank=True, null=True)
    title = models.CharField(max_length=300)
    description = models.TextField(blank=True, null=True, default='')
    chargeDate = models.DateField()
    dueDate = models.DateField(blank=True, null=True)
    periodStartDate = models.DateField(blank=True, null=True)
    periodEndDate = models.DateField(blank=True, null=True)
    grossAmount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    discountAmount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    fineAmount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    netAmount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    paidAmount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    balanceAmount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    sourceModule = models.CharField(max_length=100, blank=True, null=True, default='')
    sourceRecordID = models.CharField(max_length=100, blank=True, null=True, default='')
    canAutoPost = models.BooleanField(default=True)

    def clean(self):
        values = [self.grossAmount, self.discountAmount, self.fineAmount, self.netAmount, self.paidAmount, self.balanceAmount]
        if any(value < 0 for value in values):
            raise ValidationError('Student charge amounts cannot be negative.')
        expected_net = (self.grossAmount or Decimal('0.00')) - (self.discountAmount or Decimal('0.00')) + (self.fineAmount or Decimal('0.00'))
        if self.netAmount != expected_net:
            raise ValidationError({'netAmount': 'Net amount must be gross - discount + fine.'})
        expected_balance = (self.netAmount or Decimal('0.00')) - (self.paidAmount or Decimal('0.00'))
        if self.balanceAmount != expected_balance:
            raise ValidationError({'balanceAmount': 'Balance amount must be net - paid.'})

    def __str__(self):
        return f'{self.title} - {self.studentID}'

    class Meta:
        verbose_name_plural = 'e) Student Charges'
        indexes = [
            models.Index(fields=['schoolID', 'sessionID', 'studentID', 'status'], name='sc_school_stu_stat_idx'),
            models.Index(fields=['schoolID', 'sessionID', 'chargeDate'], name='sc_school_date_idx'),
        ]


class PaymentReceipt(FinanceAuditModel):
    STATUS_CHOICES = (
        ('draft', 'Draft'),
        ('submitted', 'Submitted'),
        ('confirmed', 'Confirmed'),
        ('cancelled', 'Cancelled'),
        ('reversed', 'Reversed'),
    )

    receiptNo = models.CharField(max_length=100)
    receiptDate = models.DateField()
    partyID = models.ForeignKey(FinanceParty, on_delete=models.PROTECT)
    studentID = models.ForeignKey(Student, blank=True, null=True, on_delete=models.PROTECT)
    receivedFromName = models.CharField(max_length=300, blank=True, null=True)
    paymentModeID = models.ForeignKey(FinancePaymentMode, on_delete=models.PROTECT)
    depositAccountID = models.ForeignKey(FinanceAccount, on_delete=models.PROTECT, related_name='deposit_receipts')
    amountReceived = models.DecimalField(max_digits=14, decimal_places=2)
    referenceNo = models.CharField(max_length=120, blank=True, null=True)
    instrumentDate = models.DateField(blank=True, null=True)
    notes = models.TextField(blank=True, null=True, default='')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    requestedApprovalStatus = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    sourceModule = models.CharField(max_length=100, blank=True, null=True, default='')
    sourceRecordID = models.CharField(max_length=100, blank=True, null=True, default='')

    def clean(self):
        if self.amountReceived <= 0:
            raise ValidationError({'amountReceived': 'Receipt amount must be greater than zero.'})

    def __str__(self):
        return self.receiptNo

    class Meta:
        verbose_name_plural = 'f) Payment Receipts'
        indexes = [
            models.Index(fields=['schoolID', 'sessionID', 'receiptDate', 'status'], name='pr_school_date_idx'),
            models.Index(fields=['schoolID', 'receiptNo', 'isDeleted'], name='pr_school_no_idx'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['schoolID', 'receiptNo'],
                condition=Q(isDeleted=False),
                name='payment_receipt_unique_active_no',
            ),
        ]


class PaymentReceiptAllocation(models.Model):
    receiptID = models.ForeignKey(PaymentReceipt, on_delete=models.CASCADE, related_name='allocations')
    studentChargeID = models.ForeignKey(StudentCharge, on_delete=models.PROTECT, related_name='receiptAllocations')
    allocatedAmount = models.DecimalField(max_digits=14, decimal_places=2)
    fineComponent = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    discountComponent = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    note = models.TextField(blank=True, null=True, default='')
    datetime = models.DateTimeField(auto_now_add=True, auto_now=False)
    lastUpdatedOn = models.DateTimeField(auto_now_add=False, auto_now=True)

    def clean(self):
        if self.allocatedAmount <= 0:
            raise ValidationError({'allocatedAmount': 'Allocation amount must be greater than zero.'})

    def __str__(self):
        return f'{self.receiptID} -> {self.studentChargeID}'

    class Meta:
        verbose_name_plural = 'g) Payment Receipt Allocations'
        indexes = [
            models.Index(fields=['receiptID', 'studentChargeID'], name='pra_receipt_charge_idx'),
        ]


class PaymentRefund(FinanceAuditModel):
    STATUS_CHOICES = (
        ('draft', 'Draft'),
        ('submitted', 'Submitted'),
        ('confirmed', 'Confirmed'),
        ('cancelled', 'Cancelled'),
        ('reversed', 'Reversed'),
    )

    refundNo = models.CharField(max_length=100)
    refundDate = models.DateField()
    receiptID = models.ForeignKey(PaymentReceipt, on_delete=models.PROTECT, related_name='refunds')
    partyID = models.ForeignKey(FinanceParty, on_delete=models.PROTECT)
    studentID = models.ForeignKey(Student, blank=True, null=True, on_delete=models.PROTECT)
    paymentModeID = models.ForeignKey(FinancePaymentMode, on_delete=models.PROTECT)
    payoutAccountID = models.ForeignKey(FinanceAccount, on_delete=models.PROTECT, related_name='refund_payouts')
    amountRefunded = models.DecimalField(max_digits=14, decimal_places=2)
    referenceNo = models.CharField(max_length=120, blank=True, null=True)
    notes = models.TextField(blank=True, null=True, default='')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    requestedApprovalStatus = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    sourceModule = models.CharField(max_length=100, blank=True, null=True, default='')
    sourceRecordID = models.CharField(max_length=100, blank=True, null=True, default='')

    def clean(self):
        if self.amountRefunded <= 0:
            raise ValidationError({'amountRefunded': 'Refund amount must be greater than zero.'})

    def __str__(self):
        return self.refundNo

    class Meta:
        verbose_name_plural = 'h) Payment Refunds'
        indexes = [
            models.Index(fields=['schoolID', 'sessionID', 'refundDate', 'status'], name='pref_school_date_idx'),
            models.Index(fields=['schoolID', 'refundNo', 'isDeleted'], name='pref_school_no_idx'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['schoolID', 'refundNo'],
                condition=Q(isDeleted=False),
                name='payment_refund_unique_active_no',
            ),
        ]


class PaymentRefundAllocation(models.Model):
    refundID = models.ForeignKey(PaymentRefund, on_delete=models.CASCADE, related_name='allocations')
    studentChargeID = models.ForeignKey(StudentCharge, on_delete=models.PROTECT, related_name='refundAllocations')
    refundedAmount = models.DecimalField(max_digits=14, decimal_places=2)
    note = models.TextField(blank=True, null=True, default='')
    datetime = models.DateTimeField(auto_now_add=True, auto_now=False)
    lastUpdatedOn = models.DateTimeField(auto_now_add=False, auto_now=True)

    def clean(self):
        if self.refundedAmount <= 0:
            raise ValidationError({'refundedAmount': 'Refund allocation amount must be greater than zero.'})

    def __str__(self):
        return f'{self.refundID} -> {self.studentChargeID}'

    class Meta:
        verbose_name_plural = 'i) Payment Refund Allocations'
        indexes = [
            models.Index(fields=['refundID', 'studentChargeID'], name='pfa_refund_charge_idx'),
        ]


class ExpenseCategory(FinanceAuditModel):
    code = models.CharField(max_length=50)
    name = models.CharField(max_length=150)
    expenseAccountID = models.ForeignKey(FinanceAccount, on_delete=models.PROTECT, related_name='expense_categories')
    payableAccountID = models.ForeignKey(FinanceAccount, blank=True, null=True, on_delete=models.PROTECT, related_name='expense_payable_categories')
    isActive = models.BooleanField(default=True)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name_plural = 'h) Expense Categories'
        constraints = [
            models.UniqueConstraint(
                fields=['schoolID', 'code'],
                condition=Q(isDeleted=False),
                name='expense_category_unique_active_code',
            ),
        ]


class ExpenseVoucher(FinanceAuditModel):
    APPROVAL_STATUS_CHOICES = (
        ('draft', 'Draft'),
        ('submitted', 'Submitted'),
        ('approved', 'Approved'),
        ('paid', 'Paid'),
        ('cancelled', 'Cancelled'),
        ('reversed', 'Reversed'),
    )

    voucherNo = models.CharField(max_length=100)
    voucherDate = models.DateField()
    partyID = models.ForeignKey(FinanceParty, blank=True, null=True, on_delete=models.PROTECT)
    expenseCategoryID = models.ForeignKey(ExpenseCategory, on_delete=models.PROTECT)
    title = models.CharField(max_length=300)
    description = models.TextField(blank=True, null=True, default='')
    grossAmount = models.DecimalField(max_digits=14, decimal_places=2)
    deductionAmount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    netAmount = models.DecimalField(max_digits=14, decimal_places=2)
    paymentModeID = models.ForeignKey(FinancePaymentMode, blank=True, null=True, on_delete=models.PROTECT)
    paymentAccountID = models.ForeignKey(FinanceAccount, blank=True, null=True, on_delete=models.PROTECT, related_name='expense_payment_accounts')
    billNo = models.CharField(max_length=100, blank=True, null=True)
    billDate = models.DateField(blank=True, null=True)
    approvalStatus = models.CharField(max_length=20, choices=APPROVAL_STATUS_CHOICES, default='draft')
    requestedApprovalStatus = models.CharField(max_length=20, choices=APPROVAL_STATUS_CHOICES, default='draft')
    isImmediatePayment = models.BooleanField(default=False)
    sourceModule = models.CharField(max_length=100, blank=True, null=True, default='')
    sourceRecordID = models.CharField(max_length=100, blank=True, null=True, default='')

    def clean(self):
        if self.grossAmount <= 0:
            raise ValidationError({'grossAmount': 'Gross amount must be greater than zero.'})
        expected_net = (self.grossAmount or Decimal('0.00')) - (self.deductionAmount or Decimal('0.00'))
        if self.netAmount != expected_net:
            raise ValidationError({'netAmount': 'Net amount must be gross - deduction.'})

    def __str__(self):
        return self.voucherNo

    class Meta:
        verbose_name_plural = 'i) Expense Vouchers'
        indexes = [
            models.Index(fields=['schoolID', 'sessionID', 'voucherDate', 'approvalStatus'], name='ev_school_date_idx'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['schoolID', 'voucherNo'],
                condition=Q(isDeleted=False),
                name='expense_voucher_unique_active_no',
            ),
        ]


class PayrollRun(FinanceAuditModel):
    STATUS_CHOICES = (
        ('draft', 'Draft'),
        ('submitted', 'Submitted'),
        ('processed', 'Processed'),
        ('posted', 'Posted'),
        ('paid', 'Paid'),
        ('closed', 'Closed'),
    )

    payrollRunNo = models.CharField(max_length=100, blank=True, null=True)
    month = models.PositiveSmallIntegerField()
    year = models.PositiveIntegerField()
    runDate = models.DateField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    requestedApprovalStatus = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')

    def __str__(self):
        return self.payrollRunNo or f'Payroll {self.month:02d}-{self.year}'

    class Meta:
        verbose_name_plural = 'j) Payroll Runs'
        constraints = [
            models.UniqueConstraint(
                fields=['schoolID', 'sessionID', 'month', 'year'],
                condition=Q(isDeleted=False),
                name='payroll_run_unique_school_period',
            ),
            models.CheckConstraint(
                check=Q(month__gte=1) & Q(month__lte=12),
                name='payroll_run_valid_month',
            ),
            models.UniqueConstraint(
                fields=['schoolID', 'payrollRunNo'],
                condition=Q(isDeleted=False) & Q(payrollRunNo__isnull=False),
                name='payroll_run_unique_active_no',
            ),
        ]


class PayrollLine(models.Model):
    PAYMENT_STATUS_CHOICES = (
        ('pending', 'Pending'),
        ('submitted', 'Submitted'),
        ('paid', 'Paid'),
        ('hold', 'Hold'),
    )

    payrollRunID = models.ForeignKey(PayrollRun, on_delete=models.CASCADE, related_name='payrollLines')
    teacherID = models.ForeignKey(TeacherDetail, blank=True, null=True, on_delete=models.SET_NULL)
    partyID = models.ForeignKey(FinanceParty, on_delete=models.PROTECT)
    basicAmount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    allowanceAmount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    deductionAmount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    advanceRecoveryAmount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    netAmount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    salaryExpenseAccountID = models.ForeignKey(FinanceAccount, on_delete=models.PROTECT, related_name='payroll_expense_lines')
    salaryPayableAccountID = models.ForeignKey(FinanceAccount, on_delete=models.PROTECT, related_name='payroll_payable_lines')
    paymentStatus = models.CharField(max_length=20, choices=PAYMENT_STATUS_CHOICES, default='pending')
    requestedPaymentStatus = models.CharField(max_length=20, choices=PAYMENT_STATUS_CHOICES, default='pending')
    paymentModeID = models.ForeignKey(FinancePaymentMode, blank=True, null=True, on_delete=models.PROTECT, related_name='payroll_payments')
    paymentDate = models.DateField(blank=True, null=True)
    datetime = models.DateTimeField(auto_now_add=True, auto_now=False)
    lastUpdatedOn = models.DateTimeField(auto_now_add=False, auto_now=True)

    def clean(self):
        values = [self.basicAmount, self.allowanceAmount, self.deductionAmount, self.advanceRecoveryAmount, self.netAmount]
        if any(value < 0 for value in values):
            raise ValidationError('Payroll amounts cannot be negative.')
        expected_net = (self.basicAmount or Decimal('0.00')) + (self.allowanceAmount or Decimal('0.00')) - (self.deductionAmount or Decimal('0.00')) - (self.advanceRecoveryAmount or Decimal('0.00'))
        if self.netAmount != expected_net:
            raise ValidationError({'netAmount': 'Net amount must be basic + allowance - deductions - advance recovery.'})

    def __str__(self):
        return f'{self.partyID} - {self.payrollRunID}'

    class Meta:
        verbose_name_plural = 'k) Payroll Lines'
        constraints = [
            models.UniqueConstraint(
                fields=['payrollRunID', 'partyID'],
                name='payroll_line_unique_party_per_run',
            ),
        ]


class FinanceTransaction(FinanceAuditModel):
    TXN_TYPE_CHOICES = (
        ('student_charge', 'Student Charge'),
        ('student_receipt', 'Student Receipt'),
        ('expense_accrual', 'Expense Accrual'),
        ('expense_payment', 'Expense Payment'),
        ('payroll_accrual', 'Payroll Accrual'),
        ('salary_payment', 'Salary Payment'),
        ('refund', 'Refund'),
        ('transfer', 'Transfer'),
        ('adjustment', 'Adjustment'),
        ('opening_balance', 'Opening Balance'),
        ('reversal', 'Reversal'),
    )
    STATUS_CHOICES = (
        ('draft', 'Draft'),
        ('posted', 'Posted'),
        ('reversed', 'Reversed'),
    )

    txnNo = models.CharField(max_length=100)
    txnDate = models.DateField()
    txnType = models.CharField(max_length=30, choices=TXN_TYPE_CHOICES)
    referenceNo = models.CharField(max_length=120, blank=True, null=True)
    description = models.TextField(blank=True, null=True, default='')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    sourceModule = models.CharField(max_length=100, blank=True, null=True, default='')
    sourceRecordID = models.CharField(max_length=100, blank=True, null=True, default='')
    reversalOfID = models.ForeignKey('self', blank=True, null=True, on_delete=models.PROTECT, related_name='reversalTransactions')

    def __str__(self):
        return self.txnNo

    class Meta:
        verbose_name_plural = 'l) Finance Transactions'
        indexes = [
            models.Index(fields=['schoolID', 'sessionID', 'txnDate', 'status'], name='ft_school_date_idx'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['schoolID', 'txnNo'],
                condition=Q(isDeleted=False),
                name='finance_txn_unique_active_no',
            ),
        ]


class FinanceEntry(models.Model):
    ENTRY_TYPE_CHOICES = (
        ('debit', 'Debit'),
        ('credit', 'Credit'),
    )

    transactionID = models.ForeignKey(FinanceTransaction, on_delete=models.CASCADE, related_name='entries')
    accountID = models.ForeignKey(FinanceAccount, on_delete=models.PROTECT)
    partyID = models.ForeignKey(FinanceParty, blank=True, null=True, on_delete=models.PROTECT)
    entryType = models.CharField(max_length=10, choices=ENTRY_TYPE_CHOICES)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    narration = models.TextField(blank=True, null=True, default='')
    lineOrder = models.PositiveIntegerField(default=0)
    datetime = models.DateTimeField(auto_now_add=True, auto_now=False)
    lastUpdatedOn = models.DateTimeField(auto_now_add=False, auto_now=True)

    def clean(self):
        if self.amount <= 0:
            raise ValidationError({'amount': 'Entry amount must be greater than zero.'})

    def __str__(self):
        return f'{self.transactionID} - {self.entryType}'

    class Meta:
        verbose_name_plural = 'm) Finance Entries'
        indexes = [
            models.Index(fields=['transactionID', 'lineOrder'], name='fe_txn_order_idx'),
            models.Index(fields=['accountID', 'entryType'], name='fe_account_type_idx'),
        ]


class FinancePeriod(FinanceAuditModel):
    STATUS_CHOICES = (
        ('open', 'Open'),
        ('soft_locked', 'Soft Locked'),
        ('closed', 'Closed'),
    )

    periodStart = models.DateField()
    periodEnd = models.DateField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='open')
    closedByUserID = models.ForeignKey(User, blank=True, null=True, on_delete=models.SET_NULL, related_name='closed_finance_periods')
    closedAt = models.DateTimeField(blank=True, null=True)

    def clean(self):
        if self.periodStart and self.periodEnd and self.periodStart > self.periodEnd:
            raise ValidationError({'periodEnd': 'Period end must be on or after period start.'})

    def __str__(self):
        return f'{self.periodStart} to {self.periodEnd}'

    class Meta:
        verbose_name_plural = 'n) Finance Periods'
        indexes = [
            models.Index(fields=['schoolID', 'sessionID', 'status'], name='fpd_school_status_idx'),
        ]


class FinanceApprovalRule(FinanceAuditModel):
    DOCUMENT_TYPE_CHOICES = (
        ('expense_voucher', 'Expense Voucher'),
        ('payment_receipt', 'Payment Receipt'),
        ('payment_refund', 'Payment Refund'),
        ('payroll_run', 'Payroll Run'),
        ('salary_payment', 'Salary Payment'),
    )
    APPROVAL_MODE_CHOICES = (
        ('approval_required', 'Approval Required'),
        ('direct_allowed', 'Direct Allowed'),
    )

    ruleName = models.CharField(max_length=150)
    documentType = models.CharField(max_length=30, choices=DOCUMENT_TYPE_CHOICES, default='expense_voucher')
    minAmount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    maxAmount = models.DecimalField(max_digits=14, decimal_places=2, blank=True, null=True)
    approvalMode = models.CharField(max_length=30, choices=APPROVAL_MODE_CHOICES, default='approval_required')
    priority = models.PositiveSmallIntegerField(default=1)
    isActive = models.BooleanField(default=True)

    def clean(self):
        if self.minAmount is not None and self.minAmount < 0:
            raise ValidationError({'minAmount': 'Minimum amount cannot be negative.'})
        if self.maxAmount is not None and self.maxAmount < self.minAmount:
            raise ValidationError({'maxAmount': 'Maximum amount must be greater than or equal to minimum amount.'})

    def __str__(self):
        upper = self.maxAmount if self.maxAmount is not None else 'No Limit'
        return f'{self.ruleName} ({self.minAmount} - {upper})'

    class Meta:
        verbose_name_plural = 'o) Finance Approval Rules'
        indexes = [
            models.Index(fields=['schoolID', 'sessionID', 'documentType', 'isActive'], name='far_school_doc_idx'),
        ]
