import csv
import json
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.http import HttpResponse, JsonResponse
from django.urls import reverse
from django.utils.dateparse import parse_date
from django.utils.html import escape
from django.views.decorators.csrf import csrf_exempt
from django_datatables_view.base_datatable_view import BaseDatatableView

from libraryApp.models import (
    LibraryAuthor,
    LibraryBook,
    LibraryBookAuthor,
    LibraryBookCopy,
    LibraryCategory,
    LibraryFine,
    LibraryIssue,
    LibraryMember,
    LibraryMemberCardDesign,
    LibraryPublisher,
    LibraryReservation,
    LibrarySetting,
)
from managementApp.models import Student, TeacherDetail
from libraryApp.services import (
    DEFAULT_LIBRARY_CARD_FIELDS_CONFIG,
    DEFAULT_LIBRARY_CARD_FOOTER_CONFIG,
    DEFAULT_LIBRARY_CARD_HEADER_CONFIG,
    DEFAULT_LIBRARY_CARD_STYLE_CONFIG,
    get_or_create_active_member_card_design,
    merged_config,
    normalize_library_card_fields,
)
from utils.custom_response import ErrorResponse, SuccessResponse
from utils.logger import logger


def _current_session(request):
    return request.session.get('current_session', {}) or {}


def _school_id(request):
    return _current_session(request).get('SchoolID')


def _session_id(request):
    return _current_session(request).get('Id')


def _clean(value):
    if value is None:
        return None
    value = str(value).strip()
    if value.lower() in {'', 'undefined', 'null', 'none'}:
        return None
    return value


def _decimal(value):
    try:
        return Decimal(str(value or '0')).quantize(Decimal('0.01'))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal('0.00')


def _int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _date(value):
    value = _clean(value)
    return parse_date(value) if value else None


def _bool(value):
    return str(value).strip().lower() in {'1', 'true', 'yes', 'on', 'active'}


def _truthy_post_value(value):
    return str(value).strip().lower() in {'1', 'true', 'yes', 'on'}


def _clean_color(value, fallback):
    value = (value or '').strip()
    if len(value) == 7 and value.startswith('#'):
        try:
            int(value[1:], 16)
            return value
        except ValueError:
            return fallback
    return fallback


def _clean_number(value, fallback, minimum, maximum):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    return max(minimum, min(maximum, number))


def _user_label(request):
    return request.user.get_full_name() or request.user.username or str(request.user.id)


def _audit(request, obj):
    obj.schoolID_id = _school_id(request)
    obj.sessionID_id = _session_id(request)
    obj.updatedByUserID = request.user
    obj.lastEditedBy = _user_label(request)


def _scope(request, model):
    return model.objects.filter(schoolID_id=_school_id(request), sessionID_id=_session_id(request), isDeleted=False)


def _setting(request):
    setting, _ = LibrarySetting.objects.get_or_create(
        schoolID_id=_school_id(request),
        sessionID_id=_session_id(request),
        isDeleted=False,
        defaults={'lastEditedBy': _user_label(request), 'updatedByUserID': request.user},
    )
    return setting


def _validation_message(exc, fallback):
    if isinstance(exc, ValidationError):
        if hasattr(exc, 'message_dict'):
            return '; '.join(f'{field}: {", ".join(messages)}' for field, messages in exc.message_dict.items())
        if hasattr(exc, 'messages'):
            return '; '.join(str(message) for message in exc.messages)
    return str(exc) or fallback


def _json_value(value):
    if isinstance(value, Decimal):
        return str(value)
    if hasattr(value, 'isoformat'):
        return value.isoformat()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _status_pill(label, color='grey'):
    return f'<span class="ui {color} tiny label">{escape(label)}</span>'


def _issue_overdue_days(issue):
    if issue.status == 'issued' and issue.dueDate and issue.dueDate < date.today():
        return (date.today() - issue.dueDate).days
    return 0


def _issue_due_date_cell(issue):
    due_date = escape(issue.dueDate.strftime('%d-%m-%Y'))
    overdue_days = _issue_overdue_days(issue)
    if not overdue_days:
        return due_date
    return f'<span class="ui red tiny label">{due_date}</span>'


def _issue_status_cell(issue):
    if issue.status == 'issued':
        overdue_days = _issue_overdue_days(issue)
        if overdue_days:
            day_label = 'day' if overdue_days == 1 else 'days'
            return _status_pill('Issued', 'orange') + f' <span class="ui red tiny label">Overdue {overdue_days} {day_label}</span>'
    return _status_pill(issue.status.title(), 'orange' if issue.status == 'issued' else 'green')


def _active_pill(active):
    return _status_pill('Active' if active else 'Inactive', 'green' if active else 'grey')


def _copy_status_pill(status):
    colors = {
        'available': 'green',
        'issued': 'orange',
        'reserved': 'teal',
        'lost': 'red',
        'damaged': 'yellow',
        'withdrawn': 'grey',
    }
    return _status_pill(status.title(), colors.get(status, 'grey'))


def _fine_status_pill(status):
    colors = {'pending': 'orange', 'paid': 'green', 'waived': 'blue', 'cancelled': 'grey'}
    return _status_pill(status.title(), colors.get(status, 'grey'))


def _dt_actions(edit_fn, delete_fn, obj_id):
    return (
        f'<button data-inverted="" data-tooltip="Edit Detail" data-position="left center" data-variation="mini" '
        f'style="font-size:10px;" onclick="{edit_fn}({obj_id})" class="ui circular facebook icon button green">'
        f'<i class="pen icon"></i></button>'
        f'<button data-inverted="" data-tooltip="Delete" data-position="left center" data-variation="mini" '
        f'style="font-size:10px; margin-left:3px;" onclick="{delete_fn}({obj_id})" class="ui circular youtube icon button">'
        f'<i class="trash alternate icon"></i></button>'
    )


def _history_button(history_fn, obj_id, label='Issue History'):
    return (
        f'<button data-inverted="" data-tooltip="{escape(label)}" data-position="left center" data-variation="mini" '
        f'style="font-size:10px; margin-left:3px;" onclick="{history_fn}({obj_id})" class="ui circular teal icon button">'
        f'<i class="history icon"></i></button>'
    )


def _book_actions(obj_id):
    return _dt_actions('editBook', 'confirmDeleteBook', obj_id) + _history_button('viewBookHistory', obj_id)


def _member_actions(obj_id):
    card_url = reverse('libraryApp:member_cards') + f'?member={obj_id}'
    card_button = (
        f'<a data-inverted="" data-tooltip="View Member Card" data-position="left center" data-variation="mini" '
        f'style="font-size:10px; margin-left:3px;" href="{card_url}" target="_blank" class="ui circular blue icon button">'
        f'<i class="id card outline icon"></i></a>'
    )
    return _dt_actions('editMember', 'confirmDeleteMember', obj_id) + _history_button('viewMemberHistory', obj_id) + card_button


def _copy_actions(obj_id):
    return _dt_actions('editCopy', 'confirmDeleteCopy', obj_id) + _history_button('viewCopyHistory', obj_id)


def _reservation_actions(obj):
    actions = [_dt_actions('editReservation', 'confirmDeleteReservation', obj.id)]
    if obj.status == 'pending':
        actions.append(
            f'<button data-inverted="" data-tooltip="Issue Reserved Book" data-position="left center" data-variation="mini" '
            f'style="font-size:10px; margin-left:3px;" onclick="issueReservation({obj.id})" class="ui circular green icon button">'
            f'<i class="share square icon"></i></button>'
        )
    return ''.join(actions)


def _fine_actions(obj):
    actions = [_dt_actions('editFine', 'confirmDeleteFine', obj.id)]
    if obj.status == 'pending':
        actions.append(
            f'<button data-inverted="" data-tooltip="Pay Fine" data-position="left center" data-variation="mini" '
            f'style="font-size:10px; margin-left:3px;" onclick="showPayFineModal({obj.id})" class="ui circular green icon button">'
            f'<i class="money bill alternate icon"></i></button>'
        )
        actions.append(
            f'<button data-inverted="" data-tooltip="Waive Fine" data-position="left center" data-variation="mini" '
            f'style="font-size:10px; margin-left:3px;" onclick="waiveFine({obj.id})" class="ui circular blue icon button">'
            f'<i class="handshake icon"></i></button>'
        )
    return ''.join(actions)


def _issue_actions(obj):
    actions = []
    actions.append(f'<button class="ui circular blue icon button" data-tooltip="Issue Detail" onclick="showIssueDetailModal({obj.id})"><i class="info circle icon"></i></button>')
    if obj.status == 'issued':
        actions.append(f'<button class="ui circular green icon button" data-tooltip="Return" onclick="showReturnModal({obj.id})"><i class="undo icon"></i></button>')
        actions.append(f'<button class="ui circular teal icon button" data-tooltip="Renew" onclick="showRenewModal({obj.id})"><i class="redo icon"></i></button>')
    return ''.join(actions) or 'N/A'


def _member_name(member):
    return member.display_name or 'N/A'


def _open_fine_balance(member):
    total = member.fines.filter(isDeleted=False, status='pending').aggregate(total=Sum('amount'), paid=Sum('paidAmount'))
    return (total.get('total') or Decimal('0.00')) - (total.get('paid') or Decimal('0.00'))


def _calculate_overdue_fine(request, due_date, return_date):
    setting = _setting(request)
    overdue_days = max((return_date - due_date).days - setting.graceDays, 0)
    return overdue_days, (Decimal(overdue_days) * setting.finePerDay).quantize(Decimal('0.01'))


def _append_note(existing, label, note):
    note = _clean(note)
    if not note:
        return existing
    entry = f'{label}: {note}'
    return f'{existing}\n{entry}' if existing else entry


def _issue_eligibility_error(member):
    if member.issues.filter(isDeleted=False, status='issued').count() >= member.maxBooksAllowed:
        return 'Member has reached the maximum allowed issued books.'
    if member.fineLimit and _open_fine_balance(member) > member.fineLimit:
        return 'Member has pending fines above the allowed limit.'
    return None


def _create_issue_for_copy(request, *, member, copy, issue_date=None, due_date=None, notes=None):
    if copy.status != 'available':
        raise ValidationError('Selected copy is not available.')
    eligibility_error = _issue_eligibility_error(member)
    if eligibility_error:
        raise ValidationError(eligibility_error)
    setting = _setting(request)
    issue_date = issue_date or date.today()
    due_date = due_date or issue_date + timedelta(days=setting.defaultIssueDays)
    issue = LibraryIssue(member=member, copy=copy, issueDate=issue_date, dueDate=due_date, notes=notes, status='issued')
    _audit(request, issue)
    issue.full_clean()
    issue.save()
    copy.status = 'issued'
    copy.condition = 'good' if copy.condition == 'lost' else copy.condition
    _audit(request, copy)
    copy.save(update_fields=['status', 'condition', 'schoolID', 'sessionID', 'updatedByUserID', 'lastEditedBy', 'lastUpdatedOn'])
    LibraryReservation.objects.filter(book=copy.book, member=member, status='pending', isDeleted=False).update(status='fulfilled')
    return issue


def _choice_chart(qs, field, choices):
    counts = {row[field]: row['total'] for row in qs.values(field).annotate(total=Count('id'))}
    return {
        'labels': [label for value, label in choices],
        'data': [counts.get(value, 0) for value, label in choices],
    }


@login_required
def dashboard_summary(request):
    try:
        copies = _scope(request, LibraryBookCopy)
        issues = _scope(request, LibraryIssue)
        fines = _scope(request, LibraryFine)
        reservations = _scope(request, LibraryReservation)
        today = date.today()
        pending_fine = fines.filter(status='pending').aggregate(total=Sum('amount'), paid=Sum('paidAmount'))
        pending_fine_amount = (pending_fine.get('total') or Decimal('0.00')) - (pending_fine.get('paid') or Decimal('0.00'))
        paid_fine_amount = fines.filter(status='paid').aggregate(total=Sum('paidAmount')).get('total') or Decimal('0.00')
        waived_fine_amount = fines.filter(status='waived').aggregate(total=Sum('amount')).get('total') or Decimal('0.00')
        issue_trend_days = [today - timedelta(days=offset) for offset in range(6, -1, -1)]
        issue_trend_counts = {
            row['issueDate']: row['total']
            for row in issues.filter(issueDate__gte=issue_trend_days[0], issueDate__lte=today).values('issueDate').annotate(total=Count('id'))
        }
        data = {
            'books': _scope(request, LibraryBook).count(),
            'copies': copies.count(),
            'availableCopies': copies.filter(status='available').count(),
            'issuedCopies': copies.filter(status='issued').count(),
            'overdueIssues': issues.filter(status='issued', dueDate__lt=today).count(),
            'pendingReservations': reservations.filter(status='pending').count(),
            'lostDamagedCopies': copies.filter(status__in=['lost', 'damaged']).count(),
            'pendingFineAmount': str(pending_fine_amount),
            'charts': {
                'copyStatus': _choice_chart(copies, 'status', LibraryBookCopy.STATUS_CHOICES),
                'issueTrend': {
                    'labels': [day.strftime('%d %b') for day in issue_trend_days],
                    'data': [issue_trend_counts.get(day, 0) for day in issue_trend_days],
                },
                'reservationStatus': _choice_chart(reservations, 'status', LibraryReservation.STATUS_CHOICES),
                'fineAmounts': {
                    'labels': ['Pending', 'Paid', 'Waived'],
                    'data': [float(pending_fine_amount), float(paid_fine_amount), float(waived_fine_amount)],
                },
            },
            'recentIssues': [
                {
                    'book': issue.copy.book.title,
                    'copy': issue.copy.accessionNumber,
                    'member': _member_name(issue.member),
                    'dueDate': issue.dueDate.strftime('%d-%m-%Y'),
                    'status': issue.status,
                }
                for issue in issues.select_related('copy__book', 'member', 'member__student', 'member__staff').order_by('-id')[:8]
            ],
        }
        logger.info(f'Library dashboard summary fetched school={_school_id(request)} session={_session_id(request)}')
        return JsonResponse({'status': 'success', 'data': data})
    except Exception as exc:
        logger.exception(f'Error loading library dashboard summary: {exc}')
        return ErrorResponse('Unable to load library dashboard summary.').to_json_response()


class LibraryCategoryListJson(BaseDatatableView):
    order_columns = ['name', 'code', 'parent__name', 'isActive', 'lastEditedBy', 'lastUpdatedOn']

    def get_initial_queryset(self):
        return _scope(self.request, LibraryCategory).select_related('parent')

    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]')
        if search:
            qs = qs.filter(Q(name__icontains=search) | Q(code__icontains=search) | Q(parent__name__icontains=search))
        return qs

    def prepare_results(self, qs):
        return [[
            escape(item.name),
            escape(item.code or 'N/A'),
            escape(item.parent.name if item.parent_id else 'N/A'),
            _active_pill(item.isActive),
            escape(item.lastEditedBy or 'N/A'),
            escape(item.lastUpdatedOn.strftime('%d-%m-%Y %I:%M %p') if item.lastUpdatedOn else 'N/A'),
            _dt_actions('editCategory', 'confirmDeleteCategory', item.id),
        ] for item in qs]


class LibraryAuthorListJson(BaseDatatableView):
    order_columns = ['name', 'country', 'isActive', 'lastEditedBy', 'lastUpdatedOn']

    def get_initial_queryset(self):
        return _scope(self.request, LibraryAuthor)

    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]')
        if search:
            qs = qs.filter(Q(name__icontains=search) | Q(country__icontains=search))
        return qs

    def prepare_results(self, qs):
        return [[escape(i.name), escape(i.country or 'N/A'), _active_pill(i.isActive), escape(i.lastEditedBy or 'N/A'), escape(i.lastUpdatedOn.strftime('%d-%m-%Y %I:%M %p') if i.lastUpdatedOn else 'N/A'), _dt_actions('editAuthor', 'confirmDeleteAuthor', i.id)] for i in qs]


class LibraryPublisherListJson(BaseDatatableView):
    order_columns = ['name', 'phoneNumber', 'email', 'isActive', 'lastEditedBy', 'lastUpdatedOn']

    def get_initial_queryset(self):
        return _scope(self.request, LibraryPublisher)

    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]')
        if search:
            qs = qs.filter(Q(name__icontains=search) | Q(phoneNumber__icontains=search) | Q(email__icontains=search))
        return qs

    def prepare_results(self, qs):
        return [[escape(i.name), escape(i.phoneNumber or 'N/A'), escape(i.email or 'N/A'), _active_pill(i.isActive), escape(i.lastEditedBy or 'N/A'), escape(i.lastUpdatedOn.strftime('%d-%m-%Y %I:%M %p') if i.lastUpdatedOn else 'N/A'), _dt_actions('editPublisher', 'confirmDeletePublisher', i.id)] for i in qs]


class LibraryBookListJson(BaseDatatableView):
    order_columns = ['title', 'isbn', 'category__name', 'publisher__name', 'shelfLocation', 'isActive', 'lastUpdatedOn']

    def get_initial_queryset(self):
        return _scope(self.request, LibraryBook).select_related('category', 'publisher').prefetch_related('authors')

    def filter_queryset(self, qs):
        category = self.request.GET.get('category')
        if category and str(category).isdigit():
            qs = qs.filter(category_id=category)
        search = self.request.GET.get('search[value]')
        if search:
            qs = qs.filter(Q(title__icontains=search) | Q(isbn__icontains=search) | Q(category__name__icontains=search) | Q(publisher__name__icontains=search) | Q(authors__name__icontains=search)).distinct()
        return qs

    def prepare_results(self, qs):
        rows = []
        for item in qs:
            rows.append([
                escape(item.title),
                escape(item.isbn or 'N/A'),
                escape(item.category.name if item.category_id else 'N/A'),
                escape(', '.join(a.name for a in item.authors.all()) or 'N/A'),
                escape(item.publisher.name if item.publisher_id else 'N/A'),
                escape(item.shelfLocation or 'N/A'),
                _active_pill(item.isActive),
                _book_actions(item.id),
            ])
        return rows


class LibraryBookCopyListJson(BaseDatatableView):
    order_columns = ['accessionNumber', 'book__title', 'status', 'condition', 'barcodeValue', 'lastUpdatedOn']

    def get_initial_queryset(self):
        return _scope(self.request, LibraryBookCopy).select_related('book')

    def filter_queryset(self, qs):
        status = self.request.GET.get('status')
        if status:
            qs = qs.filter(status=status)
        search = self.request.GET.get('search[value]')
        if search:
            qs = qs.filter(Q(accessionNumber__icontains=search) | Q(book__title__icontains=search) | Q(barcodeValue__icontains=search) | Q(qrCodeValue__icontains=search))
        return qs

    def prepare_results(self, qs):
        return [[escape(i.accessionNumber), escape(i.book.title), _copy_status_pill(i.status), escape(i.condition.title()), escape(i.barcodeValue or i.qrCodeValue or 'N/A'), escape(i.lastUpdatedOn.strftime('%d-%m-%Y %I:%M %p') if i.lastUpdatedOn else 'N/A'), _copy_actions(i.id)] for i in qs]


class LibraryMemberListJson(BaseDatatableView):
    order_columns = ['memberCode', 'memberType', 'student__name', 'maxBooksAllowed', 'maxBooksAllowed', 'isActive']

    def get_initial_queryset(self):
        return _scope(self.request, LibraryMember).select_related('student', 'staff')

    def filter_queryset(self, qs):
        member_type = self.request.GET.get('memberType')
        if member_type:
            qs = qs.filter(memberType=member_type)
        search = self.request.GET.get('search[value]')
        if search:
            qs = qs.filter(Q(memberCode__icontains=search) | Q(student__name__icontains=search) | Q(staff__name__icontains=search))
        return qs

    def prepare_results(self, qs):
        return [[escape(i.memberCode), escape(i.get_memberType_display()), escape(_member_name(i) or 'N/A'), escape(i.maxBooksAllowed), escape(str(_open_fine_balance(i))), _active_pill(i.isActive), _member_actions(i.id)] for i in qs]


class LibraryIssueListJson(BaseDatatableView):
    order_columns = ['copy__book__title', 'copy__accessionNumber', 'member__memberCode', 'member__student__name', 'issueDate', 'dueDate', 'status', 'fineAmount']

    def get_initial_queryset(self):
        return _scope(self.request, LibraryIssue).select_related('copy__book', 'member', 'member__student', 'member__staff')

    def filter_queryset(self, qs):
        status = self.request.GET.get('status')
        if status:
            qs = qs.filter(status=status)
        if self.request.GET.get('overdue') == '1':
            qs = qs.filter(status='issued', dueDate__lt=date.today())
        member = self.request.GET.get('member')
        if member and str(member).isdigit():
            qs = qs.filter(member_id=member)
        book = self.request.GET.get('book')
        if book and str(book).isdigit():
            qs = qs.filter(copy__book_id=book)
        copy = self.request.GET.get('copy')
        if copy and str(copy).isdigit():
            qs = qs.filter(copy_id=copy)
        issue_from = _date(self.request.GET.get('issueFrom'))
        if issue_from:
            qs = qs.filter(issueDate__gte=issue_from)
        issue_to = _date(self.request.GET.get('issueTo'))
        if issue_to:
            qs = qs.filter(issueDate__lte=issue_to)
        due_from = _date(self.request.GET.get('dueFrom'))
        if due_from:
            qs = qs.filter(dueDate__gte=due_from)
        due_to = _date(self.request.GET.get('dueTo'))
        if due_to:
            qs = qs.filter(dueDate__lte=due_to)
        search = self.request.GET.get('search[value]')
        if search:
            qs = qs.filter(Q(copy__book__title__icontains=search) | Q(copy__accessionNumber__icontains=search) | Q(member__memberCode__icontains=search) | Q(member__student__name__icontains=search) | Q(member__staff__name__icontains=search))
        return qs

    def prepare_results(self, qs):
        return [[escape(i.copy.book.title), escape(i.copy.accessionNumber), escape(i.member.memberCode), escape(_member_name(i.member) or 'N/A'), escape(i.issueDate.strftime('%d-%m-%Y')), _issue_due_date_cell(i), _issue_status_cell(i), escape(str(i.fineAmount)), _issue_actions(i)] for i in qs]


class LibraryReservationListJson(BaseDatatableView):
    order_columns = ['book__title', 'member__memberCode', 'member__student__name', 'reservationDate', 'expiryDate', 'status']

    def get_initial_queryset(self):
        return _scope(self.request, LibraryReservation).select_related('book', 'member', 'member__student', 'member__staff')

    def filter_queryset(self, qs):
        status = self.request.GET.get('status')
        if status:
            qs = qs.filter(status=status)
        search = self.request.GET.get('search[value]')
        if search:
            qs = qs.filter(Q(book__title__icontains=search) | Q(member__memberCode__icontains=search) | Q(member__student__name__icontains=search) | Q(member__staff__name__icontains=search))
        return qs

    def prepare_results(self, qs):
        return [[escape(i.book.title), escape(i.member.memberCode), escape(_member_name(i.member) or 'N/A'), escape(i.reservationDate.strftime('%d-%m-%Y')), escape(i.expiryDate.strftime('%d-%m-%Y') if i.expiryDate else 'N/A'), _status_pill(i.status.title(), 'orange' if i.status == 'pending' else 'grey'), _reservation_actions(i)] for i in qs]


class LibraryFineListJson(BaseDatatableView):
    order_columns = ['member__memberCode', 'member__student__name', 'reason', 'amount', 'paidAmount', 'paidAmount', 'paidDate', 'status']

    def get_initial_queryset(self):
        return _scope(self.request, LibraryFine).select_related('member', 'member__student', 'member__staff', 'issue')

    def filter_queryset(self, qs):
        status = self.request.GET.get('status')
        if status:
            qs = qs.filter(status=status)
        search = self.request.GET.get('search[value]')
        if search:
            qs = qs.filter(Q(member__memberCode__icontains=search) | Q(member__student__name__icontains=search) | Q(member__staff__name__icontains=search) | Q(reason__icontains=search))
        return qs

    def prepare_results(self, qs):
        return [[escape(i.member.memberCode), escape(_member_name(i.member) or 'N/A'), escape(i.reason.title()), escape(str(i.amount)), escape(str(i.paidAmount)), escape(str(i.balance)), escape(i.paidDate.strftime('%d-%m-%Y') if i.paidDate else 'N/A'), _fine_status_pill(i.status), _fine_actions(i)] for i in qs]


class LibraryOverdueReportListJson(LibraryIssueListJson):
    def get_initial_queryset(self):
        return super().get_initial_queryset().filter(status='issued', dueDate__lt=date.today())


@login_required
def library_options_api(request):
    try:
        students = Student.objects.filter(schoolID_id=_school_id(request), sessionID_id=_session_id(request), isDeleted=False, isActive='Yes').only('id', 'name', 'registrationCode')[:500]
        staff = TeacherDetail.objects.filter(schoolID_id=_school_id(request), sessionID_id=_session_id(request), isDeleted=False, isActive='Yes').only('id', 'name', 'employeeCode')[:500]
        data = {
            'categories': [{'id': i.id, 'name': i.name} for i in _scope(request, LibraryCategory).filter(isActive=True).order_by('name')],
            'authors': [{'id': i.id, 'name': i.name} for i in _scope(request, LibraryAuthor).filter(isActive=True).order_by('name')],
            'publishers': [{'id': i.id, 'name': i.name} for i in _scope(request, LibraryPublisher).filter(isActive=True).order_by('name')],
            'books': [{'id': i.id, 'name': i.title} for i in _scope(request, LibraryBook).filter(isActive=True).order_by('title')],
            'availableCopies': [{'id': i.id, 'name': f'{i.accessionNumber} - {i.book.title}'} for i in _scope(request, LibraryBookCopy).select_related('book').filter(isActive=True, status='available').order_by('accessionNumber')],
            'members': [{'id': i.id, 'name': f'{i.memberCode} - {_member_name(i)}'} for i in _scope(request, LibraryMember).select_related('student', 'staff').filter(isActive=True).order_by('memberCode')],
            'openIssues': [{'id': i.id, 'name': f'{i.copy.accessionNumber} - {i.copy.book.title} - {i.member.memberCode}'} for i in _scope(request, LibraryIssue).select_related('copy__book', 'member').filter(status='issued').order_by('dueDate')],
            'students': [{'id': i.id, 'name': f'{i.name} ({i.registrationCode or i.id})'} for i in students],
            'staff': [{'id': i.id, 'name': f'{i.name} ({i.employeeCode or i.id})'} for i in staff],
        }
        logger.info('Library options fetched successfully')
        return JsonResponse({'status': 'success', 'data': data})
    except Exception as exc:
        logger.exception(f'Error loading library options: {exc}')
        return ErrorResponse('Unable to load library options.').to_json_response()


@login_required
def available_copies_api(request):
    try:
        book_id = _clean(request.GET.get('book'))
        reservation_id = _clean(request.GET.get('reservation'))
        if reservation_id:
            reservation = _scope(request, LibraryReservation).select_related('book').get(pk=reservation_id)
            book_id = reservation.book_id
        qs = _scope(request, LibraryBookCopy).select_related('book').filter(isActive=True, status='available')
        if book_id:
            qs = qs.filter(book_id=book_id)
        data = [{'id': copy.id, 'name': f'{copy.accessionNumber} - {copy.book.title}'} for copy in qs.order_by('accessionNumber')[:500]]
        logger.info(f'Library available copies fetched book={book_id} count={len(data)}')
        return JsonResponse({'status': 'success', 'data': data})
    except Exception as exc:
        logger.exception(f'Error loading available library copies: {exc}')
        return ErrorResponse('Unable to load available copies.').to_json_response()


@login_required
def member_eligibility_api(request):
    try:
        member = _scope(request, LibraryMember).select_related('student', 'staff').get(pk=request.GET.get('member'), isActive=True)
        open_issues = member.issues.filter(isDeleted=False, status='issued').count()
        open_fine = _open_fine_balance(member)
        fine_limit = member.fineLimit or Decimal('0.00')
        max_books = member.maxBooksAllowed
        blocked_reason = _issue_eligibility_error(member)
        data = {
            'memberCode': member.memberCode,
            'memberName': _member_name(member),
            'openIssues': open_issues,
            'maxBooksAllowed': max_books,
            'openFineBalance': str(open_fine),
            'fineLimit': str(fine_limit),
            'fineLimitLabel': str(fine_limit) if fine_limit > Decimal('0.00') else 'No block',
            'remainingBookSlots': max(max_books - open_issues, 0),
            'canIssue': blocked_reason is None,
            'blockedReason': blocked_reason or '',
        }
        logger.info(f'Library member eligibility fetched member={member.id} canIssue={data["canIssue"]}')
        return JsonResponse({'status': 'success', 'data': data})
    except Exception as exc:
        logger.exception(f'Error loading library member eligibility: {exc}')
        return ErrorResponse('Unable to load member eligibility.').to_json_response()


@login_required
def settings_detail_api(request):
    try:
        setting = _setting(request)
        data = {
            'defaultIssueDays': setting.defaultIssueDays,
            'finePerDay': str(setting.finePerDay),
            'graceDays': setting.graceDays,
            'maxRenewals': setting.maxRenewals,
            'defaultMaxBooksAllowed': setting.defaultMaxBooksAllowed,
            'reservationExpiryDays': setting.reservationExpiryDays,
            'lostBookFine': str(setting.lostBookFine),
            'damagedBookFine': str(setting.damagedBookFine),
        }
        logger.info(f'Library settings fetched id={setting.id}')
        return JsonResponse({'status': 'success', 'data': data})
    except Exception as exc:
        logger.exception(f'Error loading library settings: {exc}')
        return ErrorResponse('Unable to load library settings.').to_json_response()


@csrf_exempt
@transaction.atomic
@login_required
def settings_api(request):
    try:
        setting = _setting(request)
        setting.defaultIssueDays = _int(request.POST.get('defaultIssueDays'), 14)
        setting.finePerDay = _decimal(request.POST.get('finePerDay'))
        setting.graceDays = _int(request.POST.get('graceDays'), 0)
        setting.maxRenewals = _int(request.POST.get('maxRenewals'), 2)
        setting.defaultMaxBooksAllowed = _int(request.POST.get('defaultMaxBooksAllowed'), 2)
        setting.reservationExpiryDays = _int(request.POST.get('reservationExpiryDays'), 3)
        setting.lostBookFine = _decimal(request.POST.get('lostBookFine'))
        setting.damagedBookFine = _decimal(request.POST.get('damagedBookFine'))
        _audit(request, setting)
        setting.full_clean()
        setting.save()
        logger.info(f'Library settings saved id={setting.id}')
        return SuccessResponse('Library settings saved successfully.').to_json_response()
    except Exception as exc:
        logger.exception(f'Error saving library settings: {exc}')
        return ErrorResponse(_validation_message(exc, 'Unable to save library settings.')).to_json_response()


def _get_or_new(request, model):
    obj_id = _clean(request.POST.get('id'))
    if obj_id:
        return _scope(request, model).get(pk=obj_id), False
    return model(), True


@csrf_exempt
@transaction.atomic
@login_required
def category_api(request):
    try:
        obj, created = _get_or_new(request, LibraryCategory)
        obj.name = _clean(request.POST.get('name')) or ''
        obj.code = _clean(request.POST.get('code'))
        obj.parent_id = _clean(request.POST.get('parent')) or None
        obj.description = _clean(request.POST.get('description'))
        obj.isActive = _bool(request.POST.get('isActive', 'true'))
        _audit(request, obj)
        obj.full_clean()
        obj.save()
        logger.info(f'Library category saved id={obj.id} created={created}')
        return SuccessResponse('Category saved successfully.').to_json_response()
    except Exception as exc:
        logger.exception(f'Error saving library category: {exc}')
        return ErrorResponse(_validation_message(exc, 'Unable to save category.')).to_json_response()


@csrf_exempt
@transaction.atomic
@login_required
def author_api(request):
    try:
        obj, created = _get_or_new(request, LibraryAuthor)
        obj.name = _clean(request.POST.get('name')) or ''
        obj.country = _clean(request.POST.get('country'))
        obj.bio = _clean(request.POST.get('bio'))
        obj.isActive = _bool(request.POST.get('isActive', 'true'))
        _audit(request, obj)
        obj.full_clean()
        obj.save()
        logger.info(f'Library author saved id={obj.id} created={created}')
        return SuccessResponse('Author saved successfully.').to_json_response()
    except Exception as exc:
        logger.exception(f'Error saving library author: {exc}')
        return ErrorResponse(_validation_message(exc, 'Unable to save author.')).to_json_response()


@csrf_exempt
@transaction.atomic
@login_required
def publisher_api(request):
    try:
        obj, created = _get_or_new(request, LibraryPublisher)
        obj.name = _clean(request.POST.get('name')) or ''
        obj.phoneNumber = _clean(request.POST.get('phoneNumber'))
        obj.email = _clean(request.POST.get('email'))
        obj.address = _clean(request.POST.get('address'))
        obj.isActive = _bool(request.POST.get('isActive', 'true'))
        _audit(request, obj)
        obj.full_clean()
        obj.save()
        logger.info(f'Library publisher saved id={obj.id} created={created}')
        return SuccessResponse('Publisher saved successfully.').to_json_response()
    except Exception as exc:
        logger.exception(f'Error saving library publisher: {exc}')
        return ErrorResponse(_validation_message(exc, 'Unable to save publisher.')).to_json_response()


@csrf_exempt
@transaction.atomic
@login_required
def book_api(request):
    try:
        obj, created = _get_or_new(request, LibraryBook)
        obj.title = _clean(request.POST.get('title')) or ''
        obj.subtitle = _clean(request.POST.get('subtitle'))
        obj.isbn = _clean(request.POST.get('isbn'))
        obj.category_id = _clean(request.POST.get('category')) or None
        obj.publisher_id = _clean(request.POST.get('publisher')) or None
        obj.edition = _clean(request.POST.get('edition'))
        obj.language = _clean(request.POST.get('language'))
        obj.publicationYear = _int(request.POST.get('publicationYear'), 0) or None
        obj.shelfLocation = _clean(request.POST.get('shelfLocation'))
        obj.description = _clean(request.POST.get('description'))
        obj.isActive = _bool(request.POST.get('isActive', 'true'))
        if request.FILES.get('coverImage'):
            obj.coverImage = request.FILES['coverImage']
        _audit(request, obj)
        obj.full_clean()
        obj.save()
        author_ids = [int(a) for a in request.POST.getlist('authors[]') + request.POST.getlist('authors') if str(a).isdigit()]
        LibraryBookAuthor.objects.filter(book=obj).exclude(author_id__in=author_ids).delete()
        for author_id in author_ids:
            LibraryBookAuthor.objects.get_or_create(book=obj, author_id=author_id)
        logger.info(f'Library book saved id={obj.id} created={created}')
        return SuccessResponse('Book saved successfully.').to_json_response()
    except Exception as exc:
        logger.exception(f'Error saving library book: {exc}')
        return ErrorResponse(_validation_message(exc, 'Unable to save book.')).to_json_response()


@csrf_exempt
@transaction.atomic
@login_required
def copy_api(request):
    try:
        obj, created = _get_or_new(request, LibraryBookCopy)
        obj.book_id = _clean(request.POST.get('book')) or None
        obj.accessionNumber = _clean(request.POST.get('accessionNumber')) or ''
        obj.barcodeValue = _clean(request.POST.get('barcodeValue'))
        obj.qrCodeValue = _clean(request.POST.get('qrCodeValue'))
        obj.status = _clean(request.POST.get('status')) or 'available'
        obj.condition = _clean(request.POST.get('condition')) or 'good'
        obj.purchaseDate = _date(request.POST.get('purchaseDate'))
        obj.purchasePrice = _decimal(request.POST.get('purchasePrice'))
        obj.notes = _clean(request.POST.get('notes'))
        obj.isActive = _bool(request.POST.get('isActive', 'true'))
        _audit(request, obj)
        obj.full_clean()
        obj.save()
        logger.info(f'Library copy saved id={obj.id} accession={obj.accessionNumber} created={created}')
        return SuccessResponse('Book copy saved successfully.').to_json_response()
    except Exception as exc:
        logger.exception(f'Error saving library copy: {exc}')
        return ErrorResponse(_validation_message(exc, 'Unable to save book copy.')).to_json_response()


@csrf_exempt
@transaction.atomic
@login_required
def member_api(request):
    try:
        obj, created = _get_or_new(request, LibraryMember)
        obj.memberType = _clean(request.POST.get('memberType')) or 'student'
        obj.student_id = _clean(request.POST.get('student')) if obj.memberType == 'student' else None
        obj.staff_id = _clean(request.POST.get('staff')) if obj.memberType == 'staff' else None
        obj.memberCode = _clean(request.POST.get('memberCode')) or ''
        obj.joinDate = _date(request.POST.get('joinDate')) or date.today()
        obj.maxBooksAllowed = _int(request.POST.get('maxBooksAllowed'), _setting(request).defaultMaxBooksAllowed)
        obj.fineLimit = _decimal(request.POST.get('fineLimit'))
        obj.isActive = _bool(request.POST.get('isActive', 'true'))
        _audit(request, obj)
        obj.full_clean()
        obj.save()
        logger.info(f'Library member saved id={obj.id} code={obj.memberCode} created={created}')
        return SuccessResponse('Library member saved successfully.').to_json_response()
    except Exception as exc:
        logger.exception(f'Error saving library member: {exc}')
        return ErrorResponse(_validation_message(exc, 'Unable to save member.')).to_json_response()


@csrf_exempt
@transaction.atomic
@login_required
def issue_book_api(request):
    try:
        member = _scope(request, LibraryMember).get(pk=request.POST.get('member'), isActive=True)
        copy = _scope(request, LibraryBookCopy).select_related('book').select_for_update().get(pk=request.POST.get('copy'), isActive=True)
        issue_date = _date(request.POST.get('issueDate')) or date.today()
        due_date = _date(request.POST.get('dueDate'))
        issue = _create_issue_for_copy(request, member=member, copy=copy, issue_date=issue_date, due_date=due_date, notes=_clean(request.POST.get('notes')))
        logger.info(f'Library book issued issue={issue.id} copy={copy.id} member={member.id}')
        return SuccessResponse('Book issued successfully.').to_json_response()
    except Exception as exc:
        logger.exception(f'Error issuing library book: {exc}')
        return ErrorResponse(_validation_message(exc, 'Unable to issue book.')).to_json_response()


@csrf_exempt
@transaction.atomic
@login_required
def return_book_api(request):
    try:
        issue = _scope(request, LibraryIssue).select_related('copy', 'member').select_for_update().get(pk=request.POST.get('issue'), status='issued')
        return_date = _date(request.POST.get('returnDate')) or date.today()
        condition = _clean(request.POST.get('returnCondition')) or 'good'
        overdue_days, fine_amount = _calculate_overdue_fine(request, issue.dueDate, return_date)
        issue.returnDate = return_date
        issue.returnCondition = condition
        issue.overdueDays = overdue_days
        issue.fineAmount = fine_amount
        issue.status = 'returned'
        issue.notes = _append_note(issue.notes, 'Return note', request.POST.get('notes'))
        if condition == 'lost':
            issue.status = 'lost'
        elif condition == 'damaged':
            issue.status = 'damaged'
        _audit(request, issue)
        issue.save()
        copy = issue.copy
        copy.condition = condition
        copy.status = 'available'
        reason = None
        amount = fine_amount
        if condition == 'lost':
            copy.status = 'lost'
            reason = 'lost'
            amount += _setting(request).lostBookFine
        elif condition == 'damaged':
            copy.status = 'damaged'
            reason = 'damaged'
            amount += _setting(request).damagedBookFine
        elif fine_amount > 0:
            reason = 'overdue'
        _audit(request, copy)
        copy.save()
        if amount > 0 and reason:
            fine = LibraryFine(issue=issue, member=issue.member, reason=reason, amount=amount, paidAmount=Decimal('0.00'), status='pending')
            _audit(request, fine)
            fine.save()
        logger.info(f'Library book returned issue={issue.id} condition={condition} fine={amount}')
        return SuccessResponse('Book return saved successfully.').to_json_response()
    except Exception as exc:
        logger.exception(f'Error returning library book: {exc}')
        return ErrorResponse(_validation_message(exc, 'Unable to return book.')).to_json_response()


@csrf_exempt
@transaction.atomic
@login_required
def renew_book_api(request):
    try:
        issue = _scope(request, LibraryIssue).select_related('copy__book').select_for_update().get(pk=request.POST.get('issue'), status='issued')
        setting = _setting(request)
        if issue.renewalCount >= setting.maxRenewals:
            return ErrorResponse('Renewal limit reached for this issue.').to_json_response()
        if LibraryReservation.objects.filter(book=issue.copy.book, status='pending', isDeleted=False).exclude(member=issue.member).exists():
            return ErrorResponse('This book has a pending reservation by another member.').to_json_response()
        issue.dueDate = _date(request.POST.get('newDueDate')) or issue.dueDate + timedelta(days=setting.defaultIssueDays)
        issue.renewalCount += 1
        issue.notes = _append_note(issue.notes, 'Renew note', request.POST.get('notes'))
        _audit(request, issue)
        issue.save()
        logger.info(f'Library book renewed issue={issue.id} renewals={issue.renewalCount}')
        return SuccessResponse('Book renewed successfully.').to_json_response()
    except Exception as exc:
        logger.exception(f'Error renewing library book: {exc}')
        return ErrorResponse(_validation_message(exc, 'Unable to renew book.')).to_json_response()


@csrf_exempt
@transaction.atomic
@login_required
def reservation_api(request):
    try:
        obj, created = _get_or_new(request, LibraryReservation)
        obj.member_id = _clean(request.POST.get('member')) or None
        obj.book_id = _clean(request.POST.get('book')) or None
        obj.reservationDate = _date(request.POST.get('reservationDate')) or date.today()
        obj.expiryDate = _date(request.POST.get('expiryDate')) or obj.reservationDate + timedelta(days=_setting(request).reservationExpiryDays)
        obj.status = _clean(request.POST.get('status')) or 'pending'
        obj.notes = _clean(request.POST.get('notes'))
        _audit(request, obj)
        obj.full_clean()
        obj.save()
        logger.info(f'Library reservation saved id={obj.id} created={created}')
        return SuccessResponse('Reservation saved successfully.').to_json_response()
    except Exception as exc:
        logger.exception(f'Error saving library reservation: {exc}')
        return ErrorResponse(_validation_message(exc, 'Unable to save reservation.')).to_json_response()


@csrf_exempt
@transaction.atomic
@login_required
def fine_api(request):
    try:
        obj, created = _get_or_new(request, LibraryFine)
        obj.member_id = _clean(request.POST.get('member')) or None
        obj.issue_id = _clean(request.POST.get('issue')) or None
        obj.reason = _clean(request.POST.get('reason')) or 'manual'
        obj.amount = _decimal(request.POST.get('amount'))
        obj.paidAmount = _decimal(request.POST.get('paidAmount'))
        obj.paidDate = _date(request.POST.get('paidDate')) or (date.today() if obj.paidAmount >= obj.amount and obj.amount > 0 else None)
        obj.status = _clean(request.POST.get('status')) or 'pending'
        if obj.status == 'paid' and not obj.paidDate:
            obj.paidDate = date.today()
        obj.notes = _clean(request.POST.get('notes'))
        _audit(request, obj)
        obj.full_clean()
        obj.save()
        logger.info(f'Library fine saved id={obj.id} created={created}')
        return SuccessResponse('Fine saved successfully.').to_json_response()
    except Exception as exc:
        logger.exception(f'Error saving library fine: {exc}')
        return ErrorResponse(_validation_message(exc, 'Unable to save fine.')).to_json_response()


@csrf_exempt
@transaction.atomic
@login_required
def pay_fine_api(request):
    try:
        fine = _scope(request, LibraryFine).select_for_update().get(pk=request.POST.get('fine'))
        if fine.status != 'pending':
            return ErrorResponse('Only pending fines can receive payments.').to_json_response()
        amount = _decimal(request.POST.get('amount'))
        if amount <= 0:
            return ErrorResponse('Payment amount must be greater than zero.').to_json_response()
        fine.paidAmount = min(fine.paidAmount + amount, fine.amount)
        fine.paidDate = _date(request.POST.get('paidDate')) or date.today()
        if fine.paidAmount >= fine.amount:
            fine.status = 'paid'
        fine.notes = _append_note(fine.notes, 'Payment note', request.POST.get('notes'))
        _audit(request, fine)
        fine.save()
        logger.info(f'Library fine payment saved fine={fine.id} amount={amount} paid={fine.paidAmount}')
        return SuccessResponse('Fine payment saved successfully.').to_json_response()
    except Exception as exc:
        logger.exception(f'Error paying library fine: {exc}')
        return ErrorResponse(_validation_message(exc, 'Unable to save fine payment.')).to_json_response()


@csrf_exempt
@transaction.atomic
@login_required
def waive_fine_api(request):
    try:
        fine = _scope(request, LibraryFine).select_for_update().get(pk=request.POST.get('fine'), status='pending')
        fine.status = 'waived'
        _audit(request, fine)
        fine.save()
        logger.info(f'Library fine waived fine={fine.id}')
        return SuccessResponse('Fine waived successfully.').to_json_response()
    except Exception as exc:
        logger.exception(f'Error waiving library fine: {exc}')
        return ErrorResponse(_validation_message(exc, 'Unable to waive fine.')).to_json_response()


@csrf_exempt
@transaction.atomic
@login_required
def save_member_card_design_api(request):
    if request.method != 'POST':
        return ErrorResponse('Invalid request method.').to_json_response()
    try:
        school_id = _school_id(request)
        session_id = _session_id(request)
        if not school_id or not session_id:
            return ErrorResponse('Current school session not found.').to_json_response()

        design = get_or_create_active_member_card_design(school_id, session_id)
        existing_header = merged_config(design.headerConfig, DEFAULT_LIBRARY_CARD_HEADER_CONFIG)
        header_layout = request.POST.get('header_layout') or existing_header.get('layout') or 'masthead'
        if header_layout not in {'masthead', 'band'}:
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

        existing_style = merged_config(design.styleConfig, DEFAULT_LIBRARY_CARD_STYLE_CONFIG)
        style_config = {
            'primaryColor': _clean_color(request.POST.get('primary_color'), existing_style['primaryColor']),
            'headerColor': _clean_color(request.POST.get('header_color'), existing_style['headerColor']),
            'headerTextColor': _clean_color(request.POST.get('header_text_color'), existing_style['headerTextColor']),
            'cardBackgroundColor': _clean_color(request.POST.get('card_background_color'), existing_style['cardBackgroundColor']),
            'textColor': _clean_color(request.POST.get('text_color'), existing_style['textColor']),
            'labelColor': _clean_color(request.POST.get('label_color'), existing_style['labelColor']),
            'fontFamily': (request.POST.get('font_family') or existing_style['fontFamily']).strip(),
            'photoShape': request.POST.get('photo_shape') if request.POST.get('photo_shape') in {'rounded', 'circle'} else 'rounded',
            'showQr': _truthy_post_value(request.POST.get('show_qr')),
            'showBarcode': _truthy_post_value(request.POST.get('show_barcode')),
        }

        validity_mode = request.POST.get('validity_mode') or DEFAULT_LIBRARY_CARD_FOOTER_CONFIG['validityMode']
        if validity_mode not in {'session_end', 'custom_date', 'custom_text', 'hidden'}:
            validity_mode = 'session_end'
        parsed_valid_till = ''
        valid_till = (request.POST.get('valid_till') or '').strip()
        if valid_till:
            parsed = _date(valid_till)
            if not parsed:
                return ErrorResponse('Invalid validity date.').to_json_response()
            parsed_valid_till = parsed.isoformat()
        footer_config = {
            'showValidity': _truthy_post_value(request.POST.get('show_validity')),
            'validityMode': validity_mode,
            'validityText': (request.POST.get('validity_text') or '').strip(),
            'validTill': parsed_valid_till,
            'showSignature': _truthy_post_value(request.POST.get('show_signature')),
            'showSignatureImage': _truthy_post_value(request.POST.get('show_signature_image')),
            'signatureLabel': (request.POST.get('signature_label') or DEFAULT_LIBRARY_CARD_FOOTER_CONFIG['signatureLabel']).strip(),
        }

        try:
            fields_config = json.loads(request.POST.get('fields_config') or '[]')
        except json.JSONDecodeError:
            return ErrorResponse('Invalid field configuration.').to_json_response()
        fields_config = normalize_library_card_fields(fields_config or DEFAULT_LIBRARY_CARD_FIELDS_CONFIG)

        design.name = (request.POST.get('design_name') or design.name or 'Default Library Card Design').strip()
        design.headerConfig = header_config
        design.fieldsConfig = fields_config
        design.styleConfig = style_config
        design.footerConfig = footer_config
        if request.FILES.get('processed_librarian_signature'):
            design.librarianSignature = request.FILES['processed_librarian_signature']
        elif request.FILES.get('librarian_signature'):
            design.librarianSignature = request.FILES['librarian_signature']
        if request.POST.get('remove_librarian_signature') == '1':
            design.librarianSignature.delete(save=False)
            design.librarianSignature = None
        if request.FILES.get('background_image'):
            design.backgroundImage = request.FILES['background_image']
        if request.POST.get('remove_background_image') == '1':
            design.backgroundImage.delete(save=False)
            design.backgroundImage = None
        _audit(request, design)
        design.save()
        logger.info(f'Library member card design saved id={design.id} user={request.user.id} has_signature={bool(design.librarianSignature)}')
        return SuccessResponse(
            'Library card design saved successfully.',
            data={'design_id': design.id, 'has_signature': bool(design.librarianSignature)},
            extra={'color': 'success'}
        ).to_json_response()
    except Exception as exc:
        logger.exception(f'Error saving library member card design: {exc}')
        return ErrorResponse('Failed to save library card design.').to_json_response()


@csrf_exempt
@transaction.atomic
@login_required
def bulk_copy_api(request):
    try:
        book = _scope(request, LibraryBook).get(pk=request.POST.get('book'), isActive=True)
        prefix = _clean(request.POST.get('prefix')) or ''
        start_number = _int(request.POST.get('startNumber'), 1)
        quantity = _int(request.POST.get('quantity'), 0)
        padding = _int(request.POST.get('padding'), 3)
        if quantity < 1 or quantity > 500:
            return ErrorResponse('Quantity must be between 1 and 500.').to_json_response()
        purchase_date = _date(request.POST.get('purchaseDate'))
        purchase_price = _decimal(request.POST.get('purchasePrice'))
        status = _clean(request.POST.get('status')) or 'available'
        condition = _clean(request.POST.get('condition')) or 'good'
        notes = _clean(request.POST.get('notes'))
        accession_numbers = [f'{prefix}{str(start_number + offset).zfill(padding)}' for offset in range(quantity)]
        duplicate_count = _scope(request, LibraryBookCopy).filter(accessionNumber__in=accession_numbers).count()
        if duplicate_count:
            return ErrorResponse('One or more accession numbers already exist in this session.').to_json_response()
        created = []
        for accession in accession_numbers:
            copy = LibraryBookCopy(
                book=book,
                accessionNumber=accession,
                barcodeValue=accession,
                qrCodeValue=accession,
                status=status,
                condition=condition,
                purchaseDate=purchase_date,
                purchasePrice=purchase_price,
                notes=notes,
                isActive=True,
            )
            _audit(request, copy)
            copy.full_clean()
            copy.save()
            created.append(copy.id)
        logger.info(f'Library bulk copies created book={book.id} count={len(created)}')
        return SuccessResponse(f'{len(created)} book copies created successfully.', data={'created': len(created)}).to_json_response()
    except Exception as exc:
        logger.exception(f'Error creating bulk library copies: {exc}')
        return ErrorResponse(_validation_message(exc, 'Unable to create bulk copies.')).to_json_response()


@csrf_exempt
@transaction.atomic
@login_required
def issue_reservation_api(request):
    try:
        reservation = _scope(request, LibraryReservation).select_related('book', 'member', 'member__student', 'member__staff').select_for_update().get(pk=request.POST.get('reservation'), status='pending')
        copy_id = _clean(request.POST.get('copy'))
        copies = _scope(request, LibraryBookCopy).select_related('book').select_for_update().filter(book=reservation.book, status='available', isActive=True).order_by('accessionNumber')
        copy = copies.get(pk=copy_id) if copy_id else copies.first()
        if not copy:
            return ErrorResponse('No available copy found for this reservation.').to_json_response()
        issue_date = _date(request.POST.get('issueDate')) or date.today()
        due_date = _date(request.POST.get('dueDate'))
        issue = _create_issue_for_copy(request, member=reservation.member, copy=copy, issue_date=issue_date, due_date=due_date, notes=f'Issued from reservation #{reservation.id}')
        reservation.status = 'fulfilled'
        _audit(request, reservation)
        reservation.save(update_fields=['status', 'schoolID', 'sessionID', 'updatedByUserID', 'lastEditedBy', 'lastUpdatedOn'])
        logger.info(f'Library reservation issued reservation={reservation.id} issue={issue.id} copy={copy.id}')
        return SuccessResponse('Reservation issued successfully.').to_json_response()
    except Exception as exc:
        logger.exception(f'Error issuing library reservation: {exc}')
        return ErrorResponse(_validation_message(exc, 'Unable to issue reserved book.')).to_json_response()


@login_required
def issue_history_api(request):
    try:
        qs = _scope(request, LibraryIssue).select_related('copy__book', 'member', 'member__student', 'member__staff').order_by('-issueDate', '-id')
        member_id = _clean(request.GET.get('member'))
        book_id = _clean(request.GET.get('book'))
        copy_id = _clean(request.GET.get('copy'))
        if member_id:
            qs = qs.filter(member_id=member_id)
        if book_id:
            qs = qs.filter(copy__book_id=book_id)
        if copy_id:
            qs = qs.filter(copy_id=copy_id)
        limit = min(max(_int(request.GET.get('limit'), 20), 1), 100)
        rows = []
        for issue in qs[:limit]:
            rows.append({
                'book': issue.copy.book.title,
                'copy': issue.copy.accessionNumber,
                'memberCode': issue.member.memberCode,
                'memberName': _member_name(issue.member),
                'issueDate': issue.issueDate.strftime('%d-%m-%Y'),
                'dueDate': issue.dueDate.strftime('%d-%m-%Y'),
                'returnDate': issue.returnDate.strftime('%d-%m-%Y') if issue.returnDate else 'N/A',
                'status': issue.status.title(),
                'fineAmount': str(issue.fineAmount),
                'renewalCount': issue.renewalCount,
            })
        logger.info(f'Library issue history preview fetched member={member_id} book={book_id} copy={copy_id} count={len(rows)}')
        return JsonResponse({'status': 'success', 'data': rows, 'limit': limit})
    except Exception as exc:
        logger.exception(f'Error fetching library issue history: {exc}')
        return ErrorResponse('Unable to fetch issue history.').to_json_response()


@login_required
def issue_detail_api(request):
    try:
        issue = _scope(request, LibraryIssue).select_related('copy__book', 'member', 'member__student', 'member__staff').get(pk=request.GET.get('issue'))
        data = {
            'id': issue.id,
            'book': issue.copy.book.title,
            'copy': issue.copy.accessionNumber,
            'memberCode': issue.member.memberCode,
            'memberName': _member_name(issue.member),
            'issueDate': issue.issueDate.isoformat(),
            'dueDate': issue.dueDate.isoformat(),
            'returnDate': issue.returnDate.isoformat() if issue.returnDate else '',
            'renewalCount': issue.renewalCount,
            'status': issue.status.title(),
            'returnCondition': issue.returnCondition or 'N/A',
            'overdueDays': issue.overdueDays,
            'fineAmount': str(issue.fineAmount),
            'notes': issue.notes or '',
            'fines': [
                {
                    'reason': fine.reason.title(),
                    'amount': str(fine.amount),
                    'paidAmount': str(fine.paidAmount),
                    'balance': str(fine.balance),
                    'paidDate': fine.paidDate.isoformat() if fine.paidDate else '',
                    'status': fine.status.title(),
                }
                for fine in issue.fines.filter(isDeleted=False).order_by('-id')
            ],
        }
        logger.info(f'Library issue detail fetched issue={issue.id}')
        return JsonResponse({'status': 'success', 'data': data})
    except Exception as exc:
        logger.exception(f'Error fetching library issue detail: {exc}')
        return ErrorResponse('Unable to fetch issue detail.').to_json_response()


@login_required
def detail_api(request):
    model_map = {
        'category': LibraryCategory,
        'author': LibraryAuthor,
        'publisher': LibraryPublisher,
        'book': LibraryBook,
        'copy': LibraryBookCopy,
        'member': LibraryMember,
        'reservation': LibraryReservation,
        'fine': LibraryFine,
    }
    try:
        entity = request.GET.get('entity')
        obj = _scope(request, model_map[entity]).get(pk=request.GET.get('id'))
        data = {
            field.name: _json_value(getattr(obj, f'{field.name}_id', getattr(obj, field.name, None)))
            for field in obj._meta.fields
            if field.name not in {'schoolID', 'sessionID', 'updatedByUserID'}
        }
        if entity == 'book':
            data['authors'] = list(obj.authors.values_list('id', flat=True))
        logger.info(f'Library detail fetched entity={entity} id={obj.id}')
        return JsonResponse({'status': 'success', 'data': data})
    except Exception as exc:
        logger.exception(f'Error fetching library detail: {exc}')
        return ErrorResponse('Unable to fetch detail.').to_json_response()


@csrf_exempt
@transaction.atomic
@login_required
def delete_api(request):
    model_map = {
        'category': LibraryCategory,
        'author': LibraryAuthor,
        'publisher': LibraryPublisher,
        'book': LibraryBook,
        'copy': LibraryBookCopy,
        'member': LibraryMember,
        'reservation': LibraryReservation,
        'fine': LibraryFine,
    }
    try:
        entity = request.POST.get('entity')
        obj = _scope(request, model_map[entity]).get(pk=request.POST.get('id'))
        obj.isDeleted = True
        _audit(request, obj)
        obj.save()
        logger.info(f'Library object deleted entity={entity} id={obj.id}')
        return SuccessResponse('Record deleted successfully.').to_json_response()
    except Exception as exc:
        logger.exception(f'Error deleting library object: {exc}')
        return ErrorResponse('Unable to delete record.').to_json_response()


@login_required
def library_report_csv(request):
    report = request.GET.get('report', 'overdue')
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="library-{report}-report.csv"'
    writer = csv.writer(response)
    try:
        if report == 'stock':
            writer.writerow(['Book', 'ISBN', 'Total Copies', 'Available', 'Issued', 'Lost', 'Damaged'])
            books = _scope(request, LibraryBook).annotate(total=Count('copies'), available=Count('copies', filter=Q(copies__status='available', copies__isDeleted=False)), issued=Count('copies', filter=Q(copies__status='issued', copies__isDeleted=False)), lost=Count('copies', filter=Q(copies__status='lost', copies__isDeleted=False)), damaged=Count('copies', filter=Q(copies__status='damaged', copies__isDeleted=False)))
            for book in books:
                writer.writerow([book.title, book.isbn or '', book.total, book.available, book.issued, book.lost, book.damaged])
        elif report == 'fines':
            writer.writerow(['Member Code', 'Member Name', 'Reason', 'Amount', 'Paid', 'Status'])
            for fine in _scope(request, LibraryFine).select_related('member', 'member__student', 'member__staff'):
                writer.writerow([fine.member.memberCode, _member_name(fine.member), fine.reason, fine.amount, fine.paidAmount, fine.status])
        else:
            writer.writerow(['Book', 'Copy', 'Member Code', 'Member Name', 'Issue Date', 'Due Date', 'Days Overdue'])
            today = date.today()
            for issue in _scope(request, LibraryIssue).select_related('copy__book', 'member', 'member__student', 'member__staff').filter(status='issued', dueDate__lt=today):
                writer.writerow([issue.copy.book.title, issue.copy.accessionNumber, issue.member.memberCode, _member_name(issue.member), issue.issueDate, issue.dueDate, (today - issue.dueDate).days])
        logger.info(f'Library report CSV exported report={report}')
        return response
    except Exception as exc:
        logger.exception(f'Error exporting library report CSV: {exc}')
        writer.writerow(['Unable to export report'])
        return response
