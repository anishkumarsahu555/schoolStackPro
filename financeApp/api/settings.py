from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.views.decorators.csrf import csrf_exempt

from financeApp.models import FinanceAccount, FinanceConfiguration, FinancePaymentMode
from financeApp.services import (
    bootstrap_expense_categories,
    bootstrap_school_finance,
    get_finance_configuration,
    preview_finance_document_number,
)
from homeApp.models import SchoolSession
from managementApp.signals import pre_save_with_user
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


def _safe_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _truthy(value):
    return str(value).strip().lower() in {'1', 'true', 'yes', 'on'}


def _user_label(user):
    full_name = f'{user.first_name} {user.last_name}'.strip()
    return full_name or user.username


def _serialize_validation_error(exc):
    if hasattr(exc, 'message_dict'):
        return '; '.join(f'{field}: {", ".join(messages)}' for field, messages in exc.message_dict.items())
    return '; '.join(exc.messages)


def _serialize_finance_configuration(config_obj):
    return {
        'id': config_obj.id,
        'receiptTitle': config_obj.receiptTitle or 'Payment Receipt',
        'receiptFooterNote': config_obj.receiptFooterNote or '',
        'defaultCashAccountID': config_obj.defaultCashAccountID_id,
        'defaultBankAccountID': config_obj.defaultBankAccountID_id,
        'receiptPrefix': config_obj.receiptPrefix or 'RCT',
        'voucherPrefix': config_obj.voucherPrefix or 'EXP',
        'refundPrefix': config_obj.refundPrefix or 'RFD',
        'transactionPrefix': config_obj.transactionPrefix or 'TXN',
        'payrollPrefix': config_obj.payrollPrefix or 'PAY',
        'sequencePadding': config_obj.sequencePadding or 5,
        'includeDateSegment': bool(config_obj.includeDateSegment),
    }


def _account_options(*, school_id, session_id):
    rows = FinanceAccount.objects.filter(
        schoolID_id=school_id,
        sessionID_id=session_id,
        isDeleted=False,
        isActive=True,
    ).order_by('accountType', 'accountName').values('id', 'accountCode', 'accountName', 'accountType')
    return [
        {
            'ID': row['id'],
            'Code': row['accountCode'],
            'Name': row['accountName'],
            'Type': row['accountType'],
            'Label': f"{row['accountCode']} - {row['accountName']}",
        }
        for row in rows
    ]


def _document_previews(*, school_id, session_id, user_obj):
    return {
        'receipt': preview_finance_document_number(document_type='receipt', school_id=school_id, session_id=session_id, user_obj=user_obj),
        'voucher': preview_finance_document_number(document_type='voucher', school_id=school_id, session_id=session_id, user_obj=user_obj),
        'refund': preview_finance_document_number(document_type='refund', school_id=school_id, session_id=session_id, user_obj=user_obj),
        'transaction': preview_finance_document_number(document_type='transaction', school_id=school_id, session_id=session_id, user_obj=user_obj),
        'payroll': preview_finance_document_number(document_type='payroll', school_id=school_id, session_id=session_id, user_obj=user_obj),
    }


@login_required
@check_groups('Admin', 'Owner')
def get_finance_account_options_api(request):
    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    if not school_id or not session_id:
        logger.warning(f'Finance account options requested without school/session user={request.user.id}')
        return SuccessResponse('Finance account options loaded.', data=[]).to_json_response()

    try:
        bootstrap_school_finance(school_id=school_id, session_id=session_id, user_obj=request.user)
        data = _account_options(school_id=school_id, session_id=session_id)
        logger.info(f'Finance account options loaded count={len(data)} school={school_id} session={session_id} user={request.user.id}')
        return SuccessResponse('Finance account options loaded.', data=data).to_json_response()
    except Exception as exc:
        logger.exception(f'Unable to load finance account options school={school_id} session={session_id} user={request.user.id}: {exc}')
        return ErrorResponse('Unable to load finance account options.', status_code=500).to_json_response()


@login_required
@check_groups('Admin', 'Owner')
def get_finance_settings_api(request):
    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    if not school_id or not session_id:
        logger.warning(f'Finance settings requested without school/session user={request.user.id}')
        return SuccessResponse('Finance settings loaded successfully.', data={}).to_json_response()

    try:
        bootstrap_school_finance(school_id=school_id, session_id=session_id, user_obj=request.user)
        config_obj = get_finance_configuration(school_id=school_id, session_id=session_id, user_obj=request.user)
        data = {
            'settings': _serialize_finance_configuration(config_obj),
            'accountOptions': _account_options(school_id=school_id, session_id=session_id),
            'previews': _document_previews(school_id=school_id, session_id=session_id, user_obj=request.user),
        }
        logger.info(f'Finance settings loaded school={school_id} session={session_id} user={request.user.id}')
        return SuccessResponse('Finance settings loaded successfully.', data=data).to_json_response()
    except Exception as exc:
        logger.exception(f'Unable to load finance settings school={school_id} session={session_id} user={request.user.id}: {exc}')
        return ErrorResponse('Unable to load finance settings.', status_code=500).to_json_response()


@transaction.atomic
@csrf_exempt
@login_required
@check_groups('Admin', 'Owner')
def upsert_finance_settings_api(request):
    if request.method != 'POST':
        logger.warning(f'Invalid finance settings upsert method={request.method} user={request.user.id}')
        return ErrorResponse('Method not allowed.', status_code=405).to_json_response()

    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    if not school_id or not session_id:
        logger.warning(f'Finance settings upsert missing school/session user={request.user.id}')
        return ErrorResponse('School session was not found.').to_json_response()

    try:
        bootstrap_school_finance(school_id=school_id, session_id=session_id, user_obj=request.user)
        config_obj = FinanceConfiguration.objects.select_for_update().filter(
            schoolID_id=school_id,
            sessionID_id=session_id,
            isDeleted=False,
        ).first()
        if not config_obj:
            config_obj = get_finance_configuration(school_id=school_id, session_id=session_id, user_obj=request.user)
            config_obj = FinanceConfiguration.objects.select_for_update().get(pk=config_obj.pk)

        receipt_title = (request.POST.get('receiptTitle') or 'Payment Receipt').strip()
        receipt_footer_note = (request.POST.get('receiptFooterNote') or '').strip()
        receipt_prefix = (request.POST.get('receiptPrefix') or 'RCT').strip()
        voucher_prefix = (request.POST.get('voucherPrefix') or 'EXP').strip()
        refund_prefix = (request.POST.get('refundPrefix') or 'RFD').strip()
        transaction_prefix = (request.POST.get('transactionPrefix') or 'TXN').strip()
        payroll_prefix = (request.POST.get('payrollPrefix') or 'PAY').strip()
        include_date_segment = _truthy(request.POST.get('includeDateSegment'))
        sequence_padding = _safe_int(request.POST.get('sequencePadding'), 5)
        sequence_padding = max(3, min(sequence_padding, 8))

        cash_account_id = request.POST.get('defaultCashAccountID')
        bank_account_id = request.POST.get('defaultBankAccountID')
        cash_account = FinanceAccount.objects.filter(
            pk=cash_account_id,
            schoolID_id=school_id,
            sessionID_id=session_id,
            isDeleted=False,
            isActive=True,
        ).first() if cash_account_id else None
        bank_account = FinanceAccount.objects.filter(
            pk=bank_account_id,
            schoolID_id=school_id,
            sessionID_id=session_id,
            isDeleted=False,
            isActive=True,
        ).first() if bank_account_id else None

        if cash_account_id and not cash_account:
            return ErrorResponse('Default cash account was not found.').to_json_response()
        if bank_account_id and not bank_account:
            return ErrorResponse('Default bank account was not found.').to_json_response()

        config_obj.receiptTitle = receipt_title[:150] or 'Payment Receipt'
        config_obj.receiptFooterNote = receipt_footer_note
        config_obj.receiptPrefix = receipt_prefix[:20] or 'RCT'
        config_obj.voucherPrefix = voucher_prefix[:20] or 'EXP'
        config_obj.refundPrefix = refund_prefix[:20] or 'RFD'
        config_obj.transactionPrefix = transaction_prefix[:20] or 'TXN'
        config_obj.payrollPrefix = payroll_prefix[:20] or 'PAY'
        config_obj.defaultCashAccountID = cash_account
        config_obj.defaultBankAccountID = bank_account
        config_obj.sequencePadding = sequence_padding
        config_obj.includeDateSegment = include_date_segment
        config_obj.lastEditedBy = _user_label(request.user)
        config_obj.updatedByUserID = request.user
        config_obj.full_clean()
        pre_save_with_user.send(sender=FinanceConfiguration, instance=config_obj, user=request.user.pk)
        config_obj.save()

        bootstrap_school_finance(school_id=school_id, session_id=session_id, user_obj=request.user)
        data = {
            'settings': _serialize_finance_configuration(config_obj),
            'previews': _document_previews(school_id=school_id, session_id=session_id, user_obj=request.user),
        }
        logger.info(f'Finance settings saved id={config_obj.id} school={school_id} session={session_id} user={request.user.id}')
        return SuccessResponse('Finance settings saved successfully.', data=data, extra={'color': 'green'}).to_json_response()
    except ValidationError as exc:
        logger.warning(f'Finance settings validation error school={school_id} session={session_id} user={request.user.id}: {_serialize_validation_error(exc)}')
        return ErrorResponse(_serialize_validation_error(exc) or 'Unable to save finance settings.').to_json_response()
    except Exception as exc:
        logger.exception(f'Unable to save finance settings school={school_id} session={session_id} user={request.user.id}: {exc}')
        return ErrorResponse('Unable to save finance settings.', status_code=500).to_json_response()


@login_required
@check_groups('Admin', 'Owner')
def get_finance_payment_mode_options_api(request):
    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    if not school_id or not session_id:
        logger.warning(f'Payment mode options requested without school/session user={request.user.id}')
        return SuccessResponse('Payment modes loaded.', data=[]).to_json_response()

    try:
        bootstrap_expense_categories(school_id=school_id, session_id=session_id, user_obj=request.user)
        rows = FinancePaymentMode.objects.select_related('linkedAccountID').filter(
            schoolID_id=school_id,
            isDeleted=False,
            isActive=True,
        ).order_by('name')
        data = [
            {
                'ID': row.id,
                'Code': row.code,
                'Name': row.name,
                'Type': row.modeType,
                'LinkedAccountID': row.linkedAccountID_id,
                'LinkedAccountLabel': str(row.linkedAccountID) if row.linkedAccountID_id else '',
            }
            for row in rows
        ]
        logger.info(f'Payment mode options loaded count={len(data)} school={school_id} session={session_id} user={request.user.id}')
        return SuccessResponse('Payment modes loaded.', data=data).to_json_response()
    except Exception as exc:
        logger.exception(f'Unable to load payment modes school={school_id} session={session_id} user={request.user.id}: {exc}')
        return ErrorResponse('Unable to load payment modes.', status_code=500).to_json_response()
