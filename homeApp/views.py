from django.contrib.auth import authenticate, login, logout
from django.http import JsonResponse
from django.shortcuts import render, redirect
from django.views.decorators.csrf import csrf_exempt

from homeApp.utils import init_session, get_all_session_list
from utils.custom_decorators import check_groups


@init_session
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
            if 'Admin' or 'Owner' in request.user.groups.values_list('name', flat=True):
                return JsonResponse({'message': 'success', 'data': '/home/'}, safe=False)
        else:
            return JsonResponse({'message': 'fail'}, safe=False)
    else:
        return JsonResponse({'message': 'fail'}, safe=False)


def homepage(request):
    if request.user.is_authenticated and (
            'Admin' in request.user.groups.values_list('name', flat=True) or 'Owner' in request.user.groups.values_list(
            'name', flat=True)):
        return redirect('/school/')
    else:
        return render(request, 'homeApp/login.html')


@check_groups('Admin', 'Owner')
def admin_home(request):
    context = {
    }
    return render(request, 'schoolApp/index.html', context)


@check_groups('Admin', 'Owner')
def manage_class(request):
    context = {
    }
    return render(request, 'schoolApp/class.html', context)
