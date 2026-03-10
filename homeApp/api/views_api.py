from django.contrib.auth.decorators import login_required
import hashlib
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse as DjangoJsonResponse
from django.utils.html import escape
from django.views.decorators.csrf import csrf_exempt
from django_datatables_view.base_datatable_view import BaseDatatableView

from homeApp.models import SchoolDetail, WebPushSubscription
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


@transaction.atomic
@csrf_exempt
@login_required
def change_session(request):
    if request.method == 'POST':
        try:
            sessionID = request.POST.get("sessionID")
            instance = SchoolSession.objects.get(pk=int(sessionID),isDeleted=False)
            request.session['current_session'] = {'currentSessionYear': instance.sessionYear, 'Id': instance.pk}
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
@csrf_exempt
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
@csrf_exempt
@login_required
def disable_push_subscription_api(request):
    if request.method != 'POST':
        return ErrorResponse('Method not allowed.').to_json_response()

    endpoint = (request.POST.get('endpoint') or '').strip()
    if not endpoint:
        return ErrorResponse('Endpoint is required.').to_json_response()

    WebPushSubscription.objects.filter(endpointHash=_endpoint_hash(endpoint), userID_id=request.user.id).update(isActive=False)
    return SuccessResponse('Push subscription removed.', extra={'color': 'green'}).to_json_response()


@csrf_exempt
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
