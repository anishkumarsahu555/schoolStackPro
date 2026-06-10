from functools import wraps

from django.shortcuts import redirect
from django.core.exceptions import PermissionDenied


def check_groups(*groups):
    def _check_group(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            from managementApp.access_control import can_pass_group_gate, init_staff_management_session, user_has_management_access

            if not can_pass_group_gate(request, groups):
                if request.user.is_authenticated:
                    raise PermissionDenied
                return redirect('/')
            if user_has_management_access(request.user) and not request.session.get('current_session'):
                init_staff_management_session(request)
            return view_func(request, *args, **kwargs)
        return wrapper
    return _check_group
