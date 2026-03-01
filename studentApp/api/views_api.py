from datetime import datetime, timedelta

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q, Count
from django.db.models.functions import TruncMonth
from django.http import JsonResponse as DjangoJsonResponse
from django.utils.html import escape
from django_datatables_view.base_datatable_view import BaseDatatableView

from managementApp.models import *
from studentApp.data_utils import StudentData
from utils.custom_response import SuccessResponse, ErrorResponse


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


def _parse_attendance_filters(request):
    by_subject = request.GET.get("ByStudentSubject", "all")
    start_raw = request.GET.get("ByStudentStartDate")
    end_raw = request.GET.get("ByStudentEndDate")
    if not start_raw or not end_raw:
        raise ValueError("Start date and end date are required.")

    start_date = datetime.strptime(start_raw, '%d/%m/%Y')
    end_date = datetime.strptime(end_raw, '%d/%m/%Y')
    return by_subject, start_date, end_date


def _attendance_base_queryset(request):
    stu_obj = StudentData(request)
    student_id = stu_obj.get_student_id()
    current_session_id = request.session.get('current_session', {}).get('Id')
    if not student_id or not current_session_id:
        return StudentAttendance.objects.none()
    return StudentAttendance.objects.filter(
        isDeleted=False,
        isHoliday=False,
        studentID_id=student_id,
        sessionID_id=current_session_id,
    )


# Class ------------------

@login_required
def get_subjects_to_class_assign_list_for_student_in_class_api(request):
    stu_obj = StudentData(request)
    current_session_id = request.session.get('current_session', {}).get('Id')
    student_class_id = stu_obj.get_student_class()
    if not current_session_id or not student_class_id:
        return SuccessResponse(
            "No class/subject mapping found for current session.",
            data=[],
            extra={'color': 'info'}
        ).to_json_response()

    objs = AssignSubjectsToClass.objects.filter(
        isDeleted=False,
        standardID_id=student_class_id,
        sessionID_id=current_session_id
    ).order_by('subjectID__name')
    data = []
    for obj in objs:
        data_dic = {
            'ID': obj.pk,
            'Name': obj.subjectID.name if obj.subjectID else 'N/A'

        }
        data.append(data_dic)
    return _api_response(
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
                                                                         studentID_id=stu_obj.get_student_id(),
                                                                         attendanceDate__range=[ByStudentStartDate,
                                                                                                ByStudentEndDate + timedelta(
                                                                                                    days=1)],
                                                                         sessionID_id=
                                                                         self.request.session["current_session"][
                                                                             "Id"]).order_by('attendanceDate')
            else:
                return StudentAttendance.objects.select_related().filter(isDeleted__exact=False, isHoliday=False,
                                                                         subjectID_id=int(ByStudentSubject),
                                                                         studentID_id=stu_obj.get_student_id(),
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


@login_required
def StudentAttendanceMonthWiseSummaryApi(request):
    try:
        by_subject, start_date, end_date = _parse_attendance_filters(request)
        qs = _attendance_base_queryset(request).filter(
            attendanceDate__range=[start_date, end_date + timedelta(days=1)]
        )
        if by_subject != "all":
            qs = qs.filter(subjectID_id=int(by_subject))

        rows = (
            qs.annotate(month=TruncMonth('attendanceDate'))
            .values('month')
            .annotate(
                total=Count('id'),
                present=Count('id', filter=Q(isPresent=True)),
                absent=Count('id', filter=Q(isPresent=False)),
            )
            .order_by('month')
        )

        data = []
        for row in rows:
            month_date = row.get('month')
            total = row.get('total') or 0
            present = row.get('present') or 0
            absent = row.get('absent') or 0
            percentage = round((present * 100.0 / total), 2) if total else 0
            data.append({
                'month': month_date.strftime('%B %Y') if month_date else 'N/A',
                'total': total,
                'present': present,
                'absent': absent,
                'percentage': percentage,
            })

        return SuccessResponse(
            "Month-wise attendance loaded successfully.",
            data=data,
            extra={'color': 'success'}
        ).to_json_response()
    except ValueError as exc:
        return ErrorResponse(
            str(exc),
            status_code=400,
            data=[],
            extra={'color': 'warning'}
        ).to_json_response()
    except Exception:
        return ErrorResponse(
            "Unable to load month-wise attendance.",
            status_code=500,
            data=[],
            extra={'color': 'error'}
        ).to_json_response()


@login_required
def StudentAttendanceSubjectWiseSummaryApi(request):
    try:
        by_subject, start_date, end_date = _parse_attendance_filters(request)
        qs = _attendance_base_queryset(request).filter(
            attendanceDate__range=[start_date, end_date + timedelta(days=1)]
        )
        if by_subject != "all":
            qs = qs.filter(subjectID_id=int(by_subject))

        rows = (
            qs.values('subjectID__name')
            .annotate(
                total=Count('id'),
                present=Count('id', filter=Q(isPresent=True)),
                absent=Count('id', filter=Q(isPresent=False)),
            )
            .order_by('subjectID__name')
        )

        data = []
        for row in rows:
            total = row.get('total') or 0
            present = row.get('present') or 0
            absent = row.get('absent') or 0
            percentage = round((present * 100.0 / total), 2) if total else 0
            data.append({
                'subject': row.get('subjectID__name') or 'N/A',
                'total': total,
                'present': present,
                'absent': absent,
                'percentage': percentage,
            })

        return SuccessResponse(
            "Subject-wise attendance loaded successfully.",
            data=data,
            extra={'color': 'success'}
        ).to_json_response()
    except ValueError as exc:
        return ErrorResponse(
            str(exc),
            status_code=400,
            data=[],
            extra={'color': 'warning'}
        ).to_json_response()
    except Exception:
        return ErrorResponse(
            "Unable to load subject-wise attendance.",
            status_code=500,
            data=[],
            extra={'color': 'error'}
        ).to_json_response()


class StudentFeeDetailsJson(BaseDatatableView):
    order_columns = ['month', 'isPaid', 'payDate', 'amount', 'note']

    @transaction.atomic
    def get_initial_queryset(self):
        try:
            stu_obj = StudentData(self.request)
            current_session_id = self.request.session.get("current_session", {}).get("Id")
            student_id = stu_obj.get_student_id()
            class_id = stu_obj.get_student_class()
            if not current_session_id or not student_id or not class_id:
                return StudentFee.objects.none()

            return StudentFee.objects.filter(
                isDeleted__exact=False,
                studentID_id=student_id,
                standardID_id=class_id,
                sessionID_id=current_session_id
            )
        except Exception:
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
            if item.isPaid is True:
                status = 'Paid'
                payDate = item.payDate.strftime('%d-%m-%Y') if item.payDate else 'N/A'
            else:
                status = 'Due'
                payDate = 'N/A'

            json_data.append([

                escape(item.month or 'N/A'),
                status,
                payDate,
                escape(item.amount if item.amount is not None else 0),
                escape(item.note or ''),

            ])

        return json_data
