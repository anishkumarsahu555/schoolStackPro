from django.dispatch import receiver, Signal

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
@receiver(pre_save_with_user, sender=StudentAttendance)
@receiver(pre_save_with_user, sender=TeacherAttendance)
@receiver(pre_save_with_user, sender=StudentFee)
@receiver(pre_save_with_user, sender=MarkOfStudentsByExam)
def update_fields_from_signal(sender, instance, **kwargs):
    # Check if the instance is being created (has no primary key yet)
    # if instance.pk is None:
    request = kwargs.get('user', None)
    extra_detail = get_current_school_session()
    action_by = action_taken_by(request)
    instance.sessionID_id = extra_detail['SessionID']
    instance.schoolID_id = extra_detail['SchoolID']
    instance.lastEditedBy = action_by['actionTakenBy']
    instance.save()

# Connect the signal
# pre_save.connect(update_fields_from_signal, sender=Standard)
