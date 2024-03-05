from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.utils.html import escape
from django.views.decorators.csrf import csrf_exempt
from django_datatables_view.base_datatable_view import BaseDatatableView

from managementApp.models import *
from managementApp.signals import pre_save_with_user


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


# assign subjects to class

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
                                                        sessionID_id=self.request.session["current_session"]["Id"]).order_by('standardID__name')

    def filter_queryset(self, qs):

        search = self.request.GET.get('search[value]', None)
        if search:
            qs = qs.filter(
                Q(standardID__name__icontains=search)
                | Q(subjectID__name__icontains=search)| Q(standardID__section__icontains=search)
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
        obj = AssignSubjectsToClass.objects.get(pk=id, isDeleted=False, sessionID_id=request.session['current_session']['Id'])
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
