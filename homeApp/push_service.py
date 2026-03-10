import json
import os
import base64
import re

from django.conf import settings

from homeApp.models import WebPushSubscription
from utils.logger import logger

try:
    from pywebpush import webpush, WebPushException
except Exception:  # pragma: no cover - optional dependency in local setup
    webpush = None

    class WebPushException(Exception):
        pass


APP_AUDIENCE_MAP = {
    'general': ['studentapp', 'teacherapp', 'managementapp'],
    'studentapp': ['studentapp'],
    'teacherapp': ['teacherapp'],
    'managementapp': ['managementapp'],
    'all_apps': ['studentapp', 'teacherapp', 'managementapp'],
}

APP_EVENT_URL_MAP = {
    'studentapp': '/student/events/',
    'teacherapp': '/teacher/manage-event/',
    'managementapp': '/management/manage_event/',
}

DEFAULT_PUSH_ICON = '/static/sw/images/icon-192.png'
DEFAULT_PUSH_BADGE = '/static/sw/images/icon-192-maskable.png'


def get_school_logo_url(school):
    if not school:
        return DEFAULT_PUSH_ICON
    logo = getattr(school, 'logo', None)
    if not logo:
        return DEFAULT_PUSH_ICON
    try:
        thumb = getattr(logo, 'thumbnail', None)
        if thumb and getattr(thumb, 'url', None):
            return thumb.url
    except Exception:
        pass
    try:
        if getattr(logo, 'url', None):
            return logo.url
    except Exception:
        pass
    return DEFAULT_PUSH_ICON


def get_vapid_public_key():
    value = os.getenv('VAPID_PUBLIC_KEY', getattr(settings, 'VAPID_PUBLIC_KEY', '')) or ''
    return re.sub(r'\s+', '', value)


def get_vapid_private_key():
    return (os.getenv('VAPID_PRIVATE_KEY', getattr(settings, 'VAPID_PRIVATE_KEY', '')) or '').strip()


def get_vapid_subject():
    return (os.getenv('VAPID_ADMIN_EMAIL', getattr(settings, 'VAPID_ADMIN_EMAIL', 'mailto:admin@schoolsstack.in')) or '').strip()


def is_valid_vapid_public_key(public_key):
    try:
        if not public_key:
            return False
        padded = public_key + '=' * ((4 - len(public_key) % 4) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode('utf-8'))
        return len(decoded) == 65 and decoded[0] == 0x04
    except Exception:
        return False


def is_web_push_configured():
    public_key = get_vapid_public_key()
    return bool(is_valid_vapid_public_key(public_key) and get_vapid_private_key())


def is_school_app_push_enabled(school, app_name):
    if not school or not getattr(school, 'webPushEnabled', False):
        return False

    app_switch = {
        'studentapp': 'webPushStudentAppEnabled',
        'teacherapp': 'webPushTeacherAppEnabled',
        'managementapp': 'webPushManagementAppEnabled',
    }.get(app_name)

    if not app_switch:
        return False

    return bool(getattr(school, app_switch, False))


def send_event_push_notifications(event, action='added'):
    if not event or not event.schoolID:
        return

    if not webpush:
        logger.warning('pywebpush is unavailable; skipping web push send.')
        return

    if not is_web_push_configured():
        logger.warning('VAPID keys are missing; skipping web push send.')
        return

    audience = getattr(event.eventID, 'audience', 'general') if event.eventID else 'general'
    app_targets = APP_AUDIENCE_MAP.get(audience, APP_AUDIENCE_MAP['general'])
    app_targets = [app for app in app_targets if is_school_app_push_enabled(event.schoolID, app)]
    if not app_targets:
        logger.info(
            f'Event push skipped: no enabled app targets for event={event.id}, '
            f'audience={audience}, school={event.schoolID_id}'
        )
        return

    subscriptions = WebPushSubscription.objects.filter(
        schoolID_id=event.schoolID_id,
        appName__in=app_targets,
        isActive=True,
    ).only('id', 'endpoint', 'authKey', 'p256dhKey', 'appName')

    if not subscriptions.exists():
        logger.info(
            f'Event push skipped: no active subscriptions for event={event.id}, '
            f'audience={audience}, app_targets={app_targets}, school={event.schoolID_id}'
        )
        return

    school_name = (getattr(event.schoolID, 'schoolName', '') or getattr(event.schoolID, 'name', '') or 'SchoolStack').strip()
    logo_url = get_school_logo_url(event.schoolID)
    action_label = 'Updated' if action == 'updated' else 'New'
    event_title = (event.title or 'School Event').strip()
    start_date = event.startDate.strftime('%d %b %Y') if event.startDate else 'N/A'
    end_date = event.endDate.strftime('%d %b %Y') if event.endDate else start_date
    date_line = start_date if start_date == end_date else f'{start_date} - {end_date}'
    message_line = (event.message or 'Tap to view event details.').strip()
    composed_body = f"{event_title} ({action_label})\n{date_line}\n{message_line[:120]}".strip()
    base_payload = {
        'title': school_name,
        'body': composed_body[:220],
        'icon': logo_url or DEFAULT_PUSH_ICON,
        'image': logo_url or DEFAULT_PUSH_ICON,
        'badge': DEFAULT_PUSH_BADGE,
        'actions': [
            {'action': 'open_event', 'title': 'View Event'},
            {'action': 'dismiss', 'title': 'Later'},
        ],
        'tag': f'event-{event.id}',
        'data': {
            'eventId': event.id,
            'schoolName': school_name,
            'eventTitle': event_title,
            'audience': audience,
            'action': action,
            'startDate': start_date,
            'endDate': end_date,
        },
    }

    vapid_private = get_vapid_private_key()
    vapid_claims = {'sub': get_vapid_subject()}

    sent = 0
    failed = 0
    total = 0
    for sub in subscriptions:
        total += 1
        payload = dict(base_payload)
        payload['data'] = dict(base_payload.get('data') or {})
        event_base_url = APP_EVENT_URL_MAP.get(sub.appName) or APP_EVENT_URL_MAP.get(app_targets[0]) or '/'
        payload['url'] = f'{event_base_url}?event_id={event.id}'
        ok, _ = _send_push_to_subscription(
            sub=sub,
            payload=payload,
            vapid_private=vapid_private,
            vapid_claims=vapid_claims,
        )
        if ok:
            sent += 1
        else:
            failed += 1

    logger.info(
        f'Event push dispatch: event={event.id}, action={action}, audience={audience}, '
        f'app_targets={app_targets}, subscriptions={total}, sent={sent}, failed={failed}'
    )


def _send_push_to_subscription(sub, payload, vapid_private, vapid_claims):
    auth_key = (sub.authKey or '').strip()
    p256dh_key = (sub.p256dhKey or '').strip()
    if not auth_key or not p256dh_key:
        return False, 'Missing subscription keys'

    # Backward-compatible normalization for earlier stored key formats.
    if '+' in auth_key or '/' in auth_key:
        auth_key = base64.urlsafe_b64encode(base64.b64decode(auth_key)).decode('utf-8').rstrip('=')
    if '+' in p256dh_key or '/' in p256dh_key:
        p256dh_key = base64.urlsafe_b64encode(base64.b64decode(p256dh_key)).decode('utf-8').rstrip('=')

    subscription_info = {
        'endpoint': sub.endpoint,
        'keys': {
            'auth': auth_key,
            'p256dh': p256dh_key,
        },
    }
    try:
        webpush(
            subscription_info=subscription_info,
            data=json.dumps(payload),
            vapid_private_key=vapid_private,
            vapid_claims=vapid_claims,
            ttl=60,
        )
        return True, ''
    except WebPushException as exc:
        status_code = getattr(getattr(exc, 'response', None), 'status_code', None)
        if status_code in (404, 410):
            sub.isActive = False
            sub.save(update_fields=['isActive', 'lastUpdatedOn'])
        logger.warning(f'Web push delivery failed for subscription {sub.id}: {exc}')
        return False, f'WebPushException(status={status_code}): {exc}'
    except Exception as exc:
        logger.error(f'Unexpected web push error for subscription {sub.id}: {exc}')
        return False, f'Exception: {exc}'


def send_test_push_notifications(school_id, app_name, user_id=None):
    if not webpush:
        return {'ok': False, 'error': 'pywebpush is unavailable'}

    if not is_web_push_configured():
        return {'ok': False, 'error': 'VAPID keys are missing or invalid'}

    qs = WebPushSubscription.objects.filter(
        schoolID_id=school_id,
        appName=app_name,
        isActive=True,
    ).only('id', 'endpoint', 'authKey', 'p256dhKey', 'userID_id')
    if user_id:
        qs = qs.filter(userID_id=user_id)

    subscriptions = list(qs)
    if not subscriptions:
        return {'ok': False, 'error': 'No active subscriptions found for selected app/user'}

    payload = {
        'title': 'SchoolStack',
        'body': 'Push test notification delivered successfully.',
        'icon': DEFAULT_PUSH_ICON,
        'image': DEFAULT_PUSH_ICON,
        'badge': DEFAULT_PUSH_BADGE,
        'actions': [
            {'action': 'open_event', 'title': 'Open App'},
            {'action': 'dismiss', 'title': 'Later'},
        ],
        'url': '/',
        'tag': 'push-test',
        'data': {'type': 'test'},
    }
    vapid_private = get_vapid_private_key()
    vapid_claims = {'sub': get_vapid_subject()}

    sent = 0
    failed = 0
    errors = []
    for sub in subscriptions:
        auth_key = (sub.authKey or '').strip()
        ok, err = _send_push_to_subscription(
            sub=sub,
            payload=payload,
            vapid_private=vapid_private,
            vapid_claims=vapid_claims,
        )
        if ok:
            sent += 1
        else:
            failed += 1
            errors.append({'subscriptionId': sub.id, 'error': err})

    return {
        'ok': sent > 0,
        'sent': sent,
        'failed': failed,
        'total': len(subscriptions),
        'errors': errors[:5],
    }
