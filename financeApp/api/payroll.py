from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import DecimalField, Sum, Value
from django.db.models.functions import Coalesce
from django.views.decorators.csrf import csrf_exempt

from financeApp.api.expense_vouchers import _apply_finance_approval_rules, _assert_finance_date_open
from financeApp.models import FinancePaymentMode, PayrollLine, PayrollRun
from financeApp.services import (
    approve_payroll_payment,
    bootstrap_school_finance,
    generate_payroll_run,
    pay_payroll_line,
    post_payroll_run,
)
from homeApp.models import SchoolSession
from managementApp.models import TeacherDetail
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


def _user_label(user):
    full_name = f'{user.first_name} {user.last_name}'.strip()
    return full_name or user.username


def _parse_filter_date(value):
    raw = (value or '').strip()
    if not raw:
        return None
    from datetime import datetime
    for date_format in ('%d/%m/%Y', '%Y-%m-%d', '%d-%m-%Y'):
        try:
            return datetime.strptime(raw, date_format).date()
        except ValueError:
            continue
    return None


def _decimal_or_zero(value):
    try:
        return Decimal(str(value or '0')).quantize(Decimal('0.01'))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal('0.00')


def _payroll_line_name(line):
    if line.teacherID_id:
        return line.teacherID.name or ''
    if line.partyID_id:
        return line.partyID.displayName or ''
    return ''


@login_required
@check_groups('Admin', 'Owner')
def get_payroll_run_list_api(request):
    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    empty_data = {
        'summary': {'totalRuns': 0, 'postedRuns': 0, 'totalPayable': 0, 'totalPaid': 0, 'totalPending': 0},
        'rows': [],
    }
    if not school_id or not session_id:
        logger.warning(f'Payroll run list requested without school/session user={request.user.id}')
        return SuccessResponse('Payroll runs loaded successfully.', data=empty_data).to_json_response()

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
    for run_obj in run_qs:
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

    logger.info(
        f'Payroll run list loaded rows={len(rows)} school={school_id} session={session_id} user={request.user.id}'
    )
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
        logger.warning(f'Payroll run detail requested with incomplete context run={run_id} user={request.user.id}')
        return ErrorResponse('Payroll run not found.').to_json_response()

    run_obj = PayrollRun.objects.filter(
        pk=run_id,
        schoolID_id=school_id,
        sessionID_id=session_id,
        isDeleted=False,
    ).prefetch_related('payrollLines__teacherID', 'payrollLines__partyID').first()
    if not run_obj:
        logger.warning(
            f'Payroll run detail requested for missing run={run_id} school={school_id} '
            f'session={session_id} user={request.user.id}'
        )
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
        rows.append({
            'id': line.id,
            'teacherName': _payroll_line_name(line) or 'N/A',
            'basicAmount': float(_decimal_or_zero(line.basicAmount)),
            'allowanceAmount': float(_decimal_or_zero(line.allowanceAmount)),
            'deductionAmount': float(_decimal_or_zero(line.deductionAmount)),
            'advanceRecoveryAmount': float(_decimal_or_zero(line.advanceRecoveryAmount)),
            'netAmount': float(net_amount),
            'paymentStatus': line.paymentStatus,
            'paymentDate': line.paymentDate.strftime('%d-%m-%Y') if line.paymentDate else '',
        })

    logger.info(
        f'Payroll run detail loaded run={run_obj.id} rows={len(rows)} school={school_id} '
        f'session={session_id} user={request.user.id}'
    )
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
        logger.warning(f'Payroll run create requested without school/session user={request.user.id}')
        return ErrorResponse('School session was not found.').to_json_response()
    if month_value < 1 or month_value > 12 or year_value <= 0:
        return ErrorResponse('Valid month and year are required.').to_json_response()
    if not run_date:
        return ErrorResponse('Valid payroll run date is required.').to_json_response()
    try:
        _assert_finance_date_open(school_id=school_id, session_id=session_id, txn_date=run_date, label='Payroll run date')
    except ValidationError as exc:
        return ErrorResponse('; '.join(exc.messages)).to_json_response()

    teacher_qs = TeacherDetail.objects.filter(
        schoolID_id=school_id,
        sessionID_id=session_id,
        isDeleted=False,
        isActive='Yes',
    ).exclude(salary__isnull=True).exclude(salary__lte=0)
    teacher_count = teacher_qs.count()
    estimated_amount = teacher_qs.aggregate(
        total=Coalesce(Sum('salary'), Value(Decimal('0.00')), output_field=DecimalField(max_digits=14, decimal_places=2))
    )['total'] or Decimal('0.00')
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

    payload = {
        'id': payroll_run.id,
        'runNo': payroll_run.payrollRunNo or '',
        'period': f'{payroll_run.month:02d}/{payroll_run.year}',
    }
    if approval_resolution['requires_queue']:
        payload['teacherCount'] = teacher_count
        rule_name = approval_resolution['rule'].ruleName if approval_resolution['rule'] else 'approval rule'
        logger.info(f'Payroll run submitted for approval id={payroll_run.id} school={school_id} session={session_id} user={request.user.id}')
        return SuccessResponse(f'Payroll run generated and submitted for approval based on rule: {rule_name}.', data=payload).to_json_response()

    logger.info(f'Payroll run generated id={payroll_run.id} status={payroll_run.status} school={school_id} session={session_id} user={request.user.id}')
    return SuccessResponse('Payroll run generated successfully.', data=payload).to_json_response()


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
        logger.warning(f'Payroll post requested with incomplete context run={run_id} user={request.user.id}')
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
        _assert_finance_date_open(school_id=school_id, session_id=session_id, txn_date=payroll_run.runDate, label='Payroll posting date')
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
        payroll_run.lastEditedBy = _user_label(request.user)
        payroll_run.updatedByUserID = request.user
        payroll_run.save(update_fields=['status', 'requestedApprovalStatus', 'lastEditedBy', 'updatedByUserID', 'lastUpdatedOn'])
        rule_name = approval_resolution['rule'].ruleName if approval_resolution['rule'] else 'approval rule'
        logger.info(f'Payroll run queued for approval id={payroll_run.id} school={school_id} session={session_id} user={request.user.id}')
        return SuccessResponse(f'Payroll run submitted for approval based on rule: {rule_name}.').to_json_response()

    try:
        post_payroll_run(payroll_run_obj=payroll_run, school_id=school_id, session_id=session_id, user_obj=request.user)
    except ValidationError as exc:
        return ErrorResponse('; '.join(exc.messages) or 'Unable to post payroll run.').to_json_response()

    logger.info(f'Payroll run posted id={payroll_run.id} school={school_id} session={session_id} user={request.user.id}')
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
        logger.warning(f'Payroll line payment requested with incomplete context line={line_id} user={request.user.id}')
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
        logger.info(f'Payroll line payment queued line={payroll_line.id} school={school_id} session={session_id} user={request.user.id}')
        return SuccessResponse(f'Salary payment saved and submitted for approval based on rule: {rule_name}.').to_json_response()

    logger.info(f'Payroll line paid line={payroll_line.id} school={school_id} session={session_id} user={request.user.id}')
    return SuccessResponse('Salary payment recorded successfully.').to_json_response()


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
    instance.lastEditedBy = _user_label(request.user)
    instance.updatedByUserID = request.user
    instance.save(update_fields=['status', 'lastEditedBy', 'updatedByUserID', 'lastUpdatedOn'])
    if requested_status == 'posted':
        try:
            post_payroll_run(payroll_run_obj=instance, school_id=school_id, session_id=session_id, user_obj=request.user)
        except ValidationError as exc:
            return ErrorResponse('; '.join(exc.messages) or 'Unable to post payroll run after approval.').to_json_response()
    logger.info(f'Payroll run approved id={instance.id} status={instance.status} school={school_id} session={session_id} user={request.user.id}')
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
        approve_payroll_payment(payroll_line_obj=payroll_line, school_id=school_id, session_id=session_id, user_obj=request.user)
    except ValidationError as exc:
        return ErrorResponse('; '.join(exc.messages) or 'Unable to approve salary payment.').to_json_response()
    logger.info(f'Payroll payment approved line={payroll_line.id} school={school_id} session={session_id} user={request.user.id}')
    return SuccessResponse('Salary payment approved successfully.').to_json_response()
