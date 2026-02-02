from django.shortcuts import redirect
from homeApp.models import SchoolSession
from managementApp.models import *
from utils.logger import logger
# change to normal function

def custom_login_required(func):
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('homeApp:login_page')
        if not init_session(request):
            return redirect('homeApp:login_page')
        return func(request, *args, **kwargs)
    return wrapper


def init_session(request):
    try:
        if not request.user.is_authenticated:
            logger.error("User is not authenticated")
            return False
        current = SchoolSession.objects.get(isCurrent__exact=True, isDeleted=False, schoolID__ownerID__userID_id=request.user.id)
        session_data = {
            'currentSessionYear': current.sessionYear,
            'Id': current.pk,
            'SchoolID': current.schoolID_id,
            'SchoolName': current.schoolID.schoolName,
            'SchoolLogo': current.schoolID.logo.url if current.schoolID.logo else None,
        }
        request.session['current_session'] = session_data
        return True   
    except Exception as e:
        logger.error(f"Failed to get current session: {e}")
        return False


def get_current_school_session(request):
    current = SchoolSession.objects.get(isCurrent__exact=True, isDeleted=False, schoolID__ownerID__userID_id=request)
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
    sessions_years = SchoolSession.objects.filter(isDeleted=False, schoolID__ownerID__userID_id=request.user.id).order_by('-datetime')
    session_list = []
    for session in sessions_years:
        session_data = {
            'currentSessionYear': session.sessionYear,
            'Id': session.pk
        }
        session_list.append(session_data)
    request.session['session_list'] = session_list


def get_user_school_id(request):
    return request.user.schoolID_id
