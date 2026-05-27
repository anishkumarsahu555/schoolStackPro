from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.html import escape
from django.views.decorators.csrf import csrf_exempt
from django_datatables_view.base_datatable_view import BaseDatatableView

from financeApp.services import clear_payment_receipt, sync_payment_receipt, sync_student_charge
from homeApp.models import SchoolSession
from homeApp.session_utils import get_session_month_sequence
from managementApp.models import Student, StudentFee
from managementApp.signals import pre_save_with_user
from utils.custom_decorators import check_groups
from utils.custom_response import ErrorResponse, SuccessResponse
from utils.get_school_detail import get_school_id
from utils.image_utils import avatar_image_html
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


def _sync_student_fee_finance(request, fee_obj):
    session_id = fee_obj.sessionID_id or _current_session_id(request)
    school_id = fee_obj.schoolID_id or _current_school_id(request)
    if not session_id or not school_id or not fee_obj.studentID_id:
        logger.warning(f'Legacy student fee finance sync skipped fee={fee_obj.id} school={school_id} session={session_id}')
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
            received_from_name=(
                fee_obj.studentID.parentID.fatherName
                if fee_obj.studentID.parentID and fee_obj.studentID.parentID.fatherName
                else fee_obj.studentID.name or ''
            ),
            user_obj=request.user,
        )
    else:
        clear_payment_receipt(
            school_id=school_id,
            source_module='legacy_student_fee_receipt',
            source_record_id=str(fee_obj.id),
            user_obj=request.user,
        )


class FeeByStudentJson(BaseDatatableView):
    order_columns = ['month', 'isPaid', 'payDate', 'amount', 'note', 'lastEditedBy', 'lastUpdatedOn']

    @transaction.atomic
    def get_initial_queryset(self):
        try:
            student_id = int(self.request.GET.get('student'))
            session_id = _current_session_id(self.request)
            school_id = _current_school_id(self.request)
            try:
                standard_id = int(self.request.GET.get('standard'))
            except (TypeError, ValueError):
                student_obj = Student.objects.filter(id=student_id, isDeleted=False, sessionID_id=session_id).only('id', 'standardID').first()
                standard_id = student_obj.standardID_id if student_obj else None
            if not session_id or not standard_id:
                return StudentFee.objects.none()

            for month_name, year_value, month_no, period_start, period_end in _session_month_rows(session_id):
                fee_obj = StudentFee.objects.filter(
                    studentID_id=student_id,
                    month__iexact=month_name,
                    standardID_id=standard_id,
                    isDeleted=False,
                    sessionID_id=session_id,
                ).order_by('id').first()
                if not fee_obj:
                    fee_obj = StudentFee(
                        schoolID_id=school_id,
                        sessionID_id=session_id,
                        studentID_id=student_id,
                        standardID_id=standard_id,
                        month=month_name,
                        feeMonth=month_no,
                        feeYear=year_value,
                        periodStartDate=period_start,
                        periodEndDate=period_end,
                        dueDate=period_start,
                    )
                    pre_save_with_user.send(sender=StudentFee, instance=fee_obj, user=self.request.user.pk)
                    fee_obj.save()
                else:
                    update_fields = []
                    for field, value in (
                        ('schoolID_id', school_id),
                        ('sessionID_id', session_id),
                        ('feeMonth', month_no),
                        ('feeYear', year_value),
                        ('periodStartDate', period_start),
                        ('periodEndDate', period_end),
                        ('dueDate', period_start),
                    ):
                        if not getattr(fee_obj, field):
                            setattr(fee_obj, field, value)
                            update_fields.append(field.replace('_id', ''))
                    if update_fields:
                        fee_obj.save(update_fields=update_fields + ['lastUpdatedOn'])

            fee_qs = StudentFee.objects.filter(
                studentID_id=student_id,
                standardID_id=standard_id,
                isDeleted=False,
                sessionID_id=session_id,
            )
            logger.info(f'Legacy fee rows loaded student={student_id} standard={standard_id} session={session_id} user={self.request.user.id}')
            return _restrict_fee_queryset_to_session_months(fee_qs, session_id).order_by('feeYear', 'feeMonth', 'id')
        except Exception as exc:
            logger.exception(f'Unable to load legacy fee rows user={self.request.user.id}: {exc}')
            return StudentFee.objects.none()

    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(month__icontains=search)
                | Q(amount__icontains=search)
                | Q(payDate__icontains=search)
                | Q(isPaid__icontains=search)
                | Q(note__icontains=search)
                | Q(lastEditedBy__icontains=search)
                | Q(lastUpdatedOn__icontains=search)
            )
        return qs

    def prepare_results(self, qs):
        json_data = []
        for item in qs:
            checked_attr = ' checked' if item.isPaid else ''
            is_paid = (
                f'<div class="ui checkbox"><input type="checkbox" name="isPresent{item.pk}" '
                f'id="isPresent{item.pk}"{checked_attr}><label>Mark as Paid</label></div>'
            )
            reason = (
                f'<div class="ui tiny input fluid"><input type="text" placeholder="Remark" '
                f'name="reason{item.pk}" id="reason{item.pk}" value="{escape(item.note or "")}"></div>'
            )
            amount = (
                f'<div class="ui tiny input fluid"><input type="number" placeholder="Amount" '
                f'name="amount{item.pk}" id="amount{item.pk}" value="{escape(item.amount)}"></div>'
            )
            pay_date_value = item.payDate.strftime('%d/%m/%Y') if item.payDate else ''
            pay_date = (
                f'<div class="ui calendar fee-pay-date-calendar" id="payDateCal{item.pk}">'
                f'<div class="ui tiny input fluid left icon"><i class="calendar alternate outline icon"></i>'
                f'<input type="text" placeholder="Paid Date" name="payDate{item.pk}" '
                f'id="payDate{item.pk}" value="{escape(pay_date_value)}"></div></div>'
            )
            action = f'<button class="ui mini primary button" onclick="pushFee({item.pk})">Save</button>'
            json_data.append([
                escape(_fee_month_label(item)),
                is_paid,
                pay_date,
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
    if request.method != 'POST':
        return ErrorResponse('Method not allowed.', status_code=405).to_json_response()
    fee_id = request.POST.get('id')
    try:
        instance = StudentFee.objects.select_related('studentID__parentID', 'standardID').get(pk=int(fee_id), isDeleted=False)
        is_paid = request.POST.get('isPresent') == 'true'
        instance.isPaid = is_paid
        instance.note = request.POST.get('reason') or ''
        instance.amount = float(request.POST.get('amount') or 0)
        instance.schoolID_id = instance.schoolID_id or _current_school_id(request)
        instance.sessionID_id = instance.sessionID_id or _current_session_id(request)
        if is_paid:
            parsed_pay_date = None
            for date_format in ('%Y-%m-%d', '%d-%m-%Y', '%d/%m/%Y'):
                try:
                    parsed_pay_date = datetime.strptime(request.POST.get('payDate') or '', date_format).date()
                    break
                except (TypeError, ValueError):
                    pass
            instance.payDate = parsed_pay_date or timezone.now().date()
        else:
            instance.payDate = None
        pre_save_with_user.send(sender=StudentFee, instance=instance, user=request.user.pk)
        instance.save()
        _sync_student_fee_finance(request, instance)
        logger.info(f'Legacy student fee saved fee={instance.id} paid={instance.isPaid} user={request.user.id}')
        return SuccessResponse('Student fee added successfully.', extra={'color': 'success'}).to_json_response()
    except Exception as exc:
        logger.exception(f'Unable to save legacy student fee id={fee_id} user={request.user.id}: {exc}')
        return ErrorResponse('Unable to save student fee.', extra={'color': 'error'}).to_json_response()


class StudentFeeDetailsByClassJson(BaseDatatableView):
    order_columns = ['photo', 'name', 'roll']

    @transaction.atomic
    def get_initial_queryset(self):
        try:
            standard_id = int(self.request.GET.get('standard'))
            return Student.objects.filter(
                isDeleted=False,
                sessionID_id=_current_session_id(self.request),
                standardID_id=standard_id,
            ).order_by('standardID__name', 'standardID__section', 'roll', 'name')
        except Exception:
            return Student.objects.none()

    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(name__icontains=search)
                | Q(roll__icontains=search)
                | Q(standardID__name__icontains=search)
                | Q(lastEditedBy__icontains=search)
                | Q(lastUpdatedOn__icontains=search)
            )
        return qs

    def prepare_results(self, qs):
        session_id = _current_session_id(self.request)
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
            month_status = [
                'Paid' if (year_value, month_no) in paid_year_month or month_name.lower() in paid_month_names else 'Due'
                for month_name, year_value, month_no, _, _ in session_month_rows
            ]
            roll_raw = '' if item.roll is None else str(item.roll).strip()
            if roll_raw == '':
                roll_value = 'N/A'
            else:
                try:
                    parsed_roll = float(roll_raw)
                    roll_value = int(parsed_roll) if parsed_roll.is_integer() else parsed_roll
                except (TypeError, ValueError):
                    roll_value = escape(roll_raw)
            json_data.append([avatar_image_html(item.photo), escape(item.name), roll_value, *month_status])
        return json_data


class StudentFeeDetailsByStudentJson(BaseDatatableView):
    order_columns = ['month', 'isPaid', 'payDate', 'amount', 'note', 'lastEditedBy', 'lastUpdatedOn']

    @transaction.atomic
    def get_initial_queryset(self):
        try:
            student_id = int(self.request.GET.get('student'))
            session_id = _current_session_id(self.request)
            try:
                standard_id = int(self.request.GET.get('standardByStudent'))
            except (TypeError, ValueError):
                student_obj = Student.objects.filter(id=student_id, isDeleted=False, sessionID_id=session_id).only('id', 'standardID').first()
                standard_id = student_obj.standardID_id if student_obj else None
            if not standard_id:
                return StudentFee.objects.none()
            fee_qs = StudentFee.objects.filter(
                isDeleted=False,
                studentID_id=student_id,
                standardID_id=standard_id,
                sessionID_id=session_id,
            )
            return _restrict_fee_queryset_to_session_months(fee_qs, session_id).order_by('feeYear', 'feeMonth', 'id')
        except Exception:
            return StudentFee.objects.none()

    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(month__icontains=search)
                | Q(note__icontains=search)
                | Q(amount__icontains=search)
                | Q(lastEditedBy__icontains=search)
                | Q(lastUpdatedOn__icontains=search)
            )
        return qs

    def prepare_results(self, qs):
        json_data = []
        for item in qs:
            if item.isPaid:
                status = 'Paid'
                pay_date = item.payDate.strftime('%d-%m-%Y') if item.payDate else 'N/A'
            else:
                status = 'Due'
                pay_date = 'N/A'
            json_data.append([
                escape(_fee_month_label(item)),
                status,
                pay_date,
                escape(item.amount),
                escape(item.note or ''),
                escape(item.lastEditedBy or 'N/A'),
                escape(item.lastUpdatedOn.strftime('%d-%m-%Y %I:%M %p') if item.lastUpdatedOn else 'N/A'),
            ])
        return json_data
