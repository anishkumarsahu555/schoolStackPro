from django.contrib.auth.decorators import login_required
import hashlib
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse as DjangoJsonResponse
from django.urls import reverse
from django.utils.html import escape
from django_datatables_view.base_datatable_view import BaseDatatableView

from homeApp.models import AuditLog, SchoolDetail, WebPushSubscription
from homeApp.session_utils import build_current_session_payload
from managementApp.models import *
from managementApp.signals import pre_save_with_user
from homeApp.push_service import (
    get_vapid_public_key,
    is_web_push_configured,
    is_valid_vapid_public_key,
    send_test_push_notifications,
)
from utils.get_school_detail import get_school_id
from utils.custom_response import SuccessResponse, ErrorResponse


LOOKUP_ALLOWED_APP_LABELS = {
    'homeApp',
    'managementApp',
    'financeApp',
    'certificateApp',
    'teacherApp',
    'studentApp',
    'libraryApp',
    'inventoryApp',
    'transportApp',
    'hostelApp',
}


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


def _endpoint_hash(endpoint):
    return hashlib.sha256(endpoint.encode('utf-8')).hexdigest()


def _current_session_school_ids(request):
    current_session = request.session.get('current_session', {})
    session_id = current_session.get('Id')
    school_id = current_session.get('SchoolID') or get_school_id(request)
    return session_id, school_id


def _object_allowed_for_lookup(obj, session_id, school_id):
    if hasattr(obj, 'isDeleted') and obj.isDeleted:
        return False
    obj_session_id = getattr(obj, 'sessionID_id', None)
    obj_school_id = getattr(obj, 'schoolID_id', None)
    if obj_session_id and session_id and int(obj_session_id) != int(session_id):
        return False
    if obj_school_id and school_id and int(obj_school_id) != int(school_id):
        return False
    return True


def _lookup_user_profile(user_obj, session_id):
    if not user_obj:
        return {
            'type': 'Unknown',
            'name': 'N/A',
            'url': '',
            'phone': 'N/A',
            'email': 'N/A',
            'username': 'N/A',
            'isActive': 'N/A',
        }
    teacher = TeacherDetail.objects.filter(
        userID_id=user_obj.pk,
        isDeleted=False,
        sessionID_id=session_id,
    ).only('id', 'name', 'phoneNumber', 'email', 'isActive', 'userID').order_by('-datetime').first()
    if teacher:
        return {
            'type': 'Staff',
            'name': teacher.name or user_obj.get_full_name() or user_obj.username,
            'url': reverse('managementApp:teacher_detail', kwargs={'id': teacher.pk}),
            'phone': teacher.phoneNumber or 'N/A',
            'email': teacher.email or user_obj.email or 'N/A',
            'username': user_obj.username or 'N/A',
            'isActive': teacher.isActive or ('Yes' if user_obj.is_active else 'No'),
        }
    student = Student.objects.filter(
        userID_id=user_obj.pk,
        isDeleted=False,
        sessionID_id=session_id,
    ).only('id', 'name', 'phoneNumber', 'email', 'isActive', 'userID').order_by('-datetime').first()
    if student:
        return {
            'type': 'Student',
            'name': student.name or user_obj.get_full_name() or user_obj.username,
            'url': reverse('managementApp:student_detail', kwargs={'id': student.pk}),
            'phone': student.phoneNumber or 'N/A',
            'email': student.email or user_obj.email or 'N/A',
            'username': user_obj.username or 'N/A',
            'isActive': student.isActive or ('Yes' if user_obj.is_active else 'No'),
        }
    return {
        'type': 'User',
        'name': user_obj.get_full_name() or user_obj.username,
        'url': '',
        'phone': 'N/A',
        'email': user_obj.email or 'N/A',
        'username': user_obj.username or 'N/A',
        'isActive': 'Yes' if user_obj.is_active else 'No',
    }


def _safe_lookup_label(obj, fallback='N/A'):
    if not obj:
        return fallback
    try:
        label = str(obj)
    except Exception:
        label = ''
    return label or fallback


def _lookup_object_profile(obj):
    if isinstance(obj, Standard):
        class_name = obj.name or 'N/A'
        if obj.section:
            class_name = f'{class_name} - {obj.section}'
        reference_parts = [
            f"Location: {obj.classLocation}" if obj.classLocation else '',
            f"Roll: {obj.startingRoll or 'N/A'} - {obj.endingRoll or 'N/A'}",
            f"Class Teacher: {obj.classTeacher.name}" if obj.classTeacher else '',
        ]
        return {
            'type': 'Class',
            'name': class_name,
            'url': f"{reverse('managementApp:manage_class')}?lookup_id={obj.pk}",
            'phone': 'N/A',
            'email': 'N/A',
            'isActive': 'Active',
            'referenceDetail': ' | '.join(part for part in reference_parts if part) or f'ID: {obj.pk}',
        }
    if isinstance(obj, Subjects):
        return {
            'type': 'Subject',
            'name': obj.name or 'N/A',
            'url': f"{reverse('managementApp:manage_subjects')}?lookup_id={obj.pk}",
            'phone': 'N/A',
            'email': 'N/A',
            'isActive': 'Active',
            'referenceDetail': f'ID: {obj.pk}',
        }
    if isinstance(obj, TeacherDetail):
        reference_parts = [
            f"Employee Code: {obj.employeeCode}" if obj.employeeCode else '',
            f"Position: {obj.currentPosition}" if obj.currentPosition else '',
            f"Staff Type: {obj.staffType}" if obj.staffType else '',
        ]
        return {
            'type': 'Staff',
            'name': obj.name or 'N/A',
            'url': reverse('managementApp:teacher_detail', kwargs={'id': obj.pk}),
            'phone': obj.phoneNumber or 'N/A',
            'email': obj.email or 'N/A',
            'isActive': obj.isActive or 'N/A',
            'referenceDetail': ' | '.join(part for part in reference_parts if part) or f'ID: {obj.pk}',
        }
    if isinstance(obj, Student):
        class_label = obj.standardID.name if obj.standardID else ''
        reference_parts = [
            f"Class: {class_label}" if class_label else '',
            f"Roll: {obj.roll}" if obj.roll else '',
            f"Registration: {obj.registrationCode}" if obj.registrationCode else '',
        ]
        return {
            'type': 'Student',
            'name': obj.name or 'N/A',
            'url': reverse('managementApp:student_detail', kwargs={'id': obj.pk}),
            'phone': obj.phoneNumber or 'N/A',
            'email': obj.email or 'N/A',
            'isActive': obj.isActive or 'N/A',
            'referenceDetail': ' | '.join(part for part in reference_parts if part) or f'ID: {obj.pk}',
        }
    return {
        'type': obj._meta.verbose_name.title() if obj else 'Record',
        'name': _safe_lookup_label(obj),
        'url': '',
        'phone': 'N/A',
        'email': 'N/A',
        'isActive': 'N/A',
        'referenceDetail': f'ID: {obj.pk}' if obj else 'N/A',
    }


@login_required
def lookup_detail_api(request):
    app_label = (request.GET.get('app_label') or '').strip()
    model_name = (request.GET.get('model') or '').strip().lower()
    object_id = (request.GET.get('id') or '').strip()
    lookup_context = (request.GET.get('lookup_context') or 'reference').strip().lower()
    if lookup_context not in {'audit', 'reference'}:
        lookup_context = 'reference'
    if app_label not in LOOKUP_ALLOWED_APP_LABELS or not model_name or not object_id.isdigit():
        return ErrorResponse('Invalid lookup request.').to_json_response()

    try:
        content_type = ContentType.objects.get(app_label=app_label, model=model_name)
        model_class = content_type.model_class()
        if not model_class:
            return ErrorResponse('Lookup model is not available.').to_json_response()
        session_id, school_id = _current_session_school_ids(request)
        obj = model_class.objects.filter(pk=int(object_id)).first()
        if not obj or not _object_allowed_for_lookup(obj, session_id, school_id):
            return ErrorResponse('Lookup detail not found.').to_json_response()

        editor_user = getattr(obj, 'updatedByUserID', None)
        editor_display = getattr(obj, 'lastEditedBy', None) or (
            editor_user.get_full_name() or editor_user.username if editor_user else 'N/A'
        )
        updated_on = getattr(obj, 'lastUpdatedOn', None)
        created_on = getattr(obj, 'datetime', None)

        audit_qs = AuditLog.objects.select_related('userID').filter(
            content_type=content_type,
            object_id=obj.pk,
        )
        if session_id:
            audit_qs = audit_qs.filter(Q(sessionID_id=session_id) | Q(sessionID__isnull=True))
        if school_id:
            audit_qs = audit_qs.filter(Q(schoolID_id=school_id) | Q(schoolID__isnull=True))

        activity = []
        for log in audit_qs.order_by('-datetime')[:5]:
            user_label = log.userID.get_full_name() or log.userID.username if log.userID else 'System'
            activity.append({
                'when': log.datetime.strftime('%d-%m-%Y %I:%M %p') if log.datetime else 'N/A',
                'action': log.get_action_display(),
                'user': user_label,
                'changedFields': ', '.join((log.changes or {}).keys()) or 'Snapshot',
            })

        object_profile = _lookup_object_profile(obj)
        data = {
            'lookupContext': lookup_context,
            'objectLabel': _safe_lookup_label(obj, object_profile.get('name') or 'N/A'),
            'modelLabel': f'{content_type.app_label}.{content_type.model}',
            'objectProfile': object_profile,
            'editedBy': editor_display,
            'editorUsername': editor_user.username if editor_user else editor_display,
            'editorEmail': editor_user.email if editor_user and editor_user.email else 'N/A',
            'editorProfile': _lookup_user_profile(editor_user, session_id),
            'updatedOn': updated_on.strftime('%d-%m-%Y %I:%M %p') if updated_on else 'N/A',
            'createdOn': created_on.strftime('%d-%m-%Y %I:%M %p') if created_on else 'N/A',
            'activity': activity,
        }
        return SuccessResponse('Lookup loaded.', data=data).to_json_response()
    except ContentType.DoesNotExist:
        return ErrorResponse('Lookup model not found.').to_json_response()
    except Exception:
        return ErrorResponse('Unable to load lookup detail.').to_json_response()


@transaction.atomic
@login_required
def change_session(request):
    if request.method == 'POST':
        try:
            sessionID = request.POST.get("sessionID")
            instance = SchoolSession.objects.get(pk=int(sessionID),isDeleted=False)
            request.session['current_session'] = build_current_session_payload(instance)
            return _api_response(
                {'status': 'success', 'message': 'Session changed successfully.', 'color': 'success'},
                safe=False)
        except:
            return _api_response({'status': 'error'}, safe=False)


@login_required
def get_push_public_config_api(request):
    current_session = request.session.get('current_session', {})
    school_id = current_session.get('SchoolID')
    app_name = (request.GET.get('app_name') or '').strip().lower()

    if app_name not in {'studentapp', 'teacherapp', 'managementapp'}:
        return ErrorResponse('Invalid app name.').to_json_response()

    if not school_id:
        school_id = get_school_id(request)
        if school_id:
            current_session = dict(current_session)
            current_session['SchoolID'] = school_id
            request.session['current_session'] = current_session

    school = SchoolDetail.objects.filter(id=school_id, isDeleted=False).first() if school_id else None
    if not school:
        return ErrorResponse('School not found.').to_json_response()

    app_switch = {
        'studentapp': school.webPushStudentAppEnabled,
        'teacherapp': school.webPushTeacherAppEnabled,
        'managementapp': school.webPushManagementAppEnabled,
    }
    is_enabled = bool(school.webPushEnabled and app_switch.get(app_name, False))
    data = {
        'enabled': is_enabled,
        'configured': is_web_push_configured(),
        'publicKey': get_vapid_public_key() if is_enabled else '',
        'configError': '',
    }
    if is_enabled and data['publicKey'] and not is_valid_vapid_public_key(data['publicKey']):
        data['configured'] = False
        data['configError'] = 'Invalid VAPID public key format.'
    return SuccessResponse('Push config fetched successfully.', data=data).to_json_response()


@transaction.atomic
@login_required
def upsert_push_subscription_api(request):
    if request.method != 'POST':
        return ErrorResponse('Method not allowed.').to_json_response()

    app_name = (request.POST.get('app_name') or '').strip().lower()
    endpoint = (request.POST.get('endpoint') or '').strip()
    auth_key = (request.POST.get('auth') or '').strip()
    p256dh_key = (request.POST.get('p256dh') or '').strip()
    if app_name not in {'studentapp', 'teacherapp', 'managementapp'}:
        return ErrorResponse('Invalid app name.').to_json_response()
    if not endpoint or not auth_key or not p256dh_key:
        return ErrorResponse('Invalid push subscription payload.').to_json_response()

    current_session = request.session.get('current_session', {})
    school_id = current_session.get('SchoolID')
    if not school_id:
        school_id = get_school_id(request)
        if school_id:
            current_session = dict(current_session)
            current_session['SchoolID'] = school_id
            request.session['current_session'] = current_session

    school = SchoolDetail.objects.filter(id=school_id, isDeleted=False).first() if school_id else None
    if not school:
        return ErrorResponse('School not found.').to_json_response()

    endpoint_hash = _endpoint_hash(endpoint)
    existing = WebPushSubscription.objects.filter(endpointHash=endpoint_hash).order_by('id').first()
    if existing:
        existing.schoolID_id = school.id
        existing.userID_id = request.user.id
        existing.appName = app_name
        existing.endpoint = endpoint
        existing.authKey = auth_key
        existing.p256dhKey = p256dh_key
        existing.isActive = True
        existing.save(update_fields=[
            'schoolID', 'userID', 'appName', 'endpoint',
            'authKey', 'p256dhKey', 'isActive', 'lastUpdatedOn'
        ])
        WebPushSubscription.objects.filter(endpointHash=endpoint_hash).exclude(id=existing.id).update(isActive=False)
    else:
        WebPushSubscription.objects.create(
            schoolID_id=school.id,
            userID_id=request.user.id,
            appName=app_name,
            endpoint=endpoint,
            endpointHash=endpoint_hash,
            authKey=auth_key,
            p256dhKey=p256dh_key,
            isActive=True,
        )
    return SuccessResponse('Push subscription saved.', extra={'color': 'green'}).to_json_response()


@transaction.atomic
@login_required
def disable_push_subscription_api(request):
    if request.method != 'POST':
        return ErrorResponse('Method not allowed.').to_json_response()

    endpoint = (request.POST.get('endpoint') or '').strip()
    if not endpoint:
        return ErrorResponse('Endpoint is required.').to_json_response()

    WebPushSubscription.objects.filter(endpointHash=_endpoint_hash(endpoint), userID_id=request.user.id).update(isActive=False)
    return SuccessResponse('Push subscription removed.', extra={'color': 'green'}).to_json_response()


@login_required
def send_test_push_api(request):
    app_name = ((request.POST.get('app_name') or request.GET.get('app_name') or '').strip().lower())
    if app_name not in {'studentapp', 'teacherapp', 'managementapp'}:
        return ErrorResponse('Invalid app name.').to_json_response()

    school_id = request.session.get('current_session', {}).get('SchoolID') or get_school_id(request)
    if not school_id:
        return ErrorResponse('School not found.').to_json_response()

    result = send_test_push_notifications(school_id=school_id, app_name=app_name, user_id=request.user.id)
    if result.get('ok'):
        return SuccessResponse('Test push sent.', data=result, extra={'color': 'green'}).to_json_response()
    return ErrorResponse(result.get('error', 'Test push failed.'), data=result, extra={'color': 'red'}).to_json_response()
