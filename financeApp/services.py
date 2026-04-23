from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.db.models import Q, Sum
from django.utils.crypto import get_random_string
from django.utils import timezone

from .models import (
    ExpenseCategory,
    ExpenseVoucher,
    FeeHead,
    FinanceAccount,
    FinanceConfiguration,
    FinanceEntry,
    FinanceParty,
    FinancePaymentMode,
    PaymentRefund,
    PaymentRefundAllocation,
    FinanceTransaction,
    PaymentReceipt,
    PaymentReceiptAllocation,
    PayrollLine,
    PayrollRun,
    StudentCharge,
)
from managementApp.models import TeacherDetail


TWO_PLACES = Decimal('0.01')


def _money(value):
    return Decimal(str(value or '0')).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def _user_label(user_obj):
    if not user_obj:
        return None
    full_name = f'{user_obj.first_name} {user_obj.last_name}'.strip()
    return full_name or user_obj.username


def _refund_tables_available():
    existing_tables = set(connection.introspection.table_names())
    return (
        PaymentRefund._meta.db_table in existing_tables
        and PaymentRefundAllocation._meta.db_table in existing_tables
    )


def _finance_configuration_available(*, required_columns=None):
    existing_tables = set(connection.introspection.table_names())
    table_name = FinanceConfiguration._meta.db_table
    if table_name not in existing_tables:
        return False
    if not required_columns:
        return True
    with connection.cursor() as cursor:
        columns = {
            column.name
            for column in connection.introspection.get_table_description(cursor, table_name)
        }
    return set(required_columns).issubset(columns)


def _sanitize_prefix(value, fallback):
    prefix = ''.join(ch for ch in str(value or fallback).upper() if ch.isalnum())
    return prefix[:20] or fallback


def _sequence_width(value):
    try:
        width = int(value or 5)
    except (TypeError, ValueError):
        width = 5
    return max(3, min(width, 8))


def _preview_doc_number(prefix, sequence, *, include_date_segment, width):
    parts = [_sanitize_prefix(prefix, 'DOC')]
    if include_date_segment:
        parts.append(timezone.localdate().strftime('%Y%m%d'))
    parts.append(str(max(int(sequence or 1), 1)).zfill(_sequence_width(width)))
    return '-'.join(parts)


def _legacy_doc_number(prefix):
    return f'{_sanitize_prefix(prefix, "DOC")}-{timezone.now().strftime("%Y%m%d%H%M%S")}-{get_random_string(5).upper()}'


def _get_or_create_finance_configuration(*, school_id, session_id, user_obj=None, lock=False, default_cash_account=None, default_bank_account=None):
    if not _finance_configuration_available():
        return None
    queryset = FinanceConfiguration.objects
    if lock:
        queryset = queryset.select_for_update()
    config_obj = queryset.filter(
        schoolID_id=school_id,
        sessionID_id=session_id,
        isDeleted=False,
    ).first()
    if config_obj:
        changed = []
        if not config_obj.defaultCashAccountID_id and default_cash_account:
            config_obj.defaultCashAccountID = default_cash_account
            changed.append('defaultCashAccountID')
        if not config_obj.defaultBankAccountID_id and default_bank_account:
            config_obj.defaultBankAccountID = default_bank_account
            changed.append('defaultBankAccountID')
        if changed:
            config_obj.lastEditedBy = _user_label(user_obj)
            config_obj.updatedByUserID = user_obj
            config_obj.save(update_fields=changed + ['lastEditedBy', 'updatedByUserID', 'lastUpdatedOn'])
        return config_obj

    if default_cash_account is None:
        default_cash_account = FinanceAccount.objects.filter(
            schoolID_id=school_id,
            sessionID_id=session_id,
            accountCode='CASH_ON_HAND',
            isDeleted=False,
        ).first()
    if default_bank_account is None:
        default_bank_account = FinanceAccount.objects.filter(
            schoolID_id=school_id,
            sessionID_id=session_id,
            accountCode='BANK_MAIN',
            isDeleted=False,
        ).first()

    return FinanceConfiguration.objects.create(
        schoolID_id=school_id,
        sessionID_id=session_id,
        defaultCashAccountID=default_cash_account,
        defaultBankAccountID=default_bank_account,
        lastEditedBy=_user_label(user_obj),
        updatedByUserID=user_obj,
    )


def get_finance_configuration(*, school_id, session_id, user_obj=None):
    return _get_or_create_finance_configuration(
        school_id=school_id,
        session_id=session_id,
        user_obj=user_obj,
        lock=False,
    )


@transaction.atomic
def generate_finance_document_number(*, document_type, school_id, session_id, user_obj=None):
    type_map = {
        'receipt': ('receiptPrefix', 'lastReceiptSequence', 'RCT'),
        'voucher': ('voucherPrefix', 'lastVoucherSequence', 'EXP'),
        'refund': ('refundPrefix', 'lastRefundSequence', 'RFD'),
        'transaction': ('transactionPrefix', 'lastTransactionSequence', 'TXN'),
        'payroll': ('payrollPrefix', 'lastPayrollSequence', 'PAY'),
    }
    if document_type not in type_map:
        raise ValidationError('Unsupported finance document type.')
    prefix_field, sequence_field, fallback = type_map[document_type]

    if not _finance_configuration_available(required_columns=[prefix_field, sequence_field, 'includeDateSegment', 'sequencePadding']):
        return _legacy_doc_number(fallback)

    config_obj = _get_or_create_finance_configuration(
        school_id=school_id,
        session_id=session_id,
        user_obj=user_obj,
        lock=True,
    )
    if not config_obj:
        return _legacy_doc_number(fallback)
    next_sequence = int(getattr(config_obj, sequence_field) or 0) + 1
    setattr(config_obj, sequence_field, next_sequence)
    config_obj.lastEditedBy = _user_label(user_obj)
    config_obj.updatedByUserID = user_obj
    config_obj.save(update_fields=[sequence_field, 'lastEditedBy', 'updatedByUserID', 'lastUpdatedOn'])
    return _preview_doc_number(
        getattr(config_obj, prefix_field),
        next_sequence,
        include_date_segment=bool(config_obj.includeDateSegment),
        width=config_obj.sequencePadding,
    )


def preview_finance_document_number(*, document_type, school_id, session_id, user_obj=None):
    type_map = {
        'receipt': ('receiptPrefix', 'lastReceiptSequence', 'RCT'),
        'voucher': ('voucherPrefix', 'lastVoucherSequence', 'EXP'),
        'refund': ('refundPrefix', 'lastRefundSequence', 'RFD'),
        'transaction': ('transactionPrefix', 'lastTransactionSequence', 'TXN'),
        'payroll': ('payrollPrefix', 'lastPayrollSequence', 'PAY'),
    }
    if document_type not in type_map:
        raise ValidationError('Unsupported finance document type.')
    prefix_field, sequence_field, fallback = type_map[document_type]
    if not _finance_configuration_available(required_columns=[prefix_field, sequence_field, 'includeDateSegment', 'sequencePadding']):
        return _legacy_doc_number(fallback)
    config_obj = get_finance_configuration(school_id=school_id, session_id=session_id, user_obj=user_obj)
    if not config_obj:
        return _legacy_doc_number(fallback)
    return _preview_doc_number(
        getattr(config_obj, prefix_field, fallback),
        int(getattr(config_obj, sequence_field) or 0) + 1,
        include_date_segment=bool(config_obj.includeDateSegment),
        width=config_obj.sequencePadding,
    )


@transaction.atomic
def backfill_payroll_run_numbers(*, school_id=None, session_id=None, user_obj=None):
    run_qs = PayrollRun.objects.select_for_update().filter(
        isDeleted=False,
    ).filter(
        Q(payrollRunNo__isnull=True) | Q(payrollRunNo__exact='')
    ).order_by('schoolID_id', 'sessionID_id', 'year', 'month', 'runDate', 'id')

    if school_id:
        run_qs = run_qs.filter(schoolID_id=school_id)
    if session_id:
        run_qs = run_qs.filter(sessionID_id=session_id)

    updated = 0
    touched_school_sessions = set()
    for payroll_run in run_qs:
        if not payroll_run.schoolID_id or not payroll_run.sessionID_id:
            continue
        payroll_run.payrollRunNo = generate_finance_document_number(
            document_type='payroll',
            school_id=payroll_run.schoolID_id,
            session_id=payroll_run.sessionID_id,
            user_obj=user_obj,
        )
        payroll_run.lastEditedBy = _user_label(user_obj)
        payroll_run.updatedByUserID = user_obj
        payroll_run.save(update_fields=['payrollRunNo', 'lastEditedBy', 'updatedByUserID', 'lastUpdatedOn'])
        updated += 1
        touched_school_sessions.add((payroll_run.schoolID_id, payroll_run.sessionID_id))

    return {
        'updated': updated,
        'scopes': len(touched_school_sessions),
    }


@transaction.atomic
def backfill_finance_document_numbers(*, document_types=None, school_id=None, session_id=None, user_obj=None):
    requested_types = list(document_types or ['receipt', 'voucher', 'refund', 'transaction'])
    valid_types = {'receipt', 'voucher', 'refund', 'transaction', 'payroll'}
    invalid_types = sorted(set(requested_types) - valid_types)
    if invalid_types:
        raise ValidationError(f'Unsupported document type(s): {", ".join(invalid_types)}')

    result = {
        'receipt': 0,
        'voucher': 0,
        'refund': 0,
        'transaction': 0,
        'payroll': 0,
        'scopes': 0,
        'skipped': [],
    }
    touched_school_sessions = set()

    def _filtered_queryset(queryset, *, order_fields):
        if school_id:
            queryset = queryset.filter(schoolID_id=school_id)
        if session_id:
            queryset = queryset.filter(sessionID_id=session_id)
        return queryset.order_by(*order_fields)

    if 'receipt' in requested_types:
        receipt_qs = _filtered_queryset(
            PaymentReceipt.objects.select_for_update().filter(
                isDeleted=False,
            ).filter(
                Q(receiptNo__isnull=True) | Q(receiptNo__exact='')
            ),
            order_fields=['schoolID_id', 'sessionID_id', 'receiptDate', 'id'],
        )
        for receipt_obj in receipt_qs:
            if not receipt_obj.schoolID_id or not receipt_obj.sessionID_id:
                continue
            receipt_obj.receiptNo = generate_finance_document_number(
                document_type='receipt',
                school_id=receipt_obj.schoolID_id,
                session_id=receipt_obj.sessionID_id,
                user_obj=user_obj,
            )
            receipt_obj.lastEditedBy = _user_label(user_obj)
            receipt_obj.updatedByUserID = user_obj
            receipt_obj.save(update_fields=['receiptNo', 'lastEditedBy', 'updatedByUserID', 'lastUpdatedOn'])
            result['receipt'] += 1
            touched_school_sessions.add((receipt_obj.schoolID_id, receipt_obj.sessionID_id))

    if 'voucher' in requested_types:
        voucher_qs = _filtered_queryset(
            ExpenseVoucher.objects.select_for_update().filter(
                isDeleted=False,
            ).filter(
                Q(voucherNo__isnull=True) | Q(voucherNo__exact='')
            ),
            order_fields=['schoolID_id', 'sessionID_id', 'voucherDate', 'id'],
        )
        for voucher_obj in voucher_qs:
            if not voucher_obj.schoolID_id or not voucher_obj.sessionID_id:
                continue
            voucher_obj.voucherNo = generate_finance_document_number(
                document_type='voucher',
                school_id=voucher_obj.schoolID_id,
                session_id=voucher_obj.sessionID_id,
                user_obj=user_obj,
            )
            voucher_obj.lastEditedBy = _user_label(user_obj)
            voucher_obj.updatedByUserID = user_obj
            voucher_obj.save(update_fields=['voucherNo', 'lastEditedBy', 'updatedByUserID', 'lastUpdatedOn'])
            result['voucher'] += 1
            touched_school_sessions.add((voucher_obj.schoolID_id, voucher_obj.sessionID_id))

    if 'refund' in requested_types:
        if not _refund_tables_available():
            result['skipped'].append('refund')
        else:
            refund_qs = _filtered_queryset(
                PaymentRefund.objects.select_for_update().filter(
                    isDeleted=False,
                ).filter(
                    Q(refundNo__isnull=True) | Q(refundNo__exact='')
                ),
                order_fields=['schoolID_id', 'sessionID_id', 'refundDate', 'id'],
            )
            for refund_obj in refund_qs:
                if not refund_obj.schoolID_id or not refund_obj.sessionID_id:
                    continue
                refund_obj.refundNo = generate_finance_document_number(
                    document_type='refund',
                    school_id=refund_obj.schoolID_id,
                    session_id=refund_obj.sessionID_id,
                    user_obj=user_obj,
                )
                refund_obj.lastEditedBy = _user_label(user_obj)
                refund_obj.updatedByUserID = user_obj
                refund_obj.save(update_fields=['refundNo', 'lastEditedBy', 'updatedByUserID', 'lastUpdatedOn'])
                result['refund'] += 1
                touched_school_sessions.add((refund_obj.schoolID_id, refund_obj.sessionID_id))

    if 'transaction' in requested_types:
        txn_qs = _filtered_queryset(
            FinanceTransaction.objects.select_for_update().filter(
                isDeleted=False,
            ).filter(
                Q(txnNo__isnull=True) | Q(txnNo__exact='')
            ),
            order_fields=['schoolID_id', 'sessionID_id', 'txnDate', 'id'],
        )
        for txn_obj in txn_qs:
            if not txn_obj.schoolID_id or not txn_obj.sessionID_id:
                continue
            txn_obj.txnNo = generate_finance_document_number(
                document_type='transaction',
                school_id=txn_obj.schoolID_id,
                session_id=txn_obj.sessionID_id,
                user_obj=user_obj,
            )
            txn_obj.lastEditedBy = _user_label(user_obj)
            txn_obj.updatedByUserID = user_obj
            txn_obj.save(update_fields=['txnNo', 'lastEditedBy', 'updatedByUserID', 'lastUpdatedOn'])
            result['transaction'] += 1
            touched_school_sessions.add((txn_obj.schoolID_id, txn_obj.sessionID_id))

    if 'payroll' in requested_types:
        payroll_result = backfill_payroll_run_numbers(
            school_id=school_id,
            session_id=session_id,
            user_obj=user_obj,
        )
        result['payroll'] = payroll_result['updated']
        if payroll_result['updated'] > 0:
            result['scopes'] = max(result['scopes'], payroll_result['scopes'])

    result['scopes'] = max(result['scopes'], len(touched_school_sessions))
    return result


def validate_balanced_entries(entries):
    debit_total = Decimal('0.00')
    credit_total = Decimal('0.00')
    for entry in entries:
        amount = _money(entry.get('amount'))
        if amount <= 0:
            raise ValidationError('Ledger entry amounts must be greater than zero.')
        if entry.get('entryType') == 'debit':
            debit_total += amount
        elif entry.get('entryType') == 'credit':
            credit_total += amount
        else:
            raise ValidationError('Ledger entry type must be debit or credit.')
    if debit_total != credit_total:
        raise ValidationError('Ledger entries are not balanced.')
    return debit_total


def bootstrap_school_finance(*, school_id, session_id, user_obj=None):
    user_label = _user_label(user_obj)
    account_specs = {
        'STUDENT_RECEIVABLE': {
            'accountName': 'Student Receivable',
            'accountType': 'asset',
            'openingBalanceType': 'debit',
            'isControlAccount': True,
        },
        'CASH_ON_HAND': {
            'accountName': 'Cash on Hand',
            'accountType': 'asset',
            'openingBalanceType': 'debit',
        },
        'BANK_MAIN': {
            'accountName': 'Bank Account',
            'accountType': 'asset',
            'openingBalanceType': 'debit',
        },
        'ADMISSION_FEE_INCOME': {
            'accountName': 'Admission Fee Income',
            'accountType': 'income',
            'openingBalanceType': 'credit',
        },
        'TUITION_FEE_INCOME': {
            'accountName': 'Tuition Fee Income',
            'accountType': 'income',
            'openingBalanceType': 'credit',
        },
        'MISC_FEE_INCOME': {
            'accountName': 'Misc Fee Income',
            'accountType': 'income',
            'openingBalanceType': 'credit',
        },
        'EXPENSE_PAYABLE': {
            'accountName': 'Expense Payable',
            'accountType': 'liability',
            'openingBalanceType': 'credit',
        },
        'OFFICE_EXPENSE': {
            'accountName': 'Office Expense',
            'accountType': 'expense',
            'openingBalanceType': 'debit',
        },
        'UTILITY_EXPENSE': {
            'accountName': 'Utility Expense',
            'accountType': 'expense',
            'openingBalanceType': 'debit',
        },
        'SALARY_EXPENSE': {
            'accountName': 'Salary Expense',
            'accountType': 'expense',
            'openingBalanceType': 'debit',
        },
        'SALARY_PAYABLE': {
            'accountName': 'Salary Payable',
            'accountType': 'liability',
            'openingBalanceType': 'credit',
        },
    }
    accounts = {}
    for code, spec in account_specs.items():
        account_obj, created = FinanceAccount.objects.get_or_create(
            schoolID_id=school_id,
            sessionID_id=session_id,
            accountCode=code,
            isDeleted=False,
            defaults={
                **spec,
                'openingBalance': Decimal('0.00'),
                'description': '',
                'isSystemGenerated': True,
                'isActive': True,
                'lastEditedBy': user_label,
                'updatedByUserID': user_obj,
            },
        )
        if created:
            accounts[code] = account_obj
            continue
        changed = []
        for field, expected in spec.items():
            if getattr(account_obj, field) != expected:
                setattr(account_obj, field, expected)
                changed.append(field)
        if not account_obj.isActive:
            account_obj.isActive = True
            changed.append('isActive')
        if changed:
            if user_label:
                account_obj.lastEditedBy = user_label
                changed.append('lastEditedBy')
            if user_obj and account_obj.updatedByUserID_id != user_obj.id:
                account_obj.updatedByUserID = user_obj
                changed.append('updatedByUserID')
            account_obj.save(update_fields=list(dict.fromkeys(changed + ['lastUpdatedOn'])))
        accounts[code] = account_obj

    config_obj = _get_or_create_finance_configuration(
        school_id=school_id,
        session_id=session_id,
        user_obj=user_obj,
        default_cash_account=accounts['CASH_ON_HAND'],
        default_bank_account=accounts['BANK_MAIN'],
    )
    cash_account = (config_obj.defaultCashAccountID if config_obj else None) or accounts['CASH_ON_HAND']
    bank_account = (config_obj.defaultBankAccountID if config_obj else None) or accounts['BANK_MAIN']

    payment_mode_specs = {
        'CASH': ('Cash', 'cash', cash_account),
        'BANK': ('Bank', 'bank', bank_account),
        'UPI': ('UPI', 'upi', bank_account),
    }
    payment_modes = {}
    for code, (name, mode_type, linked_account) in payment_mode_specs.items():
        payment_mode, created = FinancePaymentMode.objects.get_or_create(
            schoolID_id=school_id,
            code=code,
            isDeleted=False,
            defaults={
                'sessionID_id': session_id,
                'name': name,
                'modeType': mode_type,
                'linkedAccountID': linked_account,
                'isActive': True,
                'lastEditedBy': user_label,
                'updatedByUserID': user_obj,
            },
        )
        if not created:
            changed = []
            if payment_mode.sessionID_id != session_id:
                payment_mode.sessionID_id = session_id
                changed.append('sessionID')
            if payment_mode.name != name:
                payment_mode.name = name
                changed.append('name')
            if payment_mode.modeType != mode_type:
                payment_mode.modeType = mode_type
                changed.append('modeType')
            if payment_mode.linkedAccountID_id != linked_account.id:
                payment_mode.linkedAccountID = linked_account
                changed.append('linkedAccountID')
            if not payment_mode.isActive:
                payment_mode.isActive = True
                changed.append('isActive')
            if changed:
                if user_label:
                    payment_mode.lastEditedBy = user_label
                    changed.append('lastEditedBy')
                if user_obj and payment_mode.updatedByUserID_id != user_obj.id:
                    payment_mode.updatedByUserID = user_obj
                    changed.append('updatedByUserID')
                payment_mode.save(update_fields=list(dict.fromkeys(changed + ['lastUpdatedOn'])))
        payment_modes[code] = payment_mode

    fee_head_specs = {
        'ADMISSION_FEE': ('Admission Fee', 'admission', accounts['ADMISSION_FEE_INCOME']),
        'MONTHLY_STUDENT_FEE': ('Monthly Student Fee', 'tuition', accounts['TUITION_FEE_INCOME']),
        'MISC_FEE': ('Misc Fee', 'misc', accounts['MISC_FEE_INCOME']),
    }
    fee_heads = {}
    for code, (name, category, income_account) in fee_head_specs.items():
        fee_head, created = FeeHead.objects.get_or_create(
            schoolID_id=school_id,
            sessionID_id=session_id,
            code=code,
            isDeleted=False,
            defaults={
                'name': name,
                'category': category,
                'defaultAmount': Decimal('0.00'),
                'isRecurring': code == 'MONTHLY_STUDENT_FEE',
                'recurrenceType': 'monthly' if code == 'MONTHLY_STUDENT_FEE' else 'one_time',
                'incomeAccountID': income_account,
                'receivableAccountID': accounts['STUDENT_RECEIVABLE'],
                'displayOrder': len(fee_heads) + 1,
                'isActive': True,
                'lastEditedBy': user_label,
                'updatedByUserID': user_obj,
            },
        )
        if not created:
            changed = []
            if fee_head.name != name:
                fee_head.name = name
                changed.append('name')
            if fee_head.category != category:
                fee_head.category = category
                changed.append('category')
            expected_recurring = code == 'MONTHLY_STUDENT_FEE'
            expected_recurrence = 'monthly' if code == 'MONTHLY_STUDENT_FEE' else 'one_time'
            if fee_head.isRecurring != expected_recurring:
                fee_head.isRecurring = expected_recurring
                changed.append('isRecurring')
            if fee_head.recurrenceType != expected_recurrence:
                fee_head.recurrenceType = expected_recurrence
                changed.append('recurrenceType')
            if fee_head.incomeAccountID_id != income_account.id:
                fee_head.incomeAccountID = income_account
                changed.append('incomeAccountID')
            if fee_head.receivableAccountID_id != accounts['STUDENT_RECEIVABLE'].id:
                fee_head.receivableAccountID = accounts['STUDENT_RECEIVABLE']
                changed.append('receivableAccountID')
            if not fee_head.isActive:
                fee_head.isActive = True
                changed.append('isActive')
            if changed:
                if user_label:
                    fee_head.lastEditedBy = user_label
                    changed.append('lastEditedBy')
                if user_obj and fee_head.updatedByUserID_id != user_obj.id:
                    fee_head.updatedByUserID = user_obj
                    changed.append('updatedByUserID')
                fee_head.save(update_fields=list(dict.fromkeys(changed + ['lastUpdatedOn'])))
        fee_heads[code] = fee_head

    return {
        'accounts': accounts,
        'payment_modes': payment_modes,
        'fee_heads': fee_heads,
        'configuration': config_obj,
    }


def bootstrap_expense_categories(*, school_id, session_id, user_obj=None):
    setup = bootstrap_school_finance(school_id=school_id, session_id=session_id, user_obj=user_obj)
    user_label = _user_label(user_obj)
    category_specs = {
        'OFFICE': ('Office Expense', setup['accounts']['OFFICE_EXPENSE']),
        'UTILITY': ('Utility Expense', setup['accounts']['UTILITY_EXPENSE']),
    }
    categories = {}
    for code, (name, expense_account) in category_specs.items():
        category, created = ExpenseCategory.objects.get_or_create(
            schoolID_id=school_id,
            sessionID_id=session_id,
            code=code,
            isDeleted=False,
            defaults={
                'name': name,
                'expenseAccountID': expense_account,
                'payableAccountID': setup['accounts']['EXPENSE_PAYABLE'],
                'isActive': True,
                'lastEditedBy': user_label,
                'updatedByUserID': user_obj,
            },
        )
        if not created:
            changed = []
            if category.name != name:
                category.name = name
                changed.append('name')
            if category.expenseAccountID_id != expense_account.id:
                category.expenseAccountID = expense_account
                changed.append('expenseAccountID')
            if category.payableAccountID_id != setup['accounts']['EXPENSE_PAYABLE'].id:
                category.payableAccountID = setup['accounts']['EXPENSE_PAYABLE']
                changed.append('payableAccountID')
            if not category.isActive:
                category.isActive = True
                changed.append('isActive')
            if changed:
                if user_label:
                    category.lastEditedBy = user_label
                    changed.append('lastEditedBy')
                if user_obj and category.updatedByUserID_id != user_obj.id:
                    category.updatedByUserID = user_obj
                    changed.append('updatedByUserID')
                category.save(update_fields=list(dict.fromkeys(changed + ['lastUpdatedOn'])))
        categories[code] = category
    return {'setup': setup, 'categories': categories}


def ensure_student_party(*, student_obj, school_id, session_id, user_obj=None):
    display_name = student_obj.name or student_obj.registrationCode or f'Student #{student_obj.pk}'
    party_obj, created = FinanceParty.objects.get_or_create(
        schoolID_id=school_id,
        sessionID_id=session_id,
        studentID_id=student_obj.id,
        isDeleted=False,
        defaults={
            'partyType': 'student',
            'displayName': display_name,
            'phoneNumber': student_obj.phoneNumber,
            'email': student_obj.email,
            'isActive': True,
            'lastEditedBy': _user_label(user_obj),
            'updatedByUserID': user_obj,
        },
    )
    if not created:
        changed = []
        if party_obj.partyType != 'student':
            party_obj.partyType = 'student'
            changed.append('partyType')
        if party_obj.displayName != display_name:
            party_obj.displayName = display_name
            changed.append('displayName')
        if party_obj.phoneNumber != student_obj.phoneNumber:
            party_obj.phoneNumber = student_obj.phoneNumber
            changed.append('phoneNumber')
        if party_obj.email != student_obj.email:
            party_obj.email = student_obj.email
            changed.append('email')
        if not party_obj.isActive:
            party_obj.isActive = True
            changed.append('isActive')
        if changed:
            user_label = _user_label(user_obj)
            if user_label:
                party_obj.lastEditedBy = user_label
                changed.append('lastEditedBy')
            if user_obj and party_obj.updatedByUserID_id != user_obj.id:
                party_obj.updatedByUserID = user_obj
                changed.append('updatedByUserID')
            party_obj.save(update_fields=list(dict.fromkeys(changed + ['lastUpdatedOn'])))
    return party_obj


def ensure_parent_party(*, parent_obj, school_id, session_id, user_obj=None):
    if not parent_obj:
        return None
    display_name = parent_obj.fatherName or parent_obj.motherName or parent_obj.guardianName or f'Parent #{parent_obj.pk}'
    party_obj, created = FinanceParty.objects.get_or_create(
        schoolID_id=school_id,
        sessionID_id=session_id,
        parentID_id=parent_obj.id,
        isDeleted=False,
        defaults={
            'partyType': 'parent',
            'displayName': display_name,
            'phoneNumber': parent_obj.phoneNumber or parent_obj.fatherPhone or parent_obj.motherPhone,
            'email': parent_obj.email or parent_obj.fatherEmail or parent_obj.motherEmail,
            'isActive': True,
            'lastEditedBy': _user_label(user_obj),
            'updatedByUserID': user_obj,
        },
    )
    if not created:
        changed = []
        if party_obj.partyType != 'parent':
            party_obj.partyType = 'parent'
            changed.append('partyType')
        if party_obj.displayName != display_name:
            party_obj.displayName = display_name
            changed.append('displayName')
        phone_value = parent_obj.phoneNumber or parent_obj.fatherPhone or parent_obj.motherPhone
        email_value = parent_obj.email or parent_obj.fatherEmail or parent_obj.motherEmail
        if party_obj.phoneNumber != phone_value:
            party_obj.phoneNumber = phone_value
            changed.append('phoneNumber')
        if party_obj.email != email_value:
            party_obj.email = email_value
            changed.append('email')
        if not party_obj.isActive:
            party_obj.isActive = True
            changed.append('isActive')
        if changed:
            user_label = _user_label(user_obj)
            if user_label:
                party_obj.lastEditedBy = user_label
                changed.append('lastEditedBy')
            if user_obj and party_obj.updatedByUserID_id != user_obj.id:
                party_obj.updatedByUserID = user_obj
                changed.append('updatedByUserID')
            party_obj.save(update_fields=list(dict.fromkeys(changed + ['lastUpdatedOn'])))
    return party_obj


def ensure_named_party(*, school_id, session_id, display_name, party_type='vendor', phone_number='', email='', user_obj=None):
    display_name = (display_name or '').strip()
    if not display_name:
        return None
    party_obj, created = FinanceParty.objects.get_or_create(
        schoolID_id=school_id,
        sessionID_id=session_id,
        partyType=party_type,
        displayName=display_name,
        isDeleted=False,
        defaults={
            'phoneNumber': phone_number or None,
            'email': email or None,
            'isActive': True,
            'lastEditedBy': _user_label(user_obj),
            'updatedByUserID': user_obj,
        },
    )
    if not created:
        changed = []
        if phone_number and party_obj.phoneNumber != phone_number:
            party_obj.phoneNumber = phone_number
            changed.append('phoneNumber')
        if email and party_obj.email != email:
            party_obj.email = email
            changed.append('email')
        if not party_obj.isActive:
            party_obj.isActive = True
            changed.append('isActive')
        if changed:
            if user_obj and party_obj.updatedByUserID_id != user_obj.id:
                party_obj.updatedByUserID = user_obj
                changed.append('updatedByUserID')
            party_obj.lastEditedBy = _user_label(user_obj)
            changed.append('lastEditedBy')
            party_obj.save(update_fields=list(dict.fromkeys(changed + ['lastUpdatedOn'])))
    return party_obj


def ensure_teacher_party(*, teacher_obj, school_id, session_id, user_obj=None):
    if not teacher_obj:
        return None
    display_name = teacher_obj.name or teacher_obj.employeeCode or f'Teacher #{teacher_obj.pk}'
    party_obj, created = FinanceParty.objects.get_or_create(
        schoolID_id=school_id,
        sessionID_id=session_id,
        teacherID_id=teacher_obj.id,
        isDeleted=False,
        defaults={
            'partyType': 'teacher',
            'displayName': display_name,
            'phoneNumber': teacher_obj.phoneNumber,
            'email': teacher_obj.email,
            'isActive': True,
            'lastEditedBy': _user_label(user_obj),
            'updatedByUserID': user_obj,
        },
    )
    if not created:
        changed = []
        if party_obj.partyType != 'teacher':
            party_obj.partyType = 'teacher'
            changed.append('partyType')
        if party_obj.displayName != display_name:
            party_obj.displayName = display_name
            changed.append('displayName')
        if party_obj.phoneNumber != teacher_obj.phoneNumber:
            party_obj.phoneNumber = teacher_obj.phoneNumber
            changed.append('phoneNumber')
        if party_obj.email != teacher_obj.email:
            party_obj.email = teacher_obj.email
            changed.append('email')
        if not party_obj.isActive:
            party_obj.isActive = True
            changed.append('isActive')
        if changed:
            user_label = _user_label(user_obj)
            if user_label:
                party_obj.lastEditedBy = user_label
                changed.append('lastEditedBy')
            if user_obj and party_obj.updatedByUserID_id != user_obj.id:
                party_obj.updatedByUserID = user_obj
                changed.append('updatedByUserID')
            party_obj.save(update_fields=list(dict.fromkeys(changed + ['lastUpdatedOn'])))
    return party_obj


@transaction.atomic
def upsert_finance_transaction(
    *,
    school_id,
    session_id,
    txn_type,
    txn_date,
    source_module,
    source_record_id,
    description='',
    reference_no='',
    entries,
    user_obj=None,
):
    validate_balanced_entries(entries)
    user_label = _user_label(user_obj)
    finance_txn = FinanceTransaction.objects.select_for_update().filter(
        schoolID_id=school_id,
        sourceModule=source_module,
        sourceRecordID=str(source_record_id),
        isDeleted=False,
    ).first()
    if finance_txn:
        finance_txn.sessionID_id = session_id
        finance_txn.txnDate = txn_date
        finance_txn.txnType = txn_type
        finance_txn.referenceNo = reference_no
        finance_txn.description = description
        finance_txn.status = 'posted'
        finance_txn.lastEditedBy = user_label
        finance_txn.updatedByUserID = user_obj
        finance_txn.save(update_fields=[
            'sessionID', 'txnDate', 'txnType', 'referenceNo', 'description',
            'status', 'lastEditedBy', 'updatedByUserID', 'lastUpdatedOn'
        ])
        finance_txn.entries.all().delete()
    else:
        finance_txn = FinanceTransaction.objects.create(
            schoolID_id=school_id,
            sessionID_id=session_id,
            txnNo=generate_finance_document_number(
                document_type='transaction',
                school_id=school_id,
                session_id=session_id,
                user_obj=user_obj,
            ),
            txnDate=txn_date,
            txnType=txn_type,
            referenceNo=reference_no,
            description=description,
            status='posted',
            sourceModule=source_module,
            sourceRecordID=str(source_record_id),
            lastEditedBy=user_label,
            updatedByUserID=user_obj,
        )
    FinanceEntry.objects.bulk_create([
        FinanceEntry(
            transactionID=finance_txn,
            accountID=entry['accountID'],
            partyID=entry.get('partyID'),
            entryType=entry['entryType'],
            amount=_money(entry['amount']),
            narration=entry.get('narration') or '',
            lineOrder=index + 1,
        )
        for index, entry in enumerate(entries)
    ])
    return finance_txn


@transaction.atomic
def clear_finance_transaction(*, school_id, source_module, source_record_id, user_obj=None):
    finance_txn = FinanceTransaction.objects.select_for_update().filter(
        schoolID_id=school_id,
        sourceModule=source_module,
        sourceRecordID=str(source_record_id),
        isDeleted=False,
    ).first()
    if not finance_txn:
        return None
    finance_txn.entries.all().delete()
    finance_txn.isDeleted = True
    finance_txn.status = 'reversed'
    finance_txn.lastEditedBy = _user_label(user_obj)
    finance_txn.updatedByUserID = user_obj
    finance_txn.save(update_fields=['isDeleted', 'status', 'lastEditedBy', 'updatedByUserID', 'lastUpdatedOn'])
    return finance_txn


def _refresh_charge_paid_state(charge_obj, user_obj=None):
    receipt_total = charge_obj.receiptAllocations.filter(
        receiptID__isDeleted=False,
        receiptID__status='confirmed',
    ).aggregate(total=Sum('allocatedAmount')).get('total') or Decimal('0.00')
    refund_total = Decimal('0.00')
    if _refund_tables_available():
        refund_total = charge_obj.refundAllocations.filter(
            refundID__isDeleted=False,
            refundID__status='confirmed',
        ).aggregate(total=Sum('refundedAmount')).get('total') or Decimal('0.00')
    paid_total = _money(receipt_total) - _money(refund_total)
    if paid_total < 0:
        paid_total = Decimal('0.00')
    balance = _money(charge_obj.netAmount) - paid_total
    if balance <= 0:
        balance = Decimal('0.00')
        status = 'paid'
    elif paid_total > 0:
        status = 'partial'
    else:
        status = 'posted'
    charge_obj.paidAmount = paid_total
    charge_obj.balanceAmount = balance
    charge_obj.status = status
    charge_obj.lastEditedBy = _user_label(user_obj)
    charge_obj.updatedByUserID = user_obj
    charge_obj.save(update_fields=[
        'paidAmount', 'balanceAmount', 'status', 'lastEditedBy', 'updatedByUserID', 'lastUpdatedOn'
    ])
    return charge_obj


def refresh_student_charge_balance(*, charge_obj, user_obj=None):
    if not charge_obj:
        raise ValidationError('Charge could not be found.')
    return _refresh_charge_paid_state(charge_obj, user_obj=user_obj)


@transaction.atomic
def reverse_finance_transaction(
    *,
    school_id,
    session_id,
    source_module,
    source_record_id,
    reversal_source_module,
    reversal_source_record_id,
    reversal_date,
    description='',
    reference_no='',
    user_obj=None,
):
    original_txn = FinanceTransaction.objects.select_for_update().filter(
        schoolID_id=school_id,
        sourceModule=source_module,
        sourceRecordID=str(source_record_id),
        isDeleted=False,
    ).prefetch_related('entries').first()
    if not original_txn:
        return None

    reverse_entries = []
    for entry in original_txn.entries.all().order_by('lineOrder', 'id'):
        reverse_entries.append({
            'accountID': entry.accountID,
            'partyID': entry.partyID,
            'entryType': 'credit' if entry.entryType == 'debit' else 'debit',
            'amount': entry.amount,
            'narration': description or entry.narration or original_txn.referenceNo or original_txn.txnNo,
        })

    reversal_txn = upsert_finance_transaction(
        school_id=school_id,
        session_id=session_id,
        txn_type='reversal',
        txn_date=reversal_date,
        source_module=reversal_source_module,
        source_record_id=reversal_source_record_id,
        description=description or f'Reversal for {original_txn.referenceNo or original_txn.txnNo}',
        reference_no=reference_no or original_txn.referenceNo or '',
        entries=reverse_entries,
        user_obj=user_obj,
    )
    if reversal_txn.reversalOfID_id != original_txn.id:
        reversal_txn.reversalOfID = original_txn
        reversal_txn.save(update_fields=['reversalOfID'])
    if original_txn.status != 'reversed':
        original_txn.status = 'reversed'
        original_txn.lastEditedBy = _user_label(user_obj)
        original_txn.updatedByUserID = user_obj
        original_txn.save(update_fields=['status', 'lastEditedBy', 'updatedByUserID', 'lastUpdatedOn'])
    return reversal_txn


@transaction.atomic
def create_manual_payment_receipt(
    *,
    school_id,
    session_id,
    student_obj,
    receipt_date,
    payment_mode_obj,
    allocations,
    received_from_name='',
    reference_no='',
    notes='',
    status='confirmed',
    requested_status=None,
    user_obj=None,
):
    if not student_obj:
        raise ValidationError('Student is required.')
    if not payment_mode_obj:
        raise ValidationError('Payment mode is required.')
    if not allocations:
        raise ValidationError('At least one allocation is required.')

    bootstrap_school_finance(school_id=school_id, session_id=session_id, user_obj=user_obj)
    receipt_party = ensure_parent_party(
        parent_obj=getattr(student_obj, 'parentID', None),
        school_id=school_id,
        session_id=session_id,
        user_obj=user_obj,
    ) or ensure_student_party(
        student_obj=student_obj,
        school_id=school_id,
        session_id=session_id,
        user_obj=user_obj,
    )

    charge_ids = [row.get('charge_id') for row in allocations if row.get('charge_id')]
    charge_map = {
        charge.id: charge
        for charge in StudentCharge.objects.select_for_update().select_related('feeHeadID', 'partyID').filter(
            schoolID_id=school_id,
            sessionID_id=session_id,
            studentID_id=student_obj.id,
            id__in=charge_ids,
            isDeleted=False,
        )
    }

    allocation_rows = []
    total_amount = Decimal('0.00')
    for row in allocations:
        charge_id = int(row.get('charge_id') or 0)
        amount = _money(row.get('amount'))
        if amount <= 0:
            continue
        charge_obj = charge_map.get(charge_id)
        if not charge_obj:
            raise ValidationError('One or more selected charges could not be found.')
        if charge_obj.status in {'cancelled'}:
            raise ValidationError(f'Charge "{charge_obj.title}" cannot accept receipts.')
        available_balance = _money(charge_obj.balanceAmount)
        if amount > available_balance:
            raise ValidationError(f'Allocation for "{charge_obj.title}" exceeds its balance.')
        allocation_rows.append((charge_obj, amount))
        total_amount += amount

    if not allocation_rows or total_amount <= 0:
        raise ValidationError('Enter at least one valid allocation amount.')

    requested_status = (requested_status or status or 'draft').strip()
    receipt_status = (status or 'draft').strip()

    receipt_obj = PaymentReceipt.objects.create(
        schoolID_id=school_id,
        sessionID_id=session_id,
        receiptNo=generate_finance_document_number(
            document_type='receipt',
            school_id=school_id,
            session_id=session_id,
            user_obj=user_obj,
        ),
        receiptDate=receipt_date,
        partyID=receipt_party,
        studentID=student_obj,
        receivedFromName=received_from_name or receipt_party.displayName,
        paymentModeID=payment_mode_obj,
        depositAccountID=payment_mode_obj.linkedAccountID,
        amountReceived=total_amount,
        referenceNo=reference_no,
        notes=notes or '',
        status=receipt_status,
        requestedApprovalStatus=requested_status,
        sourceModule='finance_manual_receipt',
        sourceRecordID='',
        lastEditedBy=_user_label(user_obj),
        updatedByUserID=user_obj,
    )
    receipt_obj.sourceRecordID = str(receipt_obj.id)
    receipt_obj.save(update_fields=['sourceRecordID'])

    for charge_obj, amount in allocation_rows:
        PaymentReceiptAllocation.objects.create(
            receiptID=receipt_obj,
            studentChargeID=charge_obj,
            allocatedAmount=amount,
            fineComponent=Decimal('0.00'),
            discountComponent=Decimal('0.00'),
            note=notes or '',
        )
    if receipt_obj.status == 'confirmed':
        approve_payment_receipt(
            receipt_obj=receipt_obj,
            school_id=school_id,
            session_id=session_id,
            user_obj=user_obj,
        )
    return receipt_obj


@transaction.atomic
def reverse_payment_receipt(
    *,
    receipt_obj,
    school_id,
    session_id,
    reason='',
    user_obj=None,
):
    if not receipt_obj or receipt_obj.isDeleted:
        raise ValidationError('Receipt could not be found.')
    if receipt_obj.status != 'confirmed':
        raise ValidationError('Only confirmed receipts can be reversed.')
    if _refund_tables_available() and receipt_obj.refunds.filter(isDeleted=False, status='confirmed').exists():
        raise ValidationError('This receipt already has confirmed refunds. Reverse the refund entries first or use receipt history instead.')

    reverse_finance_transaction(
        school_id=school_id,
        session_id=session_id,
        source_module=receipt_obj.sourceModule or 'finance_manual_receipt',
        source_record_id=receipt_obj.sourceRecordID or receipt_obj.id,
        reversal_source_module='finance_receipt_reversal',
        reversal_source_record_id=receipt_obj.id,
        reversal_date=receipt_obj.receiptDate,
        description=reason or f'Reversal for receipt {receipt_obj.receiptNo}',
        reference_no=receipt_obj.receiptNo,
        user_obj=user_obj,
    )

    receipt_obj.status = 'reversed'
    receipt_obj.notes = '\n'.join(filter(None, [
        (receipt_obj.notes or '').strip(),
        f'Reversed: {reason}' if reason else 'Reversed from finance module',
    ])).strip()
    receipt_obj.lastEditedBy = _user_label(user_obj)
    receipt_obj.updatedByUserID = user_obj
    receipt_obj.save(update_fields=['status', 'notes', 'lastEditedBy', 'updatedByUserID', 'lastUpdatedOn'])

    for charge_obj in StudentCharge.objects.filter(
        id__in=receipt_obj.allocations.values_list('studentChargeID', flat=True),
        isDeleted=False,
    ):
        _refresh_charge_paid_state(charge_obj, user_obj=user_obj)
    return receipt_obj


@transaction.atomic
def create_payment_refund(
    *,
    receipt_obj,
    school_id,
    session_id,
    refund_date,
    payment_mode_obj,
    allocations,
    reference_no='',
    notes='',
    status='confirmed',
    requested_status=None,
    user_obj=None,
):
    if not _refund_tables_available():
        raise ValidationError('Refund tables are not available yet. Run migrations before using refunds.')
    if not receipt_obj or receipt_obj.isDeleted or receipt_obj.status != 'confirmed':
        raise ValidationError('Only confirmed receipts can be refunded.')
    if not payment_mode_obj or not payment_mode_obj.linkedAccountID_id:
        raise ValidationError('A valid refund payment mode is required.')
    if not allocations:
        raise ValidationError('At least one refund allocation is required.')

    charge_allocations = {
        row.studentChargeID_id: row
        for row in receipt_obj.allocations.select_related('studentChargeID', 'studentChargeID__feeHeadID', 'studentChargeID__partyID').all()
    }
    existing_refunds = {
        row['studentChargeID']: _money(row['total'])
        for row in PaymentRefundAllocation.objects.filter(
            refundID__receiptID=receipt_obj,
            refundID__isDeleted=False,
            refundID__status='confirmed',
        ).values('studentChargeID').annotate(total=Sum('refundedAmount'))
    }

    normalized = []
    total_refund = Decimal('0.00')
    for row in allocations:
        charge_id = int(row.get('charge_id') or 0)
        amount = _money(row.get('amount'))
        if amount <= 0:
            continue
        receipt_alloc = charge_allocations.get(charge_id)
        if not receipt_alloc:
            raise ValidationError('Refund allocation must map to an existing receipt allocation.')
        refundable_balance = _money(receipt_alloc.allocatedAmount) - existing_refunds.get(charge_id, Decimal('0.00'))
        if amount > refundable_balance:
            raise ValidationError(f'Refund for "{receipt_alloc.studentChargeID.title}" exceeds the remaining refundable amount.')
        normalized.append((receipt_alloc.studentChargeID, amount))
        total_refund += amount

    if not normalized or total_refund <= 0:
        raise ValidationError('Enter at least one valid refund amount.')

    requested_status = (requested_status or status or 'draft').strip()
    refund_status = (status or 'draft').strip()

    refund_obj = PaymentRefund.objects.create(
        schoolID_id=school_id,
        sessionID_id=session_id,
        refundNo=generate_finance_document_number(
            document_type='refund',
            school_id=school_id,
            session_id=session_id,
            user_obj=user_obj,
        ),
        refundDate=refund_date,
        receiptID=receipt_obj,
        partyID=receipt_obj.partyID,
        studentID=receipt_obj.studentID,
        paymentModeID=payment_mode_obj,
        payoutAccountID=payment_mode_obj.linkedAccountID,
        amountRefunded=total_refund,
        referenceNo=reference_no,
        notes=notes or '',
        status=refund_status,
        requestedApprovalStatus=requested_status,
        sourceModule='finance_receipt_refund',
        sourceRecordID='',
        lastEditedBy=_user_label(user_obj),
        updatedByUserID=user_obj,
    )
    refund_obj.sourceRecordID = str(refund_obj.id)
    refund_obj.save(update_fields=['sourceRecordID'])

    for charge_obj, amount in normalized:
        PaymentRefundAllocation.objects.create(
            refundID=refund_obj,
            studentChargeID=charge_obj,
            refundedAmount=amount,
            note=notes or '',
        )
    if refund_obj.status == 'confirmed':
        approve_payment_refund(
            refund_obj=refund_obj,
            school_id=school_id,
            session_id=session_id,
            user_obj=user_obj,
        )
    return refund_obj


@transaction.atomic
def generate_payroll_run(
    *,
    school_id,
    session_id,
    month,
    year,
    run_date,
    teacher_ids=None,
    status='processed',
    requested_status=None,
    user_obj=None,
):
    setup = bootstrap_school_finance(school_id=school_id, session_id=session_id, user_obj=user_obj)
    requested_status = (requested_status or status or 'draft').strip()
    effective_status = (status or 'draft').strip()
    payroll_run, _ = PayrollRun.objects.get_or_create(
        schoolID_id=school_id,
        sessionID_id=session_id,
        month=month,
        year=year,
        isDeleted=False,
        defaults={
            'runDate': run_date,
            'status': 'draft',
            'requestedApprovalStatus': requested_status,
            'payrollRunNo': generate_finance_document_number(
                document_type='payroll',
                school_id=school_id,
                session_id=session_id,
                user_obj=user_obj,
            ),
            'lastEditedBy': _user_label(user_obj),
            'updatedByUserID': user_obj,
        }
    )
    if not payroll_run.payrollRunNo:
        payroll_run.payrollRunNo = generate_finance_document_number(
            document_type='payroll',
            school_id=school_id,
            session_id=session_id,
            user_obj=user_obj,
        )
    payroll_run.runDate = run_date
    payroll_run.status = effective_status
    payroll_run.requestedApprovalStatus = requested_status
    payroll_run.lastEditedBy = _user_label(user_obj)
    payroll_run.updatedByUserID = user_obj
    payroll_run.save(update_fields=['payrollRunNo', 'runDate', 'status', 'requestedApprovalStatus', 'lastEditedBy', 'updatedByUserID', 'lastUpdatedOn'])

    teacher_qs = TeacherDetail.objects.filter(
        schoolID_id=school_id,
        sessionID_id=session_id,
        isDeleted=False,
        isActive='Yes',
    ).order_by('name', 'id')
    if teacher_ids:
        teacher_qs = teacher_qs.filter(id__in=list(teacher_ids))

    teacher_map = {}
    for teacher_obj in teacher_qs:
        salary_amount = _money(getattr(teacher_obj, 'salary', 0))
        if salary_amount <= 0:
            continue
        party_obj = ensure_teacher_party(
            teacher_obj=teacher_obj,
            school_id=school_id,
            session_id=session_id,
            user_obj=user_obj,
        )
        teacher_map[teacher_obj.id] = True
        PayrollLine.objects.update_or_create(
            payrollRunID=payroll_run,
            partyID=party_obj,
            defaults={
                'teacherID': teacher_obj,
                'basicAmount': salary_amount,
                'allowanceAmount': Decimal('0.00'),
                'deductionAmount': Decimal('0.00'),
                'advanceRecoveryAmount': Decimal('0.00'),
                'netAmount': salary_amount,
                'salaryExpenseAccountID': setup['accounts']['SALARY_EXPENSE'],
                'salaryPayableAccountID': setup['accounts']['SALARY_PAYABLE'],
                'paymentStatus': 'pending',
                'requestedPaymentStatus': 'pending',
                'paymentModeID': None,
                'paymentDate': None,
            },
        )

    for line in payroll_run.payrollLines.exclude(teacherID_id__in=list(teacher_map.keys())).all():
        if line.paymentStatus != 'paid':
            line.basicAmount = Decimal('0.00')
            line.allowanceAmount = Decimal('0.00')
            line.deductionAmount = Decimal('0.00')
            line.advanceRecoveryAmount = Decimal('0.00')
            line.netAmount = Decimal('0.00')
            line.paymentStatus = 'hold'
            line.requestedPaymentStatus = 'hold'
            line.paymentModeID = None
            line.paymentDate = None
            line.save(update_fields=['basicAmount', 'allowanceAmount', 'deductionAmount', 'advanceRecoveryAmount', 'netAmount', 'paymentStatus', 'requestedPaymentStatus', 'paymentModeID', 'paymentDate', 'lastUpdatedOn'])

    return payroll_run


@transaction.atomic
def approve_payment_receipt(*, receipt_obj, school_id, session_id, user_obj=None):
    if not receipt_obj or receipt_obj.isDeleted:
        raise ValidationError('Receipt could not be found.')
    if receipt_obj.status not in {'draft', 'submitted', 'confirmed'}:
        raise ValidationError('Only draft or submitted receipts can be approved.')
    if receipt_obj.status == 'confirmed':
        return receipt_obj

    credit_entries = {}
    total_amount = Decimal('0.00')
    receipt_party = receipt_obj.partyID
    allocation_rows = list(
        receipt_obj.allocations.select_related('studentChargeID', 'studentChargeID__feeHeadID', 'studentChargeID__partyID').all()
    )
    if not allocation_rows:
        raise ValidationError('Receipt has no allocations to confirm.')

    for row in allocation_rows:
        charge_obj = row.studentChargeID
        amount = _money(row.allocatedAmount)
        total_amount += amount
        _refresh_charge_paid_state(charge_obj, user_obj=user_obj)
        receivable_account = charge_obj.feeHeadID.receivableAccountID if charge_obj.feeHeadID_id else None
        if not receivable_account:
            raise ValidationError(f'Receivable account is missing for "{charge_obj.title}".')
        key = (receivable_account.id, charge_obj.partyID_id)
        credit_entries.setdefault(key, {
            'accountID': receivable_account,
            'partyID': charge_obj.partyID,
            'entryType': 'credit',
            'amount': Decimal('0.00'),
            'narration': receipt_obj.receiptNo,
        })
        credit_entries[key]['amount'] += amount

    ledger_entries = [{
        'accountID': receipt_obj.depositAccountID,
        'partyID': receipt_party,
        'entryType': 'debit',
        'amount': total_amount,
        'narration': receipt_obj.receiptNo,
    }] + list(credit_entries.values())

    upsert_finance_transaction(
        school_id=school_id,
        session_id=session_id,
        txn_type='student_receipt',
        txn_date=receipt_obj.receiptDate,
        source_module=receipt_obj.sourceModule or 'finance_manual_receipt',
        source_record_id=receipt_obj.sourceRecordID or receipt_obj.id,
        description=receipt_obj.notes or f'Manual receipt {receipt_obj.receiptNo}',
        reference_no=receipt_obj.receiptNo,
        entries=ledger_entries,
        user_obj=user_obj,
    )
    receipt_obj.status = 'confirmed'
    receipt_obj.lastEditedBy = _user_label(user_obj)
    receipt_obj.updatedByUserID = user_obj
    receipt_obj.save(update_fields=['status', 'lastEditedBy', 'updatedByUserID', 'lastUpdatedOn'])
    return receipt_obj


@transaction.atomic
def approve_payment_refund(*, refund_obj, school_id, session_id, user_obj=None):
    if not _refund_tables_available():
        raise ValidationError('Refund tables are not available yet. Run migrations before using refunds.')
    if not refund_obj or refund_obj.isDeleted:
        raise ValidationError('Refund could not be found.')
    if refund_obj.status not in {'draft', 'submitted', 'confirmed'}:
        raise ValidationError('Only draft or submitted refunds can be approved.')
    if refund_obj.status == 'confirmed':
        return refund_obj

    debit_entries = {}
    total_refund = Decimal('0.00')
    allocation_rows = list(
        refund_obj.allocations.select_related('studentChargeID', 'studentChargeID__feeHeadID', 'studentChargeID__partyID').all()
    )
    if not allocation_rows:
        raise ValidationError('Refund has no allocations to confirm.')

    for row in allocation_rows:
        charge_obj = row.studentChargeID
        amount = _money(row.refundedAmount)
        total_refund += amount
        _refresh_charge_paid_state(charge_obj, user_obj=user_obj)
        receivable_account = charge_obj.feeHeadID.receivableAccountID if charge_obj.feeHeadID_id else None
        if not receivable_account:
            raise ValidationError(f'Receivable account is missing for "{charge_obj.title}".')
        key = (receivable_account.id, charge_obj.partyID_id)
        debit_entries.setdefault(key, {
            'accountID': receivable_account,
            'partyID': charge_obj.partyID,
            'entryType': 'debit',
            'amount': Decimal('0.00'),
            'narration': refund_obj.refundNo,
        })
        debit_entries[key]['amount'] += amount

    ledger_entries = list(debit_entries.values()) + [{
        'accountID': refund_obj.payoutAccountID,
        'partyID': refund_obj.partyID,
        'entryType': 'credit',
        'amount': total_refund,
        'narration': refund_obj.refundNo,
    }]

    upsert_finance_transaction(
        school_id=school_id,
        session_id=session_id,
        txn_type='refund',
        txn_date=refund_obj.refundDate,
        source_module=refund_obj.sourceModule or 'finance_receipt_refund',
        source_record_id=refund_obj.sourceRecordID or refund_obj.id,
        description=refund_obj.notes or f'Refund for receipt {refund_obj.receiptID.receiptNo}',
        reference_no=refund_obj.refundNo,
        entries=ledger_entries,
        user_obj=user_obj,
    )
    refund_obj.status = 'confirmed'
    refund_obj.lastEditedBy = _user_label(user_obj)
    refund_obj.updatedByUserID = user_obj
    refund_obj.save(update_fields=['status', 'lastEditedBy', 'updatedByUserID', 'lastUpdatedOn'])
    return refund_obj


@transaction.atomic
def post_payroll_run(*, payroll_run_obj, school_id, session_id, user_obj=None):
    if payroll_run_obj.status == 'paid':
        raise ValidationError('A fully paid payroll run cannot be reposted.')
    lines = list(payroll_run_obj.payrollLines.filter(netAmount__gt=0))
    total_amount = sum((_money(line.netAmount) for line in lines), Decimal('0.00'))
    if total_amount <= 0:
        raise ValidationError('Payroll run has no payable lines to post.')

    setup = bootstrap_school_finance(school_id=school_id, session_id=session_id, user_obj=user_obj)
    upsert_finance_transaction(
        school_id=school_id,
        session_id=session_id,
        txn_type='payroll_accrual',
        txn_date=payroll_run_obj.runDate,
        source_module='payroll_run_accrual',
        source_record_id=payroll_run_obj.id,
        description=f'Payroll accrual for {payroll_run_obj.month:02d}/{payroll_run_obj.year}',
        reference_no=payroll_run_obj.payrollRunNo or f'PAY-{payroll_run_obj.month:02d}-{payroll_run_obj.year}',
        entries=[
            {
                'accountID': setup['accounts']['SALARY_EXPENSE'],
                'entryType': 'debit',
                'amount': total_amount,
                'narration': payroll_run_obj.payrollRunNo or f'Payroll accrual {payroll_run_obj.month:02d}/{payroll_run_obj.year}',
            },
            {
                'accountID': setup['accounts']['SALARY_PAYABLE'],
                'entryType': 'credit',
                'amount': total_amount,
                'narration': payroll_run_obj.payrollRunNo or f'Payroll accrual {payroll_run_obj.month:02d}/{payroll_run_obj.year}',
            },
        ],
        user_obj=user_obj,
    )
    payroll_run_obj.status = 'posted'
    payroll_run_obj.lastEditedBy = _user_label(user_obj)
    payroll_run_obj.updatedByUserID = user_obj
    payroll_run_obj.save(update_fields=['status', 'lastEditedBy', 'updatedByUserID', 'lastUpdatedOn'])
    return payroll_run_obj


@transaction.atomic
def pay_payroll_line(*, payroll_line_obj, school_id, session_id, payment_date, payment_mode_obj, status='paid', requested_status=None, user_obj=None):
    if payroll_line_obj.paymentStatus == 'paid':
        raise ValidationError('Salary line is already marked paid.')
    if _money(payroll_line_obj.netAmount) <= 0:
        raise ValidationError('Salary line has no payable amount.')
    if not payment_mode_obj or not payment_mode_obj.linkedAccountID_id:
        raise ValidationError('A valid payroll payment mode is required.')
    requested_status = (requested_status or status or 'pending').strip()
    target_status = (status or 'pending').strip()

    if target_status != 'paid':
        payroll_line_obj.paymentStatus = target_status
        payroll_line_obj.requestedPaymentStatus = requested_status
        payroll_line_obj.paymentModeID = payment_mode_obj
        payroll_line_obj.paymentDate = payment_date
        payroll_line_obj.save(update_fields=['paymentStatus', 'requestedPaymentStatus', 'paymentModeID', 'paymentDate', 'lastUpdatedOn'])
        return payroll_line_obj

    setup = bootstrap_school_finance(school_id=school_id, session_id=session_id, user_obj=user_obj)
    amount = _money(payroll_line_obj.netAmount)
    upsert_finance_transaction(
        school_id=school_id,
        session_id=session_id,
        txn_type='salary_payment',
        txn_date=payment_date,
        source_module='payroll_line_payment',
        source_record_id=payroll_line_obj.id,
        description=f'Salary payment for {payroll_line_obj.partyID.displayName}',
        reference_no=f'SAL-{payroll_line_obj.id}',
        entries=[
            {
                'accountID': setup['accounts']['SALARY_PAYABLE'],
                'partyID': payroll_line_obj.partyID,
                'entryType': 'debit',
                'amount': amount,
                'narration': f'Salary payment {payroll_line_obj.partyID.displayName}',
            },
            {
                'accountID': payment_mode_obj.linkedAccountID,
                'partyID': payroll_line_obj.partyID,
                'entryType': 'credit',
                'amount': amount,
                'narration': f'Salary payment {payroll_line_obj.partyID.displayName}',
            },
        ],
        user_obj=user_obj,
    )
    payroll_line_obj.paymentStatus = 'paid'
    payroll_line_obj.requestedPaymentStatus = requested_status or 'paid'
    payroll_line_obj.paymentModeID = payment_mode_obj
    payroll_line_obj.paymentDate = payment_date
    payroll_line_obj.save(update_fields=['paymentStatus', 'requestedPaymentStatus', 'paymentModeID', 'paymentDate', 'lastUpdatedOn'])

    payroll_run_obj = payroll_line_obj.payrollRunID
    if payroll_run_obj.payrollLines.filter(paymentStatus='pending', netAmount__gt=0).exists():
        payroll_run_obj.status = 'posted'
    else:
        payroll_run_obj.status = 'paid'
    payroll_run_obj.lastEditedBy = _user_label(user_obj)
    payroll_run_obj.updatedByUserID = user_obj
    payroll_run_obj.save(update_fields=['status', 'lastEditedBy', 'updatedByUserID', 'lastUpdatedOn'])
    return payroll_line_obj


@transaction.atomic
def approve_payroll_payment(*, payroll_line_obj, school_id, session_id, user_obj=None):
    if not payroll_line_obj:
        raise ValidationError('Payroll line could not be found.')
    if payroll_line_obj.paymentStatus == 'paid':
        return payroll_line_obj
    if payroll_line_obj.paymentStatus not in {'submitted', 'pending'}:
        raise ValidationError('Only pending or submitted salary payments can be approved.')
    if not payroll_line_obj.paymentDate:
        raise ValidationError('Approved salary payment requires a payment date.')
    if not payroll_line_obj.paymentModeID_id:
        raise ValidationError('Approved salary payment requires a payment mode.')
    return pay_payroll_line(
        payroll_line_obj=payroll_line_obj,
        school_id=school_id,
        session_id=session_id,
        payment_date=payroll_line_obj.paymentDate,
        payment_mode_obj=payroll_line_obj.paymentModeID,
        status='paid',
        requested_status=payroll_line_obj.requestedPaymentStatus or 'paid',
        user_obj=user_obj,
    )


@transaction.atomic
def sync_student_charge(
    *,
    student_obj,
    school_id,
    session_id,
    fee_head_code,
    amount,
    charge_date,
    due_date,
    source_module,
    source_record_id,
    title,
    description='',
    standard_obj=None,
    user_obj=None,
):
    amount = _money(amount)
    setup = bootstrap_school_finance(school_id=school_id, session_id=session_id, user_obj=user_obj)
    existing_charge = StudentCharge.objects.select_for_update().filter(
        schoolID_id=school_id,
        sessionID_id=session_id,
        sourceModule=source_module,
        sourceRecordID=str(source_record_id),
        isDeleted=False,
    ).first()

    if amount <= 0:
        if existing_charge:
            existing_charge.grossAmount = Decimal('0.00')
            existing_charge.discountAmount = Decimal('0.00')
            existing_charge.fineAmount = Decimal('0.00')
            existing_charge.netAmount = Decimal('0.00')
            existing_charge.paidAmount = Decimal('0.00')
            existing_charge.balanceAmount = Decimal('0.00')
            existing_charge.status = 'cancelled'
            existing_charge.description = description or existing_charge.description
            existing_charge.lastEditedBy = _user_label(user_obj)
            existing_charge.updatedByUserID = user_obj
            existing_charge.save(update_fields=[
                'grossAmount', 'discountAmount', 'fineAmount', 'netAmount',
                'paidAmount', 'balanceAmount', 'status', 'description',
                'lastEditedBy', 'updatedByUserID', 'lastUpdatedOn'
            ])
        clear_finance_transaction(
            school_id=school_id,
            source_module=source_module,
            source_record_id=source_record_id,
            user_obj=user_obj,
        )
        return None

    party_obj = ensure_student_party(
        student_obj=student_obj,
        school_id=school_id,
        session_id=session_id,
        user_obj=user_obj,
    )
    fee_head = setup['fee_heads'][fee_head_code]
    defaults = {
        'studentID': student_obj,
        'standardID': standard_obj,
        'partyID': party_obj,
        'feeHeadID': fee_head,
        'chargeType': 'admission_fee' if fee_head_code == 'ADMISSION_FEE' else 'student_fee',
        'referenceNo': str(source_record_id),
        'title': title,
        'description': description or '',
        'chargeDate': charge_date,
        'dueDate': due_date,
        'periodStartDate': charge_date,
        'periodEndDate': due_date,
        'grossAmount': amount,
        'discountAmount': Decimal('0.00'),
        'fineAmount': Decimal('0.00'),
        'netAmount': amount,
        'paidAmount': Decimal('0.00'),
        'balanceAmount': amount,
        'status': 'posted',
        'canAutoPost': True,
        'lastEditedBy': _user_label(user_obj),
        'updatedByUserID': user_obj,
    }
    if existing_charge:
        for field, value in defaults.items():
            setattr(existing_charge, field, value)
        existing_charge.save()
        charge_obj = existing_charge
    else:
        charge_obj = StudentCharge.objects.create(
            schoolID_id=school_id,
            sessionID_id=session_id,
            sourceModule=source_module,
            sourceRecordID=str(source_record_id),
            **defaults,
        )

    upsert_finance_transaction(
        school_id=school_id,
        session_id=session_id,
        txn_type='student_charge',
        txn_date=charge_date,
        source_module=source_module,
        source_record_id=source_record_id,
        description=description or title,
        reference_no=charge_obj.referenceNo or '',
        entries=[
            {
                'accountID': fee_head.receivableAccountID,
                'partyID': party_obj,
                'entryType': 'debit',
                'amount': amount,
                'narration': title,
            },
            {
                'accountID': fee_head.incomeAccountID,
                'partyID': party_obj,
                'entryType': 'credit',
                'amount': amount,
                'narration': title,
            },
        ],
        user_obj=user_obj,
    )
    return charge_obj


@transaction.atomic
def sync_payment_receipt(
    *,
    charge_obj,
    school_id,
    session_id,
    amount_received,
    receipt_date,
    source_module,
    source_record_id,
    payment_mode_code='CASH',
    reference_no='',
    notes='',
    received_from_name='',
    user_obj=None,
):
    amount_received = _money(amount_received)
    if amount_received <= 0:
        return clear_payment_receipt(
            school_id=school_id,
            source_module=source_module,
            source_record_id=source_record_id,
            user_obj=user_obj,
        )

    setup = bootstrap_school_finance(school_id=school_id, session_id=session_id, user_obj=user_obj)
    payment_mode = setup['payment_modes'].get((payment_mode_code or 'CASH').upper(), setup['payment_modes']['CASH'])
    receipt_party = ensure_parent_party(
        parent_obj=getattr(charge_obj.studentID, 'parentID', None),
        school_id=school_id,
        session_id=session_id,
        user_obj=user_obj,
    ) or charge_obj.partyID
    receipt_obj = PaymentReceipt.objects.select_for_update().filter(
        schoolID_id=school_id,
        sourceModule=source_module,
        sourceRecordID=str(source_record_id),
        isDeleted=False,
    ).first()
    defaults = {
        'sessionID_id': session_id,
        'receiptDate': receipt_date,
        'partyID': receipt_party,
        'studentID': charge_obj.studentID,
        'receivedFromName': received_from_name or receipt_party.displayName,
        'paymentModeID': payment_mode,
        'depositAccountID': payment_mode.linkedAccountID or setup['accounts']['CASH_ON_HAND'],
        'amountReceived': amount_received,
        'referenceNo': reference_no,
        'notes': notes or '',
        'status': 'confirmed',
        'lastEditedBy': _user_label(user_obj),
        'updatedByUserID': user_obj,
    }
    if receipt_obj:
        for field, value in defaults.items():
            setattr(receipt_obj, field, value)
        receipt_obj.save()
    else:
        receipt_obj = PaymentReceipt.objects.create(
            schoolID_id=school_id,
            sourceModule=source_module,
            sourceRecordID=str(source_record_id),
            receiptNo=generate_finance_document_number(
                document_type='receipt',
                school_id=school_id,
                session_id=session_id,
                user_obj=user_obj,
            ),
            **defaults,
        )

    allocation_obj, _ = PaymentReceiptAllocation.objects.update_or_create(
        receiptID=receipt_obj,
        studentChargeID=charge_obj,
        defaults={
            'allocatedAmount': amount_received,
            'fineComponent': Decimal('0.00'),
            'discountComponent': Decimal('0.00'),
            'note': notes or '',
        },
    )
    _refresh_charge_paid_state(charge_obj, user_obj=user_obj)

    receivable_account = charge_obj.feeHeadID.receivableAccountID if charge_obj.feeHeadID_id else setup['accounts']['STUDENT_RECEIVABLE']
    upsert_finance_transaction(
        school_id=school_id,
        session_id=session_id,
        txn_type='student_receipt',
        txn_date=receipt_date,
        source_module=source_module,
        source_record_id=source_record_id,
        description=notes or f'Receipt for {charge_obj.title}',
        reference_no=receipt_obj.receiptNo,
        entries=[
            {
                'accountID': receipt_obj.depositAccountID,
                'partyID': receipt_party,
                'entryType': 'debit',
                'amount': allocation_obj.allocatedAmount,
                'narration': receipt_obj.receiptNo,
            },
            {
                'accountID': receivable_account,
                'partyID': charge_obj.partyID,
                'entryType': 'credit',
                'amount': allocation_obj.allocatedAmount,
                'narration': receipt_obj.receiptNo,
            },
        ],
        user_obj=user_obj,
    )
    return receipt_obj


@transaction.atomic
def clear_payment_receipt(*, school_id, source_module, source_record_id, user_obj=None):
    receipt_obj = PaymentReceipt.objects.select_for_update().filter(
        schoolID_id=school_id,
        sourceModule=source_module,
        sourceRecordID=str(source_record_id),
        isDeleted=False,
    ).first()
    if not receipt_obj:
        return None
    charge_ids = list(receipt_obj.allocations.values_list('studentChargeID', flat=True))
    receipt_obj.allocations.all().delete()
    receipt_obj.isDeleted = True
    receipt_obj.status = 'cancelled'
    receipt_obj.lastEditedBy = _user_label(user_obj)
    receipt_obj.updatedByUserID = user_obj
    receipt_obj.save(update_fields=['isDeleted', 'status', 'lastEditedBy', 'updatedByUserID', 'lastUpdatedOn'])
    clear_finance_transaction(
        school_id=school_id,
        source_module=source_module,
        source_record_id=source_record_id,
        user_obj=user_obj,
    )
    for charge_obj in StudentCharge.objects.filter(id__in=charge_ids, isDeleted=False):
        _refresh_charge_paid_state(charge_obj, user_obj=user_obj)
    return receipt_obj


@transaction.atomic
def rebuild_payment_receipt_ledger(*, receipt_obj, school_id, session_id, user_obj=None):
    if not receipt_obj or receipt_obj.isDeleted:
        raise ValidationError('Receipt could not be found.')
    if receipt_obj.status != 'confirmed':
        raise ValidationError('Only confirmed receipts can be rebuilt into the ledger.')
    allocations = list(
        receipt_obj.allocations.select_related(
            'studentChargeID',
            'studentChargeID__feeHeadID',
            'studentChargeID__partyID',
        ).all()
    )
    if not allocations:
        raise ValidationError('Receipt has no allocations to rebuild.')
    if not receipt_obj.depositAccountID_id:
        raise ValidationError('Receipt deposit account is missing.')

    credit_entries = {}
    total_amount = Decimal('0.00')
    for allocation_obj in allocations:
        charge_obj = allocation_obj.studentChargeID
        amount = _money(allocation_obj.allocatedAmount)
        if amount <= 0:
            continue
        total_amount += amount
        receivable_account = charge_obj.feeHeadID.receivableAccountID if charge_obj.feeHeadID_id else None
        if not receivable_account:
            raise ValidationError(f'Receivable account is missing for "{charge_obj.title}".')
        key = (receivable_account.id, charge_obj.partyID_id)
        credit_entries.setdefault(key, {
            'accountID': receivable_account,
            'partyID': charge_obj.partyID,
            'entryType': 'credit',
            'amount': Decimal('0.00'),
            'narration': receipt_obj.receiptNo,
        })
        credit_entries[key]['amount'] += amount

    if total_amount <= 0:
        raise ValidationError('Receipt has no positive allocations to rebuild.')

    upsert_finance_transaction(
        school_id=school_id,
        session_id=session_id,
        txn_type='student_receipt',
        txn_date=receipt_obj.receiptDate,
        source_module=receipt_obj.sourceModule or 'finance_manual_receipt',
        source_record_id=receipt_obj.sourceRecordID or receipt_obj.id,
        description=receipt_obj.notes or f'Receipt for {receipt_obj.receiptNo}',
        reference_no=receipt_obj.receiptNo,
        entries=[{
            'accountID': receipt_obj.depositAccountID,
            'partyID': receipt_obj.partyID,
            'entryType': 'debit',
            'amount': total_amount,
            'narration': receipt_obj.receiptNo,
        }] + list(credit_entries.values()),
        user_obj=user_obj,
    )
    for allocation_obj in allocations:
        _refresh_charge_paid_state(allocation_obj.studentChargeID, user_obj=user_obj)
    return receipt_obj


@transaction.atomic
def sync_expense_voucher_posting(*, voucher_obj, school_id, session_id, user_obj=None):
    setup = bootstrap_expense_categories(school_id=school_id, session_id=session_id, user_obj=user_obj)
    category = voucher_obj.expenseCategoryID
    if not category:
        clear_finance_transaction(
            school_id=school_id,
            source_module='expense_voucher_accrual',
            source_record_id=voucher_obj.id,
            user_obj=user_obj,
        )
        clear_finance_transaction(
            school_id=school_id,
            source_module='expense_voucher_payment',
            source_record_id=voucher_obj.id,
            user_obj=user_obj,
        )
        return None

    if voucher_obj.approvalStatus in {'cancelled', 'reversed', 'draft'}:
        clear_finance_transaction(
            school_id=school_id,
            source_module='expense_voucher_accrual',
            source_record_id=voucher_obj.id,
            user_obj=user_obj,
        )
        clear_finance_transaction(
            school_id=school_id,
            source_module='expense_voucher_payment',
            source_record_id=voucher_obj.id,
            user_obj=user_obj,
        )
        return None

    amount = _money(voucher_obj.netAmount)
    payable_account = category.payableAccountID or setup['setup']['accounts']['EXPENSE_PAYABLE']

    if voucher_obj.isImmediatePayment:
        clear_finance_transaction(
            school_id=school_id,
            source_module='expense_voucher_accrual',
            source_record_id=voucher_obj.id,
            user_obj=user_obj,
        )
        if voucher_obj.approvalStatus == 'paid' and voucher_obj.paymentAccountID_id:
            return upsert_finance_transaction(
                school_id=school_id,
                session_id=session_id,
                txn_type='expense_payment',
                txn_date=voucher_obj.voucherDate,
                source_module='expense_voucher_payment',
                source_record_id=voucher_obj.id,
                description=voucher_obj.description or voucher_obj.title,
                reference_no=voucher_obj.voucherNo,
                entries=[
                    {
                        'accountID': category.expenseAccountID,
                        'partyID': voucher_obj.partyID,
                        'entryType': 'debit',
                        'amount': amount,
                        'narration': voucher_obj.title,
                    },
                    {
                        'accountID': voucher_obj.paymentAccountID,
                        'partyID': voucher_obj.partyID,
                        'entryType': 'credit',
                        'amount': amount,
                        'narration': voucher_obj.title,
                    },
                ],
                user_obj=user_obj,
            )
        clear_finance_transaction(
            school_id=school_id,
            source_module='expense_voucher_payment',
            source_record_id=voucher_obj.id,
            user_obj=user_obj,
        )
        return None

    if voucher_obj.approvalStatus in {'approved', 'paid'}:
        upsert_finance_transaction(
            school_id=school_id,
            session_id=session_id,
            txn_type='expense_accrual',
            txn_date=voucher_obj.voucherDate,
            source_module='expense_voucher_accrual',
            source_record_id=voucher_obj.id,
            description=voucher_obj.description or voucher_obj.title,
            reference_no=voucher_obj.voucherNo,
            entries=[
                {
                    'accountID': category.expenseAccountID,
                    'partyID': voucher_obj.partyID,
                    'entryType': 'debit',
                    'amount': amount,
                    'narration': voucher_obj.title,
                },
                {
                    'accountID': payable_account,
                    'partyID': voucher_obj.partyID,
                    'entryType': 'credit',
                    'amount': amount,
                    'narration': voucher_obj.title,
                },
            ],
            user_obj=user_obj,
        )
    else:
        clear_finance_transaction(
            school_id=school_id,
            source_module='expense_voucher_accrual',
            source_record_id=voucher_obj.id,
            user_obj=user_obj,
        )

    if voucher_obj.approvalStatus == 'paid' and voucher_obj.paymentAccountID_id:
        return upsert_finance_transaction(
            school_id=school_id,
            session_id=session_id,
            txn_type='expense_payment',
            txn_date=voucher_obj.voucherDate,
            source_module='expense_voucher_payment',
            source_record_id=voucher_obj.id,
            description=voucher_obj.description or voucher_obj.title,
            reference_no=voucher_obj.voucherNo,
            entries=[
                {
                    'accountID': payable_account,
                    'partyID': voucher_obj.partyID,
                    'entryType': 'debit',
                    'amount': amount,
                    'narration': voucher_obj.title,
                },
                {
                    'accountID': voucher_obj.paymentAccountID,
                    'partyID': voucher_obj.partyID,
                    'entryType': 'credit',
                    'amount': amount,
                    'narration': voucher_obj.title,
                },
            ],
            user_obj=user_obj,
        )

    clear_finance_transaction(
        school_id=school_id,
        source_module='expense_voucher_payment',
        source_record_id=voucher_obj.id,
        user_obj=user_obj,
    )
    return None
