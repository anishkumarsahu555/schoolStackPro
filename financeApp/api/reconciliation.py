from datetime import date
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.db.models import DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.http import JsonResponse as DjangoJsonResponse
from django.utils.html import escape
from django.views.decorators.csrf import csrf_exempt

from financeApp.models import (
    ExpenseVoucher,
    FinanceTransaction,
    PaymentReceipt,
    PaymentRefund,
    PaymentRefundAllocation,
    PayrollLine,
    StudentCharge,
)
from financeApp.services import (
    rebuild_payment_receipt_ledger,
    refresh_student_charge_balance,
    sync_expense_voucher_posting,
)
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


def _refund_tables_available():
    existing_tables = set(connection.introspection.table_names())
    return (
        PaymentRefund._meta.db_table in existing_tables
        and PaymentRefundAllocation._meta.db_table in existing_tables
    )


def _build_receipt_reconciliation_rows(*, school_id, session_id):
    receipt_rows = []
    receipt_qs = PaymentReceipt.objects.select_related('studentID').filter(
        schoolID_id=school_id,
        sessionID_id=session_id,
        isDeleted=False,
        status='confirmed',
    ).order_by('-receiptDate', '-id')

    txn_source_pairs = set(
        FinanceTransaction.objects.filter(
            schoolID_id=school_id,
            sessionID_id=session_id,
            isDeleted=False,
        ).exclude(
            sourceRecordID__isnull=True,
        ).values_list('sourceModule', 'sourceRecordID')
    )

    for receipt_obj in receipt_qs:
        source_module = receipt_obj.sourceModule or 'finance_manual_receipt'
        source_record_id = str(receipt_obj.sourceRecordID or receipt_obj.id)
        if (source_module, source_record_id) in txn_source_pairs:
            continue
        receipt_rows.append({
            'id': receipt_obj.id,
            'receiptNo': receipt_obj.receiptNo or '',
            'receiptDate': receipt_obj.receiptDate.strftime('%d-%m-%Y') if receipt_obj.receiptDate else 'N/A',
            'receiptDateObj': receipt_obj.receiptDate or date.min,
            'studentName': receipt_obj.studentID.name if receipt_obj.studentID_id else (receipt_obj.receivedFromName or ''),
            'amount': float(_decimal_or_zero(receipt_obj.amountReceived)),
            'issue': 'Missing ledger transaction',
        })
    return receipt_rows


def _build_charge_reconciliation_rows(*, school_id, session_id):
    charge_rows = []
    charge_qs = StudentCharge.objects.select_related('studentID').annotate(
        receipt_total=Coalesce(
            Sum(
                'receiptAllocations__allocatedAmount',
                filter=Q(
                    receiptAllocations__receiptID__isDeleted=False,
                    receiptAllocations__receiptID__status='confirmed',
                ),
            ),
            Value(Decimal('0.00')),
            output_field=DecimalField(max_digits=14, decimal_places=2),
        ),
    ).filter(
        schoolID_id=school_id,
        sessionID_id=session_id,
        isDeleted=False,
    ).order_by('-chargeDate', '-id')

    if _refund_tables_available():
        charge_qs = charge_qs.annotate(
            refund_total=Coalesce(
                Sum(
                    'refundAllocations__refundedAmount',
                    filter=Q(
                        refundAllocations__refundID__isDeleted=False,
                        refundAllocations__refundID__status='confirmed',
                    ),
                ),
                Value(Decimal('0.00')),
                output_field=DecimalField(max_digits=14, decimal_places=2),
            ),
        )

    for charge_obj in charge_qs:
        receipt_total = _decimal_or_zero(getattr(charge_obj, 'receipt_total', Decimal('0.00')))
        refund_total = _decimal_or_zero(getattr(charge_obj, 'refund_total', Decimal('0.00')))
        expected_paid = receipt_total - refund_total
        if expected_paid < 0:
            expected_paid = Decimal('0.00')
        expected_balance = _decimal_or_zero(charge_obj.netAmount) - expected_paid
        if expected_balance < 0:
            expected_balance = Decimal('0.00')

        if (
            _decimal_or_zero(charge_obj.paidAmount) == expected_paid
            and _decimal_or_zero(charge_obj.balanceAmount) == expected_balance
        ):
            continue

        charge_rows.append({
            'id': charge_obj.id,
            'title': charge_obj.title or f'Charge #{charge_obj.id}',
            'studentName': charge_obj.studentID.name if charge_obj.studentID_id else '',
            'currentPaid': float(_decimal_or_zero(charge_obj.paidAmount)),
            'expectedPaid': float(expected_paid),
            'currentBalance': float(_decimal_or_zero(charge_obj.balanceAmount)),
            'expectedBalance': float(expected_balance),
        })
    return charge_rows


def _build_voucher_reconciliation_rows(*, school_id, session_id):
    voucher_rows = []
    voucher_qs = ExpenseVoucher.objects.filter(
        schoolID_id=school_id,
        sessionID_id=session_id,
        isDeleted=False,
        approvalStatus__in=['approved', 'paid'],
    ).order_by('-voucherDate', '-id')

    accrual_ids = {
        source_record_id
        for source_record_id in FinanceTransaction.objects.filter(
            schoolID_id=school_id,
            sessionID_id=session_id,
            isDeleted=False,
            sourceModule='expense_voucher_accrual',
        ).values_list('sourceRecordID', flat=True)
        if source_record_id is not None
    }
    payment_ids = {
        source_record_id
        for source_record_id in FinanceTransaction.objects.filter(
            schoolID_id=school_id,
            sessionID_id=session_id,
            isDeleted=False,
            sourceModule='expense_voucher_payment',
        ).values_list('sourceRecordID', flat=True)
        if source_record_id is not None
    }

    for voucher_obj in voucher_qs:
        missing_parts = []
        voucher_id = str(voucher_obj.id)
        if not voucher_obj.isImmediatePayment and voucher_id not in accrual_ids:
            missing_parts.append('accrual')
        if voucher_obj.approvalStatus == 'paid' and voucher_obj.paymentAccountID_id and voucher_id not in payment_ids:
            missing_parts.append('payment')
        if not missing_parts:
            continue
        voucher_rows.append({
            'id': voucher_obj.id,
            'voucherNo': voucher_obj.voucherNo or '',
            'voucherDate': voucher_obj.voucherDate.strftime('%d-%m-%Y') if voucher_obj.voucherDate else 'N/A',
            'voucherDateObj': voucher_obj.voucherDate or date.min,
            'title': voucher_obj.title or '',
            'amount': float(_decimal_or_zero(voucher_obj.netAmount)),
            'issue': ', '.join(missing_parts),
        })
    return voucher_rows


def _build_payroll_reconciliation_rows(*, school_id, session_id):
    payroll_rows = []
    payroll_qs = PayrollLine.objects.select_related('teacherID', 'partyID', 'payrollRunID').filter(
        payrollRunID__schoolID_id=school_id,
        payrollRunID__sessionID_id=session_id,
        payrollRunID__isDeleted=False,
        paymentStatus='paid',
    ).order_by('-id')

    paid_line_ids = {
        source_record_id
        for source_record_id in FinanceTransaction.objects.filter(
            schoolID_id=school_id,
            sessionID_id=session_id,
            isDeleted=False,
            sourceModule='payroll_line_payment',
        ).values_list('sourceRecordID', flat=True)
        if source_record_id is not None
    }

    for line_obj in payroll_qs:
        if str(line_obj.id) in paid_line_ids:
            continue
        payroll_rows.append({
            'id': line_obj.id,
            'teacherName': (line_obj.teacherID.name if line_obj.teacherID_id else '') or (line_obj.partyID.displayName if line_obj.partyID_id else ''),
            'period': f'{line_obj.payrollRunID.month:02d}/{line_obj.payrollRunID.year}',
            'amount': float(_decimal_or_zero(line_obj.netAmount)),
            'issue': 'Missing salary payment ledger transaction',
        })
    return payroll_rows


@login_required
@check_groups('Admin', 'Owner')
def get_finance_reconciliation_api(request):
    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    if not school_id or not session_id:
        logger.warning(f'Finance reconciliation requested without school/session user={request.user.id}')
        return SuccessResponse('Finance reconciliation loaded successfully.', data={}).to_json_response()

    try:
        receipt_issues = _build_receipt_reconciliation_rows(school_id=school_id, session_id=session_id)
        charge_issues = _build_charge_reconciliation_rows(school_id=school_id, session_id=session_id)
        voucher_issues = _build_voucher_reconciliation_rows(school_id=school_id, session_id=session_id)
        payroll_issues = _build_payroll_reconciliation_rows(school_id=school_id, session_id=session_id)
        data = {
            'summary': {
                'receiptIssues': len(receipt_issues),
                'chargeIssues': len(charge_issues),
                'voucherIssues': len(voucher_issues),
                'payrollIssues': len(payroll_issues),
            },
        }
        logger.info(
            f'Finance reconciliation loaded receipts={len(receipt_issues)} charges={len(charge_issues)} '
            f'vouchers={len(voucher_issues)} payroll={len(payroll_issues)} school={school_id} session={session_id} user={request.user.id}'
        )
        return SuccessResponse('Finance reconciliation loaded successfully.', data=data).to_json_response()
    except Exception as exc:
        logger.exception(f'Unable to load finance reconciliation school={school_id} session={session_id} user={request.user.id}: {exc}')
        return ErrorResponse('Unable to load finance reconciliation.', status_code=500).to_json_response()


def _finance_reconciliation_datatable_response(request, *, rows, sort_keys, row_builder, log_label='reconciliation'):
    draw = _safe_int(request.GET.get('draw'), 1)
    start = max(_safe_int(request.GET.get('start'), 0), 0)
    length = _safe_int(request.GET.get('length'), 10)
    if length < 0:
        length = 10_000
    search = (request.GET.get('search[value]') or '').strip().lower()
    order_col_idx = _safe_int(request.GET.get('order[0][column]'), 0)
    order_dir = (request.GET.get('order[0][dir]') or 'asc').lower()

    total_count = len(rows)
    if search:
        filtered_rows = []
        for row in rows:
            haystack = ' '.join(str(value) for key, value in row.items() if not key.endswith('Obj')).lower()
            if search in haystack:
                filtered_rows.append(row)
        rows = filtered_rows
    filtered_count = len(rows)

    rows = sorted(rows, key=sort_keys.get(order_col_idx, sort_keys[0]), reverse=(order_dir == 'desc'))
    page_rows = rows[start:start + length]
    data = [row_builder(row) for row in page_rows]
    logger.info(f'Finance {log_label} rows prepared total={total_count} filtered={filtered_count} returned={len(data)}')
    return _datatable_json_response(draw=draw, total_count=total_count, filtered_count=filtered_count, rows=data)


@login_required
@check_groups('Admin', 'Owner')
def finance_recon_receipt_rows_api(request):
    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    if not school_id or not session_id:
        logger.warning(f'Reconciliation receipt rows requested without school/session user={request.user.id}')
        return _datatable_json_response(draw=_safe_int(request.GET.get('draw'), 1), total_count=0, filtered_count=0, rows=[])

    rows = _build_receipt_reconciliation_rows(school_id=school_id, session_id=session_id)
    return _finance_reconciliation_datatable_response(
        request,
        rows=rows,
        log_label='receipt reconciliation',
        sort_keys={
            0: lambda row: row.get('receiptDateObj') or date.min,
            1: lambda row: row.get('receiptNo') or '',
            2: lambda row: row.get('studentName') or '',
            3: lambda row: row.get('amount') or 0,
            4: lambda row: row.get('issue') or '',
            5: lambda row: row.get('id') or 0,
        },
        row_builder=lambda row: [
            escape(row.get('receiptDate') or 'N/A'),
            f'<strong>{escape(row.get("receiptNo") or "")}</strong>',
            escape(row.get('studentName') or ''),
            escape(f'Rs {float(row.get("amount") or 0):.2f}'),
            escape(row.get('issue') or ''),
            f'<button type="button" class="ui mini blue button" onclick="repairIssue(\'receipt_ledger\', {row["id"]})"><i class="sync icon"></i>Rebuild</button>',
        ],
    )


@login_required
@check_groups('Admin', 'Owner')
def finance_recon_charge_rows_api(request):
    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    if not school_id or not session_id:
        logger.warning(f'Reconciliation charge rows requested without school/session user={request.user.id}')
        return _datatable_json_response(draw=_safe_int(request.GET.get('draw'), 1), total_count=0, filtered_count=0, rows=[])

    rows = _build_charge_reconciliation_rows(school_id=school_id, session_id=session_id)
    return _finance_reconciliation_datatable_response(
        request,
        rows=rows,
        log_label='charge reconciliation',
        sort_keys={
            0: lambda row: row.get('title') or '',
            1: lambda row: row.get('studentName') or '',
            2: lambda row: row.get('currentPaid') or 0,
            3: lambda row: row.get('expectedPaid') or 0,
            4: lambda row: row.get('currentBalance') or 0,
            5: lambda row: row.get('expectedBalance') or 0,
            6: lambda row: row.get('id') or 0,
        },
        row_builder=lambda row: [
            escape(row.get('title') or ''),
            escape(row.get('studentName') or ''),
            escape(f'Rs {float(row.get("currentPaid") or 0):.2f}'),
            escape(f'Rs {float(row.get("expectedPaid") or 0):.2f}'),
            escape(f'Rs {float(row.get("currentBalance") or 0):.2f}'),
            escape(f'Rs {float(row.get("expectedBalance") or 0):.2f}'),
            f'<button type="button" class="ui mini blue button" onclick="repairIssue(\'charge_balance\', {row["id"]})"><i class="sync icon"></i>Refresh</button>',
        ],
    )


@login_required
@check_groups('Admin', 'Owner')
def finance_recon_voucher_rows_api(request):
    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    if not school_id or not session_id:
        logger.warning(f'Reconciliation voucher rows requested without school/session user={request.user.id}')
        return _datatable_json_response(draw=_safe_int(request.GET.get('draw'), 1), total_count=0, filtered_count=0, rows=[])

    rows = _build_voucher_reconciliation_rows(school_id=school_id, session_id=session_id)
    return _finance_reconciliation_datatable_response(
        request,
        rows=rows,
        log_label='voucher reconciliation',
        sort_keys={
            0: lambda row: row.get('voucherDateObj') or date.min,
            1: lambda row: row.get('voucherNo') or '',
            2: lambda row: row.get('title') or '',
            3: lambda row: row.get('amount') or 0,
            4: lambda row: row.get('issue') or '',
            5: lambda row: row.get('id') or 0,
        },
        row_builder=lambda row: [
            escape(row.get('voucherDate') or 'N/A'),
            f'<strong>{escape(row.get("voucherNo") or "")}</strong>',
            escape(row.get('title') or ''),
            escape(f'Rs {float(row.get("amount") or 0):.2f}'),
            escape(row.get('issue') or ''),
            f'<button type="button" class="ui mini blue button" onclick="repairIssue(\'expense_voucher\', {row["id"]})"><i class="sync icon"></i>Repost</button>',
        ],
    )


@login_required
@check_groups('Admin', 'Owner')
def finance_recon_payroll_rows_api(request):
    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    if not school_id or not session_id:
        logger.warning(f'Reconciliation payroll rows requested without school/session user={request.user.id}')
        return _datatable_json_response(draw=_safe_int(request.GET.get('draw'), 1), total_count=0, filtered_count=0, rows=[])

    rows = _build_payroll_reconciliation_rows(school_id=school_id, session_id=session_id)
    return _finance_reconciliation_datatable_response(
        request,
        rows=rows,
        log_label='payroll reconciliation',
        sort_keys={
            0: lambda row: row.get('teacherName') or '',
            1: lambda row: row.get('period') or '',
            2: lambda row: row.get('amount') or 0,
            3: lambda row: row.get('issue') or '',
        },
        row_builder=lambda row: [
            escape(row.get('teacherName') or ''),
            escape(row.get('period') or ''),
            escape(f'Rs {float(row.get("amount") or 0):.2f}'),
            escape(row.get('issue') or ''),
        ],
    )


@transaction.atomic
@csrf_exempt
@login_required
@check_groups('Admin', 'Owner')
def repair_finance_reconciliation_issue_api(request):
    if request.method != 'POST':
        return ErrorResponse('Method not allowed.', status_code=405).to_json_response()
    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    issue_type = (request.POST.get('issueType') or '').strip()
    record_id = request.POST.get('recordID')
    if not school_id or not session_id or not issue_type or not record_id:
        logger.warning(
            f'Reconciliation repair requested with invalid payload issue={issue_type} '
            f'record={record_id} user={request.user.id}'
        )
        return ErrorResponse('Repair request is invalid.').to_json_response()

    if issue_type == 'receipt_ledger':
        receipt_obj = PaymentReceipt.objects.filter(
            pk=record_id,
            schoolID_id=school_id,
            sessionID_id=session_id,
            isDeleted=False,
        ).first()
        if not receipt_obj:
            return ErrorResponse('Receipt could not be found.').to_json_response()
        try:
            rebuild_payment_receipt_ledger(
                receipt_obj=receipt_obj,
                school_id=school_id,
                session_id=session_id,
                user_obj=request.user,
            )
        except ValidationError as exc:
            return ErrorResponse('; '.join(exc.messages) or 'Unable to rebuild receipt ledger.').to_json_response()
        logger.info(f'Receipt ledger rebuilt receipt={receipt_obj.id} school={school_id} session={session_id} user={request.user.id}')
        return SuccessResponse('Receipt ledger rebuilt successfully.').to_json_response()

    if issue_type == 'charge_balance':
        charge_obj = StudentCharge.objects.filter(
            pk=record_id,
            schoolID_id=school_id,
            sessionID_id=session_id,
            isDeleted=False,
        ).first()
        if not charge_obj:
            return ErrorResponse('Charge could not be found.').to_json_response()
        try:
            refresh_student_charge_balance(charge_obj=charge_obj, user_obj=request.user)
        except ValidationError as exc:
            return ErrorResponse('; '.join(exc.messages) or 'Unable to refresh charge balance.').to_json_response()
        logger.info(f'Charge balance refreshed charge={charge_obj.id} school={school_id} session={session_id} user={request.user.id}')
        return SuccessResponse('Charge balance refreshed successfully.').to_json_response()

    if issue_type == 'expense_voucher':
        voucher_obj = ExpenseVoucher.objects.filter(
            pk=record_id,
            schoolID_id=school_id,
            sessionID_id=session_id,
            isDeleted=False,
        ).first()
        if not voucher_obj:
            return ErrorResponse('Expense voucher could not be found.').to_json_response()
        sync_expense_voucher_posting(voucher_obj=voucher_obj, school_id=school_id, session_id=session_id, user_obj=request.user)
        logger.info(f'Expense voucher posting repaired voucher={voucher_obj.id} school={school_id} session={session_id} user={request.user.id}')
        return SuccessResponse('Expense voucher posting repaired successfully.').to_json_response()

    return ErrorResponse('Unsupported repair type.').to_json_response()
