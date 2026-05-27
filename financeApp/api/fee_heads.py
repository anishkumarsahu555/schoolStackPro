from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils.html import escape
from django.views.decorators.csrf import csrf_exempt
from django_datatables_view.base_datatable_view import BaseDatatableView

from financeApp.models import FeeHead, FinanceAccount, StudentCharge
from financeApp.services import bootstrap_school_finance
from homeApp.models import SchoolSession
from managementApp.signals import pre_save_with_user
from utils.custom_decorators import check_groups
from utils.custom_response import ErrorResponse, SuccessResponse
from utils.get_school_detail import get_school_id
from utils.logger import logger


SYSTEM_FEE_HEAD_CODES = {'ADMISSION_FEE', 'MONTHLY_STUDENT_FEE', 'MISC_FEE'}


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


def _safe_int(value, fallback=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _finance_status_pill(status_value):
    status = (status_value or 'draft').strip().lower().replace(' ', '_')
    label = status.replace('_', ' ')
    return f'<span class="finance-status-pill {escape(status)}">{escape(label)}</span>'


def _finance_active_pill(is_active):
    return _finance_status_pill('active' if is_active else 'inactive')


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


@login_required
@check_groups('Admin', 'Owner')
def get_fee_head_list_api(request):
    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    if not school_id or not session_id:
        logger.warning(f'Fee head list requested without school/session user={request.user.id}')
        return SuccessResponse('Fee heads loaded successfully.', data=[]).to_json_response()

    try:
        bootstrap_school_finance(school_id=school_id, session_id=session_id, user_obj=request.user)
        rows = FeeHead.objects.select_related('incomeAccountID', 'receivableAccountID').filter(
            schoolID_id=school_id,
            sessionID_id=session_id,
            isDeleted=False,
        ).order_by('displayOrder', 'name', 'id')
        data = []
        for row in rows:
            data.append({
                'id': row.id,
                'code': row.code or '',
                'name': row.name or '',
                'category': row.category or '',
                'defaultAmount': float(row.defaultAmount or 0),
                'isRecurring': bool(row.isRecurring),
                'recurrenceType': row.recurrenceType or '',
                'incomeAccountID': row.incomeAccountID_id,
                'incomeAccountLabel': str(row.incomeAccountID) if row.incomeAccountID_id else '',
                'receivableAccountID': row.receivableAccountID_id,
                'receivableAccountLabel': str(row.receivableAccountID) if row.receivableAccountID_id else '',
                'displayOrder': row.displayOrder or 0,
                'isActive': bool(row.isActive),
                'isSystemGenerated': row.code in SYSTEM_FEE_HEAD_CODES,
                'updatedOn': row.lastUpdatedOn.strftime('%d-%m-%Y %I:%M %p') if row.lastUpdatedOn else 'N/A',
            })
        logger.info(f'Fee head list loaded count={len(data)} school={school_id} session={session_id} user={request.user.id}')
        return SuccessResponse('Fee heads loaded successfully.', data=data).to_json_response()
    except Exception as exc:
        logger.exception(f'Unable to load fee head list school={school_id} session={session_id} user={request.user.id}: {exc}')
        return ErrorResponse('Unable to load fee heads.', status_code=500).to_json_response()


class FinanceFeeHeadListJson(BaseDatatableView):
    order_columns = ['code', 'name', 'category', 'defaultAmount', 'recurrenceType', 'incomeAccountID__accountName',
                     'receivableAccountID__accountName', 'isActive', 'lastUpdatedOn', 'id']

    def get_initial_queryset(self):
        school_id = _current_school_id(self.request)
        session_id = _current_session_id(self.request)
        if not school_id or not session_id:
            logger.warning(f'Fee head datatable requested without school/session user={self.request.user.id}')
            return FeeHead.objects.none()
        bootstrap_school_finance(school_id=school_id, session_id=session_id, user_obj=self.request.user)
        return FeeHead.objects.select_related('incomeAccountID', 'receivableAccountID').filter(
            schoolID_id=school_id,
            sessionID_id=session_id,
            isDeleted=False,
        ).order_by('displayOrder', 'name', 'id')

    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(code__icontains=search)
                | Q(name__icontains=search)
                | Q(category__icontains=search)
                | Q(incomeAccountID__accountName__icontains=search)
                | Q(receivableAccountID__accountName__icontains=search)
                | Q(lastEditedBy__icontains=search)
            )
        return qs

    def prepare_results(self, qs):
        json_data = []
        for item in qs:
            delete_handler = None
            if item.code not in SYSTEM_FEE_HEAD_CODES:
                delete_handler = f'deleteFeeHead({item.id})'
            json_data.append([
                f'<strong>{escape(item.code or "")}</strong>',
                escape(item.name or ''),
                escape(item.category or ''),
                escape(f'Rs {float(item.defaultAmount or 0):.2f}'),
                _finance_status_pill(item.recurrenceType) if item.isRecurring else '-',
                escape(str(item.incomeAccountID) if item.incomeAccountID_id else ''),
                escape(str(item.receivableAccountID) if item.receivableAccountID_id else ''),
                _finance_active_pill(item.isActive),
                escape(item.lastUpdatedOn.strftime('%d-%m-%Y %I:%M %p') if item.lastUpdatedOn else 'N/A'),
                _management_edit_delete_buttons(
                    edit_handler=f'editFeeHead({item.id})',
                    delete_handler=delete_handler,
                ),
            ])
        logger.info(f'Fee head datatable prepared rows={len(json_data)} user={self.request.user.id}')
        return json_data


@transaction.atomic
@csrf_exempt
@login_required
@check_groups('Admin', 'Owner')
def upsert_fee_head_api(request):
    if request.method != 'POST':
        logger.warning(f'Invalid fee head upsert method={request.method} user={request.user.id}')
        return ErrorResponse('Method not allowed.', status_code=405).to_json_response()

    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    if not school_id or not session_id:
        logger.warning(f'Fee head upsert missing school/session user={request.user.id}')
        return ErrorResponse('School session was not found.').to_json_response()

    try:
        bootstrap_school_finance(school_id=school_id, session_id=session_id, user_obj=request.user)

        fee_head_id = request.POST.get('id')
        code = (request.POST.get('code') or '').strip().upper().replace(' ', '_')
        name = (request.POST.get('name') or '').strip()
        category = (request.POST.get('category') or 'misc').strip()
        recurrence_type = (request.POST.get('recurrenceType') or 'one_time').strip()
        income_account_id = request.POST.get('incomeAccountID')
        receivable_account_id = request.POST.get('receivableAccountID')
        default_amount = _decimal_or_zero(request.POST.get('defaultAmount'))
        display_order = _safe_int(request.POST.get('displayOrder'), 0)
        is_recurring = _truthy(request.POST.get('isRecurring'))
        is_active = _truthy(request.POST.get('isActive') or 'true')

        if not code or not name:
            logger.warning(f'Fee head validation failed missing code/name school={school_id} session={session_id} user={request.user.id}')
            return ErrorResponse('Code and name are required.').to_json_response()

        income_account = FinanceAccount.objects.filter(
            pk=income_account_id,
            schoolID_id=school_id,
            sessionID_id=session_id,
            isDeleted=False,
        ).first()
        receivable_account = FinanceAccount.objects.filter(
            pk=receivable_account_id,
            schoolID_id=school_id,
            sessionID_id=session_id,
            isDeleted=False,
        ).first()
        if not income_account or not receivable_account:
            logger.warning(f'Fee head validation failed missing accounts code={code} school={school_id} session={session_id} user={request.user.id}')
            return ErrorResponse('Income and receivable accounts are required.').to_json_response()

        instance = None
        if fee_head_id:
            instance = FeeHead.objects.filter(
                pk=fee_head_id,
                schoolID_id=school_id,
                sessionID_id=session_id,
                isDeleted=False,
            ).first()
            if not instance:
                logger.warning(f'Fee head update target not found id={fee_head_id} school={school_id} session={session_id} user={request.user.id}')
                return ErrorResponse('Fee head not found.').to_json_response()
            if instance.code in SYSTEM_FEE_HEAD_CODES and code != instance.code:
                logger.warning(f'System fee head code change blocked id={instance.id} old={instance.code} new={code} user={request.user.id}')
                return ErrorResponse('System fee head code cannot be changed.').to_json_response()

        duplicate_qs = FeeHead.objects.filter(
            schoolID_id=school_id,
            sessionID_id=session_id,
            code__iexact=code,
            isDeleted=False,
        )
        if instance:
            duplicate_qs = duplicate_qs.exclude(pk=instance.pk)
        if duplicate_qs.exists():
            logger.info(f'Fee head duplicate blocked code={code} school={school_id} session={session_id} user={request.user.id}')
            return ErrorResponse('Fee head code already exists.').to_json_response()

        created = instance is None
        if not instance:
            instance = FeeHead(
                schoolID_id=school_id,
                sessionID_id=session_id,
            )

        instance.code = code
        instance.name = name
        instance.category = category
        instance.defaultAmount = default_amount
        instance.isRecurring = is_recurring
        instance.recurrenceType = recurrence_type
        instance.incomeAccountID = income_account
        instance.receivableAccountID = receivable_account
        instance.displayOrder = display_order
        instance.isActive = is_active
        instance.isDeleted = False
        instance.lastEditedBy = _user_label(request.user)
        instance.updatedByUserID = request.user
        instance.full_clean()
        pre_save_with_user.send(sender=FeeHead, instance=instance, user=request.user.pk)
        instance.save()

        action = 'created' if created else 'updated'
        logger.info(f'Fee head {action} id={instance.id} code={instance.code} school={school_id} session={session_id} user={request.user.id}')
        return SuccessResponse('Fee head saved successfully.', extra={'color': 'green'}).to_json_response()
    except ValidationError as exc:
        logger.warning(f'Fee head validation error school={school_id} session={session_id} user={request.user.id}: {_serialize_validation_error(exc)}')
        return ErrorResponse(_serialize_validation_error(exc) or 'Unable to save fee head.').to_json_response()
    except Exception as exc:
        logger.exception(f'Unable to save fee head school={school_id} session={session_id} user={request.user.id}: {exc}')
        return ErrorResponse('Unable to save fee head.', status_code=500).to_json_response()


@transaction.atomic
@csrf_exempt
@login_required
@check_groups('Admin', 'Owner')
def delete_fee_head_api(request):
    if request.method != 'POST':
        logger.warning(f'Invalid fee head delete method={request.method} user={request.user.id}')
        return ErrorResponse('Method not allowed.', status_code=405).to_json_response()

    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    fee_head_id = request.POST.get('id')
    if not school_id or not session_id:
        logger.warning(f'Fee head delete missing school/session id={fee_head_id} user={request.user.id}')
        return ErrorResponse('School session was not found.').to_json_response()

    try:
        instance = FeeHead.objects.filter(
            pk=fee_head_id,
            schoolID_id=school_id,
            sessionID_id=session_id,
            isDeleted=False,
        ).first()
        if not instance:
            logger.warning(f'Fee head delete target not found id={fee_head_id} school={school_id} session={session_id} user={request.user.id}')
            return ErrorResponse('Fee head not found.').to_json_response()
        if instance.code in SYSTEM_FEE_HEAD_CODES:
            logger.warning(f'System fee head delete blocked id={instance.id} code={instance.code} user={request.user.id}')
            return ErrorResponse('System fee heads cannot be deleted.').to_json_response()
        if StudentCharge.objects.filter(
            schoolID_id=school_id,
            sessionID_id=session_id,
            feeHeadID_id=instance.id,
            isDeleted=False,
        ).exists():
            logger.info(f'Fee head delete blocked due to charges id={instance.id} code={instance.code} school={school_id} session={session_id} user={request.user.id}')
            return ErrorResponse('Fee head is already used in student charges and cannot be deleted.').to_json_response()

        instance.isDeleted = True
        instance.lastEditedBy = _user_label(request.user)
        instance.updatedByUserID = request.user
        pre_save_with_user.send(sender=FeeHead, instance=instance, user=request.user.pk)
        instance.save(update_fields=['isDeleted', 'lastEditedBy', 'updatedByUserID', 'lastUpdatedOn'])
        logger.info(f'Fee head deleted id={instance.id} code={instance.code} school={school_id} session={session_id} user={request.user.id}')
        return SuccessResponse('Fee head deleted successfully.', extra={'color': 'green'}).to_json_response()
    except Exception as exc:
        logger.exception(f'Unable to delete fee head id={fee_head_id} school={school_id} session={session_id} user={request.user.id}: {exc}')
        return ErrorResponse('Unable to delete fee head.', status_code=500).to_json_response()
