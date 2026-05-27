from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import JsonResponse as DjangoJsonResponse
from django.utils.html import escape

from financeApp.models import FinanceAccount, FinanceEntry
from financeApp.services import bootstrap_expense_categories
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


def _cash_bank_account_queryset(*, school_id, session_id):
    return FinanceAccount.objects.filter(
        schoolID_id=school_id,
        sessionID_id=session_id,
        accountType='asset',
        isDeleted=False,
        isActive=True,
    ).filter(
        Q(accountCode__in=['CASH_ON_HAND', 'BANK_MAIN']) | Q(financepaymentmode__isnull=False)
    ).distinct().order_by('accountName', 'id')


def _build_cash_bank_book_payload(*, school_id, session_id, account_id, date_from='', date_to='', user_obj=None):
    if not school_id or not session_id:
        return {'summary': {}, 'rows': []}

    bootstrap_expense_categories(school_id=school_id, session_id=session_id, user_obj=user_obj)
    account_qs = _cash_bank_account_queryset(school_id=school_id, session_id=session_id)
    selected_account = account_qs.filter(pk=account_id).first() if account_id else account_qs.first()
    if not selected_account:
        return {'summary': {}, 'rows': []}

    entry_qs = FinanceEntry.objects.select_related('transactionID', 'partyID').filter(
        transactionID__schoolID_id=school_id,
        transactionID__sessionID_id=session_id,
        transactionID__isDeleted=False,
        accountID_id=selected_account.id,
    ).order_by('transactionID__txnDate', 'transactionID__id', 'lineOrder')

    date_from_value = _parse_filter_date(date_from)
    date_to_value = _parse_filter_date(date_to)
    if date_from and not date_from_value:
        logger.warning(f'Cash bank book ignored invalid dateFrom="{date_from}" account={account_id} school={school_id} session={session_id}')
    if date_to and not date_to_value:
        logger.warning(f'Cash bank book ignored invalid dateTo="{date_to}" account={account_id} school={school_id} session={session_id}')
    if date_from_value:
        entry_qs = entry_qs.filter(transactionID__txnDate__gte=date_from_value)
    if date_to_value:
        entry_qs = entry_qs.filter(transactionID__txnDate__lte=date_to_value)

    running_balance = _decimal_or_zero(selected_account.openingBalance)
    if selected_account.openingBalanceType == 'credit':
        running_balance *= Decimal('-1')

    rows = []
    total_debit = Decimal('0.00')
    total_credit = Decimal('0.00')
    for entry in entry_qs:
        amount = _decimal_or_zero(entry.amount)
        debit = amount if entry.entryType == 'debit' else Decimal('0.00')
        credit = amount if entry.entryType == 'credit' else Decimal('0.00')
        total_debit += debit
        total_credit += credit
        running_balance += debit
        running_balance -= credit
        rows.append({
            'date': entry.transactionID.txnDate.strftime('%d-%m-%Y') if entry.transactionID.txnDate else 'N/A',
            'date_obj': entry.transactionID.txnDate,
            'txnNo': entry.transactionID.txnNo,
            'txnType': entry.transactionID.txnType,
            'reference': entry.transactionID.referenceNo or '',
            'party': entry.partyID.displayName if entry.partyID_id else '',
            'narration': entry.narration or entry.transactionID.description or '',
            'debit': float(debit),
            'credit': float(credit),
            'balance': float(running_balance),
        })

    summary = {
        'accountLabel': str(selected_account),
        'openingBalance': float(selected_account.openingBalance or 0),
        'openingBalanceType': selected_account.openingBalanceType,
        'totalDebit': float(total_debit),
        'totalCredit': float(total_credit),
        'closingBalance': float(running_balance),
    }
    return {'summary': summary, 'rows': rows}


@login_required
@check_groups('Admin', 'Owner')
def get_cash_bank_book_api(request):
    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    account_id = request.GET.get('accountID')
    date_from = request.GET.get('dateFrom')
    date_to = request.GET.get('dateTo')
    if not school_id or not session_id:
        logger.warning(f'Cash bank book requested without school/session user={request.user.id}')
        return SuccessResponse('Cash & bank book loaded.', data={'summary': {}, 'rows': []}).to_json_response()

    try:
        payload = _build_cash_bank_book_payload(
            school_id=school_id,
            session_id=session_id,
            account_id=account_id,
            date_from=date_from or '',
            date_to=date_to or '',
            user_obj=request.user,
        )
        row_count = len(payload.get('rows') or [])
        logger.info(f'Cash bank book loaded account={account_id or "default"} rows={row_count} school={school_id} session={session_id} user={request.user.id}')
        return SuccessResponse('Cash & bank book loaded.', data=payload).to_json_response()
    except Exception as exc:
        logger.exception(f'Unable to load cash bank book account={account_id} school={school_id} session={session_id} user={request.user.id}: {exc}')
        return ErrorResponse('Unable to load cash & bank book.', status_code=500).to_json_response()


@login_required
@check_groups('Admin', 'Owner')
def finance_cash_bank_book_rows_api(request):
    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    account_id = request.GET.get('accountID')
    date_from = request.GET.get('dateFrom')
    date_to = request.GET.get('dateTo')
    draw = _safe_int(request.GET.get('draw'), 1)
    start = max(_safe_int(request.GET.get('start'), 0), 0)
    length = _safe_int(request.GET.get('length'), 10)
    if length < 0:
        length = 10_000
    search = (request.GET.get('search[value]') or '').strip().lower()
    order_col_idx = _safe_int(request.GET.get('order[0][column]'), 0)
    order_dir = (request.GET.get('order[0][dir]') or 'asc').lower()

    if not school_id or not session_id:
        logger.warning(f'Cash bank book rows requested without school/session user={request.user.id}')
        return _datatable_json_response(draw=draw, total_count=0, filtered_count=0, rows=[])

    try:
        payload = _build_cash_bank_book_payload(
            school_id=school_id,
            session_id=session_id,
            account_id=account_id,
            date_from=date_from or '',
            date_to=date_to or '',
            user_obj=request.user,
        )
        rows = payload['rows']
        total_count = len(rows)
        if search:
            rows = [
                row for row in rows
                if search in ' '.join([
                    str(row.get('date', '')),
                    str(row.get('txnNo', '')),
                    str(row.get('txnType', '')),
                    str(row.get('reference', '')),
                    str(row.get('party', '')),
                    str(row.get('narration', '')),
                ]).lower()
            ]
        filtered_count = len(rows)
        sort_keys = {
            0: lambda row: row.get('date_obj') or datetime.min.date(),
            1: lambda row: row.get('txnNo') or '',
            2: lambda row: row.get('txnType') or '',
            3: lambda row: row.get('reference') or '',
            4: lambda row: row.get('party') or '',
            5: lambda row: row.get('narration') or '',
            6: lambda row: row.get('debit') or 0,
            7: lambda row: row.get('credit') or 0,
            8: lambda row: row.get('balance') or 0,
        }
        rows = sorted(rows, key=sort_keys.get(order_col_idx, sort_keys[0]), reverse=(order_dir == 'desc'))
        page_rows = rows[start:start + length]
        data = []
        for row in page_rows:
            data.append([
                escape(row.get('date') or 'N/A'),
                f'<strong>{escape(row.get("txnNo") or "")}</strong>',
                escape(row.get('txnType') or ''),
                escape(row.get('reference') or '-'),
                escape(row.get('party') or '-'),
                escape(row.get('narration') or '-'),
                escape(f'Rs {float(row.get("debit") or 0):.2f}'),
                escape(f'Rs {float(row.get("credit") or 0):.2f}'),
                escape(f'Rs {float(row.get("balance") or 0):.2f}'),
            ])
        logger.info(
            f'Cash bank book rows prepared account={account_id or "default"} total={total_count} '
            f'filtered={filtered_count} returned={len(data)} school={school_id} session={session_id} user={request.user.id}'
        )
        return _datatable_json_response(draw=draw, total_count=total_count, filtered_count=filtered_count, rows=data)
    except Exception as exc:
        logger.exception(f'Unable to load cash bank book rows account={account_id} school={school_id} session={session_id} user={request.user.id}: {exc}')
        return _datatable_json_response(draw=draw, total_count=0, filtered_count=0, rows=[])
