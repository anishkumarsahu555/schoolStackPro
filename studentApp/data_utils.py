from managementApp.models import *


class StudentData:
    def __init__(self, request):
        self.request = request
        self.user = self.request.user
        self.session = self.request.session
        self.current_session = self.session.get("current_session", {}).get("Id")
        self._student = None

        if not self.current_session:
            self._bootstrap_session_from_student()

    def _bootstrap_session_from_student(self):
        latest_student = Student.objects.select_related('sessionID', 'schoolID').filter(
            isDeleted=False,
            userID_id=self.user.id
        ).order_by('-datetime').first()
        if not latest_student or not latest_student.sessionID_id:
            return

        self.current_session = latest_student.sessionID_id
        current = dict(self.session.get('current_session', {}))
        current['Id'] = latest_student.sessionID_id
        current['currentSessionYear'] = latest_student.sessionID.sessionYear if latest_student.sessionID else current.get('currentSessionYear')
        if latest_student.schoolID_id:
            current['SchoolID'] = latest_student.schoolID_id
            school_name = latest_student.schoolID.schoolName if latest_student.schoolID else None
            if school_name:
                current['SchoolName'] = school_name
            school_logo = latest_student.schoolID.logo.url if latest_student.schoolID and latest_student.schoolID.logo else None
            if school_logo:
                current['SchoolLogo'] = school_logo
        self.session['current_session'] = current
        self._student = latest_student

    def _resolve_student(self):
        if self._student:
            return self._student
        if self.current_session:
            self._student = Student.objects.filter(
                isDeleted=False,
                sessionID_id=self.current_session,
                userID_id=self.user.id
            ).first()
        if not self._student:
            self._student = Student.objects.filter(
                isDeleted=False,
                userID_id=self.user.id
            ).order_by('-datetime').first()
        return self._student

    def get_student_id(self):
        obj = self._resolve_student()
        return obj.pk if obj else None

    def get_student_class(self):
        obj = self._resolve_student()
        return obj.standardID_id if obj else None

    def get_student_roll(self):
        obj = self._resolve_student()

        return int(float(obj.roll)) if obj else None
