from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.db.models import Q, Sum
from django.utils.html import escape
from django_datatables_view.base_datatable_view import BaseDatatableView

from financeApp.models import StudentCharge
from financeApp.services import bootstrap_school_finance
from homeApp.models import SchoolSession
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


def _decimal_or_zero(value):
    try:
        return Decimal(str(value or '0')).quantize(Decimal('0.01'))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal('0.00')


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


def _finance_status_pill(status_value):
    status = (status_value or 'draft').strip().lower().replace(' ', '_')
    label = status.replace('_', ' ')
    return f'<span class="finance-status-pill {escape(status)}">{escape(label)}</span>'


def _class_label_from_charge(charge_obj):
    standard_obj = charge_obj.standardID or (
        charge_obj.studentID.standardID
        if charge_obj.studentID_id and charge_obj.studentID.standardID_id
        else None
    )
    if not standard_obj:
        return 'N/A'
    label = standard_obj.name or 'N/A'
    if standard_obj.section:
        label = f'{label} - {standard_obj.section}'
    return label


def _charge_queryset(*, request, school_id, session_id):
    qs = StudentCharge.objects.select_related(
        'studentID',
        'studentID__standardID',
        'standardID',
        'feeHeadID',
    ).filter(
        schoolID_id=school_id,
        sessionID_id=session_id,
        isDeleted=False,
    ).order_by('-chargeDate', '-datetime', '-id')

    standard_id = request.GET.get('standard')
    student_id = request.GET.get('student')
    status_value = (request.GET.get('status') or '').strip()
    date_from_value = _parse_filter_date(request.GET.get('dateFrom'))
    date_to_value = _parse_filter_date(request.GET.get('dateTo'))
    if standard_id:
        qs = qs.filter(standardID_id=standard_id)
    if student_id:
        qs = qs.filter(studentID_id=student_id)
    if status_value:
        qs = qs.filter(status=status_value)
    if date_from_value:
        qs = qs.filter(chargeDate__gte=date_from_value)
    if date_to_value:
        qs = qs.filter(chargeDate__lte=date_to_value)
    return qs


@login_required
@check_groups('Admin', 'Owner')
def get_student_charge_list_api(request):
    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    if not school_id or not session_id:
        logger.warning(f'Student charge list requested without school/session user={request.user.id}')
        return SuccessResponse('Student charges loaded successfully.', data={
            'summary': {'totalCharges': 0, 'totalNetAmount': 0, 'totalPaidAmount': 0, 'totalBalanceAmount': 0},
            'rows': [],
        }).to_json_response()

    try:
        bootstrap_school_finance(school_id=school_id, session_id=session_id, user_obj=request.user)
        charge_qs = _charge_queryset(request=request, school_id=school_id, session_id=session_id)
        totals = charge_qs.aggregate(
            total_net=Sum('netAmount'),
            total_paid=Sum('paidAmount'),
            total_balance=Sum('balanceAmount'),
        )

        rows = []
        for row in charge_qs:
            rows.append({
                'id': row.id,
                'chargeDate': row.chargeDate.strftime('%d-%m-%Y') if row.chargeDate else 'N/A',
                'dueDate': row.dueDate.strftime('%d-%m-%Y') if row.dueDate else '-',
                'studentName': row.studentID.name if row.studentID_id and row.studentID.name else '',
                'className': _class_label_from_charge(row),
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
        logger.info(f'Student charge list loaded count={len(rows)} school={school_id} session={session_id} user={request.user.id}')
        return SuccessResponse('Student charges loaded successfully.', data={'summary': summary, 'rows': rows}).to_json_response()
    except Exception as exc:
        logger.exception(f'Unable to load student charges school={school_id} session={session_id} user={request.user.id}: {exc}')
        return ErrorResponse('Unable to load student charges.', status_code=500).to_json_response()


class FinanceStudentChargeListJson(BaseDatatableView):
    order_columns = ['chargeDate', 'dueDate', 'studentID__name', 'standardID__name', 'feeHeadID__name',
                     'referenceNo', 'description', 'netAmount', 'paidAmount', 'balanceAmount', 'status']

    def get_initial_queryset(self):
        school_id = _current_school_id(self.request)
        session_id = _current_session_id(self.request)
        if not school_id or not session_id:
            logger.warning(f'Student charge datatable requested without school/session user={self.request.user.id}')
            return StudentCharge.objects.none()
        bootstrap_school_finance(school_id=school_id, session_id=session_id, user_obj=self.request.user)
        return _charge_queryset(request=self.request, school_id=school_id, session_id=session_id)

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
            json_data.append([
                escape(item.chargeDate.strftime('%d-%m-%Y') if item.chargeDate else 'N/A'),
                escape(item.dueDate.strftime('%d-%m-%Y') if item.dueDate else '-'),
                escape(item.studentID.name if item.studentID_id and item.studentID.name else '-'),
                escape(_class_label_from_charge(item)),
                escape(item.feeHeadID.name if item.feeHeadID_id else 'N/A'),
                escape(item.referenceNo or '-'),
                escape(item.description or item.title or '-'),
                escape(f'Rs {float(_decimal_or_zero(item.netAmount)):.2f}'),
                escape(f'Rs {float(_decimal_or_zero(item.paidAmount)):.2f}'),
                escape(f'Rs {float(_decimal_or_zero(item.balanceAmount)):.2f}'),
                _finance_status_pill(item.status),
            ])
        logger.info(f'Student charge datatable prepared rows={len(json_data)} user={self.request.user.id}')
        return json_data
