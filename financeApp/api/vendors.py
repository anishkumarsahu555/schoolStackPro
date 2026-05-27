from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils.html import escape
from django.views.decorators.csrf import csrf_exempt
from django_datatables_view.base_datatable_view import BaseDatatableView

from financeApp.models import ExpenseVoucher, FinanceParty
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


def _user_label(user):
    full_name = f'{user.first_name} {user.last_name}'.strip()
    return full_name or user.username


def _truthy(value):
    return str(value).strip().lower() in {'1', 'true', 'yes', 'on'}


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
def get_vendor_list_api(request):
    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    if not school_id or not session_id:
        logger.warning(f'Vendor list requested without school/session user={request.user.id}')
        return SuccessResponse('Vendors loaded successfully.', data=[]).to_json_response()

    try:
        rows = FinanceParty.objects.filter(
            schoolID_id=school_id,
            sessionID_id=session_id,
            partyType='vendor',
            isDeleted=False,
        ).order_by('displayName', 'id')
        data = []
        for row in rows:
            data.append({
                'id': row.id,
                'displayName': row.displayName or '',
                'phoneNumber': row.phoneNumber or '',
                'email': row.email or '',
                'address': row.address or '',
                'taxIdentifier': row.taxIdentifier or '',
                'isActive': bool(row.isActive),
                'updatedOn': row.lastUpdatedOn.strftime('%d-%m-%Y %I:%M %p') if row.lastUpdatedOn else 'N/A',
            })
        logger.info(f'Vendor list loaded count={len(data)} school={school_id} session={session_id} user={request.user.id}')
        return SuccessResponse('Vendors loaded successfully.', data=data).to_json_response()
    except Exception as exc:
        logger.exception(f'Unable to load vendors school={school_id} session={session_id} user={request.user.id}: {exc}')
        return ErrorResponse('Unable to load vendors.', status_code=500).to_json_response()


@login_required
@check_groups('Admin', 'Owner')
def search_vendor_suggestions_api(request):
    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    if not school_id or not session_id:
        logger.warning(f'Vendor suggestions requested without school/session user={request.user.id}')
        return SuccessResponse('Vendor suggestions loaded successfully.', data=[]).to_json_response()

    query = (request.GET.get('q') or '').strip()
    limit = _safe_int(request.GET.get('limit'), 8)
    if limit <= 0:
        limit = 8
    limit = min(limit, 20)

    try:
        vendor_qs = FinanceParty.objects.filter(
            schoolID_id=school_id,
            sessionID_id=session_id,
            partyType='vendor',
            isDeleted=False,
        )
        if query:
            vendor_qs = vendor_qs.filter(
                Q(displayName__icontains=query)
                | Q(phoneNumber__icontains=query)
                | Q(email__icontains=query)
                | Q(taxIdentifier__icontains=query)
            )

        rows = vendor_qs.order_by('displayName', 'id')[:limit]
        data = [
            {
                'id': row.id,
                'displayName': row.displayName or '',
                'phoneNumber': row.phoneNumber or '',
                'email': row.email or '',
            }
            for row in rows
        ]
        logger.info(f'Vendor suggestions loaded count={len(data)} query="{query}" school={school_id} session={session_id} user={request.user.id}')
        return SuccessResponse('Vendor suggestions loaded successfully.', data=data).to_json_response()
    except Exception as exc:
        logger.exception(f'Unable to load vendor suggestions school={school_id} session={session_id} user={request.user.id}: {exc}')
        return ErrorResponse('Unable to load vendor suggestions.', status_code=500).to_json_response()


class FinanceVendorListJson(BaseDatatableView):
    order_columns = ['displayName', 'phoneNumber', 'email', 'taxIdentifier', 'isActive', 'lastUpdatedOn', 'id']

    def get_initial_queryset(self):
        school_id = _current_school_id(self.request)
        session_id = _current_session_id(self.request)
        if not school_id or not session_id:
            logger.warning(f'Vendor datatable requested without school/session user={self.request.user.id}')
            return FinanceParty.objects.none()
        return FinanceParty.objects.filter(
            schoolID_id=school_id,
            sessionID_id=session_id,
            partyType='vendor',
            isDeleted=False,
        ).order_by('displayName', 'id')

    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(displayName__icontains=search)
                | Q(phoneNumber__icontains=search)
                | Q(email__icontains=search)
                | Q(address__icontains=search)
                | Q(taxIdentifier__icontains=search)
                | Q(lastEditedBy__icontains=search)
            )
        return qs

    def prepare_results(self, qs):
        json_data = []
        for item in qs:
            action = (
                f'<a href="/management/finance/vendor-statement/?vendor={item.id}" '
                f'data-inverted="" data-tooltip="Open Statement" data-position="left center" data-variation="mini" '
                f'style="font-size:10px;" class="ui circular blue icon button"><i class="book open icon"></i></a>'
                f'<span style="margin-left:3px;">'
                f'{_management_edit_delete_buttons(edit_handler=f"editVendor({item.id})", delete_handler=f"deleteVendor({item.id})")}'
                f'</span>'
            )
            json_data.append([
                f'<strong>{escape(item.displayName or "")}</strong>',
                escape(item.phoneNumber or '-'),
                escape(item.email or '-'),
                escape(item.taxIdentifier or '-'),
                _finance_active_pill(item.isActive),
                escape(item.lastUpdatedOn.strftime('%d-%m-%Y %I:%M %p') if item.lastUpdatedOn else 'N/A'),
                action,
            ])
        logger.info(f'Vendor datatable prepared rows={len(json_data)} user={self.request.user.id}')
        return json_data


@transaction.atomic
@csrf_exempt
@login_required
@check_groups('Admin', 'Owner')
def upsert_vendor_api(request):
    if request.method != 'POST':
        logger.warning(f'Invalid vendor upsert method={request.method} user={request.user.id}')
        return ErrorResponse('Method not allowed.', status_code=405).to_json_response()

    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    if not school_id or not session_id:
        logger.warning(f'Vendor upsert missing school/session user={request.user.id}')
        return ErrorResponse('School session was not found.').to_json_response()

    vendor_id = request.POST.get('id')
    display_name = (request.POST.get('displayName') or '').strip()
    phone_number = (request.POST.get('phoneNumber') or '').strip()
    email = (request.POST.get('email') or '').strip()
    address = (request.POST.get('address') or '').strip()
    tax_identifier = (request.POST.get('taxIdentifier') or '').strip()
    is_active = _truthy(request.POST.get('isActive') or 'true')

    if not display_name:
        logger.warning(f'Vendor validation failed missing name school={school_id} session={session_id} user={request.user.id}')
        return ErrorResponse('Vendor name is required.').to_json_response()

    try:
        instance = None
        if vendor_id:
            instance = FinanceParty.objects.filter(
                pk=vendor_id,
                schoolID_id=school_id,
                sessionID_id=session_id,
                partyType='vendor',
                isDeleted=False,
            ).first()
            if not instance:
                logger.warning(f'Vendor update target not found id={vendor_id} school={school_id} session={session_id} user={request.user.id}')
                return ErrorResponse('Vendor not found.').to_json_response()

        duplicate_qs = FinanceParty.objects.filter(
            schoolID_id=school_id,
            sessionID_id=session_id,
            partyType='vendor',
            displayName__iexact=display_name,
            isDeleted=False,
        )
        if instance:
            duplicate_qs = duplicate_qs.exclude(pk=instance.pk)
        if duplicate_qs.exists():
            logger.info(f'Vendor duplicate blocked name="{display_name}" school={school_id} session={session_id} user={request.user.id}')
            return ErrorResponse('Vendor name already exists.').to_json_response()

        created = instance is None
        if not instance:
            instance = FinanceParty(
                schoolID_id=school_id,
                sessionID_id=session_id,
                partyType='vendor',
            )

        instance.displayName = display_name
        instance.phoneNumber = phone_number or None
        instance.email = email or None
        instance.address = address
        instance.taxIdentifier = tax_identifier or None
        instance.isActive = is_active
        instance.lastEditedBy = _user_label(request.user)
        instance.updatedByUserID = request.user
        instance.isDeleted = False
        instance.full_clean()
        pre_save_with_user.send(sender=FinanceParty, instance=instance, user=request.user.pk)
        instance.save()

        action = 'created' if created else 'updated'
        logger.info(f'Vendor {action} id={instance.id} name="{instance.displayName}" school={school_id} session={session_id} user={request.user.id}')
        return SuccessResponse('Vendor saved successfully.', extra={'color': 'green'}).to_json_response()
    except ValidationError as exc:
        logger.warning(f'Vendor validation error school={school_id} session={session_id} user={request.user.id}: {_serialize_validation_error(exc)}')
        return ErrorResponse(_serialize_validation_error(exc) or 'Unable to save vendor.').to_json_response()
    except Exception as exc:
        logger.exception(f'Unable to save vendor id={vendor_id} school={school_id} session={session_id} user={request.user.id}: {exc}')
        return ErrorResponse('Unable to save vendor.', status_code=500).to_json_response()


@transaction.atomic
@csrf_exempt
@login_required
@check_groups('Admin', 'Owner')
def delete_vendor_api(request):
    if request.method != 'POST':
        logger.warning(f'Invalid vendor delete method={request.method} user={request.user.id}')
        return ErrorResponse('Method not allowed.', status_code=405).to_json_response()

    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    vendor_id = request.POST.get('id')
    if not school_id or not session_id:
        logger.warning(f'Vendor delete missing school/session id={vendor_id} user={request.user.id}')
        return ErrorResponse('School session was not found.').to_json_response()

    try:
        instance = FinanceParty.objects.filter(
            pk=vendor_id,
            schoolID_id=school_id,
            sessionID_id=session_id,
            partyType='vendor',
            isDeleted=False,
        ).first()
        if not instance:
            logger.warning(f'Vendor delete target not found id={vendor_id} school={school_id} session={session_id} user={request.user.id}')
            return ErrorResponse('Vendor not found.').to_json_response()
        if ExpenseVoucher.objects.filter(
            partyID=instance,
            schoolID_id=school_id,
            sessionID_id=session_id,
            isDeleted=False,
        ).exists():
            logger.info(f'Vendor delete blocked due to vouchers id={instance.id} name="{instance.displayName}" school={school_id} session={session_id} user={request.user.id}')
            return ErrorResponse('Vendor is already used in expense vouchers and cannot be deleted.').to_json_response()

        instance.isDeleted = True
        instance.lastEditedBy = _user_label(request.user)
        instance.updatedByUserID = request.user
        pre_save_with_user.send(sender=FinanceParty, instance=instance, user=request.user.pk)
        instance.save(update_fields=['isDeleted', 'lastEditedBy', 'updatedByUserID', 'lastUpdatedOn'])
        logger.info(f'Vendor deleted id={instance.id} name="{instance.displayName}" school={school_id} session={session_id} user={request.user.id}')
        return SuccessResponse('Vendor deleted successfully.', extra={'color': 'green'}).to_json_response()
    except Exception as exc:
        logger.exception(f'Unable to delete vendor id={vendor_id} school={school_id} session={session_id} user={request.user.id}: {exc}')
        return ErrorResponse('Unable to delete vendor.', status_code=500).to_json_response()
