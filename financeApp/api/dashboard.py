import calendar
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q, Sum

from financeApp.models import ExpenseVoucher, FeeHead, FinanceAccount, FinanceEntry, FinanceTransaction, PaymentReceipt, PayrollLine, StudentCharge
from financeApp.services import bootstrap_expense_categories, bootstrap_school_finance
from homeApp.models import SchoolSession
from homeApp.session_utils import get_session_month_sequence
from utils.custom_decorators import check_groups
from utils.custom_response import SuccessResponse
from utils.get_school_detail import get_school_id
from utils.logger import logger


def _current_session_id(request):
    return request.session.get('current_session', {}).get('Id')


def _current_school_id(request):
    current_session = request.session.get('current_session', {})
    school_id = current_session.get('SchoolID')
    if school_id:
        return school_id
    session_id = current_session.get('Id')
    if session_id:
        school_id = SchoolSession.objects.filter(pk=session_id, isDeleted=False).values_list('schoolID_id', flat=True).first()
        if school_id:
            current_session['SchoolID'] = school_id
            request.session['current_session'] = current_session
            return school_id
    return get_school_id(request)


def _decimal_or_zero(value):
    try:
        return Decimal(str(value or '0')).quantize(Decimal('0.01'))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal('0.00')


def _parse_filter_date(value):
    raw = (value or '').strip()
    if not raw:
        return None
    for date_format in ('%d/%m/%Y', '%Y-%m-%d', '%d-%m-%Y'):
        try:
            return datetime.strptime(raw, date_format).date()
        except ValueError:
            continue
    return None


def _month_end_day(year, month):
    return calendar.monthrange(year, month)[1]


def _month_sequence_for_range(session_obj, date_from=None, date_to=None):
    if date_from and date_to and date_from <= date_to:
        months = []
        cursor = date(date_from.year, date_from.month, 1)
        last_cursor = date(date_to.year, date_to.month, 1)
        while cursor <= last_cursor and len(months) < 12:
            start_date = max(cursor, date_from)
            end_date = min(date(cursor.year, cursor.month, _month_end_day(cursor.year, cursor.month)), date_to)
            months.append((calendar.month_name[cursor.month], cursor.year, cursor.month, start_date, end_date))
            cursor = date(cursor.year + 1, 1, 1) if cursor.month == 12 else date(cursor.year, cursor.month + 1, 1)
        if months:
            return months
    return get_session_month_sequence(session_obj, max_months=12)


def _sum_amount(queryset, field_name):
    return queryset.aggregate(total=Sum(field_name)).get('total') or Decimal('0.00')


def _float(value):
    return float(_decimal_or_zero(value))


def _money(value):
    return float(_decimal_or_zero(value))


def _empty_payload():
    return {
        'summary': {
            'feeHeadsCount': 0,
            'openChargesCount': 0,
            'chargeTotalDue': 0,
            'chargeTotalPaid': 0,
            'chargeTotalBalance': 0,
            'receiptCount': 0,
            'receiptTotal': 0,
            'expenseVoucherCount': 0,
            'expenseTotal': 0,
            'payrollPaidTotal': 0,
            'payrollPendingTotal': 0,
            'netCashFlow': 0,
            'liquidityTotal': 0,
            'cashBalance': 0,
            'bankBalance': 0,
            'collectionRate': 0,
            'postedTxnCount': 0,
            'reversedTxnCount': 0,
        },
        'charts': {
            'monthLabels': [],
            'monthlyReceipts': [],
            'monthlyExpenses': [],
            'monthlyPayroll': [],
            'monthlyNetCash': [],
            'monthlyCharges': [],
            'chargeStatusLabels': [],
            'chargeStatusValues': [],
            'expenseCategoryLabels': [],
            'expenseCategoryValues': [],
            'paymentModeLabels': [],
            'paymentModeValues': [],
        },
    }


def build_finance_dashboard_payload(request, *, date_from=None, date_to=None, source='all', account_id=None):
    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    if not school_id or not session_id:
        logger.warning(f'Finance dashboard requested without school/session user={request.user.id}')
        return _empty_payload()

    session_obj = SchoolSession.objects.filter(pk=session_id).first()
    bootstrap_school_finance(school_id=school_id, session_id=session_id, user_obj=request.user)
    bootstrap_expense_categories(school_id=school_id, session_id=session_id, user_obj=request.user)

    source = source if source in {'all', 'fees', 'fines', 'expenses', 'payroll', 'vendors'} else 'all'
    account_id = account_id or None
    if account_id and not str(account_id).isdigit():
        logger.warning(f'Finance dashboard ignored invalid accountID="{account_id}" school={school_id} session={session_id} user={request.user.id}')
        account_id = None

    fee_heads_qs = FeeHead.objects.filter(schoolID_id=school_id, sessionID_id=session_id, isDeleted=False)
    charge_qs = StudentCharge.objects.filter(schoolID_id=school_id, sessionID_id=session_id, isDeleted=False)
    receipt_qs = PaymentReceipt.objects.filter(schoolID_id=school_id, sessionID_id=session_id, isDeleted=False)
    expense_qs = ExpenseVoucher.objects.filter(schoolID_id=school_id, sessionID_id=session_id, isDeleted=False)
    payroll_line_qs = PayrollLine.objects.select_related('payrollRunID').filter(
        payrollRunID__schoolID_id=school_id,
        payrollRunID__sessionID_id=session_id,
        payrollRunID__isDeleted=False,
    )
    transaction_qs = FinanceTransaction.objects.filter(schoolID_id=school_id, sessionID_id=session_id, isDeleted=False)
    finance_accounts_qs = FinanceAccount.objects.filter(schoolID_id=school_id, sessionID_id=session_id, isDeleted=False)

    if date_from:
        charge_qs = charge_qs.filter(chargeDate__gte=date_from)
        receipt_qs = receipt_qs.filter(receiptDate__gte=date_from)
        expense_qs = expense_qs.filter(voucherDate__gte=date_from)
        payroll_line_qs = payroll_line_qs.filter(Q(paymentDate__gte=date_from) | Q(paymentDate__isnull=True))
        transaction_qs = transaction_qs.filter(txnDate__gte=date_from)
    if date_to:
        charge_qs = charge_qs.filter(chargeDate__lte=date_to)
        receipt_qs = receipt_qs.filter(receiptDate__lte=date_to)
        expense_qs = expense_qs.filter(voucherDate__lte=date_to)
        payroll_line_qs = payroll_line_qs.filter(Q(paymentDate__lte=date_to) | Q(paymentDate__isnull=True))
        transaction_qs = transaction_qs.filter(txnDate__lte=date_to)

    if source == 'fees':
        charge_qs = charge_qs.filter(chargeType__in=['student_fee', 'admission_fee'])
        receipt_qs = receipt_qs.filter(studentID__isnull=False)
        expense_qs = expense_qs.none()
        payroll_line_qs = payroll_line_qs.none()
        transaction_qs = transaction_qs.filter(txnType__in=['student_charge', 'student_receipt'])
    elif source == 'fines':
        charge_qs = charge_qs.filter(fineAmount__gt=0)
        receipt_qs = receipt_qs.filter(allocations__fineComponent__gt=0).distinct()
        expense_qs = expense_qs.none()
        payroll_line_qs = payroll_line_qs.none()
        transaction_qs = transaction_qs.filter(txnType__in=['student_charge', 'student_receipt'])
    elif source == 'expenses':
        charge_qs = charge_qs.none()
        receipt_qs = receipt_qs.none()
        payroll_line_qs = payroll_line_qs.none()
        transaction_qs = transaction_qs.filter(txnType__in=['expense_accrual', 'expense_payment'])
    elif source == 'payroll':
        charge_qs = charge_qs.none()
        receipt_qs = receipt_qs.none()
        expense_qs = expense_qs.none()
        transaction_qs = transaction_qs.filter(txnType__in=['payroll_accrual', 'salary_payment'])
    elif source == 'vendors':
        charge_qs = charge_qs.none()
        receipt_qs = receipt_qs.none()
        payroll_line_qs = payroll_line_qs.none()
        expense_qs = expense_qs.filter(partyID__isnull=False)
        transaction_qs = transaction_qs.filter(txnType__in=['expense_accrual', 'expense_payment'])

    if account_id:
        receipt_qs = receipt_qs.filter(depositAccountID_id=account_id)
        expense_qs = expense_qs.filter(paymentAccountID_id=account_id)
        payroll_line_qs = payroll_line_qs.filter(paymentModeID__linkedAccountID_id=account_id)
        transaction_qs = transaction_qs.filter(entries__accountID_id=account_id).distinct()

    confirmed_receipt_qs = receipt_qs.filter(status='confirmed')
    paid_expense_qs = expense_qs.filter(approvalStatus='paid')
    paid_payroll_line_qs = payroll_line_qs.filter(paymentStatus='paid')
    pending_payroll_line_qs = payroll_line_qs.exclude(paymentStatus='paid')

    charge_summary = charge_qs.aggregate(
        total_due=Sum('netAmount'),
        total_paid=Sum('paidAmount'),
        total_balance=Sum('balanceAmount'),
    )

    def account_balance(account_code):
        account = finance_accounts_qs.filter(accountCode=account_code).first()
        if not account:
            return Decimal('0.00')
        entry_qs = FinanceEntry.objects.filter(
            accountID=account,
            transactionID__schoolID_id=school_id,
            transactionID__sessionID_id=session_id,
            transactionID__isDeleted=False,
            transactionID__status='posted',
        )
        if date_from:
            entry_qs = entry_qs.filter(transactionID__txnDate__gte=date_from)
        if date_to:
            entry_qs = entry_qs.filter(transactionID__txnDate__lte=date_to)
        totals = entry_qs.aggregate(
            debit_total=Sum('amount', filter=Q(entryType='debit')),
            credit_total=Sum('amount', filter=Q(entryType='credit')),
        )
        return (totals.get('debit_total') or Decimal('0.00')) - (totals.get('credit_total') or Decimal('0.00'))

    cash_balance = account_balance('CASH_ON_HAND')
    bank_balance = account_balance('BANK_MAIN')
    receipt_total = _sum_amount(confirmed_receipt_qs, 'amountReceived')
    expense_total = _sum_amount(paid_expense_qs, 'netAmount')
    payroll_paid_total = _sum_amount(paid_payroll_line_qs, 'netAmount')
    payroll_pending_total = _sum_amount(pending_payroll_line_qs, 'netAmount')
    net_cash_flow = receipt_total - expense_total - payroll_paid_total
    collection_rate = Decimal('0.00')
    if charge_summary.get('total_due'):
        collection_rate = ((charge_summary.get('total_paid') or Decimal('0.00')) / charge_summary.get('total_due')) * Decimal('100.00')

    month_sequence = _month_sequence_for_range(session_obj, date_from, date_to)
    month_labels = [f'{month_name[:3]} {year}' for month_name, year, month_no, start_date, end_date in month_sequence]
    monthly_receipts = []
    monthly_expenses = []
    monthly_payroll = []
    monthly_net_cash = []
    monthly_charges = []
    for month_name, year, month_no, start_date, end_date in month_sequence:
        receipt_month_total = _sum_amount(confirmed_receipt_qs.filter(receiptDate__gte=start_date, receiptDate__lte=end_date), 'amountReceived')
        expense_month_total = _sum_amount(paid_expense_qs.filter(voucherDate__gte=start_date, voucherDate__lte=end_date), 'netAmount')
        payroll_month_total = _sum_amount(paid_payroll_line_qs.filter(paymentDate__gte=start_date, paymentDate__lte=end_date), 'netAmount')
        charge_month_total = _sum_amount(charge_qs.filter(chargeDate__gte=start_date, chargeDate__lte=end_date).exclude(status='cancelled'), 'netAmount')
        monthly_receipts.append(_float(receipt_month_total))
        monthly_expenses.append(_float(expense_month_total))
        monthly_payroll.append(_float(payroll_month_total))
        monthly_net_cash.append(_float(receipt_month_total - expense_month_total - payroll_month_total))
        monthly_charges.append(_float(charge_month_total))

    charge_status_rows = list(
        charge_qs.exclude(status='cancelled').values('status').annotate(total=Sum('balanceAmount'), count=Count('id')).order_by('status')
    )
    expense_category_rows = list(
        paid_expense_qs.values('expenseCategoryID__name').annotate(total=Sum('netAmount')).order_by('-total')[:6]
    )
    payment_mode_rows = list(
        confirmed_receipt_qs.values('paymentModeID__name').annotate(total=Sum('amountReceived')).order_by('-total')[:6]
    )

    return {
        'summary': {
            'feeHeadsCount': fee_heads_qs.count(),
            'openChargesCount': charge_qs.exclude(status__in=['paid', 'cancelled']).count(),
            'chargeTotalDue': _money(charge_summary.get('total_due')),
            'chargeTotalPaid': _money(charge_summary.get('total_paid')),
            'chargeTotalBalance': _money(charge_summary.get('total_balance')),
            'receiptCount': confirmed_receipt_qs.count(),
            'receiptTotal': _money(receipt_total),
            'expenseVoucherCount': paid_expense_qs.count(),
            'expenseTotal': _money(expense_total),
            'payrollPaidTotal': _money(payroll_paid_total),
            'payrollPendingTotal': _money(payroll_pending_total),
            'netCashFlow': _money(net_cash_flow),
            'liquidityTotal': _money(cash_balance + bank_balance),
            'cashBalance': _money(cash_balance),
            'bankBalance': _money(bank_balance),
            'collectionRate': _money(collection_rate),
            'postedTxnCount': transaction_qs.filter(status='posted').count(),
            'reversedTxnCount': transaction_qs.filter(status='reversed').count(),
        },
        'charts': {
            'monthLabels': month_labels,
            'monthlyReceipts': monthly_receipts,
            'monthlyExpenses': monthly_expenses,
            'monthlyPayroll': monthly_payroll,
            'monthlyNetCash': monthly_net_cash,
            'monthlyCharges': monthly_charges,
            'chargeStatusLabels': [str(row['status'] or 'unknown').replace('_', ' ').title() for row in charge_status_rows],
            'chargeStatusValues': [_float(row.get('total')) for row in charge_status_rows],
            'expenseCategoryLabels': [row['expenseCategoryID__name'] or 'Uncategorised' for row in expense_category_rows],
            'expenseCategoryValues': [_float(row.get('total')) for row in expense_category_rows],
            'paymentModeLabels': [row['paymentModeID__name'] or 'Mode not set' for row in payment_mode_rows],
            'paymentModeValues': [_float(row.get('total')) for row in payment_mode_rows],
        },
    }


@login_required
@check_groups('Admin', 'Owner')
def get_finance_dashboard_api(request):
    date_from_raw = request.GET.get('dateFrom')
    date_to_raw = request.GET.get('dateTo')
    date_from = _parse_filter_date(date_from_raw)
    date_to = _parse_filter_date(date_to_raw)
    if date_from_raw and not date_from:
        logger.warning(f'Finance dashboard ignored invalid dateFrom="{date_from_raw}" user={request.user.id}')
    if date_to_raw and not date_to:
        logger.warning(f'Finance dashboard ignored invalid dateTo="{date_to_raw}" user={request.user.id}')

    payload = build_finance_dashboard_payload(
        request,
        date_from=date_from,
        date_to=date_to,
        source=request.GET.get('source') or 'all',
        account_id=request.GET.get('accountID') or None,
    )
    return SuccessResponse('Finance dashboard loaded successfully.', data=payload).to_json_response()
