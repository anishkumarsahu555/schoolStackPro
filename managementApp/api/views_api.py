from datetime import datetime, timedelta
import json

from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Group
from django.db import transaction, IntegrityError
from django.db.models import Q, Prefetch
from django.http import JsonResponse as DjangoJsonResponse
from django.utils.crypto import get_random_string
from django.utils.html import escape
from django.views.decorators.csrf import csrf_exempt
from django_datatables_view.base_datatable_view import BaseDatatableView

from homeApp.models import SchoolDetail
from managementApp.models import *
from managementApp.signals import pre_save_with_user
from managementApp.leave_utils import approved_leave_for_date, approved_leave_map_for_date
from utils.conts import MONTHS_LIST
from utils.get_school_detail import get_school_id

from utils.json_validator import validate_input
from utils.logger import logger
from utils.custom_response import SuccessResponse, ErrorResponse
from utils.cache_modfier import add_item_to_existing_cache, delete_item_from_existing_cache, update_item_in_existing_cache
from utils.custom_decorators import check_groups
from utils.image_utils import safe_image_url, avatar_image_html, optimize_uploaded_image


def _api_response(payload, safe=False, status=200):
    if isinstance(payload, dict):
        response_type = payload.get("status")
        message = payload.get("message")
        data = payload.get("data")
        extra = {k: v for k, v in payload.items() if k not in {"status", "message", "data"}}

        if response_type == "success":
            return SuccessResponse(
                message or "Request processed successfully.",
                status_code=status,
                data=data,
                extra=extra,
            ).to_json_response()
        if response_type == "error":
            return ErrorResponse(
                message or "Request failed.",
                status_code=status,
                data=data,
                extra=extra,
            ).to_json_response()

    return DjangoJsonResponse(payload, safe=safe, status=status)


def _current_session_id(request):
    return request.session.get("current_session", {}).get("Id")


def _editor_name(user):
    full_name = user.get_full_name().strip()
    return full_name or user.username


def _count_approved_teacher_leave_days(session_id, teacher_id, start_date, end_date):
    leaves = LeaveApplication.objects.filter(
        isDeleted=False,
        sessionID_id=session_id,
        applicantRole='teacher',
        teacherID_id=teacher_id,
        status='approved',
        startDate__lte=end_date,
        endDate__gte=start_date,
    ).only('startDate', 'endDate')

    days = set()
    for leave in leaves:
        overlap_start = max(start_date, leave.startDate)
        overlap_end = min(end_date, leave.endDate)
        if overlap_end < overlap_start:
            continue
        current_day = overlap_start
        while current_day <= overlap_end:
            days.add(current_day)
            current_day += timedelta(days=1)
    return len(days)


def _safe_image_url(image_field, fallback_path='images/default_avatar.svg'):
    return safe_image_url(image_field, fallback_path=fallback_path)


def _avatar_image_html(image_field):
    return avatar_image_html(image_field)


@login_required
@check_groups('Admin', 'Owner')
def get_school_detail_api(request):
    try:
        school_id = request.session.get('current_session', {}).get('SchoolID')
        school = None
        if school_id:
            school = SchoolDetail.objects.filter(pk=school_id, isDeleted=False).first()
        if not school:
            school = SchoolDetail.objects.filter(ownerID__userID_id=request.user.id, isDeleted=False).order_by('-datetime').first()
        if not school:
            return ErrorResponse('School detail not found.').to_json_response()

        data = {
            'id': school.id,
            'schoolName': school.schoolName or '',
            'name': school.name or '',
            'address': school.address or '',
            'city': school.city or '',
            'state': school.state or '',
            'country': school.country or '',
            'pinCode': school.pinCode or '',
            'phoneNumber': school.phoneNumber or '',
            'email': school.email or '',
            'website': school.website or '',
            'logo': school.logo.url if school.logo else '',
        }
        return SuccessResponse('School details fetched successfully.', data=data).to_json_response()
    except Exception as e:
        logger.error(f'Error in get_school_detail_api: {e}')
        return ErrorResponse('Unable to fetch school details.').to_json_response()


@transaction.atomic
@csrf_exempt
@login_required
@check_groups('Admin', 'Owner')
def update_school_detail_api(request):
    if request.method != 'POST':
        return ErrorResponse('Method not allowed.').to_json_response()
    try:
        school_id = request.session.get('current_session', {}).get('SchoolID')
        school = None
        if school_id:
            school = SchoolDetail.objects.filter(pk=school_id, isDeleted=False).first()
        if not school:
            school = SchoolDetail.objects.filter(ownerID__userID_id=request.user.id, isDeleted=False).order_by('-datetime').first()
        if not school:
            return ErrorResponse('School detail not found.').to_json_response()

        school.schoolName = (request.POST.get('schoolName') or '').strip()
        school.name = (request.POST.get('name') or '').strip()
        school.address = (request.POST.get('address') or '').strip()
        school.city = (request.POST.get('city') or '').strip()
        school.state = (request.POST.get('state') or '').strip()
        school.country = (request.POST.get('country') or '').strip()
        school.pinCode = (request.POST.get('pinCode') or '').strip()
        school.phoneNumber = (request.POST.get('phoneNumber') or '').strip()
        school.email = (request.POST.get('email') or '').strip()
        school.website = (request.POST.get('website') or '').strip()
        logo = request.FILES.get('logo')
        if logo:
            school.logo = optimize_uploaded_image(logo, max_width=1024, max_height=1024, jpeg_quality=85)

        if not school.schoolName:
            return ErrorResponse('School name is required.').to_json_response()
        if not school.address:
            return ErrorResponse('Address is required.').to_json_response()

        school.lastEditedBy = _editor_name(request.user)
        school.updatedByUserID_id = request.user.id
        school.save()

        current_session = dict(request.session.get('current_session', {}))
        current_session['SchoolID'] = school.id
        current_session['SchoolName'] = school.schoolName or school.name or current_session.get('SchoolName')
        current_session['SchoolLogo'] = school.logo.url if school.logo else current_session.get('SchoolLogo')
        request.session['current_session'] = current_session

        return SuccessResponse('School details updated successfully.', extra={'color': 'green'}).to_json_response()
    except Exception as e:
        logger.error(f'Error in update_school_detail_api: {e}')
        return ErrorResponse('Unable to update school details.').to_json_response()

# Class ------------------
@transaction.atomic
@csrf_exempt
@login_required
@validate_input(["className","classLocation","hasSection","startRoll0","endRoll0"])
def add_class(request):
    if request.method == 'POST':
        try:
            className = request.POST.get("className")
            classLocation = request.POST.get("classLocation")
            hasSection = request.POST.get("hasSection")
            startRoll0 = request.POST.get("startRoll0")
            endRoll0 = request.POST.get("endRoll0")
            secDetail = request.POST.get("secDetail")

            if hasSection == "No":
                try:
                    Standard.objects.get(name__iexact=className, hasSection=hasSection, isDeleted=False,
                                         sessionID_id=request.session["current_session"]["Id"])
                    logger.info( f"Class already exists {request.session["current_session"]["Id"]}- {className}")                     
                    return _api_response(
                        {'status': 'success', 'message': 'Class already exists. Please change the name.',
                         'color': 'info'}, safe=False)
                except:
                    instance = Standard()
                    instance.name = className
                    instance.hasSection = hasSection
                    instance.classLocation = classLocation
                    instance.startingRoll = startRoll0
                    instance.endingRoll = endRoll0
                    pre_save_with_user.send(sender=Standard, instance=instance, user=request.user.pk)
                    instance.save()
                    new_data = {
                        "ID": instance.pk,
                        "Name": instance.name

                    }
                    add_item_to_existing_cache("standard_list"+str(request.session["current_session"]["Id"]), new_data)
                    logger.info( f"Class created successfully {request.session["current_session"]["Id"]}- {instance.name}")
                    return _api_response(
                        {'status': 'success', 'message': 'New class created successfully.', 'color': 'success'},
                        safe=False)
            elif hasSection == 'Yes':
                try:

                    sectionArray = secDetail.split('@')
                    result = []
                    # Split each part by "|"
                    for part in sectionArray:
                        split2 = part.split("|")
                        result.append(split2)
                    # Remove the last empty element if it exists
                    if result[-1] == ['']:
                        result.pop()
                    for i in result:
                        try:
                            Standard.objects.get(name__iexact=className, hasSection=hasSection, section__iexact=i[0],
                                                 isDeleted=False,sessionID_id=request.session["current_session"]["Id"])
                            return _api_response(
                                {'status': 'success', 'message': 'Class already exists. Please change the name.',
                                'color': 'info'}, safe=False)
                        except:
                            instance = Standard()
                            instance.name = className
                            instance.hasSection = hasSection
                            instance.classLocation = classLocation
                            instance.startingRoll = i[1]
                            instance.endingRoll = i[2]
                            instance.section = i[0]
                            pre_save_with_user.send(sender=Standard, instance=instance, user=request.user.pk)
                            instance.save()
                            new_data = {
                            'ID': instance.pk,
                            'Name': instance.name + ' - ' + instance.section if instance.section else instance.name

                            }
                            add_item_to_existing_cache("standard_list"+str(request.session["current_session"]["Id"]), new_data)
                            logger.info( f"Class created successfully {request.session["current_session"]["Id"]}- {instance.name}-{instance.section}")

                    return _api_response(
                        {'status': 'success', 'message': 'New classes created successfully.', 'color': 'success'},
                        safe=False)
                except Exception as e:
                    logger.error(f"Error creating classes: {e}")
                    return _api_response({'status': 'error'}, safe=False)
            return _api_response({'status': 'error'}, safe=False)
        except Exception as e:
            logger.error(f"Error in add_class: {e}")
            return _api_response({'status': 'error'}, safe=False)


@transaction.atomic
@csrf_exempt
@login_required
# @validate_input()
def update_class(request):
    if request.method != 'POST':
        logger.error("Method not allowed")
        return ErrorResponse("Method not allowed").to_json_response()

    data = request.POST.dict()
    try:
        current_session_id = request.session['current_session']['Id']
        obj = Standard.objects.get(
            pk=data['dataIDEdit'],
            isDeleted=False,
            sessionID_id=current_session_id
        )

        teacher_id = data.get("teacherEdit")
        teacher_id = int(teacher_id) if teacher_id and str(teacher_id).strip() else None

        if teacher_id:
            teacher_exists = TeacherDetail.objects.filter(
                pk=teacher_id,
                isDeleted=False,
                sessionID_id=current_session_id,
                isActive='Yes'
            ).exists()
            if not teacher_exists:
                return ErrorResponse("Selected class teacher does not exist in current session.").to_json_response()

            teacher_already_assigned = Standard.objects.filter(
                isDeleted=False,
                sessionID_id=current_session_id,
                classTeacher_id=teacher_id
            ).exclude(pk=obj.pk).exists()
            if teacher_already_assigned:
                return ErrorResponse("This teacher is already assigned as class teacher for another class.").to_json_response()

        obj.name = data["classNameEdit"]
        obj.classLocation = data["classLocationEdit"]
        obj.startingRoll = data.get("startRoll0Edit") or '0'
        obj.endingRoll = data.get("endRoll0Edit") or '0'
        section_value = data.get("section0Edit")
        obj.section = None if section_value in ("", "N/A", None) else section_value
        obj.classTeacher_id = teacher_id
        pre_save_with_user.send(sender=Standard, instance=obj, user=request.user.pk)
        obj.save()

        new_data = {
                    'ID': obj.pk,
                'Name': obj.name + ' - ' + obj.section if obj.section else obj.name
                }
        update_item_in_existing_cache("standard_list"+str(request.session['current_session']['Id']), obj.pk, new_data)
        logger.info("Class detail updated successfully")
        return SuccessResponse("Class detail updated successfully").to_json_response()

    except Standard.DoesNotExist:
        logger.error("Class not found")
        return ErrorResponse("Class not found").to_json_response()
    except Exception as e:
        logger.error(f"Error in update_class: {e}")
        return ErrorResponse("Error in updating Class details").to_json_response()
    


class StandardListJson(BaseDatatableView):
    order_columns = ['name', 'section', 'classTeacher', 'startingRoll', 'endingRoll', 'classLocation', 'lastEditedBy',
                     'lastUpdatedOn']

    def get_initial_queryset(self):
        return Standard.objects.select_related('classTeacher').only(
            'id', 'name', 'section', 'startingRoll', 'endingRoll', 'classLocation',
            'lastEditedBy', 'lastUpdatedOn', 'classTeacher__name'
        ).filter(
            isDeleted__exact=False,
            sessionID_id=self.request.session["current_session"]["Id"],
            # schoolID_id=school_id
        )

    def filter_queryset(self, qs):

        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(name__icontains=search) | Q(section__icontains=search)
                | Q(classTeacher__name__icontains=search) | Q(classLocation__icontains=search)
                | Q(lastEditedBy__icontains=search) | Q(lastUpdatedOn__icontains=search)
            )

        return qs

    def prepare_results(self, qs):
        json_data = []
        for item in qs:
            action = '''<button data-inverted="" data-tooltip="Edit Detail" data-position="left center" data-variation="mini" style="font-size:10px;" onclick = "GetDataDetails('{}')" class="ui circular facebook icon button green">
                <i class="pen icon"></i>
              </button>
              <button data-inverted="" data-tooltip="Delete" data-position="left center" data-variation="mini" style="font-size:10px;" onclick ="delData('{}')" class="ui circular youtube icon button" style="margin-left: 3px">
                <i class="trash alternate icon"></i>
              </button></td>'''.format(item.pk, item.pk)
            teacher = item.classTeacher.name if item.classTeacher and item.classTeacher.name else "N/A"
            json_data.append([
                escape(item.name),
                escape(item.section if item.section else "N/A"),
                escape(teacher),
                escape(item.startingRoll),
                escape(item.endingRoll),
                escape(item.classLocation),
                escape(item.lastEditedBy or 'N/A'),
                escape(item.lastUpdatedOn.strftime('%d-%m-%Y %I:%M %p') if item.lastUpdatedOn else 'N/A'),
                action,

            ])

        return json_data


@login_required
def get_class_detail(request, **kwargs):
    try:
        id = request.GET.get('id')
        obj = Standard.objects.get(pk=id, isDeleted=False, sessionID_id=request.session['current_session']['Id'])
        if obj.classTeacher:
            teacher = obj.classTeacher.name if obj.classTeacher.name else "N/A"
            teacherID = str(obj.classTeacher.pk)
        else:
            teacher = "N/A"
            teacherID = ""
        if obj.hasSection == "Yes":
            section = obj.section
        else:
            section = "N/A"
        obj_dic = {
            'ClassID': obj.pk,
            'Class': obj.name,
            'Location': obj.classLocation,
            'Section': section,
            'StartRoll': obj.startingRoll,
            'EndRoll': obj.endingRoll,
            'Teacher': teacher,
            'TeacherID': str(teacherID)
        }
        return _api_response({'status': 'success', 'data': obj_dic}, safe=False)
    except:
        return _api_response({'status': 'error'}, safe=False)


@transaction.atomic
@csrf_exempt
@login_required
def delete_class(request):
    if request.method == 'POST':
        try:
            id = request.POST.get("dataID")
            instance = Standard.objects.get(pk=int(id), isDeleted=False,
                                            sessionID_id=request.session['current_session']['Id'])
            instance.isDeleted = True
            pre_save_with_user.send(sender=Standard, instance=instance, user=request.user.pk)
            instance.save()
            delete_item_from_existing_cache('standard_list'+str(request.session['current_session']['Id']), id)
            logger.info(f"Class detail deleted successfully {request.session['current_session']['Id']} class name {instance.name}")
            return SuccessResponse("Class detail deleted successfully.").to_json_response()
        except Exception as e:
            logger.error(f"Error in delete_class: {e}")
            return ErrorResponse("Error in deleting Class details").to_json_response()
    else:
        logger.error("Method not allowed")
        return ErrorResponse("Method not allowed").to_json_response()        


# subjects -----------------------------------

# subject

@transaction.atomic
@csrf_exempt
@login_required
def add_subject(request):
    if request.method == 'POST':
        subject_name = request.POST.get("subject_name")
        try:
            Subjects.objects.get(name__iexact=subject_name, isDeleted=False,
                                 sessionID_id=request.session['current_session']['Id'])
            return _api_response(
                {'status': 'success', 'message': 'Subject already exists. Please change the name.', 'color': 'info'},
                safe=False)
        except:
            instance = Subjects()
            instance.name = subject_name
            pre_save_with_user.send(sender=Subjects, instance=instance, user=request.user.pk)
            instance.save()
            new_item = {
                'ID': instance.pk,
                'Name': instance.name
            }
            # add a new item to the cache
            add_item_to_existing_cache('subjects_list'+str(request.session['current_session']['Id']), new_item)
            return _api_response(
                {'status': 'success', 'message': 'New subject created successfully.', 'color': 'success'},
                safe=False)
    return _api_response({'status': 'error'}, safe=False)


class SubjectListJson(BaseDatatableView):
    order_columns = ['name', 'lastEditedBy', 'lastUpdatedOn']

    def get_initial_queryset(self):
        return Subjects.objects.only(
            'id', 'name', 'lastEditedBy', 'datetime', 'lastUpdatedOn'
        ).filter(
            isDeleted__exact=False,
            sessionID_id=self.request.session["current_session"]["Id"]
        )

    def filter_queryset(self, qs):

        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(name__icontains=search)
                | Q(lastEditedBy__icontains=search) | Q(lastUpdatedOn__icontains=search)
            )

        return qs

    def prepare_results(self, qs):
        json_data = []
        for item in qs:
            action = '''<button data-inverted="" data-tooltip="Edit Detail" data-position="left center" data-variation="mini" style="font-size:10px;" onclick = "GetDataDetails('{}')" class="ui circular facebook icon button green">
                <i class="pen icon"></i>
              </button>
              <button data-inverted="" data-tooltip="Delete" data-position="left center" data-variation="mini" style="font-size:10px;" onclick ="delData('{}')" class="ui circular youtube icon button" style="margin-left: 3px">
                <i class="trash alternate icon"></i>
              </button></td>'''.format(item.pk, item.pk),

            json_data.append([
                escape(item.name),
                escape(item.lastEditedBy or 'N/A'),
                escape(item.lastUpdatedOn.strftime('%d-%m-%Y %I:%M %p') if item.lastUpdatedOn else 'N/A'),
                action,

            ])

        return json_data


@transaction.atomic
@csrf_exempt
@login_required
def delete_subject(request):
    if request.method == 'POST':
        try:
            id = request.POST.get("dataID")
            instance = Subjects.objects.get(pk=int(id), isDeleted=False,
                                            sessionID_id=request.session['current_session']['Id'])
            instance.isDeleted = True
            pre_save_with_user.send(sender=Subjects, instance=instance, user=request.user.pk)
            delete_item_from_existing_cache("subjects_list"+str(request.session['current_session']['Id']), id)
            instance.save()
            return _api_response(
                {'status': 'success', 'message': 'Subject detail deleted successfully.',
                 'color': 'success'}, safe=False)
        except:
            return _api_response({'status': 'error'}, safe=False)
    return _api_response({'status': 'error'}, safe=False)


@login_required
def get_subject_detail(request, **kwargs):
    try:
        id = request.GET.get('id')
        obj = Subjects.objects.get(pk=id, isDeleted=False, sessionID_id=request.session['current_session']['Id'])
        obj_dic = {
            'ID': obj.pk,
            'SubjectName': obj.name,
        }
        return _api_response({'status': 'success', 'data': obj_dic}, safe=False)
    except:
        return _api_response({'status': 'error'}, safe=False)


@transaction.atomic
@csrf_exempt
@login_required
def edit_subject(request):
    if request.method == 'POST':
        subject_name = request.POST.get("subject_name")
        editID = request.POST.get("editID")
        try:
            instance = Subjects.objects.get(pk=int(editID))
            data = Subjects.objects.filter(name__iexact=subject_name, isDeleted=False,
                                           sessionID_id=request.session['current_session']['Id']).exclude(
                pk=int(editID))
            if data.count() > 0:
                return _api_response(
                    {'status': 'success', 'message': 'Subject already exists. Please change the name.',
                     'color': 'info'},
                    safe=False)
            else:
                # instance = Subjects.objects.get(pk=int(editID))
                instance.name = subject_name
                pre_save_with_user.send(sender=Subjects, instance=instance, user=request.user.pk)
                instance.save()
                new_data = {
                    'ID': instance.pk,
                'Name': instance.name
                }
                update_item_in_existing_cache("subjects_list"+str(request.session['current_session']['Id']), editID, new_data)
                return _api_response(
                    {'status': 'success', 'message': 'Subject name updated successfully.', 'color': 'success'},
                    safe=False)
        except:

            return _api_response({'status': 'error'}, safe=False)



# assign subjects to class------------------------------------------------------------------

@transaction.atomic
@csrf_exempt
@login_required
def add_subject_to_class(request):
    if request.method == 'POST':
        try:
            standard = request.POST.get("standard")
            subjects = request.POST.get("subjects")
            subject_list = [int(x) for x in subjects.split(',')]
            for s in subject_list:
                try:
                    AssignSubjectsToClass.objects.get(subjectID_id=int(s), standardID_id=int(standard), isDeleted=False,
                                                      sessionID_id=request.session['current_session']['Id'])
                except:
                    instance = AssignSubjectsToClass()
                    instance.standardID_id = int(standard)
                    instance.subjectID_id = int(s)
                    pre_save_with_user.send(sender=AssignSubjectsToClass, instance=instance, user=request.user.pk)
                    instance.save()
            return _api_response(
                {'status': 'success', 'message': 'New subject created successfully.', 'color': 'success'},
                safe=False)
        except:
            return _api_response({'status': 'error'}, safe=False)


class AssignSubjectToClassListJson(BaseDatatableView):
    order_columns = ['standardID.name', 'subjectID.name', 'lastEditedBy', 'lastUpdatedOn']

    def get_initial_queryset(self):
        return AssignSubjectsToClass.objects.select_related(
            'standardID', 'subjectID'
        ).only(
            'id', 'lastEditedBy', 'lastUpdatedOn',
            'standardID__name', 'standardID__section',
            'subjectID__name'
        ).filter(
            isDeleted__exact=False,
            sessionID_id=self.request.session["current_session"]["Id"]
        ).order_by('standardID__name')

    def filter_queryset(self, qs):
        class_filter = self.request.GET.get('class_filter')
        subject_filter = self.request.GET.get('subject_filter')

        if class_filter and str(class_filter).isdigit():
            qs = qs.filter(standardID_id=int(class_filter))

        if subject_filter and str(subject_filter).isdigit():
            qs = qs.filter(subjectID_id=int(subject_filter))

        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(standardID__name__icontains=search)
                | Q(subjectID__name__icontains=search) | Q(standardID__section__icontains=search)
                | Q(lastEditedBy__icontains=search) | Q(lastUpdatedOn__icontains=search)
            )

        return qs

    def prepare_results(self, qs):
        json_data = []
        for item in qs:
            action = '''
              <button data-inverted="" data-tooltip="Edit Detail" data-position="left center" data-variation="mini" style="font-size:10px;" onclick = "GetDataDetails('{}')" class="ui circular facebook icon button green">
                <i class="pen icon"></i>
              </button>
              <button data-inverted="" data-tooltip="Delete" data-position="left center" data-variation="mini" style="font-size:10px;" onclick ="delData('{}')" class="ui circular youtube icon button" style="margin-left: 3px">
                <i class="trash alternate icon"></i>
              </button></td>'''.format(item.pk, item.pk),
            if item.standardID.section:
                name = item.standardID.name + ' - ' + item.standardID.section
            else:
                name = item.standardID.name

            json_data.append([
                escape(name),
                escape(item.subjectID.name),
                escape(item.lastEditedBy or 'N/A'),
                escape(item.lastUpdatedOn.strftime('%d-%m-%Y %I:%M %p') if item.lastUpdatedOn else 'N/A'),
                action,

            ])

        return json_data


@transaction.atomic
@csrf_exempt
@login_required
def delete_assign_subject_to_class(request):
    if request.method == 'POST':
        try:
            id = request.POST.get("dataID")
            instance = AssignSubjectsToClass.objects.get(pk=int(id), isDeleted=False,
                                                         sessionID_id=request.session['current_session']['Id'])
            instance.isDeleted = True
            pre_save_with_user.send(sender=AssignSubjectsToClass, instance=instance, user=request.user.pk)
            instance.save()
            return _api_response(
                {'status': 'success', 'message': 'Assigned Subject detail deleted successfully.',
                 'color': 'success'}, safe=False)
        except:
            return _api_response({'status': 'error'}, safe=False)
    return _api_response({'status': 'error'}, safe=False)


@login_required
def get_assigned_subject_to_class_detail(request, **kwargs):
    try:
        id = request.GET.get('id')
        obj = AssignSubjectsToClass.objects.get(pk=id, isDeleted=False,
                                                sessionID_id=request.session['current_session']['Id'])
        obj_dic = {
            'StandardID': obj.standardID.pk,
            'SubjectID': obj.subjectID.pk,
            'ID': obj.pk,
        }
        return _api_response({'status': 'success', 'data': obj_dic}, safe=False)
    except:
        return _api_response({'status': 'error'}, safe=False)


@transaction.atomic
@csrf_exempt
@login_required
def update_subject_to_class(request):
    if request.method == 'POST':
        try:
            editID = request.POST.get("editID")
            standard = request.POST.get("standard")
            subjects = request.POST.get("subjects")
            subject_list = [int(x) for x in subjects.split(',')]
            instance = AssignSubjectsToClass.objects.get(pk=int(editID))
            for s in subject_list:
                try:
                    AssignSubjectsToClass.objects.get(subjectID_id=int(s), standardID_id=int(standard), isDeleted=False,
                                                      sessionID_id=request.session['current_session']['Id']).exclude(
                        pk=int(editID))
                except:
                    # instance = AssignSubjectsToClass()
                    instance.standardID_id = int(standard)
                    instance.subjectID_id = int(s)
                    pre_save_with_user.send(sender=AssignSubjectsToClass, instance=instance, user=request.user.pk)
                    instance.save()
            return _api_response(
                {'status': 'success', 'message': 'Detail updated successfully.', 'color': 'success'},
                safe=False)
        except:
            return _api_response({'status': 'error'}, safe=False)


@login_required
def get_subjects_to_class_assign_list_api(request):
    rows = AssignSubjectsToClass.objects.filter(
        isDeleted=False,
        sessionID_id=_current_session_id(request)
    ).values(
        'id', 'standardID__name', 'standardID__section', 'subjectID__name'
    ).order_by('standardID__name')
    data = []
    for row in rows:
        standard = row.get('standardID__name') or 'N/A'
        section = row.get('standardID__section')
        subject = row.get('subjectID__name') or 'N/A'
        name = f"{standard} - {section} - {subject}" if section else f"{standard} - {subject}"
        data.append({'ID': row['id'], 'Name': name})
    return _api_response(
        {'status': 'success', 'data': data,
         'color': 'success'}, safe=False)


@login_required
def get_subjects_to_class_assign_list_with_given_class_api(request):
    standard = request.GET.get('standard')
    try:
        standard_id = int(standard)
    except (TypeError, ValueError):
        return _api_response({'status': 'success', 'data': [], 'color': 'success'}, safe=False)
    rows = AssignSubjectsToClass.objects.filter(
        isDeleted=False,
        standardID_id=standard_id,
        sessionID_id=_current_session_id(request)
    ).values('id', 'subjectID__name').order_by('subjectID__name')
    data = [{'ID': row['id'], 'Name': row['subjectID__name'] or 'N/A'} for row in rows]
    return _api_response(
        {'status': 'success', 'data': data,
         'color': 'success'}, safe=False)


# Assign Subjects To Teacher --------------------------------------------------------------
@transaction.atomic
@csrf_exempt
@login_required
def add_subject_to_teacher(request):
    if request.method == 'POST':
        try:
            standard = request.POST.get("standard")
            teachers = request.POST.get("teachers")
            branch = request.POST.get("branch")

            try:
                AssignSubjectsToTeacher.objects.get(assignedSubjectID_id=int(standard), teacherID_id=int(teachers),
                                                    subjectBranch__iexact=branch, isDeleted=False,
                                                    sessionID_id=request.session['current_session']['Id'])
                return _api_response(
                    {'status': 'success', 'message': 'Subject already assigned successfully.', 'color': 'info'},
                    safe=False)
            except:
                instance = AssignSubjectsToTeacher()
                instance.teacherID_id = int(teachers)
                instance.assignedSubjectID_id = int(standard)
                instance.subjectBranch = branch
                pre_save_with_user.send(sender=AssignSubjectsToTeacher, instance=instance, user=request.user.pk)
                instance.save()
            return _api_response(
                {'status': 'success', 'message': 'Subject assigned successfully.', 'color': 'success'},
                safe=False)
        except:
            return _api_response({'status': 'error'}, safe=False)


class AssignSubjectToTeacherListJson(BaseDatatableView):
    order_columns = ['assignedSubjectID.standardID.name', 'assignedSubjectID.standardID.section',
                     'assignedSubjectID.subjectID.name', 'teacherID.name', 'subjectBranch', 'lastEditedBy', 'lastUpdatedOn']

    def get_initial_queryset(self):
        return AssignSubjectsToTeacher.objects.select_related(
            'assignedSubjectID__standardID', 'assignedSubjectID__subjectID', 'teacherID'
        ).only(
            'id', 'subjectBranch', 'lastEditedBy', 'lastUpdatedOn',
            'assignedSubjectID__standardID__name', 'assignedSubjectID__standardID__section',
            'assignedSubjectID__subjectID__name', 'teacherID__name'
        ).filter(
            isDeleted__exact=False,
            sessionID_id=self.request.session["current_session"]["Id"]
        ).order_by(
            'assignedSubjectID__standardID__name')

    def filter_queryset(self, qs):

        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(assignedSubjectID__standardID__name__icontains=search) | Q(teacherID__name__icontains=search) | Q(
                    teacherID__employeeCode__icontains=search)
                | Q(assignedSubjectID__subjectID__name__icontains=search) | Q(
                    assignedSubjectID__standardID__section__icontains=search) | Q(
                    subjectBranch__icontains=search)
                | Q(lastEditedBy__icontains=search) | Q(lastUpdatedOn__icontains=search)
            )

        return qs

    def prepare_results(self, qs):
        json_data = []
        for item in qs:
            action = '''
              <button data-inverted="" data-tooltip="Edit Detail" data-position="left center" data-variation="mini" style="font-size:10px;" onclick = "GetDataDetails('{}')" class="ui circular facebook icon button green">
                <i class="pen icon"></i>
              </button>
              <button data-inverted="" data-tooltip="Delete" data-position="left center" data-variation="mini" style="font-size:10px;" onclick ="delData('{}')" class="ui circular youtube icon button" style="margin-left: 3px">
                <i class="trash alternate icon"></i>
              </button></td>'''.format(item.pk, item.pk),
            if item.assignedSubjectID.standardID.section:
                section = item.assignedSubjectID.standardID.section
            else:
                section = 'N/A'

            json_data.append([
                escape(item.assignedSubjectID.standardID.name),
                escape(section),
                escape(item.assignedSubjectID.subjectID.name),
                escape(item.teacherID.name),
                escape(item.subjectBranch),
                escape(item.lastEditedBy or 'N/A'),
                escape(item.lastUpdatedOn.strftime('%d-%m-%Y %I:%M %p') if item.lastUpdatedOn else 'N/A'),
                action,

            ])

        return json_data


class AssignSubjectToClassListJson(BaseDatatableView):
    order_columns = ['standardID.name', 'subjectID.name', 'lastEditedBy', 'lastUpdatedOn']

    def get_initial_queryset(self):
        return AssignSubjectsToClass.objects.select_related(
            'standardID', 'subjectID'
        ).only(
            'id', 'lastEditedBy', 'lastUpdatedOn',
            'standardID__name', 'standardID__section',
            'subjectID__name'
        ).filter(
            isDeleted__exact=False,
            sessionID_id=self.request.session["current_session"]["Id"]
        ).order_by('standardID__name')

    def filter_queryset(self, qs):
        class_filter = self.request.GET.get('class_filter')
        subject_filter = self.request.GET.get('subject_filter')

        if class_filter and str(class_filter).isdigit():
            qs = qs.filter(standardID_id=int(class_filter))

        if subject_filter and str(subject_filter).isdigit():
            qs = qs.filter(subjectID_id=int(subject_filter))

        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(standardID__name__icontains=search)
                | Q(subjectID__name__icontains=search) | Q(standardID__section__icontains=search)
                | Q(lastEditedBy__icontains=search) | Q(lastUpdatedOn__icontains=search)
            )

        return qs

    def prepare_results(self, qs):
        json_data = []
        for item in qs:
            action = '''
              <button data-inverted="" data-tooltip="Edit Detail" data-position="left center" data-variation="mini" style="font-size:10px;" onclick = "GetDataDetails('{}')" class="ui circular facebook icon button green">
                <i class="pen icon"></i>
              </button>
              <button data-inverted="" data-tooltip="Delete" data-position="left center" data-variation="mini" style="font-size:10px;" onclick ="delData('{}')" class="ui circular youtube icon button" style="margin-left: 3px">
                <i class="trash alternate icon"></i>
              </button></td>'''.format(item.pk, item.pk),
            if item.standardID.section:
                name = item.standardID.name + ' - ' + item.standardID.section
            else:
                name = item.standardID.name

            json_data.append([
                escape(name),
                escape(item.subjectID.name),
                escape(item.lastEditedBy or 'N/A'),
                escape(item.lastUpdatedOn.strftime('%d-%m-%Y %I:%M %p') if item.lastUpdatedOn else 'N/A'),
                action,

            ])

        return json_data


@transaction.atomic
@csrf_exempt
@login_required
def delete_assign_teacher_to_subject(request):
    if request.method == 'POST':
        try:
            id = request.POST.get("dataID")
            instance = AssignSubjectsToTeacher.objects.get(pk=int(id), isDeleted=False,
                                                           sessionID_id=request.session['current_session']['Id'])
            instance.isDeleted = True
            pre_save_with_user.send(sender=AssignSubjectsToTeacher, instance=instance, user=request.user.pk)
            instance.save()
            return _api_response(
                {'status': 'success', 'message': 'Assigned Teacher detail deleted successfully.',
                 'color': 'success'}, safe=False)
        except:
            return _api_response({'status': 'error'}, safe=False)
    return _api_response({'status': 'error'}, safe=False)


@login_required
def get_assigned_subject_to_teacher_detail(request, **kwargs):
    try:
        id = request.GET.get('id')
        obj = AssignSubjectsToTeacher.objects.get(pk=id, isDeleted=False,
                                                  sessionID_id=request.session['current_session']['Id'])
        obj_dic = {
            'StandardID': obj.assignedSubjectID_id,
            'teacherID': obj.teacherID_id,
            'branch': obj.subjectBranch,
            'ID': obj.pk,
        }
        return _api_response({'status': 'success', 'data': obj_dic}, safe=False)
    except:
        return _api_response({'status': 'error'}, safe=False)


@transaction.atomic
@csrf_exempt
@login_required
def update_subject_to_teacher(request):
    if request.method == 'POST':
        try:
            editID = request.POST.get("editID")
            standard = request.POST.get("standard")
            teachers = request.POST.get("teachers")
            branch = request.POST.get("branch")

            instance = AssignSubjectsToTeacher.objects.get(pk=int(editID))

            try:
                AssignSubjectsToTeacher.objects.get(subjectBranch__iexact=branch,
                                                    assignedSubjectID_id=instance.assignedSubjectID_id,
                                                    isDeleted=False,
                                                    sessionID_id=request.session['current_session']['Id']).exclude(
                    pk=int(editID))
                return _api_response(
                    {'status': 'success', 'message': 'Detail already assigned.', 'color': 'info'},
                    safe=False)
            except:
                # instance = AssignSubjectsToClass()
                instance.standardID_id = int(standard)
                instance.teacherID_id = int(teachers)
                instance.subjectBranch = branch
                pre_save_with_user.send(sender=AssignSubjectsToTeacher, instance=instance, user=request.user.pk)
                instance.save()
                return _api_response(
                    {'status': 'success', 'message': 'Detail updated successfully.', 'color': 'success'},
                    safe=False)
        except:
            return _api_response({'status': 'error'}, safe=False)


# Teachers ---------------------------------------------------------------------------------------

@transaction.atomic
@csrf_exempt
@login_required
def add_teacher_api(request):
    if request.method == 'POST':
        name = request.POST.get("name")
        email = request.POST.get("email")
        bloodGroup = request.POST.get("bloodGroup")
        gender = request.POST.get("gender")
        phone = request.POST.get("phone")
        dob = request.POST.get("dob")
        aadhar = request.POST.get("aadhar")
        qualification = request.POST.get("qualification")
        imageUpload = request.FILES["imageUpload"]
        address = request.POST.get("address")
        city = request.POST.get("city")
        state = request.POST.get("state")
        country = request.POST.get("country")
        pincode = request.POST.get("pincode")
        addressP = request.POST.get("addressP")
        cityP = request.POST.get("cityP")
        stateP = request.POST.get("stateP")
        countryP = request.POST.get("countryP")
        pincodeP = request.POST.get("pincodeP")
        empCode = request.POST.get("empCode")
        staffType = request.POST.get("staffType")
        doj = request.POST.get("doj")

        try:
            TeacherDetail.objects.get(phoneNumber__iexact=phone, isDeleted=False,
                                      sessionID_id=request.session['current_session']['Id'])
            return _api_response(
                {'status': 'success', 'message': 'Teacher already exists. Please change the name.', 'color': 'info'},
                safe=False)
        except:
            instance = TeacherDetail()
            instance.name = name
            instance.email = email
            instance.bloodGroup = bloodGroup
            instance.gender = gender
            instance.dob = datetime.strptime(dob, '%d/%m/%Y')
            instance.dateOfJoining = datetime.strptime(doj, '%d/%m/%Y')
            instance.phoneNumber = phone
            instance.aadhar = aadhar
            instance.qualification = qualification
            instance.photo = optimize_uploaded_image(imageUpload)
            instance.presentAddress = address
            instance.presentCity = city
            instance.presentState = state
            instance.presentCountry = country
            instance.presentPinCode = pincode
            instance.permanentAddress = addressP
            instance.permanentCity = cityP
            instance.permanentState = stateP
            instance.permanentCountry = countryP
            instance.permanentPinCode = pincodeP
            instance.employeeCode = empCode
            instance.staffType = staffType

            username = 'T' + get_random_string(length=5, allowed_chars='1234567890')
            password = get_random_string(length=8, allowed_chars='1234567890')
            while User.objects.filter(username__exact=username).exists():
                username = 'T' + get_random_string(length=5, allowed_chars='1234567890')
            else:
                new_user = User()
                new_user.username = username
                new_user.set_password(password)

                new_user.save()
                instance.username = username
                instance.password = password
                instance.userID_id = new_user.pk

                instance.save()

                # Handle group assignment more efficiently
            try:
                group, created = Group.objects.get_or_create(name=staffType)
                if created:
                    logger.info(f"Created new group: {staffType}")
                
                # Only add user to group if not already a member
                if not group.user_set.filter(id=new_user.pk).exists():
                    group.user_set.add(new_user.pk)
                    logger.info(f"Added user {new_user.pk} to group {staffType}")
            except Exception as e:
                logger.error(f"Error handling group assignment: {e}")
                # Continue with teacher update even if group assignment fails
            pre_save_with_user.send(sender=TeacherDetail, instance=instance, user=request.user.pk)
            instance.save()
            return _api_response(
                {'status': 'success', 'message': 'New Teacher added successfully.', 'color': 'success'},
                safe=False)
    return _api_response({'status': 'error'}, safe=False)

@transaction.atomic
@csrf_exempt
@login_required
def update_teacher_api(request):
    if request.method == 'POST':
        name = request.POST.get("name")
        email = request.POST.get("email")
        bloodGroup = request.POST.get("bloodGroup")
        gender = request.POST.get("gender")
        phone = request.POST.get("phone")
        dob = request.POST.get("dob")
        aadhar = request.POST.get("aadhar")
        qualification = request.POST.get("qualification")
        imageUpload = request.FILES.get("imageUpload")
        address = request.POST.get("address")
        city = request.POST.get("city")
        state = request.POST.get("state")
        country = request.POST.get("country")
        pincode = request.POST.get("pincode")
        addressP = request.POST.get("addressP")
        cityP = request.POST.get("cityP")
        stateP = request.POST.get("stateP")
        countryP = request.POST.get("countryP")
        pincodeP = request.POST.get("pincodeP")
        empCode = request.POST.get("empCode")
        staffType = request.POST.get("staffType")
        doj = request.POST.get("doj")
        salary = request.POST.get("salary")
        additionalDetails = request.POST.get("additionalDetails")
        id = request.POST.get("id")

        try:
            instance = TeacherDetail.objects.get(id=id, isDeleted=False,
                                      sessionID_id=request.session['current_session']['Id'])
            instance.name = name
            instance.email = email
            instance.bloodGroup = bloodGroup
            instance.gender = gender
            instance.dob = datetime.strptime(dob, '%d/%m/%Y')
            instance.dateOfJoining = datetime.strptime(doj, '%d/%m/%Y')
            instance.phoneNumber = phone
            instance.aadhar = aadhar
            instance.qualification = qualification
            if imageUpload:
                instance.photo = optimize_uploaded_image(imageUpload)
            instance.presentAddress = address
            instance.presentCity = city
            instance.presentState = state
            instance.presentCountry = country
            instance.presentPinCode = pincode
            instance.permanentAddress = addressP
            instance.permanentCity = cityP
            instance.permanentState = stateP
            instance.permanentCountry = countryP
            instance.permanentPinCode = pincodeP
            instance.employeeCode = empCode
            instance.staffType = staffType
            instance.salary = salary
            instance.additionalDetails = additionalDetails

            user = User.objects.get(id=instance.userID_id)

            # Handle group assignment more efficiently
            try:
                group, created = Group.objects.get_or_create(name=staffType)
                if created:
                    logger.info(f"Created new group: {staffType}")
                
                # Only add user to group if not already a member
                if not group.user_set.filter(id=user.pk).exists():
                    group.user_set.add(user.pk)
                    logger.info(f"Added user {user.pk} to group {staffType}")
            except Exception as e:
                logger.error(f"Error handling group assignment: {e}")
                # Continue with teacher update even if group assignment fails
            pre_save_with_user.send(sender=TeacherDetail, instance=instance, user=request.user.pk)
            instance.save()
            logger.info("Teacher details updated successfully.")
            return SuccessResponse(
                    'Teacher details updated successfully.'
                    ).to_json_response()
        except TeacherDetail.DoesNotExist:
            logger.info("Teacher details not found.")
            return ErrorResponse(
                    'Teacher details not found.'
                    ).to_json_response()  
        except Exception as e:
            logger.error("Error updating teacher details: " + str(e))
            return ErrorResponse(
                    str(e)
                    ).to_json_response()          
    return ErrorResponse(
            'Invalid request.'
            ).to_json_response()



class TeacherListJson(BaseDatatableView):
    order_columns = ['photo', 'name', 'email', 'phoneNumber', 'employeeCode', 'gender', 'staffType', 'presentCity',
                     'isActive', 'lastEditedBy', 'lastUpdatedOn']

    def get_initial_queryset(self):
        return TeacherDetail.objects.only(
            'id', 'photo', 'name', 'email', 'phoneNumber', 'employeeCode', 'gender',
            'staffType', 'presentCity', 'isActive', 'lastEditedBy', 'datetime', 'lastUpdatedOn'
        ).filter(
            isDeleted__exact=False,
            sessionID_id=self.request.session["current_session"]["Id"]
        )

    def filter_queryset(self, qs):

        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(name__icontains=search)
                | Q(email__icontains=search)
                | Q(phoneNumber__icontains=search)
                | Q(employeeCode__icontains=search)
                | Q(gender__icontains=search)
                | Q(staffType__icontains=search) | Q(presentAddress__icontains=search)
                | Q(presentCity__icontains=search) | Q(isActive__icontains=search)
                | Q(lastEditedBy__icontains=search) | Q(lastUpdatedOn__icontains=search)
            )

        return qs

    def prepare_results(self, qs):
        json_data = []
        for item in qs:
            images = _avatar_image_html(item.photo)

            action = '''<a href="/management/teacher_detail/{}/" data-inverted="" data-tooltip="View Detail" data-position="left center" data-variation="mini" style="font-size:10px;" onclick = "GetDataDetails('{}')" class="ui circular facebook icon button purple">
                <i class="eye icon"></i>
              </a>
            <a href="/management/edit_teacher/{}/" data-inverted="" data-tooltip="Edit Detail" data-position="left center" data-variation="mini" style="font-size:10px;" onclick = "GetDataDetails('{}')" class="ui circular facebook icon button green">
                <i class="pen icon"></i>
              </a>
              <button data-inverted="" data-tooltip="Delete" data-position="left center" data-variation="mini" style="font-size:10px;" onclick ="delData('{}')" class="ui circular youtube icon button" style="margin-left: 3px">
                <i class="trash alternate icon"></i>
              </button></td>'''.format(item.pk,item.pk, item.pk, item.pk, item.pk),

            json_data.append([
                images,
                escape(item.name),
                escape(item.email),
                escape(item.phoneNumber),
                escape(item.employeeCode),
                escape(item.gender),
                escape(item.staffType),
                escape(item.presentCity),
                escape(item.isActive),
                escape(item.lastEditedBy or 'N/A'),
                escape(item.lastUpdatedOn.strftime('%d-%m-%Y %I:%M %p') if item.lastUpdatedOn else 'N/A'),
                action,

            ])

        return json_data


@transaction.atomic
@csrf_exempt
@login_required
def delete_teacher(request):
    if request.method == 'POST':
        try:
            id = request.POST.get("dataID")
            instance = TeacherDetail.objects.get(pk=int(id), isDeleted=False,
                                                 sessionID_id=request.session['current_session']['Id'])
            instance.isDeleted = True
            instance.isActive = 'No'
            user = User.objects.get(pk=instance.userID_id)
            user.is_active = False
            user.save()
            pre_save_with_user.send(sender=TeacherDetail, instance=instance, user=request.user.pk)
            instance.save()
            return _api_response(
                {'status': 'success', 'message': 'Teacher/Staff detail deleted successfully.',
                 'color': 'success'}, safe=False)
        except:
            return _api_response({'status': 'error'}, safe=False)
    return _api_response({'status': 'error'}, safe=False)


@login_required
def get_teacher_list_api(request):
    rows = TeacherDetail.objects.filter(
        isDeleted=False,
        sessionID_id=_current_session_id(request),
        isActive='Yes'
    ).values('id', 'name', 'employeeCode').order_by('name')
    data = [
        {'ID': row['id'], 'Name': f"{row.get('name') or 'N/A'} - {row.get('employeeCode') or 'N/A'}"}
        for row in rows
    ]
    return _api_response(
        {'status': 'success', 'data': data,
         'color': 'success'}, safe=False)


# student api --------------------------------------------------------------------------
@transaction.atomic
@csrf_exempt
@login_required
def add_student_api(request):

    if request.method != 'POST':
        return ErrorResponse("Method not allowed").to_json_response()

    post_data = request.POST.dict()
    files_data = request.FILES


    # ---------- PARENT ----------
    parent_obj, _ = Parent.objects.get_or_create(
        fatherName = post_data.get("fname"),
        motherName = post_data.get("mname"),
        fatherOccupation = post_data.get("FatherOccupation"),
        motherOccupation = post_data.get("MotherOccupation"),
        fatherPhone = post_data.get("fatherContactNumber"),
        motherPhone = post_data.get("MotherContactNumber"),
        fatherAddress = post_data.get("FatherAddress"),
        motherAddress = post_data.get("MotherAddress"),
        guardianName = post_data.get("guardianName"),
        guardianOccupation = post_data.get("guardianOccupation"),
        guardianPhone = post_data.get("guardianPhoneNumber"),
        familyType = post_data.get("familyType"),
        totalFamilyMembers = float(post_data.get("numberOfMembers")) if post_data.get("numberOfMembers") else 0,
        annualIncome = float(post_data.get("familyAnnualIncome")) if post_data.get("familyAnnualIncome") else 0,
        phoneNumber = post_data.get("parentsPhoneNumber"),
        fatherEmail = post_data.get("fatherEmail"),
        motherEmail = post_data.get("motherEmail"),
        isDeleted = False,
        defaults={}
    )

    pre_save_with_user.send(sender=Parent, instance=parent_obj, user=request.user.pk)
    parent_obj.save()


    # ---------- STUDENT EXIST CHECK ----------
    if Student.objects.filter(
        registrationCode__iexact = post_data.get("registrationCode"),
        sessionID_id = request.session['current_session']['Id'],
        isDeleted = False
    ).exists():
        return _api_response(
            {'status': 'success', 'message': 'Student already exists.', 'color': 'info'},
            safe=False
        )



    # ---------- STUDENT CREATION ----------
    student_obj = Student.objects.create(
        registrationCode = post_data.get("registrationCode"),
        name = post_data.get("name"),
        email = post_data.get("email"),
        phoneNumber = post_data.get("phone"),
        bloodGroup = post_data.get("bloodGroup"),
        gender = post_data.get("gender"),
        aadhar = post_data.get("aadhar"),
        idMark = post_data.get("idMark"),
        penNumber = post_data.get("penNumber"),
        caste = post_data.get("caste"),
        tribe = post_data.get("tribe"),
        religion = post_data.get("religion"),
        motherTongue = post_data.get("motherTongue"),
        otherLanguages = post_data.get("otherLanguages"),
        hobbies = post_data.get("hobbies"),
        aimInLife = post_data.get("aimInLife"),
        milOption = post_data.get("milOptions"),

        familyCode = post_data.get("familyCode"),
        siblingsCount = int(post_data.get("siblings")) if post_data.get("siblings") else 0,
        roll = post_data.get("roll"),

        # Previous School
        lastSchoolName = post_data.get("previousSchoolName"),
        lastSchoolAddress = post_data.get("previousSchoolAddress"),
        lastClass = post_data.get("previousSchoolClass"),
        lastResult = post_data.get("previousSchoolResult"),
        lastDivision = post_data.get("previousSchoolDivision"),
        lastRollNo = post_data.get("previousSchoolRollNumber"),

        # Fees
        admissionFee = float(post_data.get("admissionFee")) if post_data.get("admissionFee") else 0,
        tuitionFee = float(post_data.get("tuitionFee")) if post_data.get("tuitionFee") else 0,
        miscFee = float(post_data.get("miscFee")) if post_data.get("miscFee") else 0,
        totalFee = float(post_data.get("totalFee")) if post_data.get("totalFee") else 0,

        # Foreign keys
        standardID_id = post_data.get("standard"),
        parentID = parent_obj,
        
        # schoolID_id = request.session['current_session']['SchoolID'],
        sessionID_id = request.session['current_session']['Id'],

        isDeleted = False,
    )

    # ---------- DATE HANDLING ----------
    if post_data.get("dob"):
        student_obj.dob = datetime.strptime(post_data["dob"], "%d/%m/%Y")

    if post_data.get("doj"):
        student_obj.dateOfJoining = datetime.strptime(post_data["doj"], "%d/%m/%Y")

    # ---------- IMAGE ----------
    if "imageUpload" in files_data:
        student_obj.photo = optimize_uploaded_image(files_data["imageUpload"])

    # ---------- USER ----------
    username = 'STU' + get_random_string(5, '1234567890')
    password = get_random_string(8, '1234567890')

    new_user = User.objects.create_user(username=username, password=password)
    student_obj.username = username
    student_obj.password = password
    student_obj.userID = new_user

    # ---------- FINAL SAVE ----------
    pre_save_with_user.send(sender=Student, instance=student_obj, user=request.user.pk)
    student_obj.save()

    Group.objects.get_or_create(name="Student")[0].user_set.add(new_user)

    return SuccessResponse(
        "New Student added successfully.",
        data={'status': 'success', 'message': 'New Student added successfully.', 'color': 'success'},
    ).to_json_response()

@login_required
def get_student_list_by_class_api(request):
    standard = request.GET.get('standard')
    try:
        standard_id = int(standard)
    except (TypeError, ValueError):
        return _api_response({'status': 'success', 'data': [], 'color': 'success'}, safe=False)
    rows = Student.objects.filter(
        isDeleted=False,
        sessionID_id=_current_session_id(request),
        standardID_id=standard_id
    ).values('id', 'name', 'roll').order_by('roll')
    data = []
    for row in rows:
        roll = row.get('roll')
        try:
            roll_label = str(int(float(roll)))
        except Exception:
            roll_label = str(roll or 'N/A')
        data.append({'ID': row['id'], 'Name': f"{row.get('name') or 'N/A'} - {roll_label}"})
    return _api_response(
        {'status': 'success', 'data': data,
         'color': 'success'}, safe=False)


class StudentListJson(BaseDatatableView):
    order_columns = ['photo', 'name', 'standardID.name', 'gender', 'parentID.fatherName',
                     'parentID.phoneNumber', 'presentCity',
                     'isActive', 'lastEditedBy', 'lastUpdatedOn']

    def get_initial_queryset(self):
        return Student.objects.select_related('standardID', 'parentID').only(
            'id', 'photo', 'name', 'gender', 'presentCity', 'isActive', 'lastEditedBy', 'datetime', 'lastUpdatedOn',
            'standardID__name', 'standardID__section',
            'parentID__fatherName', 'parentID__phoneNumber'
        ).filter(
            isDeleted__exact=False,
            sessionID_id=self.request.session["current_session"]["Id"]
        )

    def filter_queryset(self, qs):
        standard_filter = (self.request.GET.get('standardFilter') or '').strip()
        if standard_filter.isdigit():
            qs = qs.filter(standardID_id=int(standard_filter))

        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(name__icontains=search)
                | Q(email__icontains=search)
                | Q(phoneNumber__icontains=search)
                | Q(standardID__name__icontains=search) | Q(standardID__section__icontains=search)
                | Q(gender__icontains=search) | Q(parentID__phoneNumber__icontains=search) | Q(
                    parentID__motherName__icontains=search) | Q(parentID__profession__icontains=search)
                | Q(parentID__fatherName__icontains=search) | Q(presentAddress__icontains=search)
                | Q(presentCity__icontains=search) | Q(isActive__icontains=search)
                | Q(lastEditedBy__icontains=search) | Q(lastUpdatedOn__icontains=search)
            )

        return qs

    def prepare_results(self, qs):
        json_data = []
        for item in qs:
            images = _avatar_image_html(item.photo)

            action = '''<a href="/management/student_detail/{}/" data-inverted="" data-tooltip="View Detail" data-position="left center" data-variation="mini" style="font-size:10px;" onclick = "GetDataDetails('{}')" class="ui circular facebook icon button purple">
                <i class="eye icon"></i>
              </a>
            <button type="button" onclick="openStudentIdCardModal('{}')" data-inverted="" data-tooltip="ID Card" data-position="left center" data-variation="mini" style="font-size:10px;" class="ui circular blue icon button">
                <i class="id card outline icon"></i>
              </button>
            <a href="/management/edit_student/{}/" data-inverted="" data-tooltip="Edit Detail" data-position="left center" data-variation="mini" style="font-size:10px;" onclick = "GetDataDetails('{}')" class="ui circular facebook icon button green">
                <i class="pen icon"></i>
              </a>
              <button data-inverted="" data-tooltip="Delete" data-position="left center" data-variation="mini" style="font-size:10px;" onclick ="delData('{}')" class="ui circular youtube icon button" style="margin-left: 3px">
                <i class="trash alternate icon"></i>
              </button></td>'''.format(item.pk, item.pk, item.pk, item.pk, item.pk, item.pk)
            if item.standardID.section:
                standard = item.standardID.name + ' - ' + item.standardID.section
            else:
                standard = item.standardID.name
            json_data.append([
                images,
                escape(item.name),
                escape(standard),
                escape(item.gender),
                escape(item.parentID.fatherName),
                escape(item.parentID.phoneNumber),
                escape(item.presentCity),
                escape(item.isActive),
                escape(item.lastEditedBy or 'N/A'),
                escape(item.lastUpdatedOn.strftime('%d-%m-%Y %I:%M %p') if item.lastUpdatedOn else 'N/A'),
                action,

            ])

        return json_data


class StudentIdCardRecordListJson(BaseDatatableView):
    order_columns = [
        'studentID__name',
        'studentID__registrationCode',
        'studentID__standardID__name',
        'studentID__roll',
        'actionType',
        'validTill',
        'remark',
        'lastEditedBy',
        'lastUpdatedOn',
    ]

    def get_initial_queryset(self):
        queryset = StudentIdCardRecord.objects.select_related(
            'studentID', 'studentID__standardID'
        ).filter(
            isDeleted=False,
            sessionID_id=self.request.session['current_session']['Id'],
            studentID__isDeleted=False,
        )

        standard = self.request.GET.get('standard')
        if standard and standard.isdigit():
            queryset = queryset.filter(studentID__standardID_id=int(standard))
        student = self.request.GET.get('student')
        if student and student.isdigit():
            queryset = queryset.filter(studentID_id=int(student))
        action_filter = (self.request.GET.get('action_filter') or '').strip().lower()
        if action_filter:
            queryset = queryset.filter(actionType=action_filter)
        edited_by_filter = (self.request.GET.get('edited_by_filter') or '').strip()
        if edited_by_filter:
            queryset = queryset.filter(lastEditedBy__icontains=edited_by_filter)
        return queryset

    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(studentID__name__icontains=search)
                | Q(studentID__registrationCode__icontains=search)
                | Q(studentID__roll__icontains=search)
                | Q(studentID__standardID__name__icontains=search)
                | Q(studentID__standardID__section__icontains=search)
                | Q(actionType__icontains=search)
                | Q(validTill__icontains=search)
                | Q(remark__icontains=search)
                | Q(lastEditedBy__icontains=search)
                | Q(lastUpdatedOn__icontains=search)
            )
        return qs

    def prepare_results(self, qs):
        json_data = []
        for item in qs:
            standard = 'N/A'
            if item.studentID and item.studentID.standardID:
                standard = item.studentID.standardID.name or 'N/A'
                if item.studentID.standardID.section:
                    standard = f'{standard} - {item.studentID.standardID.section}'

            action_label = dict(StudentIdCardRecord.ACTION_CHOICES).get(item.actionType, item.actionType)
            preview_action = (
                f'<button type="button" onclick="openCardModal({item.studentID_id})" '
                f'data-inverted="" data-tooltip="View ID Card" data-position="left center" '
                f'data-variation="mini" style="font-size:10px;" class="ui circular facebook icon button purple">'
                f'<i class="id card icon"></i></button>'
            )

            json_data.append([
                escape(item.studentID.name if item.studentID and item.studentID.name else 'N/A'),
                escape(item.studentID.registrationCode if item.studentID and item.studentID.registrationCode else 'N/A'),
                escape(standard),
                escape(item.studentID.roll if item.studentID and item.studentID.roll else 'N/A'),
                escape(action_label),
                escape(item.validTill.strftime('%d-%m-%Y') if item.validTill else 'Upto 2026'),
                escape(item.remark or 'N/A'),
                escape(item.lastEditedBy or 'N/A'),
                escape(item.lastUpdatedOn.strftime('%d-%m-%Y %I:%M %p') if item.lastUpdatedOn else 'N/A'),
                preview_action,
            ])
        return json_data


@transaction.atomic
@csrf_exempt
@login_required
def add_student_id_card_record_api(request):
    if request.method != 'POST':
        return ErrorResponse('Invalid request method.', extra={'color': 'red'}).to_json_response()

    try:
        student_id = request.POST.get('student_id')
        action_type = (request.POST.get('action_type') or 'print').strip().lower()
        valid_till = (request.POST.get('valid_till') or '').strip()
        remark = (request.POST.get('remark') or '').strip()
        current_session_id = request.session['current_session']['Id']

        if not student_id:
            return ErrorResponse('Student is required.', extra={'color': 'red'}).to_json_response()

        student = Student.objects.filter(
            pk=int(student_id),
            isDeleted=False,
            sessionID_id=current_session_id,
        ).first()
        if not student:
            return ErrorResponse('Student not found in current session.', extra={'color': 'red'}).to_json_response()

        valid_actions = {choice[0] for choice in StudentIdCardRecord.ACTION_CHOICES}
        if action_type not in valid_actions:
            return ErrorResponse('Invalid tracker action.', extra={'color': 'red'}).to_json_response()

        parsed_valid_till = None
        if valid_till:
            try:
                parsed_valid_till = datetime.strptime(valid_till, '%Y-%m-%d').date()
            except ValueError:
                try:
                    parsed_valid_till = datetime.strptime(valid_till, '%d/%m/%Y').date()
                except ValueError:
                    return ErrorResponse('Invalid valid till date format.', extra={'color': 'red'}).to_json_response()

        instance = StudentIdCardRecord(
            studentID=student,
            actionType=action_type,
            validTill=parsed_valid_till,
            remark=remark,
        )
        pre_save_with_user.send(sender=StudentIdCardRecord, instance=instance, user=request.user.pk)

        return SuccessResponse(
            'ID card tracker updated successfully.',
            data={'preview_url': f'/management/student_id_card/{student.pk}/'},
            extra={'color': 'success'}
        ).to_json_response()
    except Exception as e:
        logger.error(f'Error in add_student_id_card_record_api: {e}')
        return ErrorResponse('Failed to update ID card tracker.', extra={'color': 'red'}).to_json_response()


@transaction.atomic
@csrf_exempt
@login_required
def delete_student(request):
    if request.method == 'POST':
        try:
            id = request.POST.get("dataID")
            instance = Student.objects.get(pk=int(id), isDeleted=False,
                                           sessionID_id=request.session['current_session']['Id'])
            instance.isDeleted = True
            instance.isActive = 'No'
            user = User.objects.get(pk=instance.userID_id)
            user.is_active = False
            user.save()
            pre_save_with_user.send(sender=Student, instance=instance, user=request.user.pk)
            instance.save()
            return _api_response(
                {'status': 'success', 'message': 'Student detail deleted successfully.',
                 'color': 'success'}, safe=False)
        except:
            return _api_response({'status': 'error'}, safe=False)
    return _api_response({'status': 'error'}, safe=False)


@transaction.atomic
@csrf_exempt
@login_required
def edit_student_api(request):
    try:

        if request.method != 'POST':
            return ErrorResponse("Method not allowed").to_json_response()

        post_data = request.POST.dict()
        files_data = request.FILES


        # ---------- PARENT ----------
        parent_obj, _ = Parent.objects.get_or_create(
            fatherName = post_data.get("fname"),
            motherName = post_data.get("mname"),
            fatherOccupation = post_data.get("FatherOccupation"),
            motherOccupation = post_data.get("MotherOccupation"),
            fatherPhone = post_data.get("fatherContactNumber"),
            motherPhone = post_data.get("MotherContactNumber"),
            fatherAddress = post_data.get("FatherAddress"),
            motherAddress = post_data.get("MotherAddress"),
            guardianName = post_data.get("guardianName"),
            guardianOccupation = post_data.get("guardianOccupation"),
            guardianPhone = post_data.get("guardianPhoneNumber"),
            familyType = post_data.get("familyType"),
            totalFamilyMembers = float(post_data.get("numberOfMembers")) if post_data.get("numberOfMembers") else 0,
            annualIncome = float(post_data.get("familyAnnualIncome")) if post_data.get("familyAnnualIncome") else 0,
            phoneNumber = post_data.get("parentsPhoneNumber"),
            isDeleted = False,
            fatherEmail = post_data.get("fatherEmail"),
            motherEmail = post_data.get("motherEmail"),
            defaults={}
        )

        pre_save_with_user.send(sender=Parent, instance=parent_obj, user=request.user.pk)
        parent_obj.save()


        # ---------- STUDENT EXIST CHECK ----------
        if Student.objects.filter(
            registrationCode__iexact = post_data.get("registrationCode"),
            sessionID_id = request.session['current_session']['Id'],
            isDeleted = False
        ).exclude(pk = post_data.get("editID")).exists():
            return _api_response(
                {'status': 'success', 'message': 'Student already exists.', 'color': 'info'},
                safe=False
            )



        # ---------- STUDENT UPDATE ----------
        student_obj = Student.objects.get(pk=post_data.get("editID"))
        student_obj.registrationCode = post_data.get("registrationCode")
        student_obj.name = post_data.get("name")
        student_obj.email = post_data.get("email")
        student_obj.phoneNumber = post_data.get("phone")
        student_obj.bloodGroup = post_data.get("bloodGroup")
        student_obj.gender = post_data.get("gender")
        student_obj.aadhar = post_data.get("aadhar")
        student_obj.idMark = post_data.get("idMark")
        student_obj.penNumber = post_data.get("penNumber")
        student_obj.caste = post_data.get("caste")
        student_obj.tribe = post_data.get("tribe")
        student_obj.religion = post_data.get("religion")
        student_obj.motherTongue = post_data.get("motherTongue")
        student_obj.otherLanguages = post_data.get("otherLanguages")
        student_obj.hobbies = post_data.get("hobbies")
        student_obj.aimInLife = post_data.get("aimInLife")
        student_obj.milOption = post_data.get("milOptions")

        student_obj.familyCode = post_data.get("familyCode")
        student_obj.siblingsCount = int(post_data.get("siblings")) if post_data.get("siblings") else 0
        student_obj.roll = post_data.get("roll")

        # Previous School
        student_obj.lastSchoolName = post_data.get("previousSchoolName")
        student_obj.lastSchoolAddress = post_data.get("previousSchoolAddress")
        student_obj.lastClass = post_data.get("previousSchoolClass")
        student_obj.lastResult = post_data.get("previousSchoolResult")
        student_obj.lastDivision = post_data.get("previousSchoolDivision")
        student_obj.lastRollNo = post_data.get("previousSchoolRollNumber")

        # Fees
        student_obj.admissionFee = float(post_data.get("admissionFee")) if post_data.get("admissionFee") else 0
        student_obj.tuitionFee = float(post_data.get("tuitionFee")) if post_data.get("tuitionFee") else 0
        student_obj.miscFee = float(post_data.get("miscFee")) if post_data.get("miscFee") else 0
        student_obj.totalFee = float(post_data.get("totalFee")) if post_data.get("totalFee") else 0

        # Foreign keys
        student_obj.standardID_id = post_data.get("standard")
        student_obj.parentID = parent_obj
        # student_obj.schoolID_id = request.session['current_session']['SchoolID']
        student_obj.sessionID_id = request.session['current_session']['Id']

        student_obj.isDeleted = False

        # ---------- DATE HANDLING ----------
        if post_data.get("dob"):
            student_obj.dob = datetime.strptime(post_data["dob"], "%d/%m/%Y")

        if post_data.get("doj"):
            student_obj.dateOfJoining = datetime.strptime(post_data["doj"], "%d/%m/%Y")

        # ---------- IMAGE ----------
        if "imageUpload" in files_data:
            student_obj.photo = optimize_uploaded_image(files_data["imageUpload"])

        # ---------- FINAL SAVE ----------
        pre_save_with_user.send(sender=Student, instance=student_obj, user=request.user.pk)
        student_obj.save()

        logger.info("Student details updated successfully.")
        return SuccessResponse(
            "Student details updated successfully.",
            data={'status': 'success', 'message': 'Student details updated successfully.', 'color': 'success'},
        ).to_json_response()
    except Exception as e:
        logger.error(f"Error in updating student details: {str(e)}")
        return ErrorResponse(str(e)).to_json_response()

# ---------------------Exam -------------------------------------------------
@transaction.atomic
@csrf_exempt
@login_required
def add_exam(request):
    if request.method == 'POST':
        exam = request.POST.get("exam")
        try:
            Exam.objects.get(name__iexact=exam, isDeleted=False,
                             sessionID_id=request.session['current_session']['Id'])
            return _api_response(
                {'status': 'success', 'message': 'Exam already exists. Please change the name.', 'color': 'info'},
                safe=False)
        except:
            instance = Exam()
            instance.name = exam
            pre_save_with_user.send(sender=Exam, instance=instance, user=request.user.pk)
            instance.save()
            return _api_response(
                {'status': 'success', 'message': 'New Exam created successfully.', 'color': 'success'},
                safe=False)
    return _api_response({'status': 'error'}, safe=False)


class ExamListJson(BaseDatatableView):
    order_columns = ['name', 'lastEditedBy', 'lastUpdatedOn']

    def get_initial_queryset(self):
        return Exam.objects.only(
            'id', 'name', 'lastEditedBy', 'datetime', 'lastUpdatedOn'
        ).filter(
            isDeleted__exact=False,
            sessionID_id=self.request.session["current_session"]["Id"]
        )

    def filter_queryset(self, qs):

        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(name__icontains=search)
                | Q(lastEditedBy__icontains=search) | Q(lastUpdatedOn__icontains=search)
            )

        return qs

    def prepare_results(self, qs):
        json_data = []
        for item in qs:
            action = '''<button data-inverted="" data-tooltip="Edit Detail" data-position="left center" data-variation="mini" style="font-size:10px;" onclick = "GetDataDetails('{}')" class="ui circular facebook icon button green">
                <i class="pen icon"></i>
              </button>
              <button data-inverted="" data-tooltip="Delete" data-position="left center" data-variation="mini" style="font-size:10px;" onclick ="delData('{}')" class="ui circular youtube icon button" style="margin-left: 3px">
                <i class="trash alternate icon"></i>
              </button></td>'''.format(item.pk, item.pk),

            json_data.append([
                escape(item.name),
                escape(item.lastEditedBy or 'N/A'),
                escape(item.lastUpdatedOn.strftime('%d-%m-%Y %I:%M %p') if item.lastUpdatedOn else 'N/A'),
                action,

            ])

        return json_data


@transaction.atomic
@csrf_exempt
@login_required
def delete_exam(request):
    if request.method == 'POST':
        try:
            id = request.POST.get("dataID")
            instance = Exam.objects.get(pk=int(id), isDeleted=False,
                                        sessionID_id=request.session['current_session']['Id'])
            instance.isDeleted = True
            pre_save_with_user.send(sender=Exam, instance=instance, user=request.user.pk)
            instance.save()
            return _api_response(
                {'status': 'success', 'message': 'Exam detail deleted successfully.',
                 'color': 'success'}, safe=False)
        except:
            return _api_response({'status': 'error'}, safe=False)
    return _api_response({'status': 'error'}, safe=False)


@login_required
def get_exam_detail(request, **kwargs):
    try:
        id = request.GET.get('id')
        obj = Exam.objects.get(pk=id, isDeleted=False, sessionID_id=request.session['current_session']['Id'])
        obj_dic = {
            'ID': obj.pk,
            'ExamName': obj.name,
        }
        return _api_response({'status': 'success', 'data': obj_dic}, safe=False)
    except:
        return _api_response({'status': 'error'}, safe=False)


@transaction.atomic
@csrf_exempt
@login_required
def edit_exam(request):
    if request.method == 'POST':
        exam = request.POST.get("exam")
        editID = request.POST.get("editID")
        try:
            instance = Exam.objects.get(pk=int(editID))
            data = Exam.objects.filter(name__iexact=exam, isDeleted=False,
                                       sessionID_id=request.session['current_session']['Id']).exclude(
                pk=int(editID))
            if data.count() > 0:
                return _api_response(
                    {'status': 'success', 'message': 'Exam already exists. Please change the name.',
                     'color': 'info'},
                    safe=False)
            else:
                # instance = Subjects.objects.get(pk=int(editID))
                instance.name = exam
                pre_save_with_user.send(sender=Exam, instance=instance, user=request.user.pk)
                instance.save()
                return _api_response(
                    {'status': 'success', 'message': 'Exam name updated successfully.', 'color': 'success'},
                    safe=False)
        except:

            return _api_response({'status': 'error'}, safe=False)


@login_required
def get_exams_list_api(request):
    rows = Exam.objects.filter(
        isDeleted=False,
        sessionID_id=_current_session_id(request)
    ).values('id', 'name').order_by('name')
    data = [{'ID': row['id'], 'Name': row.get('name') or 'N/A'} for row in rows]
    return _api_response(
        {'status': 'success', 'data': data, 'color': 'success'}, safe=False)


def _parse_exam_time(value):
    if not value:
        return None
    value = value.strip()
    for candidate in (value, value.upper()):
        for fmt in ('%I:%M %p', '%H:%M', '%H:%M:%S'):
            try:
                return datetime.strptime(candidate, fmt).time()
            except ValueError:
                pass
    raise ValueError("Invalid time format")


def _validate_exam_timetable_business_rules(
        current_session_id,
        standard_id,
        exam_id,
        subject_id,
        parsed_exam_date,
        parsed_start_time,
        parsed_end_time,
        room_no,
        exclude_id=None,
):
    assign_subject_exists = AssignSubjectsToClass.objects.filter(
        isDeleted=False,
        sessionID_id=current_session_id,
        standardID_id=standard_id,
        subjectID_id=subject_id,
    ).exists()
    if not assign_subject_exists:
        return False, "Selected subject is not assigned to the selected class.", 'red'

    assigned_exam = AssignExamToClass.objects.filter(
        isDeleted=False,
        sessionID_id=current_session_id,
        standardID_id=standard_id,
        examID_id=exam_id,
    ).order_by('-datetime').first()
    if not assigned_exam:
        return False, "Selected exam is not assigned to the selected class.", 'red'

    if assigned_exam.startDate and parsed_exam_date < assigned_exam.startDate:
        return False, "Exam date cannot be before assigned exam start date.", 'red'
    if assigned_exam.endDate and parsed_exam_date > assigned_exam.endDate:
        return False, "Exam date cannot be after assigned exam end date.", 'red'

    base_queryset = ExamTimeTable.objects.filter(
        isDeleted=False,
        sessionID_id=current_session_id,
        examDate=parsed_exam_date,
    )
    if exclude_id:
        base_queryset = base_queryset.exclude(pk=exclude_id)

    duplicate_exists = base_queryset.filter(
        standardID_id=standard_id,
        examID_id=exam_id,
        subjectID_id=subject_id,
    ).exists()
    if duplicate_exists:
        return False, "Exam timetable already exists for this class, exam, subject and date.", 'info'

    class_overlap_exists = base_queryset.filter(
        standardID_id=standard_id,
        startTime__lt=parsed_end_time,
        endTime__gt=parsed_start_time,
    ).exists()
    if class_overlap_exists:
        return False, "Time conflict: this class already has another paper in the selected time slot.", 'red'

    if room_no:
        room_overlap_exists = base_queryset.filter(
            roomNo__iexact=room_no,
            startTime__lt=parsed_end_time,
            endTime__gt=parsed_start_time,
        ).exists()
        if room_overlap_exists:
            return False, "Time conflict: selected room is already occupied in this time slot.", 'red'

    return True, "", 'success'


@transaction.atomic
@csrf_exempt
@login_required
def add_exam_timetable(request):
    if request.method != 'POST':
        return ErrorResponse("Method not allowed", extra={'color': 'red'}).to_json_response()

    try:
        standard_id = request.POST.get("standard")
        exam_id = request.POST.get("exam")
        subject_id = request.POST.get("subject")
        exam_date = request.POST.get("examDate")
        start_time = request.POST.get("startTime")
        end_time = request.POST.get("endTime")
        room_no = request.POST.get("roomNo", "").strip()
        note = request.POST.get("note", "").strip()
        current_session_id = request.session['current_session']['Id']

        if not (standard_id and exam_id and subject_id and exam_date and start_time and end_time):
            return ErrorResponse(
                "Class, exam, subject, date, start time and end time are required.",
                extra={'color': 'red'}
            ).to_json_response()

        standard_id = int(standard_id)
        exam_id = int(exam_id)
        subject_id = int(subject_id)

        standard_exists = Standard.objects.filter(
            pk=standard_id, isDeleted=False, sessionID_id=current_session_id
        ).exists()
        exam_exists = Exam.objects.filter(
            pk=exam_id, isDeleted=False, sessionID_id=current_session_id
        ).exists()
        subject_exists = Subjects.objects.filter(
            pk=subject_id, isDeleted=False, sessionID_id=current_session_id
        ).exists()
        if not (standard_exists and exam_exists and subject_exists):
            return ErrorResponse(
                "Invalid class, exam, or subject for current session.",
                extra={'color': 'red'}
            ).to_json_response()

        parsed_exam_date = datetime.strptime(exam_date, '%d/%m/%Y').date()
        parsed_start_time = _parse_exam_time(start_time)
        parsed_end_time = _parse_exam_time(end_time)
        if parsed_start_time >= parsed_end_time:
            return ErrorResponse("Start time must be before end time.", extra={'color': 'red'}).to_json_response()

        is_valid, validation_message, validation_color = _validate_exam_timetable_business_rules(
            current_session_id=current_session_id,
            standard_id=standard_id,
            exam_id=exam_id,
            subject_id=subject_id,
            parsed_exam_date=parsed_exam_date,
            parsed_start_time=parsed_start_time,
            parsed_end_time=parsed_end_time,
            room_no=room_no,
        )
        if not is_valid:
            return ErrorResponse(validation_message, extra={'color': validation_color}).to_json_response()

        instance = ExamTimeTable()
        instance.standardID_id = standard_id
        instance.examID_id = exam_id
        instance.subjectID_id = subject_id
        instance.examDate = parsed_exam_date
        instance.startTime = parsed_start_time
        instance.endTime = parsed_end_time
        instance.roomNo = room_no
        instance.note = note
        pre_save_with_user.send(sender=ExamTimeTable, instance=instance, user=request.user.pk)
        instance.save()
        return SuccessResponse("Exam timetable added successfully.", extra={'color': 'success'}).to_json_response()
    except ValueError:
        return ErrorResponse("Invalid date/time format.", extra={'color': 'red'}).to_json_response()
    except IntegrityError:
        return ErrorResponse(
            "Unable to save timetable due to conflict. Please refresh and try again.",
            extra={'color': 'red'}
        ).to_json_response()
    except Exception as e:
        logger.error(f"Error in add_exam_timetable: {e}")
        return ErrorResponse("Error in adding exam timetable.", extra={'color': 'red'}).to_json_response()


class ExamTimeTableListJson(BaseDatatableView):
    order_columns = [
        'standardID.name',
        'standardID.section',
        'examID.name',
        'subjectID.name',
        'examDate',
        'startTime',
        'endTime',
        'roomNo',
        'lastEditedBy',
        'lastUpdatedOn'
    ]

    def get_initial_queryset(self):
        return ExamTimeTable.objects.select_related(
            'standardID', 'examID', 'subjectID'
        ).filter(
            isDeleted=False,
            sessionID_id=self.request.session["current_session"]["Id"]
        ).order_by('-examDate', 'startTime')

    def filter_queryset(self, qs):
        standard_filter = (self.request.GET.get('standardFilter') or '').strip()
        exam_filter = (self.request.GET.get('examFilter') or '').strip()

        if standard_filter.isdigit():
            qs = qs.filter(standardID_id=int(standard_filter))
        if exam_filter.isdigit():
            qs = qs.filter(examID_id=int(exam_filter))

        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(standardID__name__icontains=search)
                | Q(standardID__section__icontains=search)
                | Q(examID__name__icontains=search)
                | Q(subjectID__name__icontains=search)
                | Q(roomNo__icontains=search)
                | Q(lastEditedBy__icontains=search)
                | Q(lastUpdatedOn__icontains=search)
            )
        return qs

    def prepare_results(self, qs):
        json_data = []
        for item in qs:
            action = '''<button data-inverted="" data-tooltip="Edit Detail" data-position="left center" data-variation="mini" style="font-size:10px;" onclick = "GetDataDetails('{}')" class="ui circular facebook icon button green">
                <i class="pen icon"></i>
              </button>
              <button data-inverted="" data-tooltip="Delete" data-position="left center" data-variation="mini" style="font-size:10px;" onclick ="delData('{}')" class="ui circular youtube icon button" style="margin-left: 3px">
                <i class="trash alternate icon"></i>
              </button></td>'''.format(item.pk, item.pk)
            section = item.standardID.section if item.standardID and item.standardID.section else 'N/A'
            json_data.append([
                escape(item.standardID.name if item.standardID else 'N/A'),
                escape(section),
                escape(item.examID.name if item.examID else 'N/A'),
                escape(item.subjectID.name if item.subjectID else 'N/A'),
                escape(item.examDate.strftime('%d-%m-%Y') if item.examDate else 'N/A'),
                escape(item.startTime.strftime('%I:%M %p') if item.startTime else 'N/A'),
                escape(item.endTime.strftime('%I:%M %p') if item.endTime else 'N/A'),
                escape(item.roomNo if item.roomNo else 'N/A'),
                escape(item.lastEditedBy if item.lastEditedBy else 'N/A'),
                escape(item.lastUpdatedOn.strftime('%d-%m-%Y %I:%M %p') if item.lastUpdatedOn else 'N/A'),
                action,
            ])
        return json_data


@login_required
def get_exam_timetable_detail(request, **kwargs):
    try:
        row_id = request.GET.get('id')
        obj = ExamTimeTable.objects.get(
            pk=row_id,
            isDeleted=False,
            sessionID_id=request.session['current_session']['Id']
        )
        obj_dic = {
            'ID': obj.pk,
            'StandardID': obj.standardID_id,
            'ExamID': obj.examID_id,
            'SubjectID': obj.subjectID_id,
            'ExamDate': obj.examDate.strftime('%d/%m/%Y') if obj.examDate else '',
            'StartTime': obj.startTime.strftime('%I:%M %p') if obj.startTime else '',
            'EndTime': obj.endTime.strftime('%I:%M %p') if obj.endTime else '',
            'RoomNo': obj.roomNo if obj.roomNo else '',
            'Note': obj.note if obj.note else '',
        }
        return SuccessResponse(
            "Exam timetable detail fetched successfully.",
            data=obj_dic,
            extra={'color': 'success'}
        ).to_json_response()
    except ExamTimeTable.DoesNotExist:
        return ErrorResponse("Exam timetable detail not found.", extra={'color': 'red'}).to_json_response()
    except Exception as e:
        logger.error(f"Error in get_exam_timetable_detail: {e}")
        return ErrorResponse("Error in fetching exam timetable detail.", extra={'color': 'red'}).to_json_response()


@transaction.atomic
@csrf_exempt
@login_required
def update_exam_timetable(request):
    if request.method != 'POST':
        return ErrorResponse("Method not allowed", extra={'color': 'red'}).to_json_response()

    try:
        edit_id = request.POST.get("editID")
        standard_id = request.POST.get("standard")
        exam_id = request.POST.get("exam")
        subject_id = request.POST.get("subject")
        exam_date = request.POST.get("examDate")
        start_time = request.POST.get("startTime")
        end_time = request.POST.get("endTime")
        room_no = request.POST.get("roomNo", "").strip()
        note = request.POST.get("note", "").strip()
        current_session_id = request.session['current_session']['Id']

        if not (edit_id and standard_id and exam_id and subject_id and exam_date and start_time and end_time):
            return ErrorResponse(
                "Class, exam, subject, date, start time and end time are required.",
                extra={'color': 'red'}
            ).to_json_response()

        edit_id = int(edit_id)
        standard_id = int(standard_id)
        exam_id = int(exam_id)
        subject_id = int(subject_id)

        standard_exists = Standard.objects.filter(
            pk=standard_id, isDeleted=False, sessionID_id=current_session_id
        ).exists()
        exam_exists = Exam.objects.filter(
            pk=exam_id, isDeleted=False, sessionID_id=current_session_id
        ).exists()
        subject_exists = Subjects.objects.filter(
            pk=subject_id, isDeleted=False, sessionID_id=current_session_id
        ).exists()
        if not (standard_exists and exam_exists and subject_exists):
            return ErrorResponse(
                "Invalid class, exam, or subject for current session.",
                extra={'color': 'red'}
            ).to_json_response()

        instance = ExamTimeTable.objects.get(
            pk=edit_id, isDeleted=False, sessionID_id=current_session_id
        )
        parsed_exam_date = datetime.strptime(exam_date, '%d/%m/%Y').date()
        parsed_start_time = _parse_exam_time(start_time)
        parsed_end_time = _parse_exam_time(end_time)
        if parsed_start_time >= parsed_end_time:
            return ErrorResponse("Start time must be before end time.", extra={'color': 'red'}).to_json_response()

        is_valid, validation_message, validation_color = _validate_exam_timetable_business_rules(
            current_session_id=current_session_id,
            standard_id=standard_id,
            exam_id=exam_id,
            subject_id=subject_id,
            parsed_exam_date=parsed_exam_date,
            parsed_start_time=parsed_start_time,
            parsed_end_time=parsed_end_time,
            room_no=room_no,
            exclude_id=edit_id,
        )
        if not is_valid:
            return ErrorResponse(validation_message, extra={'color': validation_color}).to_json_response()

        instance.standardID_id = standard_id
        instance.examID_id = exam_id
        instance.subjectID_id = subject_id
        instance.examDate = parsed_exam_date
        instance.startTime = parsed_start_time
        instance.endTime = parsed_end_time
        instance.roomNo = room_no
        instance.note = note
        pre_save_with_user.send(sender=ExamTimeTable, instance=instance, user=request.user.pk)
        instance.save()
        return SuccessResponse("Exam timetable updated successfully.", extra={'color': 'success'}).to_json_response()
    except ExamTimeTable.DoesNotExist:
        return ErrorResponse("Exam timetable detail not found.", extra={'color': 'red'}).to_json_response()
    except ValueError:
        return ErrorResponse("Invalid date/time format.", extra={'color': 'red'}).to_json_response()
    except IntegrityError:
        return ErrorResponse(
            "Unable to update timetable due to conflict. Please refresh and try again.",
            extra={'color': 'red'}
        ).to_json_response()
    except Exception as e:
        logger.error(f"Error in update_exam_timetable: {e}")
        return ErrorResponse("Error in updating exam timetable.", extra={'color': 'red'}).to_json_response()


@transaction.atomic
@csrf_exempt
@login_required
def delete_exam_timetable(request):
    if request.method != 'POST':
        return ErrorResponse("Method not allowed", extra={'color': 'red'}).to_json_response()

    try:
        row_id = request.POST.get("dataID")
        instance = ExamTimeTable.objects.get(
            pk=row_id, isDeleted=False, sessionID_id=request.session['current_session']['Id']
        )
        instance.isDeleted = True
        pre_save_with_user.send(sender=ExamTimeTable, instance=instance, user=request.user.pk)
        instance.save()
        return SuccessResponse("Exam timetable deleted successfully.", extra={'color': 'success'}).to_json_response()
    except ExamTimeTable.DoesNotExist:
        return ErrorResponse("Exam timetable detail not found.", extra={'color': 'red'}).to_json_response()
    except Exception as e:
        logger.error(f"Error in delete_exam_timetable: {e}")
        return ErrorResponse("Error in deleting exam timetable.", extra={'color': 'red'}).to_json_response()


# assign exam to class
@transaction.atomic
@csrf_exempt
@login_required
def add_exam_to_class(request):
    if request.method == 'POST':
        try:
            standard = request.POST.get("standard")
            exam = request.POST.get("exam")
            fmark = request.POST.get("fmark")
            pmark = request.POST.get("pmark")
            sDate = request.POST.get("sDate")
            eDate = request.POST.get("eDate")
            subject_list = [int(x) for x in standard.split(',')]
            for s in subject_list:
                try:
                    AssignExamToClass.objects.get(examID_id=int(exam), standardID_id=int(s), isDeleted=False,
                                                  sessionID_id=request.session['current_session']['Id'])
                except:
                    instance = AssignExamToClass()
                    instance.standardID_id = int(s)
                    instance.examID_id = int(exam)
                    instance.fullMarks = float(fmark)
                    instance.passMarks = float(pmark)
                    instance.startDate = datetime.strptime(sDate, '%d/%m/%Y')
                    instance.endDate = datetime.strptime(eDate, '%d/%m/%Y')
                    pre_save_with_user.send(sender=AssignExamToClass, instance=instance, user=request.user.pk)
                    instance.save()
            return _api_response(
                {'status': 'success', 'message': 'Exam assigned successfully.', 'color': 'success'},
                safe=False)
        except:
            return _api_response({'status': 'error'}, safe=False)


class AssignExamToClassListJson(BaseDatatableView):
    order_columns = ['standardID.name', 'standardID.section',
                     'examID.name', 'fullMarks', 'passMarks', 'startDate', 'endDate', 'lastEditedBy', 'lastUpdatedOn']

    def get_initial_queryset(self):
        return AssignExamToClass.objects.select_related(
            'standardID', 'examID'
        ).only(
            'id', 'fullMarks', 'passMarks', 'startDate', 'endDate', 'lastEditedBy', 'lastUpdatedOn',
            'standardID__name', 'standardID__section',
            'examID__name'
        ).filter(
            isDeleted__exact=False,
            sessionID_id=self.request.session["current_session"]["Id"]
        ).order_by(
            'standardID__name')

    def filter_queryset(self, qs):
        class_filter = self.request.GET.get('class_filter')
        exam_filter = self.request.GET.get('exam_filter')

        if class_filter and str(class_filter).isdigit():
            qs = qs.filter(standardID_id=int(class_filter))

        if exam_filter and str(exam_filter).isdigit():
            qs = qs.filter(examID_id=int(exam_filter))

        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(standardID__name__icontains=search) | Q(examID__name__icontains=search)
                | Q(fullMarks__icontains=search) | Q(passMarks__icontains=search)
                | Q(endDate__icontains=search) | Q(
                    standardID__section__icontains=search) | Q(
                    startDate__icontains=search)
                | Q(lastEditedBy__icontains=search) | Q(lastUpdatedOn__icontains=search)
            )

        return qs

    def prepare_results(self, qs):
        json_data = []
        for item in qs:
            action = '''
              <button data-inverted="" data-tooltip="Edit Detail" data-position="left center" data-variation="mini" style="font-size:10px;" onclick = "GetDataDetails('{}')" class="ui circular facebook icon button green">
                <i class="pen icon"></i>
              </button>
              <button data-inverted="" data-tooltip="Delete" data-position="left center" data-variation="mini" style="font-size:10px;" onclick ="delData('{}')" class="ui circular youtube icon button" style="margin-left: 3px">
                <i class="trash alternate icon"></i>
              </button></td>'''.format(item.pk, item.pk),
            if item.standardID.section:
                section = item.standardID.section
            else:
                section = 'N/A'

            json_data.append([
                escape(item.standardID.name),
                escape(section),
                escape(item.examID.name),
                escape(item.fullMarks),
                escape(item.passMarks),
                escape(item.startDate.strftime('%d-%m-%Y')),
                escape(item.endDate.strftime('%d-%m-%Y')),
                escape(item.lastEditedBy or 'N/A'),
                escape(item.lastUpdatedOn.strftime('%d-%m-%Y %I:%M %p') if item.lastUpdatedOn else 'N/A'),
                action,

            ])

        return json_data


@transaction.atomic
@csrf_exempt
@login_required
def delete_assign_exam_to_class(request):
    if request.method == 'POST':
        try:
            id = request.POST.get("dataID")
            instance = AssignExamToClass.objects.get(pk=int(id), isDeleted=False,
                                                     sessionID_id=request.session['current_session']['Id'])
            instance.isDeleted = True
            pre_save_with_user.send(sender=AssignExamToClass, instance=instance, user=request.user.pk)
            instance.save()
            return _api_response(
                {'status': 'success', 'message': 'Assigned Exam detail deleted successfully.',
                 'color': 'success'}, safe=False)
        except:
            return _api_response({'status': 'error'}, safe=False)
    return _api_response({'status': 'error'}, safe=False)


@login_required
def get_assigned_exam_to_class_detail(request, **kwargs):
    try:
        id = request.GET.get('id')
        obj = AssignExamToClass.objects.get(pk=id, isDeleted=False,
                                            sessionID_id=request.session['current_session']['Id'])
        obj_dic = {
            'StandardID': obj.standardID_id,
            'ExamID': obj.examID_id,
            'FullMarks': obj.fullMarks,
            'PassMarks': obj.passMarks,
            'StartDate': obj.startDate.strftime('%d/%m/%Y'),
            'EndDate': obj.endDate.strftime('%d/%m/%Y'),
            'ID': obj.pk,
        }
        return _api_response({'status': 'success', 'data': obj_dic}, safe=False)
    except:
        return _api_response({'status': 'error'}, safe=False)


@transaction.atomic
@csrf_exempt
@login_required
def update_exam_to_class(request):
    if request.method == 'POST':
        try:
            editID = request.POST.get("editID")
            standard = request.POST.get("standard")
            exam = request.POST.get("exam")
            fmark = request.POST.get("fmark")
            pmark = request.POST.get("pmark")
            sDate = request.POST.get("sDate")
            eDate = request.POST.get("eDate")
            subject_list = [int(x) for x in standard.split(',')]
            instance = AssignExamToClass.objects.get(pk=int(editID))
            for s in subject_list:
                try:
                    AssignExamToClass.objects.get(examID_id=int(exam), standardID_id=int(s),
                                                  isDeleted=False,
                                                  sessionID_id=request.session['current_session']['Id']).exclude(
                        pk=int(editID))
                    return _api_response(
                        {'status': 'success', 'message': 'Detail already assigned.', 'color': 'info'},
                        safe=False)
                except:
                    instance.standardID_id = int(s)
                    instance.examID_id = int(exam)
                    instance.fullMarks = float(fmark)
                    instance.passMarks = float(pmark)
                    instance.startDate = datetime.strptime(sDate, '%d/%m/%Y')
                    instance.endDate = datetime.strptime(eDate, '%d/%m/%Y')
                    pre_save_with_user.send(sender=AssignExamToClass, instance=instance, user=request.user.pk)
                    instance.save()
            return _api_response(
                {'status': 'success', 'message': 'Detail updated successfully.', 'color': 'success'},
                safe=False)
        except:
            return _api_response({'status': 'error'}, safe=False)


@login_required
def get_exam_list_by_class_api(request):
    standard = request.GET.get('standard')
    objs = AssignExamToClass.objects.filter(isDeleted=False, sessionID_id=request.session['current_session']['Id'],
                                  standardID_id=int(standard)).order_by(
        'examID__name')
    data = []
    for obj in objs:
        name = obj.examID.name

        data_dic = {
            'ID': obj.pk,
            'Name': name

        }
        data.append(data_dic)
    return _api_response(
        {'status': 'success', 'data': data,
         'color': 'success'}, safe=False)



# Attendance ------------------------------------------------------------------

class TakeStudentAttendanceByClassJson(BaseDatatableView):
    order_columns = ['studentID.photo', 'studentID.name', 'studentID.roll', 'isPresent', 'absentReason', 'lastEditedBy', 'lastUpdatedOn']

    @transaction.atomic
    def get_initial_queryset(self):
        try:
            mode = self.request.GET.get("mode")

            if mode == "ByClass":
                standard = self.request.GET.get("standard")
                aDate = self.request.GET.get("aDate")
                aDate = datetime.strptime(aDate, '%d/%m/%Y')
                students = Student.objects.select_related().filter(isDeleted__exact=False, standardID_id=int(standard),
                                                                   sessionID_id=self.request.session["current_session"][
                                                                       "Id"]).order_by('roll')
                leave_map = approved_leave_map_for_date(
                    session_id=self.request.session["current_session"]["Id"],
                    role='student',
                    date_value=aDate.date(),
                    ids=[s.id for s in students]
                )
                for s in students:
                    leave_obj = leave_map.get(s.id)
                    leave_reason = ''
                    if leave_obj:
                        leave_reason = f"Approved Leave: {leave_obj.leaveTypeID.name if leave_obj.leaveTypeID else 'Leave'}"
                    try:
                        attendance_obj = StudentAttendance.objects.get(studentID_id=s.id, attendanceDate__icontains=aDate,
                                                                       standardID_id=int(standard), bySubject=False,
                                                                       sessionID_id=self.request.session["current_session"]["Id"])
                        if leave_reason and (attendance_obj.isPresent or not attendance_obj.absentReason):
                            attendance_obj.isPresent = False
                            attendance_obj.absentReason = leave_reason
                            pre_save_with_user.send(sender=StudentAttendance, instance=attendance_obj, user=self.request.user.pk)
                            attendance_obj.save()

                    except:
                        instance = StudentAttendance.objects.create(studentID_id=s.id, attendanceDate=aDate,
                                                                    standardID_id=int(standard), isPresent=False,
                                                                    bySubject=False, absentReason=leave_reason)
                        pre_save_with_user.send(sender=StudentAttendance, instance=instance, user=self.request.user.pk)

                return StudentAttendance.objects.select_related().filter(isDeleted__exact=False, bySubject=False,
                                                                         sessionID_id=
                                                                         self.request.session["current_session"][
                                                                             "Id"], attendanceDate__icontains=aDate,
                                                                         standardID_id=int(standard))
            elif mode == "BySubject":
                subjects = self.request.GET.get("subjects")

                sDate = self.request.GET.get("sDate")
                sDate = datetime.strptime(sDate, '%d/%m/%Y')
                try:
                    obj = AssignSubjectsToClass.objects.get(pk=int(subjects), isDeleted=False)
                    students = Student.objects.select_related().filter(isDeleted__exact=False,
                                                                       standardID_id=obj.standardID_id,
                                                                       sessionID_id=
                                                                       self.request.session["current_session"][
                                                                           "Id"]).order_by('roll')
                    leave_map = approved_leave_map_for_date(
                        session_id=self.request.session["current_session"]["Id"],
                        role='student',
                        date_value=sDate.date(),
                        ids=[s.id for s in students]
                    )
                    for s in students:
                        leave_obj = leave_map.get(s.id)
                        leave_reason = ''
                        if leave_obj:
                            leave_reason = f"Approved Leave: {leave_obj.leaveTypeID.name if leave_obj.leaveTypeID else 'Leave'}"
                        try:
                            attendance_obj = StudentAttendance.objects.get(studentID_id=s.id, attendanceDate__icontains=sDate,
                                                                           standardID_id=obj.standardID_id, bySubject=True,
                                                                           subjectID_id=obj.subjectID_id,
                                                                           sessionID_id=self.request.session["current_session"]["Id"])
                            if leave_reason and (attendance_obj.isPresent or not attendance_obj.absentReason):
                                attendance_obj.isPresent = False
                                attendance_obj.absentReason = leave_reason
                                pre_save_with_user.send(sender=StudentAttendance, instance=attendance_obj,
                                                        user=self.request.user.pk)
                                attendance_obj.save()
                        except:
                            instance = StudentAttendance.objects.create(studentID_id=s.id, attendanceDate=sDate,
                                                                        subjectID_id=obj.subjectID_id,
                                                                        standardID_id=obj.standardID_id,
                                                                        isPresent=False, bySubject=True,
                                                                        absentReason=leave_reason)
                            pre_save_with_user.send(sender=StudentAttendance, instance=instance,
                                                    user=self.request.user.pk)
                    return StudentAttendance.objects.select_related().filter(isDeleted__exact=False, bySubject=True,
                                                                             sessionID_id=
                                                                             self.request.session["current_session"][
                                                                                 "Id"], attendanceDate__icontains=sDate,
                                                                             subjectID_id=obj.subjectID_id,
                                                                             standardID_id=obj.standardID_id)
                except:
                    return StudentAttendance.objects.none()
            else:
                return StudentAttendance.objects.none()
        except:
            return StudentAttendance.objects.none()

    def filter_queryset(self, qs):

        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(standardID__name__icontains=search)
                | Q(lastEditedBy__icontains=search) | Q(lastUpdatedOn__icontains=search)
            )

        return qs

    def prepare_results(self, qs):
        json_data = []
        for item in qs:
            if item.studentID and item.studentID.photo:
                images = _avatar_image_html(item.studentID.photo)
            else:
                images = _avatar_image_html(None)

            action = '''<button class="ui mini primary button" onclick="pushAttendance({})">
  Save
</button>'''.format(item.pk),
            if item.isPresent:
                is_present = '''
            <div class="ui checkbox">
  <input type="checkbox" name="isPresent{}" id="isPresent{}" checked >
  <label>Mark as Present</label>
</div>
            '''.format(item.pk, item.pk)
            else:
                is_present = '''
                            <div class="ui checkbox">
                  <input type="checkbox" name="isPresent{}" id="isPresent{}" >
                  <label>Mark as Present</label>
                </div>
                            '''.format(item.pk, item.pk)

            reason = '''<div class="ui tiny input fluid">
  <input type="text" placeholder="Reason for Absent" name="reason{}" id="reason{}" value = "{}">
</div>
            '''.format(item.pk, item.pk, item.absentReason)

            json_data.append([
                images,
                escape(item.studentID.name),
                escape(item.studentID.roll or 'N/A'),
                is_present,
                reason,
                escape(item.lastEditedBy or 'N/A'),
                escape(item.lastUpdatedOn.strftime('%d-%m-%Y %I:%M %p') if item.lastUpdatedOn else 'N/A'),
                action,

            ])

        return json_data


@transaction.atomic
@csrf_exempt
@login_required
def add_student_attendance_by_class(request):
    if request.method == 'POST':
        id = request.POST.get("id")
        isPresent = request.POST.get("isPresent")
        reason = request.POST.get("reason")
        try:
            instance = StudentAttendance.objects.select_related('studentID').get(pk=int(id))
            if isPresent == 'true':
                isPresent = True
            else:
                isPresent = False
            if isPresent:
                leave_obj = approved_leave_for_date(
                    session_id=instance.sessionID_id,
                    role='student',
                    date_value=instance.attendanceDate.date() if instance.attendanceDate else None,
                    student_id=instance.studentID_id
                )
                if leave_obj:
                    return _api_response(
                        {'status': 'error', 'message': 'Cannot mark present on approved leave date.', 'color': 'orange'},
                        safe=False)
            instance.isPresent = isPresent
            instance.absentReason = reason
            pre_save_with_user.send(sender=StudentAttendance, instance=instance, user=request.user.pk)
            instance.save()
            return _api_response(
                {'status': 'success', 'message': 'Attendance added successfully.', 'color': 'success'},
                safe=False)
        except:

            return _api_response({'status': 'error'}, safe=False)


@transaction.atomic
@csrf_exempt
@login_required
def add_student_attendance_bulk_by_class(request):
    if request.method != 'POST':
        return _api_response({'status': 'error', 'message': 'Invalid request method.'}, safe=False)

    try:
        raw_entries = request.POST.get('entries', '[]')
        entries = json.loads(raw_entries)
        if not isinstance(entries, list) or len(entries) == 0:
            return _api_response({'status': 'error', 'message': 'No attendance entries found.', 'color': 'red'}, safe=False)
    except Exception:
        return _api_response({'status': 'error', 'message': 'Invalid attendance payload.', 'color': 'red'}, safe=False)

    updated_count = 0
    blocked_students = []
    current_session_id = request.session["current_session"]["Id"]

    for entry in entries:
        try:
            attendance_id = int(entry.get('id'))
        except Exception:
            continue

        is_present = bool(entry.get('isPresent'))
        reason = (entry.get('reason') or '').strip()

        try:
            instance = StudentAttendance.objects.select_related('studentID').get(
                pk=attendance_id,
                isDeleted=False,
                sessionID_id=current_session_id
            )
        except StudentAttendance.DoesNotExist:
            continue

        if is_present:
            leave_obj = approved_leave_for_date(
                session_id=instance.sessionID_id,
                role='student',
                date_value=instance.attendanceDate.date() if instance.attendanceDate else None,
                student_id=instance.studentID_id
            )
            if leave_obj:
                blocked_students.append(instance.studentID.name if instance.studentID else f'ID {attendance_id}')
                continue

        instance.isPresent = is_present
        instance.absentReason = '' if is_present else reason
        pre_save_with_user.send(sender=StudentAttendance, instance=instance, user=request.user.pk)
        instance.save()
        updated_count += 1

    if updated_count == 0 and blocked_students:
        return _api_response(
            {'status': 'error', 'message': 'Could not update attendance. Some students are on approved leave.', 'color': 'orange'},
            safe=False
        )

    if blocked_students:
        blocked_preview = ', '.join(blocked_students[:3])
        extra_text = f' (blocked: {blocked_preview}{"..." if len(blocked_students) > 3 else ""})'
        return _api_response(
            {'status': 'success',
             'message': f'Updated {updated_count} attendance record(s). {len(blocked_students)} skipped due to approved leave{extra_text}.',
             'color': 'orange'},
            safe=False
        )

    return _api_response(
        {'status': 'success', 'message': f'Updated {updated_count} attendance record(s) successfully.', 'color': 'success'},
        safe=False
    )


class StudentAttendanceHistoryByDateRangeJson(BaseDatatableView):
    order_columns = ['photo', 'name', 'roll']

    @transaction.atomic
    def get_initial_queryset(self):
        try:
            dateRangeStandard = self.request.GET.get("dateRangeStandard")
            dateRangeSubject = self.request.GET.get("dateRangeSubject")

            return Student.objects.select_related().filter(isDeleted__exact=False, standardID_id=int(dateRangeStandard),
                                                           sessionID_id=self.request.session["current_session"][
                                                               "Id"]).order_by('roll')
            # for s in students:
            #     try:
            #         StudentAttendance.objects.get(studentID_id=s.id, attendanceDate__icontains=aDate,
            #                                       standardID_id=int(standard), bySubject=False,
            #                                       sessionID_id=self.request.session["current_session"]["Id"])
            #
            #     except:
            #         instance = StudentAttendance.objects.create(studentID_id=s.id, attendanceDate=aDate,
            #                                                     standardID_id=int(standard), isPresent=False,
            #                                                     bySubject=False, )
            #         pre_save_with_user.send(sender=StudentAttendance, instance=instance, user=self.request.user.pk)
            #
            # return StudentAttendance.objects.select_related().filter(isDeleted__exact=False, bySubject=False,
            #                                                          sessionID_id=
            #                                                          self.request.session["current_session"][
            #                                                              "Id"], attendanceDate__icontains=aDate,
            #                                                          standardID_id=int(standard))

            # if dateRangeSubject == "All":
            #     return StudentAttendance.objects.select_related().filter(isDeleted__exact=False, bySubject=False,
            #                                                              standardID_id=int(dateRangeStandard),
            #                                                             sessionID_id=
            #                                                             self.request.session["current_session"][
            #                                                                 "Id"],
            #                                                             attendanceDate__range=[dateRangeStartDate,
            #                                                                                    dateRangeEndDate+timedelta(days=1)])
            # else:
            #     return StudentAttendance.objects.select_related().filter(isDeleted__exact=False, bySubject=True,
            #                                                              standardID_id=int(dateRangeStandard),
            #                                                             sessionID_id=
            #                                                             self.request.session["current_session"][
            #                                                                 "Id"],
            #                                                             attendanceDate__range=[dateRangeStartDate,
            #                                                                                    dateRangeEndDate+timedelta(days=1)],
            #                                                             subjectID_id=int(dateRangeSubject))
        except:
            return Student.objects.none()

    def filter_queryset(self, qs):

        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(name__icontains=search) | Q(roll__icontains=search)
                | Q(standardID__name__icontains=search)
                | Q(lastEditedBy__icontains=search) | Q(lastUpdatedOn__icontains=search)
            )

        return qs

    def prepare_results(self, qs):
        dateRangeStandard = self.request.GET.get("dateRangeStandard")
        dateRangeSubject = self.request.GET.get("dateRangeSubject")
        dateRangeStartDate = self.request.GET.get("dateRangeStartDate")
        dateRangeEndDate = self.request.GET.get("dateRangeEndDate")
        dateRangeStartDate = datetime.strptime(dateRangeStartDate, '%d/%m/%Y')
        dateRangeEndDate = datetime.strptime(dateRangeEndDate, '%d/%m/%Y')
        json_data = []
        for item in qs:
            images = _avatar_image_html(item.photo)
            if dateRangeSubject == "all":
                present_count = StudentAttendance.objects.filter(studentID_id=item.id, isPresent=True, bySubject=False,
                                                                 isHoliday=False,
                                                                 attendanceDate__range=[dateRangeStartDate,
                                                                                        dateRangeEndDate + timedelta(
                                                                                            days=1)],
                                                                 standardID_id=int(dateRangeStandard)).count()
                absent_count = StudentAttendance.objects.filter(studentID_id=item.id, isPresent=False, bySubject=False,
                                                                isHoliday=False,
                                                                attendanceDate__range=[dateRangeStartDate,
                                                                                       dateRangeEndDate + timedelta(
                                                                                           days=1)],
                                                                standardID_id=int(dateRangeStandard)).count()

            else:
                present_count = StudentAttendance.objects.filter(studentID_id=item.id, isPresent=True, bySubject=True,
                                                                 subjectID_id=int(dateRangeSubject),
                                                                 isHoliday=False,
                                                                 attendanceDate__range=[dateRangeStartDate,
                                                                                        dateRangeEndDate + timedelta(
                                                                                            days=1)],
                                                                 standardID_id=int(dateRangeStandard)).count()
                absent_count = StudentAttendance.objects.filter(studentID_id=item.id, isPresent=False, bySubject=True,
                                                                isHoliday=False, subjectID_id=int(dateRangeSubject),
                                                                attendanceDate__range=[dateRangeStartDate,
                                                                                       dateRangeEndDate + timedelta(
                                                                                           days=1)],
                                                                standardID_id=int(dateRangeStandard)).count()

            if present_count + absent_count != 0:
                percentage = present_count / (present_count + absent_count) * 100
            else:
                # Handle the case when the denominator is zero
                percentage = 0

            roll_value = (str(item.roll).strip() if item.roll is not None else '')
            if roll_value:
                try:
                    roll_value = float(roll_value)
                except (TypeError, ValueError):
                    roll_value = escape(roll_value)

            json_data.append([
                images,
                escape(item.name),
                roll_value,
                present_count,
                absent_count,
                present_count + absent_count,
                round(percentage, 2)

            ])

        return json_data


class StudentAttendanceHistoryByDateRangeAndStudentJson(BaseDatatableView):
    order_columns = ['attendanceDate', 'isPresent', 'isPresent', 'absentReason', 'lastEditedBy', 'lastUpdatedOn']

    @transaction.atomic
    def get_initial_queryset(self):
        try:
            ByStudentSubject = self.request.GET.get("ByStudentSubject")
            ByStudentStudent = self.request.GET.get("ByStudentStudent")
            ByStudentStartDate = self.request.GET.get("ByStudentStartDate")
            ByStudentEndDate = self.request.GET.get("ByStudentEndDate")
            ByStudentStartDate = datetime.strptime(ByStudentStartDate, '%d/%m/%Y')
            ByStudentEndDate = datetime.strptime(ByStudentEndDate, '%d/%m/%Y')
            if ByStudentSubject == "all":
                return StudentAttendance.objects.select_related().filter(isDeleted__exact=False, isHoliday=False,
                                                                         studentID_id=int(ByStudentStudent),
                                                                         attendanceDate__range=[ByStudentStartDate,
                                                                                                ByStudentEndDate + timedelta(
                                                                                                    days=1)],
                                                                         sessionID_id=
                                                                         self.request.session["current_session"][
                                                                             "Id"]).order_by('attendanceDate')
            else:
                return StudentAttendance.objects.select_related().filter(isDeleted__exact=False, isHoliday=False,
                                                                         subjectID_id=int(ByStudentSubject),
                                                                         studentID_id=int(ByStudentStudent),
                                                                         attendanceDate__range=[ByStudentStartDate,
                                                                                                ByStudentEndDate + timedelta(
                                                                                                    days=1)],
                                                                         sessionID_id=
                                                                         self.request.session["current_session"][
                                                                             "Id"]).order_by('attendanceDate')


        except:
            return StudentAttendance.objects.none()

    def filter_queryset(self, qs):

        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(attendanceDate__icontains=search) | Q(isPresent__icontains=search)
                | Q(lastEditedBy__icontains=search) | Q(lastUpdatedOn__icontains=search)
            )

        return qs

    def prepare_results(self, qs):
        json_data = []
        for item in qs:
            if item.isPresent == True:
                Present = 'Yes'
                Absent = 'No'
            else:
                Present = 'No'
                Absent = 'Yes'

            json_data.append([
                escape(item.attendanceDate.strftime('%d-%m-%Y')),
                escape(Present),
                escape(Absent),
                escape(item.absentReason),
                escape(item.lastEditedBy or 'N/A'),
                escape(item.lastUpdatedOn.strftime('%d-%m-%Y %I:%M %p') if item.lastUpdatedOn else 'N/A'),

            ])

        return json_data


class TakeTeacherAttendanceJson(BaseDatatableView):
    order_columns = ['teacherID.photo', 'teacherID.name', 'teacherID.staffType', 'teacherID.employeeCode', 'isPresent',
                     'absentReason', 'lastEditedBy', 'lastUpdatedOn']

    @transaction.atomic
    def get_initial_queryset(self):
        try:
            aDate = self.request.GET.get("aDate")
            aDate = datetime.strptime(aDate, '%d/%m/%Y')
            teachers = TeacherDetail.objects.select_related().filter(isDeleted__exact=False,
                                                                     sessionID_id=
                                                                     self.request.session["current_session"][
                                                                         "Id"])
            leave_map = approved_leave_map_for_date(
                session_id=self.request.session["current_session"]["Id"],
                role='teacher',
                date_value=aDate.date(),
                ids=[s.id for s in teachers]
            )
            for s in teachers:
                leave_obj = leave_map.get(s.id)
                leave_reason = ''
                if leave_obj:
                    leave_reason = f"Approved Leave: {leave_obj.leaveTypeID.name if leave_obj.leaveTypeID else 'Leave'}"
                try:
                    attendance_obj = TeacherAttendance.objects.get(attendanceDate__icontains=aDate, isDeleted=False, teacherID_id=s.id,
                                                                   sessionID_id=self.request.session["current_session"]["Id"])
                    if leave_reason and (attendance_obj.isPresent or not attendance_obj.absentReason):
                        attendance_obj.isPresent = False
                        attendance_obj.absentReason = leave_reason
                        pre_save_with_user.send(sender=TeacherAttendance, instance=attendance_obj, user=self.request.user.pk)
                        attendance_obj.save()

                except:
                    instance = TeacherAttendance.objects.create(attendanceDate=aDate, isDeleted=False,
                                                                teacherID_id=s.id, absentReason=leave_reason)
                    pre_save_with_user.send(sender=TeacherAttendance, instance=instance, user=self.request.user.pk)

            return TeacherAttendance.objects.select_related().filter(isDeleted__exact=False,
                                                                     attendanceDate__icontains=aDate, isDeleted=False,
                                                                     sessionID_id=
                                                                     self.request.session["current_session"][
                                                                         "Id"])

        except:
            return TeacherAttendance.objects.none()

    def filter_queryset(self, qs):

        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(teacherID__name__icontains=search)
                | Q(teacherID__employeeCode__icontains=search)
                | Q(teacherID__staffType__icontains=search)
                | Q(lastEditedBy__icontains=search) | Q(lastUpdatedOn__icontains=search)
            )

        return qs

    def prepare_results(self, qs):
        json_data = []
        for item in qs:
            images = _avatar_image_html(item.teacherID.photo)

            action = '''<button class="ui mini primary button" onclick="pushAttendance({})">
  Save
</button>'''.format(item.pk),
            if item.isPresent:
                is_present = '''
            <div class="ui checkbox">
  <input type="checkbox" name="isPresent{}" id="isPresent{}" checked >
  <label>Mark as Present</label>
</div>
            '''.format(item.pk, item.pk)
            else:
                is_present = '''
                            <div class="ui checkbox">
                  <input type="checkbox" name="isPresent{}" id="isPresent{}" >
                  <label>Mark as Present</label>
                </div>
                            '''.format(item.pk, item.pk)

            reason = '''<div class="ui tiny input fluid">
  <input type="text" placeholder="Reason for Absent" name="reason{}" id="reason{}" value = "{}">
</div>
            '''.format(item.pk, item.pk, item.absentReason)

            json_data.append([
                images,
                escape(item.teacherID.name),
                escape(item.teacherID.staffType),
                escape(item.teacherID.employeeCode or 'N/A'),
                is_present,
                reason,
                escape(item.lastEditedBy or 'N/A'),
                escape(item.lastUpdatedOn.strftime('%d-%m-%Y %I:%M %p') if item.lastUpdatedOn else 'N/A'),
                action,

            ])

        return json_data


@transaction.atomic
@csrf_exempt
@login_required
def add_staff_attendance_api(request):
    if request.method == 'POST':
        id = request.POST.get("id")
        isPresent = request.POST.get("isPresent")
        reason = request.POST.get("reason")
        try:
            instance = TeacherAttendance.objects.select_related('teacherID').get(pk=int(id))
            if isPresent == 'true':
                isPresent = True
            else:
                isPresent = False
            if isPresent:
                leave_obj = approved_leave_for_date(
                    session_id=instance.sessionID_id,
                    role='teacher',
                    date_value=instance.attendanceDate.date() if instance.attendanceDate else None,
                    teacher_id=instance.teacherID_id
                )
                if leave_obj:
                    return _api_response(
                        {'status': 'error', 'message': 'Cannot mark present on approved leave date.', 'color': 'orange'},
                        safe=False)
            instance.isPresent = isPresent
            instance.absentReason = reason
            pre_save_with_user.send(sender=TeacherAttendance, instance=instance, user=request.user.pk)
            instance.save()
            return _api_response(
                {'status': 'success', 'message': 'Attendance added successfully.', 'color': 'success'},
                safe=False)
        except:

            return _api_response({'status': 'error'}, safe=False)


@transaction.atomic
@csrf_exempt
@login_required
def add_staff_attendance_bulk_api(request):
    if request.method != 'POST':
        return _api_response({'status': 'error', 'message': 'Invalid request method.'}, safe=False)

    try:
        raw_entries = request.POST.get('entries', '[]')
        entries = json.loads(raw_entries)
        if not isinstance(entries, list) or len(entries) == 0:
            return _api_response({'status': 'error', 'message': 'No attendance entries found.', 'color': 'red'}, safe=False)
    except Exception:
        return _api_response({'status': 'error', 'message': 'Invalid attendance payload.', 'color': 'red'}, safe=False)

    updated_count = 0
    blocked_staff = []
    current_session_id = request.session["current_session"]["Id"]

    for entry in entries:
        try:
            attendance_id = int(entry.get('id'))
        except Exception:
            continue

        is_present = bool(entry.get('isPresent'))
        reason = (entry.get('reason') or '').strip()

        try:
            instance = TeacherAttendance.objects.select_related('teacherID').get(
                pk=attendance_id,
                isDeleted=False,
                sessionID_id=current_session_id
            )
        except TeacherAttendance.DoesNotExist:
            continue

        if is_present:
            leave_obj = approved_leave_for_date(
                session_id=instance.sessionID_id,
                role='teacher',
                date_value=instance.attendanceDate.date() if instance.attendanceDate else None,
                teacher_id=instance.teacherID_id
            )
            if leave_obj:
                blocked_staff.append(instance.teacherID.name if instance.teacherID else f'ID {attendance_id}')
                continue

        instance.isPresent = is_present
        instance.absentReason = '' if is_present else reason
        pre_save_with_user.send(sender=TeacherAttendance, instance=instance, user=request.user.pk)
        instance.save()
        updated_count += 1

    if updated_count == 0 and blocked_staff:
        return _api_response(
            {'status': 'error', 'message': 'Could not update attendance. Some staff are on approved leave.', 'color': 'orange'},
            safe=False
        )

    if blocked_staff:
        blocked_preview = ', '.join(blocked_staff[:3])
        extra_text = f' (blocked: {blocked_preview}{"..." if len(blocked_staff) > 3 else ""})'
        return _api_response(
            {'status': 'success',
             'message': f'Updated {updated_count} attendance record(s). {len(blocked_staff)} skipped due to approved leave{extra_text}.',
             'color': 'orange'},
            safe=False
        )

    return _api_response(
        {'status': 'success', 'message': f'Updated {updated_count} attendance record(s) successfully.', 'color': 'success'},
        safe=False
    )


class StaffAttendanceHistoryByDateRangeJson(BaseDatatableView):
    order_columns = ['photo', 'name', 'staffType', 'employeeCode']

    @transaction.atomic
    def get_initial_queryset(self):
        try:
            return TeacherDetail.objects.select_related().filter(isDeleted__exact=False,
                                                                 sessionID_id=self.request.session["current_session"][
                                                                     "Id"])
        except:
            return TeacherDetail.objects.none()

    def filter_queryset(self, qs):

        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(name__icontains=search) | Q(staffType__icontains=search)
                | Q(employeeCode__icontains=search)
                | Q(lastEditedBy__icontains=search) | Q(lastUpdatedOn__icontains=search)
            )

        return qs

    def prepare_results(self, qs):
        json_data = []
        try:
            dateRangeStartDate = self.request.GET.get("dateRangeStartDate")
            dateRangeEndDate = self.request.GET.get("dateRangeEndDate")
            dateRangeStartDate = datetime.strptime(dateRangeStartDate, '%d/%m/%Y')
            dateRangeEndDate = datetime.strptime(dateRangeEndDate, '%d/%m/%Y')

            for item in qs:
                images = _avatar_image_html(item.photo)
                attendance_qs = TeacherAttendance.objects.filter(
                    teacherID_id=item.id,
                    isHoliday=False,
                    isDeleted=False,
                    sessionID_id=self.request.session["current_session"]["Id"],
                    attendanceDate__range=[dateRangeStartDate, dateRangeEndDate + timedelta(days=1)],
                )
                leave_count = _count_approved_teacher_leave_days(
                    session_id=self.request.session["current_session"]["Id"],
                    teacher_id=item.id,
                    start_date=dateRangeStartDate.date(),
                    end_date=dateRangeEndDate.date(),
                )
                present_count = attendance_qs.filter(isPresent=True).count()
                absent_count = attendance_qs.filter(isPresent=False).exclude(
                    absentReason__istartswith='Approved Leave'
                ).count()
                total_days = present_count + absent_count + leave_count

                if total_days != 0:
                    percentage = present_count / total_days * 100
                else:
                    # Handle the case when the denominator is zero
                    percentage = 0

                json_data.append([
                    images,
                    escape(item.name),
                    escape(item.staffType),
                    escape(item.employeeCode),
                    present_count,
                    absent_count,
                    leave_count,
                    total_days,
                    round(percentage, 2)

                ])

        except:
            pass
        return json_data


class StaffAttendanceHistoryByDateRangeAndStaffJson(BaseDatatableView):
    order_columns = ['attendanceDate', 'isPresent', 'isPresent', 'isPresent', 'absentReason', 'lastEditedBy', 'lastUpdatedOn']

    @transaction.atomic
    def get_initial_queryset(self):
        try:
            ByStaffStaff = self.request.GET.get("ByStaffStaff")
            ByStudentStartDate = self.request.GET.get("ByStudentStartDate")
            ByStudentEndDate = self.request.GET.get("ByStudentEndDate")
            ByStudentStartDate = datetime.strptime(ByStudentStartDate, '%d/%m/%Y')
            ByStudentEndDate = datetime.strptime(ByStudentEndDate, '%d/%m/%Y')
            session_id = self.request.session["current_session"]["Id"]
            teacher_id = int(ByStaffStaff)

            approved_leaves = LeaveApplication.objects.select_related('leaveTypeID').filter(
                isDeleted=False,
                sessionID_id=session_id,
                applicantRole='teacher',
                teacherID_id=teacher_id,
                status='approved',
                startDate__lte=ByStudentEndDate.date(),
                endDate__gte=ByStudentStartDate.date(),
            )
            for leave in approved_leaves:
                leave_type_name = leave.leaveTypeID.name if leave.leaveTypeID else 'Leave'
                leave_reason = f'Approved Leave: {leave_type_name}'
                day = max(ByStudentStartDate.date(), leave.startDate)
                end_day = min(ByStudentEndDate.date(), leave.endDate)
                while day <= end_day:
                    attendance_dt = datetime(day.year, day.month, day.day)
                    exists = TeacherAttendance.objects.filter(
                        isDeleted=False,
                        sessionID_id=session_id,
                        teacherID_id=teacher_id,
                        attendanceDate__date=day,
                    ).exists()
                    if not exists:
                        instance = TeacherAttendance(
                            attendanceDate=attendance_dt,
                            isDeleted=False,
                            teacherID_id=teacher_id,
                            isPresent=False,
                            absentReason=leave_reason,
                        )
                        pre_save_with_user.send(sender=TeacherAttendance, instance=instance, user=self.request.user.pk)
                    day += timedelta(days=1)

            return TeacherAttendance.objects.select_related().filter(isDeleted__exact=False, isHoliday=False,
                                                                     teacherID_id=teacher_id,
                                                                     attendanceDate__range=[ByStudentStartDate,
                                                                                            ByStudentEndDate + timedelta(
                                                                                                days=1)],
                                                                     sessionID_id=
                                                                     session_id).order_by('attendanceDate')


        except:
            return TeacherAttendance.objects.none()

    def filter_queryset(self, qs):

        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(attendanceDate__icontains=search) | Q(isPresent__icontains=search)
                | Q(lastEditedBy__icontains=search) | Q(lastUpdatedOn__icontains=search)
            )

        return qs

    def prepare_results(self, qs):
        json_data = []
        for item in qs:
            is_leave = (item.absentReason or '').strip().lower().startswith('approved leave')
            if item.isPresent is True:
                Present = 'Yes'
                Absent = 'No'
                Leave = 'No'
            else:
                Present = 'No'
                if is_leave:
                    Absent = 'No'
                    Leave = 'Yes'
                else:
                    Absent = 'Yes'
                    Leave = 'No'

            json_data.append([
                escape(item.attendanceDate.strftime('%d-%m-%Y')),
                escape(Present),
                escape(Absent),
                escape(Leave),
                escape(item.absentReason),
                escape(item.lastEditedBy or 'N/A'),
                escape(item.lastUpdatedOn.strftime('%d-%m-%Y %I:%M %p') if item.lastUpdatedOn else 'N/A'),

            ])

        return json_data


# Student fee ---------------------------------------------------------------
class FeeByStudentJson(BaseDatatableView):
    order_columns = ['month', 'isPaid', 'payDate', 'amount', 'note', 'lastEditedBy', 'lastUpdatedOn']

    @transaction.atomic
    def get_initial_queryset(self):
        try:
            student = self.request.GET.get("student")
            standard = self.request.GET.get("standard")
            for month in MONTHS_LIST:
                try:
                    StudentFee.objects.get(studentID_id=int(student), month__iexact=month,
                                           standardID_id=int(standard), isDeleted=False,
                                           sessionID_id=self.request.session["current_session"]["Id"])

                except:
                    instance = StudentFee.objects.create(studentID_id=int(student), month=month,
                                                         standardID_id=int(standard),
                                                         )
                    pre_save_with_user.send(sender=StudentFee, instance=instance, user=self.request.user.pk)

            return StudentFee.objects.select_related().filter(studentID_id=int(student),
                                                              standardID_id=int(standard), isDeleted=False,
                                                              sessionID_id=self.request.session["current_session"][
                                                                  "Id"]).order_by('id')

        except:
            return StudentFee.objects.none()

    def filter_queryset(self, qs):

        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(month__icontains=search)
                | Q(amount__icontains=search)
                | Q(payDate__icontains=search)
                | Q(isPaid__icontains=search) | Q(note__icontains=search)
                | Q(lastEditedBy__icontains=search) | Q(lastUpdatedOn__icontains=search)
            )

        return qs

    def prepare_results(self, qs):
        json_data = []
        for item in qs:

            action = '''<button class="ui mini primary button" onclick="pushFee({})">
  Save
</button>'''.format(item.pk),
            if item.isPaid:
                is_present = '''
            <div class="ui checkbox">
  <input type="checkbox" name="isPresent{}" id="isPresent{}" checked >
  <label>Mark as Pay</label>
</div>
            '''.format(item.pk, item.pk)
            else:
                is_present = '''
                            <div class="ui checkbox">
                  <input type="checkbox" name="isPresent{}" id="isPresent{}" >
                  <label>Mark as Paid</label>
                </div>
                            '''.format(item.pk, item.pk)

            reason = '''<div class="ui tiny input fluid">
  <input type="text" placeholder="Remark" name="reason{}" id="reason{}" value = "{}">
</div>
            '''.format(item.pk, item.pk, item.note)
            amount = '''<div class="ui tiny input fluid">
              <input type="number" placeholder="Amount" name="amount{}" id="amount{}" value = "{}">
            </div>
                        '''.format(item.pk, item.pk, item.amount)

            if item.payDate:
                payDate = item.payDate.strftime('%d-%m-%Y')
            else:
                payDate = 'N/A'

            json_data.append([
                escape(item.month),
                is_present,
                payDate,
                amount,
                reason,
                escape(item.lastEditedBy or 'N/A'),
                escape(item.lastUpdatedOn.strftime('%d-%m-%Y %I:%M %p') if item.lastUpdatedOn else 'N/A'),
                action,

            ])

        return json_data


@transaction.atomic
@csrf_exempt
@login_required
def add_student_fee_api(request):
    if request.method == 'POST':
        id = request.POST.get("id")
        isPresent = request.POST.get("isPresent")
        reason = request.POST.get("reason")
        amount = request.POST.get("amount")
        try:
            instance = StudentFee.objects.get(pk=int(id))
            if isPresent == 'true':
                isPresent = True
            else:
                isPresent = False
            instance.isPaid = isPresent
            instance.note = reason
            instance.amount = float(amount)
            instance.payDate = datetime.today().date()
            pre_save_with_user.send(sender=StudentFee, instance=instance, user=request.user.pk)
            instance.save()
            return _api_response(
                {'status': 'success', 'message': 'Student fee added successfully.', 'color': 'success'},
                safe=False)
        except:

            return _api_response({'status': 'error'}, safe=False)


class StudentFeeDetailsByClassJson(BaseDatatableView):
    order_columns = ['photo', 'name', 'roll']

    @transaction.atomic
    def get_initial_queryset(self):
        try:
            standard = self.request.GET.get("standard")

            return Student.objects.select_related().filter(isDeleted__exact=False, standardID_id=int(standard),
                                                           sessionID_id=self.request.session["current_session"][
                                                               "Id"]).order_by('roll')
        except:
            return Student.objects.none()

    def filter_queryset(self, qs):

        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(name__icontains=search) | Q(roll__icontains=search)
                | Q(standardID__name__icontains=search)
                | Q(lastEditedBy__icontains=search) | Q(lastUpdatedOn__icontains=search)
            )

        return qs

    def prepare_results(self, qs):

        json_data = []
        for item in qs:
            January: str = 'Due'
            February: str = 'Due'
            March: str = 'Due'
            April: str = 'Due'
            May: str = 'Due'
            June: str = 'Due'
            July: str = 'Due'
            August: str = 'Due'
            September: str = 'Due'
            October: str = 'Due'
            November: str = 'Due'
            December: str = 'Due'

            for month in MONTHS_LIST:
                obj = StudentFee.objects.filter(studentID_id=item.id, month__iexact=month, isDeleted=False, isPaid=True,
                                                sessionID_id=self.request.session["current_session"][
                                                    "Id"]).first()

                if obj:
                    if month == 'January':
                        January = 'Paid'
                    elif month == 'February':
                        February = 'Paid'
                    elif month == 'March':
                        March = 'Paid'
                    elif month == 'April':
                        April = 'Paid'
                    elif month == 'May':
                        May = 'Paid'
                    elif month == 'June':
                        June = 'Paid'
                    elif month == 'July':
                        July = 'Paid'
                    elif month == 'August':
                        August = 'Paid'
                    elif month == 'September':
                        September = 'Paid'
                    elif month == 'October':
                        October = 'Paid'
                    elif month == 'November':
                        November = 'Paid'
                    elif month == 'December':
                        December = 'Paid'

            images = _avatar_image_html(item.photo)
            roll_value = (str(item.roll).strip() if item.roll is not None else '')
            if roll_value:
                try:
                    roll_value = float(roll_value)
                except (TypeError, ValueError):
                    roll_value = escape(roll_value)
            json_data.append([
                images,
                escape(item.name),
                roll_value,
                January,
                February,
                March,
                April,
                May,
                June,
                July,
                August,
                September,
                October,
                November,
                December,

            ])

        return json_data


class StudentFeeDetailsByStudentJson(BaseDatatableView):
    order_columns = ['month', 'isPaid', 'payDate', 'amount', 'note', 'lastEditedBy', 'lastUpdatedOn']

    @transaction.atomic
    def get_initial_queryset(self):
        try:
            standardByStudent = self.request.GET.get("standardByStudent")
            student = self.request.GET.get("student")

            return StudentFee.objects.select_related().filter(isDeleted__exact=False, studentID_id=int(student),
                                                              standardID_id=int(standardByStudent),
                                                              sessionID_id=self.request.session["current_session"][
                                                                  "Id"])
        except:
            return StudentFee.objects.none()

    def filter_queryset(self, qs):

        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(month__icontains=search) | Q(note__icontains=search)
                | Q(amount__icontains=search)
                | Q(lastEditedBy__icontains=search) | Q(lastUpdatedOn__icontains=search)
            )

        return qs

    def prepare_results(self, qs):

        json_data = []
        for item in qs:
            if item.isPaid == True:
                status = 'Paid'
                payDate = item.payDate.strftime('%d-%m-%Y')
            else:
                status = 'Due'
                payDate = 'N/A'

            json_data.append([

                escape(item.month),
                status,
                payDate,
                escape(item.amount),
                escape(item.note),
                escape(item.lastEditedBy or 'N/A'),
                escape(item.lastUpdatedOn.strftime('%d-%m-%Y %I:%M %p') if item.lastUpdatedOn else 'N/A'),

            ])

        return json_data


# Marks of Students by Subject ---------------------------------
class MarksOfSubjectsByStudentJson(BaseDatatableView):
    order_columns = ['studentID.photo', 'studentID.name', 'studentID.roll', 'examID.fullMarks', 'examID.passMarks', 'mark', 'note', 'lastEditedBy', 'lastUpdatedOn']

    @transaction.atomic
    def get_initial_queryset(self):
        try:
            standard = self.request.GET.get("standard")
            exam = self.request.GET.get("exam")
            subject = self.request.GET.get("subject")
            students = Student.objects.filter(standardID_id=int(standard), isDeleted=False, sessionID_id=self.request.session["current_session"]["Id"])

            for stu in students:
                try:
                    MarkOfStudentsByExam.objects.get(studentID_id=int(stu.pk), subjectID_id=int(subject), examID_id=int(exam),
                                           standardID_id=int(standard), isDeleted=False,
                                           sessionID_id=self.request.session["current_session"]["Id"])

                except:
                    instance = MarkOfStudentsByExam.objects.create(studentID_id=int(stu.pk), subjectID_id=int(subject), examID_id=int(exam),
                                           standardID_id=int(standard)
                                                         )
                    pre_save_with_user.send(sender=MarkOfStudentsByExam, instance=instance, user=self.request.user.pk)

            return MarkOfStudentsByExam.objects.filter(standardID_id=int(standard), isDeleted=False, sessionID_id=self.request.session["current_session"]["Id"], examID_id=int(exam), subjectID_id=int(subject))

        except:
            return MarkOfStudentsByExam.objects.none()

    def filter_queryset(self, qs):

        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(studentID__name__icontains=search)
                |Q(examID__fullMarks__icontains=search)
                |Q(examID__passMarks__icontains=search)
                |Q(mark__icontains=search)
                |Q(note__icontains=search)
                | Q(studentID__roll__icontains=search)
              | Q(lastEditedBy__icontains=search) | Q(lastUpdatedOn__icontains=search)
            )

        return qs

    def prepare_results(self, qs):
        json_data = []
        for item in qs:

            action = '''<button class="ui mini primary button" onclick="pushMark({})">
  Save
</button>'''.format(item.pk),


            marks_obtained = '''<div class="ui tiny input fluid">
  <input type="number" placeholder="Mark Obtained" name="mark{}" id="mark{}" value = "{}">
</div>
            '''.format(item.pk, item.pk, item.mark)

            note = '''<div class="ui tiny input fluid">
              <input type="text" placeholder="Note" name="note{}" id="note{}" value = "{}">
            </div>
                        '''.format(item.pk, item.pk, item.note)
            if item.studentID and item.studentID.photo:
                images = _avatar_image_html(item.studentID.photo)
            else:
                images = _avatar_image_html(None)
            json_data.append([
                images,
                escape(item.studentID.name),
                escape(item.studentID.roll or 'N/A'),
                item.examID.fullMarks,
                item.examID.passMarks,
                marks_obtained,
                note,
                escape(item.lastEditedBy or 'N/A'),
                escape(item.lastUpdatedOn.strftime('%d-%m-%Y %I:%M %p') if item.lastUpdatedOn else 'N/A'),
                action,

            ])

        return json_data


@transaction.atomic
@csrf_exempt
@login_required
def add_subject_mark_api(request):
    if request.method == 'POST':
        id = request.POST.get("id")
        note = request.POST.get("note")
        mark = request.POST.get("mark")
        try:
            instance = MarkOfStudentsByExam.objects.get(pk=int(id))
            instance.note = note
            instance.mark = float(mark)
            instance.payDate = datetime.today().date()
            pre_save_with_user.send(sender=MarkOfStudentsByExam, instance=instance, user=request.user.pk)
            instance.save()
            return _api_response(
                {'status': 'success', 'message': 'Mark added successfully.', 'color': 'success'},
                safe=False)
        except:

            return _api_response({'status': 'error'}, safe=False)


class StudentMarksDetailsByClassAndExamJson(BaseDatatableView):
    order_columns = ['photo', 'name', 'roll']

    @transaction.atomic
    def get_initial_queryset(self):
        try:
            standard = self.request.GET.get("standard")

            return Student.objects.select_related().filter(isDeleted__exact=False, standardID_id=int(standard),
                                                           sessionID_id=self.request.session["current_session"][
                                                               "Id"]).order_by('roll')
        except:
            return Student.objects.none()

    def filter_queryset(self, qs):

        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(name__icontains=search) | Q(roll__icontains=search)
                | Q(standardID__name__icontains=search)
                | Q(lastEditedBy__icontains=search) | Q(lastUpdatedOn__icontains=search)
            )

        return qs

    def prepare_results(self, qs):
        standard = self.request.GET.get("standard")
        exam = self.request.GET.get("exam")

        json_data = []
        for item in qs:
            subject_list = AssignSubjectsToClass.objects.filter(standardID_id=int(standard), isDeleted=False, sessionID_id=self.request.session["current_session"]["Id"])
            subs = [i.subjectID.name for i in subject_list]
            marks = []
            for s in subs:
                exam_sub_list_by_student = MarkOfStudentsByExam.objects.filter(
                    studentID_id=item.id,
                    isDeleted=False,
                    examID_id=int(exam),
                    sessionID_id=self.request.session["current_session"]["Id"],
                    subjectID__subjectID__name=s,
                ).first()
                marks.append(exam_sub_list_by_student.mark if exam_sub_list_by_student else 0)

            if item.photo:
                images = _avatar_image_html(item.photo)
            else:
                images = _avatar_image_html(None)
            json_data.append([
                images,
                escape(item.name),
                escape(item.roll or 'N/A'),


            ] + marks)
        return json_data


class StudentMarksDetailsByStudentJson(BaseDatatableView):
    order_columns = [
        'examID__examID__name',
        'subjectID__subjectID__name',
        'examID__fullMarks',
        'examID__passMarks',
        'mark',
        'note',
        'lastEditedBy',
        'lastUpdatedOn',
    ]

    @transaction.atomic
    def get_initial_queryset(self):
        try:
            standard = self.request.GET.get("standardByStudent")
            student = self.request.GET.get("student")
            return MarkOfStudentsByExam.objects.select_related(
                'examID',
                'examID__examID',
                'subjectID',
                'subjectID__subjectID',
            ).filter(
                isDeleted=False,
                sessionID_id=self.request.session["current_session"]["Id"],
                standardID_id=int(standard),
                studentID_id=int(student),
            ).order_by('examID__examID__name', 'subjectID__subjectID__name')
        except Exception:
            return MarkOfStudentsByExam.objects.none()

    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(examID__examID__name__icontains=search)
                | Q(subjectID__subjectID__name__icontains=search)
                | Q(examID__fullMarks__icontains=search)
                | Q(examID__passMarks__icontains=search)
                | Q(mark__icontains=search)
                | Q(note__icontains=search)
                | Q(lastEditedBy__icontains=search)
                | Q(lastUpdatedOn__icontains=search)
            )
        return qs

    def prepare_results(self, qs):
        json_data = []
        for item in qs:
            exam_name = 'N/A'
            subject_name = 'N/A'
            full_mark = 0
            pass_mark = 0

            if item.examID and item.examID.examID:
                exam_name = item.examID.examID.name or 'N/A'
                full_mark = item.examID.fullMarks if item.examID.fullMarks is not None else 0
                pass_mark = item.examID.passMarks if item.examID.passMarks is not None else 0

            if item.subjectID and item.subjectID.subjectID:
                subject_name = item.subjectID.subjectID.name or 'N/A'

            json_data.append([
                escape(exam_name),
                escape(subject_name),
                escape(full_mark),
                escape(pass_mark),
                escape(item.mark if item.mark is not None else 0),
                escape(item.note or ''),
                escape(item.lastEditedBy or 'N/A'),
                escape(item.lastUpdatedOn.strftime('%d-%m-%Y %I:%M %p') if item.lastUpdatedOn else 'N/A'),
            ])
        return json_data


# Events ------------------------------------------------------
@login_required
def get_event_type_list_api(request):
    try:
        current_session_id = _current_session_id(request)
        default_types = [
            ('General Announcement', 'general'),
            ('Teacher Notice', 'teacherapp'),
            ('Student Notice', 'studentapp'),
            ('Management Circular', 'managementapp'),
            ('All Apps Broadcast', 'all_apps'),
        ]

        existing_pairs = set(
            EventType.objects.filter(
                isDeleted=False,
                sessionID_id=current_session_id,
                name__in=[name for name, _ in default_types],
                audience__in=[aud for _, aud in default_types],
            ).values_list('name', 'audience')
        )
        for type_name, audience in default_types:
            if (type_name, audience) not in existing_pairs:
                obj = EventType(name=type_name, audience=audience)
                pre_save_with_user.send(sender=EventType, instance=obj, user=request.user.pk)
                obj.save()

        objs = EventType.objects.filter(
            isDeleted=False,
            sessionID_id=current_session_id
        ).order_by('name')
        data = [{
            'ID': obj.id,
            'Name': obj.name,
            'Audience': obj.audience,
            'AudienceLabel': obj.get_audience_display(),
        } for obj in objs]
        return SuccessResponse('Event type list fetched successfully.', data=data, extra={'color': 'success'}).to_json_response()
    except Exception as e:
        logger.error(f"Error fetching event types: {e}")
        return ErrorResponse('Error in fetching event type list.', extra={'color': 'red'}).to_json_response()


class EventTypeListJson(BaseDatatableView):
    order_columns = ['name', 'audience', 'description', 'lastEditedBy', 'lastUpdatedOn']

    def get_initial_queryset(self):
        return EventType.objects.filter(
            isDeleted=False,
            sessionID_id=self.request.session['current_session']['Id'],
        )

    def filter_queryset(self, qs):
        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(name__icontains=search)
                | Q(audience__icontains=search)
                | Q(description__icontains=search)
                | Q(lastEditedBy__icontains=search)
                | Q(lastUpdatedOn__icontains=search)
            )
        return qs

    def prepare_results(self, qs):
        json_data = []
        for item in qs:
            action = '''
              <button data-inverted="" data-tooltip="Edit Detail" data-position="left center" data-variation="mini" style="font-size:10px;" onclick = "GetTypeDataDetails('{}')" class="ui circular facebook icon button green">
                <i class="pen icon"></i>
              </button>
              <button data-inverted="" data-tooltip="Delete" data-position="left center" data-variation="mini" style="font-size:10px;" onclick ="delTypeData('{}')" class="ui circular youtube icon button" style="margin-left: 3px">
                <i class="trash alternate icon"></i>
              </button></td>'''.format(item.pk, item.pk)
            json_data.append([
                escape(item.name or 'N/A'),
                escape(item.get_audience_display()),
                escape(item.description or ''),
                escape(item.lastEditedBy or 'N/A'),
                escape(item.lastUpdatedOn.strftime('%d-%m-%Y %I:%M %p') if item.lastUpdatedOn else 'N/A'),
                action,
            ])
        return json_data


@transaction.atomic
@csrf_exempt
@login_required
def add_event_type_api(request):
    if request.method != 'POST':
        return ErrorResponse('Invalid request method.', extra={'color': 'red'}).to_json_response()

    try:
        post_data = request.POST.dict()
        name = (post_data.get('type_name') or '').strip()
        audience = (post_data.get('type_audience') or '').strip()
        description = (post_data.get('type_description') or '').strip()

        valid_audiences = {choice[0] for choice in EventType.AUDIENCE_CHOICES}
        if not name or not audience:
            return ErrorResponse('Name and audience are required.', extra={'color': 'red'}).to_json_response()
        if audience not in valid_audiences:
            return ErrorResponse('Invalid audience selected.', extra={'color': 'red'}).to_json_response()

        exists = EventType.objects.filter(
            isDeleted=False,
            sessionID_id=request.session['current_session']['Id'],
            name__iexact=name,
            audience=audience,
        ).exists()
        if exists:
            return ErrorResponse('Event type already exists for selected audience.', extra={'color': 'orange'}).to_json_response()

        obj = EventType(name=name, audience=audience, description=description)
        pre_save_with_user.send(sender=EventType, instance=obj, user=request.user.pk)

        return SuccessResponse('Event type added successfully.', extra={'color': 'success'}).to_json_response()
    except Exception as e:
        logger.error(f'Error in add_event_type_api: {e}')
        return ErrorResponse('Failed to add event type.', extra={'color': 'red'}).to_json_response()


@login_required
def get_event_type_detail(request):
    try:
        obj = EventType.objects.get(
            pk=request.GET.get('id'),
            isDeleted=False,
            sessionID_id=request.session['current_session']['Id'],
        )
        data = {
            'ID': obj.pk,
            'name': obj.name,
            'audience': obj.audience,
            'description': obj.description or '',
        }
        return SuccessResponse('Event type detail fetched successfully.', data=data, extra={'color': 'success'}).to_json_response()
    except Exception as e:
        logger.error(f'Error in get_event_type_detail: {e}')
        return ErrorResponse('Error in fetching event type details.', extra={'color': 'red'}).to_json_response()


@transaction.atomic
@csrf_exempt
@login_required
def update_event_type_api(request):
    if request.method != 'POST':
        return ErrorResponse('Invalid request method.', extra={'color': 'red'}).to_json_response()

    try:
        post_data = request.POST.dict()
        edit_id = post_data.get('type_editID')
        name = (post_data.get('type_name') or '').strip()
        audience = (post_data.get('type_audience') or '').strip()
        description = (post_data.get('type_description') or '').strip()

        valid_audiences = {choice[0] for choice in EventType.AUDIENCE_CHOICES}
        if not edit_id or not name or not audience:
            return ErrorResponse('Name and audience are required.', extra={'color': 'red'}).to_json_response()
        if audience not in valid_audiences:
            return ErrorResponse('Invalid audience selected.', extra={'color': 'red'}).to_json_response()

        obj = EventType.objects.get(
            pk=int(edit_id),
            isDeleted=False,
            sessionID_id=request.session['current_session']['Id'],
        )

        duplicate = EventType.objects.filter(
            isDeleted=False,
            sessionID_id=request.session['current_session']['Id'],
            name__iexact=name,
            audience=audience,
        ).exclude(pk=obj.pk).exists()
        if duplicate:
            return ErrorResponse('Another event type already exists with same name and audience.', extra={'color': 'orange'}).to_json_response()

        obj.name = name
        obj.audience = audience
        obj.description = description
        pre_save_with_user.send(sender=EventType, instance=obj, user=request.user.pk)

        return SuccessResponse('Event type updated successfully.', extra={'color': 'success'}).to_json_response()
    except Exception as e:
        logger.error(f'Error in update_event_type_api: {e}')
        return ErrorResponse('Failed to update event type.', extra={'color': 'red'}).to_json_response()


@transaction.atomic
@csrf_exempt
@login_required
def delete_event_type(request):
    if request.method != 'POST':
        return ErrorResponse('Invalid request method.', extra={'color': 'red'}).to_json_response()

    try:
        obj = EventType.objects.get(
            pk=int(request.POST.get('dataID')),
            isDeleted=False,
            sessionID_id=request.session['current_session']['Id'],
        )
        obj.isDeleted = True
        pre_save_with_user.send(sender=EventType, instance=obj, user=request.user.pk)
        return SuccessResponse('Event type deleted successfully.', extra={'color': 'success'}).to_json_response()
    except Exception as e:
        logger.error(f'Error in delete_event_type: {e}')
        return ErrorResponse('Error in deleting event type.', extra={'color': 'red'}).to_json_response()


@transaction.atomic
@csrf_exempt
@login_required
def add_event_api(request):
    if request.method == 'POST':
        try:
            post_data = request.POST.dict()
            event_type_id = post_data.get("event_type")
            if not event_type_id:
                return ErrorResponse('Event type is required.', extra={'color': 'red'}).to_json_response()
            event_type_obj = EventType.objects.filter(
                id=event_type_id,
                isDeleted=False,
                sessionID_id=request.session['current_session']['Id']
            ).first()
            if not event_type_obj:
                return ErrorResponse('Invalid event type for current session.', extra={'color': 'red'}).to_json_response()
            obj = Event.objects.create(
            eventID_id = event_type_obj.id,
            title  = post_data.get("title"),
            startDate = datetime.strptime(post_data["start_date"], "%d/%m/%Y"),
            endDate = datetime.strptime(post_data["end_date"], "%d/%m/%Y"),
            message = post_data.get("description"),
            sessionID_id = request.session['current_session']['Id'],
            )
            pre_save_with_user.send(sender=Event, instance=obj, user=request.user.pk)

            
            logger.info("Event added successfully")
            return SuccessResponse('Event added successfully.', extra={'color': 'success'}).to_json_response()
        except Exception as e:
            logger.error(f"Error adding event: {str(e)}")
            return ErrorResponse('Failed to add event.', extra={'color': 'red'}).to_json_response()
    else:
        logger.error("Invalid request method")
        return ErrorResponse('Invalid request method.', extra={'color': 'red'}).to_json_response()


class EventListJson(BaseDatatableView):
    order_columns = ['eventID__name', 'eventID__audience', 'title', 'startDate',
                     'endDate', 'message', 'lastEditedBy', 'lastUpdatedOn']

    def get_initial_queryset(self):
        return Event.objects.select_related('eventID').only(
            'id', 'title', 'startDate', 'endDate', 'message', 'lastEditedBy', 'lastUpdatedOn',
            'eventID__name', 'eventID__audience'
        ).filter(
            isDeleted__exact=False,
            sessionID_id=self.request.session["current_session"]["Id"]
        )

    def filter_queryset(self, qs):

        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(eventID__name__icontains=search) | Q(eventID__audience__icontains=search) | Q(title__icontains=search) | Q(
                    startDate__icontains=search)| Q(
                    endDate__icontains=search)| Q(
                    message__icontains=search)
                |  Q(lastEditedBy__icontains=search) | Q(lastUpdatedOn__icontains=search)
            )

        return qs

    def prepare_results(self, qs):
        json_data = []
        for item in qs:
            action = '''
              <button data-inverted="" data-tooltip="Edit Detail" data-position="left center" data-variation="mini" style="font-size:10px;" onclick = "GetDataDetails('{}')" class="ui circular facebook icon button green">
                <i class="pen icon"></i>
              </button>
              <button data-inverted="" data-tooltip="Delete" data-position="left center" data-variation="mini" style="font-size:10px;" onclick ="delData('{}')" class="ui circular youtube icon button" style="margin-left: 3px">
                <i class="trash alternate icon"></i>
              </button></td>'''.format(item.pk, item.pk)
            json_data.append([
                escape(item.eventID.name if item.eventID else 'N/A'),
                escape(item.eventID.get_audience_display() if item.eventID else 'General'),
                escape(item.title),
                escape(item.startDate.strftime('%d-%m-%Y')),
                escape(item.endDate.strftime('%d-%m-%Y')),
                escape(item.message),
                escape(item.lastEditedBy or 'N/A'),
                escape(item.lastUpdatedOn.strftime('%d-%m-%Y %I:%M %p') if item.lastUpdatedOn else 'N/A'),
                action,

            ])

        return json_data


@transaction.atomic
@csrf_exempt
@login_required
def delete_event(request):
    if request.method == 'POST':
        try:
            id = request.POST.get("dataID")
            instance = Event.objects.get(pk=int(id), isDeleted=False,
                                            sessionID_id=request.session['current_session']['Id'])
            instance.isDeleted = True
            pre_save_with_user.send(sender=Event, instance=instance, user=request.user.pk)
            instance.save()
            logger.info(f"Event detail deleted successfully {request.session['current_session']['Id']} event title {instance.title}")
            return SuccessResponse("Event detail deleted successfully.").to_json_response()
        except Exception as e:
            logger.error(f"Error in deleting event: {e}")
            return ErrorResponse("Error in deleting Event details").to_json_response()
    else:
        logger.error("Method not allowed")
        return ErrorResponse("Method not allowed").to_json_response()        


@login_required
def get_event_detail(request, **kwargs):
    try:
        id = request.GET.get('id')
        obj = Event.objects.get(pk=id, isDeleted=False, sessionID_id=request.session['current_session']['Id'])
        obj_dic = {
            'ID': obj.pk,
            'eventTypeID': obj.eventID_id if obj.eventID_id else '',
            'audience': obj.eventID.audience if obj.eventID else 'general',
            'title': obj.title,
            'startDate': obj.startDate.strftime('%d/%m/%Y'),
            'endDate': obj.endDate.strftime('%d/%m/%Y'),
            'message': obj.message
        }
        logger.info(f"Event detail fetched successfully {request.session['current_session']['Id']} event title {obj.title}")
        return SuccessResponse("Event detail fetched successfully.", data=obj_dic).to_json_response()
    except Exception as e:
        logger.error(f"Error in fetching event details: {e}")
        return ErrorResponse("Error in fetching event details").to_json_response()


@transaction.atomic
@csrf_exempt
@login_required
def update_event_api(request):
    if request.method == 'POST':
        try:
            post_data = request.POST.dict()
            event_type_id = post_data.get("event_type")
            if not event_type_id:
                return ErrorResponse('Event type is required.', extra={'color': 'red'}).to_json_response()
            event_type_obj = EventType.objects.filter(
                id=event_type_id,
                isDeleted=False,
                sessionID_id=request.session['current_session']['Id']
            ).first()
            if not event_type_obj:
                return ErrorResponse('Invalid event type for current session.', extra={'color': 'red'}).to_json_response()
            obj = Event.objects.get(pk=int(post_data.get("editID")), isDeleted=False,
                                   sessionID_id=request.session['current_session']['Id'])
            obj.eventID_id = event_type_obj.id
            obj.title = post_data.get("title")
            obj.startDate = datetime.strptime(post_data["start_date"], "%d/%m/%Y")
            obj.endDate = datetime.strptime(post_data["end_date"], "%d/%m/%Y")
            obj.message = post_data.get("description")
            obj.save()
            pre_save_with_user.send(sender=Event, instance=obj, user=request.user.pk)

            logger.info("Event detail updated successfully")
            return SuccessResponse('Event detail updated successfully.', extra={'color': 'success'}).to_json_response()
        except Exception as e:
            logger.error(f"Error updating event: {str(e)}")
            return ErrorResponse('Failed to update event.', extra={'color': 'red'}).to_json_response()
    else:
        logger.error("Invalid request method")
        return ErrorResponse('Invalid request method.', extra={'color': 'red'}).to_json_response()

# Parents API --------------
class ParentsListJson(BaseDatatableView):
    order_columns = ['fatherName', 'fatherPhone',
                     'motherName', 'motherPhone', 
                     'guardianName', 'guardianPhone',
                     'totalFamilyMembers',
                     'lastEditedBy', 'lastUpdatedOn']

    def get_initial_queryset(self):
        current_session_id = self.request.session["current_session"]["Id"]
        return Parent.objects.only(
            'id', 'fatherName', 'fatherPhone', 'motherName', 'motherPhone',
            'guardianName', 'guardianPhone', 'totalFamilyMembers', 'lastEditedBy', 'lastUpdatedOn'
        ).prefetch_related(
            Prefetch(
                'student_set',
                queryset=Student.objects.select_related('standardID').only(
                    'id', 'name', 'roll', 'photo',
                    'standardID__name', 'standardID__section', 'standardID__hasSection'
                ).filter(isDeleted=False, sessionID_id=current_session_id),
                to_attr='active_students',
            )
        ).filter(
            isDeleted__exact=False,
            sessionID_id=current_session_id
        )

    def filter_queryset(self, qs):

        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(fatherName__icontains=search) | Q(
                    fatherPhone__icontains=search)| Q(
                    motherName__icontains=search)| Q(
                    motherPhone__icontains=search)
                |  Q(guardianName__icontains=search) | Q(guardianPhone__icontains=search)
                |  Q(totalFamilyMembers__icontains=search)
                | Q(lastEditedBy__icontains=search) | Q(lastUpdatedOn__icontains=search)
            )

        return qs

    def prepare_results(self, qs):
        json_data = []
        for item in qs:
            action = '''
              <a href="/management/parent_detail/{}/" data-inverted="" data-tooltip="View Detail" data-position="left center" data-variation="mini" style="font-size:10px;" class="ui circular facebook icon button purple">
                <i class="eye icon"></i>
              </a>
              <a href="/management/edit_parent/{}/" data-inverted="" data-tooltip="Edit Parent" data-position="left center" data-variation="mini" style="font-size:10px;" class="ui circular icon button blue">
                <i class="edit icon"></i>
              </a>'''.format(item.pk, item.pk)
            students = getattr(item, 'active_students', [])
            student_html = []
            for s in students:
                student_name = escape(s.name or 'N/A')
                roll_no = escape(str(s.roll) if s.roll is not None else 'N/A')
                class_name = escape(s.standardID.name if s.standardID else 'N/A')
                section = ''
                if s.standardID and s.standardID.hasSection == "Yes" and s.standardID.section:
                    section = f"-{escape(s.standardID.section)}"
                student_html.append(f'''
                    <div class="parent-student-chip">
                        <img src="{_safe_image_url(s.photo)}" alt="{student_name}">
                        <div class="parent-student-meta">
                            <div class="student-name">{student_name}</div>
                            <div class="student-sub">Roll: {roll_no} | Class: {class_name}{section}</div>
                        </div>
                    </div>
                    ''')
            students_markup = ''.join(student_html) if student_html else '<span class="ui grey text">N/A</span>'

            json_data.append([
                escape(item.fatherName),
                escape(item.fatherPhone if item.fatherPhone else 'N/A'),
                escape(item.motherName),
                escape(item.motherPhone if item.motherPhone else 'N/A'),
                escape(item.guardianName),
                escape(item.guardianPhone if item.guardianPhone else 'N/A'),
                escape(item.totalFamilyMembers if item.totalFamilyMembers else '1'),
                f'<div class="parent-students-wrap">{students_markup}</div>',
                escape(item.lastEditedBy or 'N/A'),
                escape(item.lastUpdatedOn.strftime('%d-%m-%Y %I:%M %p') if item.lastUpdatedOn else 'N/A'),
                action,

            ])

        return json_data
