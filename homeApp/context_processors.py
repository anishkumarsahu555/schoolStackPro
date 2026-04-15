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
