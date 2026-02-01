from functools import wraps
from django.shortcuts import redirect
from django.contrib.auth import logout
from utils.logger import logger


def check_groups(*group_names):
    """
    Decorator to check if user is authenticated and belongs
    to at least one of the given groups.
    Usage: @check_groups("admin", "collector", "manager")
    """
    def _check_group(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            # Step 1: Check authentication
            if not request.user.is_authenticated:
                logger.warning(f"User {request.user} tried to access {view_func.__name__} without authentication.")
                logout(request)
                return redirect('/')  # Or your login URL

            # Step 2: Check group membership
            if request.user.groups.filter(name__in=group_names).exists():
                return view_func(request, *args, **kwargs)

            # Step 3: If not in allowed groups, deny access
            logger.warning(f"User {request.user} tried to access {view_func.__name__} without group membership.")
            logout(request)
            return redirect('/')

        return wrapper
    return _check_group