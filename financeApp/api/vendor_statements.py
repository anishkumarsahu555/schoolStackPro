from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse as DjangoJsonResponse
from django.utils.html import escape

from financeApp.models import ExpenseVoucher, FinanceParty
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


def _safe_sort_date(value):
    return value or date.min


def _datatable_json_response(*, draw, total_count, filtered_count, rows):
    return DjangoJsonResponse({
        'draw': draw,
        'recordsTotal': total_count,
        'recordsFiltered': filtered_count,
        'data': rows,
    })


def _finance_status_pill(status_value):
    status = (status_value or 'draft').strip().lower().replace(' ', '_')
    label = status.replace('_', ' ')
    return f'<span class="finance-status-pill {escape(status)}">{escape(label)}</span>'


def _vendor_payable_components(voucher_obj):
    amount = _decimal_or_zero(voucher_obj.netAmount)
    accrued_amount = Decimal('0.00')
    paid_amount = Decimal('0.00')
    if voucher_obj.isImmediatePayment:
        if voucher_obj.approvalStatus == 'paid':
            paid_amount = amount
        return accrued_amount, paid_amount
    if voucher_obj.approvalStatus in {'approved', 'paid'}:
        accrued_amount = amount
    if voucher_obj.approvalStatus == 'paid':
        paid_amount = amount
    return accrued_amount, paid_amount


def _empty_vendor_statement_payload():
    return {
        'summary': {
            'vendorName': '',
            'totalAccrued': 0,
            'totalPaid': 0,
            'closingBalance': 0,
            'openVoucherCount': 0,
            'settledVoucherCount': 0,
            'voucherCount': 0,
        },
        'rows': [],
    }


def _build_vendor_statement_payload(*, school_id, session_id, vendor_id):
    if not school_id or not session_id or not vendor_id:
        return _empty_vendor_statement_payload()

    vendor_obj = FinanceParty.objects.filter(
        pk=vendor_id,
        schoolID_id=school_id,
        sessionID_id=session_id,
        partyType='vendor',
        isDeleted=False,
    ).first()
    if not vendor_obj:
        raise ValueError('Vendor not found.')

    voucher_qs = ExpenseVoucher.objects.select_related(
        'expenseCategoryID',
        'paymentModeID',
    ).filter(
        schoolID_id=school_id,
        sessionID_id=session_id,
        partyID_id=vendor_obj.id,
        isDeleted=False,
    ).order_by('voucherDate', 'id')

    events = []
    total_accrued = Decimal('0.00')
    total_paid = Decimal('0.00')
    open_voucher_count = 0
    settled_voucher_count = 0
    voucher_count = 0

    for voucher_obj in voucher_qs:
        amount = _decimal_or_zero(voucher_obj.netAmount)
        if amount <= 0:
            continue
        accrued_amount, paid_amount = _vendor_payable_components(voucher_obj)
        outstanding_amount = accrued_amount - paid_amount
        if outstanding_amount < 0:
            outstanding_amount = Decimal('0.00')
        if accrued_amount > 0 or paid_amount > 0:
            voucher_count += 1
        if outstanding_amount > 0:
            open_voucher_count += 1
        elif accrued_amount > 0 or paid_amount > 0:
            settled_voucher_count += 1

        category_name = voucher_obj.expenseCategoryID.name if voucher_obj.expenseCategoryID_id else 'Expense'
        voucher_title = voucher_obj.title or voucher_obj.description or 'Expense Voucher'
        voucher_status = voucher_obj.approvalStatus or 'draft'
        payment_mode = voucher_obj.paymentModeID.name if voucher_obj.paymentModeID_id else ''

        if voucher_obj.isImmediatePayment and voucher_status == 'paid':
            events.append({
                'date_obj': voucher_obj.voucherDate,
                'sort_key': (_safe_sort_date(voucher_obj.voucherDate), 0, voucher_obj.id, 0),
                'type': 'accrual',
                'reference': voucher_obj.voucherNo or f'VEN-{voucher_obj.id}',
                'category': category_name,
                'label': voucher_title,
                'note': 'Immediate payment voucher accrued and settled on the same date.',
                'debit_value': Decimal('0.00'),
                'credit_value': amount,
                'status': 'accrued',
            })
            total_accrued += amount
            events.append({
                'date_obj': voucher_obj.voucherDate,
                'sort_key': (_safe_sort_date(voucher_obj.voucherDate), 1, voucher_obj.id, 1),
                'type': 'payment',
                'reference': voucher_obj.voucherNo or f'VEN-{voucher_obj.id}',
                'category': category_name,
                'label': voucher_title,
                'note': f'Direct payment{f" via {payment_mode}" if payment_mode else ""}.',
                'debit_value': amount,
                'credit_value': Decimal('0.00'),
                'status': voucher_status,
            })
            total_paid += amount
            continue

        if accrued_amount > 0:
            events.append({
                'date_obj': voucher_obj.voucherDate,
                'sort_key': (_safe_sort_date(voucher_obj.voucherDate), 0, voucher_obj.id, 0),
                'type': 'accrual',
                'reference': voucher_obj.voucherNo or f'VEN-{voucher_obj.id}',
                'category': category_name,
                'label': voucher_title,
                'note': voucher_obj.description or voucher_title,
                'debit_value': Decimal('0.00'),
                'credit_value': accrued_amount,
                'status': 'accrued',
            })
            total_accrued += accrued_amount

        if paid_amount > 0:
            events.append({
                'date_obj': voucher_obj.voucherDate,
                'sort_key': (_safe_sort_date(voucher_obj.voucherDate), 1, voucher_obj.id, 1),
                'type': 'payment',
                'reference': voucher_obj.voucherNo or f'VEN-{voucher_obj.id}',
                'category': category_name,
                'label': voucher_title,
                'note': f'Voucher settled{f" via {payment_mode}" if payment_mode else ""}.',
                'debit_value': paid_amount,
                'credit_value': Decimal('0.00'),
                'status': voucher_status,
            })
            total_paid += paid_amount

    events.sort(key=lambda item: item['sort_key'])
    running_balance = Decimal('0.00')
    rows = []
    for item in events:
        running_balance += item['credit_value']
        running_balance -= item['debit_value']
        rows.append({
            'date': item['date_obj'].strftime('%d-%m-%Y') if item['date_obj'] else 'N/A',
            'date_obj': item['date_obj'],
            'type': item['type'],
            'reference': item['reference'],
            'category': item['category'],
            'label': item['label'],
            'note': item['note'],
            'debit': float(item['debit_value']),
            'credit': float(item['credit_value']),
            'balance': float(running_balance),
            'status': item['status'],
        })

    return {
        'summary': {
            'vendorName': vendor_obj.displayName or 'N/A',
            'totalAccrued': float(total_accrued),
            'totalPaid': float(total_paid),
            'closingBalance': float(total_accrued - total_paid),
            'openVoucherCount': open_voucher_count,
            'settledVoucherCount': settled_voucher_count,
            'voucherCount': voucher_count,
        },
        'rows': rows,
    }


@login_required
@check_groups('Admin', 'Owner')
def get_vendor_statement_api(request):
    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    vendor_id = request.GET.get('vendor')
    if not school_id or not session_id:
        logger.warning(f'Vendor statement requested without school/session vendor={vendor_id} user={request.user.id}')
        return SuccessResponse('Vendor statement loaded successfully.', data=_empty_vendor_statement_payload()).to_json_response()

    try:
        payload = _build_vendor_statement_payload(
            school_id=school_id,
            session_id=session_id,
            vendor_id=vendor_id,
        )
        row_count = len(payload.get('rows') or [])
        logger.info(f'Vendor statement loaded vendor={vendor_id or "none"} rows={row_count} school={school_id} session={session_id} user={request.user.id}')
        return SuccessResponse('Vendor statement loaded successfully.', data=payload).to_json_response()
    except ValueError as exc:
        logger.warning(f'Vendor statement target not found vendor={vendor_id} school={school_id} session={session_id} user={request.user.id}')
        return ErrorResponse(str(exc)).to_json_response()
    except Exception as exc:
        logger.exception(f'Unable to build vendor statement vendor={vendor_id} school={school_id} session={session_id} user={request.user.id}: {exc}')
        return ErrorResponse('Unable to load vendor statement.', status_code=500).to_json_response()


@login_required
@check_groups('Admin', 'Owner')
def finance_vendor_statement_rows_api(request):
    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    vendor_id = request.GET.get('vendor')
    draw = _safe_int(request.GET.get('draw'), 1)
    start = max(_safe_int(request.GET.get('start'), 0), 0)
    length = _safe_int(request.GET.get('length'), 10)
    if length < 0:
        length = 10_000
    search = (request.GET.get('search[value]') or '').strip().lower()
    order_col_idx = _safe_int(request.GET.get('order[0][column]'), 0)
    order_dir = (request.GET.get('order[0][dir]') or 'asc').lower()

    if not school_id or not session_id:
        logger.warning(f'Vendor statement rows requested without school/session vendor={vendor_id} user={request.user.id}')
        return _datatable_json_response(draw=draw, total_count=0, filtered_count=0, rows=[])

    try:
        payload = _build_vendor_statement_payload(
            school_id=school_id,
            session_id=session_id,
            vendor_id=vendor_id,
        )
    except ValueError:
        logger.warning(f'Vendor statement rows target not found vendor={vendor_id} school={school_id} session={session_id} user={request.user.id}')
        return _datatable_json_response(draw=draw, total_count=0, filtered_count=0, rows=[])
    except Exception as exc:
        logger.exception(f'Unable to build vendor statement rows vendor={vendor_id} school={school_id} session={session_id} user={request.user.id}: {exc}')
        return _datatable_json_response(draw=draw, total_count=0, filtered_count=0, rows=[])

    rows = payload['rows']
    total_count = len(rows)
    if search:
        rows = [
            row for row in rows
            if search in ' '.join([
                str(row.get('date', '')),
                str(row.get('type', '')),
                str(row.get('reference', '')),
                str(row.get('category', '')),
                str(row.get('label', '')),
                str(row.get('note', '')),
                str(row.get('status', '')),
            ]).lower()
        ]
    filtered_count = len(rows)

    sort_keys = {
        0: lambda row: row.get('date_obj') or datetime.min.date(),
        1: lambda row: row.get('type') or '',
        2: lambda row: row.get('reference') or '',
        3: lambda row: row.get('category') or '',
        4: lambda row: row.get('label') or '',
        5: lambda row: row.get('note') or '',
        6: lambda row: row.get('debit') or 0,
        7: lambda row: row.get('credit') or 0,
        8: lambda row: row.get('balance') or 0,
        9: lambda row: row.get('status') or '',
    }
    rows = sorted(rows, key=sort_keys.get(order_col_idx, sort_keys[0]), reverse=(order_dir == 'desc'))
    page_rows = rows[start:start + length]
    data = []
    for row in page_rows:
        data.append([
            escape(row.get('date') or 'N/A'),
            escape(str(row.get('type') or '').replace('_', ' ').title()),
            f'<strong>{escape(row.get("reference") or "-")}</strong>',
            escape(row.get('category') or '-'),
            escape(row.get('label') or '-'),
            escape(row.get('note') or '-'),
            escape(f'Rs {float(row.get("debit") or 0):.2f}'),
            escape(f'Rs {float(row.get("credit") or 0):.2f}'),
            escape(f'Rs {float(row.get("balance") or 0):.2f}'),
            _finance_status_pill(row.get('status')),
        ])
    logger.info(
        f'Vendor statement rows prepared vendor={vendor_id or "none"} total={total_count} '
        f'filtered={filtered_count} returned={len(data)} school={school_id} session={session_id} user={request.user.id}'
    )
    return _datatable_json_response(draw=draw, total_count=total_count, filtered_count=filtered_count, rows=data)
