from datetime import date
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


def _empty_vendor_payables_payload():
    return {
        'summary': {
            'vendorCount': 0,
            'vendorsWithBalance': 0,
            'outstandingAmount': 0,
            'paidAmount': 0,
        },
        'vendorOptions': [],
    }


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


def _vendor_queryset(*, school_id, session_id):
    return FinanceParty.objects.filter(
        schoolID_id=school_id,
        sessionID_id=session_id,
        partyType='vendor',
        isDeleted=False,
    )


def _vendor_voucher_queryset(*, school_id, session_id):
    return ExpenseVoucher.objects.select_related('partyID', 'expenseCategoryID').filter(
        schoolID_id=school_id,
        sessionID_id=session_id,
        isDeleted=False,
        partyID__partyType='vendor',
    )


@login_required
@check_groups('Admin', 'Owner')
def get_vendor_payables_api(request):
    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    if not school_id or not session_id:
        logger.warning(f'Vendor payables requested without school/session user={request.user.id}')
        return SuccessResponse('Vendor payables loaded successfully.', data=_empty_vendor_payables_payload()).to_json_response()

    try:
        vendor_rows = list(_vendor_queryset(school_id=school_id, session_id=session_id).order_by('displayName', 'id').values('id', 'displayName'))
        voucher_qs = _vendor_voucher_queryset(school_id=school_id, session_id=session_id).select_related('partyID')

        outstanding_total = Decimal('0.00')
        paid_total = Decimal('0.00')
        vendor_balances = {}
        for voucher_obj in voucher_qs:
            if not voucher_obj.partyID_id:
                continue
            accrued_amount, paid_amount = _vendor_payable_components(voucher_obj)
            outstanding_amount = accrued_amount - paid_amount
            if outstanding_amount < 0:
                outstanding_amount = Decimal('0.00')
            vendor_balances.setdefault(voucher_obj.partyID_id, Decimal('0.00'))
            vendor_balances[voucher_obj.partyID_id] += outstanding_amount
            outstanding_total += outstanding_amount
            paid_total += paid_amount

        payload = {
            'summary': {
                'vendorCount': len(vendor_rows),
                'vendorsWithBalance': sum(1 for value in vendor_balances.values() if value > 0),
                'outstandingAmount': float(outstanding_total),
                'paidAmount': float(paid_total),
            },
            'vendorOptions': [
                {'ID': row['id'], 'Name': row['displayName'], 'Label': row['displayName']}
                for row in vendor_rows
            ],
        }
        logger.info(
            f'Vendor payables loaded vendors={len(vendor_rows)} outstanding={outstanding_total} '
            f'paid={paid_total} school={school_id} session={session_id} user={request.user.id}'
        )
        return SuccessResponse('Vendor payables loaded successfully.', data=payload).to_json_response()
    except Exception as exc:
        logger.exception(f'Unable to load vendor payables school={school_id} session={session_id} user={request.user.id}: {exc}')
        return ErrorResponse('Unable to load vendor payables.', status_code=500).to_json_response()


@login_required
@check_groups('Admin', 'Owner')
def finance_vendor_payables_rows_api(request):
    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    draw = _safe_int(request.GET.get('draw'), 1)
    start = max(_safe_int(request.GET.get('start'), 0), 0)
    length = _safe_int(request.GET.get('length'), 10)
    if length < 0:
        length = 10_000
    search = (request.GET.get('search[value]') or '').strip().lower()
    vendor_id = request.GET.get('vendorID')
    status_filter = (request.GET.get('status') or 'all').strip().lower()
    order_col_idx = _safe_int(request.GET.get('order[0][column]'), 0)
    order_dir = (request.GET.get('order[0][dir]') or 'asc').lower()
    if not school_id or not session_id:
        logger.warning(f'Vendor payables rows requested without school/session user={request.user.id}')
        return _datatable_json_response(draw=draw, total_count=0, filtered_count=0, rows=[])

    try:
        vendor_map = {
            row.id: row
            for row in _vendor_queryset(school_id=school_id, session_id=session_id)
        }
        voucher_qs = _vendor_voucher_queryset(school_id=school_id, session_id=session_id).order_by('-voucherDate', '-id')
        if vendor_id:
            voucher_qs = voucher_qs.filter(partyID_id=vendor_id)

        summary_rows = {}
        for voucher_obj in voucher_qs:
            if not voucher_obj.partyID_id:
                continue
            vendor_obj = vendor_map.get(voucher_obj.partyID_id)
            if not vendor_obj:
                continue
            accrued_amount, paid_amount = _vendor_payable_components(voucher_obj)
            outstanding_amount = accrued_amount - paid_amount
            if outstanding_amount < 0:
                outstanding_amount = Decimal('0.00')
            row = summary_rows.setdefault(vendor_obj.id, {
                'id': vendor_obj.id,
                'vendorName': vendor_obj.displayName or '',
                'phoneNumber': vendor_obj.phoneNumber or '',
                'voucherCount': 0,
                'accruedAmount': Decimal('0.00'),
                'paidAmount': Decimal('0.00'),
                'outstandingAmount': Decimal('0.00'),
                'lastVoucherDateObj': date.min,
                'lastVoucherDate': 'N/A',
            })
            row['voucherCount'] += 1
            row['accruedAmount'] += accrued_amount
            row['paidAmount'] += paid_amount
            row['outstandingAmount'] += outstanding_amount
            if voucher_obj.voucherDate and voucher_obj.voucherDate >= row['lastVoucherDateObj']:
                row['lastVoucherDateObj'] = voucher_obj.voucherDate
                row['lastVoucherDate'] = voucher_obj.voucherDate.strftime('%d-%m-%Y')

        rows = list(summary_rows.values())
        if status_filter == 'outstanding':
            rows = [row for row in rows if row['outstandingAmount'] > 0]
        elif status_filter == 'settled':
            rows = [row for row in rows if row['voucherCount'] > 0 and row['outstandingAmount'] <= 0]

        total_count = len(rows)
        if search:
            rows = [
                row for row in rows
                if search in ' '.join([
                    row.get('vendorName', ''),
                    row.get('phoneNumber', ''),
                    row.get('lastVoucherDate', ''),
                    str(row.get('voucherCount', '')),
                ]).lower()
            ]
        filtered_count = len(rows)
        sort_keys = {
            0: lambda row: row.get('vendorName') or '',
            1: lambda row: row.get('phoneNumber') or '',
            2: lambda row: row.get('voucherCount') or 0,
            3: lambda row: row.get('accruedAmount') or Decimal('0.00'),
            4: lambda row: row.get('paidAmount') or Decimal('0.00'),
            5: lambda row: row.get('outstandingAmount') or Decimal('0.00'),
            6: lambda row: row.get('lastVoucherDateObj') or date.min,
            7: lambda row: row.get('outstandingAmount') or Decimal('0.00'),
            8: lambda row: row.get('id') or 0,
        }
        rows = sorted(rows, key=sort_keys.get(order_col_idx, sort_keys[0]), reverse=(order_dir == 'desc'))
        page_rows = rows[start:start + length]
        data = []
        for row in page_rows:
            status_label = 'settled' if row['outstandingAmount'] <= 0 else 'outstanding'
            action = (
                f'<a href="/management/finance/vendor-statement/?vendor={row["id"]}" '
                f'data-inverted="" data-tooltip="Open Statement" data-position="left center" data-variation="mini" '
                f'style="font-size:10px;" class="ui circular blue icon button"><i class="book open icon"></i></a>'
            )
            data.append([
                f'<strong>{escape(row["vendorName"])}</strong>',
                escape(row.get('phoneNumber') or '-'),
                escape(str(row.get('voucherCount') or 0)),
                escape(f'Rs {float(row.get("accruedAmount") or 0):.2f}'),
                escape(f'Rs {float(row.get("paidAmount") or 0):.2f}'),
                escape(f'Rs {float(row.get("outstandingAmount") or 0):.2f}'),
                escape(row.get('lastVoucherDate') or 'N/A'),
                _finance_status_pill(status_label),
                action,
            ])
        logger.info(
            f'Vendor payables rows prepared vendor={vendor_id or "all"} status={status_filter} '
            f'total={total_count} filtered={filtered_count} returned={len(data)} school={school_id} session={session_id} user={request.user.id}'
        )
        return _datatable_json_response(draw=draw, total_count=total_count, filtered_count=filtered_count, rows=data)
    except Exception as exc:
        logger.exception(f'Unable to load vendor payables rows school={school_id} session={session_id} user={request.user.id}: {exc}')
        return _datatable_json_response(draw=draw, total_count=0, filtered_count=0, rows=[])


@login_required
@check_groups('Admin', 'Owner')
def finance_vendor_outstanding_voucher_rows_api(request):
    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    draw = _safe_int(request.GET.get('draw'), 1)
    start = max(_safe_int(request.GET.get('start'), 0), 0)
    length = _safe_int(request.GET.get('length'), 10)
    if length < 0:
        length = 10_000
    search = (request.GET.get('search[value]') or '').strip().lower()
    vendor_id = request.GET.get('vendorID')
    status_filter = (request.GET.get('status') or 'outstanding').strip().lower()
    order_col_idx = _safe_int(request.GET.get('order[0][column]'), 0)
    order_dir = (request.GET.get('order[0][dir]') or 'asc').lower()
    if not school_id or not session_id:
        logger.warning(f'Vendor outstanding voucher rows requested without school/session user={request.user.id}')
        return _datatable_json_response(draw=draw, total_count=0, filtered_count=0, rows=[])

    try:
        voucher_qs = _vendor_voucher_queryset(school_id=school_id, session_id=session_id).order_by('-voucherDate', '-id')
        if vendor_id:
            voucher_qs = voucher_qs.filter(partyID_id=vendor_id)

        rows = []
        for voucher_obj in voucher_qs:
            accrued_amount, paid_amount = _vendor_payable_components(voucher_obj)
            outstanding_amount = accrued_amount - paid_amount
            if outstanding_amount < 0:
                outstanding_amount = Decimal('0.00')
            if status_filter == 'outstanding' and outstanding_amount <= 0:
                continue
            if status_filter == 'settled' and outstanding_amount > 0:
                continue
            rows.append({
                'id': voucher_obj.id,
                'voucherDateObj': voucher_obj.voucherDate or date.min,
                'voucherDate': voucher_obj.voucherDate.strftime('%d-%m-%Y') if voucher_obj.voucherDate else 'N/A',
                'voucherNo': voucher_obj.voucherNo or '',
                'vendorName': voucher_obj.partyID.displayName if voucher_obj.partyID_id else '',
                'title': voucher_obj.title or '',
                'categoryName': voucher_obj.expenseCategoryID.name if voucher_obj.expenseCategoryID_id else '',
                'accruedAmount': accrued_amount,
                'paidAmount': paid_amount,
                'outstandingAmount': outstanding_amount,
                'statusLabel': 'settled' if outstanding_amount <= 0 else 'outstanding',
            })

        total_count = len(rows)
        if search:
            rows = [
                row for row in rows
                if search in ' '.join([
                    row.get('voucherDate', ''),
                    row.get('voucherNo', ''),
                    row.get('vendorName', ''),
                    row.get('title', ''),
                    row.get('categoryName', ''),
                ]).lower()
            ]
        filtered_count = len(rows)
        sort_keys = {
            0: lambda row: row.get('voucherDateObj') or date.min,
            1: lambda row: row.get('voucherNo') or '',
            2: lambda row: row.get('vendorName') or '',
            3: lambda row: row.get('title') or '',
            4: lambda row: row.get('categoryName') or '',
            5: lambda row: row.get('accruedAmount') or Decimal('0.00'),
            6: lambda row: row.get('paidAmount') or Decimal('0.00'),
            7: lambda row: row.get('outstandingAmount') or Decimal('0.00'),
            8: lambda row: row.get('statusLabel') or '',
        }
        rows = sorted(rows, key=sort_keys.get(order_col_idx, sort_keys[0]), reverse=(order_dir == 'desc'))
        page_rows = rows[start:start + length]
        data = []
        for row in page_rows:
            data.append([
                escape(row.get('voucherDate') or 'N/A'),
                f'<strong>{escape(row.get("voucherNo") or "")}</strong>',
                escape(row.get('vendorName') or ''),
                escape(row.get('title') or ''),
                escape(row.get('categoryName') or ''),
                escape(f'Rs {float(row.get("accruedAmount") or 0):.2f}'),
                escape(f'Rs {float(row.get("paidAmount") or 0):.2f}'),
                escape(f'Rs {float(row.get("outstandingAmount") or 0):.2f}'),
                _finance_status_pill(row.get('statusLabel')),
            ])
        logger.info(
            f'Vendor outstanding voucher rows prepared vendor={vendor_id or "all"} status={status_filter} '
            f'total={total_count} filtered={filtered_count} returned={len(data)} school={school_id} session={session_id} user={request.user.id}'
        )
        return _datatable_json_response(draw=draw, total_count=total_count, filtered_count=filtered_count, rows=data)
    except Exception as exc:
        logger.exception(f'Unable to load vendor outstanding voucher rows school={school_id} session={session_id} user={request.user.id}: {exc}')
        return _datatable_json_response(draw=draw, total_count=0, filtered_count=0, rows=[])
