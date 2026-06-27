from django.contrib.auth import authenticate, login, logout
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.tokens import default_token_generator
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.validators import validate_email
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.http import JsonResponse, HttpResponse
from django.shortcuts import render, redirect
from django.template.loader import render_to_string
from django.templatetags.static import static
from django.urls import reverse
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.conf import settings
import os
import threading
from io import BytesIO

from homeApp.auth_services import (
    create_access_link,
    create_email_verification,
    email_is_configured,
    email_is_verified_for_user,
    get_verified_password_reset_email,
    get_password_reset_email,
    get_user_by_username,
    sync_profile_password,
    token_hash,
    update_user_profile_email,
)
from homeApp.branding import get_school_branding
from homeApp.models import AccessLink, EmailVerification, SchoolOwner, SchoolDetail
from homeApp.owner_access import school_owner_q
from homeApp.utils import init_session, get_all_session_list, custom_login_required, login_required
from managementApp.access_control import has_management_permission, init_staff_management_session, user_has_management_access
from managementApp.models import TeacherDetail, Student
from utils.custom_decorators import check_groups
from utils.logger import logger


def _render_error_page(request, *, status_code, title, heading, message, accent="blue"):
    context = {
        "error_status_code": status_code,
        "error_title": title,
        "error_heading": heading,
        "error_message": message,
        "error_accent": accent,
    }
    return render(request, "homeApp/errors/error_page.html", context=context, status=status_code)


def error_403(request, exception=None):
    return _render_error_page(
        request,
        status_code=403,
        title="Forbidden",
        heading="Access denied",
        message="You do not have permission to view this page. Please contact your administrator if you think this is a mistake.",
        accent="rose",
    )


def error_404(request, exception=None):
    return _render_error_page(
        request,
        status_code=404,
        title="Page Not Found",
        heading="Page not found",
        message="The page you are looking for does not exist or may have been moved.",
        accent="blue",
    )


def error_500(request):
    return _render_error_page(
        request,
        status_code=500,
        title="Server Error",
        heading="Something went wrong",
        message="The server hit an unexpected problem. Please try again in a moment.",
        accent="amber",
    )


def login_page(request):
    return render(request, 'homeApp/login.html')


def forgot_password(request):
    return render(request, 'homeApp/forgot_password.html')


def forgot_password_sent(request):
    return render(request, 'homeApp/forgot_password_sent.html')


def _send_password_reset_email_async(subject, text_message, html_message, recipient_email):
    def worker():
        try:
            email = EmailMultiAlternatives(
                subject=subject,
                body=text_message,
                from_email=settings.DEFAULT_FROM_EMAIL,
                to=[recipient_email],
            )
            email.attach_alternative(html_message, "text/html")
            email.send(fail_silently=False)
        except Exception as exc:
            logger.error('Email delivery failed recipient=%s error=%s', recipient_email, exc)

    threading.Thread(target=worker, daemon=True).start()


def _send_account_email_async(subject, text_message, html_message, recipient_email):
    _send_password_reset_email_async(subject, text_message, html_message, recipient_email)


def _send_email_verification_link(request, user, email):
    if not email or not email_is_configured():
        return False
    verification, verify_url = create_email_verification(user=user, email=email, request=request)
    role, profile = get_password_reset_email(user)[1:]
    context = {
        'user': user,
        'profile': profile,
        'role': role,
        'verify_url': verify_url,
        'verification': verification,
    }
    subject = 'Verify your SchoolsStack email address'
    text_message = render_to_string('homeApp/emails/email_verification.txt', context)
    html_message = render_to_string('homeApp/emails/email_verification.html', context)
    _send_account_email_async(subject, text_message, html_message, email)
    return True


@require_POST
def send_password_reset_link(request):
    user = get_user_by_username(request.POST.get('userName'))
    if user:
        email, role, profile = get_verified_password_reset_email(user)
        if email and email_is_configured():
            uid = urlsafe_base64_encode(force_bytes(user.pk))
            token = default_token_generator.make_token(user)
            reset_url = request.build_absolute_uri(
                reverse('homeApp:reset_password', kwargs={'uidb64': uid, 'token': token})
            )
            context = {
                'user': user,
                'profile': profile,
                'role': role,
                'reset_url': reset_url,
            }
            subject = 'Reset your SchoolsStack password'
            text_message = render_to_string('homeApp/emails/password_reset.txt', context)
            html_message = render_to_string('homeApp/emails/password_reset.html', context)
            _send_password_reset_email_async(subject, text_message, html_message, email)
    return redirect('homeApp:forgot_password_sent')


def verify_email(request, token):
    hashed_token = token_hash(token or '')
    with transaction.atomic():
        verification = EmailVerification.objects.select_for_update().select_related('userID').filter(tokenHash=hashed_token).first()
        if not verification or not verification.is_usable or not verification.userID.is_active:
            return render(request, 'homeApp/email_verification_result.html', {'invalid_link': True}, status=400)
        verification.verifiedAt = timezone.now()
        verification.save(update_fields=['verifiedAt', 'lastUpdatedOn'])

    return render(request, 'homeApp/email_verification_result.html', {'verified': True})


def reset_password(request, uidb64, token):
    user = None
    try:
        user_id = force_str(urlsafe_base64_decode(uidb64))
        user = User.objects.get(pk=user_id, is_active=True)
    except Exception:
        user = None

    if not user or not default_token_generator.check_token(user, token):
        return render(request, 'homeApp/reset_password.html', {'invalid_link': True})

    if request.method == 'POST':
        new_password = request.POST.get('new_password', '')
        confirm_password = request.POST.get('confirm_password', '')
        errors = []
        if new_password != confirm_password:
            errors.append('New password and confirm password do not match.')
        else:
            try:
                validate_password(new_password, user)
            except ValidationError as exc:
                errors.extend(exc.messages)
        if errors:
            return render(request, 'homeApp/reset_password.html', {'errors': errors})

        user.set_password(new_password)
        user.save()
        sync_profile_password(user, new_password)
        return render(request, 'homeApp/reset_password.html', {'reset_complete': True})

    return render(request, 'homeApp/reset_password.html')


def user_logout(request):
    request.session.flush()
    logout(request)
    return redirect("homeApp:login_page")


def _client_ip(request):
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


def _quick_link_target(request, target_type, target_id):
    current_school_id = request.session.get('current_session', {}).get('SchoolID')
    if target_type == 'student':
        if not has_management_permission(request.user, 'students', 'edit'):
            raise PermissionDenied
        target = Student.objects.select_related('userID', 'schoolID').filter(pk=target_id, isDeleted=False).first()
        purpose = 'student_quick_login'
    elif target_type == 'staff':
        if not has_management_permission(request.user, 'staff', 'edit'):
            raise PermissionDenied
        target = TeacherDetail.objects.select_related('userID', 'schoolID').filter(pk=target_id, isDeleted=False).first()
        purpose = 'staff_quick_login'
    else:
        target = None
        purpose = None

    if not target or not target.userID_id:
        return None, None
    if current_school_id and target.schoolID_id != current_school_id:
        raise PermissionDenied
    return target, purpose


def _invalid_access_link_response(request):
    return render(
        request,
        'homeApp/login.html',
        {'access_link_error': 'This access link is invalid, expired, revoked, or already used.'},
        status=400,
    )


@login_required
@require_POST
def generate_access_link(request, target_type, target_id):
    target, purpose = _quick_link_target(request, target_type, target_id)
    if not target:
        return JsonResponse({'success': False, 'message': 'User account is not linked for this profile.'}, status=404)

    try:
        expires_hours = int(request.POST.get('expires_hours') or 24)
        max_uses = int(request.POST.get('max_uses') or 1)
    except ValueError:
        expires_hours = 24
        max_uses = 1

    expires_hours = min(max(expires_hours, 1), 168)
    max_uses = min(max(max_uses, 1), 20)
    access_link, access_url = create_access_link(
        target_user=target.userID,
        created_by=request.user,
        purpose=purpose,
        request=request,
        expires_hours=expires_hours,
        max_uses=max_uses,
    )
    return JsonResponse({
        'success': True,
        'url': access_url,
        'expiresAt': access_link.expiresAt.strftime('%d-%m-%Y %I:%M %p'),
        'maxUses': access_link.maxUses,
    })


def access_link_login(request, token):
    hashed_token = token_hash(token or '')
    access_link = AccessLink.objects.select_related('userID').filter(tokenHash=hashed_token).first()
    if not access_link or not access_link.is_usable or not access_link.userID.is_active:
        return _invalid_access_link_response(request)

    if request.method == 'POST':
        new_password = request.POST.get('new_password', '')
        confirm_password = request.POST.get('confirm_password', '')
        errors = []
        if new_password != confirm_password:
            errors.append('New password and confirm password do not match.')
        else:
            try:
                validate_password(new_password, access_link.userID)
            except ValidationError as exc:
                errors.extend(exc.messages)
        if errors:
            return render(request, 'homeApp/access_link_set_password.html', {
                'access_link': access_link,
                'errors': errors,
            })

        with transaction.atomic():
            access_link = AccessLink.objects.select_for_update().select_related('userID').filter(tokenHash=hashed_token).first()
            if not access_link or not access_link.is_usable or not access_link.userID.is_active:
                return _invalid_access_link_response(request)
            user = access_link.userID
            user.set_password(new_password)
            user.save()
            sync_profile_password(user, new_password)
            access_link.usedCount += 1
            access_link.usedAt = timezone.now()
            access_link.lastUsedIpAddress = _client_ip(request)
            access_link.save(update_fields=['usedCount', 'usedAt', 'lastUsedIpAddress', 'lastUpdatedOn'])

        return render(request, 'homeApp/access_link_set_password.html', {'password_set': True})

    return render(request, 'homeApp/access_link_set_password.html', {'access_link': access_link})


@csrf_exempt
def post_login(request):
    if request.method == 'POST':
        username = request.POST.get('userName')
        password = request.POST.get('password')
        user = authenticate(request, username=username, password=password)
        if user is not None:
            login(request, user)
            groups = set(user.groups.values_list('name', flat=True))

            if 'Admin' in groups or 'Owner' in groups:
                if not init_session(request):
                    logout(request)
                    return JsonResponse({
                        'message': 'fail',
                        'detail': 'No active school session is assigned to this owner account.',
                    }, safe=False)
                get_all_session_list(request)
                return JsonResponse({'message': 'success', 'data': '/home/'}, safe=False)
            elif user_has_management_access(user):
                if init_staff_management_session(request):
                    return JsonResponse({'message': 'success', 'data': '/home/'}, safe=False)
                logout(request)
                return JsonResponse({
                    'message': 'fail',
                    'detail': 'No active school session is assigned to this staff account.',
                }, safe=False)
            elif 'Teaching' in groups:
                return JsonResponse({'message': 'success', 'data': '/teacher/home/'}, safe=False)
            elif 'Student' in groups:
                return JsonResponse({'message': 'success', 'data': '/student/'}, safe=False)
            else:
                return redirect('homeApp:homepage')

        else:
            return JsonResponse({'message': 'fail'}, safe=False)
    else:
        return JsonResponse({'message': 'fail'}, safe=False)

@custom_login_required
def homepage(request):
    if request.user.is_authenticated and (
            'Admin' in request.user.groups.values_list('name', flat=True) or 'Owner' in request.user.groups.values_list(
            'name', flat=True)):
        return redirect('managementApp:admin_home')
    elif request.user.is_authenticated and user_has_management_access(request.user):
        if init_staff_management_session(request):
            return redirect('managementApp:admin_home')
    elif request.user.is_authenticated and 'Teaching' in request.user.groups.values_list('name', flat=True):
        return redirect('teacherApp:teacher_home')
    elif request.user.is_authenticated and 'Student' in request.user.groups.values_list('name', flat=True):
        return redirect('studentApp:student_home')
    else:
        return render(request, 'homeApp/login.html')

@custom_login_required
@check_groups('Admin', 'Owner')
def admin_home(request):
    context = {
    }
    return render(request, 'managementApp/index.html', context)


@custom_login_required
@check_groups('Admin', 'Owner')
def manage_class(request):
    context = {
    }
    return render(request, 'managementApp/class.html', context)


@login_required
def profile_page(request):
    user = request.user
    groups = set(user.groups.values_list('name', flat=True))

    role_label = 'Owner'
    profile_name = user.get_full_name() or user.username
    profile_email = user.email or ''
    profile_phone = 'N/A'
    profile_photo_url = None
    profile_photo_small_url = None
    extra_rows = []

    if 'Student' in groups:
        role_label = 'Student'
        student = Student.objects.filter(userID_id=user.id, isDeleted=False).order_by('-datetime').first()
        if student:
            profile_name = student.name or profile_name
            profile_email = student.email or profile_email
            profile_phone = student.phoneNumber or 'N/A'
            if student.photo:
                profile_photo_url = student.photo.medium.url
                profile_photo_small_url = student.photo.thumbnail.url
            extra_rows = [
                ('Class', str(student.standardID) if student.standardID else 'N/A'),
                ('Roll', student.roll or 'N/A'),
                ('Gender', student.gender or 'N/A'),
                ('Date of Joining', student.dateOfJoining.strftime('%d-%m-%Y') if student.dateOfJoining else 'N/A'),
            ]
    elif 'Teaching' in groups:
        role_label = 'Teacher'
        teacher = TeacherDetail.objects.filter(userID_id=user.id, isDeleted=False).order_by('-datetime').first()
        if teacher:
            profile_name = teacher.name or profile_name
            profile_email = teacher.email or profile_email
            profile_phone = teacher.phoneNumber or 'N/A'
            if teacher.photo:
                profile_photo_url = teacher.photo.medium.url
                profile_photo_small_url = teacher.photo.thumbnail.url
            extra_rows = [
                ('Employee Code', teacher.employeeCode or 'N/A'),
                ('Staff Type', teacher.staffType or 'N/A'),
                ('Qualification', teacher.qualification or 'N/A'),
                ('Date of Joining', teacher.dateOfJoining.strftime('%d-%m-%Y') if teacher.dateOfJoining else 'N/A'),
            ]
    else:
        owner = SchoolOwner.objects.filter(userID_id=user.id, isDeleted=False).order_by('-datetime').first()
        school = SchoolDetail.objects.filter(school_owner_q(owner), isDeleted=False).distinct().order_by('-datetime').first() if owner else None
        if owner:
            profile_name = owner.name or profile_name
            profile_email = owner.email or profile_email
            profile_phone = owner.phoneNumber or 'N/A'
        extra_rows = [
            ('School', school.schoolName if school and school.schoolName else 'N/A'),
            ('City', school.city if school and school.city else 'N/A'),
            ('State', school.state if school and school.state else 'N/A'),
            ('Country', school.country if school and school.country else 'N/A'),
        ]

    context = {
        'role_label': role_label,
        'profile_name': profile_name,
        'profile_email': profile_email,
        'profile_email_display': profile_email or 'N/A',
        'profile_missing_email': not bool(profile_email),
        'profile_email_verified': email_is_verified_for_user(user, profile_email),
        'profile_phone': profile_phone,
        'profile_photo_url': profile_photo_url,
        'profile_photo_small_url': profile_photo_small_url or profile_photo_url,
        'profile_username': user.username or 'N/A',
        'extra_rows': extra_rows,
    }
    if role_label == 'Student':
        return render(request, 'studentApp/profile.html', context)
    if role_label == 'Teacher':
        return render(request, 'teacherApp/profile.html', context)
    return render(request, 'managementApp/profile.html', context)


@login_required
def update_email(request):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'Invalid request method.'}, safe=False, status=405)

    email = (request.POST.get('email') or '').strip()
    if not email:
        return JsonResponse({'success': False, 'message': 'Email address is required.'}, safe=False, status=400)

    try:
        validate_email(email)
    except ValidationError:
        return JsonResponse({'success': False, 'message': 'Enter a valid email address.'}, safe=False, status=400)

    update_user_profile_email(request.user, email)
    sent = _send_email_verification_link(request, request.user, email)
    message = 'Email address updated. Please verify it from the link we sent.'
    if not sent:
        message = 'Email address updated, but verification email could not be sent because email is not configured.'
    return JsonResponse({
        'success': True,
        'message': message,
        'email': email,
        'emailVerified': False,
        'verificationSent': sent,
    }, safe=False)


@login_required
def change_password(request):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'Invalid request method.'}, safe=False, status=405)

    current_password = request.POST.get('current_password', '')
    new_password = request.POST.get('new_password', '')
    confirm_password = request.POST.get('confirm_password', '')

    if not current_password or not new_password or not confirm_password:
        return JsonResponse({'success': False, 'message': 'All password fields are required.'}, safe=False, status=400)

    if new_password != confirm_password:
        return JsonResponse({'success': False, 'message': 'New password and confirm password do not match.'}, safe=False, status=400)

    if len(new_password) < 8:
        return JsonResponse({'success': False, 'message': 'New password must be at least 8 characters.'}, safe=False, status=400)

    user = request.user
    if not user.check_password(current_password):
        return JsonResponse({'success': False, 'message': 'Current password is incorrect.'}, safe=False, status=400)

    user.set_password(new_password)
    user.save()
    update_session_auth_hash(request, user)

    groups = set(user.groups.values_list('name', flat=True))
    if 'Student' in groups:
        student = Student.objects.filter(userID_id=user.id, isDeleted=False).order_by('-datetime').first()
        if student:
            student.password = new_password
            student.save(update_fields=['password', 'lastUpdatedOn'])
    elif 'Teaching' in groups:
        teacher = TeacherDetail.objects.filter(userID_id=user.id, isDeleted=False).order_by('-datetime').first()
        if teacher:
            teacher.password = new_password
            teacher.save(update_fields=['password', 'lastUpdatedOn'])
    else:
        owner = SchoolOwner.objects.filter(userID_id=user.id, isDeleted=False).order_by('-datetime').first()
        if owner:
            owner.password = new_password
            owner.save(update_fields=['password', 'lastUpdatedOn'])

    return JsonResponse({'success': True, 'message': 'Password changed successfully.', 'color': 'green'}, safe=False)


def dynamic_manifest(request):
    branding = get_school_branding(request)
    school_id = branding.get('school_id') or 0
    school_name = branding.get('school_name') or 'SCHOOLS-STACK'

    icon_192 = request.build_absolute_uri(
        f"{reverse('homeApp:dynamic_app_icon', kwargs={'size': 192})}?sid={school_id}"
    )

    icon_512 = request.build_absolute_uri(
        f"{reverse('homeApp:dynamic_app_icon', kwargs={'size': 512})}?sid={school_id}"
    )



    data = {
        "name": school_name,
        "short_name": school_name[:12] if school_name else "SchoolsStack",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "orientation": "portrait",
        "background_color": "#1a73e8",
        "theme_color": "#1a73e8",
        "icons": [
            {"src": icon_192, "sizes": "192x192", "type": "image/png"},
            {"src": icon_512, "sizes": "512x512", "type": "image/png"},
            {"src": icon_192, "sizes": "192x192", "type": "image/png", "purpose": "maskable"},
            {"src": icon_512, "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
    }

    response = JsonResponse(data)
    response["Content-Type"] = "application/manifest+json"
    response["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


def dynamic_app_icon(request, size):
    target_size = int(size)
    if target_size not in (192, 512):
        return HttpResponse(status=404)

    branding = get_school_branding(request)
    school_id = branding.get('school_id')
    school = None
    if school_id:
        school = SchoolDetail.objects.only("id", "logo").filter(pk=school_id, isDeleted=False).first()

    try:
        from PIL import Image, ImageOps
    except Exception:
        fallback_path = os.path.join(settings.BASE_DIR, 'static', 'sw', 'images', f'icon-{target_size}.png')
        with open(fallback_path, 'rb') as file_handle:
            content = file_handle.read()
        response = HttpResponse(content, content_type='image/png')
        response["Cache-Control"] = "private, max-age=300"
        return response

    if school and school.logo:
        try:
            school.logo.open('rb')
            image = Image.open(school.logo.file).convert('RGBA')
            resampling = getattr(getattr(Image, 'Resampling', Image), 'LANCZOS', Image.LANCZOS)
            fitted = ImageOps.contain(image, (target_size, target_size), resampling)
            canvas = Image.new('RGBA', (target_size, target_size), (255, 255, 255, 0))
            offset_x = (target_size - fitted.width) // 2
            offset_y = (target_size - fitted.height) // 2
            canvas.paste(fitted, (offset_x, offset_y), fitted)
            output = BytesIO()
            canvas.save(output, format='PNG', optimize=True)
            response = HttpResponse(output.getvalue(), content_type='image/png')
            response["Cache-Control"] = "private, max-age=300"
            return response
        except Exception:
            pass
        finally:
            try:
                school.logo.close()
            except Exception:
                pass

    fallback_path = os.path.join(settings.BASE_DIR, 'static', 'sw', 'images', f'icon-{target_size}.png')
    with open(fallback_path, 'rb') as file_handle:
        content = file_handle.read()
    response = HttpResponse(content, content_type='image/png')
    response["Cache-Control"] = "private, max-age=300"
    return response


def dynamic_ios_startup_image(request, width, height):
    target_width = int(width)
    target_height = int(height)
    allowed_sizes = {
        (640, 1136),
        (750, 1334),
        (828, 1792),
        (1170, 2532),
        (1179, 2556),
        (1242, 2208),
        (1284, 2778),
        (1290, 2796),
        (1536, 2048),
        (1668, 2388),
        (2048, 2732),
    }
    if (target_width, target_height) not in allowed_sizes:
        return HttpResponse(status=404)

    school_id = request.GET.get('sid')
    try:
        school_id = int(school_id) if school_id else None
    except Exception:
        school_id = None

    if not school_id:
        branding = get_school_branding(request)
        school_id = branding.get('school_id')

    school = None
    if school_id:
        school = SchoolDetail.objects.only("id", "logo").filter(pk=school_id, isDeleted=False).first()

    try:
        from PIL import Image, ImageOps
    except Exception:
        fallback_path = os.path.join(settings.BASE_DIR, 'static', 'sw', 'images', 'apple-touch-icon.png')
        with open(fallback_path, 'rb') as file_handle:
            content = file_handle.read()
        response = HttpResponse(content, content_type='image/png')
        response["Cache-Control"] = "private, max-age=300"
        return response

    canvas = Image.new('RGB', (target_width, target_height), '#1a73e8')
    logo_size = max(128, min(target_width, target_height) // 4)

    if school and school.logo:
        try:
            school.logo.open('rb')
            image = Image.open(school.logo.file).convert('RGBA')
            resampling = getattr(getattr(Image, 'Resampling', Image), 'LANCZOS', Image.LANCZOS)
            fitted = ImageOps.contain(image, (logo_size, logo_size), resampling)
            offset_x = (target_width - fitted.width) // 2
            offset_y = int(target_height * 0.36) - (fitted.height // 2)
            if offset_y < 0:
                offset_y = (target_height - fitted.height) // 2
            canvas.paste(fitted, (offset_x, offset_y), fitted)
        except Exception:
            pass
        finally:
            try:
                school.logo.close()
            except Exception:
                pass

    output = BytesIO()
    canvas.save(output, format='PNG', optimize=True)
    response = HttpResponse(output.getvalue(), content_type='image/png')
    response["Cache-Control"] = "private, max-age=3600"
    return response


def service_worker(request):
    sw_path = os.path.join(settings.BASE_DIR, 'static', 'sw', 'serviceworker.js')
    with open(sw_path, 'r', encoding='utf-8') as file_handle:
        content = file_handle.read()

    response = HttpResponse(content, content_type='application/javascript')
    response['Service-Worker-Allowed'] = '/'
    response['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return response
