from django.contrib.auth.decorators import login_required
from homeApp.models import SchoolSession
from managementApp.models import Subjects, Standard
from utils.get_school_detail import get_school_id
from utils.logger import logger
from utils.custom_response import SuccessResponse, ErrorResponse
from django.core.cache import cache


def _resolve_current_session_id(request):
    current_session = request.session.get('current_session', {})
    session_id = current_session.get('Id')
    if session_id:
        return session_id

    school_id = current_session.get('SchoolID') or get_school_id(request)
    if not school_id:
        return None

    session_obj = SchoolSession.objects.filter(
        schoolID_id=school_id,
        isDeleted=False,
        isCurrent=True,
    ).order_by('-datetime').first()
    if not session_obj:
        session_obj = SchoolSession.objects.filter(
            schoolID_id=school_id,
            isDeleted=False,
        ).order_by('-datetime').first()
    if not session_obj:
        return None

    current_session['Id'] = session_obj.id
    current_session['SchoolID'] = school_id
    current_session['currentSessionYear'] = session_obj.sessionYear
    request.session['current_session'] = current_session
    return session_obj.id


@login_required
def get_cached_subjects_list_api(request):
    try:
        session_id = _resolve_current_session_id(request)
        if not session_id:
            return SuccessResponse("Subjects list fetched successfully", data=[]).to_json_response()
        cache_key = 'subjects_list' + str(session_id)
        cached_data = cache.get(cache_key)
        if cached_data:
            return SuccessResponse("Subjects list fetched successfully", data=cached_data).to_json_response()
        objs = Subjects.objects.filter(isDeleted=False, sessionID_id=session_id).order_by(
            'name')
        data = []
        for obj in objs:
            data_dic = {
                'ID': obj.pk,
                'Name': obj.name

            }
            data.append(data_dic)
        cache.set('subjects_list' + str(session_id), data, timeout=3600)
        return SuccessResponse("Subjects list fetched successfully" ,data=data).to_json_response()
    except Exception as e:
        logger.error(f"Error in get_subjects_list_api: {e}")
        return ErrorResponse("Error in fetching Subjects list").to_json_response()

@login_required
def get_cached_standard_list_api(request):
    try:
        session_id = _resolve_current_session_id(request)
        if not session_id:
            return SuccessResponse("Standard list fetched successfully", data=[]).to_json_response()
        cache_key = 'standard_list' + str(session_id)
        cached_data = cache.get(cache_key)
        if cached_data:
            # cache.clear()
            return SuccessResponse("Standard list fetched successfully", data=cached_data).to_json_response()
        objs = Standard.objects.filter(isDeleted=False,
         sessionID_id=session_id,
         ).order_by(
            'name')
        data =[]
        for obj in objs:
            if obj.section:
                name = obj.name + ' - ' + obj.section
            else:
                name = obj.name
            data_dic = {
            'ID': obj.pk,
            'Name': name

            }
            data.append(data_dic)           
        cache.set('standard_list' + str(session_id), data, timeout=3600)
        return SuccessResponse("Standard list fetched successfully" ,data=data).to_json_response()
    except Exception as e:
        logger.error(f"Error in get_standard_list_api: {e}")
        return ErrorResponse("Error in fetching Standard list").to_json_response()
