import threading
from decimal import Decimal


_audit_state = threading.local()


def set_current_request(request):
    _audit_state.request = request


def clear_current_request():
    if hasattr(_audit_state, 'request'):
        del _audit_state.request


def get_current_request():
    return getattr(_audit_state, 'request', None)


def get_current_user():
    request = get_current_request()
    user = getattr(request, 'user', None)
    if user and getattr(user, 'is_authenticated', False):
        return user
    return None


def get_client_ip(request):
    forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR') if request else ''
    if forwarded_for:
        return forwarded_for.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR') if request else None


def serialize_model_instance(instance):
    data = {}
    for field in instance._meta.concrete_fields:
        value = getattr(instance, field.attname, None)
        if isinstance(value, Decimal):
            value = str(value)
        elif hasattr(value, 'isoformat'):
            value = value.isoformat()
        elif value is not None and not isinstance(value, (str, int, float, bool, list, dict)):
            value = str(value)
        data[field.name] = value
    return data


def build_changes(before, after):
    changes = {}
    for key, new_value in after.items():
        old_value = before.get(key)
        if old_value != new_value:
            changes[key] = {
                'old': old_value,
                'new': new_value,
            }
    return changes


class AuditContextMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        set_current_request(request)
        try:
            return self.get_response(request)
        finally:
            clear_current_request()
