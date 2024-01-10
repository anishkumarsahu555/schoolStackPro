from functools import wraps

from django.shortcuts import redirect


def check_groups(*groups):
    def _check_group(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if any(request.user.groups.filter(name=group).exists() for group in groups):
                pass
            else:
                return redirect('/')
            return view_func(request, *args, **kwargs)
        return wrapper
    return _check_group