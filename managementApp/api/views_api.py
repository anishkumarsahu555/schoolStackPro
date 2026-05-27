from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
import json

from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Group
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import transaction, IntegrityError, connection
from django.db.models import Q, Prefetch, Sum, DecimalField, Value
from django.db.models.functions import Coalesce
from django.http import JsonResponse as DjangoJsonResponse
from django.utils.crypto import get_random_string
from django.utils.html import escape
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django_datatables_view.base_datatable_view import BaseDatatableView

from financeApp.models import (
    ExpenseCategory,
    ExpenseVoucher,
    FeeHead,
    FinanceAccount,
    FinanceApprovalRule,
    FinanceConfiguration,
    FinanceEntry,
    FinanceParty,
    FinancePaymentMode,
    FinancePeriod,
    PayrollLine,
    PayrollRun,
    PaymentRefund,
    PaymentRefundAllocation,
    FinanceTransaction,
    PaymentReceipt,
    StudentCharge,
)
from homeApp.models import AuditLog, SchoolDetail, SchoolSession
from homeApp.owner_access import school_owner_user_q
from homeApp.session_utils import get_session_month_sequence
from homeApp.push_service import send_event_push_notifications
from financeApp.services import (
    approve_payment_receipt,
    approve_payment_refund,
    approve_payroll_payment,
    bootstrap_expense_categories,
    bootstrap_school_finance,
    clear_payment_receipt,
    create_manual_payment_receipt,
    create_payment_refund,
    ensure_named_party,
    generate_payroll_run,
    generate_finance_document_number,
    get_finance_configuration,
    pay_payroll_line,
    preview_finance_document_number,
    post_payroll_run,
    rebuild_payment_receipt_ledger,
    refresh_student_charge_balance,
    reverse_payment_receipt,
    sync_expense_voucher_posting,
    sync_payment_receipt,
    sync_student_charge,
)
from managementApp.models import *
from managementApp.reporting import build_report_cards_for_student, upsert_progress_report_snapshot
from managementApp.services.id_cards import (
    DEFAULT_FIELDS_CONFIG,
    DEFAULT_FOOTER_CONFIG,
    DEFAULT_HEADER_CONFIG,
    DEFAULT_STYLE_CONFIG,
    get_or_create_active_id_card_design,
    merged_config,
    normalize_fields_config,
)
from managementApp.services.session_rollover import preview_session_import, run_session_import
from managementApp.signals import pre_save_with_user
from managementApp.leave_utils import (
    ATTENDANCE_STATUS_ABSENT,
    ATTENDANCE_STATUS_HOLIDAY,
    ATTENDANCE_STATUS_LEAVE,
    apply_attendance_status,
    approved_leave_for_date,
    approved_leave_map_for_date,
    attendance_status_from_values,
    leave_application_note,
    leave_duration_label,
    pending_leave_map_for_date,
)
from managementApp.holiday_utils import (
    holiday_audiences,
    holiday_for_date,
    holiday_note,
    resync_holidays_for_scope,
)
from teacherApp.models import SubjectNote, SubjectNoteVersion
from utils.conts import MONTHS_LIST
from utils.get_school_detail import get_school_id

from utils.json_validator import validate_input
from utils.logger import logger
from utils.custom_response import SuccessResponse, ErrorResponse
from utils.cache_modfier import add_item_to_existing_cache, delete_item_from_existing_cache, update_item_in_existing_cache
from utils.custom_decorators import check_groups
from utils.image_utils import safe_image_url, avatar_image_html, optimize_uploaded_image


def _api_response(payload, safe=False, status=200):
    if isinstance(payload, dict):
        response_type = payload.get("status")
        message = payload.get("message")
        data = payload.get("data")
        extra = {k: v for k, v in payload.items() if k not in {"status", "message", "data"}}

        if response_type == "success":
            return SuccessResponse(
                message or "Request processed successfully.",
                status_code=status,
                data=data,
                extra=extra,
            ).to_json_response()
        if response_type == "error":
            return ErrorResponse(
                message or "Request failed.",
                status_code=status,
                data=data,
                extra=extra,
            ).to_json_response()

    return DjangoJsonResponse(payload, safe=safe, status=status)


def _current_session_id(request):
    return request.session.get("current_session", {}).get("Id")


def _current_school_id(request):
    current_session = request.session.get("current_session", {})
    school_id = current_session.get("SchoolID")
    if school_id:
        return school_id
    session_id = current_session.get("Id")
    if session_id:
        school_id = SchoolSession.objects.filter(pk=session_id, isDeleted=False).values_list('schoolID_id', flat=True).first()
        if school_id:
            current_session['SchoolID'] = school_id
            request.session['current_session'] = current_session
            return school_id
    return get_school_id(request)


def _truthy(value):
    return str(value).strip().lower() in {'1', 'true', 'yes', 'on'}


def _decimal_or_zero(value):
    try:
        return Decimal(str(value or '0')).quantize(Decimal('0.01'))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal('0.00')


def _finance_status_pill(status_value):
    status = (status_value or 'draft').strip().lower().replace(' ', '_')
    label = status.replace('_', ' ')
    return f'<span class="finance-status-pill {escape(status)}">{escape(label)}</span>'


def _finance_active_pill(is_active):
    return _finance_status_pill('active' if is_active else 'inactive')


def _safe_sort_date(value):
    return value or date.min


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


def _parse_filter_date(value):
    raw = (value or '').strip()
    if not raw:
        return None
    for fmt in ('%d/%m/%Y', '%Y-%m-%d', '%d-%m-%Y'):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def _normal_balance(account_type, debit_total, credit_total):
    debit_total = _decimal_or_zero(debit_total)
    credit_total = _decimal_or_zero(credit_total)
    if account_type in {'asset', 'expense'}:
        return debit_total - credit_total
    return credit_total - debit_total


def _refund_tables_available():
    existing_tables = set(connection.introspection.table_names())
    return (
        PaymentRefund._meta.db_table in existing_tables
        and PaymentRefundAllocation._meta.db_table in existing_tables
    )


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


@login_required
def audit_log_list_api(request):
    try:
        school_id = _current_school_id(request)
        session_id = _current_session_id(request)
        logs = AuditLog.objects.select_related('content_type', 'userID', 'schoolID', 'sessionID').order_by('-datetime')
        if school_id:
            logs = logs.filter(Q(schoolID_id=school_id) | Q(schoolID__isnull=True))
        if session_id:
            logs = logs.filter(Q(sessionID_id=session_id) | Q(sessionID__isnull=True))

        action = (request.GET.get('action') or '').strip()
        model_label = (request.GET.get('model') or '').strip()
        search = (request.GET.get('search') or '').strip()
        if action:
            logs = logs.filter(action=action)
        if model_label:
            app_label, _, model_name = model_label.partition('.')
            logs = logs.filter(content_type__app_label=app_label, content_type__model=model_name.lower())
        if search:
            search_q = (
                Q(action__icontains=search)
                | Q(userID__username__icontains=search)
                | Q(path__icontains=search)
                | Q(content_type__app_label__icontains=search)
                | Q(content_type__model__icontains=search)
            )
            if search.isdigit():
                search_q |= Q(object_id=int(search))
            logs = logs.filter(search_q)

        rows = []
        for item in logs[:500]:
            model_name = f'{item.content_type.app_label}.{item.content_type.model}'
            user_label = item.userID.get_full_name() or item.userID.username if item.userID else 'System'
            changed_fields = ', '.join((item.changes or {}).keys()) or 'Snapshot'
            rows.append({
                'id': item.id,
                'datetime': item.datetime.strftime('%d-%m-%Y %I:%M %p') if item.datetime else '',
                'model': model_name,
                'objectID': item.object_id,
                'action': item.get_action_display(),
                'changedFields': changed_fields,
                'user': user_label,
                'path': item.path or 'N/A',
                'ipAddress': item.ipAddress or 'N/A',
                'actions': (
                    f'<button data-tooltip="View Changes" data-position="left center" data-variation="mini" '
                    f'onclick="viewAuditDetail({item.id})" class="ui circular blue icon button">'
                    f'<i class="eye icon"></i></button>'
                ),
            })

        model_options = [
            {'id': f'{ct.app_label}.{ct.model}', 'text': f'{ct.app_label}.{ct.model}'}
            for ct in ContentType.objects.filter(app_label__in=[
                'homeApp', 'managementApp', 'financeApp', 'certificateApp',
                'teacherApp', 'studentApp', 'chatApp', 'transportApp',
            ]).order_by('app_label', 'model')
        ]
        logger.info(f'Audit log list fetched count={len(rows)} school={school_id} session={session_id}')
        return SuccessResponse('Audit logs loaded.', data={'rows': rows, 'modelOptions': model_options}).to_json_response()
    except Exception as exc:
        logger.exception(f'Error fetching audit logs: {exc}')
        return ErrorResponse('Unable to load audit logs.', status_code=500).to_json_response()


class AuditLogListJson(BaseDatatableView):
    order_columns = ['datetime', 'content_type__app_label', 'object_id', 'action', 'changes', 'userID__username', 'path', 'ipAddress']

    def get_initial_queryset(self):
        school_id = _current_school_id(self.request)
        session_id = _current_session_id(self.request)
        logs = AuditLog.objects.select_related('content_type', 'userID', 'schoolID', 'sessionID').order_by('-datetime')
        if school_id:
            logs = logs.filter(Q(schoolID_id=school_id) | Q(schoolID__isnull=True))
        if session_id:
            logs = logs.filter(Q(sessionID_id=session_id) | Q(sessionID__isnull=True))
        action = (self.request.GET.get('action') or '').strip()
        model_label = (self.request.GET.get('model') or '').strip()
        if action:
            logs = logs.filter(action=action)
        if model_label:
            app_label, _, model_name = model_label.partition('.')
            logs = logs.filter(content_type__app_label=app_label, content_type__model=model_name.lower())
        return logs

    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]', None)
        extra_search = (self.request.GET.get('auditSearch') or '').strip()
        search = extra_search or search
        if search:
            search_q = (
                Q(action__icontains=search)
                | Q(userID__username__icontains=search)
                | Q(path__icontains=search)
                | Q(content_type__app_label__icontains=search)
                | Q(content_type__model__icontains=search)
            )
            if str(search).isdigit():
                search_q |= Q(object_id=int(search))
            qs = qs.filter(search_q)
        return qs

    def prepare_results(self, qs):
        rows = []
        for item in qs:
            model_name = f'{item.content_type.app_label}.{item.content_type.model}'
            user_label = item.userID.get_full_name() or item.userID.username if item.userID else 'System'
            changed_fields = ', '.join((item.changes or {}).keys()) or 'Snapshot'
            action = (
                f'<button data-inverted="" data-tooltip="View Changes" data-position="left center" '
                f'data-variation="mini" style="font-size:10px;" onclick="viewAuditDetail({item.id})" '
                f'class="ui circular facebook icon button blue"><i class="eye icon"></i></button>'
            )
            rows.append([
                escape(item.datetime.strftime('%d-%m-%Y %I:%M %p') if item.datetime else 'N/A'),
                escape(model_name),
                escape(item.object_id),
                escape(item.get_action_display()),
                escape(changed_fields),
                escape(user_label),
                escape(item.path or 'N/A'),
                escape(item.ipAddress or 'N/A'),
                action,
            ])
        return rows


def _audit_display_value(value):
    if value is None:
        return ''
    if isinstance(value, (dict, list)):
        return json.dumps(value, indent=2, ensure_ascii=False)
    return str(value)


def _audit_comparison_rows(audit_log):
    changes = audit_log.changes or {}
    snapshot = audit_log.snapshot or {}
    rows = []

    if audit_log.action in {'update', 'soft_delete', 'restore'}:
        for field_name, values in changes.items():
            if isinstance(values, dict) and ('old' in values or 'new' in values):
                old_value = values.get('old')
                new_value = values.get('new')
            else:
                old_value = snapshot.get(field_name)
                new_value = values
            rows.append({
                'field': field_name,
                'oldValue': _audit_display_value(old_value),
                'newValue': _audit_display_value(new_value),
                'changed': old_value != new_value,
            })
        return rows

    if audit_log.action == 'create':
        source = changes or snapshot
        for field_name, value in source.items():
            rows.append({
                'field': field_name,
                'oldValue': '',
                'newValue': _audit_display_value(value),
                'changed': True,
            })
        return rows

    if audit_log.action == 'delete':
        for field_name, value in snapshot.items():
            rows.append({
                'field': field_name,
                'oldValue': _audit_display_value(value),
                'newValue': '',
                'changed': True,
            })
        return rows

    for field_name, value in snapshot.items():
        rows.append({
            'field': field_name,
            'oldValue': _audit_display_value(value),
            'newValue': _audit_display_value(value),
            'changed': False,
        })
    return rows


@login_required
def audit_log_detail_api(request):
    try:
        school_id = _current_school_id(request)
        session_id = _current_session_id(request)
        audit_id = request.GET.get('id')
        log = AuditLog.objects.select_related('content_type', 'userID', 'schoolID', 'sessionID').filter(pk=audit_id).first()
        if not log:
            logger.error(f'Audit log detail not found id={audit_id}')
            return ErrorResponse('Audit log not found.', status_code=404).to_json_response()
        if school_id and log.schoolID_id and log.schoolID_id != school_id:
            logger.warning(f'Audit log access denied id={audit_id} user={request.user.id}')
            return ErrorResponse('Audit log not found.', status_code=404).to_json_response()
        if session_id and log.sessionID_id and log.sessionID_id != session_id:
            logger.warning(f'Audit log session access denied id={audit_id} user={request.user.id}')
            return ErrorResponse('Audit log not found.', status_code=404).to_json_response()

        model_name = f'{log.content_type.app_label}.{log.content_type.model}'
        user_label = log.userID.get_full_name() or log.userID.username if log.userID else 'System'
        data = {
            'id': log.id,
            'datetime': log.datetime.strftime('%d-%m-%Y %I:%M %p') if log.datetime else '',
            'model': model_name,
            'objectID': log.object_id,
            'action': log.get_action_display(),
            'rawAction': log.action,
            'user': user_label,
            'path': log.path or 'N/A',
            'ipAddress': log.ipAddress or 'N/A',
            'userAgent': log.userAgent or 'N/A',
            'comparisonRows': _audit_comparison_rows(log),
        }
        logger.info(f'Audit log detail fetched id={log.id} model={model_name} object={log.object_id}')
        return SuccessResponse('Audit log detail loaded.', data=data).to_json_response()
    except Exception as exc:
        logger.exception(f'Error fetching audit log detail: {exc}')
        return ErrorResponse('Unable to load audit log detail.', status_code=500).to_json_response()


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


def _parse_promotion_overrides(raw_text):
    overrides = {}
    for raw_line in (raw_text or '').splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if '->' not in line:
            raise ValueError('Custom promotion mapping format is invalid. Use one mapping per line like "Class 5 A -> Class 6 A".')
        source_label, target_label = [part.strip() for part in line.split('->', 1)]
        if not source_label or not target_label:
            raise ValueError('Each custom promotion mapping needs both source and target class names.')
        overrides[source_label] = target_label
    return overrides


def _parse_selection_overrides(raw_text):
    if not raw_text:
        return {}
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError:
        raise ValueError('Selected item payload is invalid.')
    if not isinstance(payload, dict):
        raise ValueError('Selected item payload is invalid.')

    normalized = {}
    for key, values in payload.items():
        if not isinstance(values, list):
            continue
        normalized[key] = []
        for value in values:
            try:
                normalized[key].append(int(value))
            except (TypeError, ValueError):
                continue
    return normalized


def _session_month_rows(session_id):
    session_obj = SchoolSession.objects.filter(pk=session_id, isDeleted=False).first() if session_id else None
    return get_session_month_sequence(session_obj)


def _restrict_fee_queryset_to_session_months(qs, session_id):
    session_month_rows = _session_month_rows(session_id)
    if not session_month_rows:
        return qs.none()

    ym_filter = Q()
    month_name_filter = Q()
    for month_name, year_value, month_no, _, _ in session_month_rows:
        ym_filter |= Q(feeYear=year_value, feeMonth=month_no)
        month_name_filter |= Q(month__iexact=month_name)

    legacy_filter = (Q(feeYear__isnull=True) | Q(feeMonth__isnull=True)) & month_name_filter
    return qs.filter(ym_filter | legacy_filter)


def _fee_month_label(fee_obj):
    short_month = fee_obj.month
    if fee_obj.month:
        try:
            short_month = datetime.strptime(fee_obj.month, '%B').strftime('%b')
        except ValueError:
            short_month = fee_obj.month
    if fee_obj.month and fee_obj.feeYear:
        return f'{short_month}-{fee_obj.feeYear}'
    return short_month or 'N/A'


def _same_text_q(field_name, value):
    normalized = (value or '').strip()
    if not normalized:
        return Q(**{f'{field_name}__isnull': True}) | Q(**{field_name: ''})
    return Q(**{f'{field_name}__iexact': normalized})


def _previous_school_session_for_current(request):
    current_session_id = _current_session_id(request)
    school_id = request.session.get('current_session', {}).get('SchoolID')
    if not current_session_id or not school_id:
        return None
    sessions = list(
        SchoolSession.objects.filter(
            schoolID_id=school_id,
            isDeleted=False,
        ).order_by('startDate', 'datetime', 'id')
    )
    ordered_ids = [item.id for item in sessions]
    if current_session_id not in ordered_ids:
        return None
    current_index = ordered_ids.index(current_session_id)
    if current_index <= 0:
        return None
    return sessions[current_index - 1]


def _matching_current_standard_id(previous_standard, current_session_id):
    if not previous_standard or not current_session_id:
        return None
    matched = Standard.objects.filter(
        sessionID_id=current_session_id,
        isDeleted=False,
    ).filter(
        _same_text_q('name', previous_standard.name)
    ).filter(
        _same_text_q('section', previous_standard.section)
    ).order_by('id').first()
    return matched.id if matched else None


def _current_session_parent_defaults(post_data):
    total_family_members = post_data.get("numberOfMembers")
    annual_income = post_data.get("familyAnnualIncome")
    return {
        'fatherName': post_data.get("fname"),
        'motherName': post_data.get("mname"),
        'fatherOccupation': post_data.get("FatherOccupation"),
        'motherOccupation': post_data.get("MotherOccupation"),
        'fatherPhone': post_data.get("fatherContactNumber"),
        'motherPhone': post_data.get("MotherContactNumber"),
        'fatherAddress': post_data.get("FatherAddress"),
        'motherAddress': post_data.get("MotherAddress"),
        'guardianName': post_data.get("guardianName"),
        'guardianOccupation': post_data.get("guardianOccupation"),
        'guardianPhone': post_data.get("guardianPhoneNumber"),
        'familyType': post_data.get("familyType"),
        'totalFamilyMembers': float(total_family_members) if total_family_members else 0,
        'annualIncome': float(annual_income) if annual_income else 0,
        'phoneNumber': post_data.get("parentsPhoneNumber"),
        'fatherEmail': post_data.get("fatherEmail"),
        'motherEmail': post_data.get("motherEmail"),
        'isDeleted': False,
    }


def _get_or_create_current_session_parent(request, post_data):
    current_session = request.session.get('current_session', {})
    current_session_id = current_session.get('Id')
    school_id = current_session.get('SchoolID')
    parent_payload = _current_session_parent_defaults(post_data)

    parent_obj, _ = Parent.objects.get_or_create(
        sessionID_id=current_session_id,
        schoolID_id=school_id,
        **parent_payload,
        defaults={},
    )
    pre_save_with_user.send(sender=Parent, instance=parent_obj, user=request.user.pk)
    return parent_obj


def _sync_admission_finance_for_student(request, student_obj, post_data):
    current_session = request.session.get('current_session', {})
    session_id = current_session.get('Id') or student_obj.sessionID_id
    school_id = current_session.get('SchoolID')
    if not session_id:
        return
    if not school_id and session_id:
        school_id = SchoolSession.objects.filter(pk=session_id, isDeleted=False).values_list('schoolID_id', flat=True).first()
    if not school_id:
        return

    admission_amount = _decimal_or_zero(post_data.get('admissionFee'))
    charge_date = student_obj.dateOfJoining or timezone.now().date()
    due_date = charge_date
    charge_obj = sync_student_charge(
        student_obj=student_obj,
        school_id=school_id,
        session_id=session_id,
        fee_head_code='ADMISSION_FEE',
        amount=admission_amount,
        charge_date=charge_date,
        due_date=due_date,
        source_module='student_admission_charge',
        source_record_id=f'{student_obj.id}:admission',
        title='Admission Fee',
        description=f'Admission fee for {student_obj.name or student_obj.registrationCode or student_obj.id}',
        standard_obj=student_obj.standardID,
        user_obj=request.user,
    )

    payment_amount = _decimal_or_zero(
        post_data.get('admissionAmountReceived')
        or post_data.get('admissionFeePaidAmount')
    )
    if payment_amount <= 0 and _truthy(post_data.get('admissionFeePaid')):
        payment_amount = admission_amount

    receipt_source_id = f'{student_obj.id}:admission'
    receipt_touched = _truthy(post_data.get('admissionPaymentTouched'))
    if not receipt_touched:
        return
    if charge_obj and payment_amount > 0:
        sync_payment_receipt(
            charge_obj=charge_obj,
            school_id=school_id,
            session_id=session_id,
            amount_received=payment_amount,
            receipt_date=charge_date,
            source_module='student_admission_receipt',
            source_record_id=receipt_source_id,
            payment_mode_code=post_data.get('admissionPaymentMode') or post_data.get('paymentMode') or 'CASH',
            reference_no=post_data.get('admissionPaymentReference') or post_data.get('paymentReference') or '',
            notes=post_data.get('admissionPaymentRemark') or 'Admission fee receipt',
            received_from_name=(student_obj.parentID.fatherName if student_obj.parentID and student_obj.parentID.fatherName else student_obj.name or ''),
            user_obj=request.user,
        )
    else:
        clear_payment_receipt(
            school_id=school_id,
            source_module='student_admission_receipt',
            source_record_id=receipt_source_id,
            user_obj=request.user,
        )


def _sync_student_fee_finance(request, fee_obj):
    session_id = fee_obj.sessionID_id or request.session.get('current_session', {}).get('Id')
    school_id = request.session.get('current_session', {}).get('SchoolID')
    if not school_id and session_id:
        school_id = SchoolSession.objects.filter(pk=session_id, isDeleted=False).values_list('schoolID_id', flat=True).first()
    if not session_id or not school_id or not fee_obj.studentID_id:
        return

    amount = _decimal_or_zero(fee_obj.amount)
    charge_date = fee_obj.periodStartDate or fee_obj.payDate or timezone.now().date()
    due_date = fee_obj.dueDate or fee_obj.periodEndDate or charge_date
    month_label = _fee_month_label(fee_obj)
    charge_obj = sync_student_charge(
        student_obj=fee_obj.studentID,
        school_id=school_id,
        session_id=session_id,
        fee_head_code='MONTHLY_STUDENT_FEE',
        amount=amount,
        charge_date=charge_date,
        due_date=due_date,
        source_module='legacy_student_fee_charge',
        source_record_id=str(fee_obj.id),
        title=f'Monthly Fee {month_label}',
        description=fee_obj.note or '',
        standard_obj=fee_obj.standardID,
        user_obj=request.user,
    )

    if charge_obj and fee_obj.isPaid and amount > 0:
        sync_payment_receipt(
            charge_obj=charge_obj,
            school_id=school_id,
            session_id=session_id,
            amount_received=amount,
            receipt_date=fee_obj.payDate or timezone.now().date(),
            source_module='legacy_student_fee_receipt',
            source_record_id=str(fee_obj.id),
            payment_mode_code=request.POST.get('paymentMode') or request.POST.get('paymentModeCode') or 'CASH',
            reference_no=request.POST.get('paymentReference') or request.POST.get('referenceNo') or '',
            notes=fee_obj.note or f'Receipt for {month_label}',
            received_from_name=(fee_obj.studentID.parentID.fatherName if fee_obj.studentID.parentID and fee_obj.studentID.parentID.fatherName else fee_obj.studentID.name or ''),
            user_obj=request.user,
        )
    else:
        clear_payment_receipt(
            school_id=school_id,
            source_module='legacy_student_fee_receipt',
            source_record_id=str(fee_obj.id),
            user_obj=request.user,
        )


@login_required
@check_groups('Admin', 'Owner')
def get_session_import_meta_api(request):
    school_id = request.session.get('current_session', {}).get('SchoolID')
    current_session_id = request.session.get('current_session', {}).get('Id')
    if not school_id:
        return ErrorResponse('School context was not found.', extra={'color': 'red'}).to_json_response()

    sessions = list(
        SchoolSession.objects.filter(
            schoolID_id=school_id,
            isDeleted=False,
        ).order_by('-startDate', '-datetime', '-id').values(
            'id', 'sessionYear', 'startDate', 'endDate', 'isCurrent'
        )
    )
    for row in sessions:
        row['startDate'] = row['startDate'].isoformat() if row['startDate'] else None
        row['endDate'] = row['endDate'].isoformat() if row['endDate'] else None

    return SuccessResponse(
        'Session import metadata loaded successfully.',
        data={
            'currentSessionId': current_session_id,
            'sessions': sessions,
        }
    ).to_json_response()


@csrf_exempt
@login_required
@check_groups('Admin', 'Owner')
def preview_session_import_api(request):
    if request.method != 'POST':
        return ErrorResponse('Method not allowed.', status_code=405, extra={'color': 'red'}).to_json_response()

    school_id = request.session.get('current_session', {}).get('SchoolID')
    source_session_id = request.POST.get('sourceSessionID')
    target_session_id = request.POST.get('targetSessionID')
    if not school_id:
        return ErrorResponse('School context was not found.', extra={'color': 'red'}).to_json_response()
    if not source_session_id or not target_session_id:
        return ErrorResponse('Source session and target session are required.', extra={'color': 'red'}).to_json_response()

    options = {
        'copy_teachers': _truthy(request.POST.get('copyTeachers')),
        'copy_classes': _truthy(request.POST.get('copyClasses')),
        'copy_subjects': _truthy(request.POST.get('copySubjects')),
        'copy_class_subjects': _truthy(request.POST.get('copyClassSubjects')),
        'copy_teacher_subjects': _truthy(request.POST.get('copyTeacherSubjects')),
        'copy_exam_setup': _truthy(request.POST.get('copyExamSetup')),
        'copy_grading_setup': _truthy(request.POST.get('copyGradingSetup')),
        'copy_leave_types': _truthy(request.POST.get('copyLeaveTypes')),
        'copy_event_types': _truthy(request.POST.get('copyEventTypes')),
        'copy_students': _truthy(request.POST.get('copyStudents')),
        'promote_students': _truthy(request.POST.get('promoteStudents')),
    }
    try:
        options['promotion_overrides'] = _parse_promotion_overrides(request.POST.get('promotionOverrides'))
        options['selection_overrides'] = _parse_selection_overrides(request.POST.get('selectionOverrides'))
    except ValueError as exc:
        return ErrorResponse(str(exc), extra={'color': 'red'}).to_json_response()
    try:
        payload = preview_session_import(
            school_id=school_id,
            source_session_id=int(source_session_id),
            target_session_id=int(target_session_id),
            options=options,
        )
    except ValueError as exc:
        return ErrorResponse(str(exc), extra={'color': 'red'}).to_json_response()
    except Exception as exc:
        logger.exception('Preview session import failed: %s', exc)
        return ErrorResponse('Unable to preview session import right now.', extra={'color': 'red'}).to_json_response()

    return SuccessResponse('Session import preview generated successfully.', data=payload).to_json_response()


@transaction.atomic
@csrf_exempt
@login_required
@check_groups('Admin', 'Owner')
def run_session_import_api(request):
    if request.method != 'POST':
        return ErrorResponse('Method not allowed.', status_code=405, extra={'color': 'red'}).to_json_response()

    school_id = request.session.get('current_session', {}).get('SchoolID')
    source_session_id = request.POST.get('sourceSessionID')
    target_session_id = request.POST.get('targetSessionID')
    if not school_id:
        return ErrorResponse('School context was not found.', extra={'color': 'red'}).to_json_response()
    if not source_session_id or not target_session_id:
        return ErrorResponse('Source session and target session are required.', extra={'color': 'red'}).to_json_response()

    options = {
        'copy_teachers': _truthy(request.POST.get('copyTeachers')),
        'copy_classes': _truthy(request.POST.get('copyClasses')),
        'copy_subjects': _truthy(request.POST.get('copySubjects')),
        'copy_class_subjects': _truthy(request.POST.get('copyClassSubjects')),
        'copy_teacher_subjects': _truthy(request.POST.get('copyTeacherSubjects')),
        'copy_exam_setup': _truthy(request.POST.get('copyExamSetup')),
        'copy_grading_setup': _truthy(request.POST.get('copyGradingSetup')),
        'copy_leave_types': _truthy(request.POST.get('copyLeaveTypes')),
        'copy_event_types': _truthy(request.POST.get('copyEventTypes')),
        'copy_students': _truthy(request.POST.get('copyStudents')),
        'promote_students': _truthy(request.POST.get('promoteStudents')),
    }
    try:
        options['promotion_overrides'] = _parse_promotion_overrides(request.POST.get('promotionOverrides'))
        options['selection_overrides'] = _parse_selection_overrides(request.POST.get('selectionOverrides'))
    except ValueError as exc:
        return ErrorResponse(str(exc), extra={'color': 'red'}).to_json_response()
    try:
        payload = run_session_import(
            school_id=school_id,
            source_session_id=int(source_session_id),
            target_session_id=int(target_session_id),
            options=options,
            acting_user=request.user,
        )
    except ValueError as exc:
        return ErrorResponse(str(exc), extra={'color': 'red'}).to_json_response()
    except Exception as exc:
        logger.exception('Run session import failed: %s', exc)
        return ErrorResponse('Unable to complete session import right now.', extra={'color': 'red'}).to_json_response()

    return SuccessResponse(
        'Session import completed successfully.',
        data=payload,
        extra={'color': 'green'}
    ).to_json_response()


def _editor_name(user):
    full_name = user.get_full_name().strip()
    return full_name or user.username


def _count_approved_teacher_leave_days(session_id, teacher_id, start_date, end_date):
    leaves = LeaveApplication.objects.filter(
        isDeleted=False,
        sessionID_id=session_id,
        applicantRole='teacher',
        teacherID_id=teacher_id,
        status='approved',
        startDate__lte=end_date,
        endDate__gte=start_date,
    ).only('startDate', 'endDate')

    days = set()
    for leave in leaves:
        overlap_start = max(start_date, leave.startDate)
        overlap_end = min(end_date, leave.endDate)
        if overlap_end < overlap_start:
            continue
        current_day = overlap_start
        while current_day <= overlap_end:
            days.add(current_day)
            current_day += timedelta(days=1)
    return len(days)


def _safe_image_url(image_field, fallback_path='images/default_avatar.svg'):
    return safe_image_url(image_field, fallback_path=fallback_path)


def _avatar_image_html(image_field):
    return avatar_image_html(image_field)


def _management_serialize_subject_note(row):
    class_name = ''
    if row.standardID:
        class_name = row.standardID.name or ''
        if row.standardID.section:
            class_name = f'{class_name} - {row.standardID.section}'
    return {
        'id': row.id,
        'title': row.title or '',
        'status': row.status or 'draft',
        'subjectID': row.subjectID_id,
        'subjectName': row.subjectID.name if row.subjectID else '',
        'className': class_name,
        'teacherName': row.teacherID.name if row.teacherID else 'N/A',
        'publishedAt': row.publishedAt.strftime('%d-%m-%Y %I:%M %p') if row.publishedAt else '',
        'lastUpdatedOn': row.lastUpdatedOn.strftime('%d-%m-%Y %I:%M %p') if row.lastUpdatedOn else '',
        'contentHtml': row.contentHtml or '',
    }


@login_required
@check_groups('Admin', 'Owner')
def get_management_subject_note_filter_meta_api(request):
    current_session_id = request.session.get('current_session', {}).get('Id')
    current_school_id = request.session.get('current_session', {}).get('SchoolID')

    note_qs = SubjectNote.objects.select_related('teacherID', 'subjectID', 'standardID').filter(isDeleted=False)
    if current_session_id:
        note_qs = note_qs.filter(sessionID_id=current_session_id)
    if current_school_id:
        note_qs = note_qs.filter(schoolID_id=current_school_id)

    teacher_map = {}
    subject_map = {}
    class_map = {}
    for row in note_qs:
        if row.teacherID_id and row.teacherID and row.teacherID.name:
            teacher_map[row.teacherID_id] = row.teacherID.name
        if row.subjectID_id and row.subjectID and row.subjectID.name:
            subject_map[row.subjectID_id] = row.subjectID.name
        if row.standardID_id and row.standardID:
            c_name = row.standardID.name or ''
            if row.standardID.section:
                c_name = f'{c_name} - {row.standardID.section}'
            class_map[row.standardID_id] = c_name

    return SuccessResponse('Subject notes filter metadata loaded.', data={
        'teachers': [{'id': key, 'name': val} for key, val in sorted(teacher_map.items(), key=lambda x: x[1].lower())],
        'subjects': [{'id': key, 'name': val} for key, val in sorted(subject_map.items(), key=lambda x: x[1].lower())],
        'classes': [{'id': key, 'name': val} for key, val in sorted(class_map.items(), key=lambda x: x[1].lower())],
    }).to_json_response()


@login_required
@check_groups('Admin', 'Owner')
def get_management_subject_note_list_api(request):
    current_session_id = request.session.get('current_session', {}).get('Id')
    current_school_id = request.session.get('current_session', {}).get('SchoolID')

    search = (request.GET.get('search') or '').strip()
    status_value = (request.GET.get('status') or '').strip().lower()
    teacher_id = (request.GET.get('teacherID') or '').strip()
    subject_id = (request.GET.get('subjectID') or '').strip()
    standard_id = (request.GET.get('standardID') or '').strip()

    note_qs = SubjectNote.objects.select_related(
        'teacherID', 'subjectID', 'standardID'
    ).filter(isDeleted=False)
    if current_session_id:
        note_qs = note_qs.filter(sessionID_id=current_session_id)
    if current_school_id:
        note_qs = note_qs.filter(schoolID_id=current_school_id)

    if status_value in {'draft', 'published'}:
        note_qs = note_qs.filter(status=status_value)
    if teacher_id.isdigit():
        note_qs = note_qs.filter(teacherID_id=int(teacher_id))
    if subject_id.isdigit():
        note_qs = note_qs.filter(subjectID_id=int(subject_id))
    if standard_id.isdigit():
        note_qs = note_qs.filter(standardID_id=int(standard_id))
    if search:
        note_qs = note_qs.filter(
            Q(title__icontains=search)
            | Q(contentHtml__icontains=search)
            | Q(teacherID__name__icontains=search)
            | Q(subjectID__name__icontains=search)
            | Q(standardID__name__icontains=search)
            | Q(standardID__section__icontains=search)
        )

    rows = [_management_serialize_subject_note(row) for row in note_qs.order_by('-lastUpdatedOn')[:400]]
    return SuccessResponse('Subject notes loaded successfully.', data=rows).to_json_response()


@login_required
@check_groups('Admin', 'Owner')
def get_management_subject_note_detail_api(request):
    note_id = (request.GET.get('id') or '').strip()
    if not note_id.isdigit():
        return ErrorResponse('Invalid note id.', extra={'color': 'red'}).to_json_response()

    row = SubjectNote.objects.select_related(
        'teacherID', 'subjectID', 'standardID'
    ).filter(
        id=int(note_id),
        isDeleted=False,
    ).first()
    if not row:
        return ErrorResponse('Note not found.', extra={'color': 'red'}).to_json_response()
    return SuccessResponse('Note details loaded.', data=_management_serialize_subject_note(row)).to_json_response()


@transaction.atomic
@csrf_exempt
@login_required
@check_groups('Admin', 'Owner')
@validate_input(['id'])
def toggle_management_subject_note_publish_api(request):
    if request.method != 'POST':
        return ErrorResponse('Method not allowed.', status_code=405).to_json_response()

    note_id = (request.POST.get('id') or '').strip()
    if not note_id.isdigit():
        return ErrorResponse('Invalid note id.', extra={'color': 'red'}).to_json_response()

    note_obj = SubjectNote.objects.filter(
        id=int(note_id),
        isDeleted=False,
    ).first()
    if not note_obj:
        return ErrorResponse('Note not found.', extra={'color': 'red'}).to_json_response()

    if note_obj.status == 'published':
        note_obj.status = 'draft'
        note_obj.publishedAt = None
        message = 'Note moved to draft.'
    else:
        note_obj.status = 'published'
        note_obj.publishedAt = timezone.now()
        message = 'Note published successfully.'

    note_obj.currentVersionNo = (note_obj.currentVersionNo or 1) + 1
    note_obj.lastEditedBy = _editor_name(request.user)
    note_obj.updatedByUserID_id = request.user.id
    note_obj.save()

    SubjectNoteVersion.objects.create(
        noteID_id=note_obj.id,
        schoolID_id=note_obj.schoolID_id,
        sessionID_id=note_obj.sessionID_id,
        teacherID_id=note_obj.teacherID_id,
        title=note_obj.title,
        contentHtml=note_obj.contentHtml,
        status=note_obj.status,
        versionNo=note_obj.currentVersionNo,
        lastEditedBy=_editor_name(request.user),
        updatedByUserID_id=request.user.id,
    )

    return SuccessResponse(message, data=_management_serialize_subject_note(note_obj), extra={'color': 'green'}).to_json_response()


@login_required
@check_groups('Admin', 'Owner')
def get_school_detail_api(request):
    try:
        school_id = request.session.get('current_session', {}).get('SchoolID')
        school = None
        if school_id:
            school = SchoolDetail.objects.filter(pk=school_id, isDeleted=False).first()
        if not school:
            school = SchoolDetail.objects.filter(school_owner_user_q(request.user.id), isDeleted=False).distinct().order_by('-datetime').first()
        if not school:
            return ErrorResponse('School detail not found.').to_json_response()

        data = {
            'id': school.id,
            'schoolName': school.schoolName or '',
            'name': school.name or '',
            'address': school.address or '',
            'city': school.city or '',
            'state': school.state or '',
            'country': school.country or '',
            'pinCode': school.pinCode or '',
            'phoneNumber': school.phoneNumber or '',
            'email': school.email or '',
            'website': school.website or '',
            'logo': school.logo.url if school.logo else '',
            'webPushEnabled': bool(school.webPushEnabled),
            'webPushStudentAppEnabled': bool(school.webPushStudentAppEnabled),
            'webPushTeacherAppEnabled': bool(school.webPushTeacherAppEnabled),
            'webPushManagementAppEnabled': bool(school.webPushManagementAppEnabled),
        }
        return SuccessResponse('School details fetched successfully.', data=data).to_json_response()
    except Exception as e:
        logger.error(f'Error in get_school_detail_api: {e}')
        return ErrorResponse('Unable to fetch school details.').to_json_response()


@transaction.atomic
@csrf_exempt
@login_required
@check_groups('Admin', 'Owner')
def update_school_detail_api(request):
    if request.method != 'POST':
        return ErrorResponse('Method not allowed.').to_json_response()
    try:
        school_id = request.session.get('current_session', {}).get('SchoolID')
        school = None
        if school_id:
            school = SchoolDetail.objects.filter(pk=school_id, isDeleted=False).first()
        if not school:
            school = SchoolDetail.objects.filter(school_owner_user_q(request.user.id), isDeleted=False).distinct().order_by('-datetime').first()
        if not school:
            return ErrorResponse('School detail not found.').to_json_response()

        school.schoolName = (request.POST.get('schoolName') or '').strip()
        school.name = (request.POST.get('name') or '').strip()
        school.address = (request.POST.get('address') or '').strip()
        school.city = (request.POST.get('city') or '').strip()
        school.state = (request.POST.get('state') or '').strip()
        school.country = (request.POST.get('country') or '').strip()
        school.pinCode = (request.POST.get('pinCode') or '').strip()
        school.phoneNumber = (request.POST.get('phoneNumber') or '').strip()
        school.email = (request.POST.get('email') or '').strip()
        school.website = (request.POST.get('website') or '').strip()
        school.webPushEnabled = (request.POST.get('webPushEnabled') or 'false').lower() == 'true'
        school.webPushStudentAppEnabled = (request.POST.get('webPushStudentAppEnabled') or 'false').lower() == 'true'
        school.webPushTeacherAppEnabled = (request.POST.get('webPushTeacherAppEnabled') or 'false').lower() == 'true'
        school.webPushManagementAppEnabled = (request.POST.get('webPushManagementAppEnabled') or 'false').lower() == 'true'
        logo = request.FILES.get('logo')
        if logo:
            school.logo = optimize_uploaded_image(logo, max_width=1024, max_height=1024, jpeg_quality=85)

        if not school.schoolName:
            return ErrorResponse('School name is required.').to_json_response()
        if not school.address:
            return ErrorResponse('Address is required.').to_json_response()

        school.lastEditedBy = _editor_name(request.user)
        school.updatedByUserID_id = request.user.id
        school.save()

        current_session = dict(request.session.get('current_session', {}))
        current_session['SchoolID'] = school.id
        current_session['SchoolName'] = school.schoolName or school.name or current_session.get('SchoolName')
        current_session['SchoolLogo'] = school.logo.url if school.logo else current_session.get('SchoolLogo')
        request.session['current_session'] = current_session

        return SuccessResponse('School details updated successfully.', extra={'color': 'green'}).to_json_response()
    except Exception as e:
        logger.error(f'Error in update_school_detail_api: {e}')
        return ErrorResponse('Unable to update school details.').to_json_response()

# Class ------------------
@transaction.atomic
@csrf_exempt
@login_required
@validate_input(["className","classLocation","hasSection","startRoll0","endRoll0"])
def add_class(request):
    if request.method == 'POST':
        try:
            className = request.POST.get("className")
            classLocation = request.POST.get("classLocation")
            hasSection = request.POST.get("hasSection")
            startRoll0 = request.POST.get("startRoll0")
            endRoll0 = request.POST.get("endRoll0")
            secDetail = request.POST.get("secDetail")

            if hasSection == "No":
                try:
                    Standard.objects.get(name__iexact=className, hasSection=hasSection, isDeleted=False,
                                         sessionID_id=request.session["current_session"]["Id"])
                    logger.info( f"Class already exists {request.session["current_session"]["Id"]}- {className}")                     
                    return _api_response(
                        {'status': 'success', 'message': 'Class already exists. Please change the name.',
                         'color': 'info'}, safe=False)
                except:
                    instance = Standard()
                    instance.name = className
                    instance.hasSection = hasSection
                    instance.classLocation = classLocation
                    instance.startingRoll = startRoll0
                    instance.endingRoll = endRoll0
                    pre_save_with_user.send(sender=Standard, instance=instance, user=request.user.pk)
                    instance.save()
                    new_data = {
                        "ID": instance.pk,
                        "Name": instance.name

                    }
                    add_item_to_existing_cache("standard_list"+str(request.session["current_session"]["Id"]), new_data)
                    logger.info( f"Class created successfully {request.session["current_session"]["Id"]}- {instance.name}")
                    return _api_response(
                        {'status': 'success', 'message': 'New class created successfully.', 'color': 'success'},
                        safe=False)
            elif hasSection == 'Yes':
                try:

                    sectionArray = secDetail.split('@')
                    result = []
                    # Split each part by "|"
                    for part in sectionArray:
                        split2 = part.split("|")
                        result.append(split2)
                    # Remove the last empty element if it exists
                    if result[-1] == ['']:
                        result.pop()
                    for i in result:
                        try:
                            Standard.objects.get(name__iexact=className, hasSection=hasSection, section__iexact=i[0],
                                                 isDeleted=False,sessionID_id=request.session["current_session"]["Id"])
                            return _api_response(
                                {'status': 'success', 'message': 'Class already exists. Please change the name.',
                                'color': 'info'}, safe=False)
                        except:
                            instance = Standard()
                            instance.name = className
                            instance.hasSection = hasSection
                            instance.classLocation = classLocation
                            instance.startingRoll = i[1]
                            instance.endingRoll = i[2]
                            instance.section = i[0]
                            pre_save_with_user.send(sender=Standard, instance=instance, user=request.user.pk)
                            instance.save()
                            new_data = {
                            'ID': instance.pk,
                            'Name': instance.name + ' - ' + instance.section if instance.section else instance.name

                            }
                            add_item_to_existing_cache("standard_list"+str(request.session["current_session"]["Id"]), new_data)
                            logger.info( f"Class created successfully {request.session["current_session"]["Id"]}- {instance.name}-{instance.section}")

                    return _api_response(
                        {'status': 'success', 'message': 'New classes created successfully.', 'color': 'success'},
                        safe=False)
                except Exception as e:
                    logger.error(f"Error creating classes: {e}")
                    return _api_response({'status': 'error'}, safe=False)
            return _api_response({'status': 'error'}, safe=False)
        except Exception as e:
            logger.error(f"Error in add_class: {e}")
            return _api_response({'status': 'error'}, safe=False)


@transaction.atomic
@csrf_exempt
@login_required
# @validate_input()
def update_class(request):
    if request.method != 'POST':
        logger.error("Method not allowed")
        return ErrorResponse("Method not allowed").to_json_response()

    data = request.POST.dict()
    try:
        current_session_id = request.session['current_session']['Id']
        obj = Standard.objects.get(
            pk=data['dataIDEdit'],
            isDeleted=False,
            sessionID_id=current_session_id
        )

        teacher_id = data.get("teacherEdit")
        teacher_id = int(teacher_id) if teacher_id and str(teacher_id).strip() else None

        if teacher_id:
            teacher_exists = TeacherDetail.objects.filter(
                pk=teacher_id,
                isDeleted=False,
                sessionID_id=current_session_id,
                isActive='Yes'
            ).exists()
            if not teacher_exists:
                return ErrorResponse("Selected class teacher does not exist in current session.").to_json_response()

            teacher_already_assigned = Standard.objects.filter(
                isDeleted=False,
                sessionID_id=current_session_id,
                classTeacher_id=teacher_id
            ).exclude(pk=obj.pk).exists()
            if teacher_already_assigned:
                return ErrorResponse("This teacher is already assigned as class teacher for another class.").to_json_response()

        obj.name = data["classNameEdit"]
        obj.classLocation = data["classLocationEdit"]
        obj.startingRoll = data.get("startRoll0Edit") or '0'
        obj.endingRoll = data.get("endRoll0Edit") or '0'
        section_value = data.get("section0Edit")
        obj.section = None if section_value in ("", "N/A", None) else section_value
        obj.classTeacher_id = teacher_id
        pre_save_with_user.send(sender=Standard, instance=obj, user=request.user.pk)
        obj.save()

        new_data = {
                    'ID': obj.pk,
                'Name': obj.name + ' - ' + obj.section if obj.section else obj.name
                }
        update_item_in_existing_cache("standard_list"+str(request.session['current_session']['Id']), obj.pk, new_data)
        logger.info("Class detail updated successfully")
        return SuccessResponse("Class detail updated successfully").to_json_response()

    except Standard.DoesNotExist:
        logger.error("Class not found")
        return ErrorResponse("Class not found").to_json_response()
    except Exception as e:
        logger.error(f"Error in update_class: {e}")
        return ErrorResponse("Error in updating Class details").to_json_response()
    


class StandardListJson(BaseDatatableView):
    order_columns = ['name', 'section', 'classTeacher', 'startingRoll', 'endingRoll', 'classLocation', 'lastEditedBy',
                     'lastUpdatedOn']

    def get_initial_queryset(self):
        return Standard.objects.select_related('classTeacher').only(
            'id', 'name', 'section', 'startingRoll', 'endingRoll', 'classLocation',
            'lastEditedBy', 'lastUpdatedOn', 'classTeacher__name'
        ).filter(
            isDeleted__exact=False,
            sessionID_id=self.request.session["current_session"]["Id"],
            # schoolID_id=school_id
        )

    def filter_queryset(self, qs):

        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(name__icontains=search) | Q(section__icontains=search)
                | Q(classTeacher__name__icontains=search) | Q(classLocation__icontains=search)
                | Q(lastEditedBy__icontains=search) | Q(lastUpdatedOn__icontains=search)
            )

        return qs

    def prepare_results(self, qs):
        json_data = []
        for item in qs:
            action = '''<button data-inverted="" data-tooltip="Edit Detail" data-position="left center" data-variation="mini" style="font-size:10px;" onclick = "GetDataDetails('{}')" class="ui circular facebook icon button green">
                <i class="pen icon"></i>
              </button>
              <button data-inverted="" data-tooltip="Delete" data-position="left center" data-variation="mini" style="font-size:10px;" onclick ="delData('{}')" class="ui circular youtube icon button" style="margin-left: 3px">
                <i class="trash alternate icon"></i>
              </button></td>'''.format(item.pk, item.pk)
            teacher = item.classTeacher.name if item.classTeacher and item.classTeacher.name else "N/A"
            json_data.append([
                escape(item.name),
                escape(item.section if item.section else "N/A"),
                escape(teacher),
                escape(item.startingRoll),
                escape(item.endingRoll),
                escape(item.classLocation),
                escape(item.lastEditedBy or 'N/A'),
                escape(item.lastUpdatedOn.strftime('%d-%m-%Y %I:%M %p') if item.lastUpdatedOn else 'N/A'),
                action,

            ])

        return json_data


@login_required
def get_class_detail(request, **kwargs):
    try:
        id = request.GET.get('id')
        obj = Standard.objects.get(pk=id, isDeleted=False, sessionID_id=request.session['current_session']['Id'])
        if obj.classTeacher:
            teacher = obj.classTeacher.name if obj.classTeacher.name else "N/A"
            teacherID = str(obj.classTeacher.pk)
        else:
            teacher = "N/A"
            teacherID = ""
        if obj.hasSection == "Yes":
            section = obj.section
        else:
            section = "N/A"
        obj_dic = {
            'ClassID': obj.pk,
            'Class': obj.name,
            'Location': obj.classLocation,
            'Section': section,
            'StartRoll': obj.startingRoll,
            'EndRoll': obj.endingRoll,
            'Teacher': teacher,
            'TeacherID': str(teacherID)
        }
        return _api_response({'status': 'success', 'data': obj_dic}, safe=False)
    except:
        return _api_response({'status': 'error'}, safe=False)


@transaction.atomic
@csrf_exempt
@login_required
def delete_class(request):
    if request.method == 'POST':
        try:
            id = request.POST.get("dataID")
            instance = Standard.objects.get(pk=int(id), isDeleted=False,
                                            sessionID_id=request.session['current_session']['Id'])
            instance.isDeleted = True
            pre_save_with_user.send(sender=Standard, instance=instance, user=request.user.pk)
            instance.save()
            delete_item_from_existing_cache('standard_list'+str(request.session['current_session']['Id']), id)
            logger.info(f"Class detail deleted successfully {request.session['current_session']['Id']} class name {instance.name}")
            return SuccessResponse("Class detail deleted successfully.").to_json_response()
        except Exception as e:
            logger.error(f"Error in delete_class: {e}")
            return ErrorResponse("Error in deleting Class details").to_json_response()
    else:
        logger.error("Method not allowed")
        return ErrorResponse("Method not allowed").to_json_response()        


# subjects -----------------------------------

# subject

@transaction.atomic
@csrf_exempt
@login_required
def add_subject(request):
    if request.method == 'POST':
        subject_name = request.POST.get("subject_name")
        try:
            Subjects.objects.get(name__iexact=subject_name, isDeleted=False,
                                 sessionID_id=request.session['current_session']['Id'])
            return _api_response(
                {'status': 'success', 'message': 'Subject already exists. Please change the name.', 'color': 'info'},
                safe=False)
        except:
            instance = Subjects()
            instance.name = subject_name
            pre_save_with_user.send(sender=Subjects, instance=instance, user=request.user.pk)
            instance.save()
            new_item = {
                'ID': instance.pk,
                'Name': instance.name
            }
            # add a new item to the cache
            add_item_to_existing_cache('subjects_list'+str(request.session['current_session']['Id']), new_item)
            return _api_response(
                {'status': 'success', 'message': 'New subject created successfully.', 'color': 'success'},
                safe=False)
    return _api_response({'status': 'error'}, safe=False)


class SubjectListJson(BaseDatatableView):
    order_columns = ['name', 'lastEditedBy', 'lastUpdatedOn']

    def get_initial_queryset(self):
        return Subjects.objects.only(
            'id', 'name', 'lastEditedBy', 'datetime', 'lastUpdatedOn'
        ).filter(
            isDeleted__exact=False,
            sessionID_id=self.request.session["current_session"]["Id"]
        )

    def filter_queryset(self, qs):

        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(name__icontains=search)
                | Q(lastEditedBy__icontains=search) | Q(lastUpdatedOn__icontains=search)
            )

        return qs

    def prepare_results(self, qs):
        json_data = []
        for item in qs:
            action = '''<button data-inverted="" data-tooltip="Edit Detail" data-position="left center" data-variation="mini" style="font-size:10px;" onclick = "GetDataDetails('{}')" class="ui circular facebook icon button green">
                <i class="pen icon"></i>
              </button>
              <button data-inverted="" data-tooltip="Delete" data-position="left center" data-variation="mini" style="font-size:10px;" onclick ="delData('{}')" class="ui circular youtube icon button" style="margin-left: 3px">
                <i class="trash alternate icon"></i>
              </button></td>'''.format(item.pk, item.pk),

            json_data.append([
                escape(item.name),
                escape(item.lastEditedBy or 'N/A'),
                escape(item.lastUpdatedOn.strftime('%d-%m-%Y %I:%M %p') if item.lastUpdatedOn else 'N/A'),
                action,

            ])

        return json_data


@transaction.atomic
@csrf_exempt
@login_required
def delete_subject(request):
    if request.method == 'POST':
        try:
            id = request.POST.get("dataID")
            instance = Subjects.objects.get(pk=int(id), isDeleted=False,
                                            sessionID_id=request.session['current_session']['Id'])
            instance.isDeleted = True
            pre_save_with_user.send(sender=Subjects, instance=instance, user=request.user.pk)
            delete_item_from_existing_cache("subjects_list"+str(request.session['current_session']['Id']), id)
            instance.save()
            return _api_response(
                {'status': 'success', 'message': 'Subject detail deleted successfully.',
                 'color': 'success'}, safe=False)
        except:
            return _api_response({'status': 'error'}, safe=False)
    return _api_response({'status': 'error'}, safe=False)


@login_required
def get_subject_detail(request, **kwargs):
    try:
        id = request.GET.get('id')
        obj = Subjects.objects.get(pk=id, isDeleted=False, sessionID_id=request.session['current_session']['Id'])
        obj_dic = {
            'ID': obj.pk,
            'SubjectName': obj.name,
        }
        return _api_response({'status': 'success', 'data': obj_dic}, safe=False)
    except:
        return _api_response({'status': 'error'}, safe=False)


@transaction.atomic
@csrf_exempt
@login_required
def edit_subject(request):
    if request.method == 'POST':
        subject_name = request.POST.get("subject_name")
        editID = request.POST.get("editID")
        try:
            instance = Subjects.objects.get(pk=int(editID))
            data = Subjects.objects.filter(name__iexact=subject_name, isDeleted=False,
                                           sessionID_id=request.session['current_session']['Id']).exclude(
                pk=int(editID))
            if data.count() > 0:
                return _api_response(
                    {'status': 'success', 'message': 'Subject already exists. Please change the name.',
                     'color': 'info'},
                    safe=False)
            else:
                # instance = Subjects.objects.get(pk=int(editID))
                instance.name = subject_name
                pre_save_with_user.send(sender=Subjects, instance=instance, user=request.user.pk)
                instance.save()
                new_data = {
                    'ID': instance.pk,
                'Name': instance.name
                }
                update_item_in_existing_cache("subjects_list"+str(request.session['current_session']['Id']), editID, new_data)
                return _api_response(
                    {'status': 'success', 'message': 'Subject name updated successfully.', 'color': 'success'},
                    safe=False)
        except:

            return _api_response({'status': 'error'}, safe=False)



# assign subjects to class------------------------------------------------------------------

@transaction.atomic
@csrf_exempt
@login_required
def add_subject_to_class(request):
    if request.method == 'POST':
        try:
            standard = request.POST.get("standard")
            subjects = request.POST.get("subjects")
            subject_list = [int(x) for x in subjects.split(',')]
            for s in subject_list:
                try:
                    AssignSubjectsToClass.objects.get(subjectID_id=int(s), standardID_id=int(standard), isDeleted=False,
                                                      sessionID_id=request.session['current_session']['Id'])
                except:
                    instance = AssignSubjectsToClass()
                    instance.standardID_id = int(standard)
                    instance.subjectID_id = int(s)
                    pre_save_with_user.send(sender=AssignSubjectsToClass, instance=instance, user=request.user.pk)
                    instance.save()
            return _api_response(
                {'status': 'success', 'message': 'New subject created successfully.', 'color': 'success'},
                safe=False)
        except:
            return _api_response({'status': 'error'}, safe=False)


class AssignSubjectToClassListJson(BaseDatatableView):
    order_columns = ['standardID.name', 'subjectID.name', 'lastEditedBy', 'lastUpdatedOn']

    def get_initial_queryset(self):
        return AssignSubjectsToClass.objects.select_related(
            'standardID', 'subjectID'
        ).only(
            'id', 'lastEditedBy', 'lastUpdatedOn',
            'standardID__name', 'standardID__section',
            'subjectID__name'
        ).filter(
            isDeleted__exact=False,
            sessionID_id=self.request.session["current_session"]["Id"]
        ).order_by('standardID__name')

    def filter_queryset(self, qs):
        class_filter = self.request.GET.get('class_filter')
        subject_filter = self.request.GET.get('subject_filter')

        if class_filter and str(class_filter).isdigit():
            qs = qs.filter(standardID_id=int(class_filter))

        if subject_filter and str(subject_filter).isdigit():
            qs = qs.filter(subjectID_id=int(subject_filter))

        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(standardID__name__icontains=search)
                | Q(subjectID__name__icontains=search) | Q(standardID__section__icontains=search)
                | Q(lastEditedBy__icontains=search) | Q(lastUpdatedOn__icontains=search)
            )

        return qs

    def prepare_results(self, qs):
        json_data = []
        for item in qs:
            action = '''
              <button data-inverted="" data-tooltip="Edit Detail" data-position="left center" data-variation="mini" style="font-size:10px;" onclick = "GetDataDetails('{}')" class="ui circular facebook icon button green">
                <i class="pen icon"></i>
              </button>
              <button data-inverted="" data-tooltip="Delete" data-position="left center" data-variation="mini" style="font-size:10px;" onclick ="delData('{}')" class="ui circular youtube icon button" style="margin-left: 3px">
                <i class="trash alternate icon"></i>
              </button></td>'''.format(item.pk, item.pk),
            if item.standardID.section:
                name = item.standardID.name + ' - ' + item.standardID.section
            else:
                name = item.standardID.name

            json_data.append([
                escape(name),
                escape(item.subjectID.name),
                escape(item.lastEditedBy or 'N/A'),
                escape(item.lastUpdatedOn.strftime('%d-%m-%Y %I:%M %p') if item.lastUpdatedOn else 'N/A'),
                action,

            ])

        return json_data


@transaction.atomic
@csrf_exempt
@login_required
def delete_assign_subject_to_class(request):
    if request.method == 'POST':
        try:
            id = request.POST.get("dataID")
            instance = AssignSubjectsToClass.objects.get(pk=int(id), isDeleted=False,
                                                         sessionID_id=request.session['current_session']['Id'])
            instance.isDeleted = True
            pre_save_with_user.send(sender=AssignSubjectsToClass, instance=instance, user=request.user.pk)
            instance.save()
            return _api_response(
                {'status': 'success', 'message': 'Assigned Subject detail deleted successfully.',
                 'color': 'success'}, safe=False)
        except:
            return _api_response({'status': 'error'}, safe=False)
    return _api_response({'status': 'error'}, safe=False)


@login_required
def get_assigned_subject_to_class_detail(request, **kwargs):
    try:
        id = request.GET.get('id')
        obj = AssignSubjectsToClass.objects.get(pk=id, isDeleted=False,
                                                sessionID_id=request.session['current_session']['Id'])
        obj_dic = {
            'StandardID': obj.standardID.pk,
            'SubjectID': obj.subjectID.pk,
            'ID': obj.pk,
        }
        return _api_response({'status': 'success', 'data': obj_dic}, safe=False)
    except:
        return _api_response({'status': 'error'}, safe=False)


@transaction.atomic
@csrf_exempt
@login_required
def update_subject_to_class(request):
    if request.method == 'POST':
        try:
            editID = request.POST.get("editID")
            standard = request.POST.get("standard")
            subjects = request.POST.get("subjects")
            subject_list = [int(x) for x in subjects.split(',')]
            instance = AssignSubjectsToClass.objects.get(pk=int(editID))
            for s in subject_list:
                try:
                    AssignSubjectsToClass.objects.get(subjectID_id=int(s), standardID_id=int(standard), isDeleted=False,
                                                      sessionID_id=request.session['current_session']['Id']).exclude(
                        pk=int(editID))
                except:
                    # instance = AssignSubjectsToClass()
                    instance.standardID_id = int(standard)
                    instance.subjectID_id = int(s)
                    pre_save_with_user.send(sender=AssignSubjectsToClass, instance=instance, user=request.user.pk)
                    instance.save()
            return _api_response(
                {'status': 'success', 'message': 'Detail updated successfully.', 'color': 'success'},
                safe=False)
        except:
            return _api_response({'status': 'error'}, safe=False)


@login_required
def get_subjects_to_class_assign_list_api(request):
    rows = AssignSubjectsToClass.objects.filter(
        isDeleted=False,
        sessionID_id=_current_session_id(request)
    ).values(
        'id', 'standardID__name', 'standardID__section', 'subjectID__name'
    ).order_by('standardID__name')
    data = []
    for row in rows:
        standard = row.get('standardID__name') or 'N/A'
        section = row.get('standardID__section')
        subject = row.get('subjectID__name') or 'N/A'
        name = f"{standard} - {section} - {subject}" if section else f"{standard} - {subject}"
        data.append({'ID': row['id'], 'Name': name})
    return _api_response(
        {'status': 'success', 'data': data,
         'color': 'success'}, safe=False)


@login_required
def get_subjects_to_class_assign_list_with_given_class_api(request):
    standard = request.GET.get('standard')
    try:
        standard_id = int(standard)
    except (TypeError, ValueError):

        return _api_response({'status': 'success', 'data': [], 'color': 'success'}, safe=False)
    rows = AssignSubjectsToClass.objects.filter(
        isDeleted=False,
        standardID_id=standard_id,
        sessionID_id=_current_session_id(request)
    ).values('id', 'subjectID__name').order_by('subjectID__name')
    data = [{'ID': row['id'], 'Name': row['subjectID__name'] or 'N/A'} for row in rows]
    return _api_response(
        {'status': 'success', 'data': data,
         'color': 'success'}, safe=False)


TIMETABLE_DAY_ORDER = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']
TIMETABLE_PERIOD_TYPES = {
    'teaching': 'Teaching Period',
    'break': 'Break',
    'morning_assembly': 'Morning Assembly',
    'afternoon_assembly': 'Afternoon Assembly',
}


def _safe_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_time_value(value):
    raw = (value or '').strip()
    if not raw:
        return None
    for fmt in ('%H:%M', '%I:%M %p'):
        try:
            return datetime.strptime(raw, fmt).time()
        except ValueError:
            continue
    return None


def _validate_timetable_period_rows(period_rows):
    parsed_rows = []
    for index, row in enumerate(period_rows, start=1):
        name = (row.get('name') or f'Period {index}').strip()
        start_time = _parse_time_value(row.get('startTime'))
        end_time = _parse_time_value(row.get('endTime'))
        if bool(start_time) != bool(end_time):
            raise ValidationError(f'{name} must have both start and end time.')
        if start_time and end_time:
            if start_time >= end_time:
                raise ValidationError(f'{name} end time must be after start time.')
            parsed_rows.append({
                'name': name,
                'start': start_time,
                'end': end_time,
            })

    parsed_rows.sort(key=lambda item: (item['start'], item['end'], item['name']))
    for previous, current in zip(parsed_rows, parsed_rows[1:]):
        if current['start'] < previous['end']:
            raise ValidationError(
                f'{current["name"]} overlaps with {previous["name"]} '
                f'({previous["start"].strftime("%I:%M %p")} - {previous["end"].strftime("%I:%M %p")}).'
            )


def _class_label(standard):
    if not standard:
        return 'N/A'
    return f'{standard.name} - {standard.section}' if standard.section else (standard.name or 'N/A')


def _timetable_period_label(period):
    if not period:
        return 'N/A'
    time_label = ''
    if period.startTime and period.endTime:
        time_label = f' ({period.startTime.strftime("%I:%M %p")} - {period.endTime.strftime("%I:%M %p")})'
    return f'{period.name or "Period"}{time_label}'


def _timetable_period_key(period):
    if not period:
        return ''
    start = period.startTime.strftime('%H:%M') if period.startTime else ''
    end = period.endTime.strftime('%H:%M') if period.endTime else ''
    if start or end:
        return f'{start}|{end}'
    return f'order|{period.displayOrder or 0}|{period.name or "Period"}'


def _period_type_from_name(name, fallback='teaching'):
    normalized_name = (name or '').strip().lower()
    if 'assembly' in normalized_name:
        return 'afternoon_assembly' if 'afternoon' in normalized_name else 'morning_assembly'
    if 'prayer' in normalized_name:
        return 'afternoon_assembly' if 'afternoon' in normalized_name else 'morning_assembly'
    if 'break' in normalized_name or 'lunch' in normalized_name or 'recess' in normalized_name:
        return 'break'
    return fallback


def _normalized_period_type(period):
    if not period:
        return 'teaching'
    period_type = getattr(period, 'periodType', None) or ('break' if getattr(period, 'isBreak', False) else 'teaching')
    if period_type == 'morning_prayer':
        return 'morning_assembly'
    if period_type == 'afternoon_prayer':
        return 'afternoon_assembly'
    if period_type == 'teaching':
        return _period_type_from_name(getattr(period, 'name', ''), fallback=period_type)
    return period_type


def _is_teaching_period(period):
    if not period:
        return False
    return _normalized_period_type(period) == 'teaching' and not period.isBreak


def _timetable_slot_signature(entry):
    if not entry or not entry.periodID:
        return None
    period = entry.periodID
    if period.startTime and period.endTime:
        period_key = (period.startTime, period.endTime)
    else:
        period_key = ('order', period.displayOrder)
    return (entry.dayOfWeek, period_key)


def _serialize_timetable_entry(entry):
    subject_name = ''
    if entry.assignedSubjectID and entry.assignedSubjectID.subjectID:
        subject_name = entry.assignedSubjectID.subjectID.name or ''
    teacher_name = entry.teacherID.name if entry.teacherID else ''
    return {
        'id': entry.id,
        'dayOfWeek': entry.dayOfWeek,
        'periodID': entry.periodID_id,
        'assignedSubjectID': entry.assignedSubjectID_id,
        'teacherID': entry.teacherID_id,
        'room': entry.room or '',
        'note': entry.note or '',
        'subjectName': subject_name,
        'teacherName': teacher_name,
    }


def _day_sort_index(day):
    try:
        return TIMETABLE_DAY_ORDER.index(day)
    except ValueError:
        return len(TIMETABLE_DAY_ORDER)


def _warning_slot_payload(*, entry=None, day=None, period=None):
    if entry:
        day = entry.dayOfWeek
        period = entry.periodID
    return {
        'dayOfWeek': day or '',
        'periodID': period.id if period else None,
    }


def _get_or_create_school_timetable(request, standard_id):
    current_session_id = _current_session_id(request)
    current_school_id = _current_school_id(request)
    standard = Standard.objects.filter(
        pk=standard_id,
        isDeleted=False,
        sessionID_id=current_session_id,
    ).first()
    if not standard:
        raise ValidationError('Please select a valid class.')

    timetable = SchoolTimetable.objects.filter(
        standardID_id=standard_id,
        sessionID_id=current_session_id,
        isDeleted=False,
    ).order_by('-lastUpdatedOn', '-id').first()
    if not timetable:
        timetable = SchoolTimetable(
            standardID_id=standard_id,
            schoolID_id=current_school_id,
            sessionID_id=current_session_id,
            name=f'{_class_label(standard)} Timetable',
            workingDays=TIMETABLE_DAY_ORDER[:5],
        )
        pre_save_with_user.send(sender=SchoolTimetable, instance=timetable, user=request.user.pk)
        timetable.save()
        logger.info(f'Timetable created session={current_session_id} standard={standard_id} user={request.user.id}')
    return timetable


def _mark_timetable_draft(timetable, request):
    if timetable.status != 'draft':
        timetable.status = 'draft'
        timetable.publishedOn = None
        timetable.publishedByUserID = None
        pre_save_with_user.send(sender=SchoolTimetable, instance=timetable, user=request.user.pk)
        timetable.save()
        logger.info(f'Timetable moved to draft timetable={timetable.id} user={request.user.id}')


def _build_timetable_warnings(timetable):
    warnings = []
    if not timetable.workingDays:
        warnings.append({
            'severity': 'error',
            'entryID': None,
            'message': 'At least one working day is required before publishing.',
        })
    entries = list(SchoolTimetableEntry.objects.select_related(
        'periodID',
        'timetableID__standardID',
        'assignedSubjectID__subjectID',
        'assignedSubjectID__standardID',
        'teacherID',
    ).filter(
        sessionID_id=timetable.sessionID_id,
        isDeleted=False,
        timetableID__isDeleted=False,
    ))
    active_entries = [entry for entry in entries if _is_teaching_period(entry.periodID)]

    for entry in active_entries:
        entry_slot = _timetable_slot_signature(entry)
        same_teacher = [
            item for item in active_entries
            if item.id != entry.id
            and item.teacherID_id
            and item.teacherID_id == entry.teacherID_id
            and _timetable_slot_signature(item) == entry_slot
        ]
        if same_teacher:
            other = same_teacher[0]
            warnings.append({
                'severity': 'error',
                'entryID': entry.id,
                **_warning_slot_payload(entry=entry),
                'message': (
                    f'{entry.teacherID.name if entry.teacherID else "Teacher"} is already assigned to '
                    f'{_class_label(other.timetableID.standardID)} on {entry.dayOfWeek}, '
                    f'{_timetable_period_label(entry.periodID)}.'
                ),
            })

        if entry.room:
            same_room = [
                item for item in active_entries
                if item.id != entry.id
                and item.room
                and item.room.strip().lower() == entry.room.strip().lower()
                and _timetable_slot_signature(item) == entry_slot
            ]
            if same_room:
                other = same_room[0]
                warnings.append({
                    'severity': 'error',
                    'entryID': entry.id,
                    **_warning_slot_payload(entry=entry),
                    'message': (
                        f'Room {entry.room} is already used by {_class_label(other.timetableID.standardID)} '
                        f'on {entry.dayOfWeek}, {_timetable_period_label(entry.periodID)}.'
                    ),
                })

        if entry.assignedSubjectID_id and entry.teacherID_id:
            teacher_subject_exists = AssignSubjectsToTeacher.objects.filter(
                assignedSubjectID_id=entry.assignedSubjectID_id,
                teacherID_id=entry.teacherID_id,
                sessionID_id=timetable.sessionID_id,
                isDeleted=False,
            ).exists()
            if not teacher_subject_exists:
                warnings.append({
                    'severity': 'warning',
                    'entryID': entry.id,
                    **_warning_slot_payload(entry=entry),
                    'message': (
                        f'{entry.teacherID.name if entry.teacherID else "Teacher"} is not mapped to '
                        f'{entry.assignedSubjectID.subjectID.name if entry.assignedSubjectID and entry.assignedSubjectID.subjectID else "this subject"} '
                        f'for {_class_label(entry.timetableID.standardID)}.'
                    ),
                })

    own_entries = {
        (entry.dayOfWeek, entry.periodID_id)
        for entry in active_entries
        if entry.timetableID_id == timetable.id and entry.assignedSubjectID_id
    }
    active_periods = list(SchoolTimetablePeriod.objects.filter(
        timetableID=timetable,
        isDeleted=False,
    ).order_by('displayOrder', 'id'))
    active_periods = [period for period in active_periods if _is_teaching_period(period)]
    if not active_periods:
        warnings.append({
            'severity': 'error',
            'entryID': None,
            'message': 'At least one teaching period is required before publishing.',
        })
    for day in timetable.workingDays or []:
        for period in active_periods:
            if (day, period.id) not in own_entries:
                warnings.append({
                    'severity': 'warning',
                    'entryID': None,
                    **_warning_slot_payload(day=day, period=period),
                    'message': f'{day}, {_timetable_period_label(period)} is empty.',
                })

    seen = set()
    unique_warnings = []
    for item in warnings:
        key = (item['severity'], item['message'])
        if key not in seen:
            unique_warnings.append(item)
            seen.add(key)
    return unique_warnings


@login_required
def get_school_timetable_meta_api(request):
    current_session_id = _current_session_id(request)
    standards = Standard.objects.filter(
        isDeleted=False,
        sessionID_id=current_session_id,
    ).order_by('name', 'section').values('id', 'name', 'section')
    teachers = TeacherDetail.objects.filter(
        isDeleted=False,
        isActive='Yes',
        sessionID_id=current_session_id,
    ).order_by('name').values('id', 'name', 'employeeCode')
    data = {
        'days': TIMETABLE_DAY_ORDER,
        'periodTypes': [
            {'ID': key, 'Name': value}
            for key, value in TIMETABLE_PERIOD_TYPES.items()
        ],
        'standards': [
            {'ID': row['id'], 'Name': f"{row['name']} - {row['section']}" if row['section'] else row['name']}
            for row in standards
        ],
        'teachers': [
            {'ID': row['id'], 'Name': f"{row.get('name') or 'N/A'} - {row.get('employeeCode') or 'N/A'}"}
            for row in teachers
        ],
    }
    return SuccessResponse('Timetable meta loaded.', data=data, extra={'color': 'success'}).to_json_response()


@login_required
def get_school_timetable_api(request):
    standard_id = _safe_int(request.GET.get('standard'))
    if not standard_id:
        return ErrorResponse('Please select a class.', extra={'color': 'red'}).to_json_response()
    try:
        timetable = _get_or_create_school_timetable(request, standard_id)
        subject_teachers = AssignSubjectsToTeacher.objects.select_related(
            'assignedSubjectID__subjectID',
            'teacherID',
        ).filter(
            isDeleted=False,
            assignedSubjectID__isDeleted=False,
            assignedSubjectID__standardID_id=standard_id,
            sessionID_id=_current_session_id(request),
            teacherID__isDeleted=False,
            teacherID__isActive='Yes',
        ).order_by('assignedSubjectID__subjectID__name', 'teacherID__name')
        periods = SchoolTimetablePeriod.objects.filter(
            timetableID=timetable,
            isDeleted=False,
        ).order_by('displayOrder', 'id')
        entries = SchoolTimetableEntry.objects.select_related(
            'assignedSubjectID__subjectID',
            'teacherID',
        ).filter(
            timetableID=timetable,
            isDeleted=False,
        )
        data = {
            'timetable': {
                'id': timetable.id,
                'name': timetable.name or '',
                'status': timetable.status,
                'workingDays': timetable.workingDays or [],
                'publishedOn': timetable.publishedOn.strftime('%d-%m-%Y %I:%M %p') if timetable.publishedOn else '',
            },
            'periods': [
                {
                    'id': item.id,
                    'name': item.name or '',
                    'startTime': item.startTime.strftime('%H:%M') if item.startTime else '',
                    'endTime': item.endTime.strftime('%H:%M') if item.endTime else '',
                    'displayOrder': item.displayOrder,
                    'periodType': _normalized_period_type(item),
                    'isBreak': _normalized_period_type(item) != 'teaching',
                }
                for item in periods
            ],
            'subjectTeachers': [
                {
                    'ID': item.id,
                    'assignedSubjectID': item.assignedSubjectID_id,
                    'teacherID': item.teacherID_id,
                    'subjectName': item.assignedSubjectID.subjectID.name if item.assignedSubjectID and item.assignedSubjectID.subjectID else 'N/A',
                    'teacherName': item.teacherID.name if item.teacherID else 'N/A',
                    'Name': (
                        f'{item.assignedSubjectID.subjectID.name if item.assignedSubjectID and item.assignedSubjectID.subjectID else "N/A"}'
                        f' - {item.teacherID.name if item.teacherID else "N/A"}'
                    ),
                }
                for item in subject_teachers
            ],
            'entries': [_serialize_timetable_entry(item) for item in entries],
            'warnings': _build_timetable_warnings(timetable),
        }
        return SuccessResponse('Timetable loaded.', data=data, extra={'color': 'success'}).to_json_response()
    except ValidationError as exc:
        return ErrorResponse(str(exc), extra={'color': 'red'}).to_json_response()
    except Exception as exc:
        logger.error(f'Error loading timetable: {exc}')
        return ErrorResponse('Unable to load timetable.', extra={'color': 'red'}).to_json_response()


@transaction.atomic
@csrf_exempt
@login_required
def save_school_timetable_settings_api(request):
    if request.method != 'POST':
        return ErrorResponse('Method not allowed').to_json_response()
    try:
        standard_id = _safe_int(request.POST.get('standard'))
        working_days = json.loads(request.POST.get('workingDays') or '[]')
        period_rows = json.loads(request.POST.get('periods') or '[]')
        if not standard_id:
            return ErrorResponse('Please select a class.', extra={'color': 'red'}).to_json_response()
        if not working_days:
            return ErrorResponse('Select at least one working day.', extra={'color': 'red'}).to_json_response()
        _validate_timetable_period_rows(period_rows)
        timetable = _get_or_create_school_timetable(request, standard_id)
        timetable.workingDays = [day for day in TIMETABLE_DAY_ORDER if day in working_days]
        pre_save_with_user.send(sender=SchoolTimetable, instance=timetable, user=request.user.pk)
        timetable.save()

        keep_ids = []
        for index, row in enumerate(period_rows, start=1):
            name = (row.get('name') or f'Period {index}').strip()
            period_id = _safe_int(row.get('id'))
            period = None
            if period_id:
                period = SchoolTimetablePeriod.objects.filter(
                    pk=period_id,
                    timetableID=timetable,
                    isDeleted=False,
                ).first()
            if not period:
                period = SchoolTimetablePeriod(timetableID=timetable)
            period.name = name
            period.startTime = _parse_time_value(row.get('startTime'))
            period.endTime = _parse_time_value(row.get('endTime'))
            period.displayOrder = _safe_int(row.get('displayOrder')) or index
            period_type = (row.get('periodType') or '').strip()
            if period_type not in TIMETABLE_PERIOD_TYPES:
                period_type = 'break' if _truthy(row.get('isBreak')) else 'teaching'
            if period_type == 'teaching':
                period_type = _period_type_from_name(name, fallback=period_type)
            period.periodType = period_type
            period.isBreak = period_type != 'teaching'
            pre_save_with_user.send(sender=SchoolTimetablePeriod, instance=period, user=request.user.pk)
            period.save()
            if period.isBreak:
                SchoolTimetableEntry.objects.filter(
                    timetableID=timetable,
                    periodID=period,
                    isDeleted=False,
                ).update(isDeleted=True)
            keep_ids.append(period.id)

        stale_periods = SchoolTimetablePeriod.objects.filter(timetableID=timetable, isDeleted=False).exclude(id__in=keep_ids)
        for period in stale_periods:
            period.isDeleted = True
            pre_save_with_user.send(sender=SchoolTimetablePeriod, instance=period, user=request.user.pk)
            period.save()
            SchoolTimetableEntry.objects.filter(periodID=period, isDeleted=False).update(isDeleted=True)

        _mark_timetable_draft(timetable, request)
        logger.info(f'Timetable settings saved timetable={timetable.id} user={request.user.id}')
        return SuccessResponse('Timetable settings saved.', data={'warnings': _build_timetable_warnings(timetable)}, extra={'color': 'success'}).to_json_response()
    except ValidationError as exc:
        return ErrorResponse(str(exc), extra={'color': 'red'}).to_json_response()
    except Exception as exc:
        logger.error(f'Error saving timetable settings: {exc}')
        return ErrorResponse('Unable to save timetable settings.', extra={'color': 'red'}).to_json_response()


@transaction.atomic
@csrf_exempt
@login_required
def copy_school_timetable_from_class_api(request):
    if request.method != 'POST':
        return ErrorResponse('Method not allowed').to_json_response()
    try:
        source_standard_id = _safe_int(request.POST.get('sourceStandard'))
        target_standard_id = _safe_int(request.POST.get('targetStandard'))
        if not source_standard_id or not target_standard_id:
            return ErrorResponse('Source and target classes are required.', extra={'color': 'red'}).to_json_response()
        if source_standard_id == target_standard_id:
            return ErrorResponse('Choose a different source class.', extra={'color': 'red'}).to_json_response()

        current_session_id = _current_session_id(request)
        source_timetable = SchoolTimetable.objects.filter(
            standardID_id=source_standard_id,
            sessionID_id=current_session_id,
            isDeleted=False,
        ).order_by('-lastUpdatedOn', '-id').first()
        if not source_timetable:
            return ErrorResponse('Source class timetable was not found.', extra={'color': 'red'}).to_json_response()

        target_timetable = _get_or_create_school_timetable(request, target_standard_id)
        target_timetable.workingDays = source_timetable.workingDays or []
        target_timetable.name = target_timetable.name or f'{_class_label(target_timetable.standardID)} Timetable'
        pre_save_with_user.send(sender=SchoolTimetable, instance=target_timetable, user=request.user.pk)
        target_timetable.save()

        SchoolTimetableEntry.objects.filter(timetableID=target_timetable, isDeleted=False).update(isDeleted=True)
        for period in SchoolTimetablePeriod.objects.filter(timetableID=target_timetable, isDeleted=False):
            period.isDeleted = True
            pre_save_with_user.send(sender=SchoolTimetablePeriod, instance=period, user=request.user.pk)
            period.save()

        period_map = {}
        source_periods = SchoolTimetablePeriod.objects.filter(
            timetableID=source_timetable,
            isDeleted=False,
        ).order_by('displayOrder', 'id')
        for source_period in source_periods:
            new_period = SchoolTimetablePeriod(
                timetableID=target_timetable,
                name=source_period.name,
                startTime=source_period.startTime,
                endTime=source_period.endTime,
                displayOrder=source_period.displayOrder,
                periodType=source_period.periodType,
                isBreak=source_period.isBreak,
            )
            pre_save_with_user.send(sender=SchoolTimetablePeriod, instance=new_period, user=request.user.pk)
            new_period.save()
            period_map[source_period.id] = new_period

        copied = 0
        skipped = 0
        source_entries = SchoolTimetableEntry.objects.select_related(
            'assignedSubjectID__subjectID',
            'teacherID',
            'periodID',
        ).filter(timetableID=source_timetable, isDeleted=False)
        for source_entry in source_entries:
            target_period = period_map.get(source_entry.periodID_id)
            if not target_period or not _is_teaching_period(target_period):
                continue
            subject_id = source_entry.assignedSubjectID.subjectID_id if source_entry.assignedSubjectID_id else None
            target_assignment = AssignSubjectsToTeacher.objects.select_related('assignedSubjectID').filter(
                assignedSubjectID__standardID_id=target_standard_id,
                assignedSubjectID__subjectID_id=subject_id,
                assignedSubjectID__isDeleted=False,
                teacherID_id=source_entry.teacherID_id,
                teacherID__isDeleted=False,
                teacherID__isActive='Yes',
                sessionID_id=current_session_id,
                isDeleted=False,
            ).first()
            if not target_assignment:
                skipped += 1
                continue
            new_entry = SchoolTimetableEntry(
                timetableID=target_timetable,
                periodID=target_period,
                dayOfWeek=source_entry.dayOfWeek,
                assignedSubjectID_id=target_assignment.assignedSubjectID_id,
                teacherID_id=target_assignment.teacherID_id,
                room=source_entry.room,
                note=source_entry.note,
            )
            pre_save_with_user.send(sender=SchoolTimetableEntry, instance=new_entry, user=request.user.pk)
            new_entry.save()
            copied += 1

        _mark_timetable_draft(target_timetable, request)
        logger.info(
            f'Timetable copied source_standard={source_standard_id} target_standard={target_standard_id} '
            f'copied={copied} skipped={skipped} user={request.user.id}'
        )
        message = f'Timetable copied. {copied} slots copied'
        if skipped:
            message += f', {skipped} skipped because matching subject-teacher mapping was missing.'
        return SuccessResponse(
            message,
            data={'warnings': _build_timetable_warnings(target_timetable), 'copied': copied, 'skipped': skipped},
            extra={'color': 'success' if not skipped else 'orange'}
        ).to_json_response()
    except Exception as exc:
        logger.error(f'Error copying timetable from class: {exc}')
        return ErrorResponse('Unable to copy timetable.', extra={'color': 'red'}).to_json_response()


@transaction.atomic
@csrf_exempt
@login_required
def copy_school_timetable_day_api(request):
    if request.method != 'POST':
        return ErrorResponse('Method not allowed').to_json_response()
    try:
        standard_id = _safe_int(request.POST.get('standard'))
        source_day = (request.POST.get('sourceDay') or '').strip()
        target_days = json.loads(request.POST.get('targetDays') or '[]')
        if not standard_id or source_day not in TIMETABLE_DAY_ORDER:
            return ErrorResponse('Class and source day are required.', extra={'color': 'red'}).to_json_response()
        timetable = _get_or_create_school_timetable(request, standard_id)
        working_days = timetable.workingDays or []
        target_days = [day for day in target_days if day in working_days and day != source_day]
        if source_day not in working_days:
            return ErrorResponse('Source day is not enabled for this timetable.', extra={'color': 'red'}).to_json_response()
        if not target_days:
            return ErrorResponse('Select at least one target day.', extra={'color': 'red'}).to_json_response()

        source_entries = list(SchoolTimetableEntry.objects.select_related('periodID').filter(
            timetableID=timetable,
            dayOfWeek=source_day,
            isDeleted=False,
            periodID__isDeleted=False,
        ))
        source_entries = [entry for entry in source_entries if _is_teaching_period(entry.periodID)]
        if not source_entries:
            return ErrorResponse('Source day has no teaching slots to copy.', extra={'color': 'red'}).to_json_response()

        copied = 0
        for target_day in target_days:
            SchoolTimetableEntry.objects.filter(
                timetableID=timetable,
                dayOfWeek=target_day,
                periodID_id__in=[entry.periodID_id for entry in source_entries],
                isDeleted=False,
            ).update(isDeleted=True)
            for source_entry in source_entries:
                new_entry = SchoolTimetableEntry(
                    timetableID=timetable,
                    periodID_id=source_entry.periodID_id,
                    dayOfWeek=target_day,
                    assignedSubjectID_id=source_entry.assignedSubjectID_id,
                    teacherID_id=source_entry.teacherID_id,
                    room=source_entry.room,
                    note=source_entry.note,
                )
                pre_save_with_user.send(sender=SchoolTimetableEntry, instance=new_entry, user=request.user.pk)
                new_entry.save()
                copied += 1

        _mark_timetable_draft(timetable, request)
        logger.info(f'Timetable day copied timetable={timetable.id} source_day={source_day} targets={target_days} user={request.user.id}')
        return SuccessResponse(
            f'{source_day} copied to {len(target_days)} day(s).',
            data={'warnings': _build_timetable_warnings(timetable), 'copied': copied},
            extra={'color': 'success'}
        ).to_json_response()
    except Exception as exc:
        logger.error(f'Error copying timetable day: {exc}')
        return ErrorResponse('Unable to copy timetable day.', extra={'color': 'red'}).to_json_response()


@login_required
def get_teacher_school_timetable_api(request):
    teacher_id = _safe_int(request.GET.get('teacher'))
    if not teacher_id:
        return ErrorResponse('Please select a teacher.', extra={'color': 'red'}).to_json_response()
    try:
        entries = list(SchoolTimetableEntry.objects.select_related(
            'timetableID__standardID',
            'periodID',
            'assignedSubjectID__subjectID',
            'teacherID',
        ).filter(
            isDeleted=False,
            sessionID_id=_current_session_id(request),
            teacherID_id=teacher_id,
            timetableID__isDeleted=False,
        ))
        entries = [entry for entry in entries if _is_teaching_period(entry.periodID)]
        entries.sort(key=lambda item: (
            _day_sort_index(item.dayOfWeek),
            item.periodID.startTime if item.periodID and item.periodID.startTime else datetime.min.time(),
            item.periodID.displayOrder if item.periodID else 0,
        ))
        all_periods = list(SchoolTimetablePeriod.objects.select_related('timetableID').filter(
            isDeleted=False,
            sessionID_id=_current_session_id(request),
            timetableID__isDeleted=False,
        ).order_by('startTime', 'displayOrder', 'name', 'id'))
        period_rows = []
        seen_periods = set()
        for period in all_periods:
            if not _is_teaching_period(period):
                continue
            period_key = _timetable_period_key(period)
            if period_key in seen_periods:
                continue
            seen_periods.add(period_key)
            period_rows.append({
                'periodKey': period_key,
                'period': _timetable_period_label(period),
                'periodOrder': period.displayOrder,
                'periodStart': period.startTime.strftime('%H:%M') if period.startTime else '',
                'periodEnd': period.endTime.strftime('%H:%M') if period.endTime else '',
            })
        rows = []
        for entry in entries:
            standard = entry.timetableID.standardID if entry.timetableID else None
            rows.append({
                'dayOfWeek': entry.dayOfWeek,
                'periodKey': _timetable_period_key(entry.periodID),
                'periodID': entry.periodID_id,
                'period': _timetable_period_label(entry.periodID),
                'periodOrder': entry.periodID.displayOrder if entry.periodID else 0,
                'periodStart': entry.periodID.startTime.strftime('%H:%M') if entry.periodID and entry.periodID.startTime else '',
                'periodEnd': entry.periodID.endTime.strftime('%H:%M') if entry.periodID and entry.periodID.endTime else '',
                'className': _class_label(standard),
                'subjectName': entry.assignedSubjectID.subjectID.name if entry.assignedSubjectID and entry.assignedSubjectID.subjectID else 'N/A',
                'room': entry.room or '',
                'status': entry.timetableID.status if entry.timetableID else 'draft',
            })
        return SuccessResponse('Teacher timetable loaded.', data={'rows': rows, 'periods': period_rows}, extra={'color': 'success'}).to_json_response()
    except Exception as exc:
        logger.error(f'Error loading teacher timetable: {exc}')
        return ErrorResponse('Unable to load teacher timetable.', extra={'color': 'red'}).to_json_response()


@transaction.atomic
@csrf_exempt
@login_required
def save_school_timetable_entry_api(request):
    if request.method != 'POST':
        return ErrorResponse('Method not allowed').to_json_response()
    try:
        standard_id = _safe_int(request.POST.get('standard'))
        period_id = _safe_int(request.POST.get('periodID'))
        assigned_subject_id = _safe_int(request.POST.get('assignedSubjectID'))
        teacher_id = _safe_int(request.POST.get('teacherID'))
        day = (request.POST.get('dayOfWeek') or '').strip()
        room = (request.POST.get('room') or '').strip()
        note = (request.POST.get('note') or '').strip()
        if not standard_id or not period_id or day not in TIMETABLE_DAY_ORDER:
            return ErrorResponse('Class, day and period are required.', extra={'color': 'red'}).to_json_response()
        timetable = _get_or_create_school_timetable(request, standard_id)
        if day not in (timetable.workingDays or []):
            return ErrorResponse('Selected day is not enabled for this timetable.', extra={'color': 'red'}).to_json_response()
        period = SchoolTimetablePeriod.objects.filter(pk=period_id, timetableID=timetable, isDeleted=False).first()
        if not period:
            return ErrorResponse('Selected period was not found.', extra={'color': 'red'}).to_json_response()
        if not _is_teaching_period(period):
            SchoolTimetableEntry.objects.filter(
                timetableID=timetable,
                dayOfWeek=day,
                periodID=period,
                isDeleted=False,
            ).update(isDeleted=True)
            _mark_timetable_draft(timetable, request)
            logger.info(f'Ignored non-teaching timetable assignment timetable={timetable.id} period={period.id} user={request.user.id}')
            return SuccessResponse(
                'This is a non-teaching period. No teacher assignment is required.',
                data={'warnings': _build_timetable_warnings(timetable)},
                extra={'color': 'info'}
            ).to_json_response()

        if not assigned_subject_id and not teacher_id and not room and not note:
            existing = SchoolTimetableEntry.objects.filter(
                timetableID=timetable,
                dayOfWeek=day,
                periodID=period,
                isDeleted=False,
            ).first()
            if existing:
                existing.isDeleted = True
                pre_save_with_user.send(sender=SchoolTimetableEntry, instance=existing, user=request.user.pk)
                existing.save()
            _mark_timetable_draft(timetable, request)
            return SuccessResponse('Timetable slot cleared.', data={'warnings': _build_timetable_warnings(timetable)}, extra={'color': 'success'}).to_json_response()

        if assigned_subject_id or teacher_id:
            if not assigned_subject_id or not teacher_id:
                return ErrorResponse('Please select an assigned subject teacher.', extra={'color': 'red'}).to_json_response()
            valid_subject_teacher = AssignSubjectsToTeacher.objects.filter(
                assignedSubjectID_id=assigned_subject_id,
                assignedSubjectID__standardID_id=standard_id,
                assignedSubjectID__isDeleted=False,
                teacherID_id=teacher_id,
                teacherID__isDeleted=False,
                teacherID__isActive='Yes',
                sessionID_id=_current_session_id(request),
                isDeleted=False,
            ).exists()
            if not valid_subject_teacher:
                return ErrorResponse('Selected subject teacher is not assigned for this class.', extra={'color': 'red'}).to_json_response()

        entry = SchoolTimetableEntry.objects.filter(
            timetableID=timetable,
            dayOfWeek=day,
            periodID=period,
            isDeleted=False,
        ).first() or SchoolTimetableEntry(timetableID=timetable, dayOfWeek=day, periodID=period)
        entry.assignedSubjectID_id = assigned_subject_id
        entry.teacherID_id = teacher_id
        entry.room = room
        entry.note = note
        pre_save_with_user.send(sender=SchoolTimetableEntry, instance=entry, user=request.user.pk)
        entry.save()
        _mark_timetable_draft(timetable, request)
        warnings = _build_timetable_warnings(timetable)
        logger.info(f'Timetable entry saved timetable={timetable.id} entry={entry.id} user={request.user.id}')
        return SuccessResponse('Timetable slot saved.', data={'entry': _serialize_timetable_entry(entry), 'warnings': warnings}, extra={'color': 'success'}).to_json_response()
    except Exception as exc:
        logger.error(f'Error saving timetable entry: {exc}')
        return ErrorResponse('Unable to save timetable slot.', extra={'color': 'red'}).to_json_response()


@login_required
def validate_school_timetable_api(request):
    standard_id = _safe_int(request.GET.get('standard'))
    if not standard_id:
        return ErrorResponse('Please select a class.', extra={'color': 'red'}).to_json_response()
    try:
        timetable = _get_or_create_school_timetable(request, standard_id)
        warnings = _build_timetable_warnings(timetable)
        return SuccessResponse('Timetable validation completed.', data={'warnings': warnings}, extra={'color': 'success'}).to_json_response()
    except Exception as exc:
        logger.error(f'Error validating timetable: {exc}')
        return ErrorResponse('Unable to validate timetable.', extra={'color': 'red'}).to_json_response()


@transaction.atomic
@csrf_exempt
@login_required
def publish_school_timetable_api(request):
    if request.method != 'POST':
        return ErrorResponse('Method not allowed').to_json_response()
    standard_id = _safe_int(request.POST.get('standard'))
    if not standard_id:
        return ErrorResponse('Please select a class.', extra={'color': 'red'}).to_json_response()
    try:
        timetable = _get_or_create_school_timetable(request, standard_id)
        warnings = _build_timetable_warnings(timetable)
        blocking = [item for item in warnings if item.get('severity') == 'error']
        if blocking:
            logger.info(f'Timetable publish blocked timetable={timetable.id} conflicts={len(blocking)} user={request.user.id}')
            return ErrorResponse('Resolve conflict errors before publishing.', data={'warnings': warnings}, extra={'color': 'red'}).to_json_response()
        timetable.status = 'published'
        timetable.publishedOn = timezone.now()
        timetable.publishedByUserID = request.user
        pre_save_with_user.send(sender=SchoolTimetable, instance=timetable, user=request.user.pk)
        timetable.save()
        logger.info(f'Timetable published timetable={timetable.id} user={request.user.id}')
        return SuccessResponse('Timetable published successfully.', data={'warnings': warnings}, extra={'color': 'success'}).to_json_response()
    except Exception as exc:
        logger.error(f'Error publishing timetable: {exc}')
        return ErrorResponse('Unable to publish timetable.', extra={'color': 'red'}).to_json_response()


@transaction.atomic
@csrf_exempt
@login_required
def unpublish_school_timetable_api(request):
    if request.method != 'POST':
        return ErrorResponse('Method not allowed').to_json_response()
    standard_id = _safe_int(request.POST.get('standard'))
    if not standard_id:
        return ErrorResponse('Please select a class.', extra={'color': 'red'}).to_json_response()
    try:
        timetable = _get_or_create_school_timetable(request, standard_id)
        if timetable.status == 'draft':
            return SuccessResponse('Timetable is already in draft.', data={'warnings': _build_timetable_warnings(timetable)}, extra={'color': 'info'}).to_json_response()
        timetable.status = 'draft'
        timetable.publishedOn = None
        timetable.publishedByUserID = None
        pre_save_with_user.send(sender=SchoolTimetable, instance=timetable, user=request.user.pk)
        timetable.save()
        warnings = _build_timetable_warnings(timetable)
        logger.info(f'Timetable unpublished timetable={timetable.id} user={request.user.id}')
        return SuccessResponse('Timetable reverted to draft.', data={'warnings': warnings}, extra={'color': 'success'}).to_json_response()
    except Exception as exc:
        logger.error(f'Error unpublishing timetable: {exc}')
        return ErrorResponse('Unable to revert timetable to draft.', extra={'color': 'red'}).to_json_response()


# Assign Subjects To Teacher --------------------------------------------------------------
@transaction.atomic
@csrf_exempt
@login_required
def add_subject_to_teacher(request):
    if request.method == 'POST':
        try:
            standard = request.POST.get("standard")
            teachers = request.POST.get("teachers")
            branch = request.POST.get("branch")

            try:
                AssignSubjectsToTeacher.objects.get(assignedSubjectID_id=int(standard), teacherID_id=int(teachers),
                                                    subjectBranch__iexact=branch, isDeleted=False,
                                                    sessionID_id=request.session['current_session']['Id'])
                return _api_response(
                    {'status': 'success', 'message': 'Subject already assigned successfully.', 'color': 'info'},
                    safe=False)
            except:
                instance = AssignSubjectsToTeacher()
                instance.teacherID_id = int(teachers)
                instance.assignedSubjectID_id = int(standard)
                instance.subjectBranch = branch
                pre_save_with_user.send(sender=AssignSubjectsToTeacher, instance=instance, user=request.user.pk)
                instance.save()
            return _api_response(
                {'status': 'success', 'message': 'Subject assigned successfully.', 'color': 'success'},
                safe=False)
        except:
            return _api_response({'status': 'error'}, safe=False)


class AssignSubjectToTeacherListJson(BaseDatatableView):
    order_columns = ['assignedSubjectID.standardID.name', 'assignedSubjectID.standardID.section',
                     'assignedSubjectID.subjectID.name', 'teacherID.name', 'subjectBranch', 'lastEditedBy', 'lastUpdatedOn']

    def get_initial_queryset(self):
        return AssignSubjectsToTeacher.objects.select_related(
            'assignedSubjectID__standardID', 'assignedSubjectID__subjectID', 'teacherID'
        ).only(
            'id', 'subjectBranch', 'lastEditedBy', 'lastUpdatedOn',
            'assignedSubjectID__standardID__name', 'assignedSubjectID__standardID__section',
            'assignedSubjectID__subjectID__name', 'teacherID__name'
        ).filter(
            isDeleted__exact=False,
            sessionID_id=self.request.session["current_session"]["Id"]
        ).order_by(
            'assignedSubjectID__standardID__name')

    def filter_queryset(self, qs):

        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(assignedSubjectID__standardID__name__icontains=search) | Q(teacherID__name__icontains=search) | Q(
                    teacherID__employeeCode__icontains=search)
                | Q(assignedSubjectID__subjectID__name__icontains=search) | Q(
                    assignedSubjectID__standardID__section__icontains=search) | Q(
                    subjectBranch__icontains=search)
                | Q(lastEditedBy__icontains=search) | Q(lastUpdatedOn__icontains=search)
            )

        return qs

    def prepare_results(self, qs):
        json_data = []
        for item in qs:
            action = '''
              <button data-inverted="" data-tooltip="Edit Detail" data-position="left center" data-variation="mini" style="font-size:10px;" onclick = "GetDataDetails('{}')" class="ui circular facebook icon button green">
                <i class="pen icon"></i>
              </button>
              <button data-inverted="" data-tooltip="Delete" data-position="left center" data-variation="mini" style="font-size:10px;" onclick ="delData('{}')" class="ui circular youtube icon button" style="margin-left: 3px">
                <i class="trash alternate icon"></i>
              </button></td>'''.format(item.pk, item.pk),
            if item.assignedSubjectID.standardID.section:
                section = item.assignedSubjectID.standardID.section
            else:
                section = 'N/A'

            json_data.append([
                escape(item.assignedSubjectID.standardID.name),
                escape(section),
                escape(item.assignedSubjectID.subjectID.name),
                escape(item.teacherID.name),
                escape(item.subjectBranch),
                escape(item.lastEditedBy or 'N/A'),
                escape(item.lastUpdatedOn.strftime('%d-%m-%Y %I:%M %p') if item.lastUpdatedOn else 'N/A'),
                action,

            ])

        return json_data


class AssignSubjectToClassListJson(BaseDatatableView):
    order_columns = ['standardID.name', 'subjectID.name', 'lastEditedBy', 'lastUpdatedOn']

    def get_initial_queryset(self):
        return AssignSubjectsToClass.objects.select_related(
            'standardID', 'subjectID'
        ).only(
            'id', 'lastEditedBy', 'lastUpdatedOn',
            'standardID__name', 'standardID__section',
            'subjectID__name'
        ).filter(
            isDeleted__exact=False,
            sessionID_id=self.request.session["current_session"]["Id"]
        ).order_by('standardID__name')

    def filter_queryset(self, qs):
        class_filter = self.request.GET.get('class_filter')
        subject_filter = self.request.GET.get('subject_filter')

        if class_filter and str(class_filter).isdigit():
            qs = qs.filter(standardID_id=int(class_filter))

        if subject_filter and str(subject_filter).isdigit():
            qs = qs.filter(subjectID_id=int(subject_filter))

        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(standardID__name__icontains=search)
                | Q(subjectID__name__icontains=search) | Q(standardID__section__icontains=search)
                | Q(lastEditedBy__icontains=search) | Q(lastUpdatedOn__icontains=search)
            )

        return qs

    def prepare_results(self, qs):
        json_data = []
        for item in qs:
            action = '''
              <button data-inverted="" data-tooltip="Edit Detail" data-position="left center" data-variation="mini" style="font-size:10px;" onclick = "GetDataDetails('{}')" class="ui circular facebook icon button green">
                <i class="pen icon"></i>
              </button>
              <button data-inverted="" data-tooltip="Delete" data-position="left center" data-variation="mini" style="font-size:10px;" onclick ="delData('{}')" class="ui circular youtube icon button" style="margin-left: 3px">
                <i class="trash alternate icon"></i>
              </button></td>'''.format(item.pk, item.pk),
            if item.standardID.section:
                name = item.standardID.name + ' - ' + item.standardID.section
            else:
                name = item.standardID.name

            json_data.append([
                escape(name),
                escape(item.subjectID.name),
                escape(item.lastEditedBy or 'N/A'),
                escape(item.lastUpdatedOn.strftime('%d-%m-%Y %I:%M %p') if item.lastUpdatedOn else 'N/A'),
                action,

            ])

        return json_data


@transaction.atomic
@csrf_exempt
@login_required
def delete_assign_teacher_to_subject(request):
    if request.method == 'POST':
        try:
            id = request.POST.get("dataID")
            instance = AssignSubjectsToTeacher.objects.get(pk=int(id), isDeleted=False,
                                                           sessionID_id=request.session['current_session']['Id'])
            instance.isDeleted = True
            pre_save_with_user.send(sender=AssignSubjectsToTeacher, instance=instance, user=request.user.pk)
            instance.save()
            return _api_response(
                {'status': 'success', 'message': 'Assigned Teacher detail deleted successfully.',
                 'color': 'success'}, safe=False)
        except:
            return _api_response({'status': 'error'}, safe=False)
    return _api_response({'status': 'error'}, safe=False)


@login_required
def get_assigned_subject_to_teacher_detail(request, **kwargs):
    try:
        id = request.GET.get('id')
        obj = AssignSubjectsToTeacher.objects.get(pk=id, isDeleted=False,
                                                  sessionID_id=request.session['current_session']['Id'])
        obj_dic = {
            'StandardID': obj.assignedSubjectID_id,
            'teacherID': obj.teacherID_id,
            'branch': obj.subjectBranch,
            'ID': obj.pk,
        }
        return _api_response({'status': 'success', 'data': obj_dic}, safe=False)
    except:
        return _api_response({'status': 'error'}, safe=False)


@transaction.atomic
@csrf_exempt
@login_required
def update_subject_to_teacher(request):
    if request.method == 'POST':
        try:
            editID = request.POST.get("editID")
            standard = request.POST.get("standard")
            teachers = request.POST.get("teachers")
            branch = request.POST.get("branch")

            instance = AssignSubjectsToTeacher.objects.get(pk=int(editID))

            try:
                AssignSubjectsToTeacher.objects.get(subjectBranch__iexact=branch,
                                                    assignedSubjectID_id=instance.assignedSubjectID_id,
                                                    isDeleted=False,
                                                    sessionID_id=request.session['current_session']['Id']).exclude(
                    pk=int(editID))
                return _api_response(
                    {'status': 'success', 'message': 'Detail already assigned.', 'color': 'info'},
                    safe=False)
            except:
                # instance = AssignSubjectsToClass()
                instance.standardID_id = int(standard)
                instance.teacherID_id = int(teachers)
                instance.subjectBranch = branch
                pre_save_with_user.send(sender=AssignSubjectsToTeacher, instance=instance, user=request.user.pk)
                instance.save()
                return _api_response(
                    {'status': 'success', 'message': 'Detail updated successfully.', 'color': 'success'},
                    safe=False)
        except:
            return _api_response({'status': 'error'}, safe=False)


# Teachers ---------------------------------------------------------------------------------------

@transaction.atomic
@csrf_exempt
@login_required
def add_teacher_api(request):
    if request.method == 'POST':
        name = request.POST.get("name")
        email = request.POST.get("email")
        bloodGroup = request.POST.get("bloodGroup")
        gender = request.POST.get("gender")
        phone = request.POST.get("phone")
        dob = request.POST.get("dob")
        aadhar = request.POST.get("aadhar")
        qualification = request.POST.get("qualification")
        imageUpload = request.FILES["imageUpload"]
        address = request.POST.get("address")
        city = request.POST.get("city")
        state = request.POST.get("state")
        country = request.POST.get("country")
        pincode = request.POST.get("pincode")
        addressP = request.POST.get("addressP")
        cityP = request.POST.get("cityP")
        stateP = request.POST.get("stateP")
        countryP = request.POST.get("countryP")
        pincodeP = request.POST.get("pincodeP")
        empCode = request.POST.get("empCode")
        staffType = request.POST.get("staffType")
        doj = request.POST.get("doj")

        try:
            TeacherDetail.objects.get(phoneNumber__iexact=phone, isDeleted=False,
                                      sessionID_id=request.session['current_session']['Id'])
            return _api_response(
                {'status': 'success', 'message': 'Teacher already exists. Please change the name.', 'color': 'info'},
                safe=False)
        except:
            instance = TeacherDetail()
            instance.name = name
            instance.email = email
            instance.bloodGroup = bloodGroup
            instance.gender = gender
            instance.dob = datetime.strptime(dob, '%d/%m/%Y')
            instance.dateOfJoining = datetime.strptime(doj, '%d/%m/%Y')
            instance.phoneNumber = phone
            instance.aadhar = aadhar
            instance.qualification = qualification
            instance.photo = optimize_uploaded_image(imageUpload)
            instance.presentAddress = address
            instance.presentCity = city
            instance.presentState = state
            instance.presentCountry = country
            instance.presentPinCode = pincode
            instance.permanentAddress = addressP
            instance.permanentCity = cityP
            instance.permanentState = stateP
            instance.permanentCountry = countryP
            instance.permanentPinCode = pincodeP
            instance.employeeCode = empCode
            instance.staffType = staffType

            username = 'T' + get_random_string(length=5, allowed_chars='1234567890')
            password = get_random_string(length=8, allowed_chars='1234567890')
            while User.objects.filter(username__exact=username).exists():
                username = 'T' + get_random_string(length=5, allowed_chars='1234567890')
            else:
                new_user = User()
                new_user.username = username
                new_user.set_password(password)

                new_user.save()
                instance.username = username
                instance.password = password
                instance.userID_id = new_user.pk

                instance.save()

                # Handle group assignment more efficiently
            try:
                group, created = Group.objects.get_or_create(name=staffType)
                if created:
                    logger.info(f"Created new group: {staffType}")
                
                # Only add user to group if not already a member
                if not group.user_set.filter(id=new_user.pk).exists():
                    group.user_set.add(new_user.pk)
                    logger.info(f"Added user {new_user.pk} to group {staffType}")
            except Exception as e:
                logger.error(f"Error handling group assignment: {e}")
                # Continue with teacher update even if group assignment fails
            pre_save_with_user.send(sender=TeacherDetail, instance=instance, user=request.user.pk)
            instance.save()
            return _api_response(
                {'status': 'success', 'message': 'New Teacher added successfully.', 'color': 'success'},
                safe=False)
    return _api_response({'status': 'error'}, safe=False)

@transaction.atomic
@csrf_exempt
@login_required
def update_teacher_api(request):
    if request.method == 'POST':
        name = request.POST.get("name")
        email = request.POST.get("email")
        bloodGroup = request.POST.get("bloodGroup")
        gender = request.POST.get("gender")
        phone = request.POST.get("phone")
        dob = request.POST.get("dob")
        aadhar = request.POST.get("aadhar")
        qualification = request.POST.get("qualification")
        imageUpload = request.FILES.get("imageUpload")
        address = request.POST.get("address")
        city = request.POST.get("city")
        state = request.POST.get("state")
        country = request.POST.get("country")
        pincode = request.POST.get("pincode")
        addressP = request.POST.get("addressP")
        cityP = request.POST.get("cityP")
        stateP = request.POST.get("stateP")
        countryP = request.POST.get("countryP")
        pincodeP = request.POST.get("pincodeP")
        empCode = request.POST.get("empCode")
        staffType = request.POST.get("staffType")
        doj = request.POST.get("doj")
        salary = request.POST.get("salary")
        additionalDetails = request.POST.get("additionalDetails")
        id = request.POST.get("id")

        try:
            instance = TeacherDetail.objects.get(id=id, isDeleted=False,
                                      sessionID_id=request.session['current_session']['Id'])
            instance.name = name
            instance.email = email
            instance.bloodGroup = bloodGroup
            instance.gender = gender
            instance.dob = datetime.strptime(dob, '%d/%m/%Y')
            instance.dateOfJoining = datetime.strptime(doj, '%d/%m/%Y')
            instance.phoneNumber = phone
            instance.aadhar = aadhar
            instance.qualification = qualification
            if imageUpload:
                instance.photo = optimize_uploaded_image(imageUpload)
            instance.presentAddress = address
            instance.presentCity = city
            instance.presentState = state
            instance.presentCountry = country
            instance.presentPinCode = pincode
            instance.permanentAddress = addressP
            instance.permanentCity = cityP
            instance.permanentState = stateP
            instance.permanentCountry = countryP
            instance.permanentPinCode = pincodeP
            instance.employeeCode = empCode
            instance.staffType = staffType
            instance.salary = salary
            instance.additionalDetails = additionalDetails

            user = User.objects.get(id=instance.userID_id)

            # Handle group assignment more efficiently
            try:
                group, created = Group.objects.get_or_create(name=staffType)
                if created:
                    logger.info(f"Created new group: {staffType}")
                
                # Only add user to group if not already a member
                if not group.user_set.filter(id=user.pk).exists():
                    group.user_set.add(user.pk)
                    logger.info(f"Added user {user.pk} to group {staffType}")
            except Exception as e:
                logger.error(f"Error handling group assignment: {e}")
                # Continue with teacher update even if group assignment fails
            pre_save_with_user.send(sender=TeacherDetail, instance=instance, user=request.user.pk)
            instance.save()
            logger.info("Teacher details updated successfully.")
            return SuccessResponse(
                    'Teacher details updated successfully.'
                    ).to_json_response()
        except TeacherDetail.DoesNotExist:
            logger.info("Teacher details not found.")
            return ErrorResponse(
                    'Teacher details not found.'
                    ).to_json_response()  
        except Exception as e:
            logger.error("Error updating teacher details: " + str(e))
            return ErrorResponse(
                    str(e)
                    ).to_json_response()          
    return ErrorResponse(
            'Invalid request.'
            ).to_json_response()



class TeacherListJson(BaseDatatableView):
    order_columns = ['photo', 'name', 'email', 'phoneNumber', 'employeeCode', 'gender', 'staffType', 'presentCity',
                     'isActive', 'lastEditedBy', 'lastUpdatedOn']

    def get_initial_queryset(self):
        return TeacherDetail.objects.only(
            'id', 'photo', 'name', 'email', 'phoneNumber', 'employeeCode', 'gender',
            'staffType', 'presentCity', 'isActive', 'lastEditedBy', 'datetime', 'lastUpdatedOn'
        ).filter(
            isDeleted__exact=False,
            sessionID_id=self.request.session["current_session"]["Id"]
        )

    def filter_queryset(self, qs):

        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(name__icontains=search)
                | Q(email__icontains=search)
                | Q(phoneNumber__icontains=search)
                | Q(employeeCode__icontains=search)
                | Q(gender__icontains=search)
                | Q(staffType__icontains=search) | Q(presentAddress__icontains=search)
                | Q(presentCity__icontains=search) | Q(isActive__icontains=search)
                | Q(lastEditedBy__icontains=search) | Q(lastUpdatedOn__icontains=search)
            )

        return qs

    def prepare_results(self, qs):
        json_data = []
        for item in qs:
            images = _avatar_image_html(item.photo)

            action = '''<a href="/management/teacher_detail/{}/" data-inverted="" data-tooltip="View Detail" data-position="left center" data-variation="mini" style="font-size:10px;" onclick = "GetDataDetails('{}')" class="ui circular facebook icon button purple">
                <i class="eye icon"></i>
              </a>
            <a href="/management/edit_teacher/{}/" data-inverted="" data-tooltip="Edit Detail" data-position="left center" data-variation="mini" style="font-size:10px;" onclick = "GetDataDetails('{}')" class="ui circular facebook icon button green">
                <i class="pen icon"></i>
              </a>
              <button data-inverted="" data-tooltip="Delete" data-position="left center" data-variation="mini" style="font-size:10px;" onclick ="delData('{}')" class="ui circular youtube icon button" style="margin-left: 3px">
                <i class="trash alternate icon"></i>
              </button></td>'''.format(item.pk,item.pk, item.pk, item.pk, item.pk),

            json_data.append([
                images,
                escape(item.name),
                escape(item.email),
                escape(item.phoneNumber),
                escape(item.employeeCode),
                escape(item.gender),
                escape(item.staffType),
                escape(item.presentCity),
                escape(item.isActive),
                escape(item.lastEditedBy or 'N/A'),
                escape(item.lastUpdatedOn.strftime('%d-%m-%Y %I:%M %p') if item.lastUpdatedOn else 'N/A'),
                action,

            ])

        return json_data


@transaction.atomic
@csrf_exempt
@login_required
def delete_teacher(request):
    if request.method == 'POST':
        try:
            id = request.POST.get("dataID")
            instance = TeacherDetail.objects.get(pk=int(id), isDeleted=False,
                                                 sessionID_id=request.session['current_session']['Id'])
            instance.isDeleted = True
            instance.isActive = 'No'
            user = User.objects.get(pk=instance.userID_id)
            user.is_active = False
            user.save()
            pre_save_with_user.send(sender=TeacherDetail, instance=instance, user=request.user.pk)
            instance.save()
            return _api_response(
                {'status': 'success', 'message': 'Teacher/Staff detail deleted successfully.',
                 'color': 'success'}, safe=False)
        except:
            return _api_response({'status': 'error'}, safe=False)
    return _api_response({'status': 'error'}, safe=False)


@login_required
def get_teacher_list_api(request):
    rows = TeacherDetail.objects.filter(
        isDeleted=False,
        sessionID_id=_current_session_id(request),
        isActive='Yes'
    ).values('id', 'name', 'employeeCode').order_by('name')
    data = [
        {'ID': row['id'], 'Name': f"{row.get('name') or 'N/A'} - {row.get('employeeCode') or 'N/A'}"}
        for row in rows
    ]
    return _api_response(
        {'status': 'success', 'data': data,
         'color': 'success'}, safe=False)


# student api --------------------------------------------------------------------------
@login_required
def import_student_from_previous_session_api(request):
    registration_code = (request.GET.get('registration') or request.GET.get('registrationCode') or '').strip()
    if not registration_code:
        return ErrorResponse('Registration code is required.', extra={'color': 'red'}).to_json_response()

    previous_session = _previous_school_session_for_current(request)
    if not previous_session:
        return ErrorResponse('Previous session was not found for the current school.', extra={'color': 'red'}).to_json_response()

    student = Student.objects.select_related('parentID', 'standardID').filter(
        sessionID_id=previous_session.id,
        isDeleted=False,
        registrationCode__iexact=registration_code,
    ).order_by('-id').first()
    if not student:
        return ErrorResponse('Student not found in previous session for this registration code.', extra={'color': 'red'}).to_json_response()

    parent = student.parentID
    matched_standard_id = _matching_current_standard_id(student.standardID, _current_session_id(request))
    current_standard = Standard.objects.filter(pk=matched_standard_id, isDeleted=False).first() if matched_standard_id else None

    data = {
        'student': {
            'name': student.name or '',
            'email': student.email or '',
            'phone': student.phoneNumber or '',
            'bloodGroup': student.bloodGroup or '',
            'gender': student.gender or '',
            'dob': student.dob.strftime('%d/%m/%Y') if student.dob else '',
            'aadhar': student.aadhar or '',
            'idMark': student.idMark or '',
            'penNumber': student.penNumber or '',
            'caste': student.caste or '',
            'tribe': student.tribe or '',
            'religion': student.religion or '',
            'motherTongue': student.motherTongue or '',
            'otherLanguages': student.otherLanguages or '',
            'hobbies': student.hobbies or '',
            'aimInLife': student.aimInLife or '',
            'milOptions': student.milOption or '',
            'familyCode': student.familyCode or '',
            'siblings': student.siblingsCount or 0,
            'registrationCode': student.registrationCode or '',
            'roll': student.roll or '',
            'doj': student.dateOfJoining.strftime('%d/%m/%Y') if student.dateOfJoining else '',
            'previousSchoolName': student.lastSchoolName or '',
            'previousSchoolAddress': student.lastSchoolAddress or '',
            'previousSchoolClass': student.lastClass or '',
            'previousSchoolResult': (student.lastResult or '').title() if student.lastResult else '',
            'previousSchoolDivision': student.lastDivision or '',
            'previousSchoolRollNumber': student.lastRollNo or '',
            'admissionFee': student.admissionFee or 0,
            'tuitionFee': student.tuitionFee or 0,
            'miscFee': student.miscFee or 0,
            'totalFee': student.totalFee or 0,
            'standard': matched_standard_id or '',
            'previousSessionClass': f'{student.standardID.name}{(" - " + student.standardID.section) if student.standardID and student.standardID.section else ""}' if student.standardID else '',
            'matchedCurrentClass': f'{current_standard.name}{(" - " + current_standard.section) if current_standard and current_standard.section else ""}' if current_standard else '',
            'photoUrl': student.photo.url if student.photo else '',
        },
        'parent': {
            'familyType': parent.familyType if parent else '',
            'numberOfMembers': parent.totalFamilyMembers if parent and parent.totalFamilyMembers is not None else 0,
            'familyAnnualIncome': parent.annualIncome if parent and parent.annualIncome is not None else 0,
            'fname': parent.fatherName if parent else '',
            'FatherOccupation': parent.fatherOccupation if parent else '',
            'fatherContactNumber': parent.fatherPhone if parent else '',
            'FatherAddress': parent.fatherAddress if parent else '',
            'FatherEmail': parent.fatherEmail if parent else '',
            'mname': parent.motherName if parent else '',
            'MotherOccupation': parent.motherOccupation if parent else '',
            'MotherContactNumber': parent.motherPhone if parent else '',
            'MotherAddress': parent.motherAddress if parent else '',
            'MotherEmail': parent.motherEmail if parent else '',
            'guardianName': parent.guardianName if parent else '',
            'guardianOccupation': parent.guardianOccupation if parent else '',
            'guardianPhoneNumber': parent.guardianPhone if parent else '',
            'parentsPhoneNumber': parent.phoneNumber if parent else '',
        },
        'meta': {
            'previousStudentId': student.id,
            'previousSessionId': previous_session.id,
            'previousSessionYear': previous_session.sessionYear or '',
            'currentClassMatched': bool(matched_standard_id),
        }
    }
    return SuccessResponse('Student details loaded from previous session.', data=data, extra={'color': 'green'}).to_json_response()


@login_required
def student_import_registration_suggestions_api(request):
    query = (request.GET.get('q') or request.GET.get('registration') or '').strip()
    if len(query) < 2:
        return SuccessResponse('Suggestions loaded.', data=[]).to_json_response()

    previous_session = _previous_school_session_for_current(request)
    if not previous_session:
        return SuccessResponse('Suggestions loaded.', data=[]).to_json_response()

    rows = Student.objects.filter(
        sessionID_id=previous_session.id,
        isDeleted=False,
    ).filter(
        Q(registrationCode__icontains=query) | Q(name__icontains=query)
    ).select_related('standardID').order_by('registrationCode', 'name')[:10]

    data = []
    for row in rows:
        class_label = 'N/A'
        if row.standardID:
            class_label = row.standardID.name or 'N/A'
            if row.standardID.section:
                class_label = f'{class_label} - {row.standardID.section}'
        data.append({
            'registrationCode': row.registrationCode or '',
            'name': row.name or 'N/A',
            'sessionYear': previous_session.sessionYear or '',
            'classLabel': class_label,
            'roll': row.roll or '',
        })

    return SuccessResponse('Suggestions loaded.', data=data).to_json_response()


@transaction.atomic
@csrf_exempt
@login_required
def add_student_api(request):

    if request.method != 'POST':
        return ErrorResponse("Method not allowed").to_json_response()

    post_data = request.POST.dict()
    files_data = request.FILES
    current_session_id = request.session['current_session']['Id']
    imported_previous_student_id = post_data.get('importedPreviousStudentID')
    previous_session = _previous_school_session_for_current(request)
    imported_previous_student = None
    if imported_previous_student_id and previous_session:
        imported_previous_student = Student.objects.select_related('userID').filter(
            pk=imported_previous_student_id,
            sessionID_id=previous_session.id,
            isDeleted=False,
        ).first()


    # ---------- PARENT ----------
    parent_obj = _get_or_create_current_session_parent(request, post_data)


    # ---------- STUDENT EXIST CHECK ----------
    if Student.objects.filter(
        registrationCode__iexact = post_data.get("registrationCode"),
        sessionID_id = current_session_id,
        isDeleted = False
    ).exists():
        return _api_response(
            {'status': 'success', 'message': 'Student already exists.', 'color': 'info'},
            safe=False
        )

    if imported_previous_student and imported_previous_student.userID_id:
        if Student.objects.filter(
            sessionID_id=current_session_id,
            userID_id=imported_previous_student.userID_id,
            isDeleted=False,
        ).exists():
            return _api_response(
                {'status': 'success', 'message': 'Imported student login already exists in current session.', 'color': 'info'},
                safe=False
            )



    # ---------- STUDENT CREATION ----------
    student_obj = Student.objects.create(
        registrationCode = post_data.get("registrationCode"),
        name = post_data.get("name"),
        email = post_data.get("email"),
        phoneNumber = post_data.get("phone"),
        bloodGroup = post_data.get("bloodGroup"),
        gender = post_data.get("gender"),
        aadhar = post_data.get("aadhar"),
        idMark = post_data.get("idMark"),
        penNumber = post_data.get("penNumber"),
        caste = post_data.get("caste"),
        tribe = post_data.get("tribe"),
        religion = post_data.get("religion"),
        motherTongue = post_data.get("motherTongue"),
        otherLanguages = post_data.get("otherLanguages"),
        hobbies = post_data.get("hobbies"),
        aimInLife = post_data.get("aimInLife"),
        milOption = post_data.get("milOptions"),

        familyCode = post_data.get("familyCode"),
        siblingsCount = int(post_data.get("siblings")) if post_data.get("siblings") else 0,
        roll = post_data.get("roll"),

        # Previous School
        lastSchoolName = post_data.get("previousSchoolName"),
        lastSchoolAddress = post_data.get("previousSchoolAddress"),
        lastClass = post_data.get("previousSchoolClass"),
        lastResult = post_data.get("previousSchoolResult"),
        lastDivision = post_data.get("previousSchoolDivision"),
        lastRollNo = post_data.get("previousSchoolRollNumber"),

        # Fees
        admissionFee = float(post_data.get("admissionFee")) if post_data.get("admissionFee") else 0,
        tuitionFee = float(post_data.get("tuitionFee")) if post_data.get("tuitionFee") else 0,
        miscFee = float(post_data.get("miscFee")) if post_data.get("miscFee") else 0,
        totalFee = float(post_data.get("totalFee")) if post_data.get("totalFee") else 0,

        # Foreign keys
        standardID_id = post_data.get("standard"),
        parentID = parent_obj,
        
        # schoolID_id = request.session['current_session']['SchoolID'],
        sessionID_id = current_session_id,

        isDeleted = False,
    )

    # ---------- DATE HANDLING ----------
    if post_data.get("dob"):
        student_obj.dob = datetime.strptime(post_data["dob"], "%d/%m/%Y")

    if post_data.get("doj"):
        student_obj.dateOfJoining = datetime.strptime(post_data["doj"], "%d/%m/%Y")

    # ---------- IMAGE ----------
    if "imageUpload" in files_data:
        student_obj.photo = optimize_uploaded_image(files_data["imageUpload"])
    elif imported_previous_student and imported_previous_student.photo:
        student_obj.photo = imported_previous_student.photo.name

    # ---------- USER ----------
    if imported_previous_student and imported_previous_student.userID_id:
        student_obj.username = imported_previous_student.username
        student_obj.password = imported_previous_student.password
        student_obj.userID = imported_previous_student.userID
        if student_obj.userID and not student_obj.userID.is_active:
            student_obj.userID.is_active = True
            student_obj.userID.save(update_fields=['is_active'])
        Group.objects.get_or_create(name="Student")[0].user_set.add(student_obj.userID)
    else:
        username = 'STU' + get_random_string(5, '1234567890')
        password = get_random_string(8, '1234567890')

        new_user = User.objects.create_user(username=username, password=password)
        student_obj.username = username
        student_obj.password = password
        student_obj.userID = new_user
        Group.objects.get_or_create(name="Student")[0].user_set.add(new_user)

    # ---------- FINAL SAVE ----------
    pre_save_with_user.send(sender=Student, instance=student_obj, user=request.user.pk)
    student_obj.save()

    try:
        _sync_admission_finance_for_student(request, student_obj, post_data)
    except Exception as exc:
        logger.error(f"Finance sync failed for add_student_api student={student_obj.id}: {exc}")

    return SuccessResponse(
        "New Student added successfully.",
        data={'status': 'success', 'message': 'New Student added successfully.', 'color': 'success'},
    ).to_json_response()

@login_required
def get_student_list_by_class_api(request):
    standard = request.GET.get('standard')
    rows = Student.objects.select_related('standardID').filter(
        isDeleted=False,
        sessionID_id=_current_session_id(request),
    )
    try:
        standard_id = int(standard)
    except (TypeError, ValueError):
        standard_id = None
    if standard_id:
        rows = rows.filter(standardID_id=standard_id)
    rows = rows.order_by('name', 'roll')
    data = []
    for row in rows:
        roll = row.roll
        try:
            roll_label = str(int(float(roll)))
        except Exception:
            roll_label = str(roll or 'N/A')
        class_label = 'N/A'
        if row.standardID:
            class_label = row.standardID.name or 'N/A'
            if row.standardID.section:
                class_label = f'{class_label} {row.standardID.section}'
        data.append({'ID': row.id, 'Name': f"{row.name or 'N/A'} - Roll {roll_label} - Class {class_label}"})
    return _api_response(
        {'status': 'success', 'data': data,
         'color': 'success'}, safe=False)


class StudentListJson(BaseDatatableView):
    order_columns = ['photo', 'name', 'standardID.name', 'gender', 'parentID.fatherName',
                     'parentID.phoneNumber', 'presentCity',
                     'isActive', 'lastEditedBy', 'lastUpdatedOn']

    def get_initial_queryset(self):
        return Student.objects.select_related('standardID', 'parentID').only(
            'id', 'photo', 'name', 'gender', 'presentCity', 'isActive', 'lastEditedBy', 'datetime', 'lastUpdatedOn',
            'standardID__name', 'standardID__section',
            'parentID__fatherName', 'parentID__phoneNumber'
        ).filter(
            isDeleted__exact=False,
            sessionID_id=self.request.session["current_session"]["Id"]
        )

    def filter_queryset(self, qs):
        standard_filter = (self.request.GET.get('standardFilter') or '').strip()
        if standard_filter.isdigit():
            qs = qs.filter(standardID_id=int(standard_filter))

        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(name__icontains=search)
                | Q(email__icontains=search)
                | Q(phoneNumber__icontains=search)
                | Q(standardID__name__icontains=search) | Q(standardID__section__icontains=search)
                | Q(gender__icontains=search) | Q(parentID__phoneNumber__icontains=search) | Q(
                    parentID__motherName__icontains=search) | Q(parentID__profession__icontains=search)
                | Q(parentID__fatherName__icontains=search) | Q(presentAddress__icontains=search)
                | Q(presentCity__icontains=search) | Q(isActive__icontains=search)
                | Q(lastEditedBy__icontains=search) | Q(lastUpdatedOn__icontains=search)
            )

        return qs

    def prepare_results(self, qs):
        json_data = []
        for item in qs:
            images = _avatar_image_html(item.photo)

            action = '''<a href="/management/student_detail/{}/" data-inverted="" data-tooltip="View Detail" data-position="left center" data-variation="mini" style="font-size:10px;" onclick = "GetDataDetails('{}')" class="ui circular facebook icon button purple">
                <i class="eye icon"></i>
              </a>
            <button type="button" onclick="openStudentIdCardModal('{}')" data-inverted="" data-tooltip="ID Card" data-position="left center" data-variation="mini" style="font-size:10px;" class="ui circular blue icon button">
                <i class="id card outline icon"></i>
              </button>
            <a href="/management/edit_student/{}/" data-inverted="" data-tooltip="Edit Detail" data-position="left center" data-variation="mini" style="font-size:10px;" onclick = "GetDataDetails('{}')" class="ui circular facebook icon button green">
                <i class="pen icon"></i>
              </a>
              <button data-inverted="" data-tooltip="Delete" data-position="left center" data-variation="mini" style="font-size:10px;" onclick ="delData('{}')" class="ui circular youtube icon button" style="margin-left: 3px">
                <i class="trash alternate icon"></i>
              </button></td>'''.format(item.pk, item.pk, item.pk, item.pk, item.pk, item.pk)
            if item.standardID.section:
                standard = item.standardID.name + ' - ' + item.standardID.section
            else:
                standard = item.standardID.name
            json_data.append([
                images,
                escape(item.name),
                escape(standard),
                escape(item.gender),
                escape(item.parentID.fatherName),
                escape(item.parentID.phoneNumber),
                escape(item.presentCity),
                escape(item.isActive),
                escape(item.lastEditedBy or 'N/A'),
                escape(item.lastUpdatedOn.strftime('%d-%m-%Y %I:%M %p') if item.lastUpdatedOn else 'N/A'),
                action,

            ])

        return json_data


class StudentIdCardRecordListJson(BaseDatatableView):
    order_columns = [
        'studentID__name',
        'studentID__registrationCode',
        'studentID__standardID__name',
        'studentID__roll',
        'actionType',
        'validTill',
        'remark',
        'lastEditedBy',
        'lastUpdatedOn',
    ]

    def get_initial_queryset(self):
        queryset = StudentIdCardRecord.objects.select_related(
            'studentID', 'studentID__standardID'
        ).filter(
            isDeleted=False,
            sessionID_id=self.request.session['current_session']['Id'],
            studentID__isDeleted=False,
        )

        standard = self.request.GET.get('standard')
        if standard and standard.isdigit():
            queryset = queryset.filter(studentID__standardID_id=int(standard))
        student = self.request.GET.get('student')
        if student and student.isdigit():
            queryset = queryset.filter(studentID_id=int(student))
        action_filter = (self.request.GET.get('action_filter') or '').strip().lower()
        if action_filter:
            queryset = queryset.filter(actionType=action_filter)
        edited_by_filter = (self.request.GET.get('edited_by_filter') or '').strip()
        if edited_by_filter:
            queryset = queryset.filter(lastEditedBy__icontains=edited_by_filter)
        return queryset

    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(studentID__name__icontains=search)
                | Q(studentID__registrationCode__icontains=search)
                | Q(studentID__roll__icontains=search)
                | Q(studentID__standardID__name__icontains=search)
                | Q(studentID__standardID__section__icontains=search)
                | Q(actionType__icontains=search)
                | Q(validTill__icontains=search)
                | Q(remark__icontains=search)
                | Q(lastEditedBy__icontains=search)
                | Q(lastUpdatedOn__icontains=search)
            )
        return qs

    def prepare_results(self, qs):
        json_data = []
        for item in qs:
            standard = 'N/A'
            if item.studentID and item.studentID.standardID:
                standard = item.studentID.standardID.name or 'N/A'
                if item.studentID.standardID.section:
                    standard = f'{standard} - {item.studentID.standardID.section}'

            action_label = dict(StudentIdCardRecord.ACTION_CHOICES).get(item.actionType, item.actionType)
            preview_action = (
                f'<button type="button" onclick="openCardModal({item.studentID_id})" '
                f'data-inverted="" data-tooltip="View ID Card" data-position="left center" '
                f'data-variation="mini" style="font-size:10px;" class="ui circular facebook icon button purple">'
                f'<i class="id card icon"></i></button>'
            )

            json_data.append([
                escape(item.studentID.name if item.studentID and item.studentID.name else 'N/A'),
                escape(item.studentID.registrationCode if item.studentID and item.studentID.registrationCode else 'N/A'),
                escape(standard),
                escape(item.studentID.roll if item.studentID and item.studentID.roll else 'N/A'),
                escape(action_label),
                escape(item.validTill.strftime('%d-%m-%Y') if item.validTill else 'Upto 2026'),
                escape(item.remark or 'N/A'),
                escape(item.lastEditedBy or 'N/A'),
                escape(item.lastUpdatedOn.strftime('%d-%m-%Y %I:%M %p') if item.lastUpdatedOn else 'N/A'),
                preview_action,
            ])
        return json_data


@transaction.atomic
@csrf_exempt
@login_required
def add_student_id_card_record_api(request):
    if request.method != 'POST':
        return ErrorResponse('Invalid request method.', extra={'color': 'red'}).to_json_response()

    try:
        student_id = request.POST.get('student_id')
        action_type = (request.POST.get('action_type') or 'print').strip().lower()
        valid_till = (request.POST.get('valid_till') or '').strip()
        remark = (request.POST.get('remark') or '').strip()
        current_session_id = request.session['current_session']['Id']

        if not student_id:
            return ErrorResponse('Student is required.', extra={'color': 'red'}).to_json_response()

        student = Student.objects.filter(
            pk=int(student_id),
            isDeleted=False,
            sessionID_id=current_session_id,
        ).first()
        if not student:
            return ErrorResponse('Student not found in current session.', extra={'color': 'red'}).to_json_response()

        valid_actions = {choice[0] for choice in StudentIdCardRecord.ACTION_CHOICES}
        if action_type not in valid_actions:
            return ErrorResponse('Invalid tracker action.', extra={'color': 'red'}).to_json_response()

        parsed_valid_till = None
        if valid_till:
            try:
                parsed_valid_till = datetime.strptime(valid_till, '%Y-%m-%d').date()
            except ValueError:
                try:
                    parsed_valid_till = datetime.strptime(valid_till, '%d/%m/%Y').date()
                except ValueError:
                    return ErrorResponse('Invalid valid till date format.', extra={'color': 'red'}).to_json_response()

        instance = StudentIdCardRecord(
            studentID=student,
            actionType=action_type,
            validTill=parsed_valid_till,
            remark=remark,
        )
        pre_save_with_user.send(sender=StudentIdCardRecord, instance=instance, user=request.user.pk)

        return SuccessResponse(
            'ID card tracker updated successfully.',
            data={'preview_url': f'/management/student_id_card/{student.pk}/'},
            extra={'color': 'success'}
        ).to_json_response()
    except Exception as e:
        logger.error(f'Error in add_student_id_card_record_api: {e}')
        return ErrorResponse('Failed to update ID card tracker.', extra={'color': 'red'}).to_json_response()


def _truthy_post_value(value):
    return str(value).lower() in ('1', 'true', 'yes', 'on')


def _clean_color(value, fallback):
    value = (value or '').strip()
    if len(value) == 7 and value.startswith('#'):
        return value
    return fallback


def _clean_number(value, fallback, minimum, maximum):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    return max(minimum, min(maximum, number))


@transaction.atomic
@csrf_exempt
@login_required
@check_groups('Admin', 'Owner')
def save_student_id_card_design_api(request):
    if request.method != 'POST':
        return ErrorResponse('Invalid request method.', extra={'color': 'red'}).to_json_response()

    try:
        current_session = request.session.get('current_session', {})
        school_id = current_session.get('SchoolID')
        session_id = current_session.get('Id')
        if not school_id or not session_id:
            return ErrorResponse('Current school session not found.', extra={'color': 'red'}).to_json_response()

        design = get_or_create_active_id_card_design(school_id, session_id)

        existing_header = merged_config(design.headerConfig, DEFAULT_HEADER_CONFIG)
        header_layout = request.POST.get('header_layout') or existing_header.get('layout') or 'masthead'
        if header_layout not in ('masthead', 'band'):
            header_layout = 'masthead'
        header_config = {
            'layout': header_layout,
            'showLogo': _truthy_post_value(request.POST.get('show_logo')),
            'showSchoolName': _truthy_post_value(request.POST.get('show_school_name')),
            'showAddress': _truthy_post_value(request.POST.get('show_address')),
            'showPhone': _truthy_post_value(request.POST.get('show_phone')),
            'showWebsite': _truthy_post_value(request.POST.get('show_website')),
            'title': (request.POST.get('card_title') or existing_header['title']).strip(),
            'subtitle': (request.POST.get('card_subtitle') or '').strip(),
            'addressText': (request.POST.get('address_text') or '').strip(),
            'phoneNumber': (request.POST.get('phone_number') or '').strip(),
            'websiteUrl': (request.POST.get('website_url') or '').strip(),
            'schoolNameFontSize': _clean_number(request.POST.get('school_name_font_size'), existing_header.get('schoolNameFontSize', 15), 9, 24),
            'logoSizeMm': _clean_number(request.POST.get('logo_size_mm'), existing_header.get('logoSizeMm', 4.6), 0, 12),
            'addressFontSize': _clean_number(request.POST.get('address_font_size'), existing_header.get('addressFontSize', 8.8), 5, 14),
            'contactFontSize': _clean_number(request.POST.get('contact_font_size'), existing_header.get('contactFontSize', 8.3), 5, 14),
            'titleFontSize': _clean_number(request.POST.get('title_font_size'), existing_header.get('titleFontSize', 9), 5, 16),
            'subtitleFontSize': _clean_number(request.POST.get('subtitle_font_size'), existing_header.get('subtitleFontSize', 7), 5, 14),
        }

        existing_style = merged_config(design.styleConfig, DEFAULT_STYLE_CONFIG)
        style_config = {
            'primaryColor': _clean_color(request.POST.get('primary_color'), existing_style['primaryColor']),
            'headerColor': _clean_color(request.POST.get('header_color'), existing_style['headerColor']),
            'headerTextColor': _clean_color(request.POST.get('header_text_color'), existing_style['headerTextColor']),
            'cardBackgroundColor': _clean_color(request.POST.get('card_background_color'), existing_style['cardBackgroundColor']),
            'textColor': _clean_color(request.POST.get('text_color'), existing_style['textColor']),
            'labelColor': _clean_color(request.POST.get('label_color'), existing_style['labelColor']),
            'fontFamily': (request.POST.get('font_family') or existing_style['fontFamily']).strip(),
            'photoShape': request.POST.get('photo_shape') if request.POST.get('photo_shape') in ('rounded', 'circle') else 'rounded',
        }

        validity_mode = request.POST.get('validity_mode') or DEFAULT_FOOTER_CONFIG['validityMode']
        if validity_mode not in ('session_end', 'custom_date', 'custom_text', 'hidden'):
            validity_mode = 'session_end'

        valid_till = (request.POST.get('valid_till') or '').strip()
        parsed_valid_till = ''
        if valid_till:
            try:
                parsed_valid_till = datetime.strptime(valid_till, '%Y-%m-%d').date().isoformat()
            except ValueError:
                try:
                    parsed_valid_till = datetime.strptime(valid_till, '%d/%m/%Y').date().isoformat()
                except ValueError:
                    return ErrorResponse('Invalid validity date.', extra={'color': 'red'}).to_json_response()

        footer_config = {
            'showValidity': _truthy_post_value(request.POST.get('show_validity')),
            'validityMode': validity_mode,
            'validityText': (request.POST.get('validity_text') or '').strip(),
            'validTill': parsed_valid_till,
            'showSignature': _truthy_post_value(request.POST.get('show_signature')),
            'showSignatureImage': _truthy_post_value(request.POST.get('show_signature_image')),
            'signatureLabel': (request.POST.get('signature_label') or DEFAULT_FOOTER_CONFIG['signatureLabel']).strip(),
        }

        fields_payload = request.POST.get('fields_config') or '[]'
        try:
            fields_config = json.loads(fields_payload)
        except json.JSONDecodeError:
            return ErrorResponse('Invalid field configuration.', extra={'color': 'red'}).to_json_response()
        fields_config = normalize_fields_config(fields_config)

        design.name = (request.POST.get('design_name') or design.name or 'Default ID Card Design').strip()
        design.headerConfig = header_config
        design.fieldsConfig = fields_config
        design.styleConfig = style_config
        design.footerConfig = footer_config

        if request.FILES.get('processed_principal_signature'):
            design.principalSignature = request.FILES['processed_principal_signature']
        elif request.FILES.get('principal_signature'):
            design.principalSignature = request.FILES['principal_signature']
        if request.POST.get('remove_principal_signature') == '1':
            design.principalSignature.delete(save=False)
            design.principalSignature = None

        if request.FILES.get('background_image'):
            design.backgroundImage = request.FILES['background_image']
        if request.POST.get('remove_background_image') == '1':
            design.backgroundImage.delete(save=False)
            design.backgroundImage = None

        pre_save_with_user.send(sender=StudentIdCardDesign, instance=design, user=request.user.pk)

        return SuccessResponse(
            'ID card design saved successfully.',
            data={
                'design_id': design.pk,
                'has_signature': bool(design.principalSignature),
            },
            extra={'color': 'success'}
        ).to_json_response()
    except Exception as e:
        logger.error(f'Error in save_student_id_card_design_api: {e}')
        return ErrorResponse('Failed to save ID card design.', extra={'color': 'red'}).to_json_response()


@transaction.atomic
@csrf_exempt
@login_required
def delete_student(request):
    if request.method == 'POST':
        try:
            id = request.POST.get("dataID")
            instance = Student.objects.get(pk=int(id), isDeleted=False,
                                           sessionID_id=request.session['current_session']['Id'])
            instance.isDeleted = True
            instance.isActive = 'No'
            user = User.objects.get(pk=instance.userID_id)
            user.is_active = False
            user.save()
            pre_save_with_user.send(sender=Student, instance=instance, user=request.user.pk)
            instance.save()
            return _api_response(
                {'status': 'success', 'message': 'Student detail deleted successfully.',
                 'color': 'success'}, safe=False)
        except:
            return _api_response({'status': 'error'}, safe=False)
    return _api_response({'status': 'error'}, safe=False)


@transaction.atomic
@csrf_exempt
@login_required
def edit_student_api(request):
    try:

        if request.method != 'POST':
            return ErrorResponse("Method not allowed").to_json_response()

        post_data = request.POST.dict()
        files_data = request.FILES


        # ---------- PARENT ----------
        parent_obj = _get_or_create_current_session_parent(request, post_data)


        # ---------- STUDENT EXIST CHECK ----------
        if Student.objects.filter(
            registrationCode__iexact = post_data.get("registrationCode"),
            sessionID_id = request.session['current_session']['Id'],
            isDeleted = False
        ).exclude(pk = post_data.get("editID")).exists():
            return _api_response(
                {'status': 'success', 'message': 'Student already exists.', 'color': 'info'},
                safe=False
            )



        # ---------- STUDENT UPDATE ----------
        student_obj = Student.objects.get(pk=post_data.get("editID"))
        student_obj.registrationCode = post_data.get("registrationCode")
        student_obj.name = post_data.get("name")
        student_obj.email = post_data.get("email")
        student_obj.phoneNumber = post_data.get("phone")
        student_obj.bloodGroup = post_data.get("bloodGroup")
        student_obj.gender = post_data.get("gender")
        student_obj.aadhar = post_data.get("aadhar")
        student_obj.idMark = post_data.get("idMark")
        student_obj.penNumber = post_data.get("penNumber")
        student_obj.caste = post_data.get("caste")
        student_obj.tribe = post_data.get("tribe")
        student_obj.religion = post_data.get("religion")
        student_obj.motherTongue = post_data.get("motherTongue")
        student_obj.otherLanguages = post_data.get("otherLanguages")
        student_obj.hobbies = post_data.get("hobbies")
        student_obj.aimInLife = post_data.get("aimInLife")
        student_obj.milOption = post_data.get("milOptions")

        student_obj.familyCode = post_data.get("familyCode")
        student_obj.siblingsCount = int(post_data.get("siblings")) if post_data.get("siblings") else 0
        student_obj.roll = post_data.get("roll")

        # Previous School
        student_obj.lastSchoolName = post_data.get("previousSchoolName")
        student_obj.lastSchoolAddress = post_data.get("previousSchoolAddress")
        student_obj.lastClass = post_data.get("previousSchoolClass")
        student_obj.lastResult = post_data.get("previousSchoolResult")
        student_obj.lastDivision = post_data.get("previousSchoolDivision")
        student_obj.lastRollNo = post_data.get("previousSchoolRollNumber")

        # Fees
        student_obj.admissionFee = float(post_data.get("admissionFee")) if post_data.get("admissionFee") else 0
        student_obj.tuitionFee = float(post_data.get("tuitionFee")) if post_data.get("tuitionFee") else 0
        student_obj.miscFee = float(post_data.get("miscFee")) if post_data.get("miscFee") else 0
        student_obj.totalFee = float(post_data.get("totalFee")) if post_data.get("totalFee") else 0

        # Foreign keys
        student_obj.standardID_id = post_data.get("standard")
        student_obj.parentID = parent_obj
        # student_obj.schoolID_id = request.session['current_session']['SchoolID']
        student_obj.sessionID_id = request.session['current_session']['Id']

        student_obj.isDeleted = False

        # ---------- DATE HANDLING ----------
        if post_data.get("dob"):
            student_obj.dob = datetime.strptime(post_data["dob"], "%d/%m/%Y")

        if post_data.get("doj"):
            student_obj.dateOfJoining = datetime.strptime(post_data["doj"], "%d/%m/%Y")

        # ---------- IMAGE ----------
        if "imageUpload" in files_data:
            student_obj.photo = optimize_uploaded_image(files_data["imageUpload"])

        # ---------- FINAL SAVE ----------
        pre_save_with_user.send(sender=Student, instance=student_obj, user=request.user.pk)
        student_obj.save()

        try:
            _sync_admission_finance_for_student(request, student_obj, post_data)
        except Exception as exc:
            logger.error(f"Finance sync failed for edit_student_api student={student_obj.id}: {exc}")

        logger.info("Student details updated successfully.")
        return SuccessResponse(
            "Student details updated successfully.",
            data={'status': 'success', 'message': 'Student details updated successfully.', 'color': 'success'},
        ).to_json_response()
    except Exception as e:
        logger.error(f"Error in updating student details: {str(e)}")
        return ErrorResponse(str(e)).to_json_response()

# ---------------------Exam -------------------------------------------------
@transaction.atomic
@csrf_exempt
@login_required
def add_exam(request):
    if request.method == 'POST':
        exam = request.POST.get("exam")
        try:
            Exam.objects.get(name__iexact=exam, isDeleted=False,
                             sessionID_id=request.session['current_session']['Id'])
            return _api_response(
                {'status': 'success', 'message': 'Exam already exists. Please change the name.', 'color': 'info'},
                safe=False)
        except:
            instance = Exam()
            instance.name = exam
            pre_save_with_user.send(sender=Exam, instance=instance, user=request.user.pk)
            instance.save()
            return _api_response(
                {'status': 'success', 'message': 'New Exam created successfully.', 'color': 'success'},
                safe=False)
    return _api_response({'status': 'error'}, safe=False)


class ExamListJson(BaseDatatableView):
    order_columns = ['name', 'lastEditedBy', 'lastUpdatedOn']

    def get_initial_queryset(self):
        return Exam.objects.only(
            'id', 'name', 'lastEditedBy', 'datetime', 'lastUpdatedOn'
        ).filter(
            isDeleted__exact=False,
            sessionID_id=self.request.session["current_session"]["Id"]
        )

    def filter_queryset(self, qs):

        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(name__icontains=search)
                | Q(lastEditedBy__icontains=search) | Q(lastUpdatedOn__icontains=search)
            )

        return qs

    def prepare_results(self, qs):
        json_data = []
        for item in qs:
            action = '''<button data-inverted="" data-tooltip="Edit Detail" data-position="left center" data-variation="mini" style="font-size:10px;" onclick = "GetDataDetails('{}')" class="ui circular facebook icon button green">
                <i class="pen icon"></i>
              </button>
              <button data-inverted="" data-tooltip="Delete" data-position="left center" data-variation="mini" style="font-size:10px;" onclick ="delData('{}')" class="ui circular youtube icon button" style="margin-left: 3px">
                <i class="trash alternate icon"></i>
              </button></td>'''.format(item.pk, item.pk),

            json_data.append([
                escape(item.name),
                escape(item.lastEditedBy or 'N/A'),
                escape(item.lastUpdatedOn.strftime('%d-%m-%Y %I:%M %p') if item.lastUpdatedOn else 'N/A'),
                action,

            ])

        return json_data


@transaction.atomic
@csrf_exempt
@login_required
def delete_exam(request):
    if request.method == 'POST':
        try:
            id = request.POST.get("dataID")
            instance = Exam.objects.get(pk=int(id), isDeleted=False,
                                        sessionID_id=request.session['current_session']['Id'])
            instance.isDeleted = True
            pre_save_with_user.send(sender=Exam, instance=instance, user=request.user.pk)
            instance.save()
            return _api_response(
                {'status': 'success', 'message': 'Exam detail deleted successfully.',
                 'color': 'success'}, safe=False)
        except:
            return _api_response({'status': 'error'}, safe=False)
    return _api_response({'status': 'error'}, safe=False)


@login_required
def get_exam_detail(request, **kwargs):
    try:
        id = request.GET.get('id')
        obj = Exam.objects.get(pk=id, isDeleted=False, sessionID_id=request.session['current_session']['Id'])
        obj_dic = {
            'ID': obj.pk,
            'ExamName': obj.name,
        }
        return _api_response({'status': 'success', 'data': obj_dic}, safe=False)
    except:
        return _api_response({'status': 'error'}, safe=False)


@transaction.atomic
@csrf_exempt
@login_required
def edit_exam(request):
    if request.method == 'POST':
        exam = request.POST.get("exam")
        editID = request.POST.get("editID")
        try:
            instance = Exam.objects.get(pk=int(editID))
            data = Exam.objects.filter(name__iexact=exam, isDeleted=False,
                                       sessionID_id=request.session['current_session']['Id']).exclude(
                pk=int(editID))
            if data.count() > 0:
                return _api_response(
                    {'status': 'success', 'message': 'Exam already exists. Please change the name.',
                     'color': 'info'},
                    safe=False)
            else:
                # instance = Subjects.objects.get(pk=int(editID))
                instance.name = exam
                pre_save_with_user.send(sender=Exam, instance=instance, user=request.user.pk)
                instance.save()
                return _api_response(
                    {'status': 'success', 'message': 'Exam name updated successfully.', 'color': 'success'},
                    safe=False)
        except:

            return _api_response({'status': 'error'}, safe=False)


@login_required
def get_exams_list_api(request):
    rows = Exam.objects.filter(
        isDeleted=False,
        sessionID_id=_current_session_id(request)
    ).values('id', 'name').order_by('name')
    data = [{'ID': row['id'], 'Name': row.get('name') or 'N/A'} for row in rows]
    return _api_response(
        {'status': 'success', 'data': data, 'color': 'success'}, safe=False)


def _parse_exam_time(value):
    if not value:
        return None
    value = value.strip()
    for candidate in (value, value.upper()):
        for fmt in ('%I:%M %p', '%H:%M', '%H:%M:%S'):
            try:
                return datetime.strptime(candidate, fmt).time()
            except ValueError:
                pass
    raise ValueError("Invalid time format")


def _validate_exam_timetable_business_rules(
        current_session_id,
        standard_id,
        exam_id,
        subject_id,
        parsed_exam_date,
        parsed_start_time,
        parsed_end_time,
        room_no,
        exclude_id=None,
):
    assign_subject_exists = AssignSubjectsToClass.objects.filter(
        isDeleted=False,
        sessionID_id=current_session_id,
        standardID_id=standard_id,
        subjectID_id=subject_id,
    ).exists()
    if not assign_subject_exists:
        return False, "Selected subject is not assigned to the selected class.", 'red'

    assigned_exam = AssignExamToClass.objects.filter(
        isDeleted=False,
        sessionID_id=current_session_id,
        standardID_id=standard_id,
        examID_id=exam_id,
    ).order_by('-datetime').first()
    if not assigned_exam:
        return False, "Selected exam is not assigned to the selected class.", 'red'

    if assigned_exam.startDate and parsed_exam_date < assigned_exam.startDate:
        return False, "Exam date cannot be before assigned exam start date.", 'red'
    if assigned_exam.endDate and parsed_exam_date > assigned_exam.endDate:
        return False, "Exam date cannot be after assigned exam end date.", 'red'

    base_queryset = ExamTimeTable.objects.filter(
        isDeleted=False,
        sessionID_id=current_session_id,
        examDate=parsed_exam_date,
    )
    if exclude_id:
        base_queryset = base_queryset.exclude(pk=exclude_id)

    duplicate_exists = base_queryset.filter(
        standardID_id=standard_id,
        examID_id=exam_id,
        subjectID_id=subject_id,
    ).exists()
    if duplicate_exists:
        return False, "Exam timetable already exists for this class, exam, subject and date.", 'info'

    class_overlap_exists = base_queryset.filter(
        standardID_id=standard_id,
        startTime__lt=parsed_end_time,
        endTime__gt=parsed_start_time,
    ).exists()
    if class_overlap_exists:
        return False, "Time conflict: this class already has another paper in the selected time slot.", 'red'

    if room_no:
        room_overlap_exists = base_queryset.filter(
            roomNo__iexact=room_no,
            startTime__lt=parsed_end_time,
            endTime__gt=parsed_start_time,
        ).exists()
        if room_overlap_exists:
            return False, "Time conflict: selected room is already occupied in this time slot.", 'red'

    return True, "", 'success'


@transaction.atomic
@csrf_exempt
@login_required
def add_exam_timetable(request):
    if request.method != 'POST':
        return ErrorResponse("Method not allowed", extra={'color': 'red'}).to_json_response()

    try:
        standard_id = request.POST.get("standard")
        exam_id = request.POST.get("exam")
        subject_id = request.POST.get("subject")
        exam_date = request.POST.get("examDate")
        start_time = request.POST.get("startTime")
        end_time = request.POST.get("endTime")
        room_no = request.POST.get("roomNo", "").strip()
        note = request.POST.get("note", "").strip()
        current_session_id = request.session['current_session']['Id']

        if not (standard_id and exam_id and subject_id and exam_date and start_time and end_time):
            return ErrorResponse(
                "Class, exam, subject, date, start time and end time are required.",
                extra={'color': 'red'}
            ).to_json_response()

        standard_id = int(standard_id)
        exam_id = int(exam_id)
        subject_id = int(subject_id)

        standard_exists = Standard.objects.filter(
            pk=standard_id, isDeleted=False, sessionID_id=current_session_id
        ).exists()
        exam_exists = Exam.objects.filter(
            pk=exam_id, isDeleted=False, sessionID_id=current_session_id
        ).exists()
        subject_exists = Subjects.objects.filter(
            pk=subject_id, isDeleted=False, sessionID_id=current_session_id
        ).exists()
        if not (standard_exists and exam_exists and subject_exists):
            return ErrorResponse(
                "Invalid class, exam, or subject for current session.",
                extra={'color': 'red'}
            ).to_json_response()

        parsed_exam_date = datetime.strptime(exam_date, '%d/%m/%Y').date()
        parsed_start_time = _parse_exam_time(start_time)
        parsed_end_time = _parse_exam_time(end_time)
        if parsed_start_time >= parsed_end_time:
            return ErrorResponse("Start time must be before end time.", extra={'color': 'red'}).to_json_response()

        is_valid, validation_message, validation_color = _validate_exam_timetable_business_rules(
            current_session_id=current_session_id,
            standard_id=standard_id,
            exam_id=exam_id,
            subject_id=subject_id,
            parsed_exam_date=parsed_exam_date,
            parsed_start_time=parsed_start_time,
            parsed_end_time=parsed_end_time,
            room_no=room_no,
        )
        if not is_valid:
            return ErrorResponse(validation_message, extra={'color': validation_color}).to_json_response()

        instance = ExamTimeTable()
        instance.standardID_id = standard_id
        instance.examID_id = exam_id
        instance.subjectID_id = subject_id
        instance.examDate = parsed_exam_date
        instance.startTime = parsed_start_time
        instance.endTime = parsed_end_time
        instance.roomNo = room_no
        instance.note = note
        pre_save_with_user.send(sender=ExamTimeTable, instance=instance, user=request.user.pk)
        instance.save()
        return SuccessResponse("Exam timetable added successfully.", extra={'color': 'success'}).to_json_response()
    except ValueError:
        return ErrorResponse("Invalid date/time format.", extra={'color': 'red'}).to_json_response()
    except IntegrityError:
        return ErrorResponse(
            "Unable to save timetable due to conflict. Please refresh and try again.",
            extra={'color': 'red'}
        ).to_json_response()
    except Exception as e:
        logger.error(f"Error in add_exam_timetable: {e}")
        return ErrorResponse("Error in adding exam timetable.", extra={'color': 'red'}).to_json_response()


class ExamTimeTableListJson(BaseDatatableView):
    order_columns = [
        'standardID.name',
        'standardID.section',
        'examID.name',
        'subjectID.name',
        'examDate',
        'startTime',
        'endTime',
        'roomNo',
        'lastEditedBy',
        'lastUpdatedOn'
    ]

    def get_initial_queryset(self):
        return ExamTimeTable.objects.select_related(
            'standardID', 'examID', 'subjectID'
        ).filter(
            isDeleted=False,
            sessionID_id=self.request.session["current_session"]["Id"]
        ).order_by('-examDate', 'startTime')

    def filter_queryset(self, qs):
        standard_filter = (self.request.GET.get('standardFilter') or '').strip()
        exam_filter = (self.request.GET.get('examFilter') or '').strip()

        if standard_filter.isdigit():
            qs = qs.filter(standardID_id=int(standard_filter))
        if exam_filter.isdigit():
            qs = qs.filter(examID_id=int(exam_filter))

        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(standardID__name__icontains=search)
                | Q(standardID__section__icontains=search)
                | Q(examID__name__icontains=search)
                | Q(subjectID__name__icontains=search)
                | Q(roomNo__icontains=search)
                | Q(lastEditedBy__icontains=search)
                | Q(lastUpdatedOn__icontains=search)
            )
        return qs

    def prepare_results(self, qs):
        json_data = []
        for item in qs:
            action = '''<button data-inverted="" data-tooltip="Edit Detail" data-position="left center" data-variation="mini" style="font-size:10px;" onclick = "GetDataDetails('{}')" class="ui circular facebook icon button green">
                <i class="pen icon"></i>
              </button>
              <button data-inverted="" data-tooltip="Delete" data-position="left center" data-variation="mini" style="font-size:10px;" onclick ="delData('{}')" class="ui circular youtube icon button" style="margin-left: 3px">
                <i class="trash alternate icon"></i>
              </button></td>'''.format(item.pk, item.pk)
            section = item.standardID.section if item.standardID and item.standardID.section else 'N/A'
            json_data.append([
                escape(item.standardID.name if item.standardID else 'N/A'),
                escape(section),
                escape(item.examID.name if item.examID else 'N/A'),
                escape(item.subjectID.name if item.subjectID else 'N/A'),
                escape(item.examDate.strftime('%d-%m-%Y') if item.examDate else 'N/A'),
                escape(item.startTime.strftime('%I:%M %p') if item.startTime else 'N/A'),
                escape(item.endTime.strftime('%I:%M %p') if item.endTime else 'N/A'),
                escape(item.roomNo if item.roomNo else 'N/A'),
                escape(item.lastEditedBy if item.lastEditedBy else 'N/A'),
                escape(item.lastUpdatedOn.strftime('%d-%m-%Y %I:%M %p') if item.lastUpdatedOn else 'N/A'),
                action,
            ])
        return json_data


@login_required
def get_exam_timetable_detail(request, **kwargs):
    try:
        row_id = request.GET.get('id')
        obj = ExamTimeTable.objects.get(
            pk=row_id,
            isDeleted=False,
            sessionID_id=request.session['current_session']['Id']
        )
        obj_dic = {
            'ID': obj.pk,
            'StandardID': obj.standardID_id,
            'ExamID': obj.examID_id,
            'SubjectID': obj.subjectID_id,
            'ExamDate': obj.examDate.strftime('%d/%m/%Y') if obj.examDate else '',
            'StartTime': obj.startTime.strftime('%I:%M %p') if obj.startTime else '',
            'EndTime': obj.endTime.strftime('%I:%M %p') if obj.endTime else '',
            'RoomNo': obj.roomNo if obj.roomNo else '',
            'Note': obj.note if obj.note else '',
        }
        return SuccessResponse(
            "Exam timetable detail fetched successfully.",
            data=obj_dic,
            extra={'color': 'success'}
        ).to_json_response()
    except ExamTimeTable.DoesNotExist:
        return ErrorResponse("Exam timetable detail not found.", extra={'color': 'red'}).to_json_response()
    except Exception as e:
        logger.error(f"Error in get_exam_timetable_detail: {e}")
        return ErrorResponse("Error in fetching exam timetable detail.", extra={'color': 'red'}).to_json_response()


@transaction.atomic
@csrf_exempt
@login_required
def update_exam_timetable(request):
    if request.method != 'POST':
        return ErrorResponse("Method not allowed", extra={'color': 'red'}).to_json_response()

    try:
        edit_id = request.POST.get("editID")
        standard_id = request.POST.get("standard")
        exam_id = request.POST.get("exam")
        subject_id = request.POST.get("subject")
        exam_date = request.POST.get("examDate")
        start_time = request.POST.get("startTime")
        end_time = request.POST.get("endTime")
        room_no = request.POST.get("roomNo", "").strip()
        note = request.POST.get("note", "").strip()
        current_session_id = request.session['current_session']['Id']

        if not (edit_id and standard_id and exam_id and subject_id and exam_date and start_time and end_time):
            return ErrorResponse(
                "Class, exam, subject, date, start time and end time are required.",
                extra={'color': 'red'}
            ).to_json_response()

        edit_id = int(edit_id)
        standard_id = int(standard_id)
        exam_id = int(exam_id)
        subject_id = int(subject_id)

        standard_exists = Standard.objects.filter(
            pk=standard_id, isDeleted=False, sessionID_id=current_session_id
        ).exists()
        exam_exists = Exam.objects.filter(
            pk=exam_id, isDeleted=False, sessionID_id=current_session_id
        ).exists()
        subject_exists = Subjects.objects.filter(
            pk=subject_id, isDeleted=False, sessionID_id=current_session_id
        ).exists()
        if not (standard_exists and exam_exists and subject_exists):
            return ErrorResponse(
                "Invalid class, exam, or subject for current session.",
                extra={'color': 'red'}
            ).to_json_response()

        instance = ExamTimeTable.objects.get(
            pk=edit_id, isDeleted=False, sessionID_id=current_session_id
        )
        parsed_exam_date = datetime.strptime(exam_date, '%d/%m/%Y').date()
        parsed_start_time = _parse_exam_time(start_time)
        parsed_end_time = _parse_exam_time(end_time)
        if parsed_start_time >= parsed_end_time:
            return ErrorResponse("Start time must be before end time.", extra={'color': 'red'}).to_json_response()

        is_valid, validation_message, validation_color = _validate_exam_timetable_business_rules(
            current_session_id=current_session_id,
            standard_id=standard_id,
            exam_id=exam_id,
            subject_id=subject_id,
            parsed_exam_date=parsed_exam_date,
            parsed_start_time=parsed_start_time,
            parsed_end_time=parsed_end_time,
            room_no=room_no,
            exclude_id=edit_id,
        )
        if not is_valid:
            return ErrorResponse(validation_message, extra={'color': validation_color}).to_json_response()

        instance.standardID_id = standard_id
        instance.examID_id = exam_id
        instance.subjectID_id = subject_id
        instance.examDate = parsed_exam_date
        instance.startTime = parsed_start_time
        instance.endTime = parsed_end_time
        instance.roomNo = room_no
        instance.note = note
        pre_save_with_user.send(sender=ExamTimeTable, instance=instance, user=request.user.pk)
        instance.save()
        return SuccessResponse("Exam timetable updated successfully.", extra={'color': 'success'}).to_json_response()
    except ExamTimeTable.DoesNotExist:
        return ErrorResponse("Exam timetable detail not found.", extra={'color': 'red'}).to_json_response()
    except ValueError:
        return ErrorResponse("Invalid date/time format.", extra={'color': 'red'}).to_json_response()
    except IntegrityError:
        return ErrorResponse(
            "Unable to update timetable due to conflict. Please refresh and try again.",
            extra={'color': 'red'}
        ).to_json_response()
    except Exception as e:
        logger.error(f"Error in update_exam_timetable: {e}")
        return ErrorResponse("Error in updating exam timetable.", extra={'color': 'red'}).to_json_response()


@transaction.atomic
@csrf_exempt
@login_required
def delete_exam_timetable(request):
    if request.method != 'POST':
        return ErrorResponse("Method not allowed", extra={'color': 'red'}).to_json_response()

    try:
        row_id = request.POST.get("dataID")
        instance = ExamTimeTable.objects.get(
            pk=row_id, isDeleted=False, sessionID_id=request.session['current_session']['Id']
        )
        instance.isDeleted = True
        pre_save_with_user.send(sender=ExamTimeTable, instance=instance, user=request.user.pk)
        instance.save()
        return SuccessResponse("Exam timetable deleted successfully.", extra={'color': 'success'}).to_json_response()
    except ExamTimeTable.DoesNotExist:
        return ErrorResponse("Exam timetable detail not found.", extra={'color': 'red'}).to_json_response()
    except Exception as e:
        logger.error(f"Error in delete_exam_timetable: {e}")
        return ErrorResponse("Error in deleting exam timetable.", extra={'color': 'red'}).to_json_response()


# assign exam to class
@transaction.atomic
@csrf_exempt
@login_required
def add_exam_to_class(request):
    if request.method == 'POST':
        try:
            standard = request.POST.get("standard")
            exam = request.POST.get("exam")
            fmark = request.POST.get("fmark")
            pmark = request.POST.get("pmark")
            sDate = request.POST.get("sDate")
            eDate = request.POST.get("eDate")
            subject_list = [int(x) for x in standard.split(',')]
            for s in subject_list:
                try:
                    AssignExamToClass.objects.get(examID_id=int(exam), standardID_id=int(s), isDeleted=False,
                                                  sessionID_id=request.session['current_session']['Id'])
                except:
                    instance = AssignExamToClass()
                    instance.standardID_id = int(s)
                    instance.examID_id = int(exam)
                    instance.fullMarks = float(fmark)
                    instance.passMarks = float(pmark)
                    instance.startDate = datetime.strptime(sDate, '%d/%m/%Y')
                    instance.endDate = datetime.strptime(eDate, '%d/%m/%Y')
                    pre_save_with_user.send(sender=AssignExamToClass, instance=instance, user=request.user.pk)
                    instance.save()
            return _api_response(
                {'status': 'success', 'message': 'Exam assigned successfully.', 'color': 'success'},
                safe=False)
        except:
            return _api_response({'status': 'error'}, safe=False)


class AssignExamToClassListJson(BaseDatatableView):
    order_columns = ['standardID.name', 'standardID.section',
                     'examID.name', 'fullMarks', 'passMarks', 'startDate', 'endDate', 'lastEditedBy', 'lastUpdatedOn']

    def get_initial_queryset(self):
        return AssignExamToClass.objects.select_related(
            'standardID', 'examID'
        ).only(
            'id', 'fullMarks', 'passMarks', 'startDate', 'endDate', 'lastEditedBy', 'lastUpdatedOn',
            'standardID__name', 'standardID__section',
            'examID__name'
        ).filter(
            isDeleted__exact=False,
            sessionID_id=self.request.session["current_session"]["Id"]
        ).order_by(
            'standardID__name')

    def filter_queryset(self, qs):
        class_filter = self.request.GET.get('class_filter')
        exam_filter = self.request.GET.get('exam_filter')

        if class_filter and str(class_filter).isdigit():
            qs = qs.filter(standardID_id=int(class_filter))

        if exam_filter and str(exam_filter).isdigit():
            qs = qs.filter(examID_id=int(exam_filter))

        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(standardID__name__icontains=search) | Q(examID__name__icontains=search)
                | Q(fullMarks__icontains=search) | Q(passMarks__icontains=search)
                | Q(endDate__icontains=search) | Q(
                    standardID__section__icontains=search) | Q(
                    startDate__icontains=search)
                | Q(lastEditedBy__icontains=search) | Q(lastUpdatedOn__icontains=search)
            )

        return qs

    def prepare_results(self, qs):
        json_data = []
        for item in qs:
            action = '''
              <button data-inverted="" data-tooltip="Edit Detail" data-position="left center" data-variation="mini" style="font-size:10px;" onclick = "GetDataDetails('{}')" class="ui circular facebook icon button green">
                <i class="pen icon"></i>
              </button>
              <button data-inverted="" data-tooltip="Delete" data-position="left center" data-variation="mini" style="font-size:10px;" onclick ="delData('{}')" class="ui circular youtube icon button" style="margin-left: 3px">
                <i class="trash alternate icon"></i>
              </button></td>'''.format(item.pk, item.pk),
            if item.standardID.section:
                section = item.standardID.section
            else:
                section = 'N/A'

            json_data.append([
                escape(item.standardID.name),
                escape(section),
                escape(item.examID.name),
                escape(item.fullMarks),
                escape(item.passMarks),
                escape(item.startDate.strftime('%d-%m-%Y')),
                escape(item.endDate.strftime('%d-%m-%Y')),
                escape(item.lastEditedBy or 'N/A'),
                escape(item.lastUpdatedOn.strftime('%d-%m-%Y %I:%M %p') if item.lastUpdatedOn else 'N/A'),
                action,

            ])

        return json_data


@transaction.atomic
@csrf_exempt
@login_required
def delete_assign_exam_to_class(request):
    if request.method == 'POST':
        try:
            id = request.POST.get("dataID")
            instance = AssignExamToClass.objects.get(pk=int(id), isDeleted=False,
                                                     sessionID_id=request.session['current_session']['Id'])
            instance.isDeleted = True
            pre_save_with_user.send(sender=AssignExamToClass, instance=instance, user=request.user.pk)
            instance.save()
            return _api_response(
                {'status': 'success', 'message': 'Assigned Exam detail deleted successfully.',
                 'color': 'success'}, safe=False)
        except:
            return _api_response({'status': 'error'}, safe=False)
    return _api_response({'status': 'error'}, safe=False)


@login_required
def get_assigned_exam_to_class_detail(request, **kwargs):
    try:
        id = request.GET.get('id')
        obj = AssignExamToClass.objects.get(pk=id, isDeleted=False,
                                            sessionID_id=request.session['current_session']['Id'])
        obj_dic = {
            'StandardID': obj.standardID_id,
            'ExamID': obj.examID_id,
            'FullMarks': obj.fullMarks,
            'PassMarks': obj.passMarks,
            'StartDate': obj.startDate.strftime('%d/%m/%Y'),
            'EndDate': obj.endDate.strftime('%d/%m/%Y'),
            'ID': obj.pk,
        }
        return _api_response({'status': 'success', 'data': obj_dic}, safe=False)
    except:
        return _api_response({'status': 'error'}, safe=False)


@transaction.atomic
@csrf_exempt
@login_required
def update_exam_to_class(request):
    if request.method == 'POST':
        try:
            editID = request.POST.get("editID")
            standard = request.POST.get("standard")
            exam = request.POST.get("exam")
            fmark = request.POST.get("fmark")
            pmark = request.POST.get("pmark")
            sDate = request.POST.get("sDate")
            eDate = request.POST.get("eDate")
            subject_list = [int(x) for x in standard.split(',')]
            instance = AssignExamToClass.objects.get(pk=int(editID))
            for s in subject_list:
                try:
                    AssignExamToClass.objects.get(examID_id=int(exam), standardID_id=int(s),
                                                  isDeleted=False,
                                                  sessionID_id=request.session['current_session']['Id']).exclude(
                        pk=int(editID))
                    return _api_response(
                        {'status': 'success', 'message': 'Detail already assigned.', 'color': 'info'},
                        safe=False)
                except:
                    instance.standardID_id = int(s)
                    instance.examID_id = int(exam)
                    instance.fullMarks = float(fmark)
                    instance.passMarks = float(pmark)
                    instance.startDate = datetime.strptime(sDate, '%d/%m/%Y')
                    instance.endDate = datetime.strptime(eDate, '%d/%m/%Y')
                    pre_save_with_user.send(sender=AssignExamToClass, instance=instance, user=request.user.pk)
                    instance.save()
            return _api_response(
                {'status': 'success', 'message': 'Detail updated successfully.', 'color': 'success'},
                safe=False)
        except:
            return _api_response({'status': 'error'}, safe=False)


@login_required
def get_exam_list_by_class_api(request):
    standard = request.GET.get('standard')
    try:
        standard_id = int(standard)
    except (TypeError, ValueError):
        return _api_response(
            {'status': 'success', 'data': [], 'color': 'success'}, safe=False)
    objs = AssignExamToClass.objects.filter(isDeleted=False, sessionID_id=request.session['current_session']['Id'],
                                  standardID_id=standard_id).order_by(
        'examID__name')
    data = []
    for obj in objs:
        name = obj.examID.name

        data_dic = {
            'ID': obj.pk,
            'Name': name

        }
        data.append(data_dic)
    return _api_response(
        {'status': 'success', 'data': data,
         'color': 'success'}, safe=False)



# Attendance ------------------------------------------------------------------

def _student_attendance_datatable_row(item, *, pending_leave=None):
    if item.studentID and item.studentID.photo:
        images = _avatar_image_html(item.studentID.photo)
    else:
        images = _avatar_image_html(None)

    action = '''<button class="ui mini primary button" onclick="pushAttendance({})">
  Save
</button>'''.format(item.pk)

    status = attendance_status_from_values(
        is_present=item.isPresent,
        absent_reason=item.absentReason,
        is_holiday=item.isHoliday,
        attendance_status=item.attendanceStatus,
    )

    status_chip = '<span class="ui tiny grey label">Absent</span>'
    if status == 'present':
        status_chip = '<span class="ui tiny green label">Present</span>'
    elif status == ATTENDANCE_STATUS_LEAVE:
        duration_text = leave_duration_label(item.leaveDurationType)
        status_chip = f'<span class="ui tiny blue label">Approved Leave</span><div class="ui tiny basic blue label" style="margin-top:3px;">{escape(duration_text)}</div>'
    elif status == ATTENDANCE_STATUS_HOLIDAY:
        status_chip = '<span class="ui tiny teal label">Holiday</span>'
    elif pending_leave:
        duration_text = leave_duration_label(pending_leave.durationType)
        leave_name = pending_leave.leaveTypeID.name if pending_leave.leaveTypeID else 'Leave'
        status_chip = f'<span class="ui tiny orange label">Pending Leave</span><div class="ui tiny basic orange label" style="margin-top:3px;">{escape(leave_name)} - {escape(duration_text)}</div>'

    is_approved_leave = status == ATTENDANCE_STATUS_LEAVE
    is_holiday = status == ATTENDANCE_STATUS_HOLIDAY
    disabled_attr = ' disabled data-leave-locked="true"' if is_approved_leave else ''
    if is_holiday:
        disabled_attr = ' disabled data-holiday-locked="true"'
    if item.isPresent:
        is_present = '''
            <div class="ui checkbox">
  <input type="checkbox" name="isPresent{}" id="isPresent{}" checked{}>
  <label>Mark as Present</label>
</div>
            '''.format(item.pk, item.pk, disabled_attr)
    else:
        is_present = '''
                            <div class="ui checkbox">
                  <input type="checkbox" name="isPresent{}" id="isPresent{}"{}>
                  <label>Mark as Present</label>
                </div>
                            '''.format(item.pk, item.pk, disabled_attr)

    reason = '''<div class="ui tiny input fluid">
  <input type="text" placeholder="Reason for Absent" name="reason{}" id="reason{}" value = "{}"{}>
</div>
            '''.format(item.pk, item.pk, escape(item.absentReason or ''), disabled_attr)

    if is_approved_leave:
        action = '<span class="ui tiny blue label">Protected</span>'
    elif is_holiday:
        action = '<span class="ui tiny teal label">Holiday</span>'

    return [
        images,
        escape(item.studentID.name),
        escape(item.studentID.roll or 'N/A'),
        status_chip,
        is_present,
        reason,
        escape(item.lastEditedBy or 'N/A'),
        escape(item.lastUpdatedOn.strftime('%d-%m-%Y %I:%M %p') if item.lastUpdatedOn else 'N/A'),
        action,
    ]


def _student_attendance_status_priority(status):
    ranking = {
        ATTENDANCE_STATUS_ABSENT: 1,
        ATTENDANCE_STATUS_LEAVE: 2,
        'present': 3,
        ATTENDANCE_STATUS_HOLIDAY: 4,
    }
    return ranking.get(status, 0)


def _student_attendance_status_for_row(row):
    return attendance_status_from_values(
        is_present=row.isPresent,
        absent_reason=row.absentReason,
        is_holiday=row.isHoliday,
        attendance_status=row.attendanceStatus,
    )


def _student_attendance_day_map(qs):
    day_map = {}
    for row in qs:
        if not row.attendanceDate:
            continue
        day_key = row.attendanceDate.date()
        status = _student_attendance_status_for_row(row)
        previous = day_map.get(day_key)
        if (
            not previous
            or _student_attendance_status_priority(status) > _student_attendance_status_priority(previous['status'])
            or (
                _student_attendance_status_priority(status) == _student_attendance_status_priority(previous['status'])
                and row.id < previous['row'].id
            )
        ):
            day_map[day_key] = {'status': status, 'row': row}
    return day_map


class TakeStudentAttendanceByClassJson(BaseDatatableView):
    order_columns = ['studentID.photo', 'studentID.name', 'studentID.roll', 'attendanceStatus', 'isPresent', 'absentReason', 'lastEditedBy', 'lastUpdatedOn']

    def _get_or_create_student_attendance(self, *, student, attendance_date, standard_id, session_id, leave_obj=None,
                                          holiday_obj=None,
                                          subject_id=None, by_subject=False):
        queryset = StudentAttendance.objects.filter(
            isDeleted=False,
            studentID_id=student.id,
            attendanceDate__date=attendance_date.date(),
            standardID_id=standard_id,
            bySubject=by_subject,
            sessionID_id=session_id,
        )
        if by_subject:
            queryset = queryset.filter(subjectID_id=subject_id)
        else:
            queryset = queryset.filter(subjectID__isnull=True)

        attendance_obj = queryset.order_by('id').first()
        if attendance_obj:
            return attendance_obj

        leave_reason = leave_application_note(leave_obj) if leave_obj else ''
        holiday_reason = holiday_note(holiday_obj) if holiday_obj else ''
        attendance_obj = StudentAttendance.objects.create(
            studentID_id=student.id,
            attendanceDate=attendance_date,
            subjectID_id=subject_id if by_subject else None,
            standardID_id=standard_id,
            sessionID_id=session_id,
            schoolID_id=student.schoolID_id,
            isPresent=False,
            bySubject=by_subject,
            isHoliday=bool(holiday_obj),
            absentReason=holiday_reason or leave_reason,
            attendanceStatus=ATTENDANCE_STATUS_HOLIDAY if holiday_obj else (ATTENDANCE_STATUS_LEAVE if leave_obj else ATTENDANCE_STATUS_ABSENT),
            leaveDurationType=None if holiday_obj else (leave_obj.durationType if leave_obj else None),
            sourceHoliday=holiday_obj,
            holidaySyncCreatedAttendance=bool(holiday_obj),
            sourceLeaveApplication=leave_obj,
            leaveSyncCreatedAttendance=bool(leave_obj and not holiday_obj),
        )
        pre_save_with_user.send(sender=StudentAttendance, instance=attendance_obj, user=self.request.user.pk)
        return attendance_obj

    def _apply_approved_leave_to_attendance(self, attendance_obj, leave_obj):
        if not leave_obj:
            return
        if attendance_obj.isHoliday or attendance_obj.attendanceStatus == ATTENDANCE_STATUS_HOLIDAY:
            return
        leave_reason = leave_application_note(leave_obj)
        if attendance_obj.isPresent or not attendance_obj.absentReason or attendance_obj.attendanceStatus != ATTENDANCE_STATUS_LEAVE:
            apply_attendance_status(
                attendance_obj,
                is_present=False,
                absent_reason=leave_reason,
                attendance_status=ATTENDANCE_STATUS_LEAVE,
            )
            attendance_obj.leaveDurationType = leave_obj.durationType
            attendance_obj.sourceLeaveApplication = leave_obj
            pre_save_with_user.send(sender=StudentAttendance, instance=attendance_obj, user=self.request.user.pk)
            attendance_obj.save()

    def _apply_holiday_to_attendance(self, attendance_obj, holiday_obj):
        if not holiday_obj:
            return
        if attendance_obj.sourceHoliday_id != holiday_obj.id:
            attendance_obj.holidaySyncPreviousIsPresent = attendance_obj.isPresent
            attendance_obj.holidaySyncPreviousIsHoliday = attendance_obj.isHoliday
            attendance_obj.holidaySyncPreviousAbsentReason = attendance_obj.absentReason
            attendance_obj.holidaySyncPreviousAttendanceStatus = attendance_obj.attendanceStatus
            attendance_obj.holidaySyncPreviousLeaveDurationType = attendance_obj.leaveDurationType
        attendance_obj.isPresent = False
        attendance_obj.isHoliday = True
        attendance_obj.attendanceStatus = ATTENDANCE_STATUS_HOLIDAY
        attendance_obj.absentReason = holiday_note(holiday_obj)
        attendance_obj.leaveDurationType = None
        attendance_obj.sourceHoliday = holiday_obj
        pre_save_with_user.send(sender=StudentAttendance, instance=attendance_obj, user=self.request.user.pk)
        attendance_obj.save()

    @transaction.atomic
    def get_initial_queryset(self):
        try:
            mode = self.request.GET.get("mode")

            if mode == "ByClass":
                standard = self.request.GET.get("standard")
                aDate = self.request.GET.get("aDate")
                aDate = datetime.strptime(aDate, '%d/%m/%Y')
                students = Student.objects.select_related().filter(isDeleted__exact=False, standardID_id=int(standard),
                                                                   sessionID_id=self.request.session["current_session"][
                                                                       "Id"]).order_by('roll')
                leave_map = approved_leave_map_for_date(
                    session_id=self.request.session["current_session"]["Id"],
                    role='student',
                    date_value=aDate.date(),
                    ids=[s.id for s in students]
                )
                pending_leave_map = pending_leave_map_for_date(
                    session_id=self.request.session["current_session"]["Id"],
                    role='student',
                    date_value=aDate.date(),
                    ids=[s.id for s in students]
                )
                self.pending_leave_map = pending_leave_map
                holiday_obj = holiday_for_date(
                    session_id=self.request.session["current_session"]["Id"],
                    target_date=aDate.date(),
                    applies_to='students',
                )
                attendance_ids = []
                for s in students:
                    leave_obj = None if holiday_obj else leave_map.get(s.id)
                    attendance_obj = self._get_or_create_student_attendance(
                        student=s,
                        attendance_date=aDate,
                        standard_id=int(standard),
                        session_id=self.request.session["current_session"]["Id"],
                        leave_obj=leave_obj,
                        holiday_obj=holiday_obj,
                        by_subject=False,
                    )
                    self._apply_holiday_to_attendance(attendance_obj, holiday_obj)
                    self._apply_approved_leave_to_attendance(attendance_obj, leave_obj)
                    attendance_ids.append(attendance_obj.id)

                return StudentAttendance.objects.select_related().filter(isDeleted__exact=False, bySubject=False,
                                                                         sessionID_id=
                                                                         self.request.session["current_session"][
                                                                             "Id"], pk__in=attendance_ids,
                                                                         standardID_id=int(standard)).order_by('studentID__roll', 'id')
            elif mode == "BySubject":
                subjects = self.request.GET.get("subjects")

                sDate = self.request.GET.get("sDate")
                sDate = datetime.strptime(sDate, '%d/%m/%Y')
                try:
                    obj = AssignSubjectsToClass.objects.get(pk=int(subjects), isDeleted=False)
                    students = Student.objects.select_related().filter(isDeleted__exact=False,
                                                                       standardID_id=obj.standardID_id,
                                                                       sessionID_id=
                                                                       self.request.session["current_session"][
                                                                           "Id"]).order_by('roll')
                    leave_map = approved_leave_map_for_date(
                        session_id=self.request.session["current_session"]["Id"],
                        role='student',
                        date_value=sDate.date(),
                        ids=[s.id for s in students]
                    )
                    pending_leave_map = pending_leave_map_for_date(
                        session_id=self.request.session["current_session"]["Id"],
                        role='student',
                        date_value=sDate.date(),
                        ids=[s.id for s in students]
                    )
                    self.pending_leave_map = pending_leave_map
                    holiday_obj = holiday_for_date(
                        session_id=self.request.session["current_session"]["Id"],
                        target_date=sDate.date(),
                        applies_to='students',
                    )
                    attendance_ids = []
                    for s in students:
                        leave_obj = None if holiday_obj else leave_map.get(s.id)
                        attendance_obj = self._get_or_create_student_attendance(
                            student=s,
                            attendance_date=sDate,
                            standard_id=obj.standardID_id,
                            session_id=self.request.session["current_session"]["Id"],
                            leave_obj=leave_obj,
                            holiday_obj=holiday_obj,
                            subject_id=obj.subjectID_id,
                            by_subject=True,
                        )
                        self._apply_holiday_to_attendance(attendance_obj, holiday_obj)
                        self._apply_approved_leave_to_attendance(attendance_obj, leave_obj)
                        attendance_ids.append(attendance_obj.id)
                    return StudentAttendance.objects.select_related().filter(isDeleted__exact=False, bySubject=True,
                                                                             sessionID_id=
                                                                             self.request.session["current_session"][
                                                                                 "Id"], pk__in=attendance_ids,
                                                                             subjectID_id=obj.subjectID_id,
                                                                             standardID_id=obj.standardID_id).order_by('studentID__roll', 'id')
                except:
                    return StudentAttendance.objects.none()
            else:
                return StudentAttendance.objects.none()
        except:
            return StudentAttendance.objects.none()

    def filter_queryset(self, qs):

        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(standardID__name__icontains=search)
                | Q(lastEditedBy__icontains=search) | Q(lastUpdatedOn__icontains=search)
            )

        return qs

    def prepare_results(self, qs):
        json_data = []
        for item in qs:
            pending_leave = None
            if item.studentID_id and item.attendanceDate:
                pending_map = getattr(self, 'pending_leave_map', None)
                if pending_map is not None:
                    pending_leave = pending_map.get(item.studentID_id)
                elif status != ATTENDANCE_STATUS_LEAVE:
                    pending_leave = pending_leave_map_for_date(
                        session_id=item.sessionID_id,
                        role='student',
                        date_value=item.attendanceDate.date(),
                        ids=[item.studentID_id],
                    ).get(item.studentID_id)
            json_data.append(_student_attendance_datatable_row(item, pending_leave=pending_leave))

        return json_data


@transaction.atomic
@csrf_exempt
@login_required
def add_student_attendance_by_class(request):
    if request.method == 'POST':
        id = request.POST.get("id")
        isPresent = request.POST.get("isPresent")
        reason = request.POST.get("reason")
        try:
            instance = StudentAttendance.objects.select_related('studentID').get(pk=int(id))
            if instance.isHoliday or instance.attendanceStatus == ATTENDANCE_STATUS_HOLIDAY:
                return _api_response(
                    {'status': 'error', 'message': 'Cannot update attendance on a holiday.', 'color': 'orange'},
                    safe=False)
            if isPresent == 'true':
                isPresent = True
            else:
                isPresent = False
            if isPresent:
                leave_obj = approved_leave_for_date(
                    session_id=instance.sessionID_id,
                    role='student',
                    date_value=instance.attendanceDate.date() if instance.attendanceDate else None,
                    student_id=instance.studentID_id
                )
                if leave_obj:
                    return _api_response(
                        {'status': 'error', 'message': 'Cannot mark present on approved leave date.', 'color': 'orange'},
                        safe=False)
            apply_attendance_status(instance, is_present=isPresent, absent_reason=reason)
            pre_save_with_user.send(sender=StudentAttendance, instance=instance, user=request.user.pk)
            instance.save()
            instance.refresh_from_db()
            return _api_response(
                {
                    'status': 'success',
                    'message': 'Attendance added successfully.',
                    'color': 'success',
                    'data': {'row': _student_attendance_datatable_row(instance)},
                },
                safe=False)
        except:

            return _api_response({'status': 'error'}, safe=False)


@transaction.atomic
@csrf_exempt
@login_required
def add_student_attendance_bulk_by_class(request):
    if request.method != 'POST':
        return _api_response({'status': 'error', 'message': 'Invalid request method.'}, safe=False)

    try:
        raw_entries = request.POST.get('entries', '[]')
        entries = json.loads(raw_entries)
        if not isinstance(entries, list) or len(entries) == 0:
            return _api_response({'status': 'error', 'message': 'No attendance entries found.', 'color': 'red'}, safe=False)
    except Exception:
        return _api_response({'status': 'error', 'message': 'Invalid attendance payload.', 'color': 'red'}, safe=False)

    updated_count = 0
    blocked_students = []
    current_session_id = request.session["current_session"]["Id"]

    for entry in entries:
        try:
            attendance_id = int(entry.get('id'))
        except Exception:
            continue

        is_present = bool(entry.get('isPresent'))
        reason = (entry.get('reason') or '').strip()

        try:
            instance = StudentAttendance.objects.select_related('studentID').get(
                pk=attendance_id,
                isDeleted=False,
                sessionID_id=current_session_id
            )
        except StudentAttendance.DoesNotExist:
            continue

        if instance.isHoliday or instance.attendanceStatus == ATTENDANCE_STATUS_HOLIDAY:
            continue

        if is_present:
            leave_obj = approved_leave_for_date(
                session_id=instance.sessionID_id,
                role='student',
                date_value=instance.attendanceDate.date() if instance.attendanceDate else None,
                student_id=instance.studentID_id
            )
            if leave_obj:
                blocked_students.append(instance.studentID.name if instance.studentID else f'ID {attendance_id}')
                continue

        apply_attendance_status(instance, is_present=is_present, absent_reason='' if is_present else reason)
        pre_save_with_user.send(sender=StudentAttendance, instance=instance, user=request.user.pk)
        instance.save()
        updated_count += 1

    if updated_count == 0 and blocked_students:
        return _api_response(
            {'status': 'error', 'message': 'Could not update attendance. Some students are on approved leave.', 'color': 'orange'},
            safe=False
        )

    if blocked_students:
        blocked_preview = ', '.join(blocked_students[:3])
        extra_text = f' (blocked: {blocked_preview}{"..." if len(blocked_students) > 3 else ""})'
        return _api_response(
            {'status': 'success',
             'message': f'Updated {updated_count} attendance record(s). {len(blocked_students)} skipped due to approved leave{extra_text}.',
             'color': 'orange'},
            safe=False
        )

    return _api_response(
        {'status': 'success', 'message': f'Updated {updated_count} attendance record(s) successfully.', 'color': 'success'},
        safe=False
    )


class StudentAttendanceHistoryByDateRangeJson(BaseDatatableView):
    order_columns = ['photo', 'name', 'roll']

    @transaction.atomic
    def get_initial_queryset(self):
        try:
            dateRangeStandard = self.request.GET.get("dateRangeStandard")
            dateRangeSubject = self.request.GET.get("dateRangeSubject")

            return Student.objects.select_related().filter(isDeleted__exact=False, standardID_id=int(dateRangeStandard),
                                                           sessionID_id=self.request.session["current_session"][
                                                               "Id"]).order_by('roll')
            # for s in students:
            #     try:
            #         StudentAttendance.objects.get(studentID_id=s.id, attendanceDate__icontains=aDate,
            #                                       standardID_id=int(standard), bySubject=False,
            #                                       sessionID_id=self.request.session["current_session"]["Id"])
            #
            #     except:
            #         instance = StudentAttendance.objects.create(studentID_id=s.id, attendanceDate=aDate,
            #                                                     standardID_id=int(standard), isPresent=False,
            #                                                     bySubject=False, )
            #         pre_save_with_user.send(sender=StudentAttendance, instance=instance, user=self.request.user.pk)
            #
            # return StudentAttendance.objects.select_related().filter(isDeleted__exact=False, bySubject=False,
            #                                                          sessionID_id=
            #                                                          self.request.session["current_session"][
            #                                                              "Id"], attendanceDate__icontains=aDate,
            #                                                          standardID_id=int(standard))

            # if dateRangeSubject == "All":
            #     return StudentAttendance.objects.select_related().filter(isDeleted__exact=False, bySubject=False,
            #                                                              standardID_id=int(dateRangeStandard),
            #                                                             sessionID_id=
            #                                                             self.request.session["current_session"][
            #                                                                 "Id"],
            #                                                             attendanceDate__range=[dateRangeStartDate,
            #                                                                                    dateRangeEndDate+timedelta(days=1)])
            # else:
            #     return StudentAttendance.objects.select_related().filter(isDeleted__exact=False, bySubject=True,
            #                                                              standardID_id=int(dateRangeStandard),
            #                                                             sessionID_id=
            #                                                             self.request.session["current_session"][
            #                                                                 "Id"],
            #                                                             attendanceDate__range=[dateRangeStartDate,
            #                                                                                    dateRangeEndDate+timedelta(days=1)],
            #                                                             subjectID_id=int(dateRangeSubject))
        except:
            return Student.objects.none()

    def filter_queryset(self, qs):

        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(name__icontains=search) | Q(roll__icontains=search)
                | Q(standardID__name__icontains=search)
                | Q(lastEditedBy__icontains=search) | Q(lastUpdatedOn__icontains=search)
            )

        return qs

    def prepare_results(self, qs):
        dateRangeStandard = self.request.GET.get("dateRangeStandard")
        dateRangeSubject = self.request.GET.get("dateRangeSubject")
        dateRangeStartDate = self.request.GET.get("dateRangeStartDate")
        dateRangeEndDate = self.request.GET.get("dateRangeEndDate")
        dateRangeStartDate = datetime.strptime(dateRangeStartDate, '%d/%m/%Y')
        dateRangeEndDate = datetime.strptime(dateRangeEndDate, '%d/%m/%Y')
        start_date = dateRangeStartDate.date()
        end_date = dateRangeEndDate.date()
        json_data = []
        for item in qs:
            images = _avatar_image_html(item.photo)
            attendance_filter = {
                'studentID_id': item.id,
                'isDeleted': False,
                'attendanceDate__date__gte': start_date,
                'attendanceDate__date__lte': end_date,
                'standardID_id': int(dateRangeStandard),
                'sessionID_id': self.request.session["current_session"]["Id"],
            }
            if dateRangeSubject == "all":
                attendance_qs = StudentAttendance.objects.filter(
                    bySubject=False,
                    **attendance_filter,
                ).order_by('attendanceDate', 'id')
            else:
                attendance_qs = StudentAttendance.objects.filter(
                    Q(bySubject=True, subjectID_id=int(dateRangeSubject))
                    | Q(bySubject=False, attendanceStatus__in=[ATTENDANCE_STATUS_LEAVE, ATTENDANCE_STATUS_HOLIDAY]),
                    **attendance_filter,
                ).order_by('attendanceDate', 'id')

            day_map = _student_attendance_day_map(attendance_qs)
            present_count = sum(1 for data in day_map.values() if data['status'] == 'present')
            leave_count = sum(1 for data in day_map.values() if data['status'] == ATTENDANCE_STATUS_LEAVE)
            absent_count = sum(1 for data in day_map.values() if data['status'] == ATTENDANCE_STATUS_ABSENT)
            holiday_count = sum(1 for data in day_map.values() if data['status'] == ATTENDANCE_STATUS_HOLIDAY)
            working_days = present_count + absent_count + leave_count
            recorded_days = working_days + holiday_count

            if working_days != 0:
                percentage = present_count / working_days * 100
            else:
                # Handle the case when the denominator is zero
                percentage = 0

            roll_raw = '' if item.roll is None else str(item.roll).strip()
            if roll_raw == '':
                roll_value = 'N/A'
            else:
                try:
                    parsed_roll = float(roll_raw)
                    roll_value = int(parsed_roll) if parsed_roll.is_integer() else parsed_roll
                except (TypeError, ValueError):
                    roll_value = escape(roll_raw)

            json_data.append([
                images,
                escape(item.name),
                roll_value,
                present_count,
                absent_count,
                leave_count,
                holiday_count,
                working_days,
                recorded_days,
                round(percentage, 2)

            ])

        return json_data


class StudentAttendanceHistoryByDateRangeAndStudentJson(BaseDatatableView):
    order_columns = ['attendanceDate', 'attendanceStatus', 'isPresent', 'isPresent', 'absentReason', 'lastEditedBy', 'lastUpdatedOn']

    @transaction.atomic
    def get_initial_queryset(self):
        try:
            ByStudentStandard = self.request.GET.get("ByStudentStandard")
            ByStudentSubject = self.request.GET.get("ByStudentSubject")
            ByStudentStudent = self.request.GET.get("ByStudentStudent")
            ByStudentStartDate = self.request.GET.get("ByStudentStartDate")
            ByStudentEndDate = self.request.GET.get("ByStudentEndDate")
            ByStudentStartDate = datetime.strptime(ByStudentStartDate, '%d/%m/%Y')
            ByStudentEndDate = datetime.strptime(ByStudentEndDate, '%d/%m/%Y')
            base_filter = {
                'isDeleted__exact': False,
                'studentID_id': int(ByStudentStudent),
                'attendanceDate__date__gte': ByStudentStartDate.date(),
                'attendanceDate__date__lte': ByStudentEndDate.date(),
                'sessionID_id': self.request.session["current_session"]["Id"],
            }
            if ByStudentStandard:
                base_filter['standardID_id'] = int(ByStudentStandard)
            if ByStudentSubject == "all":
                return StudentAttendance.objects.select_related().filter(
                    bySubject=False,
                    **base_filter,
                ).order_by('attendanceDate', 'id')
            else:
                return StudentAttendance.objects.select_related().filter(
                    Q(bySubject=True, subjectID_id=int(ByStudentSubject))
                    | Q(bySubject=False, attendanceStatus__in=[ATTENDANCE_STATUS_LEAVE, ATTENDANCE_STATUS_HOLIDAY]),
                    **base_filter,
                ).order_by('attendanceDate', 'id')


        except:
            return StudentAttendance.objects.none()

    def filter_queryset(self, qs):

        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(attendanceDate__icontains=search) | Q(isPresent__icontains=search)
                | Q(lastEditedBy__icontains=search) | Q(lastUpdatedOn__icontains=search)
            )

        return qs

    def prepare_results(self, qs):
        json_data = []
        day_map = _student_attendance_day_map(qs)
        for day_key in sorted(day_map.keys()):
            item = day_map[day_key]['row']
            status = day_map[day_key]['status']
            status_label = 'Present'
            if status == 'present':
                Present = 'Yes'
                Absent = 'No'
                reason = item.absentReason or ''
            elif status == ATTENDANCE_STATUS_HOLIDAY:
                status_label = 'Holiday'
                Present = 'No'
                Absent = 'No'
                reason = item.absentReason or 'Holiday'
            else:
                status_label = 'Leave' if status == ATTENDANCE_STATUS_LEAVE else 'Absent'
                Present = 'No'
                Absent = 'No' if status == ATTENDANCE_STATUS_LEAVE else 'Yes'
                reason = item.absentReason or ''
                if status == ATTENDANCE_STATUS_LEAVE and item.leaveDurationType:
                    reason = f'{reason} - {leave_duration_label(item.leaveDurationType)}' if reason else leave_duration_label(item.leaveDurationType)

            json_data.append([
                escape(day_key.strftime('%d-%m-%Y')),
                escape(status_label),
                escape(Present),
                escape(Absent),
                escape(reason),
                escape(item.lastEditedBy or 'N/A'),
                escape(item.lastUpdatedOn.strftime('%d-%m-%Y %I:%M %p') if item.lastUpdatedOn else 'N/A'),

            ])

        return json_data


def _staff_attendance_datatable_row(item, *, pending_leave=None):
    images = _avatar_image_html(item.teacherID.photo if item.teacherID else None)
    action = '''<button class="ui mini primary button" onclick="pushAttendance({})">
  Save
</button>'''.format(item.pk)

    status = attendance_status_from_values(
        is_present=item.isPresent,
        absent_reason=item.absentReason,
        is_holiday=item.isHoliday,
        attendance_status=item.attendanceStatus,
    )

    status_chip = '<span class="ui tiny grey label">Absent</span>'
    if status == 'present':
        status_chip = '<span class="ui tiny green label">Present</span>'
    elif status == ATTENDANCE_STATUS_LEAVE:
        duration_text = leave_duration_label(item.leaveDurationType)
        status_chip = f'<span class="ui tiny blue label">Approved Leave</span><div class="ui tiny basic blue label" style="margin-top:3px;">{escape(duration_text)}</div>'
    elif status == ATTENDANCE_STATUS_HOLIDAY:
        status_chip = '<span class="ui tiny teal label">Holiday</span>'
    elif pending_leave:
        duration_text = leave_duration_label(pending_leave.durationType)
        leave_name = pending_leave.leaveTypeID.name if pending_leave.leaveTypeID else 'Leave'
        status_chip = f'<span class="ui tiny orange label">Pending Leave</span><div class="ui tiny basic orange label" style="margin-top:3px;">{escape(leave_name)} - {escape(duration_text)}</div>'

    is_approved_leave = status == ATTENDANCE_STATUS_LEAVE
    is_holiday = status == ATTENDANCE_STATUS_HOLIDAY
    disabled_attr = ' disabled data-leave-locked="true"' if is_approved_leave else ''
    if is_holiday:
        disabled_attr = ' disabled data-holiday-locked="true"'
    if item.isPresent:
        is_present = '''
            <div class="ui checkbox">
  <input type="checkbox" name="isPresent{}" id="isPresent{}" checked{}>
  <label>Mark as Present</label>
</div>
            '''.format(item.pk, item.pk, disabled_attr)
    else:
        is_present = '''
                            <div class="ui checkbox">
                  <input type="checkbox" name="isPresent{}" id="isPresent{}"{}>
                  <label>Mark as Present</label>
                </div>
                            '''.format(item.pk, item.pk, disabled_attr)

    reason = '''<div class="ui tiny input fluid">
  <input type="text" placeholder="Reason for Absent" name="reason{}" id="reason{}" value = "{}"{}>
</div>
            '''.format(item.pk, item.pk, escape(item.absentReason or ''), disabled_attr)

    if is_approved_leave:
        action = '<span class="ui tiny blue label">Protected</span>'
    elif is_holiday:
        action = '<span class="ui tiny teal label">Holiday</span>'

    return [
        images,
        escape(item.teacherID.name if item.teacherID else 'N/A'),
        escape(item.teacherID.staffType if item.teacherID else 'N/A'),
        escape(item.teacherID.employeeCode if item.teacherID and item.teacherID.employeeCode else 'N/A'),
        status_chip,
        is_present,
        reason,
        escape(item.lastEditedBy or 'N/A'),
        escape(item.lastUpdatedOn.strftime('%d-%m-%Y %I:%M %p') if item.lastUpdatedOn else 'N/A'),
        action,
    ]


class TakeTeacherAttendanceJson(BaseDatatableView):
    order_columns = ['teacherID.photo', 'teacherID.name', 'teacherID.staffType', 'teacherID.employeeCode', 'attendanceStatus', 'isPresent',
                     'absentReason', 'lastEditedBy', 'lastUpdatedOn']

    def _get_or_create_teacher_attendance(self, *, teacher, attendance_date, session_id, leave_obj=None, holiday_obj=None):
        attendance_obj = TeacherAttendance.objects.filter(
            isDeleted=False,
            sessionID_id=session_id,
            teacherID_id=teacher.id,
            attendanceDate__date=attendance_date.date(),
        ).order_by('id').first()
        if attendance_obj:
            return attendance_obj

        leave_reason = leave_application_note(leave_obj) if leave_obj else ''
        holiday_reason = holiday_note(holiday_obj) if holiday_obj else ''
        attendance_obj = TeacherAttendance.objects.create(
            attendanceDate=attendance_date,
            isDeleted=False,
            teacherID_id=teacher.id,
            sessionID_id=session_id,
            schoolID_id=teacher.schoolID_id,
            isPresent=False,
            isHoliday=bool(holiday_obj),
            absentReason=holiday_reason or leave_reason,
            attendanceStatus=ATTENDANCE_STATUS_HOLIDAY if holiday_obj else (ATTENDANCE_STATUS_LEAVE if leave_obj else ATTENDANCE_STATUS_ABSENT),
            leaveDurationType=None if holiday_obj else (leave_obj.durationType if leave_obj else None),
            sourceHoliday=holiday_obj,
            holidaySyncCreatedAttendance=bool(holiday_obj),
            sourceLeaveApplication=leave_obj,
            leaveSyncCreatedAttendance=bool(leave_obj and not holiday_obj),
        )
        pre_save_with_user.send(sender=TeacherAttendance, instance=attendance_obj, user=self.request.user.pk)
        return attendance_obj

    def _apply_approved_leave_to_attendance(self, attendance_obj, leave_obj):
        if not leave_obj:
            return
        if attendance_obj.isHoliday or attendance_obj.attendanceStatus == ATTENDANCE_STATUS_HOLIDAY:
            return
        leave_reason = leave_application_note(leave_obj)
        if attendance_obj.isPresent or not attendance_obj.absentReason or attendance_obj.attendanceStatus != ATTENDANCE_STATUS_LEAVE:
            apply_attendance_status(
                attendance_obj,
                is_present=False,
                absent_reason=leave_reason,
                attendance_status=ATTENDANCE_STATUS_LEAVE,
            )
            attendance_obj.leaveDurationType = leave_obj.durationType
            attendance_obj.sourceLeaveApplication = leave_obj
            pre_save_with_user.send(sender=TeacherAttendance, instance=attendance_obj, user=self.request.user.pk)
            attendance_obj.save()

    def _apply_holiday_to_attendance(self, attendance_obj, holiday_obj):
        if not holiday_obj:
            return
        if attendance_obj.sourceHoliday_id != holiday_obj.id:
            attendance_obj.holidaySyncPreviousIsPresent = attendance_obj.isPresent
            attendance_obj.holidaySyncPreviousIsHoliday = attendance_obj.isHoliday
            attendance_obj.holidaySyncPreviousAbsentReason = attendance_obj.absentReason
            attendance_obj.holidaySyncPreviousAttendanceStatus = attendance_obj.attendanceStatus
            attendance_obj.holidaySyncPreviousLeaveDurationType = attendance_obj.leaveDurationType
        attendance_obj.isPresent = False
        attendance_obj.isHoliday = True
        attendance_obj.attendanceStatus = ATTENDANCE_STATUS_HOLIDAY
        attendance_obj.absentReason = holiday_note(holiday_obj)
        attendance_obj.leaveDurationType = None
        attendance_obj.sourceHoliday = holiday_obj
        pre_save_with_user.send(sender=TeacherAttendance, instance=attendance_obj, user=self.request.user.pk)
        attendance_obj.save()

    @transaction.atomic
    def get_initial_queryset(self):
        try:
            aDate = self.request.GET.get("aDate")
            aDate = datetime.strptime(aDate, '%d/%m/%Y')
            teachers = TeacherDetail.objects.select_related().filter(isDeleted__exact=False,
                                                                     sessionID_id=
                                                                     self.request.session["current_session"][
                                                                         "Id"])
            leave_map = approved_leave_map_for_date(
                session_id=self.request.session["current_session"]["Id"],
                role='teacher',
                date_value=aDate.date(),
                ids=[s.id for s in teachers]
            )
            pending_leave_map = pending_leave_map_for_date(
                session_id=self.request.session["current_session"]["Id"],
                role='teacher',
                date_value=aDate.date(),
                ids=[s.id for s in teachers]
            )
            self.pending_leave_map = pending_leave_map
            holiday_obj = holiday_for_date(
                session_id=self.request.session["current_session"]["Id"],
                target_date=aDate.date(),
                applies_to='teachers',
            )
            attendance_ids = []
            for s in teachers:
                leave_obj = None if holiday_obj else leave_map.get(s.id)
                attendance_obj = self._get_or_create_teacher_attendance(
                    teacher=s,
                    attendance_date=aDate,
                    session_id=self.request.session["current_session"]["Id"],
                    leave_obj=leave_obj,
                    holiday_obj=holiday_obj,
                )
                self._apply_holiday_to_attendance(attendance_obj, holiday_obj)
                self._apply_approved_leave_to_attendance(attendance_obj, leave_obj)
                attendance_ids.append(attendance_obj.id)

            return TeacherAttendance.objects.select_related().filter(isDeleted__exact=False,
                                                                     pk__in=attendance_ids,
                                                                     sessionID_id=
                                                                     self.request.session["current_session"][
                                                                         "Id"]).order_by('teacherID__name', 'id')

        except:
            return TeacherAttendance.objects.none()

    def filter_queryset(self, qs):

        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(teacherID__name__icontains=search)
                | Q(teacherID__employeeCode__icontains=search)
                | Q(teacherID__staffType__icontains=search)
                | Q(lastEditedBy__icontains=search) | Q(lastUpdatedOn__icontains=search)
            )

        return qs

    def prepare_results(self, qs):
        json_data = []
        for item in qs:
            pending_leave = None
            if item.teacherID_id and item.attendanceDate:
                pending_map = getattr(self, 'pending_leave_map', None)
                if pending_map is not None:
                    pending_leave = pending_map.get(item.teacherID_id)
                else:
                    pending_leave = pending_leave_map_for_date(
                        session_id=item.sessionID_id,
                        role='teacher',
                        date_value=item.attendanceDate.date(),
                        ids=[item.teacherID_id],
                    ).get(item.teacherID_id)
            json_data.append(_staff_attendance_datatable_row(item, pending_leave=pending_leave))

        return json_data


@transaction.atomic
@csrf_exempt
@login_required
def add_staff_attendance_api(request):
    if request.method == 'POST':
        id = request.POST.get("id")
        isPresent = request.POST.get("isPresent")
        reason = request.POST.get("reason")
        try:
            instance = TeacherAttendance.objects.select_related('teacherID').get(pk=int(id))
            if instance.isHoliday or instance.attendanceStatus == ATTENDANCE_STATUS_HOLIDAY:
                return _api_response(
                    {'status': 'error', 'message': 'Cannot update attendance on a holiday.', 'color': 'orange'},
                    safe=False)
            if isPresent == 'true':
                isPresent = True
            else:
                isPresent = False
            if isPresent:
                leave_obj = approved_leave_for_date(
                    session_id=instance.sessionID_id,
                    role='teacher',
                    date_value=instance.attendanceDate.date() if instance.attendanceDate else None,
                    teacher_id=instance.teacherID_id
                )
                if leave_obj:
                    return _api_response(
                        {'status': 'error', 'message': 'Cannot mark present on approved leave date.', 'color': 'orange'},
                        safe=False)
            apply_attendance_status(instance, is_present=isPresent, absent_reason=reason)
            pre_save_with_user.send(sender=TeacherAttendance, instance=instance, user=request.user.pk)
            instance.save()
            instance.refresh_from_db()
            return _api_response(
                {
                    'status': 'success',
                    'message': 'Attendance added successfully.',
                    'color': 'success',
                    'data': {'row': _staff_attendance_datatable_row(instance)},
                },
                safe=False)
        except:

            return _api_response({'status': 'error'}, safe=False)


@transaction.atomic
@csrf_exempt
@login_required
def add_staff_attendance_bulk_api(request):
    if request.method != 'POST':
        return _api_response({'status': 'error', 'message': 'Invalid request method.'}, safe=False)

    try:
        raw_entries = request.POST.get('entries', '[]')
        entries = json.loads(raw_entries)
        if not isinstance(entries, list) or len(entries) == 0:
            return _api_response({'status': 'error', 'message': 'No attendance entries found.', 'color': 'red'}, safe=False)
    except Exception:
        return _api_response({'status': 'error', 'message': 'Invalid attendance payload.', 'color': 'red'}, safe=False)

    updated_count = 0
    blocked_staff = []
    current_session_id = request.session["current_session"]["Id"]

    for entry in entries:
        try:
            attendance_id = int(entry.get('id'))
        except Exception:
            continue

        is_present = bool(entry.get('isPresent'))
        reason = (entry.get('reason') or '').strip()

        try:
            instance = TeacherAttendance.objects.select_related('teacherID').get(
                pk=attendance_id,
                isDeleted=False,
                sessionID_id=current_session_id
            )
        except TeacherAttendance.DoesNotExist:
            continue

        if instance.isHoliday or instance.attendanceStatus == ATTENDANCE_STATUS_HOLIDAY:
            continue

        if is_present:
            leave_obj = approved_leave_for_date(
                session_id=instance.sessionID_id,
                role='teacher',
                date_value=instance.attendanceDate.date() if instance.attendanceDate else None,
                teacher_id=instance.teacherID_id
            )
            if leave_obj:
                blocked_staff.append(instance.teacherID.name if instance.teacherID else f'ID {attendance_id}')
                continue

        apply_attendance_status(instance, is_present=is_present, absent_reason='' if is_present else reason)
        pre_save_with_user.send(sender=TeacherAttendance, instance=instance, user=request.user.pk)
        instance.save()
        updated_count += 1

    if updated_count == 0 and blocked_staff:
        return _api_response(
            {'status': 'error', 'message': 'Could not update attendance. Some staff are on approved leave.', 'color': 'orange'},
            safe=False
        )

    if blocked_staff:
        blocked_preview = ', '.join(blocked_staff[:3])
        extra_text = f' (blocked: {blocked_preview}{"..." if len(blocked_staff) > 3 else ""})'
        return _api_response(
            {'status': 'success',
             'message': f'Updated {updated_count} attendance record(s). {len(blocked_staff)} skipped due to approved leave{extra_text}.',
             'color': 'orange'},
            safe=False
        )

    return _api_response(
        {'status': 'success', 'message': f'Updated {updated_count} attendance record(s) successfully.', 'color': 'success'},
        safe=False
    )


class StaffAttendanceHistoryByDateRangeJson(BaseDatatableView):
    order_columns = ['photo', 'name', 'staffType', 'employeeCode']

    @transaction.atomic
    def get_initial_queryset(self):
        try:
            return TeacherDetail.objects.select_related().filter(isDeleted__exact=False,
                                                                 sessionID_id=self.request.session["current_session"][
                                                                     "Id"])
        except:
            return TeacherDetail.objects.none()

    def filter_queryset(self, qs):

        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(name__icontains=search) | Q(staffType__icontains=search)
                | Q(employeeCode__icontains=search)
                | Q(lastEditedBy__icontains=search) | Q(lastUpdatedOn__icontains=search)
            )

        return qs

    def prepare_results(self, qs):
        json_data = []
        try:
            dateRangeStartDate = self.request.GET.get("dateRangeStartDate")
            dateRangeEndDate = self.request.GET.get("dateRangeEndDate")
            dateRangeStartDate = datetime.strptime(dateRangeStartDate, '%d/%m/%Y')
            dateRangeEndDate = datetime.strptime(dateRangeEndDate, '%d/%m/%Y')
            start_date = dateRangeStartDate.date()
            end_date = dateRangeEndDate.date()

            for item in qs:
                images = _avatar_image_html(item.photo)
                attendance_qs = TeacherAttendance.objects.filter(
                    teacherID_id=item.id,
                    isDeleted=False,
                    sessionID_id=self.request.session["current_session"]["Id"],
                    attendanceDate__date__gte=start_date,
                    attendanceDate__date__lte=end_date,
                ).order_by('attendanceDate', 'id')

                day_map = _student_attendance_day_map(attendance_qs)
                approved_leaves = LeaveApplication.objects.select_related('leaveTypeID').filter(
                    isDeleted=False,
                    sessionID_id=self.request.session["current_session"]["Id"],
                    applicantRole='teacher',
                    teacherID_id=item.id,
                    status='approved',
                    startDate__lte=end_date,
                    endDate__gte=start_date,
                )
                for leave in approved_leaves:
                    day = max(start_date, leave.startDate)
                    end_day = min(end_date, leave.endDate)
                    while day <= end_day:
                        existing = day_map.get(day)
                        if not existing or _student_attendance_status_priority(existing['status']) < _student_attendance_status_priority(ATTENDANCE_STATUS_LEAVE):
                            day_map[day] = {'status': ATTENDANCE_STATUS_LEAVE, 'row': None}
                        day += timedelta(days=1)

                present_count = sum(1 for data in day_map.values() if data['status'] == 'present')
                leave_count = sum(1 for data in day_map.values() if data['status'] == ATTENDANCE_STATUS_LEAVE)
                absent_count = sum(1 for data in day_map.values() if data['status'] == ATTENDANCE_STATUS_ABSENT)
                holiday_count = sum(1 for data in day_map.values() if data['status'] == ATTENDANCE_STATUS_HOLIDAY)
                working_days = present_count + absent_count + leave_count
                recorded_days = working_days + holiday_count

                if working_days != 0:
                    percentage = present_count / working_days * 100
                else:
                    # Handle the case when the denominator is zero
                    percentage = 0

                json_data.append([
                    images,
                    escape(item.name),
                    escape(item.staffType),
                    escape(item.employeeCode),
                    present_count,
                    absent_count,
                    leave_count,
                    holiday_count,
                    working_days,
                    recorded_days,
                    round(percentage, 2)

                ])

        except:
            pass
        return json_data


class StaffAttendanceHistoryByDateRangeAndStaffJson(BaseDatatableView):
    order_columns = ['attendanceDate', 'attendanceStatus', 'isPresent', 'isPresent', 'isPresent', 'absentReason', 'lastEditedBy', 'lastUpdatedOn']

    @transaction.atomic
    def get_initial_queryset(self):
        try:
            ByStaffStaff = self.request.GET.get("ByStaffStaff")
            ByStudentStartDate = self.request.GET.get("ByStudentStartDate")
            ByStudentEndDate = self.request.GET.get("ByStudentEndDate")
            if not ByStaffStaff or not ByStudentStartDate or not ByStudentEndDate:
                return TeacherAttendance.objects.none()
            ByStudentStartDate = datetime.strptime(ByStudentStartDate, '%d/%m/%Y')
            ByStudentEndDate = datetime.strptime(ByStudentEndDate, '%d/%m/%Y')
            session_id = self.request.session["current_session"]["Id"]
            try:
                teacher_id = int(ByStaffStaff)
            except (TypeError, ValueError):
                return TeacherAttendance.objects.none()

            approved_leaves = LeaveApplication.objects.select_related('leaveTypeID').filter(
                isDeleted=False,
                sessionID_id=session_id,
                applicantRole='teacher',
                teacherID_id=teacher_id,
                status='approved',
                startDate__lte=ByStudentEndDate.date(),
                endDate__gte=ByStudentStartDate.date(),
            )
            for leave in approved_leaves:
                leave_reason = leave_application_note(leave)
                day = max(ByStudentStartDate.date(), leave.startDate)
                end_day = min(ByStudentEndDate.date(), leave.endDate)
                while day <= end_day:
                    attendance_dt = datetime(day.year, day.month, day.day)
                    exists = TeacherAttendance.objects.filter(
                        isDeleted=False,
                        sessionID_id=session_id,
                        teacherID_id=teacher_id,
                        attendanceDate__date=day,
                    ).exists()
                    if not exists:
                        instance = TeacherAttendance(
                            attendanceDate=attendance_dt,
                            isDeleted=False,
                            teacherID_id=teacher_id,
                            isPresent=False,
                            absentReason=leave_reason,
                            attendanceStatus=ATTENDANCE_STATUS_LEAVE,
                            leaveDurationType=leave.durationType,
                            sourceLeaveApplication=leave,
                            leaveSyncCreatedAttendance=True,
                        )
                        pre_save_with_user.send(sender=TeacherAttendance, instance=instance, user=self.request.user.pk)
                    day += timedelta(days=1)

            return TeacherAttendance.objects.select_related().filter(isDeleted__exact=False,
                                                                     teacherID_id=teacher_id,
                                                                     attendanceDate__date__gte=ByStudentStartDate.date(),
                                                                     attendanceDate__date__lte=ByStudentEndDate.date(),
                                                                     sessionID_id=
                                                                     session_id).order_by('attendanceDate')


        except:
            return TeacherAttendance.objects.none()

    def filter_queryset(self, qs):

        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(attendanceDate__icontains=search) | Q(isPresent__icontains=search)
                | Q(lastEditedBy__icontains=search) | Q(lastUpdatedOn__icontains=search)
            )

        return qs

    def prepare_results(self, qs):
        json_data = []
        day_map = _student_attendance_day_map(qs)
        for day_key in sorted(day_map.keys()):
            item = day_map[day_key]['row']
            status = day_map[day_key]['status']
            status_label = 'Present'
            if status == 'present':
                Present = 'Yes'
                Absent = 'No'
                Leave = 'No'
                reason = item.absentReason or ''
            elif status == ATTENDANCE_STATUS_HOLIDAY:
                status_label = 'Holiday'
                Present = 'No'
                Absent = 'No'
                Leave = 'No'
                reason = item.absentReason or 'Holiday'
            else:
                status_label = 'Leave' if status == ATTENDANCE_STATUS_LEAVE else 'Absent'
                Present = 'No'
                if status == ATTENDANCE_STATUS_LEAVE:
                    Absent = 'No'
                    Leave = 'Yes'
                else:
                    Absent = 'Yes'
                    Leave = 'No'
                reason = item.absentReason or ''
                if status == ATTENDANCE_STATUS_LEAVE and item.leaveDurationType:
                    reason = f'{reason} - {leave_duration_label(item.leaveDurationType)}' if reason else leave_duration_label(item.leaveDurationType)

            json_data.append([
                escape(day_key.strftime('%d-%m-%Y')),
                escape(status_label),
                escape(Present),
                escape(Absent),
                escape(Leave),
                escape(reason),
                escape(item.lastEditedBy or 'N/A'),
                escape(item.lastUpdatedOn.strftime('%d-%m-%Y %I:%M %p') if item.lastUpdatedOn else 'N/A'),

            ])

        return json_data


# Marks of Students by Subject ---------------------------------
def _component_rules_for_exam_subject(session_id, exam_id, subject_id):
    return list(
        ExamSubjectComponentRule.objects.select_related('componentTypeID').filter(
            isDeleted=False,
            sessionID_id=session_id,
            examID_id=exam_id,
            subjectID_id=subject_id,
        ).order_by('displayOrder', 'id')
    )


def _component_input_html(mark_row_id, student_id, rules, component_mark_map):
    blocks = []
    for rule in rules:
        comp_obj = component_mark_map.get((student_id, rule.id))
        value = ''
        is_absent = False
        is_exempt = False
        note = ''
        if comp_obj:
            value = '' if comp_obj.marksObtained is None else comp_obj.marksObtained
            is_absent = bool(comp_obj.isAbsent)
            is_exempt = bool(comp_obj.isExempt)
            note = comp_obj.note or ''

        blocks.append(
            f'''<div class="component-entry-card">
<div class="component-entry-top">
  <div class="component-entry-title">{escape(rule.componentTypeID.name if rule.componentTypeID else 'Component')} <span>(Max {escape(rule.maxMarks)})</span></div>
  <div class="component-entry-flags">
    <label class="component-flag"><input type="checkbox" id="compabs{mark_row_id}_{rule.id}" {'checked' if is_absent else ''}> Absent</label>
    <label class="component-flag"><input type="checkbox" id="compexm{mark_row_id}_{rule.id}" {'checked' if is_exempt else ''}> Exempt</label>
  </div>
</div>
<div class="component-entry-fields">
  <div class="ui mini input fluid component-entry-mark">
    <input type="number" min="0" step="0.01" placeholder="Marks" id="compmark{mark_row_id}_{rule.id}" value="{escape(value)}">
  </div>
  <div class="ui mini input fluid component-entry-note">
    <input type="text" placeholder="Note" id="compnote{mark_row_id}_{rule.id}" value="{escape(note)}">
  </div>
</div>
</div>'''
        )
    return ''.join(blocks)


def _as_bool(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {'1', 'true', 'yes', 'on'}


@login_required
@check_groups('Admin', 'Owner')
def get_exam_component_type_list_api(request):
    current_session_id = _current_session_id(request)
    current_school_id = request.session.get('current_session', {}).get('SchoolID')
    if not current_session_id:
        return ErrorResponse('Session not found.', extra={'color': 'red'}).to_json_response()

    defaults = [
        ('theory', 'Theory', 1),
        ('practical', 'Practical', 2),
        ('internal', 'Internal Assessment', 3),
    ]
    for code, name, order in defaults:
        if not ExamComponentType.objects.filter(
            isDeleted=False,
            sessionID_id=current_session_id,
            schoolID_id=current_school_id,
            code=code,
        ).exists():
            ExamComponentType.objects.create(
                schoolID_id=current_school_id,
                sessionID_id=current_session_id,
                code=code,
                name=name,
                displayOrder=order,
                lastEditedBy=_editor_name(request.user),
                updatedByUserID=request.user,
            )

    rows = ExamComponentType.objects.filter(
        isDeleted=False,
        sessionID_id=current_session_id,
        schoolID_id=current_school_id,
    ).order_by('displayOrder', 'name')

    data = [{
        'id': row.id,
        'name': row.name or 'N/A',
        'code': row.code or '',
        'isScholastic': row.isScholastic,
    } for row in rows]
    return SuccessResponse('Component types loaded.', data=data).to_json_response()


@transaction.atomic
@csrf_exempt
@login_required
@check_groups('Admin', 'Owner')
def add_exam_component_type_api(request):
    if request.method != 'POST':
        return ErrorResponse('Method not allowed.', status_code=405).to_json_response()

    current_session_id = _current_session_id(request)
    current_school_id = request.session.get('current_session', {}).get('SchoolID')
    if not current_session_id or not current_school_id:
        return ErrorResponse('Session context not found.', extra={'color': 'red'}).to_json_response()

    name = (request.POST.get('name') or '').strip()
    code = (request.POST.get('code') or '').strip().lower()
    is_scholastic = _as_bool(request.POST.get('isScholastic', True), default=True)

    if not name:
        return ErrorResponse('Component type name is required.', extra={'color': 'red'}).to_json_response()
    if not code:
        return ErrorResponse('Component type code is required.', extra={'color': 'red'}).to_json_response()

    safe_code = ''.join(ch for ch in code if ch.isalnum() or ch in {'_', '-'})
    if not safe_code:
        return ErrorResponse('Component type code can only use letters, numbers, _ or -.', extra={'color': 'red'}).to_json_response()

    existing = ExamComponentType.objects.filter(
        isDeleted=False,
        sessionID_id=current_session_id,
        schoolID_id=current_school_id,
        code=safe_code,
    ).first()
    if existing:
        return ErrorResponse('This component type code already exists in current session.', extra={'color': 'red'}).to_json_response()

    next_order = (
        ExamComponentType.objects.filter(
            isDeleted=False,
            sessionID_id=current_session_id,
            schoolID_id=current_school_id,
        ).aggregate(models.Max('displayOrder')).get('displayOrder__max') or 0
    ) + 1

    instance = ExamComponentType(
        schoolID_id=current_school_id,
        sessionID_id=current_session_id,
        name=name,
        code=safe_code,
        isScholastic=is_scholastic,
        isActive=True,
        displayOrder=next_order,
    )
    pre_save_with_user.send(sender=ExamComponentType, instance=instance, user=request.user.pk)

    return SuccessResponse(
        'Component type added successfully.',
        data={
            'id': instance.id,
            'name': instance.name,
            'code': instance.code,
            'isScholastic': instance.isScholastic,
        },
        extra={'color': 'green'}
    ).to_json_response()


@login_required
@check_groups('Admin', 'Owner')
def get_exam_subject_component_rules_api(request):
    current_session_id = _current_session_id(request)
    current_school_id = request.session.get('current_session', {}).get('SchoolID')

    standard = (request.GET.get('standard') or '').strip()
    exam = (request.GET.get('exam') or '').strip()
    subject = (request.GET.get('subject') or '').strip()

    if not (standard.isdigit() and exam.isdigit() and subject.isdigit()):
        return ErrorResponse('Invalid class/exam/subject.', extra={'color': 'red'}).to_json_response()

    rules = _component_rules_for_exam_subject(current_session_id, int(exam), int(subject))
    pass_policy = PassPolicy.objects.filter(
        isDeleted=False,
        sessionID_id=current_session_id,
        schoolID_id=current_school_id,
        examID_id=int(exam),
    ).first()

    data = {
        'rules': [{
            'id': row.id,
            'componentTypeID': row.componentTypeID_id,
            'componentTypeName': row.componentTypeID.name if row.componentTypeID else 'N/A',
            'maxMarks': row.maxMarks,
            'passMarks': row.passMarks,
            'weightage': row.weightage,
            'isMandatory': row.isMandatory,
            'displayOrder': row.displayOrder,
        } for row in rules],
        'passPolicy': {
            'overallPassMarks': pass_policy.overallPassMarks if pass_policy else None,
            'resultComputationMode': pass_policy.resultComputationMode if pass_policy else 'total_marks',
            'requireComponentPass': pass_policy.requireComponentPass if pass_policy else True,
            'requireSubjectPass': pass_policy.requireSubjectPass if pass_policy else True,
            'requireMandatoryComponents': pass_policy.requireMandatoryComponents if pass_policy else True,
        }
    }
    return SuccessResponse('Component rules loaded.', data=data).to_json_response()


@transaction.atomic
@csrf_exempt
@login_required
@check_groups('Admin', 'Owner')
def save_exam_subject_component_rules_api(request):
    if request.method != 'POST':
        return ErrorResponse('Method not allowed.', status_code=405).to_json_response()

    current_session_id = _current_session_id(request)
    current_school_id = request.session.get('current_session', {}).get('SchoolID')
    standard = (request.POST.get('standard') or '').strip()
    exam = (request.POST.get('exam') or '').strip()
    subject = (request.POST.get('subject') or '').strip()
    rules_raw = request.POST.get('rules') or '[]'
    pass_policy_raw = request.POST.get('pass_policy') or '{}'

    if not (standard.isdigit() and exam.isdigit() and subject.isdigit()):
        return ErrorResponse('Invalid class/exam/subject.', extra={'color': 'red'}).to_json_response()

    try:
        rules_payload = json.loads(rules_raw)
        pass_policy_payload = json.loads(pass_policy_raw)
    except Exception:
        return ErrorResponse('Invalid JSON payload.', extra={'color': 'red'}).to_json_response()

    assign_exam = AssignExamToClass.objects.filter(
        id=int(exam),
        isDeleted=False,
        sessionID_id=current_session_id,
        standardID_id=int(standard),
    ).first()
    assign_subject = AssignSubjectsToClass.objects.filter(
        id=int(subject),
        isDeleted=False,
        sessionID_id=current_session_id,
        standardID_id=int(standard),
    ).first()
    if not assign_exam or not assign_subject:
        return ErrorResponse('Class/exam/subject mapping not found.', extra={'color': 'red'}).to_json_response()

    active_ids = []
    for idx, row in enumerate(rules_payload):
        component_type_id = str(row.get('componentTypeID') or '').strip()
        max_marks = row.get('maxMarks')
        pass_marks = row.get('passMarks')
        weightage = row.get('weightage')
        is_mandatory = _as_bool(row.get('isMandatory', True), default=True)

        if not component_type_id.isdigit():
            return ErrorResponse(f'Invalid component type at row {idx + 1}.', extra={'color': 'red'}).to_json_response()
        try:
            max_marks = float(max_marks)
            pass_marks = float(pass_marks)
            weightage_value = None if weightage in (None, '', 'null') else float(weightage)
        except Exception:
            return ErrorResponse(f'Invalid numeric values at row {idx + 1}.', extra={'color': 'red'}).to_json_response()

        if max_marks <= 0 or pass_marks < 0 or pass_marks > max_marks:
            return ErrorResponse(f'Invalid max/pass marks at row {idx + 1}.', extra={'color': 'red'}).to_json_response()
        if weightage_value is not None and (weightage_value < 0 or weightage_value > 100):
            return ErrorResponse(f'Invalid weightage at row {idx + 1}.', extra={'color': 'red'}).to_json_response()

        component_type = ExamComponentType.objects.filter(
            id=int(component_type_id),
            isDeleted=False,
            sessionID_id=current_session_id,
        ).first()
        if not component_type:
            return ErrorResponse(f'Component type not found at row {idx + 1}.', extra={'color': 'red'}).to_json_response()

        rule_id = row.get('id')
        rule_obj = None
        if str(rule_id).isdigit():
            rule_obj = ExamSubjectComponentRule.objects.filter(
                id=int(rule_id),
                isDeleted=False,
                sessionID_id=current_session_id,
                examID_id=assign_exam.id,
                subjectID_id=assign_subject.id,
            ).first()

        if not rule_obj:
            rule_obj = ExamSubjectComponentRule(
                schoolID_id=current_school_id,
                sessionID_id=current_session_id,
                examID_id=assign_exam.id,
                subjectID_id=assign_subject.id,
            )

        rule_obj.componentTypeID = component_type
        rule_obj.maxMarks = max_marks
        rule_obj.passMarks = pass_marks
        rule_obj.weightage = weightage_value
        rule_obj.isMandatory = is_mandatory
        rule_obj.displayOrder = idx + 1
        pre_save_with_user.send(sender=ExamSubjectComponentRule, instance=rule_obj, user=request.user.pk)
        active_ids.append(rule_obj.id)

    ExamSubjectComponentRule.objects.filter(
        isDeleted=False,
        sessionID_id=current_session_id,
        examID_id=assign_exam.id,
        subjectID_id=assign_subject.id,
    ).exclude(id__in=active_ids).update(isDeleted=True)

    if isinstance(pass_policy_payload, dict):
        pass_policy, _ = PassPolicy.objects.get_or_create(
            isDeleted=False,
            sessionID_id=current_session_id,
            schoolID_id=current_school_id,
            examID_id=assign_exam.id,
            defaults={'overallPassMarks': assign_exam.passMarks},
        )
        overall_pass = pass_policy_payload.get('overallPassMarks')
        if overall_pass in (None, '', 'null'):
            pass_policy.overallPassMarks = assign_exam.passMarks
        else:
            try:
                pass_policy.overallPassMarks = float(overall_pass)
            except Exception:
                return ErrorResponse('Invalid overall pass marks.', extra={'color': 'red'}).to_json_response()

        pass_policy.resultComputationMode = pass_policy_payload.get('resultComputationMode') or 'total_marks'
        pass_policy.requireComponentPass = _as_bool(pass_policy_payload.get('requireComponentPass', True), default=True)
        pass_policy.requireSubjectPass = _as_bool(pass_policy_payload.get('requireSubjectPass', True), default=True)
        pass_policy.requireMandatoryComponents = _as_bool(pass_policy_payload.get('requireMandatoryComponents', True), default=True)
        pre_save_with_user.send(sender=PassPolicy, instance=pass_policy, user=request.user.pk)

    return SuccessResponse('Component rules saved successfully.', extra={'color': 'green'}).to_json_response()


class MarksOfSubjectsByStudentJson(BaseDatatableView):
    order_columns = ['studentID.photo', 'studentID.name', 'studentID.roll', 'examID.fullMarks', 'examID.passMarks', 'mark', 'note', 'lastEditedBy', 'lastUpdatedOn']

    @transaction.atomic
    def get_initial_queryset(self):
        try:
            standard = self.request.GET.get("standard")
            exam = self.request.GET.get("exam")
            subject = self.request.GET.get("subject")
            students = Student.objects.filter(standardID_id=int(standard), isDeleted=False, sessionID_id=self.request.session["current_session"]["Id"])

            for stu in students:
                try:
                    MarkOfStudentsByExam.objects.get(studentID_id=int(stu.pk), subjectID_id=int(subject), examID_id=int(exam),
                                           standardID_id=int(standard), isDeleted=False,
                                           sessionID_id=self.request.session["current_session"]["Id"])

                except:
                    instance = MarkOfStudentsByExam.objects.create(studentID_id=int(stu.pk), subjectID_id=int(subject), examID_id=int(exam),
                                           standardID_id=int(standard)
                                                         )
                    pre_save_with_user.send(sender=MarkOfStudentsByExam, instance=instance, user=self.request.user.pk)

            return MarkOfStudentsByExam.objects.filter(standardID_id=int(standard), isDeleted=False, sessionID_id=self.request.session["current_session"]["Id"], examID_id=int(exam), subjectID_id=int(subject))

        except:
            return MarkOfStudentsByExam.objects.none()

    def filter_queryset(self, qs):

        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(studentID__name__icontains=search)
                |Q(examID__fullMarks__icontains=search)
                |Q(examID__passMarks__icontains=search)
                |Q(mark__icontains=search)
                |Q(note__icontains=search)
                | Q(studentID__roll__icontains=search)
              | Q(lastEditedBy__icontains=search) | Q(lastUpdatedOn__icontains=search)
            )

        return qs

    def prepare_results(self, qs):
        exam = self.request.GET.get("exam")
        subject = self.request.GET.get("subject")
        session_id = self.request.session["current_session"]["Id"]
        rules = []
        rule_ids = []
        component_mark_map = {}
        if str(exam).isdigit() and str(subject).isdigit():
            rules = _component_rules_for_exam_subject(session_id, int(exam), int(subject))
            rule_ids = [row.id for row in rules]
            if rule_ids:
                student_ids = [item.studentID_id for item in qs]
                comp_rows = StudentExamComponentMark.objects.filter(
                    isDeleted=False,
                    sessionID_id=session_id,
                    examID_id=int(exam),
                    subjectID_id=int(subject),
                    studentID_id__in=student_ids,
                    componentRuleID_id__in=rule_ids,
                )
                component_mark_map = {(row.studentID_id, row.componentRuleID_id): row for row in comp_rows}

        json_data = []
        for item in qs:

            action = '''<button class="ui mini primary button" onclick="pushMark({}, {})">
  Save
</button>'''.format(item.pk, 1 if rules else 0)

            marks_obtained = '''<div class="ui tiny input fluid">
  <input type="number" placeholder="Mark Obtained" name="mark{}" id="mark{}" value = "{}">
</div>
            '''.format(item.pk, item.pk, item.mark)
            full_mark = item.examID.fullMarks
            pass_mark = item.examID.passMarks
            if rules:
                full_mark = round(sum(float(r.maxMarks or 0) for r in rules), 2)
                pass_mark = round(sum(float(r.passMarks or 0) for r in rules), 2)
                marks_obtained = _component_input_html(item.pk, item.studentID_id, rules, component_mark_map) + \
                    f'''<div style="font-size:11px;color:#6b7280;">Total: {escape(item.mark or 0)}</div>'''

            note = '''<div class="ui tiny input fluid">
              <input type="text" placeholder="Note" name="note{}" id="note{}" value = "{}">
            </div>
                        '''.format(item.pk, item.pk, item.note)
            if item.studentID and item.studentID.photo:
                images = _avatar_image_html(item.studentID.photo)
            else:
                images = _avatar_image_html(None)
            json_data.append([
                images,
                escape(item.studentID.name),
                escape(item.studentID.roll or 'N/A'),
                full_mark,
                pass_mark,
                marks_obtained,
                note,
                escape(item.lastEditedBy or 'N/A'),
                escape(item.lastUpdatedOn.strftime('%d-%m-%Y %I:%M %p') if item.lastUpdatedOn else 'N/A'),
                action,

            ])

        return json_data


@transaction.atomic
@csrf_exempt
@login_required
@check_groups('Admin', 'Owner')
def add_subject_mark_api(request):
    if request.method == 'POST':
        id = request.POST.get("id")
        note = request.POST.get("note")
        mark = request.POST.get("mark")
        component_marks_raw = request.POST.get("component_marks") or "[]"
        try:
            instance = MarkOfStudentsByExam.objects.get(pk=int(id))
            instance.note = note
            rules = _component_rules_for_exam_subject(
                session_id=instance.sessionID_id,
                exam_id=instance.examID_id,
                subject_id=instance.subjectID_id,
            )
            if rules:
                try:
                    component_rows = json.loads(component_marks_raw)
                except Exception:
                    return _api_response({'status': 'error', 'message': 'Invalid component payload.', 'color': 'red'}, safe=False)

                component_rows_map = {int(row.get('rule_id')): row for row in component_rows if str(row.get('rule_id')).isdigit()}
                total_mark = 0.0
                for rule in rules:
                    row = component_rows_map.get(rule.id, {})
                    is_absent = _as_bool(row.get('is_absent', False), default=False)
                    is_exempt = _as_bool(row.get('is_exempt', False), default=False)
                    if is_exempt:
                        is_absent = False
                    note_value = (row.get('note') or '').strip()
                    marks_value = row.get('mark')

                    comp_instance, _ = StudentExamComponentMark.objects.get_or_create(
                        isDeleted=False,
                        sessionID_id=instance.sessionID_id,
                        schoolID_id=instance.schoolID_id,
                        examID_id=instance.examID_id,
                        studentID_id=instance.studentID_id,
                        standardID_id=instance.standardID_id,
                        subjectID_id=instance.subjectID_id,
                        componentRuleID_id=rule.id,
                        defaults={
                            'note': '',
                        }
                    )

                    comp_instance.isAbsent = is_absent
                    comp_instance.isExempt = is_exempt
                    comp_instance.note = note_value

                    if is_exempt:
                        comp_instance.marksObtained = None
                    elif is_absent:
                        comp_instance.marksObtained = 0.0
                    elif marks_value in (None, ''):
                        comp_instance.marksObtained = None
                    else:
                        numeric_mark = float(marks_value)
                        max_marks = float(rule.maxMarks or 0)
                        if numeric_mark < 0 or numeric_mark > max_marks:
                            return _api_response(
                                {'status': 'error', 'message': f'Marks for {rule.componentTypeID.name if rule.componentTypeID else "component"} must be between 0 and {max_marks}.', 'color': 'red'},
                                safe=False
                            )
                        comp_instance.marksObtained = numeric_mark

                    pre_save_with_user.send(sender=StudentExamComponentMark, instance=comp_instance, user=request.user.pk)
                    if comp_instance.marksObtained is not None and not comp_instance.isExempt:
                        total_mark += float(comp_instance.marksObtained)

                instance.mark = round(total_mark, 2)
            else:
                instance.mark = float(mark)
            pre_save_with_user.send(sender=MarkOfStudentsByExam, instance=instance, user=request.user.pk)
            return _api_response(
                {'status': 'success', 'message': 'Mark added successfully.', 'color': 'success'},
                safe=False)
        except:

            return _api_response({'status': 'error'}, safe=False)


@transaction.atomic
@csrf_exempt
@login_required
@check_groups('Admin', 'Owner')
def publish_progress_report_api(request):
    if request.method != 'POST':
        return ErrorResponse('Method not allowed.', status_code=405).to_json_response()

    current_session_id = _current_session_id(request)
    current_school_id = request.session.get('current_session', {}).get('SchoolID')
    standard = (request.POST.get('standard') or '').strip()
    student = (request.POST.get('student') or '').strip()
    exam = (request.POST.get('exam') or '').strip()
    exam_ids_raw = (request.POST.get('exam_ids') or '').strip()
    status = (request.POST.get('status') or 'published').strip().lower()
    if status not in {'draft', 'reviewed', 'published'}:
        status = 'published'

    if not (standard.isdigit() and student.isdigit()):
        return ErrorResponse('Invalid class/student.', extra={'color': 'red'}).to_json_response()
    if exam and exam != 'all' and not exam.isdigit():
        return ErrorResponse('Invalid exam.', extra={'color': 'red'}).to_json_response()

    explicit_exam_ids = []
    if exam_ids_raw:
        for token in exam_ids_raw.split(','):
            exam_id_value = token.strip()
            if not exam_id_value:
                continue
            if not exam_id_value.isdigit():
                return ErrorResponse('Invalid visible exam list.', extra={'color': 'red'}).to_json_response()
            explicit_exam_ids.append(int(exam_id_value))
        explicit_exam_ids = sorted(set(explicit_exam_ids))

    student_obj = Student.objects.filter(
        id=int(student),
        isDeleted=False,
        sessionID_id=current_session_id,
        standardID_id=int(standard),
    ).first()
    if not student_obj:
        return ErrorResponse('Student not found.', extra={'color': 'red'}).to_json_response()

    exam_queryset = AssignExamToClass.objects.select_related('examID').filter(
        isDeleted=False,
        sessionID_id=current_session_id,
        standardID_id=int(standard),
    )
    if explicit_exam_ids:
        exam_queryset = exam_queryset.filter(id__in=explicit_exam_ids)
    elif exam and exam != 'all':
        exam_queryset = exam_queryset.filter(id=int(exam))
    if not exam_queryset.exists():
        return ErrorResponse('No exams found for selected filters.', extra={'color': 'red'}).to_json_response()

    selected_exam_ids = list(exam_queryset.values_list('id', flat=True))
    skipped_not_ready = 0
    if status == 'published':
        ready_exam_ids = set(
            ProgressReport.objects.filter(
                isDeleted=False,
                sessionID_id=current_session_id,
                studentID_id=student_obj.id,
                examID_id__in=selected_exam_ids,
                readyToPublish=True,
            ).values_list('examID_id', flat=True)
        )
        eligible_exam_ids = [exam_id for exam_id in selected_exam_ids if exam_id in ready_exam_ids]
        skipped_not_ready = len(selected_exam_ids) - len(eligible_exam_ids)
        if not eligible_exam_ids:
            return ErrorResponse(
                'No selected report is marked Ready to Publish.',
                extra={'color': 'orange'}
            ).to_json_response()
        exam_queryset = exam_queryset.filter(id__in=eligible_exam_ids)

    report_cards = build_report_cards_for_student(
        current_session_id=current_session_id,
        student_obj=student_obj,
        standard_id=int(standard),
        exam_queryset=exam_queryset,
        prefer_published_snapshot=False,
    )
    card_map = {int(card.get('exam_assignment_id')): card for card in report_cards if str(card.get('exam_assignment_id')).isdigit()}
    snapshot_count = 0
    for exam_obj in exam_queryset:
        payload = card_map.get(exam_obj.id)
        if not payload:
            continue
        upsert_progress_report_snapshot(
            current_session_id=current_session_id,
            school_id=current_school_id or student_obj.schoolID_id,
            student_id=student_obj.id,
            standard_id=int(standard),
            exam_id=exam_obj.id,
            payload=payload,
            status=status,
            user_obj=request.user,
        )
        snapshot_count += 1

    if snapshot_count == 0:
        return ErrorResponse('No report data available to publish.', extra={'color': 'red'}).to_json_response()

    message = f'Progress report {status} successfully.'
    if skipped_not_ready > 0:
        message += f' Skipped {skipped_not_ready} not-ready report(s).'
    return SuccessResponse(
        message,
        data={'snapshotsSaved': snapshot_count},
        extra={'color': 'green'}
    ).to_json_response()


@transaction.atomic
@csrf_exempt
@login_required
@check_groups('Admin', 'Owner')
def set_progress_report_ready_state_api(request):
    if request.method != 'POST':
        return ErrorResponse('Method not allowed.', status_code=405).to_json_response()

    current_session_id = _current_session_id(request)
    current_school_id = request.session.get('current_session', {}).get('SchoolID')
    standard = (request.POST.get('standard') or '').strip()
    student = (request.POST.get('student') or '').strip()
    exam = (request.POST.get('exam') or '').strip()
    ready_raw = (request.POST.get('ready') or '').strip().lower()

    if not current_session_id:
        return ErrorResponse('No active session selected.', extra={'color': 'red'}).to_json_response()
    if not (standard.isdigit() and student.isdigit() and exam.isdigit()):
        return ErrorResponse('Invalid class/student/exam.', extra={'color': 'red'}).to_json_response()

    ready_value = ready_raw in {'1', 'true', 'yes', 'on'}
    standard_id = int(standard)
    student_id = int(student)
    exam_id = int(exam)

    student_obj = Student.objects.filter(
        id=student_id,
        isDeleted=False,
        sessionID_id=current_session_id,
        standardID_id=standard_id,
    ).first()
    if not student_obj:
        return ErrorResponse('Student not found.', extra={'color': 'red'}).to_json_response()

    exam_obj = AssignExamToClass.objects.filter(
        id=exam_id,
        isDeleted=False,
        sessionID_id=current_session_id,
        standardID_id=standard_id,
    ).first()
    if not exam_obj:
        return ErrorResponse('Exam not found for selected class.', extra={'color': 'red'}).to_json_response()

    report_obj = ProgressReport.objects.filter(
        isDeleted=False,
        sessionID_id=current_session_id,
        studentID_id=student_id,
        examID_id=exam_id,
    ).first()
    if not report_obj:
        report_obj = ProgressReport(
            schoolID_id=current_school_id or student_obj.schoolID_id,
            sessionID_id=current_session_id,
            examID_id=exam_id,
            studentID_id=student_id,
            standardID_id=standard_id,
            status='draft',
            readyToPublish=ready_value,
        )
    else:
        report_obj.standardID_id = standard_id
        report_obj.readyToPublish = ready_value

    pre_save_with_user.send(sender=ProgressReport, instance=report_obj, user=request.user.pk)

    return SuccessResponse(
        'Ready to Publish updated successfully.',
        data={'readyToPublish': bool(report_obj.readyToPublish)},
        extra={'color': 'green'}
    ).to_json_response()


@transaction.atomic
@csrf_exempt
@login_required
@check_groups('Admin', 'Owner')
def management_upsert_term_remark_api(request):
    if request.method != 'POST':
        return ErrorResponse('Method not allowed.', status_code=405).to_json_response()

    current_session_id = _current_session_id(request)
    current_school_id = request.session.get('current_session', {}).get('SchoolID')
    standard = (request.POST.get('standard') or '').strip()
    student = (request.POST.get('student') or '').strip()
    exam = (request.POST.get('exam') or '').strip()
    overall_remark = (request.POST.get('overall_remark') or '').strip()
    overall_result = (request.POST.get('overall_result') or '').strip().lower()

    if not current_session_id:
        return ErrorResponse('No active session selected.', extra={'color': 'red'}).to_json_response()
    if not (standard.isdigit() and student.isdigit() and exam.isdigit()):
        return ErrorResponse('Invalid class/student/exam.', extra={'color': 'red'}).to_json_response()
    if overall_result not in {'', 'auto', 'pass', 'fail'}:
        return ErrorResponse('Invalid overall result option.', extra={'color': 'red'}).to_json_response()

    standard_id = int(standard)
    student_id = int(student)
    exam_id = int(exam)

    student_obj = Student.objects.filter(
        id=student_id,
        isDeleted=False,
        sessionID_id=current_session_id,
        standardID_id=standard_id,
    ).first()
    if not student_obj:
        return ErrorResponse('Student not found.', extra={'color': 'red'}).to_json_response()

    exam_obj = AssignExamToClass.objects.filter(
        id=exam_id,
        isDeleted=False,
        sessionID_id=current_session_id,
        standardID_id=standard_id,
    ).first()
    if not exam_obj:
        return ErrorResponse('Exam not found for selected class.', extra={'color': 'red'}).to_json_response()

    remark_obj = TermTeacherRemark.objects.filter(
        isDeleted=False,
        sessionID_id=current_session_id,
        studentID_id=student_id,
        examID_id=exam_id,
    ).first()
    if not remark_obj:
        remark_obj = TermTeacherRemark(
            schoolID_id=current_school_id or student_obj.schoolID_id,
            sessionID_id=current_session_id,
            examID_id=exam_id,
            studentID_id=student_id,
            standardID_id=standard_id,
        )

    remark_obj.overallRemark = overall_remark
    is_auto_mode = overall_result in {'', 'auto'}
    remark_obj.overallResultDecision = '' if is_auto_mode else overall_result
    remark_obj.resultDecidedByRole = '' if is_auto_mode else 'management'
    pre_save_with_user.send(sender=TermTeacherRemark, instance=remark_obj, user=request.user.pk)

    # Keep student-facing published cards in sync when report is already published.
    published_exists = ProgressReport.objects.filter(
        isDeleted=False,
        sessionID_id=current_session_id,
        studentID_id=student_id,
        examID_id=exam_id,
        status='published',
    ).exists()
    if published_exists:
        live_exam_qs = AssignExamToClass.objects.filter(
            isDeleted=False,
            sessionID_id=current_session_id,
            standardID_id=standard_id,
            id=exam_id,
        )
        live_cards = build_report_cards_for_student(
            current_session_id=current_session_id,
            student_obj=student_obj,
            standard_id=standard_id,
            exam_queryset=live_exam_qs,
            prefer_published_snapshot=False,
        )
        payload = next((row for row in live_cards if int(row.get('exam_assignment_id', 0)) == exam_id), None)
        if payload:
            upsert_progress_report_snapshot(
                current_session_id=current_session_id,
                school_id=current_school_id or student_obj.schoolID_id,
                student_id=student_obj.id,
                standard_id=standard_id,
                exam_id=exam_id,
                payload=payload,
                status='published',
                user_obj=request.user,
            )

    return SuccessResponse(
        'Overall remark/result saved successfully.',
        data={
            'overallRemark': remark_obj.overallRemark or '',
            'overallResultDecision': remark_obj.overallResultDecision or '',
            'resultDecidedByRole': remark_obj.resultDecidedByRole or '',
        },
        extra={'color': 'green'}
    ).to_json_response()


class StudentMarksDetailsByClassAndExamJson(BaseDatatableView):
    order_columns = ['photo', 'name', 'roll']

    @transaction.atomic
    def get_initial_queryset(self):
        try:
            standard = self.request.GET.get("standard")

            return Student.objects.select_related().filter(isDeleted__exact=False, standardID_id=int(standard),
                                                           sessionID_id=self.request.session["current_session"][
                                                               "Id"]).order_by('roll')
        except:
            return Student.objects.none()

    def filter_queryset(self, qs):

        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(name__icontains=search) | Q(roll__icontains=search)
                | Q(standardID__name__icontains=search)
                | Q(lastEditedBy__icontains=search) | Q(lastUpdatedOn__icontains=search)
            )

        return qs

    def prepare_results(self, qs):
        standard = self.request.GET.get("standard")
        exam = self.request.GET.get("exam")

        json_data = []
        for item in qs:
            subject_list = AssignSubjectsToClass.objects.filter(standardID_id=int(standard), isDeleted=False, sessionID_id=self.request.session["current_session"]["Id"])
            subs = [i.subjectID.name for i in subject_list]
            marks = []
            for s in subs:
                exam_sub_list_by_student = MarkOfStudentsByExam.objects.filter(
                    studentID_id=item.id,
                    isDeleted=False,
                    examID_id=int(exam),
                    sessionID_id=self.request.session["current_session"]["Id"],
                    subjectID__subjectID__name=s,
                ).first()
                marks.append(exam_sub_list_by_student.mark if exam_sub_list_by_student else 0)

            if item.photo:
                images = _avatar_image_html(item.photo)
            else:
                images = _avatar_image_html(None)
            json_data.append([
                images,
                escape(item.name),
                escape(item.roll or 'N/A'),


            ] + marks)
        return json_data


class StudentMarksDetailsByStudentJson(BaseDatatableView):
    order_columns = [
        'examID__examID__name',
        'subjectID__subjectID__name',
        'examID__fullMarks',
        'examID__passMarks',
        'mark',
        'mark',
        'note',
        'lastEditedBy',
        'lastUpdatedOn',
    ]

    @transaction.atomic
    def get_initial_queryset(self):
        try:
            standard = self.request.GET.get("standardByStudent")
            student = self.request.GET.get("student")
            return MarkOfStudentsByExam.objects.select_related(
                'examID',
                'examID__examID',
                'subjectID',
                'subjectID__subjectID',
            ).filter(
                isDeleted=False,
                sessionID_id=self.request.session["current_session"]["Id"],
                standardID_id=int(standard),
                studentID_id=int(student),
            ).order_by('examID__examID__name', 'subjectID__subjectID__name')
        except Exception:
            return MarkOfStudentsByExam.objects.none()

    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(examID__examID__name__icontains=search)
                | Q(subjectID__subjectID__name__icontains=search)
                | Q(examID__fullMarks__icontains=search)
                | Q(examID__passMarks__icontains=search)
                | Q(mark__icontains=search)
                | Q(note__icontains=search)
                | Q(lastEditedBy__icontains=search)
                | Q(lastUpdatedOn__icontains=search)
            )
        return qs

    def prepare_results(self, qs):
        row_keys = [(item.studentID_id, item.examID_id, item.subjectID_id) for item in qs]
        exam_ids = list({k[1] for k in row_keys if k[1]})
        subject_ids = list({k[2] for k in row_keys if k[2]})
        session_id = self.request.session["current_session"]["Id"]
        rule_rows = ExamSubjectComponentRule.objects.select_related('componentTypeID').filter(
            isDeleted=False,
            sessionID_id=session_id,
            examID_id__in=exam_ids,
            subjectID_id__in=subject_ids,
        ).order_by('displayOrder', 'id')
        rules_map = {}
        rule_ids = []
        for rule in rule_rows:
            rules_map.setdefault((rule.examID_id, rule.subjectID_id), []).append(rule)
            rule_ids.append(rule.id)

        component_rows = StudentExamComponentMark.objects.filter(
            isDeleted=False,
            sessionID_id=session_id,
            studentID_id__in=[k[0] for k in row_keys if k[0]],
            examID_id__in=exam_ids,
            subjectID_id__in=subject_ids,
            componentRuleID_id__in=rule_ids or [0],
        )
        component_mark_map = {(row.studentID_id, row.examID_id, row.subjectID_id, row.componentRuleID_id): row for row in component_rows}

        json_data = []
        for item in qs:
            exam_name = 'N/A'
            subject_name = 'N/A'
            full_mark = 0
            pass_mark = 0

            if item.examID and item.examID.examID:
                exam_name = item.examID.examID.name or 'N/A'
                full_mark = item.examID.fullMarks if item.examID.fullMarks is not None else 0
                pass_mark = item.examID.passMarks if item.examID.passMarks is not None else 0

            if item.subjectID and item.subjectID.subjectID:
                subject_name = item.subjectID.subjectID.name or 'N/A'

            rules = rules_map.get((item.examID_id, item.subjectID_id), [])
            if rules:
                chunks = []
                for rule in rules:
                    comp_row = component_mark_map.get((item.studentID_id, item.examID_id, item.subjectID_id, rule.id))
                    if comp_row is None:
                        chunks.append(f'{rule.componentTypeID.name if rule.componentTypeID else "Component"}: Pending')
                    elif comp_row.isExempt:
                        chunks.append(f'{rule.componentTypeID.name if rule.componentTypeID else "Component"}: Exempt')
                    elif comp_row.isAbsent:
                        chunks.append(f'{rule.componentTypeID.name if rule.componentTypeID else "Component"}: Absent(0)')
                    elif comp_row.marksObtained is None:
                        chunks.append(f'{rule.componentTypeID.name if rule.componentTypeID else "Component"}: Pending')
                    else:
                        chunks.append(f'{rule.componentTypeID.name if rule.componentTypeID else "Component"}: {comp_row.marksObtained}/{rule.maxMarks}')
                component_summary = ' | '.join(chunks)
            else:
                component_summary = '-'

            json_data.append([
                escape(exam_name),
                escape(subject_name),
                escape(full_mark),
                escape(pass_mark),
                escape(item.mark if item.mark is not None else 0),
                escape(component_summary),
                escape(item.note or ''),
                escape(item.lastEditedBy or 'N/A'),
                escape(item.lastUpdatedOn.strftime('%d-%m-%Y %I:%M %p') if item.lastUpdatedOn else 'N/A'),
            ])
        return json_data


# Events ------------------------------------------------------
@login_required
def get_event_type_list_api(request):
    try:
        current_session_id = _current_session_id(request)
        default_types = [
            ('General Announcement', 'general'),
            ('Teacher Notice', 'teacherapp'),
            ('Student Notice', 'studentapp'),
            ('Management Circular', 'managementapp'),
            ('All Apps Broadcast', 'all_apps'),
        ]

        existing_pairs = set(
            EventType.objects.filter(
                isDeleted=False,
                sessionID_id=current_session_id,
                name__in=[name for name, _ in default_types],
                audience__in=[aud for _, aud in default_types],
            ).values_list('name', 'audience')
        )
        for type_name, audience in default_types:
            if (type_name, audience) not in existing_pairs:
                obj = EventType(name=type_name, audience=audience)
                pre_save_with_user.send(sender=EventType, instance=obj, user=request.user.pk)
                obj.save()

        objs = EventType.objects.filter(
            isDeleted=False,
            sessionID_id=current_session_id
        ).order_by('name')
        data = [{
            'ID': obj.id,
            'Name': obj.name,
            'Audience': obj.audience,
            'AudienceLabel': obj.get_audience_display(),
        } for obj in objs]
        return SuccessResponse('Event type list fetched successfully.', data=data, extra={'color': 'success'}).to_json_response()
    except Exception as e:
        logger.error(f"Error fetching event types: {e}")
        return ErrorResponse('Error in fetching event type list.', extra={'color': 'red'}).to_json_response()


class EventTypeListJson(BaseDatatableView):
    order_columns = ['name', 'audience', 'description', 'lastEditedBy', 'lastUpdatedOn']

    def get_initial_queryset(self):
        return EventType.objects.filter(
            isDeleted=False,
            sessionID_id=self.request.session['current_session']['Id'],
        )

    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(name__icontains=search)
                | Q(audience__icontains=search)
                | Q(description__icontains=search)
                | Q(lastEditedBy__icontains=search)
                | Q(lastUpdatedOn__icontains=search)
            )
        return qs

    def prepare_results(self, qs):
        json_data = []
        for item in qs:
            action = '''
              <button data-inverted="" data-tooltip="Edit Detail" data-position="left center" data-variation="mini" style="font-size:10px;" onclick = "GetTypeDataDetails('{}')" class="ui circular facebook icon button green">
                <i class="pen icon"></i>
              </button>
              <button data-inverted="" data-tooltip="Delete" data-position="left center" data-variation="mini" style="font-size:10px;" onclick ="delTypeData('{}')" class="ui circular youtube icon button" style="margin-left: 3px">
                <i class="trash alternate icon"></i>
              </button></td>'''.format(item.pk, item.pk)
            json_data.append([
                escape(item.name or 'N/A'),
                escape(item.get_audience_display()),
                escape(item.description or ''),
                escape(item.lastEditedBy or 'N/A'),
                escape(item.lastUpdatedOn.strftime('%d-%m-%Y %I:%M %p') if item.lastUpdatedOn else 'N/A'),
                action,
            ])
        return json_data


@transaction.atomic
@csrf_exempt
@login_required
def add_event_type_api(request):
    if request.method != 'POST':
        return ErrorResponse('Invalid request method.', extra={'color': 'red'}).to_json_response()

    try:
        post_data = request.POST.dict()
        name = (post_data.get('type_name') or '').strip()
        audience = (post_data.get('type_audience') or '').strip()
        description = (post_data.get('type_description') or '').strip()

        valid_audiences = {choice[0] for choice in EventType.AUDIENCE_CHOICES}
        if not name or not audience:
            return ErrorResponse('Name and audience are required.', extra={'color': 'red'}).to_json_response()
        if audience not in valid_audiences:
            return ErrorResponse('Invalid audience selected.', extra={'color': 'red'}).to_json_response()

        exists = EventType.objects.filter(
            isDeleted=False,
            sessionID_id=request.session['current_session']['Id'],
            name__iexact=name,
            audience=audience,
        ).exists()
        if exists:
            return ErrorResponse('Event type already exists for selected audience.', extra={'color': 'orange'}).to_json_response()

        obj = EventType(name=name, audience=audience, description=description)
        pre_save_with_user.send(sender=EventType, instance=obj, user=request.user.pk)

        return SuccessResponse('Event type added successfully.', extra={'color': 'success'}).to_json_response()
    except Exception as e:
        logger.error(f'Error in add_event_type_api: {e}')
        return ErrorResponse('Failed to add event type.', extra={'color': 'red'}).to_json_response()


@login_required
def get_event_type_detail(request):
    try:
        obj = EventType.objects.get(
            pk=request.GET.get('id'),
            isDeleted=False,
            sessionID_id=request.session['current_session']['Id'],
        )
        data = {
            'ID': obj.pk,
            'name': obj.name,
            'audience': obj.audience,
            'description': obj.description or '',
        }
        return SuccessResponse('Event type detail fetched successfully.', data=data, extra={'color': 'success'}).to_json_response()
    except Exception as e:
        logger.error(f'Error in get_event_type_detail: {e}')
        return ErrorResponse('Error in fetching event type details.', extra={'color': 'red'}).to_json_response()


@transaction.atomic
@csrf_exempt
@login_required
def update_event_type_api(request):
    if request.method != 'POST':
        return ErrorResponse('Invalid request method.', extra={'color': 'red'}).to_json_response()

    try:
        post_data = request.POST.dict()
        edit_id = post_data.get('type_editID')
        name = (post_data.get('type_name') or '').strip()
        audience = (post_data.get('type_audience') or '').strip()
        description = (post_data.get('type_description') or '').strip()

        valid_audiences = {choice[0] for choice in EventType.AUDIENCE_CHOICES}
        if not edit_id or not name or not audience:
            return ErrorResponse('Name and audience are required.', extra={'color': 'red'}).to_json_response()
        if audience not in valid_audiences:
            return ErrorResponse('Invalid audience selected.', extra={'color': 'red'}).to_json_response()

        obj = EventType.objects.get(
            pk=int(edit_id),
            isDeleted=False,
            sessionID_id=request.session['current_session']['Id'],
        )

        duplicate = EventType.objects.filter(
            isDeleted=False,
            sessionID_id=request.session['current_session']['Id'],
            name__iexact=name,
            audience=audience,
        ).exclude(pk=obj.pk).exists()
        if duplicate:
            return ErrorResponse('Another event type already exists with same name and audience.', extra={'color': 'orange'}).to_json_response()

        obj.name = name
        obj.audience = audience
        obj.description = description
        pre_save_with_user.send(sender=EventType, instance=obj, user=request.user.pk)

        return SuccessResponse('Event type updated successfully.', extra={'color': 'success'}).to_json_response()
    except Exception as e:
        logger.error(f'Error in update_event_type_api: {e}')
        return ErrorResponse('Failed to update event type.', extra={'color': 'red'}).to_json_response()


def _parse_holiday_date(value, label):
    value = (value or '').strip()
    if not value:
        raise ValueError(f'{label} is required.')
    return datetime.strptime(value, '%d/%m/%Y').date()


def _holiday_payload_from_request(request):
    post_data = request.POST.dict()
    title = (post_data.get('title') or '').strip()
    holiday_type = (post_data.get('holiday_type') or '').strip()
    applies_to = (post_data.get('applies_to') or '').strip()
    start_date = _parse_holiday_date(post_data.get('start_date'), 'Start date')
    end_date = _parse_holiday_date(post_data.get('end_date'), 'End date')
    description = (post_data.get('description') or '').strip()

    valid_types = {choice[0] for choice in SchoolHoliday.HOLIDAY_TYPE_CHOICES}
    valid_applies_to = {choice[0] for choice in SchoolHoliday.APPLIES_TO_CHOICES}
    if not title or not holiday_type or not applies_to:
        raise ValueError('Title, holiday type and applies to are required.')
    if holiday_type not in valid_types:
        raise ValueError('Invalid holiday type selected.')
    if applies_to not in valid_applies_to:
        raise ValueError('Invalid applies to selected.')
    if end_date < start_date:
        raise ValueError('End date cannot be before start date.')

    return {
        'title': title,
        'holidayType': holiday_type,
        'appliesTo': applies_to,
        'startDate': start_date,
        'endDate': end_date,
        'description': description,
    }


class HolidayListJson(BaseDatatableView):
    order_columns = ['title', 'holidayType', 'appliesTo', 'startDate', 'endDate', 'description', 'lastEditedBy', 'lastUpdatedOn']

    def get_initial_queryset(self):
        return SchoolHoliday.objects.filter(
            isDeleted=False,
            sessionID_id=self.request.session['current_session']['Id'],
        )

    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(title__icontains=search)
                | Q(holidayType__icontains=search)
                | Q(appliesTo__icontains=search)
                | Q(startDate__icontains=search)
                | Q(endDate__icontains=search)
                | Q(description__icontains=search)
                | Q(lastEditedBy__icontains=search)
                | Q(lastUpdatedOn__icontains=search)
            )
        return qs

    def prepare_results(self, qs):
        json_data = []
        for item in qs:
            action = '''
              <button data-inverted="" data-tooltip="Edit Detail" data-position="left center" data-variation="mini" style="font-size:10px;" onclick = "GetDataDetails('{}')" class="ui circular facebook icon button green">
                <i class="pen icon"></i>
              </button>
              <button data-inverted="" data-tooltip="Delete" data-position="left center" data-variation="mini" style="font-size:10px;" onclick ="delData('{}')" class="ui circular youtube icon button" style="margin-left: 3px">
                <i class="trash alternate icon"></i>
              </button></td>'''.format(item.pk, item.pk)
            json_data.append([
                escape(item.title or 'N/A'),
                escape(item.get_holidayType_display()),
                escape(item.get_appliesTo_display()),
                escape(item.startDate.strftime('%d-%m-%Y') if item.startDate else 'N/A'),
                escape(item.endDate.strftime('%d-%m-%Y') if item.endDate else 'N/A'),
                escape(item.description or ''),
                escape(item.lastEditedBy or 'N/A'),
                escape(item.lastUpdatedOn.strftime('%d-%m-%Y %I:%M %p') if item.lastUpdatedOn else 'N/A'),
                action,
            ])
        return json_data


@transaction.atomic
@csrf_exempt
@login_required
def add_holiday_api(request):
    if request.method != 'POST':
        return ErrorResponse('Invalid request method.', extra={'color': 'red'}).to_json_response()

    try:
        payload = _holiday_payload_from_request(request)
        duplicate = SchoolHoliday.objects.filter(
            isDeleted=False,
            sessionID_id=request.session['current_session']['Id'],
            title__iexact=payload['title'],
            startDate=payload['startDate'],
            endDate=payload['endDate'],
            appliesTo=payload['appliesTo'],
        ).exists()
        if duplicate:
            return ErrorResponse('Holiday already exists for the same date range and audience.', extra={'color': 'orange'}).to_json_response()

        obj = SchoolHoliday(**payload)
        pre_save_with_user.send(sender=SchoolHoliday, instance=obj, user=request.user.pk)
        resync_holidays_for_scope(
            session_id=obj.sessionID_id,
            school_id=obj.schoolID_id,
            start_date=obj.startDate,
            end_date=obj.endDate,
            audiences=holiday_audiences(obj.appliesTo),
            user_id=request.user.pk,
        )
        return SuccessResponse('Holiday added and attendance marked successfully.', extra={'color': 'success'}).to_json_response()
    except ValueError as e:
        return ErrorResponse(str(e), extra={'color': 'red'}).to_json_response()
    except Exception as e:
        logger.error(f'Error in add_holiday_api: {e}')
        return ErrorResponse('Failed to add holiday.', extra={'color': 'red'}).to_json_response()


@login_required
def get_holiday_detail(request):
    try:
        obj = SchoolHoliday.objects.get(
            pk=request.GET.get('id'),
            isDeleted=False,
            sessionID_id=request.session['current_session']['Id'],
        )
        data = {
            'ID': obj.pk,
            'title': obj.title or '',
            'holidayType': obj.holidayType or '',
            'appliesTo': obj.appliesTo or '',
            'startDate': obj.startDate.strftime('%d/%m/%Y') if obj.startDate else '',
            'endDate': obj.endDate.strftime('%d/%m/%Y') if obj.endDate else '',
            'description': obj.description or '',
        }
        return SuccessResponse('Holiday detail fetched successfully.', data=data, extra={'color': 'success'}).to_json_response()
    except Exception as e:
        logger.error(f'Error in get_holiday_detail: {e}')
        return ErrorResponse('Error in fetching holiday details.', extra={'color': 'red'}).to_json_response()


@transaction.atomic
@csrf_exempt
@login_required
def update_holiday_api(request):
    if request.method != 'POST':
        return ErrorResponse('Invalid request method.', extra={'color': 'red'}).to_json_response()

    try:
        edit_id = request.POST.get('editID')
        if not edit_id:
            return ErrorResponse('Holiday id is required.', extra={'color': 'red'}).to_json_response()

        payload = _holiday_payload_from_request(request)
        obj = SchoolHoliday.objects.get(
            pk=int(edit_id),
            isDeleted=False,
            sessionID_id=request.session['current_session']['Id'],
        )
        old_start_date = obj.startDate
        old_end_date = obj.endDate
        old_applies_to = obj.appliesTo
        duplicate = SchoolHoliday.objects.filter(
            isDeleted=False,
            sessionID_id=request.session['current_session']['Id'],
            title__iexact=payload['title'],
            startDate=payload['startDate'],
            endDate=payload['endDate'],
            appliesTo=payload['appliesTo'],
        ).exclude(pk=obj.pk).exists()
        if duplicate:
            return ErrorResponse('Another holiday already exists for the same date range and audience.', extra={'color': 'orange'}).to_json_response()

        for field, value in payload.items():
            setattr(obj, field, value)
        pre_save_with_user.send(sender=SchoolHoliday, instance=obj, user=request.user.pk)
        resync_holidays_for_scope(
            session_id=obj.sessionID_id,
            school_id=obj.schoolID_id,
            start_date=min(old_start_date, obj.startDate),
            end_date=max(old_end_date, obj.endDate),
            audiences=holiday_audiences(old_applies_to, obj.appliesTo),
            user_id=request.user.pk,
        )
        return SuccessResponse('Holiday updated and attendance refreshed successfully.', extra={'color': 'success'}).to_json_response()
    except ValueError as e:
        return ErrorResponse(str(e), extra={'color': 'red'}).to_json_response()
    except Exception as e:
        logger.error(f'Error in update_holiday_api: {e}')
        return ErrorResponse('Failed to update holiday.', extra={'color': 'red'}).to_json_response()


@transaction.atomic
@csrf_exempt
@login_required
def delete_holiday(request):
    if request.method != 'POST':
        return ErrorResponse('Invalid request method.', extra={'color': 'red'}).to_json_response()

    try:
        obj = SchoolHoliday.objects.get(
            pk=int(request.POST.get('dataID')),
            isDeleted=False,
            sessionID_id=request.session['current_session']['Id'],
        )
        start_date = obj.startDate
        end_date = obj.endDate
        applies_to = obj.appliesTo
        obj.isDeleted = True
        pre_save_with_user.send(sender=SchoolHoliday, instance=obj, user=request.user.pk)
        resync_holidays_for_scope(
            session_id=obj.sessionID_id,
            school_id=obj.schoolID_id,
            start_date=start_date,
            end_date=end_date,
            audiences=holiday_audiences(applies_to),
            user_id=request.user.pk,
        )
        return SuccessResponse('Holiday deleted and attendance reverted successfully.', extra={'color': 'success'}).to_json_response()
    except Exception as e:
        logger.error(f'Error in delete_holiday: {e}')
        return ErrorResponse('Error in deleting holiday.', extra={'color': 'red'}).to_json_response()


@transaction.atomic
@csrf_exempt
@login_required
def delete_event_type(request):
    if request.method != 'POST':
        return ErrorResponse('Invalid request method.', extra={'color': 'red'}).to_json_response()

    try:
        obj = EventType.objects.get(
            pk=int(request.POST.get('dataID')),
            isDeleted=False,
            sessionID_id=request.session['current_session']['Id'],
        )
        obj.isDeleted = True
        pre_save_with_user.send(sender=EventType, instance=obj, user=request.user.pk)
        return SuccessResponse('Event type deleted successfully.', extra={'color': 'success'}).to_json_response()
    except Exception as e:
        logger.error(f'Error in delete_event_type: {e}')
        return ErrorResponse('Error in deleting event type.', extra={'color': 'red'}).to_json_response()


@transaction.atomic
@csrf_exempt
@login_required
def add_event_api(request):
    if request.method == 'POST':
        try:
            post_data = request.POST.dict()
            event_type_id = post_data.get("event_type")
            if not event_type_id:
                return ErrorResponse('Event type is required.', extra={'color': 'red'}).to_json_response()
            event_type_obj = EventType.objects.filter(
                id=event_type_id,
                isDeleted=False,
                sessionID_id=request.session['current_session']['Id']
            ).first()
            if not event_type_obj:
                return ErrorResponse('Invalid event type for current session.', extra={'color': 'red'}).to_json_response()
            obj = Event.objects.create(
            eventID_id = event_type_obj.id,
            title  = post_data.get("title"),
            startDate = datetime.strptime(post_data["start_date"], "%d/%m/%Y"),
            endDate = datetime.strptime(post_data["end_date"], "%d/%m/%Y"),
            message = post_data.get("description"),
            sessionID_id = request.session['current_session']['Id'],
            )
            pre_save_with_user.send(sender=Event, instance=obj, user=request.user.pk)

            
            send_event_push_notifications(obj, action='added')
            logger.info("Event added successfully")
            return SuccessResponse('Event added successfully.', extra={'color': 'success'}).to_json_response()
        except Exception as e:
            logger.error(f"Error adding event: {str(e)}")
            return ErrorResponse('Failed to add event.', extra={'color': 'red'}).to_json_response()
    else:
        logger.error("Invalid request method")
        return ErrorResponse('Invalid request method.', extra={'color': 'red'}).to_json_response()


class EventListJson(BaseDatatableView):
    order_columns = ['eventID__name', 'eventID__audience', 'title', 'startDate',
                     'endDate', 'message', 'lastEditedBy', 'lastUpdatedOn']

    def get_initial_queryset(self):
        return Event.objects.select_related('eventID').only(
            'id', 'title', 'startDate', 'endDate', 'message', 'lastEditedBy', 'lastUpdatedOn',
            'eventID__name', 'eventID__audience'
        ).filter(
            isDeleted__exact=False,
            sessionID_id=self.request.session["current_session"]["Id"]
        )

    def filter_queryset(self, qs):

        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(eventID__name__icontains=search) | Q(eventID__audience__icontains=search) | Q(title__icontains=search) | Q(
                    startDate__icontains=search)| Q(
                    endDate__icontains=search)| Q(
                    message__icontains=search)
                |  Q(lastEditedBy__icontains=search) | Q(lastUpdatedOn__icontains=search)
            )

        return qs

    def prepare_results(self, qs):
        json_data = []
        for item in qs:
            action = '''
              <button data-inverted="" data-tooltip="Edit Detail" data-position="left center" data-variation="mini" style="font-size:10px;" onclick = "GetDataDetails('{}')" class="ui circular facebook icon button green">
                <i class="pen icon"></i>
              </button>
              <button data-inverted="" data-tooltip="Delete" data-position="left center" data-variation="mini" style="font-size:10px;" onclick ="delData('{}')" class="ui circular youtube icon button" style="margin-left: 3px">
                <i class="trash alternate icon"></i>
              </button></td>'''.format(item.pk, item.pk)
            json_data.append([
                escape(item.eventID.name if item.eventID else 'N/A'),
                escape(item.eventID.get_audience_display() if item.eventID else 'General'),
                escape(item.title),
                escape(item.startDate.strftime('%d-%m-%Y')),
                escape(item.endDate.strftime('%d-%m-%Y')),
                escape(item.message),
                escape(item.lastEditedBy or 'N/A'),
                escape(item.lastUpdatedOn.strftime('%d-%m-%Y %I:%M %p') if item.lastUpdatedOn else 'N/A'),
                action,

            ])

        return json_data


@transaction.atomic
@csrf_exempt
@login_required
def delete_event(request):
    if request.method == 'POST':
        try:
            id = request.POST.get("dataID")
            instance = Event.objects.get(pk=int(id), isDeleted=False,
                                            sessionID_id=request.session['current_session']['Id'])
            instance.isDeleted = True
            pre_save_with_user.send(sender=Event, instance=instance, user=request.user.pk)
            instance.save()
            logger.info(f"Event detail deleted successfully {request.session['current_session']['Id']} event title {instance.title}")
            return SuccessResponse("Event detail deleted successfully.").to_json_response()
        except Exception as e:
            logger.error(f"Error in deleting event: {e}")
            return ErrorResponse("Error in deleting Event details").to_json_response()
    else:
        logger.error("Method not allowed")
        return ErrorResponse("Method not allowed").to_json_response()        


@login_required
def get_event_detail(request, **kwargs):
    try:
        id = request.GET.get('id')
        obj = Event.objects.get(pk=id, isDeleted=False, sessionID_id=request.session['current_session']['Id'])
        obj_dic = {
            'ID': obj.pk,
            'eventTypeID': obj.eventID_id if obj.eventID_id else '',
            'audience': obj.eventID.audience if obj.eventID else 'general',
            'title': obj.title,
            'startDate': obj.startDate.strftime('%d/%m/%Y'),
            'endDate': obj.endDate.strftime('%d/%m/%Y'),
            'message': obj.message
        }
        logger.info(f"Event detail fetched successfully {request.session['current_session']['Id']} event title {obj.title}")
        return SuccessResponse("Event detail fetched successfully.", data=obj_dic).to_json_response()
    except Exception as e:
        logger.error(f"Error in fetching event details: {e}")
        return ErrorResponse("Error in fetching event details").to_json_response()


@transaction.atomic
@csrf_exempt
@login_required
def update_event_api(request):
    if request.method == 'POST':
        try:
            post_data = request.POST.dict()
            event_type_id = post_data.get("event_type")
            obj = Event.objects.get(pk=int(post_data.get("editID")), isDeleted=False,
                                   sessionID_id=request.session['current_session']['Id'])

            # Keep existing type on edit if dropdown value is temporarily empty on UI.
            if event_type_id:
                event_type_obj = EventType.objects.filter(
                    id=event_type_id,
                    isDeleted=False,
                    sessionID_id=request.session['current_session']['Id']
                ).first()
                if not event_type_obj:
                    return ErrorResponse('Invalid event type for current session.', extra={'color': 'red'}).to_json_response()
                obj.eventID_id = event_type_obj.id
            elif not obj.eventID_id:
                return ErrorResponse('Event type is required.', extra={'color': 'red'}).to_json_response()

            obj.title = post_data.get("title")
            obj.startDate = datetime.strptime(post_data["start_date"], "%d/%m/%Y")
            obj.endDate = datetime.strptime(post_data["end_date"], "%d/%m/%Y")
            obj.message = post_data.get("description")
            obj.save()
            pre_save_with_user.send(sender=Event, instance=obj, user=request.user.pk)
            send_event_push_notifications(obj, action='updated')

            logger.info("Event detail updated successfully")
            return SuccessResponse('Event detail updated successfully.', extra={'color': 'success'}).to_json_response()
        except Exception as e:
            logger.error(f"Error updating event: {str(e)}")
            return ErrorResponse('Failed to update event.', extra={'color': 'red'}).to_json_response()
    else:
        logger.error("Invalid request method")
        return ErrorResponse('Invalid request method.', extra={'color': 'red'}).to_json_response()

# Parents API --------------
class ParentsListJson(BaseDatatableView):
    order_columns = ['fatherName', 'fatherPhone',
                     'motherName', 'motherPhone', 
                     'guardianName', 'guardianPhone',
                     'totalFamilyMembers',
                     'lastEditedBy', 'lastUpdatedOn']

    def get_initial_queryset(self):
        current_session_id = self.request.session["current_session"]["Id"]
        return Parent.objects.only(
            'id', 'fatherName', 'fatherPhone', 'motherName', 'motherPhone',
            'guardianName', 'guardianPhone', 'totalFamilyMembers', 'lastEditedBy', 'lastUpdatedOn'
        ).prefetch_related(
            Prefetch(
                'student_set',
                queryset=Student.objects.select_related('standardID').only(
                    'id', 'name', 'roll', 'photo',
                    'standardID__name', 'standardID__section', 'standardID__hasSection'
                ).filter(isDeleted=False, sessionID_id=current_session_id),
                to_attr='active_students',
            )
        ).filter(
            isDeleted__exact=False,
            sessionID_id=current_session_id
        )

    def filter_queryset(self, qs):

        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(fatherName__icontains=search) | Q(
                    fatherPhone__icontains=search)| Q(
                    motherName__icontains=search)| Q(
                    motherPhone__icontains=search)
                |  Q(guardianName__icontains=search) | Q(guardianPhone__icontains=search)
                |  Q(totalFamilyMembers__icontains=search)
                | Q(lastEditedBy__icontains=search) | Q(lastUpdatedOn__icontains=search)
            )

        return qs

    def prepare_results(self, qs):
        json_data = []
        for item in qs:
            action = '''
              <a href="/management/parent_detail/{}/" data-inverted="" data-tooltip="View Detail" data-position="left center" data-variation="mini" style="font-size:10px;" class="ui circular facebook icon button purple">
                <i class="eye icon"></i>
              </a>
              <a href="/management/edit_parent/{}/" data-inverted="" data-tooltip="Edit Parent" data-position="left center" data-variation="mini" style="font-size:10px;" class="ui circular icon button blue">
                <i class="edit icon"></i>
              </a>'''.format(item.pk, item.pk)
            students = getattr(item, 'active_students', [])
            student_html = []
            for s in students:
                student_name = escape(s.name or 'N/A')
                roll_no = escape(str(s.roll) if s.roll is not None else 'N/A')
                class_name = escape(s.standardID.name if s.standardID else 'N/A')
                section = ''
                if s.standardID and s.standardID.hasSection == "Yes" and s.standardID.section:
                    section = f"-{escape(s.standardID.section)}"
                student_html.append(f'''
                    <div class="parent-student-chip">
                        <img src="{_safe_image_url(s.photo)}" alt="{student_name}">
                        <div class="parent-student-meta">
                            <div class="student-name">{student_name}</div>
                            <div class="student-sub">Roll: {roll_no} | Class: {class_name}{section}</div>
                        </div>
                    </div>
                    ''')
            students_markup = ''.join(student_html) if student_html else '<span class="ui grey text">N/A</span>'

            json_data.append([
                escape(item.fatherName),
                escape(item.fatherPhone if item.fatherPhone else 'N/A'),
                escape(item.motherName),
                escape(item.motherPhone if item.motherPhone else 'N/A'),
                escape(item.guardianName),
                escape(item.guardianPhone if item.guardianPhone else 'N/A'),
                escape(item.totalFamilyMembers if item.totalFamilyMembers else '1'),
                f'<div class="parent-students-wrap">{students_markup}</div>',
                escape(item.lastEditedBy or 'N/A'),
                escape(item.lastUpdatedOn.strftime('%d-%m-%Y %I:%M %p') if item.lastUpdatedOn else 'N/A'),
                action,

            ])

        return json_data
