from __future__ import annotations

from datetime import date
from typing import Optional

from homeApp.models import SchoolDetail, SchoolOwner


DEFAULT_EXPIRED_MESSAGE = "Activation expired. Please contact the administrator to renew your school license."


def resolve_school_for_request(request) -> Optional[SchoolDetail]:
    current_school_id = request.session.get("current_session", {}).get("SchoolID")
    if current_school_id:
        school = SchoolDetail.objects.select_related("ownerID").filter(
            pk=current_school_id,
            isDeleted=False,
        ).first()
        if school:
            return school

    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return None

    owner = SchoolOwner.objects.filter(userID_id=request.user.id, isDeleted=False).order_by("-datetime").first()
    if owner:
        school = SchoolDetail.objects.select_related("ownerID").filter(
            ownerID=owner,
            isDeleted=False,
        ).order_by("-datetime").first()
        if school:
            return school

    from managementApp.models import Student, TeacherDetail

    teacher = TeacherDetail.objects.select_related("schoolID").filter(
        userID_id=request.user.id,
        isDeleted=False,
    ).order_by("-datetime").first()
    if teacher and teacher.schoolID and not teacher.schoolID.isDeleted:
        return teacher.schoolID

    student = Student.objects.select_related("schoolID").filter(
        userID_id=request.user.id,
        isDeleted=False,
    ).order_by("-datetime").first()
    if student and student.schoolID and not student.schoolID.isDeleted:
        return student.schoolID

    return None


def build_license_context(school: Optional[SchoolDetail]) -> dict:
    today = date.today()
    base = {
        "school_id": getattr(school, "id", None),
        "school_name": (getattr(school, "schoolName", None) or getattr(school, "name", None) or "School"),
        "is_available": True,
        "status": "unknown",
        "label": "License unavailable",
        "badge_class": "neutral",
        "detail": "License details are not available for this account yet.",
        "message": "",
        "valid_from": None,
        "valid_until": None,
        "remaining_days": None,
        "show_banner": False,
        "dashboard_note": "Activation details are not configured yet.",
    }
    if not school:
        return base

    start_date = school.activationStartDate
    end_date = school.activationEndDate
    message = (school.activationMessage or "").strip() or DEFAULT_EXPIRED_MESSAGE
    base.update(
        {
            "valid_from": start_date,
            "valid_until": end_date,
            "message": message,
        }
    )

    if not school.activationEnabled:
        base.update(
            {
                "is_available": False,
                "status": "inactive",
                "label": "Inactive",
                "badge_class": "negative",
                "detail": "This school has been deactivated by the super admin.",
                "show_banner": True,
                "dashboard_note": "Super admin access control is currently disabling this school.",
            }
        )
        return base

    if start_date and start_date > today:
        base.update(
            {
                "is_available": False,
                "status": "scheduled",
                "label": "Scheduled",
                "badge_class": "warning",
                "detail": f"Activation will start on {start_date:%d %b %Y}.",
                "show_banner": True,
                "dashboard_note": "School access is scheduled but not active yet.",
            }
        )
        return base

    if end_date:
        remaining_days = (end_date - today).days
        base["remaining_days"] = remaining_days
        if remaining_days < 0:
            base.update(
                {
                    "is_available": False,
                    "status": "expired",
                    "label": "Expired",
                    "badge_class": "negative",
                    "detail": f"Activation expired on {end_date:%d %b %Y}.",
                    "show_banner": True,
                    "dashboard_note": "Access renewal is required before protected features can be used.",
                }
            )
            return base
        if remaining_days == 0:
            base.update(
                {
                    "status": "expires_today",
                    "label": "Expires today",
                    "badge_class": "warning",
                    "detail": "Activation is valid only for today.",
                    "show_banner": True,
                    "dashboard_note": "Renewal is recommended today to avoid interruption.",
                }
            )
            return base
        if remaining_days <= 7:
            base.update(
                {
                    "status": "expiring_soon",
                    "label": "Expiring soon",
                    "badge_class": "warning",
                    "detail": f"Activation is valid for {remaining_days} more day{'s' if remaining_days != 1 else ''}.",
                    "show_banner": True,
                    "dashboard_note": "License is active, but it is close to expiry.",
                }
            )
            return base

    base.update(
        {
            "status": "active",
            "label": "Active",
            "badge_class": "positive",
            "detail": "School activation is valid and all licensed features are available.",
            "dashboard_note": "License validation passed successfully.",
        }
    )
    return base
