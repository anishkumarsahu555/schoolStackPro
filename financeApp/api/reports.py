from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.db.models import DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce

from financeApp.models import FinanceAccount, FinanceEntry, FinanceTransaction
from homeApp.models import SchoolSession
from utils.custom_decorators import check_groups
from utils.custom_response import ErrorResponse, SuccessResponse
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


def _normal_balance(account_type, debit_total, credit_total):
    debit_total = _decimal_or_zero(debit_total)
    credit_total = _decimal_or_zero(credit_total)
    if account_type in {'asset', 'expense'}:
        return debit_total - credit_total
    return credit_total - debit_total


def _empty_finance_reports_payload():
    return {
        'summary': {
            'assetTotal': 0,
            'liabilityTotal': 0,
            'incomeTotal': 0,
            'expenseTotal': 0,
            'netSurplus': 0,
        },
        'trialBalance': [],
        'incomeStatement': {
            'income': [],
            'expense': [],
            'totalIncome': 0,
            'totalExpense': 0,
            'netSurplus': 0,
        },
        'balanceSheet': {
            'assets': [],
            'liabilities': [],
            'equity': [],
            'totalAssets': 0,
            'totalLiabilities': 0,
            'totalEquity': 0,
        },
        'generalLedger': {
            'accountID': '',
            'accountLabel': '',
            'rows': [],
            'closingBalance': 0,
        },
        'moneyFlow': {
            'summary': {'totalInflow': 0, 'totalOutflow': 0, 'netCashFlow': 0, 'receivableGenerated': 0},
            'byType': [],
            'rows': [],
        },
    }


@login_required
@check_groups('Admin', 'Owner')
def get_finance_reports_api(request):
    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    if not school_id or not session_id:
        logger.warning(f'Finance reports requested without school/session user={request.user.id}')
        return SuccessResponse('Finance reports loaded successfully.', data=_empty_finance_reports_payload()).to_json_response()

    date_from_raw = request.GET.get('dateFrom')
    date_to_raw = request.GET.get('dateTo')
    date_from = _parse_filter_date(date_from_raw)
    date_to = _parse_filter_date(date_to_raw)
    account_id = request.GET.get('accountID')
    if date_from_raw and not date_from:
        logger.warning(f'Finance reports ignored invalid dateFrom="{date_from_raw}" school={school_id} session={session_id} user={request.user.id}')
    if date_to_raw and not date_to:
        logger.warning(f'Finance reports ignored invalid dateTo="{date_to_raw}" school={school_id} session={session_id} user={request.user.id}')

    try:
        entry_qs = FinanceEntry.objects.select_related('accountID', 'transactionID').filter(
            transactionID__schoolID_id=school_id,
            transactionID__sessionID_id=session_id,
            transactionID__isDeleted=False,
            transactionID__status='posted',
        )
        if date_from:
            entry_qs = entry_qs.filter(transactionID__txnDate__gte=date_from)
        if date_to:
            entry_qs = entry_qs.filter(transactionID__txnDate__lte=date_to)

        account_rows = list(
            entry_qs.values(
                'accountID',
                'accountID__accountCode',
                'accountID__accountName',
                'accountID__accountType',
            ).annotate(
                debit_total=Coalesce(
                    Sum('amount', filter=Q(entryType='debit')),
                    Value(Decimal('0.00')),
                    output_field=DecimalField(max_digits=14, decimal_places=2),
                ),
                credit_total=Coalesce(
                    Sum('amount', filter=Q(entryType='credit')),
                    Value(Decimal('0.00')),
                    output_field=DecimalField(max_digits=14, decimal_places=2),
                ),
            ).order_by('accountID__accountType', 'accountID__accountName')
        )

        trial_balance = []
        income_rows = []
        expense_rows = []
        asset_rows = []
        liability_rows = []
        equity_rows = []
        total_income = Decimal('0.00')
        total_expense = Decimal('0.00')
        total_assets = Decimal('0.00')
        total_liabilities = Decimal('0.00')
        total_equity = Decimal('0.00')

        for row in account_rows:
            account_type = row['accountID__accountType']
            debit_total = _decimal_or_zero(row['debit_total'])
            credit_total = _decimal_or_zero(row['credit_total'])
            closing = _normal_balance(account_type, debit_total, credit_total)
            trial_debit = Decimal('0.00')
            trial_credit = Decimal('0.00')
            if closing >= 0:
                if account_type in {'asset', 'expense'}:
                    trial_debit = closing
                else:
                    trial_credit = closing
            else:
                if account_type in {'asset', 'expense'}:
                    trial_credit = abs(closing)
                else:
                    trial_debit = abs(closing)

            trial_balance.append({
                'accountCode': row['accountID__accountCode'] or '',
                'accountName': row['accountID__accountName'] or '',
                'accountType': account_type,
                'debitTotal': float(debit_total),
                'creditTotal': float(credit_total),
                'closingDebit': float(trial_debit),
                'closingCredit': float(trial_credit),
            })

            line = {
                'accountCode': row['accountID__accountCode'] or '',
                'accountName': row['accountID__accountName'] or '',
                'amount': float(abs(closing)),
            }
            if account_type == 'income':
                total_income += max(closing, Decimal('0.00'))
                income_rows.append(line)
            elif account_type == 'expense':
                total_expense += max(closing, Decimal('0.00'))
                expense_rows.append(line)
            elif account_type == 'asset':
                total_assets += abs(closing)
                asset_rows.append(line)
            elif account_type == 'liability':
                total_liabilities += abs(closing)
                liability_rows.append(line)
            elif account_type == 'equity':
                total_equity += abs(closing)
                equity_rows.append(line)

        net_surplus = total_income - total_expense
        general_ledger_rows = []
        general_ledger_account = None
        running_balance = Decimal('0.00')
        if account_id:
            general_ledger_account = FinanceAccount.objects.filter(
                pk=account_id,
                schoolID_id=school_id,
                sessionID_id=session_id,
                isDeleted=False,
            ).first()
            if general_ledger_account:
                ledger_qs = FinanceEntry.objects.select_related('transactionID', 'partyID').filter(
                    transactionID__schoolID_id=school_id,
                    transactionID__sessionID_id=session_id,
                    transactionID__isDeleted=False,
                    transactionID__status='posted',
                    accountID=general_ledger_account,
                ).order_by('transactionID__txnDate', 'transactionID__id', 'lineOrder', 'id')
                if date_from:
                    ledger_qs = ledger_qs.filter(transactionID__txnDate__gte=date_from)
                if date_to:
                    ledger_qs = ledger_qs.filter(transactionID__txnDate__lte=date_to)
                for row in ledger_qs:
                    amount = _decimal_or_zero(row.amount)
                    if general_ledger_account.accountType in {'asset', 'expense'}:
                        running_balance += amount if row.entryType == 'debit' else -amount
                    else:
                        running_balance += amount if row.entryType == 'credit' else -amount
                    general_ledger_rows.append({
                        'date': row.transactionID.txnDate.strftime('%d-%m-%Y') if row.transactionID.txnDate else 'N/A',
                        'reference': row.transactionID.referenceNo or row.transactionID.txnNo,
                        'txnType': row.transactionID.txnType,
                        'description': row.narration or row.transactionID.description or '',
                        'party': row.partyID.displayName if row.partyID_id else '',
                        'debit': float(amount if row.entryType == 'debit' else Decimal('0.00')),
                        'credit': float(amount if row.entryType == 'credit' else Decimal('0.00')),
                        'balance': float(running_balance),
                    })
            else:
                logger.warning(f'Finance reports account not found account={account_id} school={school_id} session={session_id} user={request.user.id}')

        cash_flow_types = {
            'student_receipt': 'inflow',
            'expense_payment': 'outflow',
            'salary_payment': 'outflow',
            'refund': 'outflow',
        }
        receivable_types = {'student_charge', 'expense_accrual', 'payroll_accrual'}
        txn_qs = FinanceTransaction.objects.prefetch_related('entries__accountID', 'entries__partyID').filter(
            schoolID_id=school_id,
            sessionID_id=session_id,
            isDeleted=False,
            status='posted',
        ).order_by('-txnDate', '-id')
        if date_from:
            txn_qs = txn_qs.filter(txnDate__gte=date_from)
        if date_to:
            txn_qs = txn_qs.filter(txnDate__lte=date_to)

        total_inflow = Decimal('0.00')
        total_outflow = Decimal('0.00')
        receivable_generated = Decimal('0.00')
        type_totals = {}
        money_flow_rows = []
        for txn in txn_qs[:300]:
            entries = list(txn.entries.all())
            amount = sum((_decimal_or_zero(entry.amount) for entry in entries if entry.entryType == 'debit'), Decimal('0.00'))
            direction = cash_flow_types.get(txn.txnType)
            if direction == 'inflow':
                total_inflow += amount
            elif direction == 'outflow':
                total_outflow += amount
            elif txn.txnType in receivable_types:
                receivable_generated += amount

            if direction or txn.txnType in receivable_types:
                bucket = type_totals.setdefault(txn.txnType, {'txnType': txn.txnType, 'inflow': Decimal('0.00'), 'outflow': Decimal('0.00'), 'receivable': Decimal('0.00')})
                if direction == 'inflow':
                    bucket['inflow'] += amount
                elif direction == 'outflow':
                    bucket['outflow'] += amount
                else:
                    bucket['receivable'] += amount
                money_flow_rows.append({
                    'date': txn.txnDate.strftime('%d-%m-%Y') if txn.txnDate else 'N/A',
                    'reference': txn.referenceNo or txn.txnNo,
                    'txnType': txn.txnType,
                    'direction': direction or 'receivable',
                    'description': txn.description or '',
                    'sourceModule': txn.sourceModule or '',
                    'sourceRecordID': txn.sourceRecordID or '',
                    'amount': float(amount),
                })

        payload = {
            'summary': {
                'assetTotal': float(total_assets),
                'liabilityTotal': float(total_liabilities),
                'incomeTotal': float(total_income),
                'expenseTotal': float(total_expense),
                'netSurplus': float(net_surplus),
            },
            'trialBalance': trial_balance,
            'incomeStatement': {
                'income': income_rows,
                'expense': expense_rows,
                'totalIncome': float(total_income),
                'totalExpense': float(total_expense),
                'netSurplus': float(net_surplus),
            },
            'balanceSheet': {
                'assets': asset_rows,
                'liabilities': liability_rows,
                'equity': equity_rows,
                'totalAssets': float(total_assets),
                'totalLiabilities': float(total_liabilities),
                'totalEquity': float(total_equity),
            },
            'generalLedger': {
                'accountID': str(general_ledger_account.id) if general_ledger_account else '',
                'accountLabel': str(general_ledger_account) if general_ledger_account else '',
                'rows': general_ledger_rows,
                'closingBalance': float(running_balance),
            },
            'moneyFlow': {
                'summary': {
                    'totalInflow': float(total_inflow),
                    'totalOutflow': float(total_outflow),
                    'netCashFlow': float(total_inflow - total_outflow),
                    'receivableGenerated': float(receivable_generated),
                },
                'byType': [
                    {
                        'txnType': row['txnType'],
                        'inflow': float(row['inflow']),
                        'outflow': float(row['outflow']),
                        'receivable': float(row['receivable']),
                    }
                    for row in sorted(type_totals.values(), key=lambda item: item['txnType'])
                ],
                'rows': money_flow_rows,
            },
        }
        logger.info(
            f'Finance reports loaded trial_rows={len(trial_balance)} ledger_rows={len(general_ledger_rows)} '
            f'account={account_id or "none"} school={school_id} session={session_id} user={request.user.id}'
        )
        return SuccessResponse('Finance reports loaded successfully.', data=payload).to_json_response()
    except Exception as exc:
        logger.exception(f'Unable to load finance reports school={school_id} session={session_id} user={request.user.id}: {exc}')
        return ErrorResponse('Unable to load finance reports.', status_code=500).to_json_response()
