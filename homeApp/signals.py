from django.contrib.auth.models import User
from django.db.models.signals import m2m_changed, post_save, pre_save
from django.dispatch import receiver
from django.utils import timezone

from homeApp.models import SchoolOwner, SchoolSession


OWNER_GROUP_NAME = 'Owner'


def _user_display_name(user):
    return user.get_full_name() or user.username or f'User {user.pk}'


def ensure_school_owner_for_user(user):
    if not user or not user.pk:
        return None
    if not user.groups.filter(name=OWNER_GROUP_NAME).exists():
        return None

    owner, created = SchoolOwner.objects.get_or_create(
        userID=user,
        defaults={
            'name': _user_display_name(user),
            'email': user.email,
            'username': user.username,
            'isActive': user.is_active,
            'userGroup': OWNER_GROUP_NAME,
            'lastEditedBy': 'user_admin_sync',
            'isDeleted': False,
        },
    )

    update_fields = []
    if not owner.name:
        owner.name = _user_display_name(user)
        update_fields.append('name')
    if owner.email != user.email:
        owner.email = user.email
        update_fields.append('email')
    if owner.username != user.username:
        owner.username = user.username
        update_fields.append('username')
    if owner.isActive != user.is_active:
        owner.isActive = user.is_active
        update_fields.append('isActive')
    if owner.userGroup != OWNER_GROUP_NAME:
        owner.userGroup = OWNER_GROUP_NAME
        update_fields.append('userGroup')
    if owner.isDeleted:
        owner.isDeleted = False
        update_fields.append('isDeleted')
    if update_fields:
        owner.save(update_fields=update_fields + ['lastUpdatedOn'])

    return owner


@receiver(post_save, sender=User)
def _sync_school_owner_profile_on_user_save(sender, instance, **kwargs):
    ensure_school_owner_for_user(instance)


@receiver(m2m_changed, sender=User.groups.through)
def _sync_school_owner_profile_on_group_change(sender, instance, action, reverse, model, pk_set, **kwargs):
    if reverse or action not in {'post_add', 'post_set'}:
        return
    if action == 'post_add' and not model.objects.filter(pk__in=pk_set, name=OWNER_GROUP_NAME).exists():
        return
    ensure_school_owner_for_user(instance)


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
