from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.html import escape
from django.views.decorators.csrf import csrf_exempt
from django_datatables_view.base_datatable_view import BaseDatatableView

from financeApp.models import (
    ExpenseCategory,
    ExpenseVoucher,
    FinanceAccount,
    FinanceApprovalRule,
    FinancePaymentMode,
    FinancePeriod,
)
from financeApp.services import (
    bootstrap_expense_categories,
    ensure_named_party,
    generate_finance_document_number,
    sync_expense_voucher_posting,
)
from homeApp.models import SchoolSession
from managementApp.signals import pre_save_with_user
from utils.custom_decorators import check_groups
from utils.custom_response import ErrorResponse, SuccessResponse
from utils.get_school_detail import get_school_id
from utils.logger import logger


VALID_EXPENSE_VOUCHER_STATUSES = {'draft', 'submitted', 'approved', 'paid', 'cancelled', 'reversed'}


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


def _user_label(user):
    full_name = f'{user.first_name} {user.last_name}'.strip()
    return full_name or user.username


def _truthy(value):
    return str(value).strip().lower() in {'1', 'true', 'yes', 'on'}


def _decimal_or_zero(value):
    try:
        return Decimal(str(value or '0')).quantize(Decimal('0.01'))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal('0.00')


def _parse_date(value, *, fallback=None):
    raw = (value or '').strip()
    if not raw:
        return fallback
    for date_format in ('%d/%m/%Y', '%Y-%m-%d', '%d-%m-%Y'):
        try:
            return datetime.strptime(raw, date_format).date()
        except ValueError:
            continue
    raise ValueError


def _finance_status_pill(status_value):
    status = (status_value or 'draft').strip().lower().replace(' ', '_')
    label = status.replace('_', ' ')
    return f'<span class="finance-status-pill {escape(status)}">{escape(label)}</span>'


def _management_edit_delete_buttons(*, edit_handler, delete_handler=None):
    actions = [
        (
            f'<button data-inverted="" data-tooltip="Edit Detail" data-position="left center" '
            f'data-variation="mini" style="font-size:10px;" onclick="{escape(edit_handler)}" '
            f'class="ui circular facebook icon button green"><i class="pencil icon"></i></button>'
        )
    ]
    if delete_handler:
        actions.append(
            (
                f'<button data-inverted="" data-tooltip="Delete" data-position="left center" '
                f'data-variation="mini" style="font-size:10px; margin-left: 3px;" onclick="{escape(delete_handler)}" '
                f'class="ui circular youtube icon button"><i class="trash alternate icon"></i></button>'
            )
        )
    return ''.join(actions)


def _serialize_validation_error(exc):
    if hasattr(exc, 'message_dict'):
        return '; '.join(f'{field}: {", ".join(messages)}' for field, messages in exc.message_dict.items())
    return '; '.join(exc.messages)


def _get_locked_finance_period(*, school_id, session_id, txn_date):
    if not school_id or not session_id or not txn_date:
        return None
    return FinancePeriod.objects.filter(
        schoolID_id=school_id,
        sessionID_id=session_id,
        isDeleted=False,
        periodStart__lte=txn_date,
        periodEnd__gte=txn_date,
        status__in=['soft_locked', 'closed'],
    ).order_by('-periodStart', '-id').first()


def _assert_finance_date_open(*, school_id, session_id, txn_date, label='Transaction date'):
    locked_period = _get_locked_finance_period(
        school_id=school_id,
        session_id=session_id,
        txn_date=txn_date,
    )
    if not locked_period:
        return
    raise ValidationError(
        f'{label} falls inside a {locked_period.get_status_display().lower()} finance period '
        f'({locked_period.periodStart:%d-%m-%Y} to {locked_period.periodEnd:%d-%m-%Y}).'
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


def _apply_finance_approval_rules(*, school_id, session_id, document_type, requested_status, amount, approvable_statuses):
    requested_status = (requested_status or 'draft').strip()
    if requested_status not in set(approvable_statuses):
        return {
            'effective_status': requested_status,
            'requested_status': requested_status,
            'rule': None,
            'rule_mode': 'direct_allowed',
            'requires_queue': False,
        }
    rule_obj, rule_mode = _resolve_finance_approval_rule(
        school_id=school_id,
        session_id=session_id,
        document_type=document_type,
        amount=amount,
    )
    effective_status = 'submitted' if rule_mode == 'approval_required' else requested_status
    return {
        'effective_status': effective_status,
        'requested_status': requested_status,
        'rule': rule_obj,
        'rule_mode': rule_mode,
        'requires_queue': effective_status == 'submitted' and requested_status != 'submitted',
    }


def _apply_expense_voucher_approval_rules(*, school_id, session_id, requested_status, net_amount):
    return _apply_finance_approval_rules(
        school_id=school_id,
        session_id=session_id,
        document_type='expense_voucher',
        requested_status=requested_status,
        amount=net_amount,
        approvable_statuses={'approved', 'paid'},
    )


def _voucher_to_dict(row):
    return {
        'id': row.id,
        'voucherNo': row.voucherNo,
        'voucherDate': row.voucherDate.strftime('%d/%m/%Y') if row.voucherDate else '',
        'categoryID': row.expenseCategoryID_id,
        'title': row.title or '',
        'description': row.description or '',
        'categoryName': row.expenseCategoryID.name if row.expenseCategoryID_id else 'N/A',
        'payeeName': row.partyID.displayName if row.partyID_id else '',
        'grossAmount': float(row.grossAmount or 0),
        'deductionAmount': float(row.deductionAmount or 0),
        'netAmount': float(row.netAmount or 0),
        'approvalStatus': row.approvalStatus,
        'requestedApprovalStatus': row.requestedApprovalStatus or row.approvalStatus,
        'isImmediatePayment': bool(row.isImmediatePayment),
        'paymentModeName': row.paymentModeID.name if row.paymentModeID_id else '',
        'paymentModeID': row.paymentModeID_id,
        'paymentAccountID': row.paymentAccountID_id,
        'paymentAccountLabel': str(row.paymentAccountID) if row.paymentAccountID_id else '',
        'billNo': row.billNo or '',
        'billDate': row.billDate.strftime('%d/%m/%Y') if row.billDate else '',
    }


@login_required
@check_groups('Admin', 'Owner')
def get_expense_voucher_list_api(request):
    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    if not school_id or not session_id:
        logger.warning(f'Expense voucher list requested without school/session user={request.user.id}')
        return SuccessResponse('Expense vouchers loaded.', data=[]).to_json_response()

    try:
        rows = ExpenseVoucher.objects.select_related(
            'expenseCategoryID', 'partyID', 'paymentModeID', 'paymentAccountID'
        ).filter(
            schoolID_id=school_id,
            sessionID_id=session_id,
            isDeleted=False,
        ).order_by('-voucherDate', '-datetime', '-id')
        data = [_voucher_to_dict(row) for row in rows]
        logger.info(f'Expense voucher list loaded count={len(data)} school={school_id} session={session_id} user={request.user.id}')
        return SuccessResponse('Expense vouchers loaded.', data=data).to_json_response()
    except Exception as exc:
        logger.exception(f'Unable to load expense vouchers school={school_id} session={session_id} user={request.user.id}: {exc}')
        return ErrorResponse('Unable to load expense vouchers.', status_code=500).to_json_response()


class FinanceExpenseVoucherListJson(BaseDatatableView):
    order_columns = ['voucherDate', 'voucherNo', 'title', 'expenseCategoryID__name', 'partyID__displayName',
                     'netAmount', 'approvalStatus', 'paymentModeID__name', 'id']

    def get_initial_queryset(self):
        school_id = _current_school_id(self.request)
        session_id = _current_session_id(self.request)
        if not school_id or not session_id:
            logger.warning(f'Expense voucher datatable requested without school/session user={self.request.user.id}')
            return ExpenseVoucher.objects.none()
        return ExpenseVoucher.objects.select_related(
            'expenseCategoryID', 'partyID', 'paymentModeID', 'paymentAccountID'
        ).filter(
            schoolID_id=school_id,
            sessionID_id=session_id,
            isDeleted=False,
        ).order_by('-voucherDate', '-datetime', '-id')

    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(voucherNo__icontains=search)
                | Q(title__icontains=search)
                | Q(expenseCategoryID__name__icontains=search)
                | Q(partyID__displayName__icontains=search)
                | Q(approvalStatus__icontains=search)
                | Q(paymentModeID__name__icontains=search)
                | Q(description__icontains=search)
            )
        return qs

    def prepare_results(self, qs):
        json_data = []
        for item in qs:
            payment_label = item.paymentModeID.name if item.paymentModeID_id else ''
            if item.paymentAccountID_id:
                payment_label = f'{payment_label} | {item.paymentAccountID}' if payment_label else str(item.paymentAccountID)
            json_data.append([
                escape(item.voucherDate.strftime('%d/%m/%Y') if item.voucherDate else ''),
                f'<strong>{escape(item.voucherNo or "")}</strong>',
                escape(item.title or ''),
                escape(item.expenseCategoryID.name if item.expenseCategoryID_id else 'N/A'),
                escape(item.partyID.displayName if item.partyID_id else '-'),
                escape(f'Rs {float(item.netAmount or 0):.2f}'),
                _finance_status_pill(item.approvalStatus),
                escape(payment_label or '-'),
                _management_edit_delete_buttons(
                    edit_handler=f'editExpenseVoucher({item.id})',
                    delete_handler=f'deleteExpenseVoucher({item.id})',
                ),
            ])
        logger.info(f'Expense voucher datatable prepared rows={len(json_data)} user={self.request.user.id}')
        return json_data


@transaction.atomic
@csrf_exempt
@login_required
@check_groups('Admin', 'Owner')
def upsert_expense_voucher_api(request):
    if request.method != 'POST':
        logger.warning(f'Invalid expense voucher upsert method={request.method} user={request.user.id}')
        return ErrorResponse('Method not allowed.', status_code=405).to_json_response()

    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    if not school_id or not session_id:
        logger.warning(f'Expense voucher upsert missing school/session user={request.user.id}')
        return ErrorResponse('School session was not found.').to_json_response()

    voucher_id = request.POST.get('id')
    try:
        bootstrap_expense_categories(school_id=school_id, session_id=session_id, user_obj=request.user)

        voucher_date_raw = request.POST.get('voucherDate')
        title = (request.POST.get('title') or '').strip()
        category_id = request.POST.get('expenseCategoryID')
        payee_name = (request.POST.get('payeeName') or '').strip()
        description = (request.POST.get('description') or '').strip()
        gross_amount = _decimal_or_zero(request.POST.get('grossAmount'))
        deduction_amount = _decimal_or_zero(request.POST.get('deductionAmount'))
        net_amount = gross_amount - deduction_amount
        approval_status = (request.POST.get('approvalStatus') or 'draft').strip()
        is_immediate = _truthy(request.POST.get('isImmediatePayment'))
        payment_mode_id = request.POST.get('paymentModeID')
        payment_account_id = request.POST.get('paymentAccountID')
        bill_no = (request.POST.get('billNo') or '').strip()
        bill_date_raw = request.POST.get('billDate')

        if approval_status not in VALID_EXPENSE_VOUCHER_STATUSES:
            logger.warning(f'Expense voucher invalid status={approval_status} school={school_id} session={session_id} user={request.user.id}')
            return ErrorResponse('Expense voucher status is invalid.').to_json_response()
        if not title or not category_id or gross_amount <= 0:
            logger.warning(f'Expense voucher validation failed title/category/amount id={voucher_id} school={school_id} session={session_id} user={request.user.id}')
            return ErrorResponse('Voucher title, category, and gross amount are required.').to_json_response()
        if deduction_amount < 0 or net_amount <= 0:
            logger.warning(f'Expense voucher invalid amount gross={gross_amount} deduction={deduction_amount} school={school_id} session={session_id} user={request.user.id}')
            return ErrorResponse('Deduction must be non-negative and net amount must be greater than zero.').to_json_response()

        try:
            voucher_date = _parse_date(voucher_date_raw, fallback=timezone.now().date())
        except ValueError:
            return ErrorResponse('Voucher date is invalid.').to_json_response()
        try:
            _assert_finance_date_open(school_id=school_id, session_id=session_id, txn_date=voucher_date, label='Voucher date')
        except ValidationError as exc:
            logger.warning(f'Expense voucher blocked by locked period date={voucher_date} school={school_id} session={session_id} user={request.user.id}: {_serialize_validation_error(exc)}')
            return ErrorResponse(_serialize_validation_error(exc)).to_json_response()
        try:
            bill_date = _parse_date(bill_date_raw, fallback=None)
        except ValueError:
            return ErrorResponse('Bill date is invalid.').to_json_response()

        category = ExpenseCategory.objects.filter(
            pk=category_id,
            schoolID_id=school_id,
            sessionID_id=session_id,
            isDeleted=False,
            isActive=True,
        ).first()
        if not category:
            logger.warning(f'Expense voucher category not found category={category_id} school={school_id} session={session_id} user={request.user.id}')
            return ErrorResponse('Expense category not found.').to_json_response()

        payment_mode = FinancePaymentMode.objects.filter(
            pk=payment_mode_id,
            schoolID_id=school_id,
            isDeleted=False,
        ).first() if payment_mode_id else None
        payment_account = FinanceAccount.objects.filter(
            pk=payment_account_id,
            schoolID_id=school_id,
            sessionID_id=session_id,
            isDeleted=False,
        ).first() if payment_account_id else None

        if is_immediate and approval_status == 'paid' and not payment_account:
            return ErrorResponse('Payment account is required for paid immediate vouchers.').to_json_response()
        if approval_status == 'paid' and not is_immediate and not payment_account:
            return ErrorResponse('Payment account is required when marking a non-immediate voucher as paid.').to_json_response()

        party = ensure_named_party(
            school_id=school_id,
            session_id=session_id,
            display_name=payee_name,
            party_type='vendor',
            user_obj=request.user,
        )

        instance = None
        if voucher_id:
            instance = ExpenseVoucher.objects.filter(
                pk=voucher_id,
                schoolID_id=school_id,
                sessionID_id=session_id,
                isDeleted=False,
            ).first()
            if not instance:
                logger.warning(f'Expense voucher update target not found id={voucher_id} school={school_id} session={session_id} user={request.user.id}')
                return ErrorResponse('Expense voucher not found.').to_json_response()

        created = instance is None
        if not instance:
            instance = ExpenseVoucher(
                schoolID_id=school_id,
                sessionID_id=session_id,
                voucherNo=generate_finance_document_number(
                    document_type='voucher',
                    school_id=school_id,
                    session_id=session_id,
                    user_obj=request.user,
                ),
                sourceModule='manual_expense_voucher',
            )

        approval_resolution = _apply_expense_voucher_approval_rules(
            school_id=school_id,
            session_id=session_id,
            requested_status=approval_status,
            net_amount=net_amount,
        )
        force_resubmission = False
        resolved_payment_account = payment_account or (payment_mode.linkedAccountID if payment_mode and payment_mode.linkedAccountID_id else None)
        if instance and instance.approvalStatus in {'approved', 'paid'}:
            material_change = any([
                instance.voucherDate != voucher_date,
                instance.partyID_id != (party.id if party else None),
                instance.expenseCategoryID_id != category.id,
                (instance.title or '') != title,
                (instance.description or '') != description,
                _decimal_or_zero(instance.grossAmount) != gross_amount,
                _decimal_or_zero(instance.deductionAmount) != deduction_amount,
                _decimal_or_zero(instance.netAmount) != net_amount,
                instance.paymentModeID_id != (payment_mode.id if payment_mode else None),
                instance.paymentAccountID_id != (resolved_payment_account.id if resolved_payment_account else None),
                (instance.billNo or '') != bill_no,
                instance.billDate != bill_date,
                bool(instance.isImmediatePayment) != bool(is_immediate),
            ])
            if material_change and approval_resolution['requested_status'] in {'approved', 'paid'}:
                force_resubmission = True
                approval_resolution['effective_status'] = 'submitted'

        instance.voucherDate = voucher_date
        instance.partyID = party
        instance.expenseCategoryID = category
        instance.title = title
        instance.description = description
        instance.grossAmount = gross_amount
        instance.deductionAmount = deduction_amount
        instance.netAmount = net_amount
        instance.paymentModeID = payment_mode
        instance.paymentAccountID = resolved_payment_account
        instance.billNo = bill_no
        instance.billDate = bill_date
        instance.requestedApprovalStatus = approval_resolution['requested_status']
        instance.approvalStatus = approval_resolution['effective_status']
        instance.isImmediatePayment = is_immediate
        instance.sourceRecordID = str(instance.id or '')
        instance.isDeleted = False
        instance.lastEditedBy = _user_label(request.user)
        instance.updatedByUserID = request.user
        instance.full_clean()
        pre_save_with_user.send(sender=ExpenseVoucher, instance=instance, user=request.user.pk)
        instance.save()
        if not instance.sourceRecordID:
            instance.sourceRecordID = str(instance.id)
            instance.save(update_fields=['sourceRecordID', 'lastUpdatedOn'])

        sync_expense_voucher_posting(
            voucher_obj=instance,
            school_id=school_id,
            session_id=session_id,
            user_obj=request.user,
        )

        action = 'created' if created else 'updated'
        logger.info(
            f'Expense voucher {action} id={instance.id} no={instance.voucherNo} status={instance.approvalStatus} '
            f'net={instance.netAmount} school={school_id} session={session_id} user={request.user.id}'
        )
        if force_resubmission:
            return SuccessResponse('Approved expense voucher was updated and resubmitted for approval.').to_json_response()
        if approval_resolution['requires_queue']:
            rule_name = approval_resolution['rule'].ruleName if approval_resolution['rule'] else 'approval rule'
            return SuccessResponse(f'Expense voucher saved and submitted for approval based on rule: {rule_name}.').to_json_response()
        return SuccessResponse('Expense voucher saved successfully.', extra={'color': 'green'}).to_json_response()
    except ValidationError as exc:
        logger.warning(f'Expense voucher validation error id={voucher_id} school={school_id} session={session_id} user={request.user.id}: {_serialize_validation_error(exc)}')
        return ErrorResponse(_serialize_validation_error(exc) or 'Unable to save expense voucher.').to_json_response()
    except Exception as exc:
        logger.exception(f'Unable to save expense voucher id={voucher_id} school={school_id} session={session_id} user={request.user.id}: {exc}')
        return ErrorResponse('Unable to save expense voucher.', status_code=500).to_json_response()


@transaction.atomic
@csrf_exempt
@login_required
@check_groups('Admin', 'Owner')
def delete_expense_voucher_api(request):
    if request.method != 'POST':
        logger.warning(f'Invalid expense voucher delete method={request.method} user={request.user.id}')
        return ErrorResponse('Method not allowed.', status_code=405).to_json_response()

    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    voucher_id = request.POST.get('id')
    if not school_id or not session_id:
        logger.warning(f'Expense voucher delete missing school/session id={voucher_id} user={request.user.id}')
        return ErrorResponse('School session was not found.').to_json_response()

    try:
        instance = ExpenseVoucher.objects.filter(
            pk=voucher_id,
            schoolID_id=school_id,
            sessionID_id=session_id,
            isDeleted=False,
        ).first()
        if not instance:
            logger.warning(f'Expense voucher delete target not found id={voucher_id} school={school_id} session={session_id} user={request.user.id}')
            return ErrorResponse('Expense voucher not found.').to_json_response()
        try:
            _assert_finance_date_open(
                school_id=school_id,
                session_id=session_id,
                txn_date=instance.voucherDate,
                label='Voucher date',
            )
        except ValidationError as exc:
            logger.warning(f'Expense voucher delete blocked by locked period id={instance.id} school={school_id} session={session_id} user={request.user.id}: {_serialize_validation_error(exc)}')
            return ErrorResponse(_serialize_validation_error(exc)).to_json_response()

        instance.isDeleted = True
        instance.approvalStatus = 'cancelled'
        instance.lastEditedBy = _user_label(request.user)
        instance.updatedByUserID = request.user
        pre_save_with_user.send(sender=ExpenseVoucher, instance=instance, user=request.user.pk)
        instance.save(update_fields=['isDeleted', 'approvalStatus', 'lastEditedBy', 'updatedByUserID', 'lastUpdatedOn'])
        sync_expense_voucher_posting(voucher_obj=instance, school_id=school_id, session_id=session_id, user_obj=request.user)
        logger.info(f'Expense voucher deleted id={instance.id} no={instance.voucherNo} school={school_id} session={session_id} user={request.user.id}')
        return SuccessResponse('Expense voucher deleted successfully.', extra={'color': 'green'}).to_json_response()
    except Exception as exc:
        logger.exception(f'Unable to delete expense voucher id={voucher_id} school={school_id} session={session_id} user={request.user.id}: {exc}')
        return ErrorResponse('Unable to delete expense voucher.', status_code=500).to_json_response()


@transaction.atomic
@csrf_exempt
@login_required
@check_groups('Admin', 'Owner')
def approve_expense_voucher_api(request):
    if request.method != 'POST':
        return ErrorResponse('Method not allowed.', status_code=405).to_json_response()
    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    voucher_id = request.POST.get('id')
    instance = ExpenseVoucher.objects.filter(
        pk=voucher_id,
        schoolID_id=school_id,
        sessionID_id=session_id,
        isDeleted=False,
    ).first()
    if not instance:
        return ErrorResponse('Expense voucher not found.').to_json_response()
    try:
        _assert_finance_date_open(
            school_id=school_id,
            session_id=session_id,
            txn_date=instance.voucherDate,
            label='Voucher date',
        )
    except ValidationError as exc:
        return ErrorResponse(_serialize_validation_error(exc)).to_json_response()
    if instance.approvalStatus not in {'submitted', 'draft'}:
        return ErrorResponse('Only draft or submitted vouchers can be approved.').to_json_response()
    requested_status = (instance.requestedApprovalStatus or instance.approvalStatus or 'approved').strip()
    instance.approvalStatus = requested_status if requested_status in {'approved', 'paid'} else 'approved'
    instance.lastEditedBy = _user_label(request.user)
    instance.updatedByUserID = request.user
    pre_save_with_user.send(sender=ExpenseVoucher, instance=instance, user=request.user.pk)
    instance.save(update_fields=['approvalStatus', 'lastEditedBy', 'updatedByUserID', 'lastUpdatedOn'])
    sync_expense_voucher_posting(voucher_obj=instance, school_id=school_id, session_id=session_id, user_obj=request.user)
    logger.info(
        f'Expense voucher approved id={instance.id} status={instance.approvalStatus} '
        f'school={school_id} session={session_id} user={request.user.id}'
    )
    return SuccessResponse(f'Expense voucher moved to {instance.get_approvalStatus_display().lower()} successfully.').to_json_response()
