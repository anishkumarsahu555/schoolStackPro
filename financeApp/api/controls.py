from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db import connection
from django.db.models import Q
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from financeApp.models import (
    ExpenseVoucher,
    FinanceApprovalRule,
    FinancePeriod,
    PaymentReceipt,
    PaymentRefund,
    PaymentRefundAllocation,
    PayrollLine,
    PayrollRun,
)
from homeApp.models import SchoolSession
from managementApp.signals import pre_save_with_user
from utils.custom_decorators import check_groups
from utils.custom_response import ErrorResponse, SuccessResponse
from utils.get_school_detail import get_school_id
from utils.logger import logger


VALID_APPROVAL_DOCUMENT_TYPES = {key for key, _label in FinanceApprovalRule.DOCUMENT_TYPE_CHOICES}
VALID_APPROVAL_MODES = {key for key, _label in FinanceApprovalRule.APPROVAL_MODE_CHOICES}


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


def _truthy(value):
    return str(value).strip().lower() in {'1', 'true', 'yes', 'on'}


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


def _user_label(user):
    full_name = f'{user.first_name} {user.last_name}'.strip()
    return full_name or user.username


def _serialize_validation_error(exc):
    if hasattr(exc, 'message_dict'):
        return '; '.join(f'{field}: {", ".join(messages)}' for field, messages in exc.message_dict.items())
    return '; '.join(exc.messages)


def _empty_control_center_payload():
    return {
        'summary': {
            'openPeriods': 0,
            'lockedPeriods': 0,
            'pendingApprovals': 0,
            'activeApprovalRules': 0,
        },
        'periods': [],
        'approvalRules': [],
        'pendingApprovals': [],
    }


def _refund_tables_available():
    existing_tables = set(connection.introspection.table_names())
    return (
        PaymentRefund._meta.db_table in existing_tables
        and PaymentRefundAllocation._meta.db_table in existing_tables
    )


def _resolve_finance_approval_rule(*, school_id, session_id, document_type, amount):
    amount = _decimal_or_zero(amount)
    rule_rows = FinanceApprovalRule.objects.filter(
        schoolID_id=school_id,
        sessionID_id=session_id,
        documentType=document_type,
        isDeleted=False,
        isActive=True,
        minAmount__lte=amount,
    ).filter(
        Q(maxAmount__isnull=True) | Q(maxAmount__gte=amount)
    ).order_by('priority', 'minAmount', 'id')
    rule_obj = rule_rows.first()
    if not rule_obj:
        return None, 'direct_allowed'
    return rule_obj, rule_obj.approvalMode


def _pending_sort_date(item):
    if item.get('date') == 'N/A':
        return date.min
    return datetime.strptime(item['date'], '%d-%m-%Y').date()


@login_required
@check_groups('Admin', 'Owner')
def get_finance_control_center_api(request):
    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    if not school_id or not session_id:
        logger.warning(f'Finance controls requested without school/session user={request.user.id}')
        return SuccessResponse('Finance controls loaded successfully.', data=_empty_control_center_payload()).to_json_response()

    try:
        period_rows = FinancePeriod.objects.filter(
            schoolID_id=school_id,
            sessionID_id=session_id,
            isDeleted=False,
        ).order_by('-periodStart', '-id')
        periods = [{
            'id': row.id,
            'periodStart': row.periodStart.strftime('%d-%m-%Y'),
            'periodEnd': row.periodEnd.strftime('%d-%m-%Y'),
            'status': row.status,
            'closedAt': row.closedAt.strftime('%d-%m-%Y %I:%M %p') if row.closedAt else '',
        } for row in period_rows]

        approval_rule_rows = FinanceApprovalRule.objects.filter(
            schoolID_id=school_id,
            sessionID_id=session_id,
            isDeleted=False,
        ).order_by('documentType', 'priority', 'minAmount', 'id')
        approval_rules = [{
            'id': row.id,
            'ruleName': row.ruleName or '',
            'documentType': row.documentType,
            'documentTypeLabel': row.get_documentType_display(),
            'minAmount': float(_decimal_or_zero(row.minAmount)),
            'maxAmount': float(_decimal_or_zero(row.maxAmount)) if row.maxAmount is not None else None,
            'approvalMode': row.approvalMode,
            'approvalModeLabel': row.get_approvalMode_display(),
            'priority': row.priority,
            'isActive': bool(row.isActive),
        } for row in approval_rule_rows]

        pending_approvals = []

        pending_voucher_qs = ExpenseVoucher.objects.select_related('expenseCategoryID', 'paymentModeID').filter(
            schoolID_id=school_id,
            sessionID_id=session_id,
            isDeleted=False,
            approvalStatus='submitted',
        ).order_by('-voucherDate', '-id')
        for row in pending_voucher_qs:
            matched_rule, _ = _resolve_finance_approval_rule(
                school_id=school_id,
                session_id=session_id,
                document_type='expense_voucher',
                amount=_decimal_or_zero(row.netAmount),
            )
            pending_approvals.append({
                'documentType': 'expense_voucher',
                'documentTypeLabel': 'Expense Voucher',
                'id': row.id,
                'documentNo': row.voucherNo,
                'date': row.voucherDate.strftime('%d-%m-%Y') if row.voucherDate else 'N/A',
                'title': row.title or '',
                'meta': row.expenseCategoryID.name if row.expenseCategoryID_id else '',
                'amount': float(_decimal_or_zero(row.netAmount)),
                'requestedApprovalStatus': row.requestedApprovalStatus or row.approvalStatus,
                'matchedRuleLabel': matched_rule.ruleName if matched_rule else '',
                'approveAction': 'approveVoucher',
            })

        pending_receipt_qs = PaymentReceipt.objects.select_related('studentID', 'paymentModeID').filter(
            schoolID_id=school_id,
            sessionID_id=session_id,
            isDeleted=False,
            status='submitted',
        ).order_by('-receiptDate', '-id')
        for row in pending_receipt_qs:
            matched_rule, _ = _resolve_finance_approval_rule(
                school_id=school_id,
                session_id=session_id,
                document_type='payment_receipt',
                amount=_decimal_or_zero(row.amountReceived),
            )
            pending_approvals.append({
                'documentType': 'payment_receipt',
                'documentTypeLabel': 'Payment Receipt',
                'id': row.id,
                'documentNo': row.receiptNo,
                'date': row.receiptDate.strftime('%d-%m-%Y') if row.receiptDate else 'N/A',
                'title': row.receivedFromName or (row.studentID.name if row.studentID_id else 'Receipt'),
                'meta': row.paymentModeID.name if row.paymentModeID_id else '',
                'amount': float(_decimal_or_zero(row.amountReceived)),
                'requestedApprovalStatus': row.requestedApprovalStatus or row.status,
                'matchedRuleLabel': matched_rule.ruleName if matched_rule else '',
                'approveAction': 'approveReceipt',
            })

        if _refund_tables_available():
            pending_refund_qs = PaymentRefund.objects.select_related('studentID', 'paymentModeID', 'receiptID').filter(
                schoolID_id=school_id,
                sessionID_id=session_id,
                isDeleted=False,
                status='submitted',
            ).order_by('-refundDate', '-id')
            for row in pending_refund_qs:
                matched_rule, _ = _resolve_finance_approval_rule(
                    school_id=school_id,
                    session_id=session_id,
                    document_type='payment_refund',
                    amount=_decimal_or_zero(row.amountRefunded),
                )
                pending_approvals.append({
                    'documentType': 'payment_refund',
                    'documentTypeLabel': 'Payment Refund',
                    'id': row.id,
                    'documentNo': row.refundNo,
                    'date': row.refundDate.strftime('%d-%m-%Y') if row.refundDate else 'N/A',
                    'title': row.receiptID.receiptNo if row.receiptID_id else 'Refund',
                    'meta': row.paymentModeID.name if row.paymentModeID_id else '',
                    'amount': float(_decimal_or_zero(row.amountRefunded)),
                    'requestedApprovalStatus': row.requestedApprovalStatus or row.status,
                    'matchedRuleLabel': matched_rule.ruleName if matched_rule else '',
                    'approveAction': 'approveRefund',
                })

        pending_payroll_qs = PayrollRun.objects.prefetch_related('payrollLines').filter(
            schoolID_id=school_id,
            sessionID_id=session_id,
            isDeleted=False,
            status='submitted',
        ).order_by('-year', '-month', '-runDate', '-id')
        for row in pending_payroll_qs:
            total_amount = sum(
                (_decimal_or_zero(line.netAmount) for line in row.payrollLines.all() if _decimal_or_zero(line.netAmount) > 0),
                Decimal('0.00'),
            )
            matched_rule, _ = _resolve_finance_approval_rule(
                school_id=school_id,
                session_id=session_id,
                document_type='payroll_run',
                amount=total_amount,
            )
            pending_approvals.append({
                'documentType': 'payroll_run',
                'documentTypeLabel': 'Payroll Run',
                'id': row.id,
                'documentNo': row.payrollRunNo or f'Payroll {row.month:02d}/{row.year}',
                'date': row.runDate.strftime('%d-%m-%Y') if row.runDate else 'N/A',
                'title': f'Payroll {row.month:02d}/{row.year}',
                'meta': f'{row.payrollLines.count()} line(s)',
                'amount': float(total_amount),
                'requestedApprovalStatus': row.requestedApprovalStatus or row.status,
                'matchedRuleLabel': matched_rule.ruleName if matched_rule else '',
                'approveAction': 'approvePayrollRun',
            })

        pending_payroll_payment_qs = PayrollLine.objects.select_related('payrollRunID', 'teacherID', 'partyID', 'paymentModeID').filter(
            payrollRunID__schoolID_id=school_id,
            payrollRunID__sessionID_id=session_id,
            payrollRunID__isDeleted=False,
            paymentStatus='submitted',
        ).order_by('-paymentDate', '-id')
        for row in pending_payroll_payment_qs:
            matched_rule, _ = _resolve_finance_approval_rule(
                school_id=school_id,
                session_id=session_id,
                document_type='salary_payment',
                amount=_decimal_or_zero(row.netAmount),
            )
            pending_approvals.append({
                'documentType': 'salary_payment',
                'documentTypeLabel': 'Salary Payment',
                'id': row.id,
                'documentNo': f'SAL-{row.id}',
                'date': row.paymentDate.strftime('%d-%m-%Y') if row.paymentDate else 'N/A',
                'title': row.teacherID.name if row.teacherID_id else (row.partyID.displayName if row.partyID_id else 'Salary Payment'),
                'meta': row.paymentModeID.name if row.paymentModeID_id else (row.payrollRunID.payrollRunNo or f'Payroll {row.payrollRunID.month:02d}/{row.payrollRunID.year}'),
                'amount': float(_decimal_or_zero(row.netAmount)),
                'requestedApprovalStatus': row.requestedPaymentStatus or row.paymentStatus,
                'matchedRuleLabel': matched_rule.ruleName if matched_rule else '',
                'approveAction': 'approvePayrollPayment',
            })

        pending_approvals.sort(key=_pending_sort_date, reverse=True)

        locked_count = sum(1 for row in periods if row['status'] in {'soft_locked', 'closed'})
        open_count = sum(1 for row in periods if row['status'] == 'open')

        payload = {
            'summary': {
                'openPeriods': open_count,
                'lockedPeriods': locked_count,
                'pendingApprovals': len(pending_approvals),
                'activeApprovalRules': sum(1 for row in approval_rules if row['isActive']),
            },
            'periods': periods,
            'approvalRules': approval_rules,
            'pendingApprovals': pending_approvals,
        }
        logger.info(
            f'Finance controls loaded periods={len(periods)} rules={len(approval_rules)} '
            f'pending={len(pending_approvals)} school={school_id} session={session_id} user={request.user.id}'
        )
        return SuccessResponse('Finance controls loaded successfully.', data=payload).to_json_response()
    except Exception as exc:
        logger.exception(f'Unable to load finance controls school={school_id} session={session_id} user={request.user.id}: {exc}')
        return ErrorResponse('Unable to load finance controls.', status_code=500).to_json_response()


@transaction.atomic
@csrf_exempt
@login_required
@check_groups('Admin', 'Owner')
def upsert_finance_period_api(request):
    if request.method != 'POST':
        logger.warning(f'Invalid finance period upsert method={request.method} user={request.user.id}')
        return ErrorResponse('Method not allowed.', status_code=405).to_json_response()

    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    if not school_id or not session_id:
        logger.warning(f'Finance period upsert missing school/session user={request.user.id}')
        return ErrorResponse('School session was not found.').to_json_response()

    period_id = request.POST.get('id')
    try:
        period_start = _parse_filter_date(request.POST.get('periodStart'))
        period_end = _parse_filter_date(request.POST.get('periodEnd'))
        status_value = (request.POST.get('status') or 'open').strip()
        if not period_start or not period_end:
            return ErrorResponse('Valid period start and end dates are required.').to_json_response()
        if status_value not in {'open', 'soft_locked', 'closed'}:
            return ErrorResponse('Invalid period status.').to_json_response()

        instance = None
        if period_id:
            instance = FinancePeriod.objects.filter(
                pk=period_id,
                schoolID_id=school_id,
                sessionID_id=session_id,
                isDeleted=False,
            ).first()
            if not instance:
                logger.warning(f'Finance period update target not found id={period_id} school={school_id} session={session_id} user={request.user.id}')
                return ErrorResponse('Finance period not found.').to_json_response()

        overlap_qs = FinancePeriod.objects.filter(
            schoolID_id=school_id,
            sessionID_id=session_id,
            isDeleted=False,
            periodStart__lte=period_end,
            periodEnd__gte=period_start,
        )
        if instance:
            overlap_qs = overlap_qs.exclude(pk=instance.pk)
        if overlap_qs.exists():
            logger.info(f'Finance period overlap blocked start={period_start} end={period_end} school={school_id} session={session_id} user={request.user.id}')
            return ErrorResponse('Finance periods cannot overlap.').to_json_response()

        created = instance is None
        if not instance:
            instance = FinancePeriod(schoolID_id=school_id, sessionID_id=session_id)
        instance.periodStart = period_start
        instance.periodEnd = period_end
        instance.status = status_value
        if status_value == 'closed':
            instance.closedByUserID = request.user
            instance.closedAt = timezone.now()
        else:
            instance.closedByUserID = None
            instance.closedAt = None
        instance.lastEditedBy = _user_label(request.user)
        instance.updatedByUserID = request.user
        instance.full_clean()
        pre_save_with_user.send(sender=FinancePeriod, instance=instance, user=request.user.pk)
        instance.save()

        action = 'created' if created else 'updated'
        logger.info(f'Finance period {action} id={instance.id} status={instance.status} school={school_id} session={session_id} user={request.user.id}')
        return SuccessResponse('Finance period saved successfully.', extra={'color': 'green'}).to_json_response()
    except ValidationError as exc:
        logger.warning(f'Finance period validation error id={period_id} school={school_id} session={session_id} user={request.user.id}: {_serialize_validation_error(exc)}')
        return ErrorResponse(_serialize_validation_error(exc) or 'Unable to save finance period.').to_json_response()
    except Exception as exc:
        logger.exception(f'Unable to save finance period id={period_id} school={school_id} session={session_id} user={request.user.id}: {exc}')
        return ErrorResponse('Unable to save finance period.', status_code=500).to_json_response()


@transaction.atomic
@csrf_exempt
@login_required
@check_groups('Admin', 'Owner')
def upsert_finance_approval_rule_api(request):
    if request.method != 'POST':
        logger.warning(f'Invalid approval rule upsert method={request.method} user={request.user.id}')
        return ErrorResponse('Method not allowed.', status_code=405).to_json_response()

    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    if not school_id or not session_id:
        logger.warning(f'Approval rule upsert missing school/session user={request.user.id}')
        return ErrorResponse('School session was not found.').to_json_response()

    rule_id = request.POST.get('id')
    try:
        rule_name = (request.POST.get('ruleName') or '').strip()
        document_type = (request.POST.get('documentType') or 'expense_voucher').strip()
        approval_mode = (request.POST.get('approvalMode') or 'approval_required').strip()
        min_amount = _decimal_or_zero(request.POST.get('minAmount'))
        max_amount_raw = (request.POST.get('maxAmount') or '').strip()
        max_amount = _decimal_or_zero(max_amount_raw) if max_amount_raw else None
        priority = _safe_int(request.POST.get('priority'), 1)
        is_active = _truthy(request.POST.get('isActive') or 'true')

        if not rule_name:
            logger.warning(f'Approval rule validation failed missing name school={school_id} session={session_id} user={request.user.id}')
            return ErrorResponse('Rule name is required.').to_json_response()
        if document_type not in VALID_APPROVAL_DOCUMENT_TYPES:
            logger.warning(f'Approval rule validation failed invalid document_type={document_type} school={school_id} session={session_id} user={request.user.id}')
            return ErrorResponse('Invalid document type.').to_json_response()
        if approval_mode not in VALID_APPROVAL_MODES:
            logger.warning(f'Approval rule validation failed invalid mode={approval_mode} school={school_id} session={session_id} user={request.user.id}')
            return ErrorResponse('Invalid approval mode.').to_json_response()
        if min_amount < 0:
            return ErrorResponse('Minimum amount cannot be negative.').to_json_response()
        if priority <= 0:
            return ErrorResponse('Priority must be greater than zero.').to_json_response()
        if max_amount is not None and max_amount < min_amount:
            return ErrorResponse('Maximum amount must be greater than or equal to minimum amount.').to_json_response()

        instance = None
        if rule_id:
            instance = FinanceApprovalRule.objects.filter(
                pk=rule_id,
                schoolID_id=school_id,
                sessionID_id=session_id,
                isDeleted=False,
            ).first()
            if not instance:
                logger.warning(f'Approval rule update target not found id={rule_id} school={school_id} session={session_id} user={request.user.id}')
                return ErrorResponse('Approval rule not found.').to_json_response()

        overlap_qs = FinanceApprovalRule.objects.filter(
            schoolID_id=school_id,
            sessionID_id=session_id,
            documentType=document_type,
            isDeleted=False,
            isActive=True,
        )
        if instance:
            overlap_qs = overlap_qs.exclude(pk=instance.pk)
        overlapping = False
        if is_active:
            for row in overlap_qs:
                row_min = _decimal_or_zero(row.minAmount)
                row_max = _decimal_or_zero(row.maxAmount) if row.maxAmount is not None else None
                lower = max(min_amount, row_min)
                upper_candidates = [value for value in [max_amount, row_max] if value is not None]
                upper = min(upper_candidates) if upper_candidates else None
                if upper is None or lower <= upper:
                    overlapping = True
                    break
        if overlapping:
            logger.info(f'Approval rule overlap blocked document_type={document_type} min={min_amount} max={max_amount} school={school_id} session={session_id} user={request.user.id}')
            return ErrorResponse('Approval rule amount ranges cannot overlap for the same document type.').to_json_response()

        created = instance is None
        if not instance:
            instance = FinanceApprovalRule(schoolID_id=school_id, sessionID_id=session_id)
        instance.ruleName = rule_name
        instance.documentType = document_type
        instance.approvalMode = approval_mode
        instance.minAmount = min_amount
        instance.maxAmount = max_amount
        instance.priority = priority
        instance.isActive = is_active
        instance.lastEditedBy = _user_label(request.user)
        instance.updatedByUserID = request.user
        instance.full_clean()
        pre_save_with_user.send(sender=FinanceApprovalRule, instance=instance, user=request.user.pk)
        instance.save()

        action = 'created' if created else 'updated'
        logger.info(f'Approval rule {action} id={instance.id} document_type={instance.documentType} school={school_id} session={session_id} user={request.user.id}')
        return SuccessResponse('Approval rule saved successfully.', extra={'color': 'green'}).to_json_response()
    except ValidationError as exc:
        logger.warning(f'Approval rule validation error id={rule_id} school={school_id} session={session_id} user={request.user.id}: {_serialize_validation_error(exc)}')
        return ErrorResponse(_serialize_validation_error(exc) or 'Unable to save approval rule.').to_json_response()
    except Exception as exc:
        logger.exception(f'Unable to save approval rule id={rule_id} school={school_id} session={session_id} user={request.user.id}: {exc}')
        return ErrorResponse('Unable to save approval rule.', status_code=500).to_json_response()


@transaction.atomic
@csrf_exempt
@login_required
@check_groups('Admin', 'Owner')
def delete_finance_approval_rule_api(request):
    if request.method != 'POST':
        logger.warning(f'Invalid approval rule delete method={request.method} user={request.user.id}')
        return ErrorResponse('Method not allowed.', status_code=405).to_json_response()

    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    rule_id = request.POST.get('id')
    if not school_id or not session_id:
        logger.warning(f'Approval rule delete missing school/session id={rule_id} user={request.user.id}')
        return ErrorResponse('School session was not found.').to_json_response()

    try:
        instance = FinanceApprovalRule.objects.filter(
            pk=rule_id,
            schoolID_id=school_id,
            sessionID_id=session_id,
            isDeleted=False,
        ).first()
        if not instance:
            logger.warning(f'Approval rule delete target not found id={rule_id} school={school_id} session={session_id} user={request.user.id}')
            return ErrorResponse('Approval rule not found.').to_json_response()

        instance.isDeleted = True
        instance.lastEditedBy = _user_label(request.user)
        instance.updatedByUserID = request.user
        pre_save_with_user.send(sender=FinanceApprovalRule, instance=instance, user=request.user.pk)
        instance.save(update_fields=['isDeleted', 'lastEditedBy', 'updatedByUserID', 'lastUpdatedOn'])
        logger.info(f'Approval rule deleted id={instance.id} document_type={instance.documentType} school={school_id} session={session_id} user={request.user.id}')
        return SuccessResponse('Approval rule deleted successfully.', extra={'color': 'green'}).to_json_response()
    except Exception as exc:
        logger.exception(f'Unable to delete approval rule id={rule_id} school={school_id} session={session_id} user={request.user.id}: {exc}')
        return ErrorResponse('Unable to delete approval rule.', status_code=500).to_json_response()
