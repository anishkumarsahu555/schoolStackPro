from homeApp.utils import get_current_school_session, action_taken_by
from schoolApp.models import *
from django.db.models.signals import pre_save
from django.dispatch import receiver, Signal

pre_save_with_user = Signal()

@receiver(pre_save_with_user, sender=Standard)
def update_fields_from_signal(sender, instance, **kwargs):
    # Check if the instance is being created (has no primary key yet)
    if instance.pk is None:
        request = kwargs.get('user', None)
        extra_detail = get_current_school_session()
        action_by = action_taken_by(request)
        instance.sessionID_id = extra_detail['SessionID']
        instance.schoolID_id = extra_detail['SchoolID']
        instance.lastEditedBy = action_by['actionTakenBy']
        instance.save()


# Connect the signal
# pre_save.connect(update_fields_from_signal, sender=Standard)
