from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.db.models import Sum
from django.http import JsonResponse as DjangoJsonResponse
from django.utils.html import escape

from financeApp.models import PaymentReceipt, StudentCharge
from homeApp.models import SchoolSession
from managementApp.models import Student
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


def _empty_student_ledger_payload():
    return {
        'summary': {
            'studentName': '',
            'className': '',
            'totalCharged': 0,
            'totalPaid': 0,
            'totalBalance': 0,
        },
        'rows': [],
    }


def _student_class_label(student_obj):
    if not student_obj.standardID_id:
        return 'N/A'
    class_label = student_obj.standardID.name or 'N/A'
    if student_obj.standardID.section:
        class_label = f'{class_label} - {student_obj.standardID.section}'
    return class_label


def _build_student_finance_ledger_payload(*, school_id, session_id, student_id):
    if not school_id or not session_id or not student_id:
        return _empty_student_ledger_payload()

    student_obj = Student.objects.select_related('standardID', 'parentID').filter(
        pk=student_id,
        sessionID_id=session_id,
        isDeleted=False,
    ).first()
    if not student_obj:
        raise ValueError('Student not found.')

    charges = list(
        StudentCharge.objects.select_related('feeHeadID').filter(
            schoolID_id=school_id,
            sessionID_id=session_id,
            studentID_id=student_obj.id,
            isDeleted=False,
        ).order_by('chargeDate', 'datetime', 'id')
    )
    receipts = list(
        PaymentReceipt.objects.select_related('paymentModeID').filter(
            schoolID_id=school_id,
            sessionID_id=session_id,
            studentID_id=student_obj.id,
            isDeleted=False,
            status='confirmed',
        ).prefetch_related('allocations__studentChargeID__feeHeadID').order_by('receiptDate', 'datetime', 'id')
    )

    events = []
    for charge in charges:
        events.append({
            'date_obj': charge.chargeDate,
            'sort_key': (_safe_sort_date(charge.chargeDate), 0, charge.id),
            'type': 'charge',
            'label': charge.title or 'Charge',
            'reference': charge.referenceNo or f'CHG-{charge.id}',
            'debit_value': _decimal_or_zero(charge.netAmount),
            'credit_value': Decimal('0.00'),
            'status': charge.status,
            'note': charge.description or '',
            'feeHead': charge.feeHeadID.name if charge.feeHeadID_id else 'N/A',
            'receiptUrl': '',
        })
    for receipt in receipts:
        allocation_total = receipt.allocations.aggregate(total=Sum('allocatedAmount')).get('total') or Decimal('0.00')
        events.append({
            'date_obj': receipt.receiptDate,
            'sort_key': (_safe_sort_date(receipt.receiptDate), 1, receipt.id),
            'type': 'receipt',
            'label': receipt.receivedFromName or 'Receipt',
            'reference': receipt.receiptNo,
            'debit_value': Decimal('0.00'),
            'credit_value': _decimal_or_zero(allocation_total),
            'status': receipt.status,
            'note': receipt.notes or '',
            'feeHead': ', '.join(sorted(set(
                alloc.studentChargeID.feeHeadID.name
                for alloc in receipt.allocations.all()
                if alloc.studentChargeID_id and alloc.studentChargeID.feeHeadID_id
            ))) or 'Receipt',
            'receiptUrl': f'/management/finance/receipt/{receipt.id}/',
        })

    events.sort(key=lambda item: item['sort_key'])
    running_balance = Decimal('0.00')
    rows = []
    total_charged = Decimal('0.00')
    total_paid = Decimal('0.00')
    for item in events:
        running_balance += item['debit_value']
        running_balance -= item['credit_value']
        total_charged += item['debit_value']
        total_paid += item['credit_value']
        rows.append({
            'date': item['date_obj'].strftime('%d-%m-%Y') if item['date_obj'] else 'N/A',
            'date_obj': item['date_obj'],
            'type': item['type'],
            'label': item['label'],
            'reference': item['reference'],
            'feeHead': item['feeHead'],
            'debit': float(item['debit_value']),
            'credit': float(item['credit_value']),
            'balance': float(running_balance),
            'status': item['status'],
            'note': item['note'],
            'receiptUrl': item['receiptUrl'],
        })

    summary = {
        'studentName': student_obj.name or 'N/A',
        'className': _student_class_label(student_obj),
        'totalCharged': float(total_charged),
        'totalPaid': float(total_paid),
        'totalBalance': float(total_charged - total_paid),
    }
    return {'summary': summary, 'rows': rows}


@login_required
@check_groups('Admin', 'Owner')
def get_student_finance_ledger_api(request):
    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    student_id = request.GET.get('student')
    if not school_id or not session_id:
        logger.warning(f'Student ledger requested without school/session student={student_id} user={request.user.id}')
        return SuccessResponse('Student ledger loaded successfully.', data=_empty_student_ledger_payload()).to_json_response()

    try:
        payload = _build_student_finance_ledger_payload(
            school_id=school_id,
            session_id=session_id,
            student_id=student_id,
        )
        row_count = len(payload.get('rows') or [])
        logger.info(f'Student ledger loaded student={student_id or "none"} rows={row_count} school={school_id} session={session_id} user={request.user.id}')
        return SuccessResponse('Student ledger loaded successfully.', data=payload).to_json_response()
    except ValueError as exc:
        logger.warning(f'Student ledger target not found student={student_id} school={school_id} session={session_id} user={request.user.id}')
        return ErrorResponse(str(exc)).to_json_response()
    except Exception as exc:
        logger.exception(f'Unable to build student ledger student={student_id} school={school_id} session={session_id} user={request.user.id}: {exc}')
        return ErrorResponse('Unable to load student ledger.', status_code=500).to_json_response()


@login_required
@check_groups('Admin', 'Owner')
def finance_student_ledger_rows_api(request):
    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    student_id = request.GET.get('student')
    draw = _safe_int(request.GET.get('draw'), 1)
    start = max(_safe_int(request.GET.get('start'), 0), 0)
    length = _safe_int(request.GET.get('length'), 10)
    if length < 0:
        length = 10_000
    search = (request.GET.get('search[value]') or '').strip().lower()
    order_col_idx = _safe_int(request.GET.get('order[0][column]'), 0)
    order_dir = (request.GET.get('order[0][dir]') or 'asc').lower()

    if not school_id or not session_id:
        logger.warning(f'Student ledger rows requested without school/session student={student_id} user={request.user.id}')
        return _datatable_json_response(draw=draw, total_count=0, filtered_count=0, rows=[])

    try:
        payload = _build_student_finance_ledger_payload(
            school_id=school_id,
            session_id=session_id,
            student_id=student_id,
        )
    except ValueError:
        logger.warning(f'Student ledger rows target not found student={student_id} school={school_id} session={session_id} user={request.user.id}')
        return _datatable_json_response(draw=draw, total_count=0, filtered_count=0, rows=[])
    except Exception as exc:
        logger.exception(f'Unable to build student ledger rows student={student_id} school={school_id} session={session_id} user={request.user.id}: {exc}')
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
                str(row.get('feeHead', '')),
                str(row.get('note', '')),
                str(row.get('status', '')),
            ]).lower()
        ]
    filtered_count = len(rows)

    sort_keys = {
        0: lambda row: row.get('date_obj') or datetime.min.date(),
        1: lambda row: row.get('type') or '',
        2: lambda row: row.get('reference') or '',
        3: lambda row: row.get('feeHead') or '',
        4: lambda row: row.get('note') or '',
        5: lambda row: row.get('debit') or 0,
        6: lambda row: row.get('credit') or 0,
        7: lambda row: row.get('balance') or 0,
        8: lambda row: row.get('status') or '',
        9: lambda row: row.get('receiptUrl') or '',
    }
    rows = sorted(rows, key=sort_keys.get(order_col_idx, sort_keys[0]), reverse=(order_dir == 'desc'))
    page_rows = rows[start:start + length]
    data = []
    for row in page_rows:
        action = f'<a class="ui mini blue button" target="_blank" href="{escape(row["receiptUrl"])}"><i class="print icon"></i>Receipt</a>' if row.get('receiptUrl') else '-'
        data.append([
            escape(row.get('date') or 'N/A'),
            escape(row.get('type') or ''),
            f'<strong>{escape(row.get("reference") or "")}</strong>',
            escape(row.get('feeHead') or ''),
            escape(row.get('note') or row.get('label') or ''),
            escape(f'Rs {float(row.get("debit") or 0):.2f}'),
            escape(f'Rs {float(row.get("credit") or 0):.2f}'),
            escape(f'Rs {float(row.get("balance") or 0):.2f}'),
            _finance_status_pill(row.get('status')),
            action,
        ])
    logger.info(
        f'Student ledger rows prepared student={student_id or "none"} total={total_count} '
        f'filtered={filtered_count} returned={len(data)} school={school_id} session={session_id} user={request.user.id}'
    )
    return _datatable_json_response(draw=draw, total_count=total_count, filtered_count=filtered_count, rows=data)
