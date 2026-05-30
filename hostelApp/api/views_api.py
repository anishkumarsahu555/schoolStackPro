import calendar
import csv
from datetime import date
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Count, Q, Sum
from django.http import HttpResponse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.html import escape
from django_datatables_view.base_datatable_view import BaseDatatableView

from financeApp.services import clear_payment_receipt, sync_payment_receipt, sync_student_charge
from hostelApp.models import (
    HostelAdmission,
    HostelAssignment,
    HostelBed,
    HostelBuilding,
    HostelFeeMapping,
    HostelFeeRecord,
    HostelFloor,
    HostelRoom,
    HostelRoomType,
)
from managementApp.models import Student, TeacherDetail
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


def _bool(value):
    return str(value).strip().lower() in {'1', 'true', 'yes', 'on', 'active'}


def _user_label(request):
    return request.user.get_full_name() or request.user.username or str(request.user.id)


def _audit_fields(request, obj):
    obj.schoolID_id = _school_id(request)
    obj.sessionID_id = _session_id(request)
    obj.updatedByUserID = request.user
    obj.lastEditedBy = _user_label(request)


def _validation_message(exc, fallback):
    if isinstance(exc, ValidationError):
        if hasattr(exc, 'message_dict'):
            parts = []
            for field, messages in exc.message_dict.items():
                parts.append(f'{field}: {", ".join(str(message) for message in messages)}')
            return '; '.join(parts) or fallback
        if hasattr(exc, 'messages'):
            return '; '.join(str(message) for message in exc.messages) or fallback
    return str(exc) or fallback


def _scoped(model):
    return model.objects.filter(isDeleted=False)


def _scoped_for_request(request, model):
    return _scoped(model).filter(schoolID_id=_school_id(request), sessionID_id=_session_id(request))


def _scoped_object_or_new(request, model, object_id):
    object_id = _clean(object_id)
    if object_id:
        return _scoped_for_request(request, model).filter(pk=object_id).first()
    return model()


def _ensure_scoped_fk(request, model, object_id, label, optional=False):
    object_id = _clean(object_id)
    if not object_id:
        if optional:
            return None
        raise ValidationError(f'{label} is required.')
    if not _scoped_for_request(request, model).filter(pk=object_id).exists():
        raise ValidationError(f'{label} is invalid for current school/session.')
    return object_id


def _status_pill(active):
    label = 'Active' if active else 'Inactive'
    color = 'green' if active else 'grey'
    return f'<span class="ui {color} tiny label">{label}</span>'


def _choice_pill(label, color='blue'):
    return f'<span class="ui {color} tiny label">{escape(label or "N/A")}</span>'


def _fee_status_pill(status):
    labels = {
        'pending': ('Pending', 'orange'),
        'partial': ('Partial', 'yellow'),
        'paid': ('Paid', 'green'),
        'waived': ('Waived', 'blue'),
        'cancelled': ('Cancelled', 'grey'),
    }
    label, color = labels.get(status, (status or 'N/A', 'grey'))
    return _choice_pill(label, color)


def _dt_actions(edit_fn, delete_fn, obj_id):
    return (
        f'<button data-inverted="" data-tooltip="Edit Detail" data-position="left center" '
        f'data-variation="mini" style="font-size:10px;" onclick="{edit_fn}({obj_id})" '
        f'class="ui circular facebook icon button green"><i class="pen icon"></i></button>'
        f'<button data-inverted="" data-tooltip="Delete" data-position="left center" '
        f'data-variation="mini" style="font-size:10px; margin-left:3px;" onclick="{delete_fn}({obj_id})" '
        f'class="ui circular youtube icon button"><i class="trash alternate icon"></i></button>'
    )


def _fee_record_actions(obj_id):
    return (
        f'<button data-inverted="" data-tooltip="Record Payment" data-position="left center" '
        f'data-variation="mini" style="font-size:10px;" onclick="showPaymentModal({obj_id})" '
        f'class="ui circular facebook icon button green"><i class="rupee sign icon"></i></button>'
        f'<button data-inverted="" data-tooltip="Waive" data-position="left center" '
        f'data-variation="mini" style="font-size:10px; margin-left:3px;" onclick="waiveFeeRecord({obj_id})" '
        f'class="ui circular blue icon button"><i class="ban icon"></i></button>'
        f'<button data-inverted="" data-tooltip="Cancel" data-position="left center" '
        f'data-variation="mini" style="font-size:10px; margin-left:3px;" onclick="cancelFeeRecord({obj_id})" '
        f'class="ui circular youtube icon button"><i class="times icon"></i></button>'
    )


def _period_dates(year, month):
    year = int(year)
    month = int(month)
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last_day)


def _sync_hostel_student_charge(request, record):
    assignment = record.assignmentID
    if not assignment or assignment.residentType != 'student' or assignment.feeMode != 'student_fee' or not assignment.studentID_id:
        return None
    charge = sync_student_charge(
        student_obj=assignment.studentID,
        school_id=_school_id(request),
        session_id=_session_id(request),
        fee_head_code='HOSTEL_FEE',
        amount=record.netAmount if record.status not in {'waived', 'cancelled'} else Decimal('0.00'),
        title=f'Hostel Fee - {calendar.month_name[record.feeMonth]} {record.feeYear}',
        description=f'Hostel fee for {assignment.roomID.roomNumber if assignment.roomID else "room"}',
        charge_date=record.periodStartDate,
        due_date=record.dueDate or record.periodEndDate,
        source_module='hostel_fee_record',
        source_record_id=record.id,
        standard_obj=assignment.studentID.standardID if assignment.studentID else None,
        user_obj=request.user,
    )
    if charge and record.financeChargeID_id != charge.id:
        record.financeChargeID = charge
        record.save(update_fields=['financeChargeID', 'lastUpdatedOn'])
    return charge


def _student_display(student):
    roll = getattr(student, 'roll', None)
    try:
        roll_label = str(int(float(roll)))
    except Exception:
        roll_label = str(roll or 'N/A')
    class_label = 'N/A'
    if getattr(student, 'standardID_id', None) and getattr(student, 'standardID', None):
        class_label = student.standardID.name or 'N/A'
        if student.standardID.section:
            class_label = f'{class_label} {student.standardID.section}'
    return f"{student.name or 'N/A'} - Roll {roll_label} - Class {class_label}"


def _teacher_display(teacher):
    code = getattr(teacher, 'employeeCode', None) or teacher.id
    return f'{teacher.name} ({code})'


def _resident_display(obj):
    if obj.residentType == 'teacher' and obj.teacherID_id:
        return _teacher_display(obj.teacherID)
    if obj.studentID_id:
        return _student_display(obj.studentID)
    return 'N/A'


def _active_assignment_for_resident(request, resident_type, student_id=None, teacher_id=None, exclude_id=None):
    qs = _scoped_for_request(request, HostelAssignment).filter(isActive=True, residentType=resident_type)
    if exclude_id:
        qs = qs.exclude(pk=exclude_id)
    if resident_type == 'teacher':
        return qs.filter(teacherID_id=teacher_id).first()
    return qs.filter(studentID_id=student_id).first()


class HostelDatatableView(BaseDatatableView):
    def handle_exception(self, exc):
        logger.exception(f'Hostel datatable error in {self.__class__.__name__}: {exc}')
        draw = self.request.GET.get('draw') or self.request.GET.get('sEcho') or 0
        try:
            draw = int(draw)
        except (TypeError, ValueError):
            draw = 0
        return {
            'draw': draw,
            'recordsTotal': 0,
            'recordsFiltered': 0,
            'data': [],
            'result': 'ok',
        }

    def ordering(self, qs):
        sorting_cols = 0
        sort_key = f'order[{sorting_cols}][column]'
        while sort_key in self._querydict:
            sorting_cols += 1
            sort_key = f'order[{sorting_cols}][column]'

        order = []
        order_columns = self.get_order_columns()
        for index in range(sorting_cols):
            try:
                sort_col = int(self._querydict.get(f'order[{index}][column]'))
            except (TypeError, ValueError):
                sort_col = 0
            if sort_col < 0 or sort_col >= len(order_columns):
                continue
            sort_dir = self._querydict.get(f'order[{index}][dir]')
            sort_prefix = '-' if sort_dir == 'desc' else ''
            sort_field = order_columns[sort_col]
            if not sort_field:
                continue
            if isinstance(sort_field, list):
                order.extend(f'{sort_prefix}{field.replace(".", "__")}' for field in sort_field if field)
            else:
                order.append(f'{sort_prefix}{sort_field.replace(".", "__")}')
        return qs.order_by(*order) if order else qs


class HostelBuildingListJson(HostelDatatableView):
    order_columns = ['buildingCode', 'buildingName', 'wardenName', 'wardenPhone', 'isActive', 'lastUpdatedOn']

    def get_initial_queryset(self):
        return _scoped_for_request(self.request, HostelBuilding)

    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]')
        if search:
            qs = qs.filter(Q(buildingCode__icontains=search) | Q(buildingName__icontains=search) | Q(wardenName__icontains=search))
        return qs

    def prepare_results(self, qs):
        return [[escape(i.buildingCode), escape(i.buildingName), escape(i.wardenName or 'N/A'), escape(i.wardenPhone or 'N/A'), _status_pill(i.isActive), _dt_actions('editBuilding', 'confirmDeleteBuilding', i.id)] for i in qs]


class HostelFloorListJson(HostelDatatableView):
    order_columns = ['buildingID__buildingName', 'floorName', 'displayOrder', 'isActive', 'id']

    def get_initial_queryset(self):
        qs = _scoped_for_request(self.request, HostelFloor).select_related('buildingID')
        building_id = self.request.GET.get('buildingID')
        if building_id:
            qs = qs.filter(buildingID_id=building_id)
        return qs

    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]')
        if search:
            qs = qs.filter(Q(buildingID__buildingName__icontains=search) | Q(floorName__icontains=search))
        return qs

    def prepare_results(self, qs):
        return [[escape(i.buildingID.buildingName), escape(i.floorName), escape(i.displayOrder), _status_pill(i.isActive), _dt_actions('editFloor', 'confirmDeleteFloor', i.id)] for i in qs]


class HostelRoomTypeListJson(HostelDatatableView):
    order_columns = ['name', 'capacity', 'defaultMonthlyFee', 'isActive', 'id']

    def get_initial_queryset(self):
        return _scoped_for_request(self.request, HostelRoomType)

    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]')
        return qs.filter(name__icontains=search) if search else qs

    def prepare_results(self, qs):
        return [[escape(i.name), escape(i.capacity), escape(str(i.defaultMonthlyFee)), _status_pill(i.isActive), _dt_actions('editRoomType', 'confirmDeleteRoomType', i.id)] for i in qs]


class HostelRoomListJson(HostelDatatableView):
    order_columns = ['buildingID__buildingName', 'floorID__floorName', 'roomNumber', 'roomTypeID__name', 'capacity', 'monthlyFee', 'isActive', 'id']

    def get_initial_queryset(self):
        qs = _scoped_for_request(self.request, HostelRoom).select_related('buildingID', 'floorID', 'roomTypeID')
        if self.request.GET.get('buildingID'):
            qs = qs.filter(buildingID_id=self.request.GET.get('buildingID'))
        return qs

    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]')
        if search:
            qs = qs.filter(Q(buildingID__buildingName__icontains=search) | Q(roomNumber__icontains=search) | Q(roomTypeID__name__icontains=search))
        return qs

    def prepare_results(self, qs):
        return [[escape(i.buildingID.buildingName), escape(i.floorID.floorName if i.floorID else 'N/A'), escape(i.roomNumber), escape(i.roomTypeID.name if i.roomTypeID else 'N/A'), escape(i.capacity), escape(str(i.monthlyFee)), _status_pill(i.isActive), _dt_actions('editRoom', 'confirmDeleteRoom', i.id)] for i in qs]


class HostelBedListJson(HostelDatatableView):
    order_columns = ['roomID__buildingID__buildingName', 'roomID__roomNumber', 'bedNumber', 'status', 'isActive', 'id']

    def get_initial_queryset(self):
        qs = _scoped_for_request(self.request, HostelBed).select_related('roomID', 'roomID__buildingID')
        if self.request.GET.get('roomID'):
            qs = qs.filter(roomID_id=self.request.GET.get('roomID'))
        return qs

    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]')
        if search:
            qs = qs.filter(Q(roomID__roomNumber__icontains=search) | Q(bedNumber__icontains=search) | Q(roomID__buildingID__buildingName__icontains=search))
        return qs

    def prepare_results(self, qs):
        colors = {'available': 'green', 'occupied': 'orange', 'reserved': 'blue', 'maintenance': 'grey'}
        return [[escape(i.roomID.buildingID.buildingName), escape(i.roomID.roomNumber), escape(i.bedNumber), _choice_pill(i.get_status_display(), colors.get(i.status, 'grey')), _status_pill(i.isActive), _dt_actions('editBed', 'confirmDeleteBed', i.id)] for i in qs]


class HostelAdmissionListJson(HostelDatatableView):
    order_columns = ['applicationNo', 'residentType', 'studentID__name', 'applicationDate', 'preferredRoomTypeID__name', 'status', 'admissionFee', 'isActive', 'id']

    def get_initial_queryset(self):
        qs = _scoped_for_request(self.request, HostelAdmission).select_related('studentID', 'teacherID', 'preferredRoomTypeID')
        if self.request.GET.get('status'):
            qs = qs.filter(status=self.request.GET.get('status'))
        if self.request.GET.get('residentType'):
            qs = qs.filter(residentType=self.request.GET.get('residentType'))
        return qs

    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]')
        if search:
            qs = qs.filter(
                Q(applicationNo__icontains=search)
                | Q(studentID__name__icontains=search)
                | Q(studentID__registrationCode__icontains=search)
                | Q(teacherID__name__icontains=search)
                | Q(teacherID__employeeCode__icontains=search)
            )
        return qs

    def prepare_results(self, qs):
        colors = {'applied': 'blue', 'approved': 'teal', 'waitlisted': 'yellow', 'rejected': 'red', 'admitted': 'green', 'cancelled': 'grey'}
        return [[escape(i.applicationNo), escape(i.get_residentType_display()), escape(_resident_display(i)), escape(i.applicationDate.strftime('%d-%m-%Y') if i.applicationDate else 'N/A'), escape(i.preferredRoomTypeID.name if i.preferredRoomTypeID else 'N/A'), _choice_pill(i.get_status_display(), colors.get(i.status, 'grey')), escape(str(i.admissionFee)), _status_pill(i.isActive), _dt_actions('editAdmission', 'confirmDeleteAdmission', i.id)] for i in qs]


class HostelAssignmentListJson(HostelDatatableView):
    order_columns = ['residentType', 'studentID__name', 'buildingID__buildingName', 'roomID__roomNumber', 'bedID__bedNumber', 'monthlyFee', 'feeMode', 'startDate', 'isActive', 'id']

    def get_initial_queryset(self):
        return _scoped_for_request(self.request, HostelAssignment).select_related('studentID', 'teacherID', 'buildingID', 'roomID', 'bedID', 'admissionID')

    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]')
        if search:
            qs = qs.filter(Q(studentID__name__icontains=search) | Q(studentID__registrationCode__icontains=search) | Q(teacherID__name__icontains=search) | Q(teacherID__employeeCode__icontains=search) | Q(buildingID__buildingName__icontains=search) | Q(roomID__roomNumber__icontains=search) | Q(bedID__bedNumber__icontains=search))
        return qs

    def prepare_results(self, qs):
        rows = []
        for i in qs:
            rows.append([
                escape(i.get_residentType_display()),
                escape(_resident_display(i)),
                escape(i.buildingID.buildingName if i.buildingID_id and i.buildingID else 'N/A'),
                escape(i.roomID.roomNumber if i.roomID_id and i.roomID else 'N/A'),
                escape(i.bedID.bedNumber if i.bedID_id and i.bedID else 'N/A'),
                escape(str(i.monthlyFee)),
                escape(i.get_feeMode_display()),
                escape(i.startDate.strftime('%d-%m-%Y') if i.startDate else 'N/A'),
                _status_pill(i.isActive),
                _dt_actions('editAssignment', 'confirmDeleteAssignment', i.id),
            ])
        return rows


class HostelFeeMappingListJson(HostelDatatableView):
    order_columns = ['buildingID__buildingName', 'roomTypeID__name', 'roomID__roomNumber', 'monthlyFee', 'effectiveFrom', 'effectiveTo', 'isActive', 'id']

    def get_initial_queryset(self):
        return _scoped_for_request(self.request, HostelFeeMapping).select_related('buildingID', 'roomTypeID', 'roomID')

    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]')
        if search:
            qs = qs.filter(Q(buildingID__buildingName__icontains=search) | Q(roomTypeID__name__icontains=search) | Q(roomID__roomNumber__icontains=search))
        return qs

    def prepare_results(self, qs):
        return [[escape(i.buildingID.buildingName if i.buildingID else 'All'), escape(i.roomTypeID.name if i.roomTypeID else 'All'), escape(i.roomID.roomNumber if i.roomID else 'All'), escape(str(i.monthlyFee)), escape(i.effectiveFrom.strftime('%d-%m-%Y') if i.effectiveFrom else 'N/A'), escape(i.effectiveTo.strftime('%d-%m-%Y') if i.effectiveTo else 'N/A'), _status_pill(i.isActive), _dt_actions('editFeeMapping', 'confirmDeleteFeeMapping', i.id)] for i in qs]


def _fee_record_row(record):
    assignment = record.assignmentID
    return {
        'id': record.id,
        'period': f'{calendar.month_abbr[record.feeMonth]} {record.feeYear}',
        'residentName': assignment.resident_name,
        'studentName': assignment.resident_name,
        'building': assignment.buildingID.buildingName if assignment and assignment.buildingID else 'N/A',
        'room': assignment.roomID.roomNumber if assignment and assignment.roomID else 'N/A',
        'bed': assignment.bedID.bedNumber if assignment and assignment.bedID else 'N/A',
        'netAmount': str(record.netAmount),
        'paidAmount': str(record.paidAmount),
        'balanceAmount': str(record.balanceAmount),
        'status': record.status,
        'statusLabel': record.get_status_display(),
        'dueDate': record.dueDate.strftime('%d-%m-%Y') if record.dueDate else 'N/A',
        'paymentMode': record.paymentMode or '',
        'referenceNo': record.referenceNo or '',
        'notes': record.notes or '',
    }


class HostelFeeRecordListJson(HostelDatatableView):
    order_columns = ['feeYear', 'assignmentID__residentType', 'assignmentID__studentID__name', 'assignmentID__buildingID__buildingName', 'assignmentID__roomID__roomNumber', 'assignmentID__bedID__bedNumber', 'netAmount', 'paidAmount', 'balanceAmount', 'status', 'dueDate', 'id']

    def get_initial_queryset(self):
        qs = _scoped_for_request(self.request, HostelFeeRecord).select_related('assignmentID', 'assignmentID__studentID', 'assignmentID__teacherID', 'assignmentID__buildingID', 'assignmentID__roomID', 'assignmentID__bedID')
        if self.request.GET.get('feeMonth'):
            qs = qs.filter(feeMonth=self.request.GET.get('feeMonth'))
        if self.request.GET.get('feeYear'):
            qs = qs.filter(feeYear=self.request.GET.get('feeYear'))
        if self.request.GET.get('status'):
            qs = qs.filter(status=self.request.GET.get('status'))
        return qs

    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]')
        if search:
            qs = qs.filter(Q(assignmentID__studentID__name__icontains=search) | Q(assignmentID__studentID__registrationCode__icontains=search) | Q(assignmentID__teacherID__name__icontains=search) | Q(assignmentID__teacherID__employeeCode__icontains=search) | Q(assignmentID__roomID__roomNumber__icontains=search) | Q(assignmentID__bedID__bedNumber__icontains=search))
        return qs

    def prepare_results(self, qs):
        return [[f'{calendar.month_abbr[i.feeMonth]} {i.feeYear}', escape(i.assignmentID.get_residentType_display()), escape(i.student_name), escape(i.assignmentID.buildingID.buildingName), escape(i.assignmentID.roomID.roomNumber), escape(i.assignmentID.bedID.bedNumber), escape(str(i.netAmount)), escape(str(i.paidAmount)), escape(str(i.balanceAmount)), _fee_status_pill(i.status), escape(i.dueDate.strftime('%d-%m-%Y') if i.dueDate else 'N/A'), _fee_record_actions(i.id)] for i in qs]


def _serialize_common(obj, fields):
    data = {'id': obj.id}
    for field in fields:
        value = getattr(obj, field)
        if hasattr(value, 'isoformat'):
            value = value.isoformat()
        data[field] = value
    return data


@login_required
def hostel_options_api(request):
    buildings = _scoped_for_request(request, HostelBuilding).filter(isActive=True).order_by('buildingCode')
    floors = _scoped_for_request(request, HostelFloor).filter(isActive=True).select_related('buildingID').order_by('buildingID__buildingCode', 'displayOrder')
    room_types = _scoped_for_request(request, HostelRoomType).filter(isActive=True).order_by('name')
    rooms = _scoped_for_request(request, HostelRoom).filter(isActive=True).select_related('buildingID', 'roomTypeID').order_by('buildingID__buildingCode', 'roomNumber')
    beds = _scoped_for_request(request, HostelBed).filter(isActive=True).select_related('roomID').order_by('roomID__roomNumber', 'bedNumber')
    admissions = _scoped_for_request(request, HostelAdmission).filter(isActive=True).exclude(status__in=['rejected', 'cancelled']).select_related('studentID', 'teacherID').order_by('residentType', 'applicationNo')
    assigned_student_ids = set(_scoped_for_request(request, HostelAssignment).filter(isActive=True, residentType='student', studentID__isnull=False).values_list('studentID_id', flat=True))
    assigned_teacher_ids = set(_scoped_for_request(request, HostelAssignment).filter(isActive=True, residentType='teacher', teacherID__isnull=False).values_list('teacherID_id', flat=True))
    mappings = _scoped_for_request(request, HostelFeeMapping).filter(isActive=True).order_by('-roomID_id', '-roomTypeID_id', '-buildingID_id')
    return SuccessResponse('Hostel options loaded.', data={
        'buildings': [{'id': i.id, 'text': f'{i.buildingCode} - {i.buildingName}'} for i in buildings],
        'floors': [{'id': i.id, 'buildingID': i.buildingID_id, 'text': i.floorName} for i in floors],
        'roomTypes': [{'id': i.id, 'text': i.name, 'monthlyFee': str(i.defaultMonthlyFee), 'capacity': i.capacity} for i in room_types],
        'rooms': [{'id': i.id, 'buildingID': i.buildingID_id, 'floorID': i.floorID_id, 'roomTypeID': i.roomTypeID_id, 'text': f'{i.buildingID.buildingName} / {i.roomNumber}', 'monthlyFee': str(i.monthlyFee), 'capacity': i.capacity} for i in rooms],
        'beds': [{'id': i.id, 'roomID': i.roomID_id, 'text': i.bedNumber, 'status': i.status} for i in beds],
        'admissions': [{
            'id': i.id,
            'residentType': i.residentType,
            'studentID': i.studentID_id,
            'teacherID': i.teacherID_id,
            'status': i.status,
            'hasActiveAssignment': (i.residentType == 'student' and i.studentID_id in assigned_student_ids) or (i.residentType == 'teacher' and i.teacherID_id in assigned_teacher_ids),
            'text': f'{i.applicationNo} - {i.get_status_display()} - {i.get_residentType_display()} - {_resident_display(i)}'
        } for i in admissions],
        'feeMappings': [{'id': i.id, 'buildingID': i.buildingID_id, 'roomTypeID': i.roomTypeID_id, 'roomID': i.roomID_id, 'monthlyFee': str(i.monthlyFee)} for i in mappings],
    }).to_json_response()


@login_required
def student_options_api(request):
    try:
        students = Student.objects.select_related('standardID').filter(schoolID_id=_school_id(request), sessionID_id=_session_id(request), isDeleted=False).order_by('name', 'roll')[:800]
        return SuccessResponse('Student options loaded.', data=[{'id': i.id, 'text': _student_display(i)} for i in students]).to_json_response()
    except Exception as exc:
        logger.exception(f'Error loading hostel student options: {exc}')
        return ErrorResponse('Unable to load students.', status_code=500).to_json_response()


@login_required
def resident_options_api(request):
    try:
        students = Student.objects.select_related('standardID').filter(schoolID_id=_school_id(request), sessionID_id=_session_id(request), isDeleted=False).order_by('name', 'roll')[:800]
        teachers = TeacherDetail.objects.filter(schoolID_id=_school_id(request), sessionID_id=_session_id(request), isDeleted=False).order_by('name')[:800]
        return SuccessResponse('Resident options loaded.', data={
            'students': [{'id': i.id, 'text': _student_display(i)} for i in students],
            'teachers': [{'id': i.id, 'text': _teacher_display(i)} for i in teachers],
        }).to_json_response()
    except Exception as exc:
        logger.exception(f'Error loading hostel resident options: {exc}')
        return ErrorResponse('Unable to load residents.', status_code=500).to_json_response()


def _save_model(request, model, object_id, assigner, success_message):
    try:
        obj = _scoped_object_or_new(request, model, object_id)
        if obj is None:
            return ErrorResponse('Record not found.', status_code=404).to_json_response()
        assigner(obj)
        _audit_fields(request, obj)
        obj.full_clean()
        obj.save()
        return SuccessResponse(success_message, data={'id': obj.id}).to_json_response()
    except (ValidationError, IntegrityError) as exc:
        return ErrorResponse(_validation_message(exc, 'Record could not be saved.')).to_json_response()
    except Exception as exc:
        logger.exception(f'Error saving hostel {model.__name__}: {exc}')
        return ErrorResponse('Unable to save hostel record.', status_code=500).to_json_response()


def _delete_model(request, model, object_id, success_message):
    obj = _scoped_for_request(request, model).filter(pk=object_id).first()
    if not obj:
        return ErrorResponse('Record not found.', status_code=404).to_json_response()
    obj.isDeleted = True
    if hasattr(obj, 'isActive'):
        obj.isActive = False
    _audit_fields(request, obj)
    obj.save()
    return SuccessResponse(success_message).to_json_response()


@login_required
def buildings_api(request):
    def assign(obj):
        obj.buildingCode = _clean(request.POST.get('buildingCode')) or ''
        obj.buildingName = _clean(request.POST.get('buildingName')) or ''
        obj.wardenName = _clean(request.POST.get('wardenName'))
        obj.wardenPhone = _clean(request.POST.get('wardenPhone'))
        obj.address = _clean(request.POST.get('address'))
        obj.isActive = _bool(request.POST.get('isActive', 'true'))
    return _save_model(request, HostelBuilding, request.POST.get('id'), assign, 'Hostel building saved successfully.')


@login_required
def building_detail_api(request):
    obj = _scoped_for_request(request, HostelBuilding).filter(pk=request.GET.get('id')).first()
    return SuccessResponse('Building loaded.', data=_serialize_common(obj, ['buildingCode', 'buildingName', 'wardenName', 'wardenPhone', 'address', 'isActive'])).to_json_response() if obj else ErrorResponse('Building not found.', 404).to_json_response()


@login_required
def delete_building_api(request):
    return _delete_model(request, HostelBuilding, request.POST.get('id'), 'Hostel building deleted successfully.')


@login_required
def floors_api(request):
    def assign(obj):
        obj.buildingID_id = _ensure_scoped_fk(request, HostelBuilding, request.POST.get('buildingID'), 'Building')
        obj.floorName = _clean(request.POST.get('floorName')) or ''
        obj.displayOrder = int(request.POST.get('displayOrder') or 0)
        obj.isActive = _bool(request.POST.get('isActive', 'true'))
    return _save_model(request, HostelFloor, request.POST.get('id'), assign, 'Hostel floor saved successfully.')


@login_required
def floor_detail_api(request):
    obj = _scoped_for_request(request, HostelFloor).filter(pk=request.GET.get('id')).first()
    return SuccessResponse('Floor loaded.', data=_serialize_common(obj, ['buildingID_id', 'floorName', 'displayOrder', 'isActive'])).to_json_response() if obj else ErrorResponse('Floor not found.', 404).to_json_response()


@login_required
def delete_floor_api(request):
    return _delete_model(request, HostelFloor, request.POST.get('id'), 'Hostel floor deleted successfully.')


@login_required
def room_types_api(request):
    def assign(obj):
        obj.name = _clean(request.POST.get('name')) or ''
        obj.capacity = int(request.POST.get('capacity') or 1)
        obj.defaultMonthlyFee = _decimal(request.POST.get('defaultMonthlyFee'))
        obj.description = _clean(request.POST.get('description'))
        obj.isActive = _bool(request.POST.get('isActive', 'true'))
    return _save_model(request, HostelRoomType, request.POST.get('id'), assign, 'Hostel room type saved successfully.')


@login_required
def room_type_detail_api(request):
    obj = _scoped_for_request(request, HostelRoomType).filter(pk=request.GET.get('id')).first()
    return SuccessResponse('Room type loaded.', data=_serialize_common(obj, ['name', 'capacity', 'defaultMonthlyFee', 'description', 'isActive'])).to_json_response() if obj else ErrorResponse('Room type not found.', 404).to_json_response()


@login_required
def delete_room_type_api(request):
    return _delete_model(request, HostelRoomType, request.POST.get('id'), 'Hostel room type deleted successfully.')


@login_required
def rooms_api(request):
    def assign(obj):
        obj.buildingID_id = _ensure_scoped_fk(request, HostelBuilding, request.POST.get('buildingID'), 'Building')
        obj.floorID_id = _ensure_scoped_fk(request, HostelFloor, request.POST.get('floorID'), 'Floor', optional=True)
        obj.roomTypeID_id = _ensure_scoped_fk(request, HostelRoomType, request.POST.get('roomTypeID'), 'Room type', optional=True)
        obj.roomNumber = _clean(request.POST.get('roomNumber')) or ''
        obj.capacity = int(request.POST.get('capacity') or 1)
        obj.monthlyFee = _decimal(request.POST.get('monthlyFee'))
        obj.notes = _clean(request.POST.get('notes'))
        obj.isActive = _bool(request.POST.get('isActive', 'true'))
    return _save_model(request, HostelRoom, request.POST.get('id'), assign, 'Hostel room saved successfully.')


@login_required
def room_detail_api(request):
    obj = _scoped_for_request(request, HostelRoom).filter(pk=request.GET.get('id')).first()
    return SuccessResponse('Room loaded.', data=_serialize_common(obj, ['buildingID_id', 'floorID_id', 'roomTypeID_id', 'roomNumber', 'capacity', 'monthlyFee', 'notes', 'isActive'])).to_json_response() if obj else ErrorResponse('Room not found.', 404).to_json_response()


@login_required
def delete_room_api(request):
    return _delete_model(request, HostelRoom, request.POST.get('id'), 'Hostel room deleted successfully.')


@login_required
def beds_api(request):
    def assign(obj):
        obj.roomID_id = _ensure_scoped_fk(request, HostelRoom, request.POST.get('roomID'), 'Room')
        obj.bedNumber = _clean(request.POST.get('bedNumber')) or ''
        obj.status = _clean(request.POST.get('status')) or 'available'
        obj.isActive = _bool(request.POST.get('isActive', 'true'))
    return _save_model(request, HostelBed, request.POST.get('id'), assign, 'Hostel bed saved successfully.')


@login_required
def bed_detail_api(request):
    obj = _scoped_for_request(request, HostelBed).filter(pk=request.GET.get('id')).first()
    return SuccessResponse('Bed loaded.', data=_serialize_common(obj, ['roomID_id', 'bedNumber', 'status', 'isActive'])).to_json_response() if obj else ErrorResponse('Bed not found.', 404).to_json_response()


@login_required
def delete_bed_api(request):
    return _delete_model(request, HostelBed, request.POST.get('id'), 'Hostel bed deleted successfully.')


@login_required
def admissions_api(request):
    def assign(obj):
        resident_type = _clean(request.POST.get('residentType')) or 'student'
        if resident_type not in {'student', 'teacher'}:
            raise ValidationError('Resident type is invalid.')
        obj.residentType = resident_type
        student_id = _clean(request.POST.get('studentID'))
        teacher_id = _clean(request.POST.get('teacherID'))
        if resident_type == 'student':
            if not Student.objects.filter(pk=student_id, schoolID_id=_school_id(request), sessionID_id=_session_id(request), isDeleted=False).exists():
                raise ValidationError('Student is invalid for current school/session.')
            obj.studentID_id = student_id
            obj.teacherID_id = None
        else:
            if not TeacherDetail.objects.filter(pk=teacher_id, schoolID_id=_school_id(request), sessionID_id=_session_id(request), isDeleted=False).exists():
                raise ValidationError('Teacher is invalid for current school/session.')
            obj.teacherID_id = teacher_id
            obj.studentID_id = None
        obj.applicationNo = _clean(request.POST.get('applicationNo')) or f'HADM-{timezone.now().strftime("%Y%m%d%H%M%S")}'
        obj.applicationDate = parse_date(request.POST.get('applicationDate') or '') or timezone.now().date()
        obj.preferredRoomTypeID_id = _ensure_scoped_fk(request, HostelRoomType, request.POST.get('preferredRoomTypeID'), 'Preferred room type', optional=True)
        obj.guardianConsent = _bool(request.POST.get('guardianConsent'))
        obj.emergencyContactName = _clean(request.POST.get('emergencyContactName'))
        obj.emergencyContactPhone = _clean(request.POST.get('emergencyContactPhone'))
        obj.medicalNotes = _clean(request.POST.get('medicalNotes'))
        obj.admissionFee = _decimal(request.POST.get('admissionFee'))
        obj.status = _clean(request.POST.get('status')) or 'applied'
        obj.approvedDate = parse_date(request.POST.get('approvedDate') or '') if _clean(request.POST.get('approvedDate')) else None
        obj.admissionDate = parse_date(request.POST.get('admissionDate') or '') if _clean(request.POST.get('admissionDate')) else None
        obj.notes = _clean(request.POST.get('notes'))
        obj.isActive = _bool(request.POST.get('isActive', 'true'))
    return _save_model(request, HostelAdmission, request.POST.get('id'), assign, 'Hostel admission saved successfully.')


@login_required
def admission_detail_api(request):
    obj = _scoped_for_request(request, HostelAdmission).filter(pk=request.GET.get('id')).first()
    return SuccessResponse('Admission loaded.', data=_serialize_common(obj, ['residentType', 'studentID_id', 'teacherID_id', 'applicationNo', 'applicationDate', 'preferredRoomTypeID_id', 'guardianConsent', 'emergencyContactName', 'emergencyContactPhone', 'medicalNotes', 'admissionFee', 'status', 'approvedDate', 'admissionDate', 'notes', 'isActive'])).to_json_response() if obj else ErrorResponse('Admission not found.', 404).to_json_response()


@login_required
def delete_admission_api(request):
    return _delete_model(request, HostelAdmission, request.POST.get('id'), 'Hostel admission deleted successfully.')


def _resolve_hostel_fee(request, building_id, room_type_id, room_id, fallback):
    mappings = _scoped_for_request(request, HostelFeeMapping).filter(isActive=True)
    candidates = [
        mappings.filter(roomID_id=room_id).first(),
        mappings.filter(roomID__isnull=True, roomTypeID_id=room_type_id, buildingID_id=building_id).first(),
        mappings.filter(roomID__isnull=True, roomTypeID_id=room_type_id, buildingID__isnull=True).first(),
        mappings.filter(roomID__isnull=True, roomTypeID__isnull=True, buildingID_id=building_id).first(),
    ]
    for mapping in candidates:
        if mapping:
            return mapping.monthlyFee
    return fallback


@login_required
def assignments_api(request):
    try:
        obj = _scoped_object_or_new(request, HostelAssignment, request.POST.get('id'))
        if obj is None:
            return ErrorResponse('Hostel assignment not found.', 404).to_json_response()
        old_bed_id = obj.bedID_id if obj.pk else None
        obj.admissionID_id = _ensure_scoped_fk(request, HostelAdmission, request.POST.get('admissionID'), 'Admission', optional=True)
        admission = None
        if obj.admissionID_id:
            admission = _scoped_for_request(request, HostelAdmission).filter(pk=obj.admissionID_id).first()
            if not admission:
                raise ValidationError('Admission is invalid for current school/session.')
            if admission.status in {'rejected', 'cancelled'}:
                raise ValidationError('Rejected or cancelled hostel applications cannot be allocated a bed.')
            resident_type = admission.residentType
            student_id = admission.studentID_id
            teacher_id = admission.teacherID_id
        else:
            resident_type = _clean(request.POST.get('residentType')) or 'student'
            if resident_type not in {'student', 'teacher'}:
                raise ValidationError('Resident type is invalid.')
            student_id = _clean(request.POST.get('studentID'))
            teacher_id = _clean(request.POST.get('teacherID'))
            if resident_type == 'student':
                if not Student.objects.filter(pk=student_id, schoolID_id=_school_id(request), sessionID_id=_session_id(request), isDeleted=False).exists():
                    raise ValidationError('Student is invalid for current school/session.')
            else:
                if not TeacherDetail.objects.filter(pk=teacher_id, schoolID_id=_school_id(request), sessionID_id=_session_id(request), isDeleted=False).exists():
                    raise ValidationError('Teacher is invalid for current school/session.')
        obj.residentType = resident_type
        if resident_type == 'student':
            obj.studentID_id = student_id
            obj.teacherID_id = None
        else:
            obj.teacherID_id = teacher_id
            obj.studentID_id = None
        existing_assignment = _active_assignment_for_resident(
            request,
            resident_type,
            student_id=student_id,
            teacher_id=teacher_id,
            exclude_id=obj.pk,
        )
        if existing_assignment:
            resident_label = 'teacher' if resident_type == 'teacher' else 'student'
            raise ValidationError(f'This {resident_label} already has an active hostel assignment. Edit the existing assignment or deactivate it first.')
        obj.buildingID_id = _ensure_scoped_fk(request, HostelBuilding, request.POST.get('buildingID'), 'Building')
        obj.roomID_id = _ensure_scoped_fk(request, HostelRoom, request.POST.get('roomID'), 'Room')
        obj.bedID_id = _ensure_scoped_fk(request, HostelBed, request.POST.get('bedID'), 'Bed')
        active_bed_assignment = _scoped_for_request(request, HostelAssignment).filter(isActive=True, bedID_id=obj.bedID_id).exclude(pk=obj.pk).first()
        if active_bed_assignment:
            raise ValidationError('Selected bed is already assigned. Choose an available bed or edit the existing assignment.')
        fee_mode = _clean(request.POST.get('feeMode')) or ('student_fee' if resident_type == 'student' else 'staff_receivable')
        if resident_type == 'student' and fee_mode in {'payroll_deduction', 'staff_receivable'}:
            fee_mode = 'student_fee'
        if resident_type == 'teacher' and fee_mode == 'student_fee':
            fee_mode = 'staff_receivable'
        obj.feeMode = fee_mode
        room = _scoped_for_request(request, HostelRoom).filter(pk=obj.roomID_id).first()
        obj.monthlyFee = _decimal(request.POST.get('monthlyFee')) or _resolve_hostel_fee(request, obj.buildingID_id, room.roomTypeID_id if room else None, obj.roomID_id, room.monthlyFee if room else Decimal('0.00'))
        obj.startDate = parse_date(request.POST.get('startDate') or '') if _clean(request.POST.get('startDate')) else None
        obj.endDate = parse_date(request.POST.get('endDate') or '') if _clean(request.POST.get('endDate')) else None
        obj.isActive = _bool(request.POST.get('isActive', 'true'))
        _audit_fields(request, obj)
        obj.full_clean()
        with transaction.atomic():
            obj.save()
            if obj.admissionID_id and obj.admissionID.status != 'admitted':
                obj.admissionID.status = 'admitted'
                obj.admissionID.admissionDate = obj.startDate or timezone.now().date()
                _audit_fields(request, obj.admissionID)
                obj.admissionID.save(update_fields=['status', 'admissionDate', 'schoolID', 'sessionID', 'updatedByUserID', 'lastEditedBy', 'lastUpdatedOn'])
            if old_bed_id and old_bed_id != obj.bedID_id:
                HostelBed.objects.filter(pk=old_bed_id).update(status='available')
            HostelBed.objects.filter(pk=obj.bedID_id).update(status='occupied' if obj.isActive else 'available')
        return SuccessResponse('Hostel assignment saved successfully.', data={'id': obj.id}).to_json_response()
    except (ValidationError, IntegrityError) as exc:
        return ErrorResponse(_validation_message(exc, 'Hostel assignment could not be saved.')).to_json_response()
    except Exception as exc:
        logger.exception(f'Error saving hostel assignment: {exc}')
        return ErrorResponse('Unable to save hostel assignment.', 500).to_json_response()


@login_required
def assignment_detail_api(request):
    obj = _scoped_for_request(request, HostelAssignment).filter(pk=request.GET.get('id')).first()
    return SuccessResponse('Assignment loaded.', data=_serialize_common(obj, ['admissionID_id', 'residentType', 'studentID_id', 'teacherID_id', 'buildingID_id', 'roomID_id', 'bedID_id', 'feeMode', 'monthlyFee', 'startDate', 'endDate', 'isActive'])).to_json_response() if obj else ErrorResponse('Assignment not found.', 404).to_json_response()


@login_required
def delete_assignment_api(request):
    obj = _scoped_for_request(request, HostelAssignment).filter(pk=request.POST.get('id')).first()
    if not obj:
        return ErrorResponse('Assignment not found.', 404).to_json_response()
    bed_id = obj.bedID_id
    obj.isDeleted = True
    obj.isActive = False
    _audit_fields(request, obj)
    obj.save()
    HostelBed.objects.filter(pk=bed_id).update(status='available')
    return SuccessResponse('Hostel assignment deleted successfully.').to_json_response()


@login_required
def fee_mappings_api(request):
    def assign(obj):
        obj.buildingID_id = _ensure_scoped_fk(request, HostelBuilding, request.POST.get('buildingID'), 'Building', optional=True)
        obj.roomTypeID_id = _ensure_scoped_fk(request, HostelRoomType, request.POST.get('roomTypeID'), 'Room type', optional=True)
        obj.roomID_id = _ensure_scoped_fk(request, HostelRoom, request.POST.get('roomID'), 'Room', optional=True)
        obj.monthlyFee = _decimal(request.POST.get('monthlyFee'))
        obj.effectiveFrom = parse_date(request.POST.get('effectiveFrom') or '') if _clean(request.POST.get('effectiveFrom')) else None
        obj.effectiveTo = parse_date(request.POST.get('effectiveTo') or '') if _clean(request.POST.get('effectiveTo')) else None
        obj.notes = _clean(request.POST.get('notes'))
        obj.isActive = _bool(request.POST.get('isActive', 'true'))
    return _save_model(request, HostelFeeMapping, request.POST.get('id'), assign, 'Hostel fee mapping saved successfully.')


@login_required
def fee_mapping_detail_api(request):
    obj = _scoped_for_request(request, HostelFeeMapping).filter(pk=request.GET.get('id')).first()
    return SuccessResponse('Fee mapping loaded.', data=_serialize_common(obj, ['buildingID_id', 'roomTypeID_id', 'roomID_id', 'monthlyFee', 'effectiveFrom', 'effectiveTo', 'notes', 'isActive'])).to_json_response() if obj else ErrorResponse('Fee mapping not found.', 404).to_json_response()


@login_required
def delete_fee_mapping_api(request):
    return _delete_model(request, HostelFeeMapping, request.POST.get('id'), 'Hostel fee mapping deleted successfully.')


@login_required
def generate_hostel_fee_records_api(request):
    try:
        month = int(request.POST.get('feeMonth') or timezone.now().month)
        year = int(request.POST.get('feeYear') or timezone.now().year)
        start, end = _period_dates(year, month)
        due_date = parse_date(request.POST.get('dueDate') or '') or end
        assignments = _scoped_for_request(request, HostelAssignment).select_related('studentID', 'teacherID', 'roomID', 'roomID__roomTypeID').filter(isActive=True)
        created_count = 0
        updated_count = 0
        with transaction.atomic():
            for assignment in assignments:
                amount = assignment.monthlyFee or Decimal('0.00')
                if assignment.residentType != 'student' or assignment.feeMode != 'student_fee':
                    amount = Decimal('0.00')
                record, created = HostelFeeRecord.objects.get_or_create(
                    assignmentID=assignment,
                    feeMonth=month,
                    feeYear=year,
                    isDeleted=False,
                    defaults={
                        'schoolID_id': _school_id(request),
                        'sessionID_id': _session_id(request),
                        'periodStartDate': start,
                        'periodEndDate': end,
                        'dueDate': due_date,
                        'grossAmount': amount,
                        'netAmount': amount,
                        'balanceAmount': amount,
                        'lastEditedBy': _user_label(request),
                        'updatedByUserID': request.user,
                    },
                )
                if created:
                    created_count += 1
                else:
                    record.periodStartDate = start
                    record.periodEndDate = end
                    record.dueDate = due_date
                    record.grossAmount = amount
                    _audit_fields(request, record)
                    record.save()
                    updated_count += 1
                _sync_hostel_student_charge(request, record)
        return SuccessResponse(f'Hostel fee records generated. Created {created_count}, updated {updated_count}.', data={'created': created_count, 'updated': updated_count}).to_json_response()
    except (ValueError, ValidationError) as exc:
        return ErrorResponse(_validation_message(exc, 'Unable to generate hostel fee records.')).to_json_response()
    except Exception as exc:
        logger.exception(f'Error generating hostel fee records: {exc}')
        return ErrorResponse('Unable to generate hostel fee records.', 500).to_json_response()


@login_required
def hostel_fee_record_detail_api(request):
    record = _scoped_for_request(request, HostelFeeRecord).select_related('assignmentID', 'assignmentID__studentID', 'assignmentID__teacherID', 'assignmentID__buildingID', 'assignmentID__roomID', 'assignmentID__bedID').filter(pk=request.GET.get('id')).first()
    return SuccessResponse('Hostel fee record loaded.', data=_fee_record_row(record)).to_json_response() if record else ErrorResponse('Hostel fee record not found.', 404).to_json_response()


@login_required
def record_hostel_fee_payment_api(request):
    try:
        record = _scoped_for_request(request, HostelFeeRecord).filter(pk=request.POST.get('id')).first()
        if not record:
            return ErrorResponse('Hostel fee record not found.', 404).to_json_response()
        if record.status in {'waived', 'cancelled'}:
            return ErrorResponse('Cannot record payment for waived or cancelled fee records.').to_json_response()
        record.paidAmount = _decimal(request.POST.get('paidAmount'))
        record.paymentDate = parse_date(request.POST.get('paymentDate') or '') or timezone.now().date()
        record.paymentMode = _clean(request.POST.get('paymentMode')) or 'Cash'
        record.referenceNo = _clean(request.POST.get('referenceNo'))
        record.notes = _clean(request.POST.get('notes'))
        _audit_fields(request, record)
        record.full_clean()
        record.save()
        charge = _sync_hostel_student_charge(request, record)
        if charge and record.paidAmount > 0:
            sync_payment_receipt(
                charge_obj=charge,
                school_id=_school_id(request),
                session_id=_session_id(request),
                amount_received=record.paidAmount,
                receipt_date=record.paymentDate,
                source_module='hostel_fee_receipt',
                source_record_id=record.id,
                payment_mode_code=(record.paymentMode or 'CASH').upper(),
                reference_no=record.referenceNo or '',
                notes=record.notes or f'Hostel fee payment for {record.resident_name}',
                user_obj=request.user,
            )
        elif record.paidAmount <= 0:
            clear_payment_receipt(school_id=_school_id(request), source_module='hostel_fee_receipt', source_record_id=record.id, user_obj=request.user)
        return SuccessResponse('Hostel fee payment recorded successfully.', data=_fee_record_row(record)).to_json_response()
    except ValidationError as exc:
        return ErrorResponse(_validation_message(exc, 'Payment could not be recorded.')).to_json_response()
    except Exception as exc:
        logger.exception(f'Error recording hostel fee payment: {exc}')
        return ErrorResponse('Unable to record hostel fee payment.', 500).to_json_response()


@login_required
def update_hostel_fee_status_api(request):
    record = _scoped_for_request(request, HostelFeeRecord).filter(pk=request.POST.get('id')).first()
    if not record:
        return ErrorResponse('Hostel fee record not found.', 404).to_json_response()
    status = request.POST.get('status')
    if status not in {'waived', 'cancelled'}:
        return ErrorResponse('Unsupported fee status action.').to_json_response()
    record.status = status
    if status == 'waived':
        record.discountAmount = record.grossAmount
        record.paidAmount = Decimal('0.00')
    if status == 'cancelled':
        record.paidAmount = Decimal('0.00')
    _audit_fields(request, record)
    record.save()
    _sync_hostel_student_charge(request, record)
    clear_payment_receipt(school_id=_school_id(request), source_module='hostel_fee_receipt', source_record_id=record.id, user_obj=request.user)
    return SuccessResponse('Hostel fee status updated successfully.', data=_fee_record_row(record)).to_json_response()


@login_required
def dashboard_summary(request):
    buildings_qs = _scoped_for_request(request, HostelBuilding).filter(isActive=True)
    rooms_qs = _scoped_for_request(request, HostelRoom).filter(isActive=True)
    assignments = _scoped_for_request(request, HostelAssignment).filter(isActive=True)
    beds = _scoped_for_request(request, HostelBed).filter(isActive=True)
    fees = _scoped_for_request(request, HostelFeeRecord)
    fee_summary = fees.aggregate(net=Sum('netAmount'), paid=Sum('paidAmount'), due=Sum('balanceAmount'))
    bed_counts = dict(beds.values_list('status').annotate(total=Count('id')))
    recent = assignments.select_related('studentID', 'teacherID', 'buildingID', 'roomID', 'bedID').order_by('-lastUpdatedOn')[:8]

    resident_counts = dict(assignments.values_list('residentType').annotate(total=Count('id')))
    admission_status_rows = list(
        _scoped_for_request(request, HostelAdmission)
        .filter(isActive=True)
        .values('status')
        .annotate(total=Count('id'))
        .order_by('status')
    )
    fee_status_rows = list(
        fees.values('status')
        .annotate(total=Count('id'), due=Sum('balanceAmount'))
        .order_by('status')
    )
    building_rows = list(
        buildings_qs.annotate(
            roomCount=Count('rooms', filter=Q(rooms__isDeleted=False, rooms__isActive=True), distinct=True),
            bedCount=Count('rooms__beds', filter=Q(rooms__beds__isDeleted=False, rooms__beds__isActive=True), distinct=True),
            occupiedCount=Count('rooms__beds', filter=Q(rooms__beds__isDeleted=False, rooms__beds__isActive=True, rooms__beds__status='occupied'), distinct=True),
            availableCount=Count('rooms__beds', filter=Q(rooms__beds__isDeleted=False, rooms__beds__isActive=True, rooms__beds__status='available'), distinct=True),
            maintenanceCount=Count('rooms__beds', filter=Q(rooms__beds__isDeleted=False, rooms__beds__isActive=True, rooms__beds__status='maintenance'), distinct=True),
        ).order_by('buildingName')
    )
    room_rows = list(
        rooms_qs.select_related('buildingID', 'floorID', 'roomTypeID')
        .annotate(
            bedCount=Count('beds', filter=Q(beds__isDeleted=False, beds__isActive=True), distinct=True),
            occupiedCount=Count('beds', filter=Q(beds__isDeleted=False, beds__isActive=True, beds__status='occupied'), distinct=True),
            availableCount=Count('beds', filter=Q(beds__isDeleted=False, beds__isActive=True, beds__status='available'), distinct=True),
            reservedCount=Count('beds', filter=Q(beds__isDeleted=False, beds__isActive=True, beds__status='reserved'), distinct=True),
            maintenanceCount=Count('beds', filter=Q(beds__isDeleted=False, beds__isActive=True, beds__status='maintenance'), distinct=True),
        )
        .order_by('buildingID__buildingName', 'floorID__displayOrder', 'roomNumber')[:80]
    )

    def percent(used, total):
        total = int(total or 0)
        if total <= 0:
            return 0
        return round((int(used or 0) / total) * 100)

    return SuccessResponse('Hostel dashboard loaded.', data={
        'buildings': buildings_qs.count(),
        'rooms': rooms_qs.count(),
        'beds': beds.count(),
        'occupiedBeds': bed_counts.get('occupied', 0),
        'availableBeds': bed_counts.get('available', 0),
        'admissions': _scoped_for_request(request, HostelAdmission).filter(status__in=['applied', 'approved', 'waitlisted', 'admitted']).count(),
        'activeResidents': assignments.count(),
        'feeNet': str(fee_summary.get('net') or Decimal('0.00')),
        'feePaid': str(fee_summary.get('paid') or Decimal('0.00')),
        'feeDue': str(fee_summary.get('due') or Decimal('0.00')),
        'recentResidents': [{'resident': _resident_display(i), 'residentType': i.get_residentType_display(), 'building': i.buildingID.buildingName, 'room': i.roomID.roomNumber, 'bed': i.bedID.bedNumber} for i in recent],
        'charts': {
            'bedStatusLabels': ['Available', 'Occupied', 'Reserved', 'Maintenance'],
            'bedStatusValues': [bed_counts.get('available', 0), bed_counts.get('occupied', 0), bed_counts.get('reserved', 0), bed_counts.get('maintenance', 0)],
            'residentTypeLabels': ['Students', 'Teachers'],
            'residentTypeValues': [resident_counts.get('student', 0), resident_counts.get('teacher', 0)],
            'admissionStatusLabels': [str(row.get('status') or 'unknown').replace('_', ' ').title() for row in admission_status_rows],
            'admissionStatusValues': [row.get('total') or 0 for row in admission_status_rows],
            'feeStatusLabels': [str(row.get('status') or 'unknown').replace('_', ' ').title() for row in fee_status_rows],
            'feeStatusValues': [float(row.get('due') or Decimal('0.00')) for row in fee_status_rows],
            'buildingLabels': [row.buildingName for row in building_rows],
            'buildingOccupiedValues': [row.occupiedCount for row in building_rows],
            'buildingAvailableValues': [row.availableCount for row in building_rows],
        },
        'buildingOccupancy': [
            {
                'id': row.id,
                'code': row.buildingCode,
                'name': row.buildingName,
                'warden': row.wardenName or '',
                'wardenPhone': row.wardenPhone or '',
                'rooms': row.roomCount,
                'beds': row.bedCount,
                'occupied': row.occupiedCount,
                'available': row.availableCount,
                'maintenance': row.maintenanceCount,
                'occupancyPercent': percent(row.occupiedCount, row.bedCount),
            }
            for row in building_rows
        ],
        'roomBoard': [
            {
                'id': row.id,
                'building': row.buildingID.buildingName if row.buildingID_id else 'N/A',
                'floor': row.floorID.floorName if row.floorID_id else 'No floor',
                'room': row.roomNumber,
                'roomType': row.roomTypeID.name if row.roomTypeID_id else 'General',
                'capacity': row.capacity,
                'beds': row.bedCount,
                'occupied': row.occupiedCount,
                'available': row.availableCount,
                'reserved': row.reservedCount,
                'maintenance': row.maintenanceCount,
                'occupancyPercent': percent(row.occupiedCount, row.bedCount or row.capacity),
            }
            for row in room_rows
        ],
        'roomBoardLimit': 80,
        'roomBoardTruncated': rooms_qs.count() > 80,
    }).to_json_response()


@login_required
def hostel_report_summary_api(request):
    return dashboard_summary(request)


@login_required
def resident_manifest_csv(request):
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="hostel-resident-manifest.csv"'
    writer = csv.writer(response)
    writer.writerow(['Resident Type', 'Resident', 'Building', 'Room', 'Bed', 'Start Date', 'Monthly Fee'])
    rows = _scoped_for_request(request, HostelAssignment).filter(isActive=True).select_related('studentID', 'teacherID', 'buildingID', 'roomID', 'bedID').order_by('buildingID__buildingName', 'roomID__roomNumber', 'bedID__bedNumber')
    for row in rows:
        writer.writerow([row.get_residentType_display(), row.resident_name, row.buildingID.buildingName, row.roomID.roomNumber, row.bedID.bedNumber, row.startDate or '', row.monthlyFee])
    return response
