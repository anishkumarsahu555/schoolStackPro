from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils.html import escape
from django.views.decorators.csrf import csrf_exempt
from django_datatables_view.base_datatable_view import BaseDatatableView

from financeApp.models import ExpenseCategory, ExpenseVoucher, FinanceAccount
from financeApp.services import bootstrap_expense_categories
from homeApp.models import SchoolSession
from managementApp.signals import pre_save_with_user
from utils.custom_decorators import check_groups
from utils.custom_response import ErrorResponse, SuccessResponse
from utils.get_school_detail import get_school_id
from utils.logger import logger


SYSTEM_EXPENSE_CATEGORY_CODES = {'OFFICE', 'UTILITY'}


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
def get_expense_category_list_api(request):
    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    if not school_id or not session_id:
        logger.warning(f'Expense category list requested without school/session user={request.user.id}')
        return SuccessResponse('Expense categories loaded.', data=[]).to_json_response()

    try:
        bootstrap_expense_categories(school_id=school_id, session_id=session_id, user_obj=request.user)
        rows = ExpenseCategory.objects.select_related('expenseAccountID', 'payableAccountID').filter(
            schoolID_id=school_id,
            sessionID_id=session_id,
            isDeleted=False,
        ).order_by('name', 'id')
        data = []
        for row in rows:
            data.append({
                'id': row.id,
                'code': row.code or '',
                'name': row.name or '',
                'expenseAccountID': row.expenseAccountID_id,
                'expenseAccountLabel': str(row.expenseAccountID) if row.expenseAccountID_id else '',
                'payableAccountID': row.payableAccountID_id,
                'payableAccountLabel': str(row.payableAccountID) if row.payableAccountID_id else '',
                'isActive': bool(row.isActive),
                'isSystemGenerated': row.code in SYSTEM_EXPENSE_CATEGORY_CODES,
                'updatedOn': row.lastUpdatedOn.strftime('%d-%m-%Y %I:%M %p') if row.lastUpdatedOn else 'N/A',
            })
        logger.info(f'Expense category list loaded count={len(data)} school={school_id} session={session_id} user={request.user.id}')
        return SuccessResponse('Expense categories loaded.', data=data).to_json_response()
    except Exception as exc:
        logger.exception(f'Unable to load expense categories school={school_id} session={session_id} user={request.user.id}: {exc}')
        return ErrorResponse('Unable to load expense categories.', status_code=500).to_json_response()


class FinanceExpenseCategoryListJson(BaseDatatableView):
    order_columns = ['code', 'name', 'expenseAccountID__accountName', 'payableAccountID__accountName', 'isActive',
                     'lastUpdatedOn', 'id']

    def get_initial_queryset(self):
        school_id = _current_school_id(self.request)
        session_id = _current_session_id(self.request)
        if not school_id or not session_id:
            logger.warning(f'Expense category datatable requested without school/session user={self.request.user.id}')
            return ExpenseCategory.objects.none()
        bootstrap_expense_categories(school_id=school_id, session_id=session_id, user_obj=self.request.user)
        return ExpenseCategory.objects.select_related('expenseAccountID', 'payableAccountID').filter(
            schoolID_id=school_id,
            sessionID_id=session_id,
            isDeleted=False,
        ).order_by('name', 'id')

    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(code__icontains=search)
                | Q(name__icontains=search)
                | Q(expenseAccountID__accountName__icontains=search)
                | Q(payableAccountID__accountName__icontains=search)
                | Q(lastEditedBy__icontains=search)
            )
        return qs

    def prepare_results(self, qs):
        json_data = []
        for item in qs:
            delete_handler = None
            if item.code not in SYSTEM_EXPENSE_CATEGORY_CODES:
                delete_handler = f'deleteExpenseCategory({item.id})'
            json_data.append([
                f'<strong>{escape(item.code or "")}</strong>',
                escape(item.name or ''),
                escape(str(item.expenseAccountID) if item.expenseAccountID_id else ''),
                escape(str(item.payableAccountID) if item.payableAccountID_id else '-'),
                _finance_active_pill(item.isActive),
                escape(item.lastUpdatedOn.strftime('%d-%m-%Y %I:%M %p') if item.lastUpdatedOn else 'N/A'),
                _management_edit_delete_buttons(
                    edit_handler=f'editExpenseCategory({item.id})',
                    delete_handler=delete_handler,
                ),
            ])
        logger.info(f'Expense category datatable prepared rows={len(json_data)} user={self.request.user.id}')
        return json_data


@transaction.atomic
@csrf_exempt
@login_required
@check_groups('Admin', 'Owner')
def upsert_expense_category_api(request):
    if request.method != 'POST':
        logger.warning(f'Invalid expense category upsert method={request.method} user={request.user.id}')
        return ErrorResponse('Method not allowed.', status_code=405).to_json_response()

    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    if not school_id or not session_id:
        logger.warning(f'Expense category upsert missing school/session user={request.user.id}')
        return ErrorResponse('School session was not found.').to_json_response()

    try:
        bootstrap_expense_categories(school_id=school_id, session_id=session_id, user_obj=request.user)

        category_id = request.POST.get('id')
        code = (request.POST.get('code') or '').strip().upper().replace(' ', '_')
        name = (request.POST.get('name') or '').strip()
        expense_account_id = request.POST.get('expenseAccountID')
        payable_account_id = request.POST.get('payableAccountID')
        is_active = _truthy(request.POST.get('isActive') or 'true')

        if not code or not name:
            logger.warning(f'Expense category validation failed missing code/name school={school_id} session={session_id} user={request.user.id}')
            return ErrorResponse('Code and name are required.').to_json_response()

        expense_account = FinanceAccount.objects.filter(
            pk=expense_account_id,
            schoolID_id=school_id,
            sessionID_id=session_id,
            isDeleted=False,
        ).first()
        payable_account = FinanceAccount.objects.filter(
            pk=payable_account_id,
            schoolID_id=school_id,
            sessionID_id=session_id,
            isDeleted=False,
        ).first() if payable_account_id else None
        if not expense_account:
            logger.warning(f'Expense category validation failed missing expense account code={code} school={school_id} session={session_id} user={request.user.id}')
            return ErrorResponse('Expense account is required.').to_json_response()

        instance = None
        if category_id:
            instance = ExpenseCategory.objects.filter(
                pk=category_id,
                schoolID_id=school_id,
                sessionID_id=session_id,
                isDeleted=False,
            ).first()
            if not instance:
                logger.warning(f'Expense category update target not found id={category_id} school={school_id} session={session_id} user={request.user.id}')
                return ErrorResponse('Expense category not found.').to_json_response()
            if instance.code in SYSTEM_EXPENSE_CATEGORY_CODES and code != instance.code:
                logger.warning(f'System expense category code change blocked id={instance.id} old={instance.code} new={code} user={request.user.id}')
                return ErrorResponse('System category code cannot be changed.').to_json_response()

        duplicate_qs = ExpenseCategory.objects.filter(
            schoolID_id=school_id,
            sessionID_id=session_id,
            code__iexact=code,
            isDeleted=False,
        )
        if instance:
            duplicate_qs = duplicate_qs.exclude(pk=instance.pk)
        if duplicate_qs.exists():
            logger.info(f'Expense category duplicate blocked code={code} school={school_id} session={session_id} user={request.user.id}')
            return ErrorResponse('Expense category code already exists.').to_json_response()

        created = instance is None
        if not instance:
            instance = ExpenseCategory(schoolID_id=school_id, sessionID_id=session_id)

        instance.code = code
        instance.name = name
        instance.expenseAccountID = expense_account
        instance.payableAccountID = payable_account
        instance.isActive = is_active
        instance.isDeleted = False
        instance.lastEditedBy = _user_label(request.user)
        instance.updatedByUserID = request.user
        instance.full_clean()
        pre_save_with_user.send(sender=ExpenseCategory, instance=instance, user=request.user.pk)
        instance.save()

        action = 'created' if created else 'updated'
        logger.info(f'Expense category {action} id={instance.id} code={instance.code} school={school_id} session={session_id} user={request.user.id}')
        return SuccessResponse('Expense category saved successfully.', extra={'color': 'green'}).to_json_response()
    except ValidationError as exc:
        logger.warning(f'Expense category validation error school={school_id} session={session_id} user={request.user.id}: {_serialize_validation_error(exc)}')
        return ErrorResponse(_serialize_validation_error(exc) or 'Unable to save expense category.').to_json_response()
    except Exception as exc:
        logger.exception(f'Unable to save expense category school={school_id} session={session_id} user={request.user.id}: {exc}')
        return ErrorResponse('Unable to save expense category.', status_code=500).to_json_response()


@transaction.atomic
@csrf_exempt
@login_required
@check_groups('Admin', 'Owner')
def delete_expense_category_api(request):
    if request.method != 'POST':
        logger.warning(f'Invalid expense category delete method={request.method} user={request.user.id}')
        return ErrorResponse('Method not allowed.', status_code=405).to_json_response()

    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    category_id = request.POST.get('id')
    if not school_id or not session_id:
        logger.warning(f'Expense category delete missing school/session id={category_id} user={request.user.id}')
        return ErrorResponse('School session was not found.').to_json_response()

    try:
        instance = ExpenseCategory.objects.filter(
            pk=category_id,
            schoolID_id=school_id,
            sessionID_id=session_id,
            isDeleted=False,
        ).first()
        if not instance:
            logger.warning(f'Expense category delete target not found id={category_id} school={school_id} session={session_id} user={request.user.id}')
            return ErrorResponse('Expense category not found.').to_json_response()
        if instance.code in SYSTEM_EXPENSE_CATEGORY_CODES:
            logger.warning(f'System expense category delete blocked id={instance.id} code={instance.code} user={request.user.id}')
            return ErrorResponse('System categories cannot be deleted.').to_json_response()
        if ExpenseVoucher.objects.filter(
            expenseCategoryID_id=instance.id,
            schoolID_id=school_id,
            sessionID_id=session_id,
            isDeleted=False,
        ).exists():
            logger.info(f'Expense category delete blocked due to vouchers id={instance.id} code={instance.code} school={school_id} session={session_id} user={request.user.id}')
            return ErrorResponse('Expense category is already used in vouchers and cannot be deleted.').to_json_response()

        instance.isDeleted = True
        instance.lastEditedBy = _user_label(request.user)
        instance.updatedByUserID = request.user
        pre_save_with_user.send(sender=ExpenseCategory, instance=instance, user=request.user.pk)
        instance.save(update_fields=['isDeleted', 'lastEditedBy', 'updatedByUserID', 'lastUpdatedOn'])
        logger.info(f'Expense category deleted id={instance.id} code={instance.code} school={school_id} session={session_id} user={request.user.id}')
        return SuccessResponse('Expense category deleted successfully.', extra={'color': 'green'}).to_json_response()
    except Exception as exc:
        logger.exception(f'Unable to delete expense category id={category_id} school={school_id} session={session_id} user={request.user.id}: {exc}')
        return ErrorResponse('Unable to delete expense category.', status_code=500).to_json_response()
