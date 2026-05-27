from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import json

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, Sum
from django.utils.html import escape
from django.views.decorators.csrf import csrf_exempt
from django_datatables_view.base_datatable_view import BaseDatatableView

from financeApp.models import (
    FinanceTransaction,
    FinancePaymentMode,
    PaymentReceipt,
    PaymentRefund,
    PaymentRefundAllocation,
    StudentCharge,
)
from financeApp.services import (
    approve_payment_receipt,
    approve_payment_refund,
    bootstrap_school_finance,
    create_manual_payment_receipt,
    create_payment_refund,
    reverse_payment_receipt,
)
from financeApp.api.expense_vouchers import _apply_finance_approval_rules, _assert_finance_date_open
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


def _serialize_validation_error(exc):
    if hasattr(exc, 'message_dict'):
        return '; '.join(f'{field}: {", ".join(messages)}' for field, messages in exc.message_dict.items())
    return '; '.join(exc.messages)


def _finance_status_pill(status_value):
    status = (status_value or 'draft').strip().lower().replace(' ', '_')
    label = status.replace('_', ' ')
    return f'<span class="finance-status-pill {escape(status)}">{escape(label)}</span>'


def _class_label(student_obj):
    if not student_obj or not student_obj.standardID_id:
        return 'N/A'
    label = student_obj.standardID.name or 'N/A'
    if student_obj.standardID.section:
        label = f'{label} - {student_obj.standardID.section}'
    return label


def _receipt_queryset(*, request, school_id, session_id):
    qs = PaymentReceipt.objects.select_related(
        'studentID',
        'studentID__standardID',
        'partyID',
        'paymentModeID',
        'depositAccountID',
    ).prefetch_related(
        'allocations__studentChargeID__feeHeadID'
    ).filter(
        schoolID_id=school_id,
        sessionID_id=session_id,
        isDeleted=False,
    ).order_by('-receiptDate', '-datetime', '-id')

    student_id = request.GET.get('student')
    status_value = (request.GET.get('status') or '').strip()
    date_from_value = _parse_filter_date(request.GET.get('dateFrom'))
    date_to_value = _parse_filter_date(request.GET.get('dateTo'))
    if student_id:
        qs = qs.filter(studentID_id=student_id)
    if status_value:
        qs = qs.filter(status=status_value)
    if date_from_value:
        qs = qs.filter(receiptDate__gte=date_from_value)
    if date_to_value:
        qs = qs.filter(receiptDate__lte=date_to_value)
    return qs


def _receipt_amounts_and_heads(receipt_obj):
    allocations = list(receipt_obj.allocations.all())
    allocated_total = sum((_decimal_or_zero(item.allocatedAmount) for item in allocations), Decimal('0.00'))
    fee_head_names = sorted({
        item.studentChargeID.feeHeadID.name
        for item in allocations
        if item.studentChargeID_id and item.studentChargeID.feeHeadID_id
    })
    return allocated_total, fee_head_names


@login_required
@check_groups('Admin', 'Owner')
def get_receipt_charge_options_api(request):
    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    student_id = request.GET.get('student')
    if not school_id or not session_id or not student_id:
        logger.warning(f'Receipt charge options requested with incomplete context student={student_id} user={request.user.id}')
        return SuccessResponse('Charge options loaded.', data=[]).to_json_response()

    try:
        rows = StudentCharge.objects.select_related('feeHeadID').filter(
            schoolID_id=school_id,
            sessionID_id=session_id,
            studentID_id=student_id,
            isDeleted=False,
        ).exclude(
            status='cancelled'
        ).order_by('chargeDate', 'id')

        data = []
        for row in rows:
            balance = _decimal_or_zero(row.balanceAmount)
            if balance <= 0:
                continue
            data.append({
                'ID': row.id,
                'Title': row.title or 'Charge',
                'FeeHead': row.feeHeadID.name if row.feeHeadID_id else 'General',
                'Reference': row.referenceNo or '',
                'ChargeDate': row.chargeDate.strftime('%d/%m/%Y') if row.chargeDate else '',
                'NetAmount': float(_decimal_or_zero(row.netAmount)),
                'PaidAmount': float(_decimal_or_zero(row.paidAmount)),
                'BalanceAmount': float(balance),
                'Status': row.status,
            })
        logger.info(f'Receipt charge options loaded count={len(data)} student={student_id} school={school_id} session={session_id} user={request.user.id}')
        return SuccessResponse('Charge options loaded.', data=data).to_json_response()
    except Exception as exc:
        logger.exception(f'Unable to load receipt charge options student={student_id} school={school_id} session={session_id} user={request.user.id}: {exc}')
        return ErrorResponse('Unable to load charge options.', status_code=500).to_json_response()


@login_required
@check_groups('Admin', 'Owner')
def get_receipt_refund_options_api(request):
    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    receipt_id = request.GET.get('receiptID')
    if not school_id or not session_id or not receipt_id:
        logger.warning(f'Receipt refund options requested with incomplete context receipt={receipt_id} user={request.user.id}')
        return SuccessResponse('Refund options loaded.', data=[]).to_json_response()

    try:
        receipt_obj = PaymentReceipt.objects.filter(
            pk=receipt_id,
            schoolID_id=school_id,
            sessionID_id=session_id,
            isDeleted=False,
            status='confirmed',
        ).first()
        if not receipt_obj:
            return SuccessResponse('Refund options loaded.', data=[]).to_json_response()

        refunded_map = {
            row['studentChargeID']: _decimal_or_zero(row['total'])
            for row in PaymentRefundAllocation.objects.filter(
                refundID__receiptID=receipt_obj,
                refundID__isDeleted=False,
                refundID__status='confirmed',
            ).values('studentChargeID').annotate(total=Sum('refundedAmount'))
        }

        data = []
        for row in receipt_obj.allocations.select_related('studentChargeID', 'studentChargeID__feeHeadID').all().order_by('id'):
            refundable = _decimal_or_zero(row.allocatedAmount) - refunded_map.get(row.studentChargeID_id, Decimal('0.00'))
            if refundable <= 0:
                continue
            charge_obj = row.studentChargeID
            data.append({
                'ID': charge_obj.id,
                'Title': charge_obj.title or 'Charge',
                'FeeHead': charge_obj.feeHeadID.name if charge_obj.feeHeadID_id else 'General',
                'AllocatedAmount': float(_decimal_or_zero(row.allocatedAmount)),
                'RefundedAmount': float(refunded_map.get(row.studentChargeID_id, Decimal('0.00'))),
                'RefundableAmount': float(refundable),
            })
        logger.info(f'Receipt refund options loaded count={len(data)} receipt={receipt_id} school={school_id} session={session_id} user={request.user.id}')
        return SuccessResponse('Refund options loaded.', data=data).to_json_response()
    except Exception as exc:
        logger.exception(f'Unable to load receipt refund options receipt={receipt_id} school={school_id} session={session_id} user={request.user.id}: {exc}')
        return ErrorResponse('Unable to load refund options.', status_code=500).to_json_response()


@login_required
@check_groups('Admin', 'Owner')
def get_payment_receipt_list_api(request):
    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    if not school_id or not session_id:
        logger.warning(f'Receipt list requested without school/session user={request.user.id}')
        return SuccessResponse('Receipts loaded successfully.', data={
            'summary': {'totalReceipts': 0, 'totalAmount': 0, 'confirmedAmount': 0, 'cancelledAmount': 0},
            'rows': [],
        }).to_json_response()

    try:
        bootstrap_school_finance(school_id=school_id, session_id=session_id, user_obj=request.user)
        receipt_qs = _receipt_queryset(request=request, school_id=school_id, session_id=session_id)

        total_amount = Decimal('0.00')
        confirmed_amount = Decimal('0.00')
        cancelled_amount = Decimal('0.00')
        rows = []
        for row in receipt_qs:
            amount_received = _decimal_or_zero(row.amountReceived)
            total_amount += amount_received
            if row.status == 'confirmed':
                confirmed_amount += amount_received
            elif row.status == 'cancelled':
                cancelled_amount += amount_received

            allocated_total, fee_head_names = _receipt_amounts_and_heads(row)
            rows.append({
                'id': row.id,
                'receiptNo': row.receiptNo or '',
                'receiptDate': row.receiptDate.strftime('%d-%m-%Y') if row.receiptDate else 'N/A',
                'studentName': row.studentID.name if row.studentID_id and row.studentID.name else '',
                'className': _class_label(row.studentID),
                'receivedFromName': row.receivedFromName or (row.partyID.displayName if row.partyID_id else ''),
                'paymentMode': row.paymentModeID.name if row.paymentModeID_id else '',
                'depositAccount': str(row.depositAccountID) if row.depositAccountID_id else '',
                'amountReceived': float(amount_received),
                'allocatedAmount': float(allocated_total),
                'referenceNo': row.referenceNo or '',
                'status': row.status,
                'sourceModule': row.sourceModule or '',
                'heads': ', '.join(fee_head_names) or 'Receipt',
                'receiptUrl': f'/management/finance/receipt/{row.id}/',
            })

        summary = {
            'totalReceipts': len(rows),
            'totalAmount': float(total_amount),
            'confirmedAmount': float(confirmed_amount),
            'cancelledAmount': float(cancelled_amount),
        }
        logger.info(f'Receipt list loaded count={len(rows)} school={school_id} session={session_id} user={request.user.id}')
        return SuccessResponse('Receipts loaded successfully.', data={'summary': summary, 'rows': rows}).to_json_response()
    except Exception as exc:
        logger.exception(f'Unable to load receipts school={school_id} session={session_id} user={request.user.id}: {exc}')
        return ErrorResponse('Unable to load receipts.', status_code=500).to_json_response()


class FinanceReceiptListJson(BaseDatatableView):
    order_columns = ['receiptDate', 'receiptNo', 'studentID__name', 'studentID__standardID__name', 'receivedFromName',
                     'paymentModeID__name', 'referenceNo', 'amountReceived', 'amountReceived', 'status', 'id']

    def get_initial_queryset(self):
        school_id = _current_school_id(self.request)
        session_id = _current_session_id(self.request)
        if not school_id or not session_id:
            logger.warning(f'Receipt datatable requested without school/session user={self.request.user.id}')
            return PaymentReceipt.objects.none()
        bootstrap_school_finance(school_id=school_id, session_id=session_id, user_obj=self.request.user)
        return _receipt_queryset(request=self.request, school_id=school_id, session_id=session_id)

    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(receiptNo__icontains=search)
                | Q(studentID__name__icontains=search)
                | Q(receivedFromName__icontains=search)
                | Q(referenceNo__icontains=search)
                | Q(paymentModeID__name__icontains=search)
                | Q(partyID__displayName__icontains=search)
                | Q(status__icontains=search)
            )
        return qs

    def prepare_results(self, qs):
        json_data = []
        for item in qs:
            allocated_total, fee_head_names = _receipt_amounts_and_heads(item)
            action_parts = [
                f'<a target="_blank" href="/management/finance/receipt/{item.id}/" '
                f'data-inverted="" data-tooltip="Print Receipt" data-position="left center" data-variation="mini" '
                f'class="ui mini circular blue icon button">'
                f'<i class="print icon"></i></a>',
                f'<button type="button" onclick="openReceiptAdjustments({item.id}, \'{escape(item.receiptNo or "")}\')" '
                f'data-inverted="" data-tooltip="Adjustment History" data-position="left center" data-variation="mini" '
                f'class="ui mini circular teal icon button">'
                f'<i class="history icon"></i></button>',
            ]
            if item.status == 'confirmed':
                action_parts.append(
                    f'<button type="button" onclick="openRefundReceiptModal({item.id}, \'{escape(item.receiptNo or "")}\')" '
                    f'data-inverted="" data-tooltip="Create Refund" data-position="left center" data-variation="mini" '
                    f'class="ui mini circular orange icon button">'
                    f'<i class="reply icon"></i></button>'
                )
                action_parts.append(
                    f'<button type="button" onclick="openReverseReceiptModal({item.id}, \'{escape(item.receiptNo or "")}\')" '
                    f'data-inverted="" data-tooltip="Reverse Receipt" data-position="left center" data-variation="mini" '
                    f'class="ui mini circular red icon button">'
                    f'<i class="undo icon"></i></button>'
                )
            action = '<div class="finance-row-actions">' + ''.join(action_parts) + '</div>'
            display_name = item.studentID.name if item.studentID_id and item.studentID.name else item.receivedFromName or '-'
            json_data.append([
                escape(item.receiptDate.strftime('%d-%m-%Y') if item.receiptDate else 'N/A'),
                f'<strong>{escape(item.receiptNo or "")}</strong>',
                escape(display_name),
                escape(_class_label(item.studentID)),
                escape(', '.join(fee_head_names) or 'Receipt'),
                escape(item.paymentModeID.name if item.paymentModeID_id else '-'),
                escape(item.referenceNo or '-'),
                escape(f'Rs {float(_decimal_or_zero(item.amountReceived)):.2f}'),
                escape(f'Rs {float(allocated_total):.2f}'),
                _finance_status_pill(item.status),
                action,
            ])
        logger.info(f'Receipt datatable prepared rows={len(json_data)} user={self.request.user.id}')
        return json_data


@login_required
@check_groups('Admin', 'Owner')
def get_receipt_adjustment_history_api(request):
    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    receipt_id = request.GET.get('receiptID')
    if not school_id or not session_id or not receipt_id:
        logger.warning(
            f'Receipt adjustment history requested with incomplete context receipt={receipt_id} user={request.user.id}'
        )
        return ErrorResponse('Receipt could not be found.').to_json_response()

    receipt_obj = PaymentReceipt.objects.filter(
        pk=receipt_id,
        schoolID_id=school_id,
        sessionID_id=session_id,
        isDeleted=False,
    ).first()
    if not receipt_obj:
        logger.warning(
            f'Receipt adjustment history requested for missing receipt receipt={receipt_id} '
            f'school={school_id} session={session_id} user={request.user.id}'
        )
        return ErrorResponse('Receipt could not be found.').to_json_response()

    refund_rows = list(PaymentRefund.objects.filter(
        schoolID_id=school_id,
        sessionID_id=session_id,
        receiptID=receipt_obj,
        isDeleted=False,
    ).order_by('-refundDate', '-id'))
    reversal_rows = list(FinanceTransaction.objects.prefetch_related('entries').filter(
        schoolID_id=school_id,
        sessionID_id=session_id,
        isDeleted=False,
        txnType='reversal',
        sourceModule='finance_receipt_reversal',
        sourceRecordID=str(receipt_obj.id),
    ).order_by('-txnDate', '-id'))

    adjustments = []
    total_refunded = Decimal('0.00')
    for row in refund_rows:
        amount = _decimal_or_zero(row.amountRefunded)
        total_refunded += amount
        adjustments.append({
            'type': 'refund',
            'date': row.refundDate.strftime('%d-%m-%Y') if row.refundDate else 'N/A',
            'reference': row.refundNo,
            'amount': float(amount),
            'status': row.status,
            'note': row.notes or '',
        })
    for row in reversal_rows:
        reversal_amount = sum(
            (_decimal_or_zero(entry.amount) for entry in row.entries.all() if entry.entryType == 'debit'),
            Decimal('0.00'),
        )
        adjustments.append({
            'type': 'reversal',
            'date': row.txnDate.strftime('%d-%m-%Y') if row.txnDate else 'N/A',
            'reference': row.referenceNo or row.txnNo,
            'amount': float(reversal_amount),
            'status': row.status,
            'note': row.description or '',
        })
    adjustments.sort(
        key=lambda item: datetime.strptime(item['date'], '%d-%m-%Y').date() if item['date'] != 'N/A' else date.min,
        reverse=True,
    )

    logger.info(
        f'Receipt adjustment history loaded receipt={receipt_obj.id} rows={len(adjustments)} '
        f'school={school_id} session={session_id} user={request.user.id}'
    )
    return SuccessResponse('Receipt adjustments loaded successfully.', data={
        'receipt': {
            'id': receipt_obj.id,
            'receiptNo': receipt_obj.receiptNo,
            'amountReceived': float(_decimal_or_zero(receipt_obj.amountReceived)),
            'status': receipt_obj.status,
            'totalRefunded': float(total_refunded),
        },
        'rows': adjustments,
    }).to_json_response()


@transaction.atomic
@csrf_exempt
@login_required
@check_groups('Admin', 'Owner')
def create_manual_payment_receipt_api(request):
    if request.method != 'POST':
        return ErrorResponse('Method not allowed.', status_code=405).to_json_response()

    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    if not school_id or not session_id:
        logger.warning(f'Manual receipt requested without school/session user={request.user.id}')
        return ErrorResponse('School session was not found.').to_json_response()

    student_id = request.POST.get('studentID')
    payment_mode_id = request.POST.get('paymentModeID')
    receipt_date = _parse_filter_date(request.POST.get('receiptDate'))
    received_from_name = (request.POST.get('receivedFromName') or '').strip()
    reference_no = (request.POST.get('referenceNo') or '').strip()
    notes = (request.POST.get('notes') or '').strip()
    allocations_raw = request.POST.get('allocations') or '[]'
    requested_status = (request.POST.get('status') or 'confirmed').strip()

    if not student_id:
        return ErrorResponse('Student is required.').to_json_response()
    if not receipt_date:
        return ErrorResponse('Valid receipt date is required.').to_json_response()
    try:
        _assert_finance_date_open(school_id=school_id, session_id=session_id, txn_date=receipt_date, label='Receipt date')
    except ValidationError as exc:
        return ErrorResponse('; '.join(exc.messages)).to_json_response()

    try:
        allocations_payload = json.loads(allocations_raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return ErrorResponse('Receipt allocations are invalid.').to_json_response()

    student_obj = Student.objects.filter(
        pk=student_id,
        schoolID_id=school_id,
        sessionID_id=session_id,
        isDeleted=False,
    ).first()
    if not student_obj:
        return ErrorResponse('Student not found.').to_json_response()

    payment_mode = FinancePaymentMode.objects.select_related('linkedAccountID').filter(
        pk=payment_mode_id,
        schoolID_id=school_id,
        isDeleted=False,
        isActive=True,
    ).first()
    if not payment_mode or not payment_mode.linkedAccountID_id:
        return ErrorResponse('A valid payment mode is required.').to_json_response()

    normalized_allocations = []
    for row in allocations_payload if isinstance(allocations_payload, list) else []:
        normalized_allocations.append({
            'charge_id': row.get('chargeID') or row.get('charge_id'),
            'amount': row.get('amount'),
        })

    receipt_amount = sum((_decimal_or_zero(row.get('amount')) for row in normalized_allocations), Decimal('0.00'))
    approval_resolution = _apply_finance_approval_rules(
        school_id=school_id,
        session_id=session_id,
        document_type='payment_receipt',
        requested_status=requested_status,
        amount=receipt_amount,
        approvable_statuses={'confirmed'},
    )

    try:
        receipt_obj = create_manual_payment_receipt(
            school_id=school_id,
            session_id=session_id,
            student_obj=student_obj,
            receipt_date=receipt_date,
            payment_mode_obj=payment_mode,
            allocations=normalized_allocations,
            received_from_name=received_from_name,
            reference_no=reference_no,
            notes=notes,
            status=approval_resolution['effective_status'],
            requested_status=approval_resolution['requested_status'],
            user_obj=request.user,
        )
    except ValidationError as exc:
        return ErrorResponse(_serialize_validation_error(exc) or 'Unable to create receipt.').to_json_response()
    except Exception as exc:
        logger.exception(f'Unable to create manual receipt school={school_id} session={session_id} user={request.user.id}: {exc}')
        return ErrorResponse('Unable to create receipt.', status_code=500).to_json_response()

    payload = {
        'id': receipt_obj.id,
        'receiptNo': receipt_obj.receiptNo,
        'receiptUrl': f'/management/finance/receipt/{receipt_obj.id}/',
    }
    if approval_resolution['requires_queue']:
        rule_name = approval_resolution['rule'].ruleName if approval_resolution['rule'] else 'approval rule'
        logger.info(f'Receipt submitted for approval id={receipt_obj.id} school={school_id} session={session_id} user={request.user.id}')
        return SuccessResponse(f'Receipt saved and submitted for approval based on rule: {rule_name}.', data=payload).to_json_response()

    logger.info(f'Receipt created id={receipt_obj.id} status={receipt_obj.status} school={school_id} session={session_id} user={request.user.id}')
    return SuccessResponse('Receipt created successfully.', data=payload).to_json_response()


@transaction.atomic
@csrf_exempt
@login_required
@check_groups('Admin', 'Owner')
def create_payment_refund_api(request):
    if request.method != 'POST':
        return ErrorResponse('Method not allowed.', status_code=405).to_json_response()

    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    receipt_id = request.POST.get('receiptID')
    payment_mode_id = request.POST.get('paymentModeID')
    refund_date = _parse_filter_date(request.POST.get('refundDate'))
    reference_no = (request.POST.get('referenceNo') or '').strip()
    notes = (request.POST.get('notes') or '').strip()
    allocations_raw = request.POST.get('allocations') or '[]'
    requested_status = (request.POST.get('status') or 'confirmed').strip()

    if not school_id or not session_id or not receipt_id:
        logger.warning(f'Refund requested with incomplete context receipt={receipt_id} user={request.user.id}')
        return ErrorResponse('Receipt could not be found.').to_json_response()
    if not refund_date:
        return ErrorResponse('Valid refund date is required.').to_json_response()
    try:
        _assert_finance_date_open(school_id=school_id, session_id=session_id, txn_date=refund_date, label='Refund date')
    except ValidationError as exc:
        return ErrorResponse('; '.join(exc.messages)).to_json_response()

    receipt_obj = PaymentReceipt.objects.select_related('partyID', 'studentID').filter(
        pk=receipt_id,
        schoolID_id=school_id,
        sessionID_id=session_id,
        isDeleted=False,
    ).first()
    if not receipt_obj:
        return ErrorResponse('Receipt could not be found.').to_json_response()

    payment_mode = FinancePaymentMode.objects.select_related('linkedAccountID').filter(
        pk=payment_mode_id,
        schoolID_id=school_id,
        isDeleted=False,
        isActive=True,
    ).first()
    if not payment_mode or not payment_mode.linkedAccountID_id:
        return ErrorResponse('A valid refund payment mode is required.').to_json_response()

    try:
        allocations_payload = json.loads(allocations_raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return ErrorResponse('Refund allocations are invalid.').to_json_response()

    normalized_allocations = []
    for row in allocations_payload if isinstance(allocations_payload, list) else []:
        normalized_allocations.append({
            'charge_id': row.get('chargeID') or row.get('charge_id'),
            'amount': row.get('amount'),
        })

    refund_amount = sum((_decimal_or_zero(row.get('amount')) for row in normalized_allocations), Decimal('0.00'))
    approval_resolution = _apply_finance_approval_rules(
        school_id=school_id,
        session_id=session_id,
        document_type='payment_refund',
        requested_status=requested_status,
        amount=refund_amount,
        approvable_statuses={'confirmed'},
    )

    try:
        refund_obj = create_payment_refund(
            receipt_obj=receipt_obj,
            school_id=school_id,
            session_id=session_id,
            refund_date=refund_date,
            payment_mode_obj=payment_mode,
            allocations=normalized_allocations,
            reference_no=reference_no,
            notes=notes,
            status=approval_resolution['effective_status'],
            requested_status=approval_resolution['requested_status'],
            user_obj=request.user,
        )
    except ValidationError as exc:
        return ErrorResponse('; '.join(exc.messages) or 'Unable to create refund.').to_json_response()
    except Exception as exc:
        logger.exception(f'Unable to create refund receipt={receipt_id} school={school_id} session={session_id} user={request.user.id}: {exc}')
        return ErrorResponse('Unable to create refund.', status_code=500).to_json_response()

    payload = {'id': refund_obj.id, 'refundNo': refund_obj.refundNo}
    if approval_resolution['requires_queue']:
        rule_name = approval_resolution['rule'].ruleName if approval_resolution['rule'] else 'approval rule'
        logger.info(f'Refund submitted for approval id={refund_obj.id} school={school_id} session={session_id} user={request.user.id}')
        return SuccessResponse(f'Refund saved and submitted for approval based on rule: {rule_name}.', data=payload).to_json_response()

    logger.info(f'Refund created id={refund_obj.id} status={refund_obj.status} school={school_id} session={session_id} user={request.user.id}')
    return SuccessResponse('Refund created successfully.', data=payload).to_json_response()


@transaction.atomic
@csrf_exempt
@login_required
@check_groups('Admin', 'Owner')
def reverse_payment_receipt_api(request):
    if request.method != 'POST':
        return ErrorResponse('Method not allowed.', status_code=405).to_json_response()

    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    receipt_id = request.POST.get('receiptID')
    reason = (request.POST.get('reason') or '').strip()
    if not school_id or not session_id or not receipt_id:
        logger.warning(f'Receipt reversal requested with incomplete context receipt={receipt_id} user={request.user.id}')
        return ErrorResponse('Receipt could not be found.').to_json_response()

    receipt_obj = PaymentReceipt.objects.filter(
        pk=receipt_id,
        schoolID_id=school_id,
        sessionID_id=session_id,
        isDeleted=False,
    ).first()
    if not receipt_obj:
        return ErrorResponse('Receipt could not be found.').to_json_response()
    try:
        _assert_finance_date_open(
            school_id=school_id,
            session_id=session_id,
            txn_date=receipt_obj.receiptDate,
            label='Receipt reversal date',
        )
    except ValidationError as exc:
        return ErrorResponse('; '.join(exc.messages)).to_json_response()

    try:
        reverse_payment_receipt(
            receipt_obj=receipt_obj,
            school_id=school_id,
            session_id=session_id,
            reason=reason,
            user_obj=request.user,
        )
    except ValidationError as exc:
        return ErrorResponse('; '.join(exc.messages) or 'Unable to reverse receipt.').to_json_response()
    except Exception as exc:
        logger.exception(f'Unable to reverse receipt={receipt_id} school={school_id} session={session_id} user={request.user.id}: {exc}')
        return ErrorResponse(f'Unable to reverse receipt: {exc}').to_json_response()

    logger.info(f'Receipt reversed id={receipt_obj.id} school={school_id} session={session_id} user={request.user.id}')
    return SuccessResponse('Receipt reversed successfully.').to_json_response()


@transaction.atomic
@csrf_exempt
@login_required
@check_groups('Admin', 'Owner')
def approve_payment_receipt_api(request):
    if request.method != 'POST':
        return ErrorResponse('Method not allowed.', status_code=405).to_json_response()
    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    receipt_id = request.POST.get('id')
    instance = PaymentReceipt.objects.filter(
        pk=receipt_id,
        schoolID_id=school_id,
        sessionID_id=session_id,
        isDeleted=False,
    ).first()
    if not instance:
        return ErrorResponse('Payment receipt not found.').to_json_response()
    try:
        _assert_finance_date_open(school_id=school_id, session_id=session_id, txn_date=instance.receiptDate, label='Receipt date')
    except ValidationError as exc:
        return ErrorResponse('; '.join(exc.messages)).to_json_response()
    try:
        approve_payment_receipt(receipt_obj=instance, school_id=school_id, session_id=session_id, user_obj=request.user)
    except ValidationError as exc:
        return ErrorResponse('; '.join(exc.messages) or 'Unable to approve payment receipt.').to_json_response()
    logger.info(f'Receipt approved id={instance.id} school={school_id} session={session_id} user={request.user.id}')
    return SuccessResponse('Payment receipt confirmed successfully.').to_json_response()


@transaction.atomic
@csrf_exempt
@login_required
@check_groups('Admin', 'Owner')
def approve_payment_refund_api(request):
    if request.method != 'POST':
        return ErrorResponse('Method not allowed.', status_code=405).to_json_response()
    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    refund_id = request.POST.get('id')
    instance = PaymentRefund.objects.filter(
        pk=refund_id,
        schoolID_id=school_id,
        sessionID_id=session_id,
        isDeleted=False,
    ).first()
    if not instance:
        return ErrorResponse('Payment refund not found.').to_json_response()
    try:
        _assert_finance_date_open(school_id=school_id, session_id=session_id, txn_date=instance.refundDate, label='Refund date')
    except ValidationError as exc:
        return ErrorResponse('; '.join(exc.messages)).to_json_response()
    try:
        approve_payment_refund(refund_obj=instance, school_id=school_id, session_id=session_id, user_obj=request.user)
    except ValidationError as exc:
        return ErrorResponse('; '.join(exc.messages) or 'Unable to approve payment refund.').to_json_response()
    logger.info(f'Refund approved id={instance.id} school={school_id} session={session_id} user={request.user.id}')
    return SuccessResponse('Payment refund confirmed successfully.').to_json_response()
