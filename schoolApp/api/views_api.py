from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.utils.html import escape
from django.views.decorators.csrf import csrf_exempt
from django_datatables_view.base_datatable_view import BaseDatatableView

from schoolApp.models import *
from schoolApp.signals import pre_save_with_user


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
                    Standard.objects.get(name__iexact=className, hasSection=hasSection, isDeleted=False, sessionID_id=request.session['currentSessionYear'])
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
                            Standard.objects.get(name__iexact=className, hasSection=hasSection, section__iexact=i[0], isDeleted=False)
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
        return Standard.objects.select_related().filter(isDeleted__exact=False, sessionID_id=self.request.session["current_session"]["Id"])

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
            action = '''<button data-inverted="" data-tooltip="Edit Detail" data-position="left center" data-variation="mini" style="font-size:10px;" onclick = "GetUserDetails('{}')" class="ui circular facebook icon button green">
                <i class="pen icon"></i>
              </button>
              <button data-inverted="" data-tooltip="Delete" data-position="left center" data-variation="mini" style="font-size:10px;" onclick ="delUser('{}')" class="ui circular youtube icon button" style="margin-left: 3px">
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
