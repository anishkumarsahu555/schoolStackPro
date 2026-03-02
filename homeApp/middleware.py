from homeApp.models import SchoolSession
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
        current = dict(request.session.get("current_session", {}))
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

        current["Id"] = target_session.pk
        current["currentSessionYear"] = target_session.sessionYear
        current["SchoolID"] = target_session.schoolID_id

        school = school_obj or getattr(target_session, "schoolID", None)
        if school:
            if school.schoolName:
                current["SchoolName"] = school.schoolName
            if getattr(school, "logo", None):
                try:
                    current["SchoolLogo"] = school.logo.url
                except Exception:
                    pass

        request.session["current_session"] = current

        if school_id:
            session_qs = SchoolSession.objects.filter(
                isDeleted=False,
                schoolID_id=school_id,
            ).order_by("-datetime")
            request.session["session_list"] = [
                {"currentSessionYear": s.sessionYear, "Id": s.pk}
                for s in session_qs
            ]
