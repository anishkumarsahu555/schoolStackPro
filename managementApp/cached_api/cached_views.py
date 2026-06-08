from django.contrib.auth.decorators import login_required
from homeApp.models import SchoolSession
from managementApp.models import Student, Subjects, Standard, TeacherDetail
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


def _resolve_current_school_id(request):
    current_session = request.session.get('current_session', {})
    school_id = current_session.get('SchoolID') or get_school_id(request)
    if school_id:
        return school_id
    session_id = _resolve_current_session_id(request)
    if not session_id:
        return None
    session_obj = SchoolSession.objects.filter(pk=session_id, isDeleted=False).first()
    return session_obj.schoolID_id if session_obj else None


def _student_option_name(student):
    roll = getattr(student, 'roll', None)
    try:
        roll_label = str(int(float(roll)))
    except Exception:
        roll_label = str(roll or 'N/A')
    class_label = 'N/A'
    if student.standardID:
        class_label = student.standardID.name or 'N/A'
        if student.standardID.section:
            class_label = f'{class_label} {student.standardID.section}'
    return f"{student.name or 'N/A'} - Roll {roll_label} - Class {class_label}"


def _teacher_option_name(teacher):
    code = teacher.employeeCode or teacher.id
    return f"{teacher.name or 'N/A'} ({code})"


def invalidate_cached_student_list(school_id, session_id):
    if not school_id or not session_id:
        return
    keys = [f'student_list_school_{school_id}_session_{session_id}_standard_all']
    standard_ids = Standard.objects.filter(
        schoolID_id=school_id,
        sessionID_id=session_id,
        isDeleted=False,
    ).values_list('id', flat=True)
    keys.extend(f'student_list_school_{school_id}_session_{session_id}_standard_{standard_id}' for standard_id in standard_ids)
    cache.delete_many(keys)


def invalidate_cached_teacher_list(school_id, session_id):
    if not school_id or not session_id:
        return
    cache.delete(f'teacher_list_school_{school_id}_session_{session_id}')


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


@login_required
def get_cached_student_list_api(request):
    try:
        school_id = _resolve_current_school_id(request)
        session_id = _resolve_current_session_id(request)
        standard = request.GET.get('standard') or request.GET.get('standardID') or ''
        try:
            standard_id = int(standard)
        except (TypeError, ValueError):
            standard_id = None
        if not school_id or not session_id:
            return SuccessResponse("Student list fetched successfully", data=[]).to_json_response()
        cache_key = f'student_list_school_{school_id}_session_{session_id}_standard_{standard_id or "all"}'
        cached_data = cache.get(cache_key)
        if cached_data:
            return SuccessResponse("Student list fetched successfully", data=cached_data).to_json_response()
        objs = Student.objects.select_related('standardID').filter(
            schoolID_id=school_id,
            sessionID_id=session_id,
            isDeleted=False,
        )
        if standard_id:
            objs = objs.filter(standardID_id=standard_id)
        objs = objs.order_by('name', 'roll')
        data = []
        for obj in objs:
            name = _student_option_name(obj)
            data.append({
                'ID': obj.pk,
                'Name': name,
                'id': obj.pk,
                'text': name,
                'standardID': obj.standardID_id,
                'roll': obj.roll or '',
                'registrationCode': obj.registrationCode or '',
            })
        cache.set(cache_key, data, timeout=3600)
        return SuccessResponse("Student list fetched successfully", data=data).to_json_response()
    except Exception as e:
        logger.error(f"Error in get_cached_student_list_api: {e}")
        return ErrorResponse("Error in fetching Student list").to_json_response()


@login_required
def get_cached_teacher_list_api(request):
    try:
        school_id = _resolve_current_school_id(request)
        session_id = _resolve_current_session_id(request)
        if not school_id or not session_id:
            return SuccessResponse("Teacher list fetched successfully", data=[]).to_json_response()
        cache_key = f'teacher_list_school_{school_id}_session_{session_id}'
        cached_data = cache.get(cache_key)
        if cached_data:
            return SuccessResponse("Teacher list fetched successfully", data=cached_data).to_json_response()
        objs = TeacherDetail.objects.filter(
            schoolID_id=school_id,
            sessionID_id=session_id,
            isDeleted=False,
        ).order_by('name')
        data = []
        for obj in objs:
            name = _teacher_option_name(obj)
            data.append({
                'ID': obj.pk,
                'Name': name,
                'id': obj.pk,
                'text': name,
                'employeeCode': obj.employeeCode or '',
            })
        cache.set(cache_key, data, timeout=3600)
        return SuccessResponse("Teacher list fetched successfully", data=data).to_json_response()
    except Exception as e:
        logger.error(f"Error in get_cached_teacher_list_api: {e}")
        return ErrorResponse("Error in fetching Teacher list").to_json_response()
