from datetime import datetime, timedelta

from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Group
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.utils.crypto import get_random_string
from django.utils.html import escape
from django.views.decorators.csrf import csrf_exempt
from django_datatables_view.base_datatable_view import BaseDatatableView

from managementApp.models import *
from managementApp.signals import pre_save_with_user
from utils.conts import MONTHS_LIST


# Class ------------------
@transaction.atomic
@csrf_exempt
@login_required
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
                                         sessionID_id=request.session['currentSessionYear'])
                    return JsonResponse(
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
                    return JsonResponse(
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
                                                 isDeleted=False)
                            pass
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
                    return JsonResponse(
                        {'status': 'success', 'message': 'New classes created successfully.', 'color': 'success'},
                        safe=False)
                except:
                    return JsonResponse({'status': 'error'}, safe=False)
            return JsonResponse({'status': 'error'}, safe=False)
        except:
            return JsonResponse({'status': 'error'}, safe=False)


class StandardListJson(BaseDatatableView):
    order_columns = ['name', 'section', 'classTeacher', 'startingRoll', 'endingRoll', 'classLocation', 'lastEditedBy',
                     'datetime']

    def get_initial_queryset(self):
        return Standard.objects.select_related().filter(isDeleted__exact=False,
                                                        sessionID_id=self.request.session["current_session"]["Id"])

    def filter_queryset(self, qs):

        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(name__icontains=search) | Q(section__icontains=search)
                | Q(classTeacher__firstName__icontains=search) | Q(classLocation__icontains=search)
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
            if item.classTeacher:
                teacher = item.classTeacher.firstName + " " + item.classTeacher.middleName + " " + item.classTeacher.lastName
            else:
                teacher = "N/A"
            json_data.append([
                escape(item.name),
                escape(item.section),
                escape(teacher),
                escape(item.startingRoll),
                escape(item.endingRoll),
                escape(item.classLocation),
                escape(item.lastEditedBy),
                escape(item.datetime.strftime('%d-%m-%Y %I:%M %p')),
                action,

            ])

        return json_data


@login_required
def get_class_detail(request, **kwargs):
    try:
        id = request.GET.get('id')
        obj = Standard.objects.get(pk=id, isDeleted=False, sessionID_id=request.session['current_session']['Id'])
        if obj.classTeacher:
            teacher = obj.classTeacher.firstName + " " + obj.classTeacher.middleName + " " + obj.classTeacher.lastName
            teacherID = obj.classTeacher.pk
        else:
            teacher = "N/A"
            teacherID = "N/A"
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
        return JsonResponse({'status': 'success', 'data': obj_dic}, safe=False)
    except:
        return JsonResponse({'status': 'error'}, safe=False)


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
            return JsonResponse(
                {'status': 'success', 'message': 'Class detail deleted successfully.',
                 'color': 'success'}, safe=False)
        except:
            return JsonResponse({'status': 'error'}, safe=False)
    return JsonResponse({'status': 'error'}, safe=False)


@login_required
def get_standard_list_api(request):
    objs = Standard.objects.filter(isDeleted=False, sessionID_id=request.session['current_session']['Id']).order_by(
        'name')
    data = []
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
    return JsonResponse(
        {'status': 'success', 'data': data,
         'color': 'success'}, safe=False)


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
            return JsonResponse(
                {'status': 'success', 'message': 'Subject already exists. Please change the name.', 'color': 'info'},
                safe=False)
        except:
            instance = Subjects()
            instance.name = subject_name
            pre_save_with_user.send(sender=Subjects, instance=instance, user=request.user.pk)
            instance.save()
            return JsonResponse(
                {'status': 'success', 'message': 'New subject created successfully.', 'color': 'success'},
                safe=False)
    return JsonResponse({'status': 'error'}, safe=False)


class SubjectListJson(BaseDatatableView):
    order_columns = ['name', 'lastEditedBy', 'datetime']

    def get_initial_queryset(self):
        return Subjects.objects.select_related().filter(isDeleted__exact=False,
                                                        sessionID_id=self.request.session["current_session"]["Id"])

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
                escape(item.lastEditedBy),
                escape(item.datetime.strftime('%d-%m-%Y %I:%M %p')),
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
            instance.save()
            return JsonResponse(
                {'status': 'success', 'message': 'Subject detail deleted successfully.',
                 'color': 'success'}, safe=False)
        except:
            return JsonResponse({'status': 'error'}, safe=False)
    return JsonResponse({'status': 'error'}, safe=False)


@login_required
def get_subject_detail(request, **kwargs):
    try:
        id = request.GET.get('id')
        obj = Subjects.objects.get(pk=id, isDeleted=False, sessionID_id=request.session['current_session']['Id'])
        obj_dic = {
            'ID': obj.pk,
            'SubjectName': obj.name,
        }
        return JsonResponse({'status': 'success', 'data': obj_dic}, safe=False)
    except:
        return JsonResponse({'status': 'error'}, safe=False)


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
                return JsonResponse(
                    {'status': 'success', 'message': 'Subject already exists. Please change the name.',
                     'color': 'info'},
                    safe=False)
            else:
                # instance = Subjects.objects.get(pk=int(editID))
                instance.name = subject_name
                pre_save_with_user.send(sender=Subjects, instance=instance, user=request.user.pk)
                instance.save()
                return JsonResponse(
                    {'status': 'success', 'message': 'Subject name updated successfully.', 'color': 'success'},
                    safe=False)
        except:

            return JsonResponse({'status': 'error'}, safe=False)


@login_required
def get_subjects_list_api(request):
    objs = Subjects.objects.filter(isDeleted=False, sessionID_id=request.session['current_session']['Id']).order_by(
        'name')
    data = []
    for obj in objs:
        data_dic = {
            'ID': obj.pk,
            'Name': obj.name

        }
        data.append(data_dic)
    return JsonResponse(
        {'status': 'success', 'data': data, 'color': 'success'}, safe=False)


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
            return JsonResponse(
                {'status': 'success', 'message': 'New subject created successfully.', 'color': 'success'},
                safe=False)
        except:
            return JsonResponse({'status': 'error'}, safe=False)


class AssignSubjectToClassListJson(BaseDatatableView):
    order_columns = ['standardID.name', 'subjectID.name', 'lastEditedBy', 'datetime']

    def get_initial_queryset(self):
        return AssignSubjectsToClass.objects.select_related().filter(isDeleted__exact=False,
                                                                     sessionID_id=
                                                                     self.request.session["current_session"][
                                                                         "Id"]).order_by('standardID__name')

    def filter_queryset(self, qs):

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
                escape(item.lastEditedBy),
                escape(item.datetime.strftime('%d-%m-%Y %I:%M %p')),
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
            return JsonResponse(
                {'status': 'success', 'message': 'Assigned Subject detail deleted successfully.',
                 'color': 'success'}, safe=False)
        except:
            return JsonResponse({'status': 'error'}, safe=False)
    return JsonResponse({'status': 'error'}, safe=False)


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
        return JsonResponse({'status': 'success', 'data': obj_dic}, safe=False)
    except:
        return JsonResponse({'status': 'error'}, safe=False)


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
            return JsonResponse(
                {'status': 'success', 'message': 'Detail updated successfully.', 'color': 'success'},
                safe=False)
        except:
            return JsonResponse({'status': 'error'}, safe=False)


@login_required
def get_subjects_to_class_assign_list_api(request):
    objs = AssignSubjectsToClass.objects.filter(isDeleted=False,
                                                sessionID_id=request.session['current_session']['Id']).order_by(
        'standardID__name')
    data = []
    for obj in objs:
        if obj.standardID.section:
            name = obj.standardID.name + ' - ' + obj.standardID.section + ' - ' + obj.subjectID.name
        else:
            name = obj.standardID.name + ' - ' + obj.subjectID.name
        data_dic = {
            'ID': obj.pk,
            'Name': name

        }
        data.append(data_dic)
    return JsonResponse(
        {'status': 'success', 'data': data,
         'color': 'success'}, safe=False)


@login_required
def get_subjects_to_class_assign_list_with_given_class_api(request):
    standard = request.GET.get('standard')
    objs = AssignSubjectsToClass.objects.filter(isDeleted=False, standardID_id=int(standard),
                                                sessionID_id=request.session['current_session']['Id']).order_by(
        'standardID__name')
    data = []
    for obj in objs:
        data_dic = {
            'ID': obj.pk,
            'Name': obj.subjectID.name

        }
        data.append(data_dic)
    return JsonResponse(
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
                return JsonResponse(
                    {'status': 'success', 'message': 'Subject already assigned successfully.', 'color': 'info'},
                    safe=False)
            except:
                instance = AssignSubjectsToTeacher()
                instance.teacherID_id = int(teachers)
                instance.assignedSubjectID_id = int(standard)
                instance.subjectBranch = branch
                pre_save_with_user.send(sender=AssignSubjectsToTeacher, instance=instance, user=request.user.pk)
                instance.save()
            return JsonResponse(
                {'status': 'success', 'message': 'Subject assigned successfully.', 'color': 'success'},
                safe=False)
        except:
            return JsonResponse({'status': 'error'}, safe=False)


class AssignSubjectToTeacherListJson(BaseDatatableView):
    order_columns = ['assignedSubjectID.standardID.name', 'assignedSubjectID.standardID.section',
                     'assignedSubjectID.subjectID.name', 'teacherID.name', 'subjectBranch', 'lastEditedBy', 'datetime']

    def get_initial_queryset(self):
        return AssignSubjectsToTeacher.objects.select_related().filter(isDeleted__exact=False,
                                                                       sessionID_id=
                                                                       self.request.session["current_session"][
                                                                           "Id"]).order_by(
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
                escape(item.lastEditedBy),
                escape(item.datetime.strftime('%d-%m-%Y %I:%M %p')),
                action,

            ])

        return json_data


class AssignSubjectToClassListJson(BaseDatatableView):
    order_columns = ['standardID.name', 'subjectID.name', 'lastEditedBy', 'datetime']

    def get_initial_queryset(self):
        return AssignSubjectsToClass.objects.select_related().filter(isDeleted__exact=False,
                                                                     sessionID_id=
                                                                     self.request.session["current_session"][
                                                                         "Id"]).order_by('standardID__name')

    def filter_queryset(self, qs):

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
                escape(item.lastEditedBy),
                escape(item.datetime.strftime('%d-%m-%Y %I:%M %p')),
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
            return JsonResponse(
                {'status': 'success', 'message': 'Assigned Teacher detail deleted successfully.',
                 'color': 'success'}, safe=False)
        except:
            return JsonResponse({'status': 'error'}, safe=False)
    return JsonResponse({'status': 'error'}, safe=False)


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
        return JsonResponse({'status': 'success', 'data': obj_dic}, safe=False)
    except:
        return JsonResponse({'status': 'error'}, safe=False)


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
                return JsonResponse(
                    {'status': 'success', 'message': 'Detail already assigned.', 'color': 'info'},
                    safe=False)
            except:
                # instance = AssignSubjectsToClass()
                instance.standardID_id = int(standard)
                instance.teacherID_id = int(teachers)
                instance.subjectBranch = branch
                pre_save_with_user.send(sender=AssignSubjectsToTeacher, instance=instance, user=request.user.pk)
                instance.save()
                return JsonResponse(
                    {'status': 'success', 'message': 'Detail updated successfully.', 'color': 'success'},
                    safe=False)
        except:
            return JsonResponse({'status': 'error'}, safe=False)


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
            return JsonResponse(
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
            instance.photo = imageUpload
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
            while User.objects.select_related().filter(username__exact=username).count() > 0:
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

                try:
                    g = Group.objects.get(name="Staff")
                    g.user_set.add(new_user.pk)
                    g.save()

                except:
                    g = Group()
                    g.name = "Staff"
                    g.save()
                    g.user_set.add(new_user.pk)
                    g.save()
            pre_save_with_user.send(sender=TeacherDetail, instance=instance, user=request.user.pk)
            instance.save()
            return JsonResponse(
                {'status': 'success', 'message': 'New Teacher added successfully.', 'color': 'success'},
                safe=False)
    return JsonResponse({'status': 'error'}, safe=False)


class TeacherListJson(BaseDatatableView):
    order_columns = ['photo', 'name', 'email', 'phoneNumber', 'employeeCode', 'gender', 'staffType', 'presentCity',
                     'isActive', 'lastEditedBy', 'datetime']

    def get_initial_queryset(self):
        return TeacherDetail.objects.select_related().filter(isDeleted__exact=False,
                                                             sessionID_id=self.request.session["current_session"]["Id"])

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
            images = '<img class="ui avatar image" src="{}">'.format(item.photo.thumbnail.url)

            action = '''<button data-inverted="" data-tooltip="View Detail" data-position="left center" data-variation="mini" style="font-size:10px;" onclick = "GetDataDetails('{}')" class="ui circular facebook icon button purple">
                <i class="eye icon"></i>
              </button>
            <button data-inverted="" data-tooltip="Edit Detail" data-position="left center" data-variation="mini" style="font-size:10px;" onclick = "GetDataDetails('{}')" class="ui circular facebook icon button green">
                <i class="pen icon"></i>
              </button>
              <button data-inverted="" data-tooltip="Delete" data-position="left center" data-variation="mini" style="font-size:10px;" onclick ="delData('{}')" class="ui circular youtube icon button" style="margin-left: 3px">
                <i class="trash alternate icon"></i>
              </button></td>'''.format(item.pk, item.pk, item.pk),

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
                escape(item.lastEditedBy),
                escape(item.datetime.strftime('%d-%m-%Y %I:%M %p')),
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
            return JsonResponse(
                {'status': 'success', 'message': 'Teacher/Staff detail deleted successfully.',
                 'color': 'success'}, safe=False)
        except:
            return JsonResponse({'status': 'error'}, safe=False)
    return JsonResponse({'status': 'error'}, safe=False)


@login_required
def get_teacher_list_api(request):
    objs = TeacherDetail.objects.filter(isDeleted=False, sessionID_id=request.session['current_session']['Id'],
                                        isActive='Yes').order_by(
        'name')
    data = []
    for obj in objs:
        name = obj.name + ' - ' + obj.employeeCode

        data_dic = {
            'ID': obj.pk,
            'Name': name

        }
        data.append(data_dic)
    return JsonResponse(
        {'status': 'success', 'data': data,
         'color': 'success'}, safe=False)


# student api --------------------------------------------------------------------------
@transaction.atomic
@csrf_exempt
@login_required
def add_student_api(request):
    if request.method == 'POST':
        name = request.POST.get("name")
        email = request.POST.get("email")
        bloodGroup = request.POST.get("bloodGroup")
        gender = request.POST.get("gender")
        phone = request.POST.get("phone")
        dob = request.POST.get("dob")
        aadhar = request.POST.get("aadhar")
        fname = request.POST.get("fname")
        mname = request.POST.get("mname")
        contactNumber = request.POST.get("contactNumber")
        cEmail = request.POST.get("cEmail")
        occupation = request.POST.get("occupation")
        registrationCode = request.POST.get("registrationCode")
        standard = request.POST.get("standard")
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
        roll = request.POST.get("roll")
        tuitionFee = request.POST.get("tuitionFee")
        admissionFee = request.POST.get("admissionFee")
        doj = request.POST.get("doj")
        parent_id = ''

        try:
            parent_obj = Parent.objects.get(phoneNumber__icontains=contactNumber, isDeleted=False)
            parent_id = parent_obj.pk


        except:
            parent_obj = Parent()
            parent_obj.fatherName = fname
            parent_obj.motherName = mname
            parent_obj.phoneNumber = contactNumber
            parent_obj.email = cEmail
            parent_obj.profession = occupation
            pre_save_with_user.send(sender=Parent, instance=parent_obj, user=request.user.pk)
            parent_obj.save()
            parent_id = parent_obj.pk

        try:
            Student.objects.get(registrationCode__iexact=registrationCode, isDeleted=False,
                                sessionID_id=request.session['current_session']['Id'])
            return JsonResponse(
                {'status': 'success', 'message': 'Student already exists. Please change the name.', 'color': 'info'},
                safe=False)

        except:
            instance = Student()
            instance.parentID_id = parent_id
            instance.name = name
            instance.email = email
            instance.bloodGroup = bloodGroup
            instance.gender = gender
            instance.dob = datetime.strptime(dob, '%d/%m/%Y')
            instance.dateOfJoining = datetime.strptime(doj, '%d/%m/%Y')
            instance.phoneNumber = phone
            instance.aadhar = aadhar
            instance.photo = imageUpload
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
            instance.registrationCode = registrationCode
            instance.standardID_id = int(standard)
            try:
                instance.roll = float(roll)

            except:
                pass

            try:
                instance.tuitionFee = float(tuitionFee)
            except:
                pass

            try:
                instance.admissionFee = float(admissionFee)
            except:
                pass

            username = 'STU' + get_random_string(length=5, allowed_chars='1234567890')
            password = get_random_string(length=8, allowed_chars='1234567890')
            while User.objects.select_related().filter(username__exact=username).count() > 0:
                username = 'STU' + get_random_string(length=5, allowed_chars='1234567890')
            else:
                new_user = User()
                new_user.username = username
                new_user.set_password(password)

                new_user.save()
                instance.username = username
                instance.password = password
                instance.userID_id = new_user.pk

                instance.save()

                try:
                    g = Group.objects.get(name="Student")
                    g.user_set.add(new_user.pk)
                    g.save()

                except:
                    g = Group()
                    g.name = "Student"
                    g.save()
                    g.user_set.add(new_user.pk)
                    g.save()
            pre_save_with_user.send(sender=Student, instance=instance, user=request.user.pk)
            instance.save()
            return JsonResponse(
                {'status': 'success', 'message': 'New Student added successfully.', 'color': 'success'},
                safe=False)
    return JsonResponse({'status': 'error'}, safe=False)


@login_required
def get_student_list_by_class_api(request):
    standard = request.GET.get('standard')
    objs = Student.objects.filter(isDeleted=False, sessionID_id=request.session['current_session']['Id'],
                                  standardID_id=int(standard)).order_by(
        'roll')
    data = []
    for obj in objs:
        name = obj.name + ' - ' + str(int(float(obj.roll)))

        data_dic = {
            'ID': obj.pk,
            'Name': name

        }
        data.append(data_dic)
    return JsonResponse(
        {'status': 'success', 'data': data,
         'color': 'success'}, safe=False)


class StudentListJson(BaseDatatableView):
    order_columns = ['photo', 'name', 'standardID.name', 'gender', 'parentID.fatherName',
                     'parentID.phoneNumber', 'presentCity',
                     'isActive', 'lastEditedBy', 'datetime']

    def get_initial_queryset(self):
        return Student.objects.select_related().filter(isDeleted__exact=False,
                                                       sessionID_id=self.request.session["current_session"]["Id"])

    def filter_queryset(self, qs):

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
            images = '<img class="ui avatar image" src="{}">'.format(item.photo.thumbnail.url)

            action = '''<button data-inverted="" data-tooltip="View Detail" data-position="left center" data-variation="mini" style="font-size:10px;" onclick = "GetDataDetails('{}')" class="ui circular facebook icon button purple">
                <i class="eye icon"></i>
              </button>
            <button data-inverted="" data-tooltip="Edit Detail" data-position="left center" data-variation="mini" style="font-size:10px;" onclick = "GetDataDetails('{}')" class="ui circular facebook icon button green">
                <i class="pen icon"></i>
              </button>
              <button data-inverted="" data-tooltip="Delete" data-position="left center" data-variation="mini" style="font-size:10px;" onclick ="delData('{}')" class="ui circular youtube icon button" style="margin-left: 3px">
                <i class="trash alternate icon"></i>
              </button></td>'''.format(item.pk, item.pk, item.pk),
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
                escape(item.lastEditedBy),
                escape(item.datetime.strftime('%d-%m-%Y %I:%M %p')),
                action,

            ])

        return json_data


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
            return JsonResponse(
                {'status': 'success', 'message': 'Student detail deleted successfully.',
                 'color': 'success'}, safe=False)
        except:
            return JsonResponse({'status': 'error'}, safe=False)
    return JsonResponse({'status': 'error'}, safe=False)


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
            return JsonResponse(
                {'status': 'success', 'message': 'Exam already exists. Please change the name.', 'color': 'info'},
                safe=False)
        except:
            instance = Exam()
            instance.name = exam
            pre_save_with_user.send(sender=Exam, instance=instance, user=request.user.pk)
            instance.save()
            return JsonResponse(
                {'status': 'success', 'message': 'New Exam created successfully.', 'color': 'success'},
                safe=False)
    return JsonResponse({'status': 'error'}, safe=False)


class ExamListJson(BaseDatatableView):
    order_columns = ['name', 'lastEditedBy', 'datetime']

    def get_initial_queryset(self):
        return Exam.objects.select_related().filter(isDeleted__exact=False,
                                                    sessionID_id=self.request.session["current_session"]["Id"])

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
                escape(item.lastEditedBy),
                escape(item.datetime.strftime('%d-%m-%Y %I:%M %p')),
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
            return JsonResponse(
                {'status': 'success', 'message': 'Exam detail deleted successfully.',
                 'color': 'success'}, safe=False)
        except:
            return JsonResponse({'status': 'error'}, safe=False)
    return JsonResponse({'status': 'error'}, safe=False)


@login_required
def get_exam_detail(request, **kwargs):
    try:
        id = request.GET.get('id')
        obj = Exam.objects.get(pk=id, isDeleted=False, sessionID_id=request.session['current_session']['Id'])
        obj_dic = {
            'ID': obj.pk,
            'ExamName': obj.name,
        }
        return JsonResponse({'status': 'success', 'data': obj_dic}, safe=False)
    except:
        return JsonResponse({'status': 'error'}, safe=False)


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
                return JsonResponse(
                    {'status': 'success', 'message': 'Exam already exists. Please change the name.',
                     'color': 'info'},
                    safe=False)
            else:
                # instance = Subjects.objects.get(pk=int(editID))
                instance.name = exam
                pre_save_with_user.send(sender=Exam, instance=instance, user=request.user.pk)
                instance.save()
                return JsonResponse(
                    {'status': 'success', 'message': 'Exam name updated successfully.', 'color': 'success'},
                    safe=False)
        except:

            return JsonResponse({'status': 'error'}, safe=False)


@login_required
def get_exams_list_api(request):
    objs = Exam.objects.filter(isDeleted=False, sessionID_id=request.session['current_session']['Id']).order_by(
        'name')
    data = []
    for obj in objs:
        data_dic = {
            'ID': obj.pk,
            'Name': obj.name

        }
        data.append(data_dic)
    return JsonResponse(
        {'status': 'success', 'data': data, 'color': 'success'}, safe=False)


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
            return JsonResponse(
                {'status': 'success', 'message': 'Exam assigned successfully.', 'color': 'success'},
                safe=False)
        except:
            return JsonResponse({'status': 'error'}, safe=False)


class AssignExamToClassListJson(BaseDatatableView):
    order_columns = ['standardID.name', 'standardID.section',
                     'examID.name', 'fullMarks', 'passMarks', 'startDate', 'endDate', 'lastEditedBy', 'datetime']

    def get_initial_queryset(self):
        return AssignExamToClass.objects.select_related().filter(isDeleted__exact=False,
                                                                 sessionID_id=
                                                                 self.request.session["current_session"][
                                                                     "Id"]).order_by(
            'standardID__name')

    def filter_queryset(self, qs):

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
                escape(item.lastEditedBy),
                escape(item.datetime.strftime('%d-%m-%Y %I:%M %p')),
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
            return JsonResponse(
                {'status': 'success', 'message': 'Assigned Exam detail deleted successfully.',
                 'color': 'success'}, safe=False)
        except:
            return JsonResponse({'status': 'error'}, safe=False)
    return JsonResponse({'status': 'error'}, safe=False)


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
        return JsonResponse({'status': 'success', 'data': obj_dic}, safe=False)
    except:
        return JsonResponse({'status': 'error'}, safe=False)


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
                    return JsonResponse(
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
            return JsonResponse(
                {'status': 'success', 'message': 'Detail updated successfully.', 'color': 'success'},
                safe=False)
        except:
            return JsonResponse({'status': 'error'}, safe=False)


# Attendance ------------------------------------------------------------------

class TakeStudentAttendanceByClassJson(BaseDatatableView):
    order_columns = ['studentID.photo', 'studentID.name', 'studentID.roll', 'isPresent', 'absentReason']

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
                for s in students:
                    try:
                        StudentAttendance.objects.get(studentID_id=s.id, attendanceDate__icontains=aDate,
                                                      standardID_id=int(standard), bySubject=False,
                                                      sessionID_id=self.request.session["current_session"]["Id"])

                    except:
                        instance = StudentAttendance.objects.create(studentID_id=s.id, attendanceDate=aDate,
                                                                    standardID_id=int(standard), isPresent=False,
                                                                    bySubject=False, )
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
                    for s in students:
                        try:
                            StudentAttendance.objects.get(studentID_id=s.id, attendanceDate__icontains=sDate,
                                                          standardID_id=obj.standardID_id, bySubject=True,
                                                          subjectID_id=obj.subjectID_id,
                                                          sessionID_id=self.request.session["current_session"]["Id"])
                        except:
                            instance = StudentAttendance.objects.create(studentID_id=s.id, attendanceDate=sDate,
                                                                        subjectID_id=obj.subjectID_id,
                                                                        standardID_id=obj.standardID_id,
                                                                        isPresent=False, bySubject=True)
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
            images = '<img class="ui avatar image" src="{}">'.format(item.studentID.photo.thumbnail.url)

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
                float(escape(item.studentID.roll)),
                is_present,
                reason,
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
            instance = StudentAttendance.objects.get(pk=int(id))
            if isPresent == 'true':
                isPresent = True
            else:
                isPresent = False
            instance.isPresent = isPresent
            instance.absentReason = reason
            pre_save_with_user.send(sender=StudentAttendance, instance=instance, user=request.user.pk)
            instance.save()
            return JsonResponse(
                {'status': 'success', 'message': 'Attendance added successfully.', 'color': 'success'},
                safe=False)
        except:

            return JsonResponse({'status': 'error'}, safe=False)


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
            images = '<img class="ui avatar image" src="{}">'.format(item.photo.thumbnail.url)
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

            json_data.append([
                images,
                escape(item.name),
                float(escape(item.roll)),
                present_count,
                absent_count,
                present_count + absent_count,
                round(percentage, 2)

            ])

        return json_data


class StudentAttendanceHistoryByDateRangeAndStudentJson(BaseDatatableView):
    order_columns = ['attendanceDate', 'isPresent', 'isPresent', 'absentReason']

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

            ])

        return json_data


class TakeTeacherAttendanceJson(BaseDatatableView):
    order_columns = ['teacherID.photo', 'teacherID.name', 'teacherID.staffType', 'teacherID.employeeCode', 'isPresent',
                     'absentReason']

    @transaction.atomic
    def get_initial_queryset(self):
        try:
            aDate = self.request.GET.get("aDate")
            aDate = datetime.strptime(aDate, '%d/%m/%Y')
            teachers = TeacherDetail.objects.select_related().filter(isDeleted__exact=False,
                                                                     sessionID_id=
                                                                     self.request.session["current_session"][
                                                                         "Id"])
            for s in teachers:
                try:
                    TeacherAttendance.objects.get(attendanceDate__icontains=aDate, isDeleted=False, teacherID_id=s.id,
                                                  sessionID_id=self.request.session["current_session"]["Id"])

                except:
                    instance = TeacherAttendance.objects.create(attendanceDate=aDate, isDeleted=False,
                                                                teacherID_id=s.id)
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
            images = '<img class="ui avatar image" src="{}">'.format(item.teacherID.photo.thumbnail.url)

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
                float(escape(item.teacherID.employeeCode)),
                is_present,
                reason,
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
            instance = TeacherAttendance.objects.get(pk=int(id))
            if isPresent == 'true':
                isPresent = True
            else:
                isPresent = False
            instance.isPresent = isPresent
            instance.absentReason = reason
            pre_save_with_user.send(sender=TeacherAttendance, instance=instance, user=request.user.pk)
            instance.save()
            return JsonResponse(
                {'status': 'success', 'message': 'Attendance added successfully.', 'color': 'success'},
                safe=False)
        except:

            return JsonResponse({'status': 'error'}, safe=False)


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
                images = '<img class="ui avatar image" src="{}">'.format(item.photo.thumbnail.url)
                present_count = TeacherAttendance.objects.filter(teacherID_id=item.id, isPresent=True,
                                                                 isHoliday=False, isDeleted=False,
                                                                 attendanceDate__range=[dateRangeStartDate,
                                                                                        dateRangeEndDate + timedelta(
                                                                                            days=1)],
                                                                 ).count()
                absent_count = TeacherAttendance.objects.filter(teacherID_id=item.id, isPresent=False,
                                                                isHoliday=False, isDeleted=False,
                                                                attendanceDate__range=[dateRangeStartDate,
                                                                                       dateRangeEndDate + timedelta(
                                                                                           days=1)]).count()

                if present_count + absent_count != 0:
                    percentage = present_count / (present_count + absent_count) * 100
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
                    present_count + absent_count,
                    round(percentage, 2)

                ])

        except:
            pass
        return json_data


class StaffAttendanceHistoryByDateRangeAndStaffJson(BaseDatatableView):
    order_columns = ['attendanceDate', 'isPresent', 'isPresent', 'absentReason']

    @transaction.atomic
    def get_initial_queryset(self):
        try:
            ByStaffStaff = self.request.GET.get("ByStaffStaff")
            ByStudentStartDate = self.request.GET.get("ByStudentStartDate")
            ByStudentEndDate = self.request.GET.get("ByStudentEndDate")
            ByStudentStartDate = datetime.strptime(ByStudentStartDate, '%d/%m/%Y')
            ByStudentEndDate = datetime.strptime(ByStudentEndDate, '%d/%m/%Y')

            return TeacherAttendance.objects.select_related().filter(isDeleted__exact=False, isHoliday=False,
                                                                     teacherID_id=int(ByStaffStaff),
                                                                     attendanceDate__range=[ByStudentStartDate,
                                                                                            ByStudentEndDate + timedelta(
                                                                                                days=1)],
                                                                     sessionID_id=
                                                                     self.request.session["current_session"][
                                                                         "Id"]).order_by('attendanceDate')


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

            ])

        return json_data


# Student fee ---------------------------------------------------------------
class FeeByStudentJson(BaseDatatableView):
    order_columns = ['month', 'isPaid', 'payDate', 'amount', 'note']

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
  <label>Mark as Present</label>
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
            return JsonResponse(
                {'status': 'success', 'message': 'Student fee added successfully.', 'color': 'success'},
                safe=False)
        except:

            return JsonResponse({'status': 'error'}, safe=False)


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

            images = '<img class="ui avatar image" src="{}">'.format(item.photo.thumbnail.url)
            json_data.append([
                images,
                escape(item.name),
                float(escape(item.roll)),
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
    order_columns = ['month', 'isPaid', 'payDate', 'amount', 'note']

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

            ])

        return json_data
