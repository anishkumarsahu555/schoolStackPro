from django.shortcuts import redirect
from homeApp.models import SchoolSession
from managementApp.models import *


def init_session(func):
    def wrapper(request, *args, **kwargs):
        current = SchoolSession.objects.get(isCurrent__exact=True, isDeleted=False)
        session_data = {
            'currentSessionYear': current.sessionYear,
            'Id': current.pk
        }
        request.session['current_session'] = session_data
        # Call the decorated function
        return func(request, *args, **kwargs)
    return wrapper


def get_current_school_session():
    current = SchoolSession.objects.get(isCurrent__exact=True, isDeleted=False)
    return {'currentSessionYear': current.sessionYear, 'SessionID': current.pk, 'SchoolID': current.schoolID_id}


def action_taken_by(user):
    try:
        user_detail = TeacherDetail.objects.get(userID_id=user)
        return {'actionTakenBy': user_detail.name + ' - ' + user_detail.username}
    except:
        return {'actionTakenBy': 'N/A'}


def login_required(func):
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('homeApp:login_page')
        return func(request, *args, **kwargs)
    return wrapper


def get_all_session_list(request):
    sessions_years = SchoolSession.objects.filter(isDeleted=False).order_by('-datetime')
    session_list = []
    for session in sessions_years:
        session_data = {
            'currentSessionYear': session.sessionYear,
            'Id': session.pk
        }
        session_list.append(session_data)
    request.session['session_list'] = session_list
