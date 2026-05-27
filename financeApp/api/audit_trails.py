from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.utils.html import escape
from django_datatables_view.base_datatable_view import BaseDatatableView

from financeApp.models import FinanceTransaction
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


def _filtered_transaction_queryset(*, request, school_id, session_id):
    qs = FinanceTransaction.objects.prefetch_related('entries__accountID', 'entries__partyID').filter(
        schoolID_id=school_id,
        sessionID_id=session_id,
        isDeleted=False,
    ).order_by('-txnDate', '-id')
    date_from = _parse_filter_date(request.GET.get('dateFrom'))
    date_to = _parse_filter_date(request.GET.get('dateTo'))
    txn_type = (request.GET.get('txnType') or '').strip()
    status_value = (request.GET.get('status') or '').strip()
    source_module = (request.GET.get('sourceModule') or '').strip()
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


@login_required
@check_groups('Admin', 'Owner')
def get_finance_audit_trail_api(request):
    school_id = _current_school_id(request)
    session_id = _current_session_id(request)
    if not school_id or not session_id:
        logger.warning(f'Finance audit trail requested without school/session user={request.user.id}')
        return SuccessResponse('Finance audit trail loaded successfully.', data={'summary': {}, 'rows': []}).to_json_response()

    try:
        txn_qs = _filtered_transaction_queryset(request=request, school_id=school_id, session_id=session_id)
        search = (request.GET.get('search') or '').strip()
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

        total_count = txn_qs.count()
        data = {
            'summary': {
                'totalTransactions': total_count,
                'postedTransactions': txn_qs.filter(status='posted').count(),
                'reversedTransactions': txn_qs.filter(status='reversed').count(),
            },
            'rows': rows,
        }
        logger.info(f'Finance audit trail loaded total={total_count} returned={len(rows)} school={school_id} session={session_id} user={request.user.id}')
        return SuccessResponse('Finance audit trail loaded successfully.', data=data).to_json_response()
    except Exception as exc:
        logger.exception(f'Unable to load finance audit trail school={school_id} session={session_id} user={request.user.id}: {exc}')
        return ErrorResponse('Unable to load finance audit trail.', status_code=500).to_json_response()


class FinanceAuditTrailListJson(BaseDatatableView):
    order_columns = ['txnDate', 'txnNo', 'txnType', 'referenceNo', 'description', 'sourceModule', 'lastEditedBy', 'lastUpdatedOn', 'status']

    def get_initial_queryset(self):
        school_id = _current_school_id(self.request)
        session_id = _current_session_id(self.request)
        if not school_id or not session_id:
            logger.warning(f'Finance audit trail datatable requested without school/session user={self.request.user.id}')
            return FinanceTransaction.objects.none()
        return _filtered_transaction_queryset(request=self.request, school_id=school_id, session_id=session_id)

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
        logger.info(f'Finance audit trail datatable prepared rows={len(json_data)} user={self.request.user.id}')
        return json_data
