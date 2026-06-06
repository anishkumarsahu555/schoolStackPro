import json

from homeApp.branding import get_school_branding
from homeApp.license import build_license_context, resolve_school_for_request


def app_branding(request):
    branding = get_school_branding(request)
    return {
        "app_school_name": branding.get("school_name"),
        "app_icon_url": branding.get("icon_url"),
        "app_install_icon_url": branding.get("icon_url"),
    }


def school_license(request):
    license_info = getattr(request, "school_license", None)
    if license_info is None:
        license_info = build_license_context(resolve_school_for_request(request))
    return {
        "school_license": license_info,
    }


def management_access(request):
    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return {
            "management_permissions": {},
            "management_can": {},
            "management_modules": [],
        }

    from managementApp.access_control import (
        MANAGEMENT_MODULES,
        get_staff_access,
        get_user_permission_flags,
        is_owner_or_admin,
        module_visible,
    )

    permissions = get_user_permission_flags(request.user)
    visible = {module: module_visible(permissions, module) for module, label, icon in MANAGEMENT_MODULES}
    return {
        "management_permissions": permissions,
        "management_can": visible,
        "management_visible_modules_json": json.dumps([module for module, allowed in visible.items() if allowed]),
        "management_modules": MANAGEMENT_MODULES,
        "management_staff_access": get_staff_access(request.user),
        "management_is_full_access": is_owner_or_admin(request.user),
    }
