from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
import json

from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Group
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
from homeApp.models import SchoolDetail, SchoolSession
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
from managementApp.services.session_rollover import preview_session_import, run_session_import
from managementApp.signals import pre_save_with_user
from managementApp.leave_utils import approved_leave_for_date, approved_leave_map_for_date
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
            school = SchoolDetail.objects.filter(ownerID__userID_id=request.user.id, isDeleted=False).order_by('-datetime').first()
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
            school = SchoolDetail.objects.filter(ownerID__userID_id=request.user.id, isDeleted=False).order_by('-datetime').first()
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
    try:
        standard_id = int(standard)
    except (TypeError, ValueError):
        return _api_response({'status': 'success', 'data': [], 'color': 'success'}, safe=False)
    rows = Student.objects.filter(
        isDeleted=False,
        sessionID_id=_current_session_id(request),
        standardID_id=standard_id
    ).values('id', 'name', 'roll').order_by('roll')
    data = []
    for row in rows:
        roll = row.get('roll')
        try:
            roll_label = str(int(float(roll)))
        except Exception:
            roll_label = str(roll or 'N/A')
        data.append({'ID': row['id'], 'Name': f"{row.get('name') or 'N/A'} - {roll_label}"})
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

class TakeStudentAttendanceByClassJson(BaseDatatableView):
    order_columns = ['studentID.photo', 'studentID.name', 'studentID.roll', 'isPresent', 'absentReason', 'lastEditedBy', 'lastUpdatedOn']

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
                for s in students:
                    leave_obj = leave_map.get(s.id)
                    leave_reason = ''
                    if leave_obj:
                        leave_reason = f"Approved Leave: {leave_obj.leaveTypeID.name if leave_obj.leaveTypeID else 'Leave'}"
                    try:
                        attendance_obj = StudentAttendance.objects.get(studentID_id=s.id, attendanceDate__icontains=aDate,
                                                                       standardID_id=int(standard), bySubject=False,
                                                                       sessionID_id=self.request.session["current_session"]["Id"])
                        if leave_reason and (attendance_obj.isPresent or not attendance_obj.absentReason):
                            attendance_obj.isPresent = False
                            attendance_obj.absentReason = leave_reason
                            pre_save_with_user.send(sender=StudentAttendance, instance=attendance_obj, user=self.request.user.pk)
                            attendance_obj.save()

                    except:
                        instance = StudentAttendance.objects.create(studentID_id=s.id, attendanceDate=aDate,
                                                                    standardID_id=int(standard), isPresent=False,
                                                                    bySubject=False, absentReason=leave_reason)
                        pre_save_with_user.send(sender=StudentAttendance, instance=instance, user=self.request.user.pk)

                return StudentAttendance.objects.select_related().filter(isDeleted__exact=False, bySubject=False,
                                                                         sessionID_id=
                                                                         self.request.session["current_session"][
                                                                             "Id"], attendanceDate__icontains=aDate,
                                                                         standardID_id=int(standard))
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
                    for s in students:
                        leave_obj = leave_map.get(s.id)
                        leave_reason = ''
                        if leave_obj:
                            leave_reason = f"Approved Leave: {leave_obj.leaveTypeID.name if leave_obj.leaveTypeID else 'Leave'}"
                        try:
                            attendance_obj = StudentAttendance.objects.get(studentID_id=s.id, attendanceDate__icontains=sDate,
                                                                           standardID_id=obj.standardID_id, bySubject=True,
                                                                           subjectID_id=obj.subjectID_id,
                                                                           sessionID_id=self.request.session["current_session"]["Id"])
                            if leave_reason and (attendance_obj.isPresent or not attendance_obj.absentReason):
                                attendance_obj.isPresent = False
                                attendance_obj.absentReason = leave_reason
                                pre_save_with_user.send(sender=StudentAttendance, instance=attendance_obj,
                                                        user=self.request.user.pk)
                                attendance_obj.save()
                        except:
                            instance = StudentAttendance.objects.create(studentID_id=s.id, attendanceDate=sDate,
                                                                        subjectID_id=obj.subjectID_id,
                                                                        standardID_id=obj.standardID_id,
                                                                        isPresent=False, bySubject=True,
                                                                        absentReason=leave_reason)
                            pre_save_with_user.send(sender=StudentAttendance, instance=instance,
                                                    user=self.request.user.pk)
                    return StudentAttendance.objects.select_related().filter(isDeleted__exact=False, bySubject=True,
                                                                             sessionID_id=
                                                                             self.request.session["current_session"][
                                                                                 "Id"], attendanceDate__icontains=sDate,
                                                                             subjectID_id=obj.subjectID_id,
                                                                             standardID_id=obj.standardID_id)
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
            if item.studentID and item.studentID.photo:
                images = _avatar_image_html(item.studentID.photo)
            else:
                images = _avatar_image_html(None)

            action = '''<button class="ui mini primary button" onclick="pushAttendance({})">
  Save
</button>'''.format(item.pk),
            if item.isPresent:
                is_present = '''
            <div class="ui checkbox">
  <input type="checkbox" name="isPresent{}" id="isPresent{}" checked >
  <label>Mark as Present</label>
</div>
            '''.format(item.pk, item.pk)
            else:
                is_present = '''
                            <div class="ui checkbox">
                  <input type="checkbox" name="isPresent{}" id="isPresent{}" >
                  <label>Mark as Present</label>
                </div>
                            '''.format(item.pk, item.pk)

            reason = '''<div class="ui tiny input fluid">
  <input type="text" placeholder="Reason for Absent" name="reason{}" id="reason{}" value = "{}">
</div>
            '''.format(item.pk, item.pk, item.absentReason)

            json_data.append([
                images,
                escape(item.studentID.name),
                escape(item.studentID.roll or 'N/A'),
                is_present,
                reason,
                escape(item.lastEditedBy or 'N/A'),
                escape(item.lastUpdatedOn.strftime('%d-%m-%Y %I:%M %p') if item.lastUpdatedOn else 'N/A'),
                action,

            ])

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
            instance.isPresent = isPresent
            instance.absentReason = reason
            pre_save_with_user.send(sender=StudentAttendance, instance=instance, user=request.user.pk)
            instance.save()
            return _api_response(
                {'status': 'success', 'message': 'Attendance added successfully.', 'color': 'success'},
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

        instance.isPresent = is_present
        instance.absentReason = '' if is_present else reason
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
        json_data = []
        for item in qs:
            images = _avatar_image_html(item.photo)
            if dateRangeSubject == "all":
                present_count = StudentAttendance.objects.filter(studentID_id=item.id, isPresent=True, bySubject=False,
                                                                 isHoliday=False,
                                                                 attendanceDate__range=[dateRangeStartDate,
                                                                                        dateRangeEndDate + timedelta(
                                                                                            days=1)],
                                                                 standardID_id=int(dateRangeStandard)).count()
                absent_count = StudentAttendance.objects.filter(studentID_id=item.id, isPresent=False, bySubject=False,
                                                                isHoliday=False,
                                                                attendanceDate__range=[dateRangeStartDate,
                                                                                       dateRangeEndDate + timedelta(
                                                                                           days=1)],
                                                                standardID_id=int(dateRangeStandard)).count()

            else:
                present_count = StudentAttendance.objects.filter(studentID_id=item.id, isPresent=True, bySubject=True,
                                                                 subjectID_id=int(dateRangeSubject),
                                                                 isHoliday=False,
                                                                 attendanceDate__range=[dateRangeStartDate,
                                                                                        dateRangeEndDate + timedelta(
                                                                                            days=1)],
                                                                 standardID_id=int(dateRangeStandard)).count()
                absent_count = StudentAttendance.objects.filter(studentID_id=item.id, isPresent=False, bySubject=True,
                                                                isHoliday=False, subjectID_id=int(dateRangeSubject),
                                                                attendanceDate__range=[dateRangeStartDate,
                                                                                       dateRangeEndDate + timedelta(
                                                                                           days=1)],
                                                                standardID_id=int(dateRangeStandard)).count()

            if present_count + absent_count != 0:
                percentage = present_count / (present_count + absent_count) * 100
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
                present_count + absent_count,
                round(percentage, 2)

            ])

        return json_data


class StudentAttendanceHistoryByDateRangeAndStudentJson(BaseDatatableView):
    order_columns = ['attendanceDate', 'isPresent', 'isPresent', 'absentReason', 'lastEditedBy', 'lastUpdatedOn']

    @transaction.atomic
    def get_initial_queryset(self):
        try:
            ByStudentSubject = self.request.GET.get("ByStudentSubject")
            ByStudentStudent = self.request.GET.get("ByStudentStudent")
            ByStudentStartDate = self.request.GET.get("ByStudentStartDate")
            ByStudentEndDate = self.request.GET.get("ByStudentEndDate")
            ByStudentStartDate = datetime.strptime(ByStudentStartDate, '%d/%m/%Y')
            ByStudentEndDate = datetime.strptime(ByStudentEndDate, '%d/%m/%Y')
            if ByStudentSubject == "all":
                return StudentAttendance.objects.select_related().filter(isDeleted__exact=False, isHoliday=False,
                                                                         studentID_id=int(ByStudentStudent),
                                                                         attendanceDate__range=[ByStudentStartDate,
                                                                                                ByStudentEndDate + timedelta(
                                                                                                    days=1)],
                                                                         sessionID_id=
                                                                         self.request.session["current_session"][
                                                                             "Id"]).order_by('attendanceDate')
            else:
                return StudentAttendance.objects.select_related().filter(isDeleted__exact=False, isHoliday=False,
                                                                         subjectID_id=int(ByStudentSubject),
                                                                         studentID_id=int(ByStudentStudent),
                                                                         attendanceDate__range=[ByStudentStartDate,
                                                                                                ByStudentEndDate + timedelta(
                                                                                                    days=1)],
                                                                         sessionID_id=
                                                                         self.request.session["current_session"][
                                                                             "Id"]).order_by('attendanceDate')


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
        for item in qs:
            if item.isPresent == True:
                Present = 'Yes'
                Absent = 'No'
            else:
                Present = 'No'
                Absent = 'Yes'

            json_data.append([
                escape(item.attendanceDate.strftime('%d-%m-%Y')),
                escape(Present),
                escape(Absent),
                escape(item.absentReason),
                escape(item.lastEditedBy or 'N/A'),
                escape(item.lastUpdatedOn.strftime('%d-%m-%Y %I:%M %p') if item.lastUpdatedOn else 'N/A'),

            ])

        return json_data


class TakeTeacherAttendanceJson(BaseDatatableView):
    order_columns = ['teacherID.photo', 'teacherID.name', 'teacherID.staffType', 'teacherID.employeeCode', 'isPresent',
                     'absentReason', 'lastEditedBy', 'lastUpdatedOn']

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
            for s in teachers:
                leave_obj = leave_map.get(s.id)
                leave_reason = ''
                if leave_obj:
                    leave_reason = f"Approved Leave: {leave_obj.leaveTypeID.name if leave_obj.leaveTypeID else 'Leave'}"
                try:
                    attendance_obj = TeacherAttendance.objects.get(attendanceDate__icontains=aDate, isDeleted=False, teacherID_id=s.id,
                                                                   sessionID_id=self.request.session["current_session"]["Id"])
                    if leave_reason and (attendance_obj.isPresent or not attendance_obj.absentReason):
                        attendance_obj.isPresent = False
                        attendance_obj.absentReason = leave_reason
                        pre_save_with_user.send(sender=TeacherAttendance, instance=attendance_obj, user=self.request.user.pk)
                        attendance_obj.save()

                except:
                    instance = TeacherAttendance.objects.create(attendanceDate=aDate, isDeleted=False,
                                                                teacherID_id=s.id, absentReason=leave_reason)
                    pre_save_with_user.send(sender=TeacherAttendance, instance=instance, user=self.request.user.pk)

            return TeacherAttendance.objects.select_related().filter(isDeleted__exact=False,
                                                                     attendanceDate__icontains=aDate, isDeleted=False,
                                                                     sessionID_id=
                                                                     self.request.session["current_session"][
                                                                         "Id"])

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
            images = _avatar_image_html(item.teacherID.photo)

            action = '''<button class="ui mini primary button" onclick="pushAttendance({})">
  Save
</button>'''.format(item.pk),
            if item.isPresent:
                is_present = '''
            <div class="ui checkbox">
  <input type="checkbox" name="isPresent{}" id="isPresent{}" checked >
  <label>Mark as Present</label>
</div>
            '''.format(item.pk, item.pk)
            else:
                is_present = '''
                            <div class="ui checkbox">
                  <input type="checkbox" name="isPresent{}" id="isPresent{}" >
                  <label>Mark as Present</label>
                </div>
                            '''.format(item.pk, item.pk)

            reason = '''<div class="ui tiny input fluid">
  <input type="text" placeholder="Reason for Absent" name="reason{}" id="reason{}" value = "{}">
</div>
            '''.format(item.pk, item.pk, item.absentReason)

            json_data.append([
                images,
                escape(item.teacherID.name),
                escape(item.teacherID.staffType),
                escape(item.teacherID.employeeCode or 'N/A'),
                is_present,
                reason,
                escape(item.lastEditedBy or 'N/A'),
                escape(item.lastUpdatedOn.strftime('%d-%m-%Y %I:%M %p') if item.lastUpdatedOn else 'N/A'),
                action,

            ])

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
            instance.isPresent = isPresent
            instance.absentReason = reason
            pre_save_with_user.send(sender=TeacherAttendance, instance=instance, user=request.user.pk)
            instance.save()
            return _api_response(
                {'status': 'success', 'message': 'Attendance added successfully.', 'color': 'success'},
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

        instance.isPresent = is_present
        instance.absentReason = '' if is_present else reason
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

            for item in qs:
                images = _avatar_image_html(item.photo)
                attendance_qs = TeacherAttendance.objects.filter(
                    teacherID_id=item.id,
                    isHoliday=False,
                    isDeleted=False,
                    sessionID_id=self.request.session["current_session"]["Id"],
                    attendanceDate__range=[dateRangeStartDate, dateRangeEndDate + timedelta(days=1)],
                )
                leave_count = _count_approved_teacher_leave_days(
                    session_id=self.request.session["current_session"]["Id"],
                    teacher_id=item.id,
                    start_date=dateRangeStartDate.date(),
                    end_date=dateRangeEndDate.date(),
                )
                present_count = attendance_qs.filter(isPresent=True).count()
                absent_count = attendance_qs.filter(isPresent=False).exclude(
                    absentReason__istartswith='Approved Leave'
                ).count()
                total_days = present_count + absent_count + leave_count

                if total_days != 0:
                    percentage = present_count / total_days * 100
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
                    total_days,
                    round(percentage, 2)

                ])

        except:
            pass
        return json_data


class StaffAttendanceHistoryByDateRangeAndStaffJson(BaseDatatableView):
    order_columns = ['attendanceDate', 'isPresent', 'isPresent', 'isPresent', 'absentReason', 'lastEditedBy', 'lastUpdatedOn']

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
                leave_type_name = leave.leaveTypeID.name if leave.leaveTypeID else 'Leave'
                leave_reason = f'Approved Leave: {leave_type_name}'
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
                        )
                        pre_save_with_user.send(sender=TeacherAttendance, instance=instance, user=self.request.user.pk)
                    day += timedelta(days=1)

            return TeacherAttendance.objects.select_related().filter(isDeleted__exact=False, isHoliday=False,
                                                                     teacherID_id=teacher_id,
                                                                     attendanceDate__range=[ByStudentStartDate,
                                                                                            ByStudentEndDate + timedelta(
                                                                                                days=1)],
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
        for item in qs:
            is_leave = (item.absentReason or '').strip().lower().startswith('approved leave')
            if item.isPresent is True:
                Present = 'Yes'
                Absent = 'No'
                Leave = 'No'
            else:
                Present = 'No'
                if is_leave:
                    Absent = 'No'
                    Leave = 'Yes'
                else:
                    Absent = 'Yes'
                    Leave = 'No'

            json_data.append([
                escape(item.attendanceDate.strftime('%d-%m-%Y')),
                escape(Present),
                escape(Absent),
                escape(Leave),
                escape(item.absentReason),
                escape(item.lastEditedBy or 'N/A'),
                escape(item.lastUpdatedOn.strftime('%d-%m-%Y %I:%M %p') if item.lastUpdatedOn else 'N/A'),

            ])

        return json_data


# Student fee ---------------------------------------------------------------
class FeeByStudentJson(BaseDatatableView):
    order_columns = ['month', 'isPaid', 'payDate', 'amount', 'note', 'lastEditedBy', 'lastUpdatedOn']

    @transaction.atomic
    def get_initial_queryset(self):
        try:
            student = self.request.GET.get("student")
            standard = self.request.GET.get("standard")
            session_id = self.request.session["current_session"]["Id"]
            for month_name, year_value, month_no, period_start, period_end in _session_month_rows(session_id):
                fee_obj = StudentFee.objects.filter(
                    studentID_id=int(student),
                    month__iexact=month_name,
                    standardID_id=int(standard),
                    isDeleted=False,
                    sessionID_id=session_id,
                ).order_by('id').first()

                if not fee_obj:
                    fee_obj = StudentFee.objects.create(
                        studentID_id=int(student),
                        month=month_name,
                        standardID_id=int(standard),
                        feeMonth=month_no,
                        feeYear=year_value,
                        periodStartDate=period_start,
                        periodEndDate=period_end,
                        dueDate=period_start,
                    )
                    pre_save_with_user.send(sender=StudentFee, instance=fee_obj, user=self.request.user.pk)
                else:
                    update_fields = []
                    if not fee_obj.feeMonth:
                        fee_obj.feeMonth = month_no
                        update_fields.append('feeMonth')
                    if not fee_obj.feeYear:
                        fee_obj.feeYear = year_value
                        update_fields.append('feeYear')
                    if not fee_obj.periodStartDate:
                        fee_obj.periodStartDate = period_start
                        update_fields.append('periodStartDate')
                    if not fee_obj.periodEndDate:
                        fee_obj.periodEndDate = period_end
                        update_fields.append('periodEndDate')
                    if not fee_obj.dueDate:
                        fee_obj.dueDate = period_start
                        update_fields.append('dueDate')
                    if update_fields:
                        fee_obj.save(update_fields=update_fields + ['lastUpdatedOn'])

            fee_qs = StudentFee.objects.select_related().filter(
                studentID_id=int(student),
                standardID_id=int(standard),
                isDeleted=False,
                sessionID_id=session_id,
            )
            return _restrict_fee_queryset_to_session_months(fee_qs, session_id).order_by('feeYear', 'feeMonth', 'id')

        except:
            return StudentFee.objects.none()

    def filter_queryset(self, qs):

        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(month__icontains=search)
                | Q(amount__icontains=search)
                | Q(payDate__icontains=search)
                | Q(isPaid__icontains=search) | Q(note__icontains=search)
                | Q(lastEditedBy__icontains=search) | Q(lastUpdatedOn__icontains=search)
            )

        return qs

    def prepare_results(self, qs):
        json_data = []
        for item in qs:

            action = '''<button class="ui mini primary button" onclick="pushFee({})">
  Save
</button>'''.format(item.pk),
            if item.isPaid:
                is_present = '''
            <div class="ui checkbox">
  <input type="checkbox" name="isPresent{}" id="isPresent{}" checked >
  <label>Mark as Pay</label>
</div>
            '''.format(item.pk, item.pk)
            else:
                is_present = '''
                            <div class="ui checkbox">
                  <input type="checkbox" name="isPresent{}" id="isPresent{}" >
                  <label>Mark as Paid</label>
                </div>
                            '''.format(item.pk, item.pk)

            reason = '''<div class="ui tiny input fluid">
  <input type="text" placeholder="Remark" name="reason{}" id="reason{}" value = "{}">
</div>
            '''.format(item.pk, item.pk, item.note)
            amount = '''<div class="ui tiny input fluid">
              <input type="number" placeholder="Amount" name="amount{}" id="amount{}" value = "{}">
            </div>
                        '''.format(item.pk, item.pk, item.amount)

            if item.payDate:
                payDate = item.payDate.strftime('%d-%m-%Y')
            else:
                payDate = 'N/A'

            json_data.append([
                escape(_fee_month_label(item)),
                is_present,
                payDate,
                amount,
                reason,
                escape(item.lastEditedBy or 'N/A'),
                escape(item.lastUpdatedOn.strftime('%d-%m-%Y %I:%M %p') if item.lastUpdatedOn else 'N/A'),
                action,

            ])

        return json_data


@transaction.atomic
@csrf_exempt
@login_required
def add_student_fee_api(request):
    if request.method == 'POST':
        id = request.POST.get("id")
        isPresent = request.POST.get("isPresent")
        reason = request.POST.get("reason")
        amount = request.POST.get("amount")
        try:
            instance = StudentFee.objects.get(pk=int(id))
            if isPresent == 'true':
                isPresent = True
            else:
                isPresent = False
            instance.isPaid = isPresent
            instance.note = reason
            instance.amount = float(amount)
            instance.payDate = datetime.today().date()
            pre_save_with_user.send(sender=StudentFee, instance=instance, user=request.user.pk)
            instance.save()
            try:
                _sync_student_fee_finance(request, instance)
            except Exception as exc:
                logger.error(f"Finance sync failed for add_student_fee_api fee={instance.id}: {exc}")
            return _api_response(
                {'status': 'success', 'message': 'Student fee added successfully.', 'color': 'success'},
                safe=False)
        except:

            return _api_response({'status': 'error'}, safe=False)


class StudentFeeDetailsByClassJson(BaseDatatableView):
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
        session_id = self.request.session["current_session"]["Id"]
        session_month_rows = _session_month_rows(session_id)
        json_data = []
        for item in qs:
            paid_fee_qs = StudentFee.objects.filter(
                studentID_id=item.id,
                isDeleted=False,
                isPaid=True,
                sessionID_id=session_id,
            )
            paid_fee_rows = list(_restrict_fee_queryset_to_session_months(paid_fee_qs, session_id).values('feeYear', 'feeMonth', 'month'))
            paid_year_month = {
                (row.get('feeYear'), row.get('feeMonth'))
                for row in paid_fee_rows
                if row.get('feeYear') and row.get('feeMonth')
            }
            paid_month_names = {
                (row.get('month') or '').strip().lower()
                for row in paid_fee_rows
                if row.get('month') and (not row.get('feeYear') or not row.get('feeMonth'))
            }

            month_status = []
            for month_name, year_value, month_no, _, _ in session_month_rows:
                is_paid = (
                    (year_value, month_no) in paid_year_month
                    or month_name.lower() in paid_month_names
                )
                month_status.append('Paid' if is_paid else 'Due')

            images = _avatar_image_html(item.photo)
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
                *month_status,

            ])

        return json_data


class StudentFeeDetailsByStudentJson(BaseDatatableView):
    order_columns = ['month', 'isPaid', 'payDate', 'amount', 'note', 'lastEditedBy', 'lastUpdatedOn']

    @transaction.atomic
    def get_initial_queryset(self):
        try:
            standardByStudent = self.request.GET.get("standardByStudent")
            student = self.request.GET.get("student")

            session_id = self.request.session["current_session"]["Id"]
            fee_qs = StudentFee.objects.select_related().filter(
                isDeleted__exact=False,
                studentID_id=int(student),
                standardID_id=int(standardByStudent),
                sessionID_id=session_id,
            )
            return _restrict_fee_queryset_to_session_months(fee_qs, session_id).order_by('feeYear', 'feeMonth', 'id')
        except:
            return StudentFee.objects.none()

    def filter_queryset(self, qs):

        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(month__icontains=search) | Q(note__icontains=search)
                | Q(amount__icontains=search)
                | Q(lastEditedBy__icontains=search) | Q(lastUpdatedOn__icontains=search)
            )

        return qs

    def prepare_results(self, qs):

        json_data = []
        for item in qs:
            if item.isPaid == True:
                status = 'Paid'
                payDate = item.payDate.strftime('%d-%m-%Y')
            else:
                status = 'Due'
                payDate = 'N/A'

            json_data.append([

                escape(_fee_month_label(item)),
                status,
                payDate,
                escape(item.amount),
                escape(item.note),
                escape(item.lastEditedBy or 'N/A'),
                escape(item.lastUpdatedOn.strftime('%d-%m-%Y %I:%M %p') if item.lastUpdatedOn else 'N/A'),

            ])

        return json_data


@login_required
@check_groups('Admin', 'Owner')
def get_finance_account_options_api(request):
    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    if not school_id or not session_id:
        return SuccessResponse('Finance account options loaded.', data=[]).to_json_response()
    bootstrap_school_finance(school_id=school_id, session_id=session_id, user_obj=request.user)
    rows = FinanceAccount.objects.filter(
        schoolID_id=school_id,
        sessionID_id=session_id,
        isDeleted=False,
        isActive=True,
    ).order_by('accountType', 'accountName').values('id', 'accountCode', 'accountName', 'accountType')
    data = [
        {
            'ID': row['id'],
            'Code': row['accountCode'],
            'Name': row['accountName'],
            'Type': row['accountType'],
            'Label': f"{row['accountCode']} - {row['accountName']}",
        }
        for row in rows
    ]
    return SuccessResponse('Finance account options loaded.', data=data).to_json_response()


@login_required
@check_groups('Admin', 'Owner')
def get_finance_settings_api(request):
    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    if not school_id or not session_id:
        return SuccessResponse('Finance settings loaded successfully.', data={}).to_json_response()

    bootstrap_school_finance(school_id=school_id, session_id=session_id, user_obj=request.user)
    config_obj = get_finance_configuration(school_id=school_id, session_id=session_id, user_obj=request.user)
    account_rows = FinanceAccount.objects.filter(
        schoolID_id=school_id,
        sessionID_id=session_id,
        isDeleted=False,
        isActive=True,
    ).order_by('accountType', 'accountName').values('id', 'accountCode', 'accountName', 'accountType')
    account_options = [
        {
            'ID': row['id'],
            'Code': row['accountCode'],
            'Name': row['accountName'],
            'Type': row['accountType'],
            'Label': f"{row['accountCode']} - {row['accountName']}",
        }
        for row in account_rows
    ]
    return SuccessResponse('Finance settings loaded successfully.', data={
        'settings': _serialize_finance_configuration(config_obj),
        'accountOptions': account_options,
        'previews': {
            'receipt': preview_finance_document_number(document_type='receipt', school_id=school_id, session_id=session_id, user_obj=request.user),
            'voucher': preview_finance_document_number(document_type='voucher', school_id=school_id, session_id=session_id, user_obj=request.user),
            'refund': preview_finance_document_number(document_type='refund', school_id=school_id, session_id=session_id, user_obj=request.user),
            'transaction': preview_finance_document_number(document_type='transaction', school_id=school_id, session_id=session_id, user_obj=request.user),
            'payroll': preview_finance_document_number(document_type='payroll', school_id=school_id, session_id=session_id, user_obj=request.user),
        },
    }).to_json_response()


@transaction.atomic
@csrf_exempt
@login_required
@check_groups('Admin', 'Owner')
def upsert_finance_settings_api(request):
    if request.method != 'POST':
        return ErrorResponse('Method not allowed.', status_code=405).to_json_response()

    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    if not school_id or not session_id:
        return ErrorResponse('School session was not found.').to_json_response()

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
    config_obj.lastEditedBy = (f'{request.user.first_name} {request.user.last_name}'.strip() or request.user.username)
    config_obj.updatedByUserID = request.user
    config_obj.full_clean()
    config_obj.save()

    bootstrap_school_finance(school_id=school_id, session_id=session_id, user_obj=request.user)

    return SuccessResponse('Finance settings saved successfully.', data={
        'settings': _serialize_finance_configuration(config_obj),
        'previews': {
            'receipt': preview_finance_document_number(document_type='receipt', school_id=school_id, session_id=session_id, user_obj=request.user),
            'voucher': preview_finance_document_number(document_type='voucher', school_id=school_id, session_id=session_id, user_obj=request.user),
            'refund': preview_finance_document_number(document_type='refund', school_id=school_id, session_id=session_id, user_obj=request.user),
            'transaction': preview_finance_document_number(document_type='transaction', school_id=school_id, session_id=session_id, user_obj=request.user),
            'payroll': preview_finance_document_number(document_type='payroll', school_id=school_id, session_id=session_id, user_obj=request.user),
        },
    }).to_json_response()


@login_required
@check_groups('Admin', 'Owner')
def get_fee_head_list_api(request):
    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    if not school_id or not session_id:
        return SuccessResponse('Fee heads loaded successfully.', data=[]).to_json_response()
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
            'isSystemGenerated': bool(row.code in {'ADMISSION_FEE', 'MONTHLY_STUDENT_FEE', 'MISC_FEE'}),
            'updatedOn': row.lastUpdatedOn.strftime('%d-%m-%Y %I:%M %p') if row.lastUpdatedOn else 'N/A',
        })
    return SuccessResponse('Fee heads loaded successfully.', data=data).to_json_response()


class FinanceFeeHeadListJson(BaseDatatableView):
    order_columns = ['code', 'name', 'category', 'defaultAmount', 'recurrenceType', 'incomeAccountID__accountName',
                     'receivableAccountID__accountName', 'isActive', 'lastUpdatedOn', 'id']

    def get_initial_queryset(self):
        school_id = _current_school_id(self.request)
        session_id = _current_session_id(self.request)
        if not school_id or not session_id:
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
            if item.code not in {'ADMISSION_FEE', 'MONTHLY_STUDENT_FEE', 'MISC_FEE'}:
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
        return json_data


@transaction.atomic
@csrf_exempt
@login_required
@check_groups('Admin', 'Owner')
def upsert_fee_head_api(request):
    if request.method != 'POST':
        return ErrorResponse('Method not allowed.', status_code=405).to_json_response()

    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    if not school_id or not session_id:
        return ErrorResponse('School session was not found.').to_json_response()

    bootstrap_school_finance(school_id=school_id, session_id=session_id, user_obj=request.user)

    fee_head_id = request.POST.get('id')
    code = (request.POST.get('code') or '').strip().upper().replace(' ', '_')
    name = (request.POST.get('name') or '').strip()
    category = (request.POST.get('category') or 'misc').strip()
    recurrence_type = (request.POST.get('recurrenceType') or 'one_time').strip()
    income_account_id = request.POST.get('incomeAccountID')
    receivable_account_id = request.POST.get('receivableAccountID')
    default_amount = _decimal_or_zero(request.POST.get('defaultAmount'))
    display_order = int(request.POST.get('displayOrder') or 0)
    is_recurring = _truthy(request.POST.get('isRecurring'))
    is_active = _truthy(request.POST.get('isActive') or 'true')

    if not code or not name:
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
            return ErrorResponse('Fee head not found.').to_json_response()
        if instance.code in {'ADMISSION_FEE', 'MONTHLY_STUDENT_FEE', 'MISC_FEE'} and code != instance.code:
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
        return ErrorResponse('Fee head code already exists.').to_json_response()

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
    instance.lastEditedBy = (f'{request.user.first_name} {request.user.last_name}'.strip() or request.user.username)
    instance.updatedByUserID = request.user
    instance.full_clean()
    instance.save()
    return SuccessResponse('Fee head saved successfully.').to_json_response()


@transaction.atomic
@csrf_exempt
@login_required
@check_groups('Admin', 'Owner')
def delete_fee_head_api(request):
    if request.method != 'POST':
        return ErrorResponse('Method not allowed.', status_code=405).to_json_response()
    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    fee_head_id = request.POST.get('id')
    instance = FeeHead.objects.filter(
        pk=fee_head_id,
        schoolID_id=school_id,
        sessionID_id=session_id,
        isDeleted=False,
    ).first()
    if not instance:
        return ErrorResponse('Fee head not found.').to_json_response()
    if instance.code in {'ADMISSION_FEE', 'MONTHLY_STUDENT_FEE', 'MISC_FEE'}:
        return ErrorResponse('System fee heads cannot be deleted.').to_json_response()
    if StudentCharge.objects.filter(
        schoolID_id=school_id,
        sessionID_id=session_id,
        feeHeadID_id=instance.id,
        isDeleted=False,
    ).exists():
        return ErrorResponse('Fee head is already used in student charges and cannot be deleted.').to_json_response()
    instance.isDeleted = True
    instance.lastEditedBy = (f'{request.user.first_name} {request.user.last_name}'.strip() or request.user.username)
    instance.updatedByUserID = request.user
    instance.save(update_fields=['isDeleted', 'lastEditedBy', 'updatedByUserID', 'lastUpdatedOn'])
    return SuccessResponse('Fee head deleted successfully.').to_json_response()


def _build_student_finance_ledger_payload(*, school_id, session_id, student_id):
    if not school_id or not session_id or not student_id:
        return {
            'summary': {'studentName': '', 'className': '', 'totalCharged': 0, 'totalPaid': 0, 'totalBalance': 0},
            'rows': [],
        }

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
        'className': f"{student_obj.standardID.name or 'N/A'}{(' - ' + student_obj.standardID.section) if student_obj.standardID and student_obj.standardID.section else ''}" if student_obj.standardID_id else 'N/A',
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
    try:
        payload = _build_student_finance_ledger_payload(
            school_id=school_id,
            session_id=session_id,
            student_id=student_id,
        )
    except ValueError as exc:
        return ErrorResponse(str(exc)).to_json_response()
    except Exception as exc:
        logger.exception('Unable to build student finance ledger payload.')
        return ErrorResponse(f'Unable to load student ledger: {exc}').to_json_response()
    return SuccessResponse('Student ledger loaded successfully.', data=payload).to_json_response()


@login_required
@check_groups('Admin', 'Owner')
def get_finance_payment_mode_options_api(request):
    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    if not school_id or not session_id:
        return SuccessResponse('Payment modes loaded.', data=[]).to_json_response()
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
    return SuccessResponse('Payment modes loaded.', data=data).to_json_response()


@login_required
@check_groups('Admin', 'Owner')
def get_receipt_charge_options_api(request):
    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    student_id = request.GET.get('student')
    if not school_id or not session_id or not student_id:
        return SuccessResponse('Charge options loaded.', data=[]).to_json_response()

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
    return SuccessResponse('Charge options loaded.', data=data).to_json_response()


@login_required
@check_groups('Admin', 'Owner')
def get_receipt_refund_options_api(request):
    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    receipt_id = request.GET.get('receiptID')
    if not school_id or not session_id or not receipt_id:
        return SuccessResponse('Refund options loaded.', data=[]).to_json_response()

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
    return SuccessResponse('Refund options loaded.', data=data).to_json_response()


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
        if hasattr(exc, 'message_dict'):
            message = '; '.join([f'{key}: {", ".join(value)}' for key, value in exc.message_dict.items()])
        else:
            message = '; '.join(exc.messages)
        return ErrorResponse(message or 'Unable to create receipt.').to_json_response()

    if approval_resolution['requires_queue']:
        rule_name = approval_resolution['rule'].ruleName if approval_resolution['rule'] else 'approval rule'
        return SuccessResponse(
            f'Receipt saved and submitted for approval based on rule: {rule_name}.',
            data={
                'id': receipt_obj.id,
                'receiptNo': receipt_obj.receiptNo,
                'receiptUrl': f'/management/finance/receipt/{receipt_obj.id}/',
            },
        ).to_json_response()

    return SuccessResponse('Receipt created successfully.', data={
        'id': receipt_obj.id,
        'receiptNo': receipt_obj.receiptNo,
        'receiptUrl': f'/management/finance/receipt/{receipt_obj.id}/',
    }).to_json_response()


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

    if approval_resolution['requires_queue']:
        rule_name = approval_resolution['rule'].ruleName if approval_resolution['rule'] else 'approval rule'
        return SuccessResponse(
            f'Refund saved and submitted for approval based on rule: {rule_name}.',
            data={
                'id': refund_obj.id,
                'refundNo': refund_obj.refundNo,
            },
        ).to_json_response()

    return SuccessResponse('Refund created successfully.', data={
        'id': refund_obj.id,
        'refundNo': refund_obj.refundNo,
    }).to_json_response()


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
        logger.exception('Unable to reverse finance receipt.')
        return ErrorResponse(f'Unable to reverse receipt: {exc}').to_json_response()

    return SuccessResponse('Receipt reversed successfully.').to_json_response()


@login_required
@check_groups('Admin', 'Owner')
def get_payroll_run_list_api(request):
    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    if not school_id or not session_id:
        return SuccessResponse('Payroll runs loaded successfully.', data={
            'summary': {'totalRuns': 0, 'postedRuns': 0, 'totalPayable': 0, 'totalPaid': 0, 'totalPending': 0},
            'rows': [],
        }).to_json_response()

    bootstrap_school_finance(school_id=school_id, session_id=session_id, user_obj=request.user)
    month_value = _safe_int(request.GET.get('month') or 0, 0)
    year_value = _safe_int(request.GET.get('year') or 0, 0)

    run_qs = PayrollRun.objects.filter(
        schoolID_id=school_id,
        sessionID_id=session_id,
        isDeleted=False,
    ).prefetch_related('payrollLines__teacherID', 'payrollLines__partyID').order_by('-year', '-month', '-runDate', '-id')

    if 1 <= month_value <= 12:
        run_qs = run_qs.filter(month=month_value)
    if year_value > 0:
        run_qs = run_qs.filter(year=year_value)

    rows = []
    total_payable = Decimal('0.00')
    total_paid = Decimal('0.00')
    posted_runs = 0
    run_list = list(run_qs)
    for run_obj in run_list:
        line_items = [line for line in run_obj.payrollLines.all() if _decimal_or_zero(line.netAmount) > 0]
        payable_amount = sum((_decimal_or_zero(line.netAmount) for line in line_items), Decimal('0.00'))
        paid_amount = sum((_decimal_or_zero(line.netAmount) for line in line_items if line.paymentStatus == 'paid'), Decimal('0.00'))
        pending_count = sum(1 for line in line_items if line.paymentStatus != 'paid')
        total_payable += payable_amount
        total_paid += paid_amount
        if run_obj.status in {'posted', 'paid', 'closed'}:
            posted_runs += 1
        rows.append({
            'id': run_obj.id,
            'runNo': run_obj.payrollRunNo or '',
            'period': f'{run_obj.month:02d}/{run_obj.year}',
            'runDate': run_obj.runDate.strftime('%d-%m-%Y') if run_obj.runDate else 'N/A',
            'status': run_obj.status,
            'lineCount': len(line_items),
            'payableAmount': float(payable_amount),
            'paidAmount': float(paid_amount),
            'pendingAmount': float(payable_amount - paid_amount),
            'pendingCount': pending_count,
        })

    return SuccessResponse('Payroll runs loaded successfully.', data={
        'summary': {
            'totalRuns': len(rows),
            'postedRuns': posted_runs,
            'totalPayable': float(total_payable),
            'totalPaid': float(total_paid),
            'totalPending': float(total_payable - total_paid),
        },
        'rows': rows,
    }).to_json_response()


@login_required
@check_groups('Admin', 'Owner')
def get_payroll_run_detail_api(request):
    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    run_id = request.GET.get('runID')
    if not school_id or not session_id or not run_id:
        return ErrorResponse('Payroll run not found.').to_json_response()

    run_obj = PayrollRun.objects.filter(
        pk=run_id,
        schoolID_id=school_id,
        sessionID_id=session_id,
        isDeleted=False,
    ).prefetch_related('payrollLines__teacherID', 'payrollLines__partyID').first()
    if not run_obj:
        return ErrorResponse('Payroll run not found.').to_json_response()

    rows = []
    total_payable = Decimal('0.00')
    total_paid = Decimal('0.00')
    for line in run_obj.payrollLines.all().order_by('teacherID__name', 'partyID__displayName', 'id'):
        net_amount = _decimal_or_zero(line.netAmount)
        if net_amount <= 0:
            continue
        total_payable += net_amount
        if line.paymentStatus == 'paid':
            total_paid += net_amount
        teacher_name = ''
        if line.teacherID_id:
            teacher_name = line.teacherID.name or ''
        if not teacher_name and line.partyID_id:
            teacher_name = line.partyID.displayName or ''
        rows.append({
            'id': line.id,
            'teacherName': teacher_name or 'N/A',
            'basicAmount': float(_decimal_or_zero(line.basicAmount)),
            'allowanceAmount': float(_decimal_or_zero(line.allowanceAmount)),
            'deductionAmount': float(_decimal_or_zero(line.deductionAmount)),
            'advanceRecoveryAmount': float(_decimal_or_zero(line.advanceRecoveryAmount)),
            'netAmount': float(net_amount),
            'paymentStatus': line.paymentStatus,
            'paymentDate': line.paymentDate.strftime('%d-%m-%Y') if line.paymentDate else '',
        })

    return SuccessResponse('Payroll run loaded successfully.', data={
        'run': {
            'id': run_obj.id,
            'runNo': run_obj.payrollRunNo or '',
            'period': f'{run_obj.month:02d}/{run_obj.year}',
            'month': run_obj.month,
            'year': run_obj.year,
            'runDate': run_obj.runDate.strftime('%d-%m-%Y') if run_obj.runDate else '',
            'status': run_obj.status,
            'totalPayable': float(total_payable),
            'totalPaid': float(total_paid),
            'totalPending': float(total_payable - total_paid),
            'canPost': run_obj.status in {'processed', 'posted'},
        },
        'rows': rows,
    }).to_json_response()


@transaction.atomic
@csrf_exempt
@login_required
@check_groups('Admin', 'Owner')
def create_payroll_run_api(request):
    if request.method != 'POST':
        return ErrorResponse('Method not allowed.', status_code=405).to_json_response()

    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    month_value = _safe_int(request.POST.get('month') or 0, 0)
    year_value = _safe_int(request.POST.get('year') or 0, 0)
    run_date = _parse_filter_date(request.POST.get('runDate'))
    requested_status = (request.POST.get('status') or 'processed').strip()
    if not school_id or not session_id:
        return ErrorResponse('School session was not found.').to_json_response()
    if month_value < 1 or month_value > 12 or year_value <= 0:
        return ErrorResponse('Valid month and year are required.').to_json_response()
    if not run_date:
        return ErrorResponse('Valid payroll run date is required.').to_json_response()
    try:
        _assert_finance_date_open(school_id=school_id, session_id=session_id, txn_date=run_date, label='Payroll run date')
    except ValidationError as exc:
        return ErrorResponse('; '.join(exc.messages)).to_json_response()

    teacher_count = TeacherDetail.objects.filter(
        schoolID_id=school_id,
        sessionID_id=session_id,
        isDeleted=False,
        isActive='Yes',
    ).exclude(salary__isnull=True).exclude(salary__lte=0).count()
    estimated_amount = TeacherDetail.objects.filter(
        schoolID_id=school_id,
        sessionID_id=session_id,
        isDeleted=False,
        isActive='Yes',
    ).aggregate(total=Coalesce(Sum('salary'), Value(Decimal('0.00')), output_field=DecimalField(max_digits=14, decimal_places=2)))['total'] or Decimal('0.00')
    approval_resolution = _apply_finance_approval_rules(
        school_id=school_id,
        session_id=session_id,
        document_type='payroll_run',
        requested_status=requested_status,
        amount=estimated_amount,
        approvable_statuses={'processed', 'posted'},
    )

    try:
        payroll_run = generate_payroll_run(
            school_id=school_id,
            session_id=session_id,
            month=month_value,
            year=year_value,
            run_date=run_date,
            status=approval_resolution['effective_status'],
            requested_status=approval_resolution['requested_status'],
            user_obj=request.user,
        )
    except ValidationError as exc:
        return ErrorResponse('; '.join(exc.messages) or 'Unable to generate payroll run.').to_json_response()

    if approval_resolution['requires_queue']:
        rule_name = approval_resolution['rule'].ruleName if approval_resolution['rule'] else 'approval rule'
        return SuccessResponse(
            f'Payroll run generated and submitted for approval based on rule: {rule_name}.',
            data={
                'id': payroll_run.id,
                'runNo': payroll_run.payrollRunNo or '',
                'period': f'{payroll_run.month:02d}/{payroll_run.year}',
                'teacherCount': teacher_count,
            },
        ).to_json_response()

    return SuccessResponse('Payroll run generated successfully.', data={
        'id': payroll_run.id,
        'runNo': payroll_run.payrollRunNo or '',
        'period': f'{payroll_run.month:02d}/{payroll_run.year}',
    }).to_json_response()


@transaction.atomic
@csrf_exempt
@login_required
@check_groups('Admin', 'Owner')
def post_payroll_run_api(request):
    if request.method != 'POST':
        return ErrorResponse('Method not allowed.', status_code=405).to_json_response()

    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    run_id = request.POST.get('runID')
    if not school_id or not session_id or not run_id:
        return ErrorResponse('Payroll run not found.').to_json_response()

    payroll_run = PayrollRun.objects.select_for_update().filter(
        pk=run_id,
        schoolID_id=school_id,
        sessionID_id=session_id,
        isDeleted=False,
    ).first()
    if not payroll_run:
        return ErrorResponse('Payroll run not found.').to_json_response()
    try:
        _assert_finance_date_open(
            school_id=school_id,
            session_id=session_id,
            txn_date=payroll_run.runDate,
            label='Payroll posting date',
        )
    except ValidationError as exc:
        return ErrorResponse('; '.join(exc.messages)).to_json_response()

    total_amount = payroll_run.payrollLines.filter(netAmount__gt=0).aggregate(
        total=Coalesce(Sum('netAmount'), Value(Decimal('0.00')), output_field=DecimalField(max_digits=14, decimal_places=2))
    )['total'] or Decimal('0.00')
    approval_resolution = _apply_finance_approval_rules(
        school_id=school_id,
        session_id=session_id,
        document_type='payroll_run',
        requested_status='posted',
        amount=total_amount,
        approvable_statuses={'processed', 'posted'},
    )
    if approval_resolution['requires_queue']:
        payroll_run.status = 'submitted'
        payroll_run.requestedApprovalStatus = 'posted'
        payroll_run.lastEditedBy = (f'{request.user.first_name} {request.user.last_name}'.strip() or request.user.username)
        payroll_run.updatedByUserID = request.user
        payroll_run.save(update_fields=['status', 'requestedApprovalStatus', 'lastEditedBy', 'updatedByUserID', 'lastUpdatedOn'])
        rule_name = approval_resolution['rule'].ruleName if approval_resolution['rule'] else 'approval rule'
        return SuccessResponse(f'Payroll run submitted for approval based on rule: {rule_name}.').to_json_response()

    try:
        post_payroll_run(
            payroll_run_obj=payroll_run,
            school_id=school_id,
            session_id=session_id,
            user_obj=request.user,
        )
    except ValidationError as exc:
        return ErrorResponse('; '.join(exc.messages) or 'Unable to post payroll run.').to_json_response()

    return SuccessResponse('Payroll run posted successfully.').to_json_response()


@transaction.atomic
@csrf_exempt
@login_required
@check_groups('Admin', 'Owner')
def pay_payroll_line_api(request):
    if request.method != 'POST':
        return ErrorResponse('Method not allowed.', status_code=405).to_json_response()

    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    line_id = request.POST.get('lineID')
    payment_mode_id = request.POST.get('paymentModeID')
    payment_date = _parse_filter_date(request.POST.get('paymentDate'))
    if not school_id or not session_id or not line_id:
        return ErrorResponse('Payroll line not found.').to_json_response()
    if not payment_date:
        return ErrorResponse('Valid payment date is required.').to_json_response()
    try:
        _assert_finance_date_open(school_id=school_id, session_id=session_id, txn_date=payment_date, label='Salary payment date')
    except ValidationError as exc:
        return ErrorResponse('; '.join(exc.messages)).to_json_response()

    payroll_line = PayrollLine.objects.select_related('payrollRunID', 'partyID').select_for_update().filter(
        pk=line_id,
        payrollRunID__schoolID_id=school_id,
        payrollRunID__sessionID_id=session_id,
        payrollRunID__isDeleted=False,
    ).first()
    if not payroll_line:
        return ErrorResponse('Payroll line not found.').to_json_response()

    payment_mode = FinancePaymentMode.objects.select_related('linkedAccountID').filter(
        pk=payment_mode_id,
        schoolID_id=school_id,
        isDeleted=False,
        isActive=True,
    ).first()
    if not payment_mode or not payment_mode.linkedAccountID_id:
        return ErrorResponse('A valid payment mode is required.').to_json_response()

    approval_resolution = _apply_finance_approval_rules(
        school_id=school_id,
        session_id=session_id,
        document_type='salary_payment',
        requested_status='paid',
        amount=_decimal_or_zero(payroll_line.netAmount),
        approvable_statuses={'paid'},
    )

    try:
        pay_payroll_line(
            payroll_line_obj=payroll_line,
            school_id=school_id,
            session_id=session_id,
            payment_date=payment_date,
            payment_mode_obj=payment_mode,
            status=approval_resolution['effective_status'] if approval_resolution['effective_status'] in {'submitted', 'paid'} else 'paid',
            requested_status=approval_resolution['requested_status'],
            user_obj=request.user,
        )
    except ValidationError as exc:
        return ErrorResponse('; '.join(exc.messages) or 'Unable to pay payroll line.').to_json_response()

    if approval_resolution['requires_queue']:
        rule_name = approval_resolution['rule'].ruleName if approval_resolution['rule'] else 'approval rule'
        return SuccessResponse(f'Salary payment saved and submitted for approval based on rule: {rule_name}.').to_json_response()

    return SuccessResponse('Salary payment recorded successfully.').to_json_response()


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


@login_required
@check_groups('Admin', 'Owner')
def get_finance_control_center_api(request):
    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    if not school_id or not session_id:
        return SuccessResponse('Finance controls loaded successfully.', data={
            'summary': {'openPeriods': 0, 'lockedPeriods': 0, 'pendingApprovals': 0, 'activeApprovalRules': 0},
            'periods': [],
            'approvalRules': [],
            'pendingApprovals': [],
        }).to_json_response()

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
        total_amount = sum((_decimal_or_zero(line.netAmount) for line in row.payrollLines.all() if _decimal_or_zero(line.netAmount) > 0), Decimal('0.00'))
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

    pending_approvals.sort(key=lambda item: datetime.strptime(item['date'], '%d-%m-%Y').date() if item['date'] != 'N/A' else date.min, reverse=True)

    locked_count = sum(1 for row in periods if row['status'] in {'soft_locked', 'closed'})
    open_count = sum(1 for row in periods if row['status'] == 'open')

    return SuccessResponse('Finance controls loaded successfully.', data={
        'summary': {
            'openPeriods': open_count,
            'lockedPeriods': locked_count,
            'pendingApprovals': len(pending_approvals),
            'activeApprovalRules': sum(1 for row in approval_rules if row['isActive']),
        },
        'periods': periods,
        'approvalRules': approval_rules,
        'pendingApprovals': pending_approvals,
    }).to_json_response()


@transaction.atomic
@csrf_exempt
@login_required
@check_groups('Admin', 'Owner')
def upsert_finance_period_api(request):
    if request.method != 'POST':
        return ErrorResponse('Method not allowed.', status_code=405).to_json_response()
    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    if not school_id or not session_id:
        return ErrorResponse('School session was not found.').to_json_response()

    period_id = request.POST.get('id')
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
        return ErrorResponse('Finance periods cannot overlap.').to_json_response()

    if not instance:
        instance = FinancePeriod(schoolID_id=school_id, sessionID_id=session_id)
    instance.periodStart = period_start
    instance.periodEnd = period_end
    instance.status = status_value
    if status_value == 'closed':
        instance.closedByUserID = request.user
        instance.closedAt = timezone.now()
    elif status_value == 'soft_locked':
        instance.closedByUserID = None
        instance.closedAt = None
    else:
        instance.closedByUserID = None
        instance.closedAt = None
    instance.lastEditedBy = (f'{request.user.first_name} {request.user.last_name}'.strip() or request.user.username)
    instance.updatedByUserID = request.user
    instance.full_clean()
    instance.save()
    return SuccessResponse('Finance period saved successfully.').to_json_response()


@transaction.atomic
@csrf_exempt
@login_required
@check_groups('Admin', 'Owner')
def upsert_finance_approval_rule_api(request):
    if request.method != 'POST':
        return ErrorResponse('Method not allowed.', status_code=405).to_json_response()
    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    if not school_id or not session_id:
        return ErrorResponse('School session was not found.').to_json_response()

    rule_id = request.POST.get('id')
    rule_name = (request.POST.get('ruleName') or '').strip()
    document_type = (request.POST.get('documentType') or 'expense_voucher').strip()
    approval_mode = (request.POST.get('approvalMode') or 'approval_required').strip()
    min_amount = _decimal_or_zero(request.POST.get('minAmount'))
    max_amount_raw = (request.POST.get('maxAmount') or '').strip()
    max_amount = _decimal_or_zero(max_amount_raw) if max_amount_raw else None
    priority = _safe_int(request.POST.get('priority'), 1)
    is_active = _truthy(request.POST.get('isActive') or 'true')

    if not rule_name:
        return ErrorResponse('Rule name is required.').to_json_response()
    if document_type not in {'expense_voucher', 'payment_receipt', 'payment_refund', 'payroll_run'}:
        return ErrorResponse('Invalid document type.').to_json_response()
    if approval_mode not in {'approval_required', 'direct_allowed'}:
        return ErrorResponse('Invalid approval mode.').to_json_response()
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
            return ErrorResponse('Approval rule not found.').to_json_response()

    overlap_qs = FinanceApprovalRule.objects.filter(
        schoolID_id=school_id,
        sessionID_id=session_id,
        documentType=document_type,
        isDeleted=False,
        isActive=True if is_active else False,
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
        return ErrorResponse('Approval rule amount ranges cannot overlap for the same document type.').to_json_response()

    if not instance:
        instance = FinanceApprovalRule(schoolID_id=school_id, sessionID_id=session_id)
    instance.ruleName = rule_name
    instance.documentType = document_type
    instance.approvalMode = approval_mode
    instance.minAmount = min_amount
    instance.maxAmount = max_amount
    instance.priority = priority
    instance.isActive = is_active
    instance.lastEditedBy = (f'{request.user.first_name} {request.user.last_name}'.strip() or request.user.username)
    instance.updatedByUserID = request.user
    instance.full_clean()
    instance.save()
    return SuccessResponse('Approval rule saved successfully.').to_json_response()


@transaction.atomic
@csrf_exempt
@login_required
@check_groups('Admin', 'Owner')
def delete_finance_approval_rule_api(request):
    if request.method != 'POST':
        return ErrorResponse('Method not allowed.', status_code=405).to_json_response()
    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    rule_id = request.POST.get('id')
    instance = FinanceApprovalRule.objects.filter(
        pk=rule_id,
        schoolID_id=school_id,
        sessionID_id=session_id,
        isDeleted=False,
    ).first()
    if not instance:
        return ErrorResponse('Approval rule not found.').to_json_response()
    instance.isDeleted = True
    instance.lastEditedBy = (f'{request.user.first_name} {request.user.last_name}'.strip() or request.user.username)
    instance.updatedByUserID = request.user
    instance.save(update_fields=['isDeleted', 'lastEditedBy', 'updatedByUserID', 'lastUpdatedOn'])
    return SuccessResponse('Approval rule deleted successfully.').to_json_response()


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
        return ErrorResponse('; '.join(exc.messages)).to_json_response()
    if instance.approvalStatus not in {'submitted', 'draft'}:
        return ErrorResponse('Only draft or submitted vouchers can be approved.').to_json_response()
    requested_status = (instance.requestedApprovalStatus or instance.approvalStatus or 'approved').strip()
    instance.approvalStatus = requested_status if requested_status in {'approved', 'paid'} else 'approved'
    instance.lastEditedBy = (f'{request.user.first_name} {request.user.last_name}'.strip() or request.user.username)
    instance.updatedByUserID = request.user
    instance.save(update_fields=['approvalStatus', 'lastEditedBy', 'updatedByUserID', 'lastUpdatedOn'])
    sync_expense_voucher_posting(voucher_obj=instance, school_id=school_id, session_id=session_id, user_obj=request.user)
    return SuccessResponse(f'Expense voucher moved to {instance.get_approvalStatus_display().lower()} successfully.').to_json_response()


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
    return SuccessResponse('Payment refund confirmed successfully.').to_json_response()


@transaction.atomic
@csrf_exempt
@login_required
@check_groups('Admin', 'Owner')
def approve_payroll_run_api(request):
    if request.method != 'POST':
        return ErrorResponse('Method not allowed.', status_code=405).to_json_response()
    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    run_id = request.POST.get('id')
    instance = PayrollRun.objects.filter(
        pk=run_id,
        schoolID_id=school_id,
        sessionID_id=session_id,
        isDeleted=False,
    ).first()
    if not instance:
        return ErrorResponse('Payroll run not found.').to_json_response()
    try:
        _assert_finance_date_open(school_id=school_id, session_id=session_id, txn_date=instance.runDate, label='Payroll run date')
    except ValidationError as exc:
        return ErrorResponse('; '.join(exc.messages)).to_json_response()
    if instance.status not in {'submitted', 'draft'}:
        return ErrorResponse('Only draft or submitted payroll runs can be approved.').to_json_response()
    requested_status = (instance.requestedApprovalStatus or instance.status or 'processed').strip()
    if requested_status not in {'processed', 'posted'}:
        requested_status = 'processed'
    instance.status = requested_status
    instance.lastEditedBy = (f'{request.user.first_name} {request.user.last_name}'.strip() or request.user.username)
    instance.updatedByUserID = request.user
    instance.save(update_fields=['status', 'lastEditedBy', 'updatedByUserID', 'lastUpdatedOn'])
    if requested_status == 'posted':
        try:
            post_payroll_run(
                payroll_run_obj=instance,
                school_id=school_id,
                session_id=session_id,
                user_obj=request.user,
            )
        except ValidationError as exc:
            return ErrorResponse('; '.join(exc.messages) or 'Unable to post payroll run after approval.').to_json_response()
    return SuccessResponse(f'Payroll run moved to {instance.get_status_display().lower()} successfully.').to_json_response()


@transaction.atomic
@csrf_exempt
@login_required
@check_groups('Admin', 'Owner')
def approve_payroll_payment_api(request):
    if request.method != 'POST':
        return ErrorResponse('Method not allowed.', status_code=405).to_json_response()
    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    line_id = request.POST.get('id')
    payroll_line = PayrollLine.objects.select_related('paymentModeID', 'partyID', 'payrollRunID').filter(
        pk=line_id,
        payrollRunID__schoolID_id=school_id,
        payrollRunID__sessionID_id=session_id,
        payrollRunID__isDeleted=False,
    ).first()
    if not payroll_line:
        return ErrorResponse('Payroll payment line not found.').to_json_response()
    try:
        _assert_finance_date_open(school_id=school_id, session_id=session_id, txn_date=payroll_line.paymentDate, label='Salary payment date')
    except ValidationError as exc:
        return ErrorResponse('; '.join(exc.messages)).to_json_response()
    try:
        approve_payroll_payment(
            payroll_line_obj=payroll_line,
            school_id=school_id,
            session_id=session_id,
            user_obj=request.user,
        )
    except ValidationError as exc:
        return ErrorResponse('; '.join(exc.messages) or 'Unable to approve salary payment.').to_json_response()
    return SuccessResponse('Salary payment approved successfully.').to_json_response()


@login_required
@check_groups('Admin', 'Owner')
def get_receipt_adjustment_history_api(request):
    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    receipt_id = request.GET.get('receiptID')
    if not school_id or not session_id or not receipt_id:
        return ErrorResponse('Receipt could not be found.').to_json_response()

    receipt_obj = PaymentReceipt.objects.filter(
        pk=receipt_id,
        schoolID_id=school_id,
        sessionID_id=session_id,
        isDeleted=False,
    ).first()
    if not receipt_obj:
        return ErrorResponse('Receipt could not be found.').to_json_response()

    refund_rows = list(PaymentRefund.objects.filter(
        schoolID_id=school_id,
        sessionID_id=session_id,
        receiptID=receipt_obj,
        isDeleted=False,
    ).order_by('-refundDate', '-id'))
    reversal_rows = list(FinanceTransaction.objects.filter(
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
        reversal_amount = sum((_decimal_or_zero(entry.amount) for entry in row.entries.filter(entryType='debit')), Decimal('0.00'))
        adjustments.append({
            'type': 'reversal',
            'date': row.txnDate.strftime('%d-%m-%Y') if row.txnDate else 'N/A',
            'reference': row.referenceNo or row.txnNo,
            'amount': float(reversal_amount),
            'status': row.status,
            'note': row.description or '',
        })
    adjustments.sort(key=lambda item: datetime.strptime(item['date'], '%d-%m-%Y').date() if item['date'] != 'N/A' else date.min, reverse=True)

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


@login_required
@check_groups('Admin', 'Owner')
def get_finance_audit_trail_api(request):
    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    if not school_id or not session_id:
        return SuccessResponse('Finance audit trail loaded successfully.', data={'summary': {}, 'rows': []}).to_json_response()

    date_from = _parse_filter_date(request.GET.get('dateFrom'))
    date_to = _parse_filter_date(request.GET.get('dateTo'))
    txn_type = (request.GET.get('txnType') or '').strip()
    status_value = (request.GET.get('status') or '').strip()
    source_module = (request.GET.get('sourceModule') or '').strip()
    search = (request.GET.get('search') or '').strip()

    txn_qs = FinanceTransaction.objects.select_related('updatedByUserID').prefetch_related('entries__accountID', 'entries__partyID').filter(
        schoolID_id=school_id,
        sessionID_id=session_id,
        isDeleted=False,
    ).order_by('-txnDate', '-id')
    if date_from:
        txn_qs = txn_qs.filter(txnDate__gte=date_from)
    if date_to:
        txn_qs = txn_qs.filter(txnDate__lte=date_to)
    if txn_type:
        txn_qs = txn_qs.filter(txnType=txn_type)
    if status_value:
        txn_qs = txn_qs.filter(status=status_value)
    if source_module:
        txn_qs = txn_qs.filter(sourceModule=source_module)
    if search:
        txn_qs = txn_qs.filter(
            Q(txnNo__icontains=search)
            | Q(referenceNo__icontains=search)
            | Q(description__icontains=search)
            | Q(sourceModule__icontains=search)
            | Q(sourceRecordID__icontains=search)
            | Q(lastEditedBy__icontains=search)
        )

    rows = []
    for txn_obj in txn_qs[:250]:
        entry_rows = []
        for entry_obj in txn_obj.entries.all().order_by('lineOrder', 'id'):
            entry_rows.append({
                'account': str(entry_obj.accountID) if entry_obj.accountID_id else '',
                'party': entry_obj.partyID.displayName if entry_obj.partyID_id else '',
                'entryType': entry_obj.entryType,
                'amount': float(_decimal_or_zero(entry_obj.amount)),
                'narration': entry_obj.narration or '',
            })
        rows.append({
            'id': txn_obj.id,
            'txnNo': txn_obj.txnNo,
            'txnDate': txn_obj.txnDate.strftime('%d-%m-%Y') if txn_obj.txnDate else 'N/A',
            'txnType': txn_obj.txnType,
            'status': txn_obj.status,
            'referenceNo': txn_obj.referenceNo or '',
            'description': txn_obj.description or '',
            'sourceModule': txn_obj.sourceModule or '',
            'sourceRecordID': txn_obj.sourceRecordID or '',
            'editedBy': txn_obj.lastEditedBy or (txn_obj.updatedByUserID.username if txn_obj.updatedByUserID_id else ''),
            'updatedOn': txn_obj.lastUpdatedOn.strftime('%d-%m-%Y %I:%M %p') if txn_obj.lastUpdatedOn else '',
            'entries': entry_rows,
        })

    return SuccessResponse('Finance audit trail loaded successfully.', data={
        'summary': {
            'totalTransactions': txn_qs.count(),
            'postedTransactions': txn_qs.filter(status='posted').count(),
            'reversedTransactions': txn_qs.filter(status='reversed').count(),
        },
        'rows': rows,
    }).to_json_response()


class FinanceAuditTrailListJson(BaseDatatableView):
    order_columns = ['txnDate', 'txnNo', 'txnType', 'referenceNo', 'description', 'sourceModule', 'lastEditedBy', 'lastUpdatedOn', 'status']

    def get_initial_queryset(self):
        school_id = _current_school_id(self.request)
        session_id = _current_session_id(self.request)
        if not school_id or not session_id:
            return FinanceTransaction.objects.none()
        qs = FinanceTransaction.objects.prefetch_related('entries__accountID', 'entries__partyID').filter(
            schoolID_id=school_id,
            sessionID_id=session_id,
            isDeleted=False,
        ).order_by('-txnDate', '-id')
        date_from = _parse_filter_date(self.request.GET.get('dateFrom'))
        date_to = _parse_filter_date(self.request.GET.get('dateTo'))
        txn_type = (self.request.GET.get('txnType') or '').strip()
        status_value = (self.request.GET.get('status') or '').strip()
        source_module = (self.request.GET.get('sourceModule') or '').strip()
        if date_from:
            qs = qs.filter(txnDate__gte=date_from)
        if date_to:
            qs = qs.filter(txnDate__lte=date_to)
        if txn_type:
            qs = qs.filter(txnType=txn_type)
        if status_value:
            qs = qs.filter(status=status_value)
        if source_module:
            qs = qs.filter(sourceModule=source_module)
        return qs

    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(txnNo__icontains=search)
                | Q(referenceNo__icontains=search)
                | Q(description__icontains=search)
                | Q(sourceModule__icontains=search)
                | Q(sourceRecordID__icontains=search)
                | Q(lastEditedBy__icontains=search)
            )
        return qs

    def prepare_results(self, qs):
        json_data = []
        for item in qs:
            entries = list(item.entries.all().order_by('lineOrder', 'id'))
            entry_summary = '<br>'.join([
                escape(f"{entry.accountID.accountName if entry.accountID_id else ''} | {entry.entryType} | Rs {float(_decimal_or_zero(entry.amount)):.2f}")
                for entry in entries[:3]
            ]) or '-'
            if len(entries) > 3:
                entry_summary += f'<br><span style="color:var(--app-muted);">+{len(entries) - 3} more</span>'
            json_data.append([
                escape(item.txnDate.strftime('%d-%m-%Y') if item.txnDate else 'N/A'),
                f'<strong>{escape(item.txnNo or "")}</strong>',
                escape((item.txnType or '').replace('_', ' ').title()),
                escape(item.referenceNo or '-'),
                escape(item.description or '-'),
                escape(f"{item.sourceModule or '-'} / {item.sourceRecordID or '-'}"),
                entry_summary,
                escape(item.lastEditedBy or '-'),
                escape(item.lastUpdatedOn.strftime('%d-%m-%Y %I:%M %p') if item.lastUpdatedOn else '-'),
                _finance_status_pill(item.status),
            ])
        return json_data


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
        return SuccessResponse('Finance reconciliation loaded successfully.', data={}).to_json_response()

    receipt_issues = _build_receipt_reconciliation_rows(school_id=school_id, session_id=session_id)
    charge_issues = _build_charge_reconciliation_rows(school_id=school_id, session_id=session_id)
    voucher_issues = _build_voucher_reconciliation_rows(school_id=school_id, session_id=session_id)
    payroll_issues = _build_payroll_reconciliation_rows(school_id=school_id, session_id=session_id)

    return SuccessResponse('Finance reconciliation loaded successfully.', data={
        'summary': {
            'receiptIssues': len(receipt_issues),
            'chargeIssues': len(charge_issues),
            'voucherIssues': len(voucher_issues),
            'payrollIssues': len(payroll_issues),
        },
    }).to_json_response()


def _finance_reconciliation_datatable_response(request, *, rows, sort_keys, row_builder):
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
    return _datatable_json_response(draw=draw, total_count=total_count, filtered_count=filtered_count, rows=data)


@login_required
@check_groups('Admin', 'Owner')
def finance_recon_receipt_rows_api(request):
    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    if not school_id or not session_id:
        return _datatable_json_response(draw=_safe_int(request.GET.get('draw'), 1), total_count=0, filtered_count=0, rows=[])

    rows = _build_receipt_reconciliation_rows(school_id=school_id, session_id=session_id)
    return _finance_reconciliation_datatable_response(
        request,
        rows=rows,
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
        return _datatable_json_response(draw=_safe_int(request.GET.get('draw'), 1), total_count=0, filtered_count=0, rows=[])

    rows = _build_charge_reconciliation_rows(school_id=school_id, session_id=session_id)
    return _finance_reconciliation_datatable_response(
        request,
        rows=rows,
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
        return _datatable_json_response(draw=_safe_int(request.GET.get('draw'), 1), total_count=0, filtered_count=0, rows=[])

    rows = _build_voucher_reconciliation_rows(school_id=school_id, session_id=session_id)
    return _finance_reconciliation_datatable_response(
        request,
        rows=rows,
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
        return _datatable_json_response(draw=_safe_int(request.GET.get('draw'), 1), total_count=0, filtered_count=0, rows=[])

    rows = _build_payroll_reconciliation_rows(school_id=school_id, session_id=session_id)
    return _finance_reconciliation_datatable_response(
        request,
        rows=rows,
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
        return SuccessResponse('Expense voucher posting repaired successfully.').to_json_response()

    return ErrorResponse('Unsupported repair type.').to_json_response()


@login_required
@check_groups('Admin', 'Owner')
def get_finance_reports_api(request):
    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    if not school_id or not session_id:
        return SuccessResponse('Finance reports loaded successfully.', data={
            'summary': {'assetTotal': 0, 'liabilityTotal': 0, 'incomeTotal': 0, 'expenseTotal': 0, 'netSurplus': 0},
            'trialBalance': [],
            'incomeStatement': {'income': [], 'expense': [], 'totalIncome': 0, 'totalExpense': 0, 'netSurplus': 0},
            'balanceSheet': {'assets': [], 'liabilities': [], 'equity': [], 'totalAssets': 0, 'totalLiabilities': 0, 'totalEquity': 0},
            'generalLedger': {'accountID': '', 'accountLabel': '', 'rows': [], 'closingBalance': 0},
        }).to_json_response()

    date_from = _parse_filter_date(request.GET.get('dateFrom'))
    date_to = _parse_filter_date(request.GET.get('dateTo'))
    account_id = request.GET.get('accountID')

    entry_qs = FinanceEntry.objects.select_related('accountID', 'transactionID').filter(
        transactionID__schoolID_id=school_id,
        transactionID__sessionID_id=session_id,
        transactionID__isDeleted=False,
        transactionID__status='posted',
    )
    if date_from:
        entry_qs = entry_qs.filter(transactionID__txnDate__gte=date_from)
    if date_to:
        entry_qs = entry_qs.filter(transactionID__txnDate__lte=date_to)

    account_rows = list(
        entry_qs.values(
            'accountID',
            'accountID__accountCode',
            'accountID__accountName',
            'accountID__accountType',
        ).annotate(
            debit_total=Coalesce(Sum('amount', filter=Q(entryType='debit')), Value(Decimal('0.00')), output_field=DecimalField(max_digits=14, decimal_places=2)),
            credit_total=Coalesce(Sum('amount', filter=Q(entryType='credit')), Value(Decimal('0.00')), output_field=DecimalField(max_digits=14, decimal_places=2)),
        ).order_by('accountID__accountType', 'accountID__accountName')
    )

    trial_balance = []
    income_rows = []
    expense_rows = []
    asset_rows = []
    liability_rows = []
    equity_rows = []
    total_income = Decimal('0.00')
    total_expense = Decimal('0.00')
    total_assets = Decimal('0.00')
    total_liabilities = Decimal('0.00')
    total_equity = Decimal('0.00')

    for row in account_rows:
        account_type = row['accountID__accountType']
        debit_total = _decimal_or_zero(row['debit_total'])
        credit_total = _decimal_or_zero(row['credit_total'])
        closing = _normal_balance(account_type, debit_total, credit_total)
        trial_debit = Decimal('0.00')
        trial_credit = Decimal('0.00')
        if closing >= 0:
            if account_type in {'asset', 'expense'}:
                trial_debit = closing
            else:
                trial_credit = closing
        else:
            if account_type in {'asset', 'expense'}:
                trial_credit = abs(closing)
            else:
                trial_debit = abs(closing)

        trial_balance.append({
            'accountCode': row['accountID__accountCode'] or '',
            'accountName': row['accountID__accountName'] or '',
            'accountType': account_type,
            'debitTotal': float(debit_total),
            'creditTotal': float(credit_total),
            'closingDebit': float(trial_debit),
            'closingCredit': float(trial_credit),
        })

        line = {
            'accountCode': row['accountID__accountCode'] or '',
            'accountName': row['accountID__accountName'] or '',
            'amount': float(abs(closing)),
        }
        if account_type == 'income':
            total_income += max(closing, Decimal('0.00'))
            income_rows.append(line)
        elif account_type == 'expense':
            total_expense += max(closing, Decimal('0.00'))
            expense_rows.append(line)
        elif account_type == 'asset':
            total_assets += abs(closing)
            asset_rows.append(line)
        elif account_type == 'liability':
            total_liabilities += abs(closing)
            liability_rows.append(line)
        elif account_type == 'equity':
            total_equity += abs(closing)
            equity_rows.append(line)

    net_surplus = total_income - total_expense
    general_ledger_rows = []
    general_ledger_account = None
    running_balance = Decimal('0.00')
    if account_id:
        general_ledger_account = FinanceAccount.objects.filter(
            pk=account_id,
            schoolID_id=school_id,
            sessionID_id=session_id,
            isDeleted=False,
        ).first()
        if general_ledger_account:
            ledger_qs = FinanceEntry.objects.select_related('transactionID', 'partyID').filter(
                transactionID__schoolID_id=school_id,
                transactionID__sessionID_id=session_id,
                transactionID__isDeleted=False,
                transactionID__status='posted',
                accountID=general_ledger_account,
            ).order_by('transactionID__txnDate', 'transactionID__id', 'lineOrder', 'id')
            if date_from:
                ledger_qs = ledger_qs.filter(transactionID__txnDate__gte=date_from)
            if date_to:
                ledger_qs = ledger_qs.filter(transactionID__txnDate__lte=date_to)
            for row in ledger_qs:
                amount = _decimal_or_zero(row.amount)
                if general_ledger_account.accountType in {'asset', 'expense'}:
                    running_balance += amount if row.entryType == 'debit' else -amount
                else:
                    running_balance += amount if row.entryType == 'credit' else -amount
                general_ledger_rows.append({
                    'date': row.transactionID.txnDate.strftime('%d-%m-%Y') if row.transactionID.txnDate else 'N/A',
                    'reference': row.transactionID.referenceNo or row.transactionID.txnNo,
                    'txnType': row.transactionID.txnType,
                    'description': row.narration or row.transactionID.description or '',
                    'party': row.partyID.displayName if row.partyID_id else '',
                    'debit': float(amount if row.entryType == 'debit' else Decimal('0.00')),
                    'credit': float(amount if row.entryType == 'credit' else Decimal('0.00')),
                    'balance': float(running_balance),
                })

    return SuccessResponse('Finance reports loaded successfully.', data={
        'summary': {
            'assetTotal': float(total_assets),
            'liabilityTotal': float(total_liabilities),
            'incomeTotal': float(total_income),
            'expenseTotal': float(total_expense),
            'netSurplus': float(net_surplus),
        },
        'trialBalance': trial_balance,
        'incomeStatement': {
            'income': income_rows,
            'expense': expense_rows,
            'totalIncome': float(total_income),
            'totalExpense': float(total_expense),
            'netSurplus': float(net_surplus),
        },
        'balanceSheet': {
            'assets': asset_rows,
            'liabilities': liability_rows,
            'equity': equity_rows,
            'totalAssets': float(total_assets),
            'totalLiabilities': float(total_liabilities),
            'totalEquity': float(total_equity),
        },
        'generalLedger': {
            'accountID': str(general_ledger_account.id) if general_ledger_account else '',
            'accountLabel': str(general_ledger_account) if general_ledger_account else '',
            'rows': general_ledger_rows,
            'closingBalance': float(running_balance),
        },
    }).to_json_response()


@login_required
@check_groups('Admin', 'Owner')
def get_payment_receipt_list_api(request):
    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    if not school_id or not session_id:
        return SuccessResponse('Receipts loaded successfully.', data={
            'summary': {'totalReceipts': 0, 'totalAmount': 0, 'confirmedAmount': 0, 'cancelledAmount': 0},
            'rows': [],
        }).to_json_response()

    bootstrap_school_finance(school_id=school_id, session_id=session_id, user_obj=request.user)

    student_id = request.GET.get('student')
    status_value = (request.GET.get('status') or '').strip()
    date_from = request.GET.get('dateFrom')
    date_to = request.GET.get('dateTo')

    receipt_qs = PaymentReceipt.objects.select_related(
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

    if student_id:
        receipt_qs = receipt_qs.filter(studentID_id=student_id)
    if status_value:
        receipt_qs = receipt_qs.filter(status=status_value)
    date_from_value = _parse_filter_date(date_from)
    date_to_value = _parse_filter_date(date_to)
    if date_from_value:
        receipt_qs = receipt_qs.filter(receiptDate__gte=date_from_value)
    if date_to_value:
        receipt_qs = receipt_qs.filter(receiptDate__lte=date_to_value)

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

        allocations = list(row.allocations.all())
        allocated_total = sum((_decimal_or_zero(item.allocatedAmount) for item in allocations), Decimal('0.00'))
        fee_head_names = sorted({
            item.studentChargeID.feeHeadID.name
            for item in allocations
            if item.studentChargeID_id and item.studentChargeID.feeHeadID_id
        })
        class_label = 'N/A'
        if row.studentID_id and row.studentID.standardID_id:
            class_label = row.studentID.standardID.name or 'N/A'
            if row.studentID.standardID.section:
                class_label = f'{class_label} - {row.studentID.standardID.section}'

        rows.append({
            'id': row.id,
            'receiptNo': row.receiptNo or '',
            'receiptDate': row.receiptDate.strftime('%d-%m-%Y') if row.receiptDate else 'N/A',
            'studentName': row.studentID.name if row.studentID_id and row.studentID.name else '',
            'className': class_label,
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
    return SuccessResponse('Receipts loaded successfully.', data={'summary': summary, 'rows': rows}).to_json_response()


class FinanceReceiptListJson(BaseDatatableView):
    order_columns = ['receiptDate', 'receiptNo', 'studentID__name', 'studentID__standardID__name', 'receivedFromName',
                     'paymentModeID__name', 'referenceNo', 'amountReceived', 'amountReceived', 'status', 'id']

    def get_initial_queryset(self):
        school_id = _current_school_id(self.request)
        session_id = _current_session_id(self.request)
        if not school_id or not session_id:
            return PaymentReceipt.objects.none()
        bootstrap_school_finance(school_id=school_id, session_id=session_id, user_obj=self.request.user)
        qs = PaymentReceipt.objects.select_related(
            'studentID', 'studentID__standardID', 'partyID', 'paymentModeID', 'depositAccountID'
        ).prefetch_related('allocations__studentChargeID__feeHeadID').filter(
            schoolID_id=school_id,
            sessionID_id=session_id,
            isDeleted=False,
        ).order_by('-receiptDate', '-datetime', '-id')
        student_id = self.request.GET.get('student')
        status_value = (self.request.GET.get('status') or '').strip()
        date_from = self.request.GET.get('dateFrom')
        date_to = self.request.GET.get('dateTo')
        if student_id:
            qs = qs.filter(studentID_id=student_id)
        if status_value:
            qs = qs.filter(status=status_value)
        date_from_value = _parse_filter_date(date_from)
        date_to_value = _parse_filter_date(date_to)
        if date_from_value:
            qs = qs.filter(receiptDate__gte=date_from_value)
        if date_to_value:
            qs = qs.filter(receiptDate__lte=date_to_value)
        return qs

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
            allocations = list(item.allocations.all())
            allocated_total = sum((_decimal_or_zero(row.allocatedAmount) for row in allocations), Decimal('0.00'))
            fee_head_names = sorted({
                row.studentChargeID.feeHeadID.name
                for row in allocations
                if row.studentChargeID_id and row.studentChargeID.feeHeadID_id
            })
            class_label = 'N/A'
            if item.studentID_id and item.studentID.standardID_id:
                class_label = item.studentID.standardID.name or 'N/A'
                if item.studentID.standardID.section:
                    class_label = f'{class_label} - {item.studentID.standardID.section}'
            action_parts = [
                f'<a target="_blank" href="/management/finance/receipt/{item.id}/" '
                f'data-inverted="" data-tooltip="Print Receipt" data-position="left center" data-variation="mini" '
                f'style="font-size:10px;" class="ui circular blue icon button">'
                f'<i class="print icon"></i></a>',
                f'<button type="button" onclick="openReceiptAdjustments({item.id}, \'{escape(item.receiptNo or "")}\')" '
                f'data-inverted="" data-tooltip="Adjustment History" data-position="left center" data-variation="mini" '
                f'style="font-size:10px; margin-left: 3px;" class="ui circular teal icon button">'
                f'<i class="history icon"></i></button>',
            ]
            if item.status == 'confirmed':
                action_parts.append(
                    f'<button type="button" onclick="openRefundReceiptModal({item.id}, \'{escape(item.receiptNo or "")}\')" '
                    f'data-inverted="" data-tooltip="Create Refund" data-position="left center" data-variation="mini" '
                    f'style="font-size:10px; margin-left: 3px;" class="ui circular orange icon button">'
                    f'<i class="reply icon"></i></button>'
                )
                action_parts.append(
                    f'<button type="button" onclick="openReverseReceiptModal({item.id}, \'{escape(item.receiptNo or "")}\')" '
                    f'data-inverted="" data-tooltip="Reverse Receipt" data-position="left center" data-variation="mini" '
                    f'style="font-size:10px; margin-left: 3px;" class="ui circular red icon button">'
                    f'<i class="undo icon"></i></button>'
                )
            action = ''.join(action_parts)
            json_data.append([
                escape(item.receiptDate.strftime('%d-%m-%Y') if item.receiptDate else 'N/A'),
                f'<strong>{escape(item.receiptNo or "")}</strong>',
                escape(item.studentID.name if item.studentID_id and item.studentID.name else item.receivedFromName or '-'),
                escape(class_label),
                escape(', '.join(fee_head_names) or 'Receipt'),
                escape(item.paymentModeID.name if item.paymentModeID_id else '-'),
                escape(item.referenceNo or '-'),
                escape(f'Rs {float(_decimal_or_zero(item.amountReceived)):.2f}'),
                escape(f'Rs {float(allocated_total):.2f}'),
                _finance_status_pill(item.status),
                action,
            ])
        return json_data


@login_required
@check_groups('Admin', 'Owner')
def get_student_charge_list_api(request):
    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    if not school_id or not session_id:
        return SuccessResponse('Student charges loaded successfully.', data={
            'summary': {'totalCharges': 0, 'totalNetAmount': 0, 'totalPaidAmount': 0, 'totalBalanceAmount': 0},
            'rows': [],
        }).to_json_response()

    bootstrap_school_finance(school_id=school_id, session_id=session_id, user_obj=request.user)

    standard_id = request.GET.get('standard')
    student_id = request.GET.get('student')
    status_value = (request.GET.get('status') or '').strip()
    date_from = request.GET.get('dateFrom')
    date_to = request.GET.get('dateTo')

    charge_qs = StudentCharge.objects.select_related(
        'studentID',
        'studentID__standardID',
        'feeHeadID',
    ).filter(
        schoolID_id=school_id,
        sessionID_id=session_id,
        isDeleted=False,
    ).order_by('-chargeDate', '-datetime', '-id')

    if standard_id:
        charge_qs = charge_qs.filter(standardID_id=standard_id)
    if student_id:
        charge_qs = charge_qs.filter(studentID_id=student_id)
    if status_value:
        charge_qs = charge_qs.filter(status=status_value)
    if date_from:
        try:
            charge_qs = charge_qs.filter(chargeDate__gte=datetime.strptime(date_from, '%d/%m/%Y').date())
        except ValueError:
            pass
    if date_to:
        try:
            charge_qs = charge_qs.filter(chargeDate__lte=datetime.strptime(date_to, '%d/%m/%Y').date())
        except ValueError:
            pass

    totals = charge_qs.aggregate(
        total_net=Sum('netAmount'),
        total_paid=Sum('paidAmount'),
        total_balance=Sum('balanceAmount'),
    )

    rows = []
    for row in charge_qs:
        class_label = 'N/A'
        if row.studentID_id and row.studentID.standardID_id:
            class_label = row.studentID.standardID.name or 'N/A'
            if row.studentID.standardID.section:
                class_label = f'{class_label} - {row.studentID.standardID.section}'
        rows.append({
            'id': row.id,
            'chargeDate': row.chargeDate.strftime('%d-%m-%Y') if row.chargeDate else 'N/A',
            'dueDate': row.dueDate.strftime('%d-%m-%Y') if row.dueDate else '-',
            'studentName': row.studentID.name if row.studentID_id and row.studentID.name else '',
            'className': class_label,
            'feeHead': row.feeHeadID.name if row.feeHeadID_id else 'N/A',
            'title': row.title or '',
            'referenceNo': row.referenceNo or '',
            'chargeType': row.chargeType,
            'status': row.status,
            'netAmount': float(_decimal_or_zero(row.netAmount)),
            'paidAmount': float(_decimal_or_zero(row.paidAmount)),
            'balanceAmount': float(_decimal_or_zero(row.balanceAmount)),
            'description': row.description or '',
        })

    summary = {
        'totalCharges': len(rows),
        'totalNetAmount': float(totals.get('total_net') or Decimal('0.00')),
        'totalPaidAmount': float(totals.get('total_paid') or Decimal('0.00')),
        'totalBalanceAmount': float(totals.get('total_balance') or Decimal('0.00')),
    }
    return SuccessResponse('Student charges loaded successfully.', data={'summary': summary, 'rows': rows}).to_json_response()


class FinanceStudentChargeListJson(BaseDatatableView):
    order_columns = ['chargeDate', 'dueDate', 'studentID__name', 'standardID__name', 'feeHeadID__name',
                     'referenceNo', 'description', 'netAmount', 'paidAmount', 'balanceAmount', 'status']

    def get_initial_queryset(self):
        school_id = _current_school_id(self.request)
        session_id = _current_session_id(self.request)
        if not school_id or not session_id:
            return StudentCharge.objects.none()
        bootstrap_school_finance(school_id=school_id, session_id=session_id, user_obj=self.request.user)
        qs = StudentCharge.objects.select_related(
            'studentID', 'studentID__standardID', 'standardID', 'feeHeadID'
        ).filter(
            schoolID_id=school_id,
            sessionID_id=session_id,
            isDeleted=False,
        ).order_by('-chargeDate', '-datetime', '-id')
        standard_id = self.request.GET.get('standard')
        student_id = self.request.GET.get('student')
        status_value = (self.request.GET.get('status') or '').strip()
        date_from = self.request.GET.get('dateFrom')
        date_to = self.request.GET.get('dateTo')
        if standard_id:
            qs = qs.filter(standardID_id=standard_id)
        if student_id:
            qs = qs.filter(studentID_id=student_id)
        if status_value:
            qs = qs.filter(status=status_value)
        if date_from:
            try:
                qs = qs.filter(chargeDate__gte=datetime.strptime(date_from, '%d/%m/%Y').date())
            except ValueError:
                pass
        if date_to:
            try:
                qs = qs.filter(chargeDate__lte=datetime.strptime(date_to, '%d/%m/%Y').date())
            except ValueError:
                pass
        return qs

    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(studentID__name__icontains=search)
                | Q(feeHeadID__name__icontains=search)
                | Q(referenceNo__icontains=search)
                | Q(title__icontains=search)
                | Q(description__icontains=search)
                | Q(status__icontains=search)
            )
        return qs

    def prepare_results(self, qs):
        json_data = []
        for item in qs:
            class_label = 'N/A'
            standard_obj = item.standardID or (item.studentID.standardID if item.studentID_id and item.studentID.standardID_id else None)
            if standard_obj:
                class_label = standard_obj.name or 'N/A'
                if standard_obj.section:
                    class_label = f'{class_label} - {standard_obj.section}'
            json_data.append([
                escape(item.chargeDate.strftime('%d-%m-%Y') if item.chargeDate else 'N/A'),
                escape(item.dueDate.strftime('%d-%m-%Y') if item.dueDate else '-'),
                escape(item.studentID.name if item.studentID_id and item.studentID.name else '-'),
                escape(class_label),
                escape(item.feeHeadID.name if item.feeHeadID_id else 'N/A'),
                escape(item.referenceNo or '-'),
                escape(item.description or item.title or '-'),
                escape(f'Rs {float(_decimal_or_zero(item.netAmount)):.2f}'),
                escape(f'Rs {float(_decimal_or_zero(item.paidAmount)):.2f}'),
                escape(f'Rs {float(_decimal_or_zero(item.balanceAmount)):.2f}'),
                _finance_status_pill(item.status),
            ])
        return json_data


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

    try:
        payload = _build_student_finance_ledger_payload(
            school_id=school_id,
            session_id=session_id,
            student_id=student_id,
        )
    except ValueError:
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
        action = f'<a class="ui mini blue button" target="_blank" href="{row["receiptUrl"]}"><i class="print icon"></i>Receipt</a>' if row.get('receiptUrl') else '-'
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
    return _datatable_json_response(draw=draw, total_count=total_count, filtered_count=filtered_count, rows=data)


def _vendor_payable_components(voucher_obj):
    amount = _decimal_or_zero(voucher_obj.netAmount)
    accrued_amount = Decimal('0.00')
    paid_amount = Decimal('0.00')
    if voucher_obj.isImmediatePayment:
        if voucher_obj.approvalStatus == 'paid':
            paid_amount = amount
        return accrued_amount, paid_amount
    if voucher_obj.approvalStatus in {'approved', 'paid'}:
        accrued_amount = amount
    if voucher_obj.approvalStatus == 'paid':
        paid_amount = amount
    return accrued_amount, paid_amount


@login_required
@check_groups('Admin', 'Owner')
def get_vendor_list_api(request):
    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    if not school_id or not session_id:
        return SuccessResponse('Vendors loaded successfully.', data=[]).to_json_response()
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
    return SuccessResponse('Vendors loaded successfully.', data=data).to_json_response()


@login_required
@check_groups('Admin', 'Owner')
def search_vendor_suggestions_api(request):
    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    if not school_id or not session_id:
        return SuccessResponse('Vendor suggestions loaded successfully.', data=[]).to_json_response()

    query = (request.GET.get('q') or '').strip()
    limit = _safe_int(request.GET.get('limit'), 8)
    if limit <= 0:
        limit = 8
    limit = min(limit, 20)

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
    return SuccessResponse('Vendor suggestions loaded successfully.', data=data).to_json_response()


class FinanceVendorListJson(BaseDatatableView):
    order_columns = ['displayName', 'phoneNumber', 'email', 'taxIdentifier', 'isActive', 'lastUpdatedOn', 'id']

    def get_initial_queryset(self):
        school_id = _current_school_id(self.request)
        session_id = _current_session_id(self.request)
        if not school_id or not session_id:
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
        return json_data


@transaction.atomic
@csrf_exempt
@login_required
@check_groups('Admin', 'Owner')
def upsert_vendor_api(request):
    if request.method != 'POST':
        return ErrorResponse('Method not allowed.', status_code=405).to_json_response()
    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    if not school_id or not session_id:
        return ErrorResponse('School session was not found.').to_json_response()

    vendor_id = request.POST.get('id')
    display_name = (request.POST.get('displayName') or '').strip()
    phone_number = (request.POST.get('phoneNumber') or '').strip()
    email = (request.POST.get('email') or '').strip()
    address = (request.POST.get('address') or '').strip()
    tax_identifier = (request.POST.get('taxIdentifier') or '').strip()
    is_active = _truthy(request.POST.get('isActive') or 'true')

    if not display_name:
        return ErrorResponse('Vendor name is required.').to_json_response()

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
        return ErrorResponse('Vendor name already exists.').to_json_response()

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
    instance.lastEditedBy = (f'{request.user.first_name} {request.user.last_name}'.strip() or request.user.username)
    instance.updatedByUserID = request.user
    instance.isDeleted = False
    instance.full_clean()
    instance.save()
    return SuccessResponse('Vendor saved successfully.').to_json_response()


@transaction.atomic
@csrf_exempt
@login_required
@check_groups('Admin', 'Owner')
def delete_vendor_api(request):
    if request.method != 'POST':
        return ErrorResponse('Method not allowed.', status_code=405).to_json_response()
    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    vendor_id = request.POST.get('id')
    instance = FinanceParty.objects.filter(
        pk=vendor_id,
        schoolID_id=school_id,
        sessionID_id=session_id,
        partyType='vendor',
        isDeleted=False,
    ).first()
    if not instance:
        return ErrorResponse('Vendor not found.').to_json_response()
    if ExpenseVoucher.objects.filter(
        partyID=instance,
        schoolID_id=school_id,
        sessionID_id=session_id,
        isDeleted=False,
    ).exists():
        return ErrorResponse('Vendor is already used in expense vouchers and cannot be deleted.').to_json_response()
    instance.isDeleted = True
    instance.lastEditedBy = (f'{request.user.first_name} {request.user.last_name}'.strip() or request.user.username)
    instance.updatedByUserID = request.user
    instance.save(update_fields=['isDeleted', 'lastEditedBy', 'updatedByUserID', 'lastUpdatedOn'])
    return SuccessResponse('Vendor deleted successfully.').to_json_response()


@login_required
@check_groups('Admin', 'Owner')
def get_vendor_payables_api(request):
    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    if not school_id or not session_id:
        return SuccessResponse('Vendor payables loaded successfully.', data={
            'summary': {'vendorCount': 0, 'vendorsWithBalance': 0, 'outstandingAmount': 0, 'paidAmount': 0},
            'vendorOptions': [],
        }).to_json_response()

    vendor_rows = FinanceParty.objects.filter(
        schoolID_id=school_id,
        sessionID_id=session_id,
        partyType='vendor',
        isDeleted=False,
    ).order_by('displayName', 'id').values('id', 'displayName')

    voucher_qs = ExpenseVoucher.objects.select_related('partyID').filter(
        schoolID_id=school_id,
        sessionID_id=session_id,
        isDeleted=False,
        partyID__partyType='vendor',
    )

    outstanding_total = Decimal('0.00')
    paid_total = Decimal('0.00')
    vendor_balances = {}
    for voucher_obj in voucher_qs:
        if not voucher_obj.partyID_id:
            continue
        accrued_amount, paid_amount = _vendor_payable_components(voucher_obj)
        outstanding_amount = accrued_amount - paid_amount
        if outstanding_amount < 0:
            outstanding_amount = Decimal('0.00')
        vendor_balances.setdefault(voucher_obj.partyID_id, Decimal('0.00'))
        vendor_balances[voucher_obj.partyID_id] += outstanding_amount
        outstanding_total += outstanding_amount
        paid_total += paid_amount

    return SuccessResponse('Vendor payables loaded successfully.', data={
        'summary': {
            'vendorCount': len(list(vendor_rows)),
            'vendorsWithBalance': sum(1 for value in vendor_balances.values() if value > 0),
            'outstandingAmount': float(outstanding_total),
            'paidAmount': float(paid_total),
        },
        'vendorOptions': [
            {'ID': row['id'], 'Name': row['displayName'], 'Label': row['displayName']}
            for row in vendor_rows
        ],
    }).to_json_response()


@login_required
@check_groups('Admin', 'Owner')
def finance_vendor_payables_rows_api(request):
    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    draw = _safe_int(request.GET.get('draw'), 1)
    start = max(_safe_int(request.GET.get('start'), 0), 0)
    length = _safe_int(request.GET.get('length'), 10)
    if length < 0:
        length = 10_000
    search = (request.GET.get('search[value]') or '').strip().lower()
    vendor_id = request.GET.get('vendorID')
    status_filter = (request.GET.get('status') or 'all').strip().lower()
    order_col_idx = _safe_int(request.GET.get('order[0][column]'), 0)
    order_dir = (request.GET.get('order[0][dir]') or 'asc').lower()
    if not school_id or not session_id:
        return _datatable_json_response(draw=draw, total_count=0, filtered_count=0, rows=[])

    vendor_map = {
        row.id: row
        for row in FinanceParty.objects.filter(
            schoolID_id=school_id,
            sessionID_id=session_id,
            partyType='vendor',
            isDeleted=False,
        )
    }
    voucher_qs = ExpenseVoucher.objects.select_related('partyID').filter(
        schoolID_id=school_id,
        sessionID_id=session_id,
        isDeleted=False,
        partyID__partyType='vendor',
    ).order_by('-voucherDate', '-id')
    if vendor_id:
        voucher_qs = voucher_qs.filter(partyID_id=vendor_id)

    summary_rows = {}
    for voucher_obj in voucher_qs:
        if not voucher_obj.partyID_id:
            continue
        vendor_obj = vendor_map.get(voucher_obj.partyID_id)
        if not vendor_obj:
            continue
        accrued_amount, paid_amount = _vendor_payable_components(voucher_obj)
        outstanding_amount = accrued_amount - paid_amount
        if outstanding_amount < 0:
            outstanding_amount = Decimal('0.00')
        row = summary_rows.setdefault(vendor_obj.id, {
            'id': vendor_obj.id,
            'vendorName': vendor_obj.displayName or '',
            'phoneNumber': vendor_obj.phoneNumber or '',
            'voucherCount': 0,
            'accruedAmount': Decimal('0.00'),
            'paidAmount': Decimal('0.00'),
            'outstandingAmount': Decimal('0.00'),
            'lastVoucherDateObj': date.min,
            'lastVoucherDate': 'N/A',
        })
        row['voucherCount'] += 1
        row['accruedAmount'] += accrued_amount
        row['paidAmount'] += paid_amount
        row['outstandingAmount'] += outstanding_amount
        if voucher_obj.voucherDate and voucher_obj.voucherDate >= row['lastVoucherDateObj']:
            row['lastVoucherDateObj'] = voucher_obj.voucherDate
            row['lastVoucherDate'] = voucher_obj.voucherDate.strftime('%d-%m-%Y')

    rows = list(summary_rows.values())
    if status_filter == 'outstanding':
        rows = [row for row in rows if row['outstandingAmount'] > 0]
    elif status_filter == 'settled':
        rows = [row for row in rows if row['voucherCount'] > 0 and row['outstandingAmount'] <= 0]

    total_count = len(rows)
    if search:
        rows = [
            row for row in rows
            if search in ' '.join([
                row.get('vendorName', ''),
                row.get('phoneNumber', ''),
                row.get('lastVoucherDate', ''),
                str(row.get('voucherCount', '')),
            ]).lower()
        ]
    filtered_count = len(rows)
    sort_keys = {
        0: lambda row: row.get('vendorName') or '',
        1: lambda row: row.get('phoneNumber') or '',
        2: lambda row: row.get('voucherCount') or 0,
        3: lambda row: row.get('accruedAmount') or Decimal('0.00'),
        4: lambda row: row.get('paidAmount') or Decimal('0.00'),
        5: lambda row: row.get('outstandingAmount') or Decimal('0.00'),
        6: lambda row: row.get('lastVoucherDateObj') or date.min,
        7: lambda row: row.get('outstandingAmount') or Decimal('0.00'),
        8: lambda row: row.get('id') or 0,
    }
    rows = sorted(rows, key=sort_keys.get(order_col_idx, sort_keys[0]), reverse=(order_dir == 'desc'))
    page_rows = rows[start:start + length]
    data = []
    for row in page_rows:
        status_label = 'settled' if row['outstandingAmount'] <= 0 else 'outstanding'
        action = (
            f'<a href="/management/finance/vendor-statement/?vendor={row["id"]}" '
            f'data-inverted="" data-tooltip="Open Statement" data-position="left center" data-variation="mini" '
            f'style="font-size:10px;" class="ui circular blue icon button"><i class="book open icon"></i></a>'
        )
        data.append([
            f'<strong>{escape(row["vendorName"])}</strong>',
            escape(row.get('phoneNumber') or '-'),
            escape(str(row.get('voucherCount') or 0)),
            escape(f'Rs {float(row.get("accruedAmount") or 0):.2f}'),
            escape(f'Rs {float(row.get("paidAmount") or 0):.2f}'),
            escape(f'Rs {float(row.get("outstandingAmount") or 0):.2f}'),
            escape(row.get('lastVoucherDate') or 'N/A'),
            _finance_status_pill(status_label),
            action,
        ])
    return _datatable_json_response(draw=draw, total_count=total_count, filtered_count=filtered_count, rows=data)


@login_required
@check_groups('Admin', 'Owner')
def finance_vendor_outstanding_voucher_rows_api(request):
    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    draw = _safe_int(request.GET.get('draw'), 1)
    start = max(_safe_int(request.GET.get('start'), 0), 0)
    length = _safe_int(request.GET.get('length'), 10)
    if length < 0:
        length = 10_000
    search = (request.GET.get('search[value]') or '').strip().lower()
    vendor_id = request.GET.get('vendorID')
    status_filter = (request.GET.get('status') or 'outstanding').strip().lower()
    order_col_idx = _safe_int(request.GET.get('order[0][column]'), 0)
    order_dir = (request.GET.get('order[0][dir]') or 'asc').lower()
    if not school_id or not session_id:
        return _datatable_json_response(draw=draw, total_count=0, filtered_count=0, rows=[])

    voucher_qs = ExpenseVoucher.objects.select_related('partyID', 'expenseCategoryID').filter(
        schoolID_id=school_id,
        sessionID_id=session_id,
        isDeleted=False,
        partyID__partyType='vendor',
    ).order_by('-voucherDate', '-id')
    if vendor_id:
        voucher_qs = voucher_qs.filter(partyID_id=vendor_id)

    rows = []
    for voucher_obj in voucher_qs:
        accrued_amount, paid_amount = _vendor_payable_components(voucher_obj)
        outstanding_amount = accrued_amount - paid_amount
        if outstanding_amount < 0:
            outstanding_amount = Decimal('0.00')
        if status_filter == 'outstanding' and outstanding_amount <= 0:
            continue
        if status_filter == 'settled' and outstanding_amount > 0:
            continue
        rows.append({
            'id': voucher_obj.id,
            'voucherDateObj': voucher_obj.voucherDate or date.min,
            'voucherDate': voucher_obj.voucherDate.strftime('%d-%m-%Y') if voucher_obj.voucherDate else 'N/A',
            'voucherNo': voucher_obj.voucherNo or '',
            'vendorName': voucher_obj.partyID.displayName if voucher_obj.partyID_id else '',
            'title': voucher_obj.title or '',
            'categoryName': voucher_obj.expenseCategoryID.name if voucher_obj.expenseCategoryID_id else '',
            'accruedAmount': accrued_amount,
            'paidAmount': paid_amount,
            'outstandingAmount': outstanding_amount,
            'statusLabel': 'settled' if outstanding_amount <= 0 else 'outstanding',
        })

    total_count = len(rows)
    if search:
        rows = [
            row for row in rows
            if search in ' '.join([
                row.get('voucherDate', ''),
                row.get('voucherNo', ''),
                row.get('vendorName', ''),
                row.get('title', ''),
                row.get('categoryName', ''),
            ]).lower()
        ]
    filtered_count = len(rows)
    sort_keys = {
        0: lambda row: row.get('voucherDateObj') or date.min,
        1: lambda row: row.get('voucherNo') or '',
        2: lambda row: row.get('vendorName') or '',
        3: lambda row: row.get('title') or '',
        4: lambda row: row.get('categoryName') or '',
        5: lambda row: row.get('accruedAmount') or Decimal('0.00'),
        6: lambda row: row.get('paidAmount') or Decimal('0.00'),
        7: lambda row: row.get('outstandingAmount') or Decimal('0.00'),
        8: lambda row: row.get('statusLabel') or '',
    }
    rows = sorted(rows, key=sort_keys.get(order_col_idx, sort_keys[0]), reverse=(order_dir == 'desc'))
    page_rows = rows[start:start + length]
    data = []
    for row in page_rows:
        data.append([
            escape(row.get('voucherDate') or 'N/A'),
            f'<strong>{escape(row.get("voucherNo") or "")}</strong>',
            escape(row.get('vendorName') or ''),
            escape(row.get('title') or ''),
            escape(row.get('categoryName') or ''),
            escape(f'Rs {float(row.get("accruedAmount") or 0):.2f}'),
            escape(f'Rs {float(row.get("paidAmount") or 0):.2f}'),
            escape(f'Rs {float(row.get("outstandingAmount") or 0):.2f}'),
            _finance_status_pill(row.get('statusLabel')),
        ])
    return _datatable_json_response(draw=draw, total_count=total_count, filtered_count=filtered_count, rows=data)


def _build_vendor_statement_payload(*, school_id, session_id, vendor_id):
    if not school_id or not session_id or not vendor_id:
        return {
            'summary': {
                'vendorName': '',
                'totalAccrued': 0,
                'totalPaid': 0,
                'closingBalance': 0,
                'openVoucherCount': 0,
                'settledVoucherCount': 0,
                'voucherCount': 0,
            },
            'rows': [],
        }

    vendor_obj = FinanceParty.objects.filter(
        pk=vendor_id,
        schoolID_id=school_id,
        sessionID_id=session_id,
        partyType='vendor',
        isDeleted=False,
    ).first()
    if not vendor_obj:
        raise ValueError('Vendor not found.')

    voucher_qs = ExpenseVoucher.objects.select_related(
        'expenseCategoryID',
        'paymentModeID',
    ).filter(
        schoolID_id=school_id,
        sessionID_id=session_id,
        partyID_id=vendor_obj.id,
        isDeleted=False,
    ).order_by('voucherDate', 'id')

    events = []
    total_accrued = Decimal('0.00')
    total_paid = Decimal('0.00')
    open_voucher_count = 0
    settled_voucher_count = 0
    voucher_count = 0

    for voucher_obj in voucher_qs:
        amount = _decimal_or_zero(voucher_obj.netAmount)
        if amount <= 0:
            continue
        accrued_amount, paid_amount = _vendor_payable_components(voucher_obj)
        outstanding_amount = accrued_amount - paid_amount
        if outstanding_amount < 0:
            outstanding_amount = Decimal('0.00')
        if accrued_amount > 0 or paid_amount > 0:
            voucher_count += 1
        if outstanding_amount > 0:
            open_voucher_count += 1
        elif accrued_amount > 0 or paid_amount > 0:
            settled_voucher_count += 1

        category_name = voucher_obj.expenseCategoryID.name if voucher_obj.expenseCategoryID_id else 'Expense'
        voucher_title = voucher_obj.title or voucher_obj.description or 'Expense Voucher'
        voucher_status = voucher_obj.approvalStatus or 'draft'
        payment_mode = voucher_obj.paymentModeID.name if voucher_obj.paymentModeID_id else ''

        if voucher_obj.isImmediatePayment and voucher_status == 'paid':
            events.append({
                'date_obj': voucher_obj.voucherDate,
                'sort_key': (_safe_sort_date(voucher_obj.voucherDate), 0, voucher_obj.id, 0),
                'type': 'accrual',
                'reference': voucher_obj.voucherNo or f'VEN-{voucher_obj.id}',
                'category': category_name,
                'label': voucher_title,
                'note': 'Immediate payment voucher accrued and settled on the same date.',
                'debit_value': Decimal('0.00'),
                'credit_value': amount,
                'status': 'accrued',
            })
            total_accrued += amount
            events.append({
                'date_obj': voucher_obj.voucherDate,
                'sort_key': (_safe_sort_date(voucher_obj.voucherDate), 1, voucher_obj.id, 1),
                'type': 'payment',
                'reference': voucher_obj.voucherNo or f'VEN-{voucher_obj.id}',
                'category': category_name,
                'label': voucher_title,
                'note': f'Direct payment{f" via {payment_mode}" if payment_mode else ""}.',
                'debit_value': amount,
                'credit_value': Decimal('0.00'),
                'status': voucher_status,
            })
            total_paid += amount
            continue

        if accrued_amount > 0:
            events.append({
                'date_obj': voucher_obj.voucherDate,
                'sort_key': (_safe_sort_date(voucher_obj.voucherDate), 0, voucher_obj.id, 0),
                'type': 'accrual',
                'reference': voucher_obj.voucherNo or f'VEN-{voucher_obj.id}',
                'category': category_name,
                'label': voucher_title,
                'note': voucher_obj.description or voucher_title,
                'debit_value': Decimal('0.00'),
                'credit_value': accrued_amount,
                'status': 'accrued',
            })
            total_accrued += accrued_amount

        if paid_amount > 0:
            events.append({
                'date_obj': voucher_obj.voucherDate,
                'sort_key': (_safe_sort_date(voucher_obj.voucherDate), 1, voucher_obj.id, 1),
                'type': 'payment',
                'reference': voucher_obj.voucherNo or f'VEN-{voucher_obj.id}',
                'category': category_name,
                'label': voucher_title,
                'note': f'Voucher settled{f" via {payment_mode}" if payment_mode else ""}.',
                'debit_value': paid_amount,
                'credit_value': Decimal('0.00'),
                'status': voucher_status,
            })
            total_paid += paid_amount

    events.sort(key=lambda item: item['sort_key'])
    running_balance = Decimal('0.00')
    rows = []
    for item in events:
        running_balance += item['credit_value']
        running_balance -= item['debit_value']
        rows.append({
            'date': item['date_obj'].strftime('%d-%m-%Y') if item['date_obj'] else 'N/A',
            'date_obj': item['date_obj'],
            'type': item['type'],
            'reference': item['reference'],
            'category': item['category'],
            'label': item['label'],
            'note': item['note'],
            'debit': float(item['debit_value']),
            'credit': float(item['credit_value']),
            'balance': float(running_balance),
            'status': item['status'],
        })

    return {
        'summary': {
            'vendorName': vendor_obj.displayName or 'N/A',
            'totalAccrued': float(total_accrued),
            'totalPaid': float(total_paid),
            'closingBalance': float(total_accrued - total_paid),
            'openVoucherCount': open_voucher_count,
            'settledVoucherCount': settled_voucher_count,
            'voucherCount': voucher_count,
        },
        'rows': rows,
    }


@login_required
@check_groups('Admin', 'Owner')
def get_vendor_statement_api(request):
    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    vendor_id = request.GET.get('vendor')
    try:
        payload = _build_vendor_statement_payload(
            school_id=school_id,
            session_id=session_id,
            vendor_id=vendor_id,
        )
    except ValueError as exc:
        return ErrorResponse(str(exc)).to_json_response()
    except Exception as exc:
        logger.exception('Unable to build vendor statement payload.')
        return ErrorResponse(f'Unable to load vendor statement: {exc}').to_json_response()
    return SuccessResponse('Vendor statement loaded successfully.', data=payload).to_json_response()


@login_required
@check_groups('Admin', 'Owner')
def finance_vendor_statement_rows_api(request):
    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    vendor_id = request.GET.get('vendor')
    draw = _safe_int(request.GET.get('draw'), 1)
    start = max(_safe_int(request.GET.get('start'), 0), 0)
    length = _safe_int(request.GET.get('length'), 10)
    if length < 0:
        length = 10_000
    search = (request.GET.get('search[value]') or '').strip().lower()
    order_col_idx = _safe_int(request.GET.get('order[0][column]'), 0)
    order_dir = (request.GET.get('order[0][dir]') or 'asc').lower()

    try:
        payload = _build_vendor_statement_payload(
            school_id=school_id,
            session_id=session_id,
            vendor_id=vendor_id,
        )
    except ValueError:
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
                str(row.get('category', '')),
                str(row.get('label', '')),
                str(row.get('note', '')),
                str(row.get('status', '')),
            ]).lower()
        ]
    filtered_count = len(rows)

    sort_keys = {
        0: lambda row: row.get('date_obj') or datetime.min.date(),
        1: lambda row: row.get('type') or '',
        2: lambda row: row.get('reference') or '',
        3: lambda row: row.get('category') or '',
        4: lambda row: row.get('label') or '',
        5: lambda row: row.get('note') or '',
        6: lambda row: row.get('debit') or 0,
        7: lambda row: row.get('credit') or 0,
        8: lambda row: row.get('balance') or 0,
        9: lambda row: row.get('status') or '',
    }
    rows = sorted(rows, key=sort_keys.get(order_col_idx, sort_keys[0]), reverse=(order_dir == 'desc'))
    page_rows = rows[start:start + length]
    data = []
    for row in page_rows:
        data.append([
            escape(row.get('date') or 'N/A'),
            escape(str(row.get('type') or '').replace('_', ' ').title()),
            f'<strong>{escape(row.get("reference") or "-")}</strong>',
            escape(row.get('category') or '-'),
            escape(row.get('label') or '-'),
            escape(row.get('note') or '-'),
            escape(f'Rs {float(row.get("debit") or 0):.2f}'),
            escape(f'Rs {float(row.get("credit") or 0):.2f}'),
            escape(f'Rs {float(row.get("balance") or 0):.2f}'),
            _finance_status_pill(row.get('status')),
        ])
    return _datatable_json_response(draw=draw, total_count=total_count, filtered_count=filtered_count, rows=data)


@login_required
@check_groups('Admin', 'Owner')
def get_expense_category_list_api(request):
    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    if not school_id or not session_id:
        return SuccessResponse('Expense categories loaded.', data=[]).to_json_response()
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
            'isSystemGenerated': row.code in {'OFFICE', 'UTILITY'},
            'updatedOn': row.lastUpdatedOn.strftime('%d-%m-%Y %I:%M %p') if row.lastUpdatedOn else 'N/A',
        })
    return SuccessResponse('Expense categories loaded.', data=data).to_json_response()


class FinanceExpenseCategoryListJson(BaseDatatableView):
    order_columns = ['code', 'name', 'expenseAccountID__accountName', 'payableAccountID__accountName', 'isActive',
                     'lastUpdatedOn', 'id']

    def get_initial_queryset(self):
        school_id = _current_school_id(self.request)
        session_id = _current_session_id(self.request)
        if not school_id or not session_id:
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
            if item.code not in {'OFFICE', 'UTILITY'}:
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
        return json_data


@transaction.atomic
@csrf_exempt
@login_required
@check_groups('Admin', 'Owner')
def upsert_expense_category_api(request):
    if request.method != 'POST':
        return ErrorResponse('Method not allowed.', status_code=405).to_json_response()
    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    if not school_id or not session_id:
        return ErrorResponse('School session was not found.').to_json_response()
    bootstrap_expense_categories(school_id=school_id, session_id=session_id, user_obj=request.user)

    category_id = request.POST.get('id')
    code = (request.POST.get('code') or '').strip().upper().replace(' ', '_')
    name = (request.POST.get('name') or '').strip()
    expense_account_id = request.POST.get('expenseAccountID')
    payable_account_id = request.POST.get('payableAccountID')
    is_active = _truthy(request.POST.get('isActive') or 'true')
    if not code or not name:
        return ErrorResponse('Code and name are required.').to_json_response()

    expense_account = FinanceAccount.objects.filter(pk=expense_account_id, schoolID_id=school_id, sessionID_id=session_id, isDeleted=False).first()
    payable_account = FinanceAccount.objects.filter(pk=payable_account_id, schoolID_id=school_id, sessionID_id=session_id, isDeleted=False).first()
    if not expense_account:
        return ErrorResponse('Expense account is required.').to_json_response()

    instance = None
    if category_id:
        instance = ExpenseCategory.objects.filter(pk=category_id, schoolID_id=school_id, sessionID_id=session_id, isDeleted=False).first()
        if not instance:
            return ErrorResponse('Expense category not found.').to_json_response()
        if instance.code in {'OFFICE', 'UTILITY'} and code != instance.code:
            return ErrorResponse('System category code cannot be changed.').to_json_response()

    duplicate_qs = ExpenseCategory.objects.filter(schoolID_id=school_id, sessionID_id=session_id, code__iexact=code, isDeleted=False)
    if instance:
        duplicate_qs = duplicate_qs.exclude(pk=instance.pk)
    if duplicate_qs.exists():
        return ErrorResponse('Expense category code already exists.').to_json_response()

    if not instance:
        instance = ExpenseCategory(schoolID_id=school_id, sessionID_id=session_id)
    instance.code = code
    instance.name = name
    instance.expenseAccountID = expense_account
    instance.payableAccountID = payable_account
    instance.isActive = is_active
    instance.isDeleted = False
    instance.lastEditedBy = (f'{request.user.first_name} {request.user.last_name}'.strip() or request.user.username)
    instance.updatedByUserID = request.user
    instance.full_clean()
    instance.save()
    return SuccessResponse('Expense category saved successfully.').to_json_response()


@transaction.atomic
@csrf_exempt
@login_required
@check_groups('Admin', 'Owner')
def delete_expense_category_api(request):
    if request.method != 'POST':
        return ErrorResponse('Method not allowed.', status_code=405).to_json_response()
    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    category_id = request.POST.get('id')
    instance = ExpenseCategory.objects.filter(pk=category_id, schoolID_id=school_id, sessionID_id=session_id, isDeleted=False).first()
    if not instance:
        return ErrorResponse('Expense category not found.').to_json_response()
    if instance.code in {'OFFICE', 'UTILITY'}:
        return ErrorResponse('System categories cannot be deleted.').to_json_response()
    if ExpenseVoucher.objects.filter(expenseCategoryID_id=instance.id, schoolID_id=school_id, sessionID_id=session_id, isDeleted=False).exists():
        return ErrorResponse('Expense category is already used in vouchers and cannot be deleted.').to_json_response()
    instance.isDeleted = True
    instance.lastEditedBy = (f'{request.user.first_name} {request.user.last_name}'.strip() or request.user.username)
    instance.updatedByUserID = request.user
    instance.save(update_fields=['isDeleted', 'lastEditedBy', 'updatedByUserID', 'lastUpdatedOn'])
    return SuccessResponse('Expense category deleted successfully.').to_json_response()


@login_required
@check_groups('Admin', 'Owner')
def get_expense_voucher_list_api(request):
    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    if not school_id or not session_id:
        return SuccessResponse('Expense vouchers loaded.', data=[]).to_json_response()
    rows = ExpenseVoucher.objects.select_related(
        'expenseCategoryID', 'partyID', 'paymentModeID', 'paymentAccountID'
    ).filter(
        schoolID_id=school_id,
        sessionID_id=session_id,
        isDeleted=False,
    ).order_by('-voucherDate', '-datetime', '-id')
    data = []
    for row in rows:
        data.append({
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
        })
    return SuccessResponse('Expense vouchers loaded.', data=data).to_json_response()


class FinanceExpenseVoucherListJson(BaseDatatableView):
    order_columns = ['voucherDate', 'voucherNo', 'title', 'expenseCategoryID__name', 'partyID__displayName',
                     'netAmount', 'approvalStatus', 'paymentModeID__name', 'id']

    def get_initial_queryset(self):
        school_id = _current_school_id(self.request)
        session_id = _current_session_id(self.request)
        if not school_id or not session_id:
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
            payment_label = (item.paymentModeID.name if item.paymentModeID_id else '')
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
        return json_data


@transaction.atomic
@csrf_exempt
@login_required
@check_groups('Admin', 'Owner')
def upsert_expense_voucher_api(request):
    if request.method != 'POST':
        return ErrorResponse('Method not allowed.', status_code=405).to_json_response()
    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    if not school_id or not session_id:
        return ErrorResponse('School session was not found.').to_json_response()
    bootstrap_expense_categories(school_id=school_id, session_id=session_id, user_obj=request.user)

    voucher_id = request.POST.get('id')
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

    if not title or not category_id or gross_amount <= 0:
        return ErrorResponse('Voucher title, category, and gross amount are required.').to_json_response()

    try:
        voucher_date = datetime.strptime(voucher_date_raw, '%d/%m/%Y').date() if voucher_date_raw else timezone.now().date()
    except ValueError:
        return ErrorResponse('Voucher date is invalid.').to_json_response()
    try:
        _assert_finance_date_open(school_id=school_id, session_id=session_id, txn_date=voucher_date, label='Voucher date')
    except ValidationError as exc:
        return ErrorResponse('; '.join(exc.messages)).to_json_response()
    try:
        bill_date = datetime.strptime(bill_date_raw, '%d/%m/%Y').date() if bill_date_raw else None
    except ValueError:
        return ErrorResponse('Bill date is invalid.').to_json_response()

    category = ExpenseCategory.objects.filter(pk=category_id, schoolID_id=school_id, sessionID_id=session_id, isDeleted=False).first()
    if not category:
        return ErrorResponse('Expense category not found.').to_json_response()

    payment_mode = FinancePaymentMode.objects.filter(pk=payment_mode_id, schoolID_id=school_id, isDeleted=False).first() if payment_mode_id else None
    payment_account = FinanceAccount.objects.filter(pk=payment_account_id, schoolID_id=school_id, sessionID_id=session_id, isDeleted=False).first() if payment_account_id else None

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
        instance = ExpenseVoucher.objects.filter(pk=voucher_id, schoolID_id=school_id, sessionID_id=session_id, isDeleted=False).first()
        if not instance:
            return ErrorResponse('Expense voucher not found.').to_json_response()

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
            instance.paymentAccountID_id != (payment_account.id if payment_account else (payment_mode.linkedAccountID_id if payment_mode and payment_mode.linkedAccountID_id else None)),
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
    instance.paymentAccountID = payment_account or (payment_mode.linkedAccountID if payment_mode and payment_mode.linkedAccountID_id else None)
    instance.billNo = bill_no
    instance.billDate = bill_date
    instance.requestedApprovalStatus = approval_resolution['requested_status']
    instance.approvalStatus = approval_resolution['effective_status']
    instance.isImmediatePayment = is_immediate
    instance.sourceRecordID = str(instance.id or '')
    instance.isDeleted = False
    instance.lastEditedBy = (f'{request.user.first_name} {request.user.last_name}'.strip() or request.user.username)
    instance.updatedByUserID = request.user
    instance.full_clean()
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
    if force_resubmission:
        return SuccessResponse(
            'Approved expense voucher was updated and resubmitted for approval.'
        ).to_json_response()
    if approval_resolution['requires_queue']:
        rule_name = approval_resolution['rule'].ruleName if approval_resolution['rule'] else 'approval rule'
        return SuccessResponse(
            f'Expense voucher saved and submitted for approval based on rule: {rule_name}.'
        ).to_json_response()
    return SuccessResponse('Expense voucher saved successfully.').to_json_response()


@transaction.atomic
@csrf_exempt
@login_required
@check_groups('Admin', 'Owner')
def delete_expense_voucher_api(request):
    if request.method != 'POST':
        return ErrorResponse('Method not allowed.', status_code=405).to_json_response()
    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    voucher_id = request.POST.get('id')
    instance = ExpenseVoucher.objects.filter(pk=voucher_id, schoolID_id=school_id, sessionID_id=session_id, isDeleted=False).first()
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
        return ErrorResponse('; '.join(exc.messages)).to_json_response()
    instance.isDeleted = True
    instance.approvalStatus = 'cancelled'
    instance.lastEditedBy = (f'{request.user.first_name} {request.user.last_name}'.strip() or request.user.username)
    instance.updatedByUserID = request.user
    instance.save(update_fields=['isDeleted', 'approvalStatus', 'lastEditedBy', 'updatedByUserID', 'lastUpdatedOn'])
    sync_expense_voucher_posting(voucher_obj=instance, school_id=school_id, session_id=session_id, user_obj=request.user)
    return SuccessResponse('Expense voucher deleted successfully.').to_json_response()


@login_required
@check_groups('Admin', 'Owner')
def get_cash_bank_book_api(request):
    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    account_id = request.GET.get('accountID')
    date_from = request.GET.get('dateFrom')
    date_to = request.GET.get('dateTo')
    if not school_id or not session_id:
        return SuccessResponse('Cash & bank book loaded.', data={'summary': {}, 'rows': []}).to_json_response()
    bootstrap_expense_categories(school_id=school_id, session_id=session_id, user_obj=request.user)
    account_qs = FinanceAccount.objects.filter(
        schoolID_id=school_id,
        sessionID_id=session_id,
        accountType='asset',
        isDeleted=False,
        isActive=True,
    ).filter(
        Q(accountCode__in=['CASH_ON_HAND', 'BANK_MAIN']) | Q(financepaymentmode__isnull=False)
    ).distinct().order_by('accountName')
    selected_account = account_qs.filter(pk=account_id).first() if account_id else account_qs.first()
    if not selected_account:
        return SuccessResponse('Cash & bank book loaded.', data={'summary': {}, 'rows': []}).to_json_response()

    entry_qs = FinanceEntry.objects.select_related('transactionID', 'partyID').filter(
        transactionID__schoolID_id=school_id,
        transactionID__sessionID_id=session_id,
        transactionID__isDeleted=False,
        accountID_id=selected_account.id,
    ).order_by('transactionID__txnDate', 'transactionID__id', 'lineOrder')
    if date_from:
        try:
            date_from_value = datetime.strptime(date_from, '%d/%m/%Y').date()
            entry_qs = entry_qs.filter(transactionID__txnDate__gte=date_from_value)
        except ValueError:
            pass
    if date_to:
        try:
            date_to_value = datetime.strptime(date_to, '%d/%m/%Y').date()
            entry_qs = entry_qs.filter(transactionID__txnDate__lte=date_to_value)
        except ValueError:
            pass

    running_balance = _decimal_or_zero(selected_account.openingBalance)
    if selected_account.openingBalanceType == 'credit':
        running_balance *= Decimal('-1')
    rows = []
    total_debit = Decimal('0.00')
    total_credit = Decimal('0.00')
    for entry in entry_qs:
        amount = _decimal_or_zero(entry.amount)
        debit = amount if entry.entryType == 'debit' else Decimal('0.00')
        credit = amount if entry.entryType == 'credit' else Decimal('0.00')
        total_debit += debit
        total_credit += credit
        running_balance += debit
        running_balance -= credit
        rows.append({
            'date': entry.transactionID.txnDate.strftime('%d-%m-%Y') if entry.transactionID.txnDate else 'N/A',
            'txnNo': entry.transactionID.txnNo,
            'txnType': entry.transactionID.txnType,
            'reference': entry.transactionID.referenceNo or '',
            'party': entry.partyID.displayName if entry.partyID_id else '',
            'narration': entry.narration or entry.transactionID.description or '',
            'debit': float(debit),
            'credit': float(credit),
            'balance': float(running_balance),
        })
    summary = {
        'accountLabel': str(selected_account),
        'openingBalance': float(selected_account.openingBalance or 0),
        'openingBalanceType': selected_account.openingBalanceType,
        'totalDebit': float(total_debit),
        'totalCredit': float(total_credit),
        'closingBalance': float(running_balance),
    }
    return SuccessResponse('Cash & bank book loaded.', data={'summary': summary, 'rows': rows}).to_json_response()


def _build_cash_bank_book_payload(*, school_id, session_id, account_id, date_from='', date_to='', user_obj=None):
    if not school_id or not session_id:
        return {'summary': {}, 'rows': []}
    bootstrap_expense_categories(school_id=school_id, session_id=session_id, user_obj=user_obj)
    account_qs = FinanceAccount.objects.filter(
        schoolID_id=school_id,
        sessionID_id=session_id,
        accountType='asset',
        isDeleted=False,
        isActive=True,
    ).filter(
        Q(accountCode__in=['CASH_ON_HAND', 'BANK_MAIN']) | Q(financepaymentmode__isnull=False)
    ).distinct().order_by('accountName')
    selected_account = account_qs.filter(pk=account_id).first() if account_id else account_qs.first()
    if not selected_account:
        return {'summary': {}, 'rows': []}

    entry_qs = FinanceEntry.objects.select_related('transactionID', 'partyID').filter(
        transactionID__schoolID_id=school_id,
        transactionID__sessionID_id=session_id,
        transactionID__isDeleted=False,
        accountID_id=selected_account.id,
    ).order_by('transactionID__txnDate', 'transactionID__id', 'lineOrder')
    if date_from:
        try:
            entry_qs = entry_qs.filter(transactionID__txnDate__gte=datetime.strptime(date_from, '%d/%m/%Y').date())
        except ValueError:
            pass
    if date_to:
        try:
            entry_qs = entry_qs.filter(transactionID__txnDate__lte=datetime.strptime(date_to, '%d/%m/%Y').date())
        except ValueError:
            pass

    running_balance = _decimal_or_zero(selected_account.openingBalance)
    if selected_account.openingBalanceType == 'credit':
        running_balance *= Decimal('-1')
    rows = []
    total_debit = Decimal('0.00')
    total_credit = Decimal('0.00')
    for entry in entry_qs:
        amount = _decimal_or_zero(entry.amount)
        debit = amount if entry.entryType == 'debit' else Decimal('0.00')
        credit = amount if entry.entryType == 'credit' else Decimal('0.00')
        total_debit += debit
        total_credit += credit
        running_balance += debit
        running_balance -= credit
        rows.append({
            'date': entry.transactionID.txnDate.strftime('%d-%m-%Y') if entry.transactionID.txnDate else 'N/A',
            'date_obj': entry.transactionID.txnDate,
            'txnNo': entry.transactionID.txnNo,
            'txnType': entry.transactionID.txnType,
            'reference': entry.transactionID.referenceNo or '',
            'party': entry.partyID.displayName if entry.partyID_id else '',
            'narration': entry.narration or entry.transactionID.description or '',
            'debit': float(debit),
            'credit': float(credit),
            'balance': float(running_balance),
        })
    summary = {
        'accountLabel': str(selected_account),
        'openingBalance': float(selected_account.openingBalance or 0),
        'openingBalanceType': selected_account.openingBalanceType,
        'totalDebit': float(total_debit),
        'totalCredit': float(total_credit),
        'closingBalance': float(running_balance),
    }
    return {'summary': summary, 'rows': rows}


@login_required
@check_groups('Admin', 'Owner')
def finance_cash_bank_book_rows_api(request):
    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    account_id = request.GET.get('accountID')
    date_from = request.GET.get('dateFrom')
    date_to = request.GET.get('dateTo')
    draw = _safe_int(request.GET.get('draw'), 1)
    start = max(_safe_int(request.GET.get('start'), 0), 0)
    length = _safe_int(request.GET.get('length'), 10)
    if length < 0:
        length = 10_000
    search = (request.GET.get('search[value]') or '').strip().lower()
    order_col_idx = _safe_int(request.GET.get('order[0][column]'), 0)
    order_dir = (request.GET.get('order[0][dir]') or 'asc').lower()

    payload = _build_cash_bank_book_payload(
        school_id=school_id,
        session_id=session_id,
        account_id=account_id,
        date_from=date_from or '',
        date_to=date_to or '',
        user_obj=request.user,
    )
    rows = payload['rows']
    total_count = len(rows)
    if search:
        rows = [
            row for row in rows
            if search in ' '.join([
                str(row.get('date', '')),
                str(row.get('txnNo', '')),
                str(row.get('txnType', '')),
                str(row.get('reference', '')),
                str(row.get('party', '')),
                str(row.get('narration', '')),
            ]).lower()
        ]
    filtered_count = len(rows)
    sort_keys = {
        0: lambda row: row.get('date_obj') or datetime.min.date(),
        1: lambda row: row.get('txnNo') or '',
        2: lambda row: row.get('txnType') or '',
        3: lambda row: row.get('reference') or '',
        4: lambda row: row.get('party') or '',
        5: lambda row: row.get('narration') or '',
        6: lambda row: row.get('debit') or 0,
        7: lambda row: row.get('credit') or 0,
        8: lambda row: row.get('balance') or 0,
    }
    rows = sorted(rows, key=sort_keys.get(order_col_idx, sort_keys[0]), reverse=(order_dir == 'desc'))
    page_rows = rows[start:start + length]
    data = []
    for row in page_rows:
        data.append([
            escape(row.get('date') or 'N/A'),
            f'<strong>{escape(row.get("txnNo") or "")}</strong>',
            escape(row.get('txnType') or ''),
            escape(row.get('reference') or '-'),
            escape(row.get('party') or '-'),
            escape(row.get('narration') or '-'),
            escape(f'Rs {float(row.get("debit") or 0):.2f}'),
            escape(f'Rs {float(row.get("credit") or 0):.2f}'),
            escape(f'Rs {float(row.get("balance") or 0):.2f}'),
        ])
    return _datatable_json_response(draw=draw, total_count=total_count, filtered_count=filtered_count, rows=data)


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
