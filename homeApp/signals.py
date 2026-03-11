from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from django.utils import timezone

from homeApp.models import SchoolSession


@receiver(pre_save, sender=SchoolSession)
def _mark_session_fee_resync_needed(sender, instance, **kwargs):
    if not instance.pk:
        instance._needs_fee_period_resync = False
        return

    old = SchoolSession.objects.filter(pk=instance.pk).values('startDate', 'endDate', 'sessionYear').first()
    if not old:
        instance._needs_fee_period_resync = False
        return

    instance._needs_fee_period_resync = (
        old.get('startDate') != instance.startDate
        or old.get('endDate') != instance.endDate
        or old.get('sessionYear') != instance.sessionYear
    )


@receiver(post_save, sender=SchoolSession)
def _auto_resync_fee_periods_on_session_change(sender, instance, created, **kwargs):
    if created:
        return

    if not getattr(instance, '_needs_fee_period_resync', False):
        return

    # Mark for background/batch processing; avoid heavy inline work on save.
    SchoolSession.objects.filter(pk=instance.pk).update(
        feeResyncStatus='pending',
        feeResyncRequestedAt=timezone.now(),
        feeResyncStartedAt=None,
        feeResyncFinishedAt=None,
        feeResyncUpdatedCount=0,
        feeResyncCreatedCount=0,
        feeResyncError='',
    )
