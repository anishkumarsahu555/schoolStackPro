from homeApp.branding import get_school_branding


def app_branding(request):
    branding = get_school_branding(request)
    return {
        "app_school_name": branding.get("school_name"),
        "app_icon_url": branding.get("icon_url"),
        "app_install_icon_url": branding.get("icon_url"),
    }
