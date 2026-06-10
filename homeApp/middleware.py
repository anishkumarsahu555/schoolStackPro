from django.http import JsonResponse
from django.shortcuts import render
from django.urls import Resolver404, resolve

from homeApp.license import build_license_context, resolve_school_for_request
from homeApp.models import SchoolSession
from homeApp.session_utils import build_current_session_payload, build_session_list_item
from managementApp.access_control import (
    CHAT_POST_ACTION_PERMISSION_MAP,
    MODULE_LABELS,
    has_management_permission,
    init_staff_management_session,
    is_owner_or_admin,
    permission_for_resolver,
    user_has_management_access,
)
from managementApp.models import Student, TeacherDetail


class RoleSessionBootstrapMiddleware:
    """
    Ensure teacher/student users always have a valid default current_session.
    Applies only to /teacher and /student routes (including APIs).
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            path = request.path or ""
            if path.startswith("/teacher"):
                self._ensure_teacher_session(request)
            elif path.startswith("/student"):
                self._ensure_student_session(request)
        return self.get_response(request)

    def _ensure_teacher_session(self, request):
        teacher = TeacherDetail.objects.select_related("sessionID", "schoolID").filter(
            userID_id=request.user.id,
            isDeleted=False,
        ).order_by("-datetime").first()
        if not teacher:
            return

        self._ensure_profile_session(
            request=request,
            school_id=teacher.schoolID_id,
            fallback_session_id=teacher.sessionID_id,
            fallback_session_obj=teacher.sessionID,
            school_obj=teacher.schoolID,
        )

    def _ensure_student_session(self, request):
        student = Student.objects.select_related("sessionID", "schoolID").filter(
            userID_id=request.user.id,
            isDeleted=False,
        ).order_by("-datetime").first()
        if not student:
            return

        self._ensure_profile_session(
            request=request,
            school_id=student.schoolID_id,
            fallback_session_id=student.sessionID_id,
            fallback_session_obj=student.sessionID,
            school_obj=student.schoolID,
        )

    def _ensure_profile_session(
        self,
        request,
        school_id,
        fallback_session_id,
        fallback_session_obj,
        school_obj,
    ):
        raw_current = request.session.get("current_session", {})
        current = dict(raw_current) if isinstance(raw_current, dict) else {}
        current_id = current.get("Id")

        is_current_valid = False
        if current_id and school_id:
            is_current_valid = SchoolSession.objects.filter(
                pk=current_id,
                isDeleted=False,
                schoolID_id=school_id,
            ).exists()

        target_session = None
        if is_current_valid:
            target_session = SchoolSession.objects.filter(
                pk=current_id,
                isDeleted=False,
            ).first()
        if not target_session and school_id:
            target_session = SchoolSession.objects.filter(
                isDeleted=False,
                schoolID_id=school_id,
                isCurrent=True,
            ).order_by("-datetime").first()
        if not target_session and fallback_session_id:
            target_session = fallback_session_obj or SchoolSession.objects.filter(
                pk=fallback_session_id,
                isDeleted=False,
            ).first()

        if not target_session:
            return

        payload = build_current_session_payload(target_session)
        current.update(payload)

        request.session["current_session"] = current

        if school_id:
            session_qs = SchoolSession.objects.filter(
                isDeleted=False,
                schoolID_id=school_id,
            ).order_by("-datetime")
            request.session["session_list"] = [
                build_session_list_item(s)
                for s in session_qs
            ]


class ManagementAccessPermissionMiddleware:
    protected_prefixes = (
        "/management/",
        "/certificates/",
        "/transport/",
        "/hostel/",
    )
    staff_protected_prefixes = (
        "/chat/",
    )
    api_prefixes = (
        "/management/api/",
        "/management/library/api/",
        "/certificates/api/",
        "/transport/api/",
        "/hostel/api/",
        "/chat/api/",
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not self._should_check(request):
            return self.get_response(request)

        match = self._resolve_match(request)
        permission = self._permission_for_request(match, request)
        if not permission:
            return self.get_response(request)

        module_key, action = permission
        if is_owner_or_admin(request.user):
            return self.get_response(request)

        if user_has_management_access(request.user) and not request.session.get("current_session"):
            init_staff_management_session(request)

        if has_management_permission(request.user, module_key, action):
            return self.get_response(request)

        module_label, action_label, message = self._permission_message(module_key, action)
        if request.path.startswith(self.api_prefixes):
            return JsonResponse(
                {
                    "success": False,
                    "message": message,
                    "detail": "Ask an administrator to update your staff access role if you need this permission.",
                    "permission": {
                        "module": module_key,
                        "moduleLabel": module_label,
                        "action": action,
                        "actionLabel": action_label,
                    },
                },
                status=403,
            )

        return render(
            request,
            "homeApp/errors/error_page.html",
            {
                "error_status_code": 403,
                "error_title": "Forbidden",
                "error_heading": "Permission needed",
                "error_message": message,
                "error_details": [
                    f"Required permission: {module_label} - {action_label}",
                    "Ask an administrator to update your staff access role if you need this page.",
                ],
                "error_accent": "rose",
            },
            status=403,
        )

    def _permission_message(self, module_key, action):
        action_labels = {
            "view": "View",
            "add": "Add",
            "edit": "Edit",
            "delete": "Delete",
            "approve": "Approve",
            "report": "Report",
            "export": "Report",
        }
        module_label = MODULE_LABELS.get(module_key, module_key.replace("_", " ").title())
        action_label = action_labels.get(action, action.replace("_", " ").title())
        message = f"You need {action_label} permission for {module_label} to continue."
        return module_label, action_label, message

    def _should_check(self, request):
        if not request.user.is_authenticated:
            return False
        if request.path.rstrip('/') == '/management/api/global_search_api' or request.path_info.rstrip('/') == '/management/api/global_search_api':
            return False
        if request.path.startswith(self.protected_prefixes):
            return True
        if request.path.startswith(self.staff_protected_prefixes):
            return is_owner_or_admin(request.user) or user_has_management_access(request.user)
        return False

    def _resolve_match(self, request):
        try:
            return getattr(request, "resolver_match", None) or resolve(request.path_info)
        except Resolver404:
            return None

    def _permission_for_request(self, match, request):
        if (
            match
            and match.namespace == "chatApp"
            and match.url_name in {"inbox", "room"}
            and request.method == "POST"
        ):
            return CHAT_POST_ACTION_PERMISSION_MAP.get(request.POST.get("action")) or ("communication", "add")
        return permission_for_resolver(match, request.method)


class SchoolLicenseMiddleware:
    protected_prefixes = ("/management/", "/teacher/", "/student/")
    dashboard_routes = {
        ("managementApp", "admin_home"),
        ("teacherApp", "teacher_root"),
        ("teacherApp", "teacher_home"),
        ("studentApp", "student_root"),
        ("studentApp", "student_home"),
    }

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.school_license = build_license_context(resolve_school_for_request(request))

        if self._should_block(request):
            if request.path.startswith(("/management/api/", "/teacher/api/", "/student/api/")):
                return JsonResponse(
                    {
                        "success": False,
                        "message": request.school_license["message"] or "School activation is not valid.",
                        "license": request.school_license,
                    },
                    status=403,
                )
            return render(
                request,
                "homeApp/license_blocked.html",
                {
                    "hide_global_license_banner": True,
                    "school_license": request.school_license,
                },
                status=403,
            )

        return self.get_response(request)

    def _should_block(self, request):
        if not request.user.is_authenticated:
            return False
        if not request.path.startswith(self.protected_prefixes):
            return False
        if self._is_dashboard_route(request):
            return False
        return not request.school_license.get("is_available", True)

    def _is_dashboard_route(self, request):
        try:
            match = getattr(request, "resolver_match", None) or resolve(request.path_info)
        except Resolver404:
            return False

        namespace = match.namespace or ""
        url_name = match.url_name or ""
        return (namespace, url_name) in self.dashboard_routes
