from django.contrib.auth.models import User
from django.dispatch import Signal, receiver

from homeApp.utils import get_current_school_session, action_taken_by
from managementApp.models import *

pre_save_with_user = Signal()


@receiver(pre_save_with_user, sender=Subjects)
@receiver(pre_save_with_user, sender=Standard)
@receiver(pre_save_with_user, sender=AssignSubjectsToClass)
@receiver(pre_save_with_user, sender=TeacherDetail)
@receiver(pre_save_with_user, sender=Parent)
@receiver(pre_save_with_user, sender=Student)
@receiver(pre_save_with_user, sender=AssignSubjectsToTeacher)
@receiver(pre_save_with_user, sender=Exam)
@receiver(pre_save_with_user, sender=AssignExamToClass)
@receiver(pre_save_with_user, sender=ExamTimeTable)
@receiver(pre_save_with_user, sender=StudentAttendance)
@receiver(pre_save_with_user, sender=TeacherAttendance)
@receiver(pre_save_with_user, sender=StudentFee)
@receiver(pre_save_with_user, sender=MarkOfStudentsByExam)
@receiver(pre_save_with_user, sender=ExamComponentType)
@receiver(pre_save_with_user, sender=GradingPolicy)
@receiver(pre_save_with_user, sender=GradingBand)
@receiver(pre_save_with_user, sender=PassPolicy)
@receiver(pre_save_with_user, sender=ExamSubjectComponentRule)
@receiver(pre_save_with_user, sender=StudentExamComponentMark)
@receiver(pre_save_with_user, sender=SubjectTeacherRemark)
@receiver(pre_save_with_user, sender=TermTeacherRemark)
@receiver(pre_save_with_user, sender=CoScholasticArea)
@receiver(pre_save_with_user, sender=CoScholasticGrade)
@receiver(pre_save_with_user, sender=ProgressReport)
@receiver(pre_save_with_user, sender=ProgressReportSnapshot)
@receiver(pre_save_with_user, sender=EventType)
@receiver(pre_save_with_user, sender=Event)
@receiver(pre_save_with_user, sender=StudentIdCardRecord)
@receiver(pre_save_with_user, sender=LeaveType)
@receiver(pre_save_with_user, sender=LeaveApplication)
@receiver(pre_save_with_user, sender=LeaveActionLog)
def update_fields_from_signal(sender, instance, **kwargs):
    user_id = kwargs.get('user')
    if not user_id:
        return

    user = User.objects.filter(pk=user_id).only('username', 'first_name', 'last_name').first()
    if user:
        full_name = f'{(user.first_name or "").strip()} {(user.last_name or "").strip()}'.strip()
        editor_name = full_name or user.username or 'N/A'
        instance.updatedByUserID_id = user.pk
    else:
        editor_name = action_taken_by(user_id).get('actionTakenBy') or 'N/A'

    if not getattr(instance, 'sessionID_id', None) or not getattr(instance, 'schoolID_id', None):
        extra_detail = get_current_school_session(user_id)
        if extra_detail:
            instance.sessionID_id = extra_detail.get('SessionID')
            instance.schoolID_id = extra_detail.get('SchoolID')

    instance.lastEditedBy = editor_name
    instance.save()

# Connect the signal
# pre_save.connect(update_fields_from_signal, sender=Standard)
