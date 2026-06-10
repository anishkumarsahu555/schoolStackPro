import hashlib
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone

from homeApp.models import AccessLink, EmailVerification, SchoolOwner
from managementApp.models import Student, TeacherDetail


def resolve_user_profile(user):
    student = Student.objects.filter(userID_id=user.id, isDeleted=False).order_by('-datetime').first()
    if student:
        return 'student', student

    teacher = TeacherDetail.objects.filter(userID_id=user.id, isDeleted=False).order_by('-datetime').first()
    if teacher:
        return 'teacher', teacher

    owner = SchoolOwner.objects.filter(userID_id=user.id, isDeleted=False).order_by('-datetime').first()
    if owner:
        return 'owner', owner

    return None, None


def get_password_reset_email(user):
    role, profile = resolve_user_profile(user)
    profile_email = (getattr(profile, 'email', '') or '').strip() if profile else ''
    return profile_email or (user.email or '').strip(), role, profile


def email_is_verified_for_user(user, email):
    email = (email or '').strip().lower()
    if not email:
        return False
    return EmailVerification.objects.filter(
        userID=user,
        email__iexact=email,
        verifiedAt__isnull=False,
        isRevoked=False,
    ).exists()


def get_verified_password_reset_email(user):
    email, role, profile = get_password_reset_email(user)
    if email and email_is_verified_for_user(user, email):
        return email, role, profile
    return '', role, profile


def user_missing_email(user):
    email, role, profile = get_password_reset_email(user)
    return not bool(email)


def update_user_profile_email(user, email):
    email = (email or '').strip()
    role, profile = resolve_user_profile(user)
    if profile:
        profile.email = email
        profile.save(update_fields=['email', 'lastUpdatedOn'])
    user.email = email
    user.save(update_fields=['email'])
    return role, profile


def create_email_verification(*, user, email, request, expires_hours=48):
    email = (email or '').strip()
    EmailVerification.objects.filter(
        userID=user,
        email__iexact=email,
        verifiedAt__isnull=True,
        isRevoked=False,
    ).update(isRevoked=True)

    token = secrets.token_urlsafe(32)
    verification = EmailVerification.objects.create(
        userID=user,
        email=email,
        tokenHash=token_hash(token),
        expiresAt=timezone.now() + timedelta(hours=expires_hours),
    )
    verify_url = request.build_absolute_uri(reverse('homeApp:verify_email', kwargs={'token': token}))
    return verification, verify_url


def sync_profile_password(user, raw_password):
    role, profile = resolve_user_profile(user)
    if not profile:
        return
    profile.password = raw_password
    profile.save(update_fields=['password', 'lastUpdatedOn'])


def get_post_login_redirect(user):
    groups = set(user.groups.values_list('name', flat=True))
    if 'Admin' in groups or 'Owner' in groups:
        return '/home/'
    if 'Teaching' in groups:
        from managementApp.access_control import user_has_management_access

        if user_has_management_access(user):
            return '/home/'
        return '/teacher/home/'
    if 'Student' in groups:
        return '/student/'
    return '/home/'


def token_hash(token):
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def build_absolute_url(request, route_name, **kwargs):
    return request.build_absolute_uri(reverse(route_name, kwargs=kwargs))


def create_access_link(*, target_user, created_by, purpose, request, expires_hours=24, max_uses=1):
    token = secrets.token_urlsafe(32)
    role, profile = resolve_user_profile(target_user)
    school = getattr(profile, 'schoolID', None)
    access_link = AccessLink.objects.create(
        userID=target_user,
        createdByUserID=created_by,
        schoolID=school,
        purpose=purpose,
        tokenHash=token_hash(token),
        expiresAt=timezone.now() + timedelta(hours=expires_hours),
        maxUses=max(1, int(max_uses or 1)),
    )
    return access_link, request.build_absolute_uri(reverse('homeApp:access_link_login', kwargs={'token': token}))


def get_user_by_username(username):
    username = (username or '').strip()
    if not username:
        return None
    return User.objects.filter(username__iexact=username, is_active=True).first()


def email_is_configured():
    backend = getattr(settings, 'EMAIL_BACKEND', '')
    if backend == 'django.core.mail.backends.console.EmailBackend':
        return True
    return bool(getattr(settings, 'EMAIL_HOST', '') and getattr(settings, 'DEFAULT_FROM_EMAIL', ''))
