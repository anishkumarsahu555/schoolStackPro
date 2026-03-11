from django.contrib.auth import authenticate, login, logout
from django.contrib.auth import update_session_auth_hash
from django.http import JsonResponse, HttpResponse
from django.shortcuts import render, redirect
from django.templatetags.static import static
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
import os
from io import BytesIO

from homeApp.branding import get_school_branding
from homeApp.models import SchoolOwner, SchoolDetail
from homeApp.utils import init_session, get_all_session_list, custom_login_required, login_required
from managementApp.models import TeacherDetail, Student
from utils.custom_decorators import check_groups


def login_page(request):
    return render(request, 'homeApp/login.html')


def user_logout(request):
    request.session.flush()
    logout(request)
    return redirect("homeApp:login_page")


@csrf_exempt
def post_login(request):
    if request.method == 'POST':
        username = request.POST.get('userName')
        password = request.POST.get('password')
        user = authenticate(request, username=username, password=password)
        if user is not None:
            login(request, user)
            get_all_session_list(request)
            init_session(request)
            groups = set(user.groups.values_list('name', flat=True))

            if 'Admin' in groups or 'Owner' in groups:
                return JsonResponse({'message': 'success', 'data': '/home/'}, safe=False)
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
    profile_email = user.email or 'N/A'
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
        school = SchoolDetail.objects.filter(ownerID=owner, isDeleted=False).order_by('-datetime').first() if owner else None
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
