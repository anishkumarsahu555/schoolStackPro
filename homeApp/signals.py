from django.contrib.auth.models import User
from django.contrib.contenttypes.models import ContentType
from django.db.models.signals import m2m_changed, post_delete, post_save, pre_save
from django.dispatch import receiver
from django.utils import timezone

from homeApp.audit import build_changes, get_client_ip, get_current_request, get_current_user, serialize_model_instance
from homeApp.models import AuditLog, SchoolOwner, SchoolSession
from utils.logger import logger


OWNER_GROUP_NAME = 'Owner'
AUDITED_APP_LABELS = {
    'homeApp',
    'managementApp',
    'financeApp',
    'certificateApp',
    'teacherApp',
    'studentApp',
    'chatApp',
    'transportApp',
    'libraryApp',
    'hostelApp',
}
AUDIT_EXCLUDED_MODELS = {
    ('homeApp', 'AuditLog'),
}


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


def _is_audited_sender(sender):
    meta = getattr(sender, '_meta', None)
    if not meta:
        return False
    if meta.abstract or meta.proxy:
        return False
    if meta.app_label not in AUDITED_APP_LABELS:
        return False
    if (meta.app_label, meta.object_name) in AUDIT_EXCLUDED_MODELS:
        return False
    return True


def _school_session_ids(instance):
    return getattr(instance, 'schoolID_id', None), getattr(instance, 'sessionID_id', None)


def _audit_request_meta():
    request = get_current_request()
    if not request:
        return None, None
    return request.path[:500], get_client_ip(request)


def _create_audit_log(instance, action, changes=None, snapshot=None):
    try:
        path, ip_address = _audit_request_meta()
        request = get_current_request()
        school_id, session_id = _school_session_ids(instance)
        user_agent = request.META.get('HTTP_USER_AGENT', '') if request else ''
        user = get_current_user() or getattr(instance, 'updatedByUserID', None)
        AuditLog.objects.create(
            content_type=ContentType.objects.get_for_model(instance.__class__),
            object_id=instance.pk,
            action=action,
            changes=changes or {},
            snapshot=snapshot or serialize_model_instance(instance),
            schoolID_id=school_id,
            sessionID_id=session_id,
            userID=user if getattr(user, 'pk', None) else None,
            path=path,
            ipAddress=ip_address,
            userAgent=user_agent,
        )
        logger.info(f'Audit log created action={action} model={instance._meta.label} object_id={instance.pk}')
    except Exception as exc:
        logger.exception(f'Unable to create audit log for {instance._meta.label}: {exc}')


@receiver(pre_save)
def _capture_audit_before_save(sender, instance, **kwargs):
    if not _is_audited_sender(sender) or not instance.pk:
        return
    try:
        old_instance = sender.objects.filter(pk=instance.pk).first()
        instance._audit_before = serialize_model_instance(old_instance) if old_instance else {}
    except Exception as exc:
        instance._audit_before = {}
        logger.exception(f'Unable to capture audit before state for {sender._meta.label}: {exc}')


@receiver(post_save)
def _write_audit_after_save(sender, instance, created, **kwargs):
    if not _is_audited_sender(sender):
        return
    snapshot = serialize_model_instance(instance)
    if created:
        _create_audit_log(instance, 'create', changes=snapshot, snapshot=snapshot)
        return
    before = getattr(instance, '_audit_before', {})
    changes = build_changes(before, snapshot)
    if not changes:
        return
    if 'isDeleted' in changes and len(changes) == 1:
        action = 'soft_delete' if changes['isDeleted']['new'] else 'restore'
    else:
        action = 'update'
    _create_audit_log(instance, action, changes=changes, snapshot=snapshot)


@receiver(post_delete)
def _write_audit_after_delete(sender, instance, **kwargs):
    if not _is_audited_sender(sender):
        return
    _create_audit_log(instance, 'delete', snapshot=serialize_model_instance(instance))
