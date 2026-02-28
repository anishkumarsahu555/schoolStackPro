from django.contrib.auth import authenticate, login, logout
from django.http import JsonResponse
from django.shortcuts import render, redirect
from django.views.decorators.csrf import csrf_exempt

from homeApp.models import SchoolOwner, SchoolDetail
from homeApp.utils import init_session, get_all_session_list, custom_login_required
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

            if 'Admin' in user.groups.values_list('name', flat=True) or 'Owner' in user.groups.values_list('name', flat=True):
                return JsonResponse({'message': 'success', 'data': '/home/'}, safe=False)
            elif 'Teaching' in user.groups.values_list('name', flat=True):
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


@custom_login_required
def profile_page(request):
    user = request.user
    groups = set(user.groups.values_list('name', flat=True))

    role_label = 'Owner'
    profile_name = user.get_full_name() or user.username
    profile_email = user.email or 'N/A'
    profile_phone = 'N/A'
    profile_photo_url = None
    extra_rows = []

    if 'Student' in groups:
        role_label = 'Student'
        student = Student.objects.filter(userID_id=user.id, isDeleted=False).order_by('-datetime').first()
        if student:
            profile_name = student.name or profile_name
            profile_email = student.email or profile_email
            profile_phone = student.phoneNumber or 'N/A'
            if student.photo:
                profile_photo_url = student.photo.thumbnail.url
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
                profile_photo_url = teacher.photo.thumbnail.url
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
        'profile_username': user.username or 'N/A',
        'extra_rows': extra_rows,
    }
    if role_label == 'Student':
        return render(request, 'studentApp/profile.html', context)
    return render(request, 'managementApp/profile.html', context)
