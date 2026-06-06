import csv
import calendar
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.utils.html import escape
from django.utils.dateparse import parse_date, parse_time
from django_datatables_view.base_datatable_view import BaseDatatableView

from managementApp.models import Student, TeacherDetail
from financeApp.services import clear_payment_receipt, sync_payment_receipt, sync_student_charge
from managementApp.access_control import has_management_permission
from transportApp.models import (
    TransportAssignment,
    TransportDriver,
    TransportFeeMapping,
    TransportFeeRecord,
    TransportRoute,
    TransportStop,
    TransportVehicle,
)
from utils.custom_response import ErrorResponse, SuccessResponse
from utils.image_utils import avatar_image_html, optimize_uploaded_image, safe_image_url
from utils.logger import logger


def _current_session(request):
    return request.session.get('current_session', {}) or {}


def _school_id(request):
    return _current_session(request).get('SchoolID')


def _session_id(request):
    return _current_session(request).get('Id')


def _decimal(value):
    try:
        return Decimal(str(value or '0')).quantize(Decimal('0.01'))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal('0.00')


def _time(value):
    value = _clean_form_value(value)
    if not value:
        return None
    parsed = parse_time(value)
    if parsed:
        return parsed
    for fmt in ('%I:%M %p', '%I:%M%p', '%I %p', '%H:%M'):
        try:
            return datetime.strptime(value.upper(), fmt).time()
        except ValueError:
            continue
    raise ValueError(f'Invalid time value: {value}')


def _clean_form_value(value):
    if value is None:
        return None
    value = str(value).strip()
    if value.lower() in {'', 'undefined', 'null', 'none'}:
        return None
    return value


def _bool(value):
    return str(value).strip().lower() in {'1', 'true', 'yes', 'on', 'active'}


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


def _user_label(request):
    return request.user.get_full_name() or request.user.username or str(request.user.id)


def _audit_fields(request, obj):
    obj.schoolID_id = _school_id(request)
    obj.sessionID_id = _session_id(request)
    obj.updatedByUserID = request.user
    obj.lastEditedBy = _user_label(request)


def _status_pill(active):
    label = 'Active' if active else 'Inactive'
    color = 'green' if active else 'grey'
    return f'<span class="ui {color} tiny label">{label}</span>'


def _fee_status_pill(status):
    labels = {
        'pending': ('Pending', 'orange'),
        'partial': ('Partial', 'yellow'),
        'paid': ('Paid', 'green'),
        'waived': ('Waived', 'blue'),
        'cancelled': ('Cancelled', 'grey'),
    }
    label, color = labels.get(status, (status or 'N/A', 'grey'))
    return f'<span class="ui {color} tiny label">{escape(label)}</span>'


def _transport_button_allowed(request, action):
    return has_management_permission(request.user, 'transport', action)


def _actions(edit_fn, delete_fn, obj_id, request=None):
    buttons = []
    if not request or _transport_button_allowed(request, 'edit'):
        buttons.append(
            f'<button data-tooltip="Edit" data-position="left center" data-variation="mini" '
            f'onclick="{edit_fn}({obj_id})" class="ui circular green icon button"><i class="pencil icon"></i></button>'
        )
    if not request or _transport_button_allowed(request, 'delete'):
        buttons.append(
            f'<button data-tooltip="Delete" data-position="left center" data-variation="mini" '
            f'onclick="{delete_fn}({obj_id})" class="ui circular red icon button" style="margin-left:3px;">'
            f'<i class="trash alternate icon"></i></button>'
        )
    return ''.join(buttons) or '<span class="ui tiny grey label">View only</span>'


def _dt_actions(edit_fn, delete_fn, obj_id, request=None):
    return _actions(edit_fn, delete_fn, obj_id, request=request)


def _route_dt_actions(obj_id, request=None):
    buttons = [
        f'<button data-inverted="" data-tooltip="View Stops" data-position="left center" '
        f'data-variation="mini" style="font-size:10px;" onclick="viewRouteDetail({obj_id})" '
        f'class="ui circular teal icon button"><i class="eye icon"></i></button>'
    ]
    if not request or _transport_button_allowed(request, 'edit'):
        buttons.append(
            f'<button data-inverted="" data-tooltip="Edit Detail" data-position="left center" '
            f'data-variation="mini" style="font-size:10px; margin-left:3px;" onclick="editRoute({obj_id})" '
            f'class="ui circular facebook icon button green"><i class="pen icon"></i></button>'
        )
    if not request or _transport_button_allowed(request, 'delete'):
        buttons.append(
            f'<button data-inverted="" data-tooltip="Delete" data-position="left center" '
            f'data-variation="mini" style="font-size:10px; margin-left:3px;" onclick="confirmDeleteRoute({obj_id})" '
            f'class="ui circular youtube icon button"><i class="trash alternate icon"></i></button>'
        )
    return ''.join(buttons)


def _fee_record_actions(obj_id, request=None):
    if request and not _transport_button_allowed(request, 'edit'):
        return '<span class="ui tiny grey label">View only</span>'
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


class TransportRouteListJson(BaseDatatableView):
    order_columns = ['routeCode', 'routeName', 'startPoint', 'endPoint', 'isActive', 'lastUpdatedOn']

    def get_initial_queryset(self):
        return _scoped_for_request(self.request, TransportRoute).only(
            'id', 'routeCode', 'routeName', 'startPoint', 'endPoint', 'isActive', 'lastUpdatedOn'
        )

    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(routeCode__icontains=search) | Q(routeName__icontains=search)
                | Q(startPoint__icontains=search) | Q(endPoint__icontains=search)
            )
        return qs

    def prepare_results(self, qs):
        return [[
            escape(item.routeCode),
            escape(item.routeName),
            escape(item.startPoint or 'N/A'),
            escape(item.endPoint or 'N/A'),
            _status_pill(item.isActive),
            escape(item.lastUpdatedOn.strftime('%d-%m-%Y %I:%M %p') if item.lastUpdatedOn else 'N/A'),
            _route_dt_actions(item.id, request=self.request),
        ] for item in qs]


class TransportStopListJson(BaseDatatableView):
    order_columns = ['routeID__routeName', 'stopName', 'pickupTime', 'dropTime', 'monthlyFee', 'displayOrder', 'isActive']

    def get_initial_queryset(self):
        qs = _scoped_for_request(self.request, TransportStop).select_related('routeID')
        route_id = self.request.GET.get('routeID')
        if route_id:
            qs = qs.filter(routeID_id=route_id)
        return qs

    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(Q(routeID__routeName__icontains=search) | Q(stopName__icontains=search))
        return qs

    def prepare_results(self, qs):
        return [[
            escape(item.routeID.routeName if item.routeID else 'N/A'),
            escape(item.stopName),
            escape(item.pickupTime.strftime('%I:%M %p') if item.pickupTime else 'N/A'),
            escape(item.dropTime.strftime('%I:%M %p') if item.dropTime else 'N/A'),
            escape(str(item.monthlyFee)),
            escape(item.displayOrder),
            _status_pill(item.isActive),
            _dt_actions('editStop', 'confirmDeleteStop', item.id, request=self.request),
        ] for item in qs]


class TransportDriverListJson(BaseDatatableView):
    order_columns = ['photo', 'name', 'phoneNumber', 'licenseNumber', 'licenseExpiryDate', 'isActive']

    def get_initial_queryset(self):
        return _scoped_for_request(self.request, TransportDriver)

    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(Q(name__icontains=search) | Q(phoneNumber__icontains=search) | Q(licenseNumber__icontains=search))
        return qs

    def prepare_results(self, qs):
        return [[
            avatar_image_html(item.photo),
            escape(item.name),
            escape(item.phoneNumber or 'N/A'),
            escape(item.licenseNumber or 'N/A'),
            escape(item.licenseExpiryDate.strftime('%d-%m-%Y') if item.licenseExpiryDate else 'N/A'),
            _status_pill(item.isActive),
            _dt_actions('editDriver', 'confirmDeleteDriver', item.id, request=self.request),
        ] for item in qs]


class TransportFeeMappingListJson(BaseDatatableView):
    order_columns = ['routeID__routeName', 'stopID__stopName', 'assigneeType', 'feeMode', 'monthlyFee', 'effectiveFrom', 'effectiveTo', 'isActive']

    def get_initial_queryset(self):
        qs = _scoped_for_request(self.request, TransportFeeMapping).select_related('routeID', 'stopID')
        route_id = self.request.GET.get('routeID')
        if route_id:
            qs = qs.filter(routeID_id=route_id)
        return qs

    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(routeID__routeName__icontains=search) | Q(routeID__routeCode__icontains=search)
                | Q(stopID__stopName__icontains=search) | Q(assigneeType__icontains=search)
                | Q(feeMode__icontains=search)
            )
        return qs

    def prepare_results(self, qs):
        return [[
            escape(item.routeID.routeName if item.routeID else 'N/A'),
            escape(item.stopID.stopName if item.stopID else 'Route Default'),
            escape(item.get_assigneeType_display()),
            escape(item.get_feeMode_display()),
            escape(str(item.monthlyFee)),
            escape(item.effectiveFrom.strftime('%d-%m-%Y') if item.effectiveFrom else 'N/A'),
            escape(item.effectiveTo.strftime('%d-%m-%Y') if item.effectiveTo else 'N/A'),
            _status_pill(item.isActive),
            _dt_actions('editFeeMapping', 'confirmDeleteFeeMapping', item.id, request=self.request),
        ] for item in qs]


class TransportVehicleListJson(BaseDatatableView):
    order_columns = ['vehicleNumber', 'vehicleType', 'capacity', 'driverID__name', 'routeID__routeName', 'isActive']

    def get_initial_queryset(self):
        return _scoped_for_request(self.request, TransportVehicle).select_related('driverID', 'routeID')

    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(vehicleNumber__icontains=search) | Q(vehicleType__icontains=search)
                | Q(driverID__name__icontains=search) | Q(routeID__routeName__icontains=search)
            )
        return qs

    def prepare_results(self, qs):
        return [[
            escape(item.vehicleNumber),
            escape(item.get_vehicleType_display()),
            escape(item.capacity),
            escape(item.driverID.name if item.driverID else 'N/A'),
            escape(item.routeID.routeName if item.routeID else 'N/A'),
            _status_pill(item.isActive),
            _dt_actions('editVehicle', 'confirmDeleteVehicle', item.id, request=self.request),
        ] for item in qs]


class TransportAssignmentListJson(BaseDatatableView):
    order_columns = ['assigneeType', 'studentID__name', 'routeID__routeName', 'pickupStopID__stopName', 'dropStopID__stopName', 'vehicleID__vehicleNumber', 'monthlyFee', 'feeMode', 'isActive']

    def get_initial_queryset(self):
        qs = _scoped_for_request(self.request, TransportAssignment).select_related(
            'studentID', 'teacherID', 'routeID', 'pickupStopID', 'dropStopID', 'vehicleID'
        )
        assignee_type = self.request.GET.get('assigneeType')
        if assignee_type:
            qs = qs.filter(assigneeType=assignee_type)
        return qs

    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(assigneeType__icontains=search) | Q(studentID__name__icontains=search)
                | Q(teacherID__name__icontains=search) | Q(routeID__routeName__icontains=search)
                | Q(pickupStopID__stopName__icontains=search) | Q(dropStopID__stopName__icontains=search)
                | Q(vehicleID__vehicleNumber__icontains=search) | Q(feeMode__icontains=search)
            )
        return qs

    def prepare_results(self, qs):
        return [[
            escape(item.get_assigneeType_display()),
            escape(item.assignee_name),
            escape(item.routeID.routeName if item.routeID else 'N/A'),
            escape(item.pickupStopID.stopName if item.pickupStopID else 'N/A'),
            escape(item.dropStopID.stopName if item.dropStopID else 'N/A'),
            escape(item.vehicleID.vehicleNumber if item.vehicleID else 'N/A'),
            escape(str(item.monthlyFee)),
            escape(item.get_feeMode_display()),
            _status_pill(item.isActive),
            _dt_actions('editAssignment', 'confirmDeleteAssignment', item.id, request=self.request),
        ] for item in qs]


class TransportFeeRecordListJson(BaseDatatableView):
    order_columns = ['feeYear', 'feeMonth', 'assignmentID__assigneeType', 'assignmentID__studentID__name', 'assignmentID__routeID__routeName', 'netAmount', 'paidAmount', 'balanceAmount', 'status', 'dueDate']

    def get_initial_queryset(self):
        qs = _scoped_for_request(self.request, TransportFeeRecord).select_related(
            'assignmentID', 'assignmentID__studentID', 'assignmentID__teacherID', 'assignmentID__routeID',
            'assignmentID__pickupStopID', 'assignmentID__dropStopID', 'financeChargeID',
        )
        fee_month = self.request.GET.get('feeMonth')
        fee_year = self.request.GET.get('feeYear')
        status = self.request.GET.get('status')
        assignee_type = self.request.GET.get('assigneeType')
        if fee_month:
            qs = qs.filter(feeMonth=fee_month)
        if fee_year:
            qs = qs.filter(feeYear=fee_year)
        if status:
            qs = qs.filter(status=status)
        if assignee_type:
            qs = qs.filter(assignmentID__assigneeType=assignee_type)
        return qs

    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(assignmentID__studentID__name__icontains=search)
                | Q(assignmentID__teacherID__name__icontains=search)
                | Q(assignmentID__routeID__routeName__icontains=search)
                | Q(referenceNo__icontains=search)
            )
        return qs

    def prepare_results(self, qs):
        rows = []
        for item in qs:
            assignment = item.assignmentID
            rows.append([
                escape(f'{calendar.month_abbr[item.feeMonth]} {item.feeYear}'),
                escape(assignment.get_assigneeType_display() if assignment else 'N/A'),
                escape(item.assignee_name),
                escape(assignment.routeID.routeName if assignment and assignment.routeID else 'N/A'),
                escape(assignment.pickupStopID.stopName if assignment and assignment.pickupStopID else 'N/A'),
                escape(str(item.netAmount)),
                escape(str(item.paidAmount)),
                escape(str(item.balanceAmount)),
                _fee_status_pill(item.status),
                escape(item.dueDate.strftime('%d-%m-%Y') if item.dueDate else 'N/A'),
                _fee_record_actions(item.id, request=self.request),
            ])
        return rows


def _route_row(route, request=None):
    return {
        'id': route.id,
        'routeCode': route.routeCode,
        'routeName': route.routeName,
        'startPoint': route.startPoint or 'N/A',
        'endPoint': route.endPoint or 'N/A',
        'status': _status_pill(route.isActive),
        'updatedOn': route.lastUpdatedOn.strftime('%d-%m-%Y %I:%M %p') if route.lastUpdatedOn else '',
        'actions': _actions('editRoute', 'confirmDeleteRoute', route.id, request=request),
    }


def _stop_row(stop, request=None):
    return {
        'id': stop.id,
        'route': stop.routeID.routeName if stop.routeID else 'N/A',
        'stopName': stop.stopName,
        'pickupTime': stop.pickupTime.strftime('%I:%M %p') if stop.pickupTime else 'N/A',
        'dropTime': stop.dropTime.strftime('%I:%M %p') if stop.dropTime else 'N/A',
        'monthlyFee': str(stop.monthlyFee),
        'displayOrder': stop.displayOrder,
        'status': _status_pill(stop.isActive),
        'actions': _actions('editStop', 'confirmDeleteStop', stop.id, request=request),
    }


def _driver_row(driver, request=None):
    return {
        'id': driver.id,
        'photoUrl': safe_image_url(driver.photo, fallback_path='images/add_photo.svg'),
        'name': driver.name,
        'phoneNumber': driver.phoneNumber or 'N/A',
        'licenseNumber': driver.licenseNumber or 'N/A',
        'licenseExpiryDate': driver.licenseExpiryDate.strftime('%Y-%m-%d') if driver.licenseExpiryDate else 'N/A',
        'status': _status_pill(driver.isActive),
        'actions': _actions('editDriver', 'confirmDeleteDriver', driver.id, request=request),
    }


def _fee_mapping_row(mapping, request=None):
    return {
        'id': mapping.id,
        'route': mapping.routeID.routeName if mapping.routeID else 'N/A',
        'stop': mapping.stopID.stopName if mapping.stopID else 'Route Default',
        'assigneeType': mapping.get_assigneeType_display(),
        'feeMode': mapping.get_feeMode_display(),
        'monthlyFee': str(mapping.monthlyFee),
        'effectiveFrom': mapping.effectiveFrom.strftime('%Y-%m-%d') if mapping.effectiveFrom else '',
        'effectiveTo': mapping.effectiveTo.strftime('%Y-%m-%d') if mapping.effectiveTo else '',
        'status': _status_pill(mapping.isActive),
        'actions': _actions('editFeeMapping', 'confirmDeleteFeeMapping', mapping.id, request=request),
    }


def _vehicle_row(vehicle, request=None):
    return {
        'id': vehicle.id,
        'vehicleNumber': vehicle.vehicleNumber,
        'vehicleType': vehicle.get_vehicleType_display(),
        'capacity': vehicle.capacity,
        'driver': vehicle.driverID.name if vehicle.driverID else 'N/A',
        'route': vehicle.routeID.routeName if vehicle.routeID else 'N/A',
        'status': _status_pill(vehicle.isActive),
        'actions': _actions('editVehicle', 'confirmDeleteVehicle', vehicle.id, request=request),
    }


def _assignment_row(assignment, request=None):
    return {
        'id': assignment.id,
        'assigneeType': assignment.get_assigneeType_display(),
        'assigneeName': assignment.assignee_name,
        'route': assignment.routeID.routeName if assignment.routeID else 'N/A',
        'pickupStop': assignment.pickupStopID.stopName if assignment.pickupStopID else 'N/A',
        'dropStop': assignment.dropStopID.stopName if assignment.dropStopID else 'N/A',
        'vehicle': assignment.vehicleID.vehicleNumber if assignment.vehicleID else 'N/A',
        'monthlyFee': str(assignment.monthlyFee),
        'feeMode': assignment.get_feeMode_display(),
        'status': _status_pill(assignment.isActive),
        'actions': _actions('editAssignment', 'confirmDeleteAssignment', assignment.id, request=request),
    }


def _assignment_manifest_row(assignment):
    student = assignment.studentID
    teacher = assignment.teacherID
    phone = ''
    class_name = ''
    roll = ''
    if assignment.assigneeType == 'student' and student:
        phone = student.phoneNumber or ''
        class_name = student.standardID.name if student.standardID else ''
        roll = student.roll or ''
    elif assignment.assigneeType == 'teacher' and teacher:
        phone = teacher.phoneNumber or ''
        class_name = teacher.currentPosition or teacher.staffType or ''
    return {
        'id': assignment.id,
        'type': assignment.get_assigneeType_display(),
        'name': assignment.assignee_name,
        'classOrRole': class_name or 'N/A',
        'roll': roll or 'N/A',
        'phone': phone or 'N/A',
        'route': assignment.routeID.routeName if assignment.routeID else 'N/A',
        'pickupStop': assignment.pickupStopID.stopName if assignment.pickupStopID else 'N/A',
        'dropStop': assignment.dropStopID.stopName if assignment.dropStopID else 'N/A',
        'vehicle': assignment.vehicleID.vehicleNumber if assignment.vehicleID else 'N/A',
        'tripType': assignment.get_tripType_display(),
        'feeMode': assignment.get_feeMode_display(),
        'monthlyFee': str(assignment.monthlyFee),
        'startDate': assignment.startDate.strftime('%d-%m-%Y') if assignment.startDate else 'N/A',
        'endDate': assignment.endDate.strftime('%d-%m-%Y') if assignment.endDate else 'N/A',
        'status': 'Active' if assignment.isActive else 'Inactive',
    }


def _scoped(model):
    return model.objects.filter(isDeleted=False)


def _scoped_for_request(request, model):
    return _scoped(model).filter(schoolID_id=_school_id(request), sessionID_id=_session_id(request))


def _scoped_object_or_new(request, model, object_id):
    object_id = _clean_form_value(object_id)
    if object_id:
        return _scoped_for_request(request, model).filter(pk=object_id).first()
    return model()


def _ensure_scoped_fk(request, model, object_id, label, optional=False):
    object_id = _clean_form_value(object_id)
    if not object_id:
        if optional:
            return None
        raise ValidationError(f'{label} is required.')
    if not _scoped_for_request(request, model).filter(pk=object_id).exists():
        raise ValidationError(f'{label} is invalid for current school/session.')
    return object_id


def _ensure_scoped_assignee(request, assignee_type, student_id=None, teacher_id=None):
    student_id = _clean_form_value(student_id)
    teacher_id = _clean_form_value(teacher_id)
    if assignee_type == 'student':
        if not student_id or not Student.objects.filter(pk=student_id, schoolID_id=_school_id(request), sessionID_id=_session_id(request), isDeleted=False).exists():
            raise ValidationError('Student is invalid for current school/session.')
    if assignee_type == 'teacher':
        if not teacher_id or not TeacherDetail.objects.filter(pk=teacher_id, schoolID_id=_school_id(request), sessionID_id=_session_id(request), isDeleted=False).exists():
            raise ValidationError('Teacher is invalid for current school/session.')


def _period_dates(year, month):
    year = int(year)
    month = int(month)
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last_day)


def _sync_transport_student_charge(request, record):
    assignment = record.assignmentID
    if not assignment or assignment.assigneeType != 'student' or not assignment.studentID_id:
        return None
    charge = sync_student_charge(
        student_obj=assignment.studentID,
        school_id=_school_id(request),
        session_id=_session_id(request),
        fee_head_code='TRANSPORT_FEE',
        amount=record.netAmount if record.status not in {'waived', 'cancelled'} else Decimal('0.00'),
        title=f'Transport Fee - {calendar.month_name[record.feeMonth]} {record.feeYear}',
        description=f'Transport fee for {assignment.routeID.routeName if assignment.routeID else "route"}',
        charge_date=record.periodStartDate,
        due_date=record.dueDate or record.periodEndDate,
        source_module='transport_fee_record',
        source_record_id=record.id,
        standard_obj=assignment.studentID.standardID if assignment.studentID else None,
        user_obj=request.user,
    )
    if charge and record.financeChargeID_id != charge.id:
        record.financeChargeID = charge
        record.save(update_fields=['financeChargeID', 'lastUpdatedOn'])
    return charge


def _fee_record_row(record):
    assignment = record.assignmentID
    return {
        'id': record.id,
        'period': f'{calendar.month_abbr[record.feeMonth]} {record.feeYear}',
        'assigneeType': assignment.get_assigneeType_display() if assignment else 'N/A',
        'assigneeName': record.assignee_name,
        'route': assignment.routeID.routeName if assignment and assignment.routeID else 'N/A',
        'pickupPoint': assignment.pickupStopID.stopName if assignment and assignment.pickupStopID else 'N/A',
        'netAmount': str(record.netAmount),
        'paidAmount': str(record.paidAmount),
        'balanceAmount': str(record.balanceAmount),
        'status': record.status,
        'statusLabel': record.get_status_display(),
        'dueDate': record.dueDate.strftime('%Y-%m-%d') if record.dueDate else '',
        'paymentDate': record.paymentDate.strftime('%Y-%m-%d') if record.paymentDate else '',
        'paymentMode': record.paymentMode or '',
        'referenceNo': record.referenceNo or '',
        'notes': record.notes or '',
        'financeChargeID': record.financeChargeID_id,
    }


def _assignment_report_queryset(request):
    qs = _scoped_for_request(request, TransportAssignment).select_related(
        'studentID__standardID', 'teacherID', 'routeID', 'pickupStopID', 'dropStopID', 'vehicleID'
    )
    route_id = request.GET.get('routeID')
    vehicle_id = request.GET.get('vehicleID')
    assignee_type = request.GET.get('assigneeType')
    active_only = request.GET.get('activeOnly', 'true')
    if route_id:
        qs = qs.filter(routeID_id=route_id)
    if vehicle_id:
        qs = qs.filter(vehicleID_id=vehicle_id)
    if assignee_type:
        qs = qs.filter(assigneeType=assignee_type)
    if _bool(active_only):
        qs = qs.filter(isActive=True)
    return qs.order_by('routeID__routeCode', 'vehicleID__vehicleNumber', 'assigneeType', 'studentID__name', 'teacherID__name')


@login_required
def dashboard_summary(request):
    try:
        today = date.today()
        alert_until = today + timedelta(days=30)
        routes = list(_scoped_for_request(request, TransportRoute).filter(isActive=True).order_by('routeCode'))
        active_vehicles = list(
            _scoped_for_request(request, TransportVehicle)
            .filter(isActive=True)
            .select_related('routeID', 'driverID')
            .order_by('vehicleNumber')
        )
        active_drivers = list(_scoped_for_request(request, TransportDriver).filter(isActive=True).order_by('name'))
        active_assignments = list(
            _scoped_for_request(request, TransportAssignment)
            .filter(isActive=True)
            .select_related('routeID', 'vehicleID', 'studentID', 'teacherID', 'pickupStopID', 'dropStopID')
        )

        total_capacity = sum(vehicle.capacity or 0 for vehicle in active_vehicles)
        occupied = len(active_assignments)
        student_assignments = sum(1 for item in active_assignments if item.assigneeType == 'student')
        staff_assignments = sum(1 for item in active_assignments if item.assigneeType == 'teacher')
        assigned_driver_ids = {vehicle.driverID_id for vehicle in active_vehicles if vehicle.driverID_id}
        active_driver_ids = {driver.id for driver in active_drivers}
        unassigned_drivers = max(len(active_driver_ids - assigned_driver_ids), 0)
        vehicles_without_route = sum(1 for vehicle in active_vehicles if not vehicle.routeID_id)
        vehicles_without_driver = sum(1 for vehicle in active_vehicles if not vehicle.driverID_id)

        current_fee_records = list(
            _scoped_for_request(request, TransportFeeRecord)
            .filter(feeMonth=today.month, feeYear=today.year)
            .select_related('assignmentID', 'assignmentID__routeID', 'assignmentID__studentID', 'assignmentID__teacherID')
        )
        fee_net = sum((record.netAmount or Decimal('0.00')) for record in current_fee_records)
        fee_paid = sum((record.paidAmount or Decimal('0.00')) for record in current_fee_records)
        fee_due = sum((record.balanceAmount or Decimal('0.00')) for record in current_fee_records)
        fee_pending = sum(1 for record in current_fee_records if record.status in {'pending', 'partial'})

        route_rows = []
        for route in routes:
            route_vehicles = [vehicle for vehicle in active_vehicles if vehicle.routeID_id == route.id]
            route_assignments = [assignment for assignment in active_assignments if assignment.routeID_id == route.id]
            capacity = sum(vehicle.capacity or 0 for vehicle in route_vehicles)
            assigned = len(route_assignments)
            occupancy = round((assigned / capacity) * 100, 1) if capacity else 0
            route_rows.append({
                'id': route.id,
                'code': route.routeCode,
                'name': route.routeName,
                'vehicles': len(route_vehicles),
                'assigned': assigned,
                'capacity': capacity,
                'vacant': max(capacity - assigned, 0),
                'overCapacity': max(assigned - capacity, 0),
                'occupancyPercent': occupancy,
            })
        route_rows = sorted(route_rows, key=lambda row: (row['overCapacity'] > 0, row['occupancyPercent'], row['assigned']), reverse=True)[:6]

        vehicle_rows = []
        for vehicle in active_vehicles:
            assigned = sum(1 for assignment in active_assignments if assignment.vehicleID_id == vehicle.id)
            capacity = vehicle.capacity or 0
            vehicle_rows.append({
                'id': vehicle.id,
                'vehicleNumber': vehicle.vehicleNumber,
                'route': vehicle.routeID.routeName if vehicle.routeID else 'No Route',
                'driver': vehicle.driverID.name if vehicle.driverID else 'No Driver',
                'assigned': assigned,
                'capacity': capacity,
                'vacant': max(capacity - assigned, 0),
                'overCapacity': max(assigned - capacity, 0),
                'occupancyPercent': round((assigned / capacity) * 100, 1) if capacity else 0,
            })
        vehicle_rows = sorted(vehicle_rows, key=lambda row: (row['overCapacity'] > 0, row['occupancyPercent'], row['assigned']), reverse=True)[:6]

        alerts = []

        def add_expiry_alert(kind, title, due_date, target):
            if not due_date or due_date > alert_until:
                return
            days = (due_date - today).days
            if days < 0:
                status = f'Expired {abs(days)} days ago'
                tone = 'danger'
            elif days == 0:
                status = 'Due today'
                tone = 'danger'
            elif days <= 7:
                status = f'Due in {days} days'
                tone = 'warning'
            else:
                status = f'Due in {days} days'
                tone = 'notice'
            alerts.append({
                'kind': kind,
                'title': title,
                'target': target,
                'date': due_date.strftime('%d-%m-%Y'),
                'status': status,
                'tone': tone,
                'sortDate': due_date.isoformat(),
            })

        for driver in active_drivers:
            add_expiry_alert('License', 'Driver license', driver.licenseExpiryDate, driver.name)
        for vehicle in active_vehicles:
            add_expiry_alert('Registration', 'Registration expiry', vehicle.registrationExpiryDate, vehicle.vehicleNumber)
            add_expiry_alert('Insurance', 'Insurance expiry', vehicle.insuranceExpiryDate, vehicle.vehicleNumber)
            add_expiry_alert('Pollution', 'Pollution expiry', vehicle.pollutionExpiryDate, vehicle.vehicleNumber)
        alerts = sorted(alerts, key=lambda item: item['sortDate'])[:8]

        data = {
            'summary': {
                'routes': _scoped_for_request(request, TransportRoute).count(),
                'activeRoutes': len(routes),
                'stops': _scoped_for_request(request, TransportStop).count(),
                'vehicles': _scoped_for_request(request, TransportVehicle).count(),
                'activeVehicles': len(active_vehicles),
                'drivers': _scoped_for_request(request, TransportDriver).count(),
                'activeDrivers': len(active_drivers),
                'assignments': _scoped_for_request(request, TransportAssignment).count(),
                'activeAssignments': occupied,
                'studentAssignments': student_assignments,
                'staffAssignments': staff_assignments,
                'totalCapacity': total_capacity,
                'vacantSeats': max(total_capacity - occupied, 0),
                'overCapacity': max(occupied - total_capacity, 0),
                'occupancyPercent': round((occupied / total_capacity) * 100, 1) if total_capacity else 0,
                'vehiclesWithoutRoute': vehicles_without_route,
                'vehiclesWithoutDriver': vehicles_without_driver,
                'unassignedDrivers': unassigned_drivers,
            },
            'feeSummary': {
                'period': f'{calendar.month_name[today.month]} {today.year}',
                'records': len(current_fee_records),
                'pendingRecords': fee_pending,
                'netAmount': str(fee_net),
                'paidAmount': str(fee_paid),
                'dueAmount': str(fee_due),
                'collectionPercent': float(round((fee_paid / fee_net) * 100, 1)) if fee_net else 0,
            },
            'alerts': alerts,
            'routes': route_rows,
            'vehicles': vehicle_rows,
            'generatedAt': timezone.now().strftime('%d-%m-%Y %I:%M %p'),
        }
        logger.info(f'Transport dashboard summary fetched school={_school_id(request)} session={_session_id(request)}')
        return SuccessResponse('Transport dashboard loaded.', data=data).to_json_response()
    except Exception as exc:
        logger.exception(f'Error loading transport dashboard summary: {exc}')
        return ErrorResponse('Unable to load transport dashboard.', status_code=500).to_json_response()


@login_required
def transport_report_summary_api(request):
    try:
        route_id = request.GET.get('routeID')
        vehicle_id = request.GET.get('vehicleID')
        routes_qs = _scoped_for_request(request, TransportRoute).filter(isActive=True).order_by('routeCode')
        vehicles_qs = _scoped_for_request(request, TransportVehicle).filter(isActive=True).select_related('routeID', 'driverID').order_by('vehicleNumber')
        if route_id:
            routes_qs = routes_qs.filter(id=route_id)
            vehicles_qs = vehicles_qs.filter(routeID_id=route_id)
        if vehicle_id:
            vehicles_qs = vehicles_qs.filter(id=vehicle_id)
        vehicles = list(vehicles_qs)
        if vehicle_id and not route_id:
            vehicle_route_ids = [vehicle.routeID_id for vehicle in vehicles if vehicle.routeID_id]
            routes_qs = routes_qs.filter(id__in=vehicle_route_ids)
        routes = list(routes_qs)
        assignments = list(_assignment_report_queryset(request))
        active_assignments = [assignment for assignment in assignments if assignment.isActive]

        route_rows = []
        for route in routes:
            route_vehicles = [vehicle for vehicle in vehicles if vehicle.routeID_id == route.id]
            route_assignments = [assignment for assignment in active_assignments if assignment.routeID_id == route.id]
            capacity = sum(vehicle.capacity or 0 for vehicle in route_vehicles)
            occupied = len(route_assignments)
            route_rows.append({
                'routeID': route.id,
                'routeCode': route.routeCode,
                'routeName': route.routeName,
                'vehicles': len(route_vehicles),
                'capacity': capacity,
                'assigned': occupied,
                'students': len([item for item in route_assignments if item.assigneeType == 'student']),
                'teachers': len([item for item in route_assignments if item.assigneeType == 'teacher']),
                'vacant': max(capacity - occupied, 0),
                'overCapacity': max(occupied - capacity, 0) if capacity else occupied,
                'occupancyPercent': round((occupied / capacity) * 100, 1) if capacity else 0,
            })

        vehicle_rows = []
        for vehicle in vehicles:
            vehicle_assignments = [assignment for assignment in active_assignments if assignment.vehicleID_id == vehicle.id]
            occupied = len(vehicle_assignments)
            capacity = vehicle.capacity or 0
            vehicle_rows.append({
                'vehicleID': vehicle.id,
                'vehicleNumber': vehicle.vehicleNumber,
                'route': vehicle.routeID.routeName if vehicle.routeID else 'Unassigned',
                'driver': vehicle.driverID.name if vehicle.driverID else 'N/A',
                'capacity': capacity,
                'assigned': occupied,
                'vacant': max(capacity - occupied, 0),
                'overCapacity': max(occupied - capacity, 0) if capacity else occupied,
                'occupancyPercent': round((occupied / capacity) * 100, 1) if capacity else 0,
            })

        fee_total = sum((item.monthlyFee or Decimal('0.00')) for item in active_assignments)
        total_capacity = sum(row['capacity'] for row in vehicle_rows)
        total_assigned = len(active_assignments)
        data = {
            'generatedAt': timezone.now().strftime('%d-%m-%Y %I:%M %p'),
            'summary': {
                'routes': len(route_rows),
                'vehicles': len(vehicle_rows),
                'assigned': total_assigned,
                'students': len([item for item in active_assignments if item.assigneeType == 'student']),
                'teachers': len([item for item in active_assignments if item.assigneeType == 'teacher']),
                'capacity': total_capacity,
                'vacant': max(total_capacity - total_assigned, 0),
                'overCapacity': max(total_assigned - total_capacity, 0),
                'occupancyPercent': round((total_assigned / total_capacity) * 100, 1) if total_capacity else 0,
                'monthlyFee': str(fee_total),
            },
            'routes': route_rows,
            'vehicles': vehicle_rows,
            'assignments': [_assignment_manifest_row(item) for item in assignments[:500]],
        }
        logger.info(f'Transport report summary generated school={_school_id(request)} session={_session_id(request)}')
        return SuccessResponse('Transport report loaded.', data=data).to_json_response()
    except Exception as exc:
        logger.exception(f'Error loading transport report summary: {exc}')
        return ErrorResponse('Unable to load transport report.', status_code=500).to_json_response()


@login_required
def passenger_manifest_csv(request):
    try:
        rows = [_assignment_manifest_row(item) for item in _assignment_report_queryset(request)]
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="transport-passenger-manifest.csv"'
        writer = csv.writer(response)
        writer.writerow([
            'Type', 'Name', 'Class/Role', 'Roll', 'Phone', 'Route', 'Pickup Stop', 'Drop Stop',
            'Vehicle', 'Trip Type', 'Fee Mode', 'Monthly Fee', 'Start Date', 'End Date', 'Status',
        ])
        for row in rows:
            writer.writerow([
                row['type'], row['name'], row['classOrRole'], row['roll'], row['phone'], row['route'],
                row['pickupStop'], row['dropStop'], row['vehicle'], row['tripType'], row['feeMode'],
                row['monthlyFee'], row['startDate'], row['endDate'], row['status'],
            ])
        logger.info(f'Transport manifest CSV exported rows={len(rows)}')
        return response
    except Exception as exc:
        logger.exception(f'Error exporting transport manifest CSV: {exc}')
        return ErrorResponse('Unable to export passenger manifest.', status_code=500).to_json_response()


@login_required
def routes_api(request):
    try:
        if request.method == 'GET':
            routes = _scoped_for_request(request, TransportRoute).order_by('routeCode')
            return JsonResponse({'success': True, 'data': [_route_row(route, request=request) for route in routes]})
        route_id = request.POST.get('id')
        route = _scoped_object_or_new(request, TransportRoute, route_id)
        if route_id and not route:
            return ErrorResponse('Route not found.', status_code=404).to_json_response()
        route.routeCode = (request.POST.get('routeCode') or '').strip()
        route.routeName = (request.POST.get('routeName') or '').strip()
        route.startPoint = (request.POST.get('startPoint') or '').strip()
        route.endPoint = (request.POST.get('endPoint') or '').strip()
        route.description = (request.POST.get('description') or '').strip()
        route.isActive = _bool(request.POST.get('isActive', 'true'))
        _audit_fields(request, route)
        route.full_clean()
        route.save()
        logger.info(f'Transport route saved id={route.id} code={route.routeCode}')
        return SuccessResponse('Route saved successfully.', data=_route_row(route, request=request)).to_json_response()
    except (ValidationError, IntegrityError) as exc:
        logger.error(f'Transport route validation failed: {exc}')
        return ErrorResponse(_validation_message(exc, 'Route could not be saved. Check duplicate code and required fields.')).to_json_response()
    except Exception as exc:
        logger.exception(f'Error saving transport route: {exc}')
        return ErrorResponse('Unable to save route.', status_code=500).to_json_response()


@login_required
def route_detail_api(request):
    route = _scoped_for_request(request, TransportRoute).filter(pk=request.GET.get('id')).first()
    if not route:
        logger.error(f'Transport route not found id={request.GET.get("id")}')
        return ErrorResponse('Route not found.', status_code=404).to_json_response()
    stops = _scoped_for_request(request, TransportStop).filter(routeID=route).order_by('displayOrder', 'stopName')
    logger.info(f'Transport route detail fetched id={route.id}')
    return SuccessResponse('Route detail loaded.', data={
        'id': route.id,
        'routeCode': route.routeCode,
        'routeName': route.routeName,
        'startPoint': route.startPoint or '',
        'endPoint': route.endPoint or '',
        'description': route.description or '',
        'isActive': route.isActive,
        'stops': [{
            'id': stop.id,
            'stopName': stop.stopName,
            'pickupTime': stop.pickupTime.strftime('%I:%M %p') if stop.pickupTime else '',
            'dropTime': stop.dropTime.strftime('%I:%M %p') if stop.dropTime else '',
            'monthlyFee': str(stop.monthlyFee),
            'displayOrder': stop.displayOrder,
            'isActive': stop.isActive,
        } for stop in stops],
    }).to_json_response()


@login_required
def delete_route_api(request):
    return _soft_delete(request, TransportRoute, request.POST.get('id'), 'Route')


@login_required
def stops_api(request):
    try:
        if request.method == 'GET':
            qs = _scoped_for_request(request, TransportStop).select_related('routeID').order_by('routeID__routeCode', 'displayOrder')
            route_id = request.GET.get('routeID')
            if route_id:
                qs = qs.filter(routeID_id=route_id)
            return JsonResponse({'success': True, 'data': [_stop_row(stop, request=request) for stop in qs]})
        stop_id = request.POST.get('id')
        stop = _scoped_object_or_new(request, TransportStop, stop_id)
        if stop_id and not stop:
            return ErrorResponse('Stop not found.', status_code=404).to_json_response()
        stop.routeID_id = _ensure_scoped_fk(request, TransportRoute, request.POST.get('routeID'), 'Route')
        stop.stopName = (request.POST.get('stopName') or '').strip()
        stop.pickupTime = _time(request.POST.get('pickupTime'))
        stop.dropTime = _time(request.POST.get('dropTime'))
        stop.monthlyFee = _decimal(request.POST.get('monthlyFee'))
        stop.displayOrder = int(request.POST.get('displayOrder') or 0)
        stop.isActive = _bool(request.POST.get('isActive', 'true'))
        _audit_fields(request, stop)
        stop.full_clean()
        stop.save()
        logger.info(f'Transport stop saved id={stop.id} route={stop.routeID_id}')
        return SuccessResponse('Stop saved successfully.', data=_stop_row(stop, request=request)).to_json_response()
    except (ValidationError, IntegrityError, ValueError) as exc:
        logger.error(f'Transport stop validation failed: {exc}')
        return ErrorResponse(_validation_message(exc, 'Stop could not be saved. Check route, duplicate stop name, and required fields.')).to_json_response()
    except Exception as exc:
        logger.exception(f'Error saving transport stop: {exc}')
        return ErrorResponse('Unable to save stop.', status_code=500).to_json_response()


@login_required
def stop_detail_api(request):
    stop = _scoped_for_request(request, TransportStop).filter(pk=request.GET.get('id')).first()
    if not stop:
        return ErrorResponse('Stop not found.', status_code=404).to_json_response()
    logger.info(f'Transport stop detail fetched id={stop.id}')
    return SuccessResponse('Stop detail loaded.', data={
        'id': stop.id,
        'routeID': stop.routeID_id,
        'stopName': stop.stopName,
        'pickupTime': stop.pickupTime.strftime('%I:%M %p') if stop.pickupTime else '',
        'dropTime': stop.dropTime.strftime('%I:%M %p') if stop.dropTime else '',
        'monthlyFee': str(stop.monthlyFee),
        'displayOrder': stop.displayOrder,
        'isActive': stop.isActive,
    }).to_json_response()


@login_required
def delete_stop_api(request):
    return _soft_delete(request, TransportStop, request.POST.get('id'), 'Stop')


@login_required
def drivers_api(request):
    try:
        if request.method == 'GET':
            drivers = _scoped_for_request(request, TransportDriver).order_by('name')
            return JsonResponse({'success': True, 'data': [_driver_row(driver, request=request) for driver in drivers]})
        driver_id = request.POST.get('id')
        driver = _scoped_object_or_new(request, TransportDriver, driver_id)
        if driver_id and not driver:
            return ErrorResponse('Driver not found.', status_code=404).to_json_response()
        driver.name = (request.POST.get('name') or '').strip()
        if request.FILES.get('imageUpload'):
            driver.photo = optimize_uploaded_image(request.FILES.get('imageUpload'))
        driver.phoneNumber = (request.POST.get('phoneNumber') or '').strip()
        driver.licenseNumber = (request.POST.get('licenseNumber') or '').strip()
        driver.licenseExpiryDate = parse_date(request.POST.get('licenseExpiryDate') or '')
        driver.address = (request.POST.get('address') or '').strip()
        driver.isActive = _bool(request.POST.get('isActive', 'true'))
        _audit_fields(request, driver)
        driver.full_clean()
        driver.save()
        logger.info(f'Transport driver saved id={driver.id} name={driver.name}')
        return SuccessResponse('Driver saved successfully.', data=_driver_row(driver, request=request)).to_json_response()
    except ValidationError as exc:
        logger.error(f'Transport driver validation failed: {exc}')
        return ErrorResponse(_validation_message(exc, 'Driver could not be saved. Check required fields.')).to_json_response()
    except Exception as exc:
        logger.exception(f'Error saving transport driver: {exc}')
        return ErrorResponse('Unable to save driver.', status_code=500).to_json_response()


@login_required
def driver_detail_api(request):
    driver = _scoped_for_request(request, TransportDriver).filter(pk=request.GET.get('id')).first()
    if not driver:
        return ErrorResponse('Driver not found.', status_code=404).to_json_response()
    logger.info(f'Transport driver detail fetched id={driver.id}')
    return SuccessResponse('Driver detail loaded.', data={
        'id': driver.id,
        'photoUrl': safe_image_url(driver.photo, fallback_path='images/add_photo.svg'),
        'name': driver.name,
        'phoneNumber': driver.phoneNumber or '',
        'licenseNumber': driver.licenseNumber or '',
        'licenseExpiryDate': driver.licenseExpiryDate.strftime('%Y-%m-%d') if driver.licenseExpiryDate else '',
        'address': driver.address or '',
        'isActive': driver.isActive,
    }).to_json_response()


@login_required
def delete_driver_api(request):
    return _soft_delete(request, TransportDriver, request.POST.get('id'), 'Driver')


@login_required
def fee_mappings_api(request):
    try:
        if request.method == 'GET':
            qs = _scoped_for_request(request, TransportFeeMapping).select_related('routeID', 'stopID').order_by('routeID__routeCode', 'stopID__displayOrder')
            route_id = request.GET.get('routeID')
            if route_id:
                qs = qs.filter(routeID_id=route_id)
            return JsonResponse({'success': True, 'data': [_fee_mapping_row(mapping, request=request) for mapping in qs]})
        mapping_id = request.POST.get('id')
        mapping = _scoped_object_or_new(request, TransportFeeMapping, mapping_id)
        if mapping_id and not mapping:
            return ErrorResponse('Fee mapping not found.', status_code=404).to_json_response()
        mapping.routeID_id = _ensure_scoped_fk(request, TransportRoute, request.POST.get('routeID'), 'Route')
        mapping.stopID_id = _ensure_scoped_fk(request, TransportStop, request.POST.get('stopID'), 'Pickup/drop point', optional=True)
        mapping.assigneeType = request.POST.get('assigneeType') or 'student'
        mapping.feeMode = request.POST.get('feeMode') or 'student_fee'
        mapping.monthlyFee = _decimal(request.POST.get('monthlyFee'))
        mapping.effectiveFrom = parse_date(request.POST.get('effectiveFrom') or '')
        mapping.effectiveTo = parse_date(request.POST.get('effectiveTo') or '')
        mapping.notes = (request.POST.get('notes') or '').strip()
        mapping.isActive = _bool(request.POST.get('isActive', 'true'))
        _audit_fields(request, mapping)
        mapping.full_clean()
        mapping.save()
        logger.info(f'Transport fee mapping saved id={mapping.id} route={mapping.routeID_id} stop={mapping.stopID_id}')
        return SuccessResponse('Fee mapping saved successfully.', data=_fee_mapping_row(mapping, request=request)).to_json_response()
    except (ValidationError, IntegrityError, ValueError) as exc:
        logger.error(f'Transport fee mapping validation failed: {exc}')
        return ErrorResponse(_validation_message(exc, 'Fee mapping could not be saved. Check route, stop, and fee details.')).to_json_response()
    except Exception as exc:
        logger.exception(f'Error saving transport fee mapping: {exc}')
        return ErrorResponse('Unable to save fee mapping.', status_code=500).to_json_response()


@login_required
def fee_mapping_detail_api(request):
    mapping = _scoped_for_request(request, TransportFeeMapping).filter(pk=request.GET.get('id')).first()
    if not mapping:
        logger.error(f'Transport fee mapping not found id={request.GET.get("id")}')
        return ErrorResponse('Fee mapping not found.', status_code=404).to_json_response()
    logger.info(f'Transport fee mapping detail fetched id={mapping.id}')
    return SuccessResponse('Fee mapping detail loaded.', data={
        'id': mapping.id,
        'routeID': mapping.routeID_id,
        'stopID': mapping.stopID_id,
        'assigneeType': mapping.assigneeType,
        'feeMode': mapping.feeMode,
        'monthlyFee': str(mapping.monthlyFee),
        'effectiveFrom': mapping.effectiveFrom.strftime('%Y-%m-%d') if mapping.effectiveFrom else '',
        'effectiveTo': mapping.effectiveTo.strftime('%Y-%m-%d') if mapping.effectiveTo else '',
        'notes': mapping.notes or '',
        'isActive': mapping.isActive,
    }).to_json_response()


@login_required
def delete_fee_mapping_api(request):
    return _soft_delete(request, TransportFeeMapping, request.POST.get('id'), 'Fee mapping')


@login_required
def vehicles_api(request):
    try:
        if request.method == 'GET':
            vehicles = _scoped_for_request(request, TransportVehicle).select_related('driverID', 'routeID').order_by('vehicleNumber')
            return JsonResponse({'success': True, 'data': [_vehicle_row(vehicle, request=request) for vehicle in vehicles]})
        vehicle_id = request.POST.get('id')
        vehicle = _scoped_object_or_new(request, TransportVehicle, vehicle_id)
        if vehicle_id and not vehicle:
            return ErrorResponse('Vehicle not found.', status_code=404).to_json_response()
        vehicle.vehicleNumber = (request.POST.get('vehicleNumber') or '').strip()
        vehicle.vehicleType = request.POST.get('vehicleType') or 'bus'
        vehicle.capacity = int(request.POST.get('capacity') or 0)
        vehicle.driverID_id = _ensure_scoped_fk(request, TransportDriver, request.POST.get('driverID'), 'Driver', optional=True)
        vehicle.routeID_id = _ensure_scoped_fk(request, TransportRoute, request.POST.get('routeID'), 'Route', optional=True)
        vehicle.registrationExpiryDate = parse_date(request.POST.get('registrationExpiryDate') or '')
        vehicle.insuranceExpiryDate = parse_date(request.POST.get('insuranceExpiryDate') or '')
        vehicle.pollutionExpiryDate = parse_date(request.POST.get('pollutionExpiryDate') or '')
        vehicle.isActive = _bool(request.POST.get('isActive', 'true'))
        _audit_fields(request, vehicle)
        vehicle.full_clean()
        vehicle.save()
        logger.info(f'Transport vehicle saved id={vehicle.id} number={vehicle.vehicleNumber}')
        return SuccessResponse('Vehicle saved successfully.', data=_vehicle_row(vehicle, request=request)).to_json_response()
    except (ValidationError, IntegrityError, ValueError) as exc:
        logger.error(f'Transport vehicle validation failed: {exc}')
        return ErrorResponse(_validation_message(exc, 'Vehicle could not be saved. Check duplicate number and required fields.')).to_json_response()
    except Exception as exc:
        logger.exception(f'Error saving transport vehicle: {exc}')
        return ErrorResponse('Unable to save vehicle.', status_code=500).to_json_response()


@login_required
def vehicle_detail_api(request):
    vehicle = _scoped_for_request(request, TransportVehicle).filter(pk=request.GET.get('id')).first()
    if not vehicle:
        return ErrorResponse('Vehicle not found.', status_code=404).to_json_response()
    logger.info(f'Transport vehicle detail fetched id={vehicle.id}')
    return SuccessResponse('Vehicle detail loaded.', data={
        'id': vehicle.id,
        'vehicleNumber': vehicle.vehicleNumber,
        'vehicleType': vehicle.vehicleType,
        'capacity': vehicle.capacity,
        'driverID': vehicle.driverID_id,
        'routeID': vehicle.routeID_id,
        'registrationExpiryDate': vehicle.registrationExpiryDate.strftime('%Y-%m-%d') if vehicle.registrationExpiryDate else '',
        'insuranceExpiryDate': vehicle.insuranceExpiryDate.strftime('%Y-%m-%d') if vehicle.insuranceExpiryDate else '',
        'pollutionExpiryDate': vehicle.pollutionExpiryDate.strftime('%Y-%m-%d') if vehicle.pollutionExpiryDate else '',
        'isActive': vehicle.isActive,
    }).to_json_response()


@login_required
def delete_vehicle_api(request):
    return _soft_delete(request, TransportVehicle, request.POST.get('id'), 'Vehicle')


@login_required
def assignments_api(request):
    try:
        if request.method == 'GET':
            qs = _scoped_for_request(request, TransportAssignment).select_related(
                'studentID', 'teacherID', 'routeID', 'pickupStopID', 'dropStopID', 'vehicleID'
            ).order_by('assigneeType', 'studentID__name', 'teacherID__name')
            assignee_type = request.GET.get('assigneeType')
            if assignee_type:
                qs = qs.filter(assigneeType=assignee_type)
            return JsonResponse({'success': True, 'data': [_assignment_row(assignment, request=request) for assignment in qs]})
        with transaction.atomic():
            assignment_id = request.POST.get('id')
            assignment = _scoped_object_or_new(request, TransportAssignment, assignment_id)
            if assignment_id and not assignment:
                return ErrorResponse('Assignment not found.', status_code=404).to_json_response()
            assignment.assigneeType = request.POST.get('assigneeType') or 'student'
            _ensure_scoped_assignee(request, assignment.assigneeType, request.POST.get('studentID'), request.POST.get('teacherID'))
            assignment.studentID_id = _clean_form_value(request.POST.get('studentID'))
            assignment.teacherID_id = _clean_form_value(request.POST.get('teacherID'))
            assignment.routeID_id = _ensure_scoped_fk(request, TransportRoute, request.POST.get('routeID'), 'Route')
            assignment.pickupStopID_id = _ensure_scoped_fk(request, TransportStop, request.POST.get('pickupStopID'), 'Pickup point', optional=True)
            assignment.dropStopID_id = _ensure_scoped_fk(request, TransportStop, request.POST.get('dropStopID'), 'Drop point', optional=True)
            assignment.vehicleID_id = _ensure_scoped_fk(request, TransportVehicle, request.POST.get('vehicleID'), 'Vehicle', optional=True)
            assignment.tripType = request.POST.get('tripType') or 'both'
            assignment.feeMode = request.POST.get('feeMode') or ('student_fee' if assignment.assigneeType == 'student' else 'informational')
            assignment.monthlyFee = _decimal(request.POST.get('monthlyFee'))
            assignment.startDate = parse_date(request.POST.get('startDate') or '')
            assignment.endDate = parse_date(request.POST.get('endDate') or '')
            assignment.isActive = _bool(request.POST.get('isActive', 'true'))
            _audit_fields(request, assignment)
            assignment.full_clean()
            assignment.save()
        logger.info(f'Transport assignment saved id={assignment.id} type={assignment.assigneeType}')
        return SuccessResponse('Transport assignment saved successfully.', data=_assignment_row(assignment, request=request)).to_json_response()
    except (ValidationError, IntegrityError) as exc:
        logger.error(f'Transport assignment validation failed: {exc}')
        return ErrorResponse(_validation_message(exc, 'Assignment could not be saved. Check duplicate active assignment and required fields.')).to_json_response()
    except Exception as exc:
        logger.exception(f'Error saving transport assignment: {exc}')
        return ErrorResponse('Unable to save assignment.', status_code=500).to_json_response()


@login_required
def assignment_detail_api(request):
    assignment = _scoped_for_request(request, TransportAssignment).filter(pk=request.GET.get('id')).first()
    if not assignment:
        return ErrorResponse('Assignment not found.', status_code=404).to_json_response()
    logger.info(f'Transport assignment detail fetched id={assignment.id}')
    return SuccessResponse('Assignment detail loaded.', data={
        'id': assignment.id,
        'assigneeType': assignment.assigneeType,
        'studentID': assignment.studentID_id,
        'teacherID': assignment.teacherID_id,
        'routeID': assignment.routeID_id,
        'pickupStopID': assignment.pickupStopID_id,
        'dropStopID': assignment.dropStopID_id,
        'vehicleID': assignment.vehicleID_id,
        'tripType': assignment.tripType,
        'feeMode': assignment.feeMode,
        'monthlyFee': str(assignment.monthlyFee),
        'startDate': assignment.startDate.strftime('%Y-%m-%d') if assignment.startDate else '',
        'endDate': assignment.endDate.strftime('%Y-%m-%d') if assignment.endDate else '',
        'isActive': assignment.isActive,
    }).to_json_response()


@login_required
def delete_assignment_api(request):
    return _soft_delete(request, TransportAssignment, request.POST.get('id'), 'Assignment')


@login_required
def generate_transport_fee_records_api(request):
    try:
        fee_month = int(request.POST.get('feeMonth') or timezone.now().month)
        fee_year = int(request.POST.get('feeYear') or timezone.now().year)
        period_start, period_end = _period_dates(fee_year, fee_month)
        due_day = int(request.POST.get('dueDay') or 10)
        due_day = min(max(due_day, 1), calendar.monthrange(fee_year, fee_month)[1])
        due_date = date(fee_year, fee_month, due_day)
        qs = _scoped_for_request(request, TransportAssignment).select_related(
            'studentID__standardID', 'teacherID', 'routeID', 'pickupStopID', 'dropStopID'
        ).filter(isActive=True)
        assignee_type = request.POST.get('assigneeType')
        if assignee_type:
            qs = qs.filter(assigneeType=assignee_type)
        created_count = 0
        updated_count = 0
        skipped_count = 0
        with transaction.atomic():
            for assignment in qs:
                amount = _decimal(assignment.monthlyFee)
                if amount <= 0 or assignment.feeMode in {'free', 'informational'}:
                    skipped_count += 1
                    continue
                record, created = TransportFeeRecord.objects.get_or_create(
                    assignmentID=assignment,
                    feeMonth=fee_month,
                    feeYear=fee_year,
                    isDeleted=False,
                    defaults={
                        'schoolID_id': _school_id(request),
                        'sessionID_id': _session_id(request),
                        'periodStartDate': period_start,
                        'periodEndDate': period_end,
                        'dueDate': due_date,
                        'grossAmount': amount,
                        'netAmount': amount,
                        'balanceAmount': amount,
                        'status': 'pending',
                        'lastEditedBy': _user_label(request),
                        'updatedByUserID': request.user,
                    },
                )
                if created:
                    created_count += 1
                    _sync_transport_student_charge(request, record)
                elif record.status not in {'paid', 'partial'}:
                    record.periodStartDate = period_start
                    record.periodEndDate = period_end
                    record.dueDate = due_date
                    record.grossAmount = amount
                    record.discountAmount = Decimal('0.00')
                    record.fineAmount = Decimal('0.00')
                    record.paidAmount = Decimal('0.00')
                    record.notes = record.notes or ''
                    _audit_fields(request, record)
                    record.save()
                    updated_count += 1
                    _sync_transport_student_charge(request, record)
                else:
                    skipped_count += 1
        logger.info(f'Transport fee records generated month={fee_month} year={fee_year} created={created_count} updated={updated_count} skipped={skipped_count}')
        return SuccessResponse(
            f'Fee records generated. Created: {created_count}, Updated: {updated_count}, Skipped: {skipped_count}.',
            data={'created': created_count, 'updated': updated_count, 'skipped': skipped_count},
        ).to_json_response()
    except (ValueError, ValidationError) as exc:
        logger.error(f'Transport fee generation validation failed: {exc}')
        return ErrorResponse(_validation_message(exc, 'Unable to generate fee records. Check month, year, and due day.')).to_json_response()
    except Exception as exc:
        logger.exception(f'Error generating transport fee records: {exc}')
        return ErrorResponse('Unable to generate transport fee records.', status_code=500).to_json_response()


@login_required
def transport_fee_record_detail_api(request):
    record = _scoped_for_request(request, TransportFeeRecord).select_related(
        'assignmentID', 'assignmentID__studentID', 'assignmentID__teacherID', 'assignmentID__routeID', 'financeChargeID'
    ).filter(pk=request.GET.get('id')).first()
    if not record:
        return ErrorResponse('Transport fee record not found.', status_code=404).to_json_response()
    logger.info(f'Transport fee record detail fetched id={record.id}')
    return SuccessResponse('Transport fee record loaded.', data=_fee_record_row(record)).to_json_response()


@login_required
def record_transport_fee_payment_api(request):
    try:
        record = _scoped_for_request(request, TransportFeeRecord).filter(pk=request.POST.get('id')).first()
        if not record:
            return ErrorResponse('Transport fee record not found.', status_code=404).to_json_response()
        if record.status in {'waived', 'cancelled'}:
            return ErrorResponse('Cannot record payment for waived or cancelled fee records.').to_json_response()
        record.paidAmount = _decimal(request.POST.get('paidAmount'))
        record.paymentDate = parse_date(request.POST.get('paymentDate') or '') or timezone.now().date()
        record.paymentMode = (request.POST.get('paymentMode') or '').strip()
        record.referenceNo = (request.POST.get('referenceNo') or '').strip()
        record.notes = (request.POST.get('notes') or '').strip()
        _audit_fields(request, record)
        record.full_clean()
        record.save()
        charge = _sync_transport_student_charge(request, record)
        if charge and record.paidAmount > 0:
            sync_payment_receipt(
                charge_obj=charge,
                school_id=_school_id(request),
                session_id=_session_id(request),
                amount_received=record.paidAmount,
                receipt_date=record.paymentDate,
                source_module='transport_fee_receipt',
                source_record_id=record.id,
                payment_mode_code=(record.paymentMode or 'CASH').upper(),
                reference_no=record.referenceNo or '',
                notes=record.notes or f'Transport fee payment for {record.assignee_name}',
                user_obj=request.user,
            )
        elif record.paidAmount <= 0:
            clear_payment_receipt(
                school_id=_school_id(request),
                source_module='transport_fee_receipt',
                source_record_id=record.id,
                user_obj=request.user,
            )
        logger.info(f'Transport fee payment recorded id={record.id} paid={record.paidAmount}')
        return SuccessResponse('Transport fee payment recorded successfully.', data=_fee_record_row(record)).to_json_response()
    except ValidationError as exc:
        logger.error(f'Transport fee payment validation failed: {exc}')
        return ErrorResponse(_validation_message(exc, 'Payment could not be recorded. Check amount and date.')).to_json_response()
    except Exception as exc:
        logger.exception(f'Error recording transport fee payment: {exc}')
        return ErrorResponse('Unable to record transport fee payment.', status_code=500).to_json_response()


@login_required
def update_transport_fee_status_api(request):
    try:
        record = _scoped_for_request(request, TransportFeeRecord).filter(pk=request.POST.get('id')).first()
        if not record:
            return ErrorResponse('Transport fee record not found.', status_code=404).to_json_response()
        status = request.POST.get('status')
        if status not in {'waived', 'cancelled'}:
            return ErrorResponse('Unsupported fee status action.').to_json_response()
        record.status = status
        if status == 'waived':
            record.discountAmount = record.grossAmount
            record.paidAmount = Decimal('0.00')
        if status == 'cancelled':
            record.paidAmount = Decimal('0.00')
        record.notes = (request.POST.get('notes') or record.notes or '').strip()
        _audit_fields(request, record)
        record.save()
        _sync_transport_student_charge(request, record)
        clear_payment_receipt(
            school_id=_school_id(request),
            source_module='transport_fee_receipt',
            source_record_id=record.id,
            user_obj=request.user,
        )
        logger.info(f'Transport fee record status updated id={record.id} status={status}')
        return SuccessResponse('Transport fee status updated successfully.', data=_fee_record_row(record)).to_json_response()
    except Exception as exc:
        logger.exception(f'Error updating transport fee status: {exc}')
        return ErrorResponse('Unable to update transport fee status.', status_code=500).to_json_response()


@login_required
def transport_options_api(request):
    try:
        routes = _scoped_for_request(request, TransportRoute).filter(isActive=True).order_by('routeCode')
        stops = _scoped_for_request(request, TransportStop).filter(isActive=True).order_by('routeID__routeCode', 'displayOrder')
        drivers = _scoped_for_request(request, TransportDriver).filter(isActive=True).order_by('name')
        vehicles = _scoped_for_request(request, TransportVehicle).filter(isActive=True).order_by('vehicleNumber')
        fee_mappings = _scoped_for_request(request, TransportFeeMapping).filter(isActive=True).order_by('routeID__routeCode', 'stopID__displayOrder')
        data = {
            'routes': [{'id': item.id, 'text': f'{item.routeCode} - {item.routeName}'} for item in routes],
            'stops': [{'id': item.id, 'routeID': item.routeID_id, 'text': item.stopName, 'monthlyFee': str(item.monthlyFee)} for item in stops],
            'drivers': [{'id': item.id, 'text': item.name} for item in drivers],
            'vehicles': [{'id': item.id, 'text': item.vehicleNumber, 'routeID': item.routeID_id} for item in vehicles],
            'feeMappings': [
                {
                    'id': item.id,
                    'routeID': item.routeID_id,
                    'stopID': item.stopID_id,
                    'assigneeType': item.assigneeType,
                    'feeMode': item.feeMode,
                    'monthlyFee': str(item.monthlyFee),
                } for item in fee_mappings
            ],
        }
        logger.info('Transport options fetched successfully')
        return SuccessResponse('Transport options loaded.', data=data).to_json_response()
    except Exception as exc:
        logger.exception(f'Error loading transport options: {exc}')
        return ErrorResponse('Unable to load transport options.', status_code=500).to_json_response()


@login_required
def assignee_options_api(request):
    try:
        assignee_type = request.GET.get('assigneeType') or 'student'
        if assignee_type == 'teacher':
            qs = TeacherDetail.objects.filter(
                schoolID_id=_school_id(request),
                sessionID_id=_session_id(request),
                isDeleted=False,
            ).order_by('name')
        else:
            qs = Student.objects.filter(
                schoolID_id=_school_id(request),
                sessionID_id=_session_id(request),
                isDeleted=False,
            ).order_by('name')
        data = [{'id': item.id, 'text': item.name or f'#{item.id}'} for item in qs]
        logger.info(f'Transport assignee options fetched type={assignee_type}')
        return SuccessResponse('Assignees loaded.', data=data).to_json_response()
    except Exception as exc:
        logger.exception(f'Error loading transport assignee options: {exc}')
        return ErrorResponse('Unable to load assignees.', status_code=500).to_json_response()


def _soft_delete(request, model, object_id, label):
    try:
        obj = _scoped_for_request(request, model).filter(pk=object_id).first()
        if not obj:
            logger.error(f'Transport {label.lower()} not found for delete id={object_id}')
            return ErrorResponse(f'{label} not found.', status_code=404).to_json_response()
        obj.isDeleted = True
        if hasattr(obj, 'isActive'):
            obj.isActive = False
        obj.updatedByUserID = request.user
        obj.lastEditedBy = _user_label(request)
        obj.save()
        logger.info(f'Transport {label.lower()} deleted id={object_id}')
        return SuccessResponse(f'{label} deleted successfully.').to_json_response()
    except Exception as exc:
        logger.exception(f'Error deleting transport {label.lower()}: {exc}')
        return ErrorResponse(f'Unable to delete {label.lower()}.', status_code=500).to_json_response()
