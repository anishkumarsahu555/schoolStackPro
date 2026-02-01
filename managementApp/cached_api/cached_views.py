from django.contrib.auth.decorators import login_required
from managementApp.models import Subjects, Standard
from utils.logger import logger
from utils.custom_response import SuccessResponse, ErrorResponse
from django.core.cache import cache


@login_required
def get_cached_subjects_list_api(request):
    try:
        cache_key = 'subjects_list'+str(request.session['current_session']['Id'])
        cached_data = cache.get(cache_key)
        if cached_data:
            return SuccessResponse("Subjects list fetched successfully", data=cached_data).to_json_response()
        objs = Subjects.objects.filter(isDeleted=False, sessionID_id=request.session['current_session']['Id']).order_by(
            'name')
        data = []
        for obj in objs:
            data_dic = {
                'ID': obj.pk,
                'Name': obj.name

            }
            data.append(data_dic)
        cache.set('subjects_list'+str(request.session['current_session']['Id']), data, timeout=3600)
        return SuccessResponse("Subjects list fetched successfully" ,data=data).to_json_response()
    except Exception as e:
        logger.error(f"Error in get_subjects_list_api: {e}")
        return ErrorResponse("Error in fetching Subjects list").to_json_response()

@login_required
def get_cached_standard_list_api(request):
    try:
        cache_key = 'standard_list'+str(request.session['current_session']['Id'])
        cached_data = cache.get(cache_key)
        if cached_data:
            # cache.clear()
            return SuccessResponse("Standard list fetched successfully", data=cached_data).to_json_response()
        objs = Standard.objects.filter(isDeleted=False,
         sessionID_id=request.session['current_session']['Id'],
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
        cache.set('standard_list'+str(request.session['current_session']['Id']), data, timeout=3600)
        return SuccessResponse("Standard list fetched successfully" ,data=data).to_json_response()
    except Exception as e:
        logger.error(f"Error in get_standard_list_api: {e}")
        return ErrorResponse("Error in fetching Standard list").to_json_response()