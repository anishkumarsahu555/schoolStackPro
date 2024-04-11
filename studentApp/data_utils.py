from managementApp.models import *


class StudentData:
    def __init__(self, request):
        self.request = request
        self.user = self.request.user
        self.session = self.request.session
        self.current_session = self.session["current_session"]["Id"]

    def get_student_id(self):
        obj = Student.objects.filter(isDeleted__exact=False, sessionID_id=self.current_session,
                                     userID_id__exact=self.user.id).first()

        return obj.pk if obj else None

    def get_student_class(self):
        obj = Student.objects.filter(isDeleted__exact=False, sessionID_id=self.current_session,
                                     userID_id__exact=self.user.id).first()

        return obj.standardID.pk if obj else None

    def get_student_roll(self):
        obj = Student.objects.filter(isDeleted__exact=False, sessionID_id=self.current_session,
                                     userID_id__exact=self.user.id).first()

        return int(float(obj.roll)) if obj else None
