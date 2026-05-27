from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import JsonResponse as DjangoJsonResponse
from django.urls import reverse
from django.utils.html import escape

from financeApp.models import FinanceAccount, FinanceTransaction
from homeApp.models import SchoolSession
from utils.custom_decorators import check_groups
from utils.custom_response import SuccessResponse
from utils.get_school_detail import get_school_id
from utils.logger import logger


CASH_FLOW_TYPES = {
    'student_receipt': 'inflow',
    'expense_payment': 'outflow',
    'salary_payment': 'outflow',
    'refund': 'outflow',
}
RECEIVABLE_TYPES = {'student_charge', 'expense_accrual', 'payroll_accrual'}
SOURCE_GROUP_TYPES = {
    'fees': {'student_charge', 'student_receipt'},
    'fines': {'student_charge', 'student_receipt'},
    'expenses': {'expense_accrual', 'expense_payment'},
    'payroll': {'payroll_accrual', 'salary_payment'},
    'vendors': {'expense_accrual', 'expense_payment'},
}


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


def _safe_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


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


def _datatable_json_response(*, draw, total_count, filtered_count, rows):
    return DjangoJsonResponse({
        'draw': draw,
        'recordsTotal': total_count,
        'recordsFiltered': filtered_count,
        'data': rows,
    })


def _txn_direction(txn_type):
    if txn_type in CASH_FLOW_TYPES:
        return CASH_FLOW_TYPES[txn_type]
    if txn_type in RECEIVABLE_TYPES:
        return 'receivable'
    if txn_type == 'reversal':
        return 'adjustment'
    return 'adjustment'


def _txn_amount(txn):
    entries = list(txn.entries.all())
    debit_total = sum((_decimal_or_zero(entry.amount) for entry in entries if entry.entryType == 'debit'), Decimal('0.00'))
    credit_total = sum((_decimal_or_zero(entry.amount) for entry in entries if entry.entryType == 'credit'), Decimal('0.00'))
    return max(debit_total, credit_total)


def _money_ledger_queryset(*, request, school_id, session_id):
    qs = FinanceTransaction.objects.prefetch_related('entries__accountID', 'entries__partyID').filter(
        schoolID_id=school_id,
        sessionID_id=session_id,
        isDeleted=False,
        status='posted',
    ).order_by('-txnDate', '-id')

    date_from = _parse_filter_date(request.GET.get('dateFrom'))
    date_to = _parse_filter_date(request.GET.get('dateTo'))
    txn_type = (request.GET.get('txnType') or '').strip()
    direction = (request.GET.get('direction') or '').strip()
    source_module = (request.GET.get('sourceModule') or '').strip()
    source_group = (request.GET.get('sourceGroup') or '').strip()
    account_id = request.GET.get('accountID')

    if date_from:
        qs = qs.filter(txnDate__gte=date_from)
    if date_to:
        qs = qs.filter(txnDate__lte=date_to)
    if txn_type:
        qs = qs.filter(txnType=txn_type)
    if source_module:
        qs = qs.filter(sourceModule=source_module)
    if source_group in SOURCE_GROUP_TYPES:
        qs = qs.filter(txnType__in=SOURCE_GROUP_TYPES[source_group])
        if source_group == 'fines':
            qs = qs.filter(Q(sourceModule__icontains='fine') | Q(description__icontains='fine'))
        elif source_group == 'vendors':
            qs = qs.filter(sourceModule__in=['expense_voucher_accrual', 'expense_voucher_payment', 'manual_expense_voucher'])
    if account_id:
        qs = qs.filter(entries__accountID_id=account_id)
    if direction == 'inflow':
        qs = qs.filter(txnType__in=[key for key, value in CASH_FLOW_TYPES.items() if value == 'inflow'])
    elif direction == 'outflow':
        qs = qs.filter(txnType__in=[key for key, value in CASH_FLOW_TYPES.items() if value == 'outflow'])
    elif direction == 'receivable':
        qs = qs.filter(txnType__in=RECEIVABLE_TYPES)
    elif direction == 'adjustment':
        known_types = set(CASH_FLOW_TYPES) | RECEIVABLE_TYPES
        qs = qs.exclude(txnType__in=known_types)

    return qs.distinct()


def _detail_link(row):
    source_module = row.get('sourceModule') or ''
    source_record_id = str(row.get('sourceRecordID') or '').strip()
    txn_type = row.get('txnType') or ''
    if source_module in {'finance_manual_receipt', 'student_admission_receipt'} and source_record_id.isdigit():
        return reverse('managementApp:finance_receipt_detail', args=[source_record_id])
    if txn_type == 'student_charge':
        return reverse('managementApp:manage_student_charges')
    if txn_type == 'student_receipt':
        return reverse('managementApp:manage_receipts')
    if txn_type in {'expense_accrual', 'expense_payment'}:
        return reverse('managementApp:manage_expense_vouchers')
    if txn_type in {'payroll_accrual', 'salary_payment'}:
        return reverse('managementApp:finance_payroll')
    if source_module.startswith('library_fine'):
        return reverse('libraryApp:manage_fines')
    return reverse('managementApp:finance_audit_trail')


def _money_ledger_payload(*, request, school_id, session_id):
    qs = _money_ledger_queryset(request=request, school_id=school_id, session_id=session_id)
    rows = []
    total_inflow = Decimal('0.00')
    total_outflow = Decimal('0.00')
    total_receivable = Decimal('0.00')

    for txn in qs[:500]:
        amount = _txn_amount(txn)
        direction = _txn_direction(txn.txnType)
        if direction == 'inflow':
            total_inflow += amount
        elif direction == 'outflow':
            total_outflow += amount
        elif direction == 'receivable':
            total_receivable += amount
        rows.append({
            'id': txn.id,
            'date': txn.txnDate.strftime('%d-%m-%Y') if txn.txnDate else 'N/A',
            'dateObj': txn.txnDate,
            'txnNo': txn.txnNo,
            'referenceNo': txn.referenceNo or '',
            'txnType': txn.txnType,
            'txnTypeLabel': txn.get_txnType_display(),
            'direction': direction,
            'sourceModule': txn.sourceModule or '',
            'sourceRecordID': txn.sourceRecordID or '',
            'description': txn.description or '',
            'amount': float(amount),
        })

    return {
        'summary': {
            'totalTransactions': qs.count(),
            'totalInflow': float(total_inflow),
            'totalOutflow': float(total_outflow),
            'netCashFlow': float(total_inflow - total_outflow),
            'totalReceivable': float(total_receivable),
        },
        'rows': rows,
    }


@login_required
@check_groups('Admin', 'Owner')
def get_money_ledger_filter_options_api(request):
    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    if not school_id or not session_id:
        logger.warning(f'Money ledger filter options requested without school/session user={request.user.id}')
        return SuccessResponse('Money ledger filters loaded.', data={'transactionTypes': [], 'sourceModules': [], 'accounts': []}).to_json_response()

    source_modules = list(FinanceTransaction.objects.filter(
        schoolID_id=school_id,
        sessionID_id=session_id,
        isDeleted=False,
    ).exclude(sourceModule__isnull=True).exclude(sourceModule='').values_list('sourceModule', flat=True).distinct().order_by('sourceModule'))
    accounts = list(FinanceAccount.objects.filter(
        schoolID_id=school_id,
        sessionID_id=session_id,
        isDeleted=False,
        isActive=True,
    ).order_by('accountType', 'accountCode').values('id', 'accountCode', 'accountName', 'accountType'))
    data = {
        'transactionTypes': [{'value': key, 'label': label} for key, label in FinanceTransaction.TXN_TYPE_CHOICES],
        'sourceModules': source_modules,
        'accounts': [
            {
                'id': row['id'],
                'label': f"{row['accountCode']} - {row['accountName']}",
                'accountType': row['accountType'],
            }
            for row in accounts
        ],
    }
    logger.info(f'Money ledger filters loaded sources={len(source_modules)} accounts={len(accounts)} school={school_id} session={session_id} user={request.user.id}')
    return SuccessResponse('Money ledger filters loaded.', data=data).to_json_response()


@login_required
@check_groups('Admin', 'Owner')
def get_money_ledger_api(request):
    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    if not school_id or not session_id:
        logger.warning(f'Money ledger requested without school/session user={request.user.id}')
        return SuccessResponse('Money ledger loaded.', data={'summary': {}, 'rows': []}).to_json_response()

    payload = _money_ledger_payload(request=request, school_id=school_id, session_id=session_id)
    logger.info(f'Money ledger loaded rows={len(payload["rows"])} school={school_id} session={session_id} user={request.user.id}')
    return SuccessResponse('Money ledger loaded.', data=payload).to_json_response()


@login_required
@check_groups('Admin', 'Owner')
def money_ledger_rows_api(request):
    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    draw = _safe_int(request.GET.get('draw'), 1)
    start = max(_safe_int(request.GET.get('start'), 0), 0)
    length = _safe_int(request.GET.get('length'), 10)
    if length < 0:
        length = 10_000
    search = (request.GET.get('search[value]') or '').strip().lower()
    order_col_idx = _safe_int(request.GET.get('order[0][column]'), 0)
    order_dir = (request.GET.get('order[0][dir]') or 'desc').lower()

    if not school_id or not session_id:
        logger.warning(f'Money ledger rows requested without school/session user={request.user.id}')
        return _datatable_json_response(draw=draw, total_count=0, filtered_count=0, rows=[])

    payload = _money_ledger_payload(request=request, school_id=school_id, session_id=session_id)
    rows = payload['rows']
    total_count = len(rows)
    if search:
        rows = [
            row for row in rows
            if search in ' '.join([
                str(row.get('date', '')),
                str(row.get('txnNo', '')),
                str(row.get('referenceNo', '')),
                str(row.get('txnTypeLabel', '')),
                str(row.get('direction', '')),
                str(row.get('sourceModule', '')),
                str(row.get('description', '')),
            ]).lower()
        ]
    filtered_count = len(rows)

    sort_keys = {
        0: lambda row: row.get('dateObj') or datetime.min.date(),
        1: lambda row: row.get('txnNo') or '',
        2: lambda row: row.get('txnTypeLabel') or '',
        3: lambda row: row.get('direction') or '',
        4: lambda row: row.get('sourceModule') or '',
        5: lambda row: row.get('description') or '',
        6: lambda row: row.get('amount') or 0,
        7: lambda row: row.get('txnNo') or '',
    }
    rows.sort(key=sort_keys.get(order_col_idx, sort_keys[0]), reverse=order_dir == 'desc')
    page_rows = rows[start:start + length]
    data = [
        [
            escape(row['date']),
            f'<strong>{escape(row["txnNo"])}</strong><br><span class="muted-text">{escape(row["referenceNo"] or "-")}</span>',
            escape(row['txnTypeLabel']),
            f'<span class="finance-status-pill {escape(row["direction"])}">{escape(row["direction"].replace("_", " "))}</span>',
            f'{escape(row["sourceModule"] or "-")}<br><span class="muted-text">{escape(row["sourceRecordID"] or "")}</span>',
            escape(row['description'] or '-'),
            escape(f'Rs {float(row["amount"]):.2f}'),
            f'<a class="ui mini basic button" href="{escape(_detail_link(row))}"><i class="external alternate icon"></i>Open</a>',
        ]
        for row in page_rows
    ]
    logger.info(f'Money ledger datatable rows prepared total={total_count} filtered={filtered_count} returned={len(data)} user={request.user.id}')
    return _datatable_json_response(draw=draw, total_count=total_count, filtered_count=filtered_count, rows=data)
