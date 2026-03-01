from homeApp.models import SchoolDetail, SchoolOwner, SchoolSession
from managementApp.models import TeacherDetail, Student


def get_school_branding(request):
    """
    Resolve current school branding for any logged-in user type (owner/admin, teacher, student).
    """
    school_id = None
    school_name = "SCHOOLS-STACK"
    icon_url = None

    session_info = request.session.get("current_session", {}) if hasattr(request, "session") else {}
    school_id = session_info.get("SchoolID")

    if not school_id and session_info.get("Id"):
        school_id = SchoolSession.objects.filter(
            pk=session_info.get("Id"),
            isDeleted=False,
        ).values_list("schoolID_id", flat=True).first()

    user = getattr(request, "user", None)
    if not school_id and user and user.is_authenticated:
        school_id = SchoolDetail.objects.filter(
            ownerID__userID_id=user.id,
            isDeleted=False,
        ).values_list("id", flat=True).first()

    if not school_id and user and user.is_authenticated:
        school_id = TeacherDetail.objects.filter(
            userID_id=user.id,
            isDeleted=False,
        ).values_list("schoolID_id", flat=True).first()

    if not school_id and user and user.is_authenticated:
        school_id = Student.objects.filter(
            userID_id=user.id,
            isDeleted=False,
        ).values_list("schoolID_id", flat=True).first()

    school = None
    if school_id:
        school = SchoolDetail.objects.only("id", "schoolName", "name", "logo").filter(
            pk=school_id,
            isDeleted=False,
        ).first()

    if school:
        school_name = (school.schoolName or school.name or school_name).strip()
        if school.logo:
            icon_url = school.logo.url

    if hasattr(request, "session"):
        current = dict(request.session.get("current_session", {}))
        changed = False
        if school_id and current.get("SchoolID") != school_id:
            current["SchoolID"] = school_id
            changed = True
        if school_name and current.get("SchoolName") != school_name:
            current["SchoolName"] = school_name
            changed = True
        if icon_url and current.get("SchoolLogo") != icon_url:
            current["SchoolLogo"] = icon_url
            changed = True
        if changed:
            request.session["current_session"] = current

    return {
        "school_id": school_id,
        "school_name": school_name,
        "icon_url": icon_url,
    }
