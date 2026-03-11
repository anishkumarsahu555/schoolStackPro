from django.shortcuts import redirect
from homeApp.models import SchoolSession
from homeApp.session_utils import build_current_session_payload, build_session_list_item
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
        session_data = build_current_session_payload(current)
        request.session['current_session'] = session_data
        return True   
    except Exception as e:
        logger.error(f"Failed to get current session: {e}")
        return False


def get_current_school_session(request):
    try:
        current = SchoolSession.objects.get(
            isCurrent__exact=True,
            isDeleted=False,
            schoolID__ownerID__userID_id=request,
        )
        return {'currentSessionYear': current.sessionYear, 'SessionID': current.pk, 'SchoolID': current.schoolID_id}
    except Exception:
        pass

    teacher = TeacherDetail.objects.filter(userID_id=request, isDeleted=False).order_by('-datetime').first()
    if teacher:
        current = SchoolSession.objects.filter(
            isCurrent=True,
            isDeleted=False,
            schoolID_id=teacher.schoolID_id,
        ).order_by('-datetime').first()
        if not current and teacher.sessionID_id:
            current = SchoolSession.objects.filter(pk=teacher.sessionID_id, isDeleted=False).first()
        if current:
            return {'currentSessionYear': current.sessionYear, 'SessionID': current.pk, 'SchoolID': current.schoolID_id}

    student = Student.objects.filter(userID_id=request, isDeleted=False).order_by('-datetime').first()
    if student:
        current = SchoolSession.objects.filter(
            isCurrent=True,
            isDeleted=False,
            schoolID_id=student.schoolID_id,
        ).order_by('-datetime').first()
        if not current and student.sessionID_id:
            current = SchoolSession.objects.filter(pk=student.sessionID_id, isDeleted=False).first()
        if current:
            return {'currentSessionYear': current.sessionYear, 'SessionID': current.pk, 'SchoolID': current.schoolID_id}

    raise SchoolSession.DoesNotExist('No active school session found for this user')


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
        session_data = build_session_list_item(session)
        session_list.append(session_data)
    request.session['session_list'] = session_list


def get_user_school_id(request):
    return request.user.schoolID_id
