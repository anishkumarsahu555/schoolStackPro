from datetime import datetime, timedelta

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.utils.html import escape
from django_datatables_view.base_datatable_view import BaseDatatableView

from managementApp.models import *
from studentApp.data_utils import StudentData


# Class ------------------

@login_required
def get_subjects_to_class_assign_list_for_student_in_class_api(request):
    stu_obj = StudentData(request)
    objs = AssignSubjectsToClass.objects.filter(isDeleted=False, standardID_id=stu_obj.get_student_class(),
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


class StudentAttendanceHistoryByDateRangeJson(BaseDatatableView):
    order_columns = ['attendanceDate', 'isPresent', 'isPresent', 'absentReason']

    @transaction.atomic
    def get_initial_queryset(self):
        try:
            ByStudentSubject = self.request.GET.get("ByStudentSubject")
            ByStudentStartDate = self.request.GET.get("ByStudentStartDate")
            ByStudentEndDate = self.request.GET.get("ByStudentEndDate")
            ByStudentStartDate = datetime.strptime(ByStudentStartDate, '%d/%m/%Y')
            ByStudentEndDate = datetime.strptime(ByStudentEndDate, '%d/%m/%Y')
            stu_obj = StudentData(self.request)
            if ByStudentSubject == "all":
                return StudentAttendance.objects.select_related().filter(isDeleted__exact=False, isHoliday=False,
                                                                         studentID_id=stu_obj.get_student_class(),
                                                                         attendanceDate__range=[ByStudentStartDate,
                                                                                                ByStudentEndDate + timedelta(
                                                                                                    days=1)],
                                                                         sessionID_id=
                                                                         self.request.session["current_session"][
                                                                             "Id"]).order_by('attendanceDate')
            else:
                return StudentAttendance.objects.select_related().filter(isDeleted__exact=False, isHoliday=False,
                                                                         subjectID_id=int(ByStudentSubject),
                                                                         studentID_id=stu_obj.get_student_class(),
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


class StudentFeeDetailsJson(BaseDatatableView):
    order_columns = ['month', 'isPaid', 'payDate', 'amount', 'note']

    @transaction.atomic
    def get_initial_queryset(self):
        try:
            stu_obj = StudentData(self.request)

            return StudentFee.objects.select_related().filter(isDeleted__exact=False,
                                                              studentID_id=stu_obj.get_student_id(),
                                                              standardID_id=stu_obj.get_student_class(),
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
