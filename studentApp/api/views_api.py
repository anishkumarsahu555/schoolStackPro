from datetime import datetime, timedelta

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q, Count
from django.db.models.functions import TruncMonth
from django.http import JsonResponse as DjangoJsonResponse
from django.utils.html import escape
from django_datatables_view.base_datatable_view import BaseDatatableView

from homeApp.models import SchoolSession
from homeApp.session_utils import get_session_month_sequence
from managementApp.models import *
from managementApp.signals import pre_save_with_user
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


def _compact_fee_month(month_value, year_value):
    short_month = month_value
    if month_value:
        try:
            short_month = datetime.strptime(month_value, '%B').strftime('%b')
        except ValueError:
            short_month = month_value
    if short_month and year_value:
        return f'{short_month}-{year_value}'
    return short_month or 'N/A'


def _session_month_rows(session_id):
    session_obj = SchoolSession.objects.filter(pk=session_id, isDeleted=False).first() if session_id else None
    return get_session_month_sequence(session_obj)


def _restrict_fee_queryset_to_session_months(qs, session_id):
    session_month_rows = _session_month_rows(session_id)
    if not session_month_rows:
        return qs.none()

    ym_filter = Q()
    month_name_filter = Q()
    for month_name, year_value, month_no, _, _ in session_month_rows:
        ym_filter |= Q(feeYear=year_value, feeMonth=month_no)
        month_name_filter |= Q(month__iexact=month_name)

    legacy_filter = (Q(feeYear__isnull=True) | Q(feeMonth__isnull=True)) & month_name_filter
    return qs.filter(ym_filter | legacy_filter)


def _count_approved_student_leave_days(session_id, student_id, start_date, end_date):
    leave_qs = LeaveApplication.objects.filter(
        isDeleted=False,
        sessionID_id=session_id,
        applicantRole='student',
        studentID_id=student_id,
        status='approved',
        startDate__lte=end_date,
        endDate__gte=start_date,
    ).only('startDate', 'endDate')

    days = set()
    for leave in leave_qs:
        overlap_start = max(start_date, leave.startDate)
        overlap_end = min(end_date, leave.endDate)
        if overlap_end < overlap_start:
            continue
        current = overlap_start
        while current <= overlap_end:
            days.add(current)
            current += timedelta(days=1)
    return len(days)


def _backfill_student_leave_attendance(request, *, student_id, class_id, session_id, start_date, end_date):
    leave_qs = LeaveApplication.objects.select_related('leaveTypeID').filter(
        isDeleted=False,
        sessionID_id=session_id,
        applicantRole='student',
        studentID_id=student_id,
        status='approved',
        startDate__lte=end_date,
        endDate__gte=start_date,
    )
    for leave in leave_qs:
        leave_type_name = leave.leaveTypeID.name if leave.leaveTypeID else 'Leave'
        leave_reason = f'Approved Leave: {leave_type_name}'
        day = max(start_date, leave.startDate)
        last_day = min(end_date, leave.endDate)
        while day <= last_day:
            exists = StudentAttendance.objects.filter(
                isDeleted=False,
                sessionID_id=session_id,
                studentID_id=student_id,
                bySubject=False,
                attendanceDate__date=day,
            ).exists()
            if not exists:
                attendance_dt = datetime(day.year, day.month, day.day)
                instance = StudentAttendance(
                    studentID_id=student_id,
                    standardID_id=class_id,
                    attendanceDate=attendance_dt,
                    isPresent=False,
                    bySubject=False,
                    absentReason=leave_reason,
                )
                pre_save_with_user.send(sender=StudentAttendance, instance=instance, user=request.user.pk)
            day += timedelta(days=1)


def _status_from_attendance_row(is_present, reason):
    if is_present:
        return 'present'
    if (reason or '').strip().lower().startswith('approved leave'):
        return 'leave'
    return 'absent'


def _status_priority(status):
    # Higher number wins when multiple rows exist on same date
    ranking = {'absent': 1, 'leave': 2, 'present': 3}
    return ranking.get(status, 0)


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
            student_id = stu_obj.get_student_id()
            session_id = self.request.session["current_session"]["Id"]
            class_id = stu_obj.get_student_class()
            if not student_id or not session_id:
                return StudentAttendance.objects.none()

            _backfill_student_leave_attendance(
                self.request,
                student_id=student_id,
                class_id=class_id,
                session_id=session_id,
                start_date=ByStudentStartDate.date(),
                end_date=ByStudentEndDate.date(),
            )

            base_qs = StudentAttendance.objects.select_related().filter(
                isDeleted__exact=False,
                isHoliday=False,
                studentID_id=student_id,
                attendanceDate__range=[ByStudentStartDate, ByStudentEndDate + timedelta(days=1)],
                sessionID_id=session_id,
            )
            if ByStudentSubject == "all":
                return base_qs.filter(bySubject=False).order_by('attendanceDate')
            else:
                return base_qs.filter(
                    Q(subjectID_id=int(ByStudentSubject)) | Q(absentReason__istartswith='Approved Leave')
                ).order_by('attendanceDate')


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
        # Deduplicate by attendance day to avoid inflated rows due to historical duplicate entries.
        day_map = {}
        for item in qs:
            if not item.attendanceDate:
                continue
            day_key = item.attendanceDate.date()
            status = _status_from_attendance_row(item.isPresent, item.absentReason)
            reason = item.absentReason or ''
            previous = day_map.get(day_key)
            if not previous or _status_priority(status) >= _status_priority(previous['status']):
                day_map[day_key] = {'status': status, 'reason': reason}

        json_data = []
        for day_key in sorted(day_map.keys()):
            status = day_map[day_key]['status']
            reason = day_map[day_key]['reason']
            if status == 'present':
                present, absent = 'Yes', 'No'
            elif status == 'leave':
                present, absent = 'No', 'No'
            else:
                present, absent = 'No', 'Yes'

            json_data.append([
                escape(day_key.strftime('%d-%m-%Y')),
                escape(present),
                escape(absent),
                escape(reason),
            ])

        return json_data


@login_required
def StudentAttendanceMonthWiseSummaryApi(request):
    try:
        by_subject, start_date, end_date = _parse_attendance_filters(request)
        stu_obj = StudentData(request)
        student_id = stu_obj.get_student_id()
        class_id = stu_obj.get_student_class()
        current_session_id = request.session.get('current_session', {}).get('Id')
        if not current_session_id or not student_id:
            return SuccessResponse(
                "Month-wise attendance loaded successfully.",
                data=[],
                extra={'color': 'success'}
            ).to_json_response()

        _backfill_student_leave_attendance(
            request,
            student_id=student_id,
            class_id=class_id,
            session_id=current_session_id,
            start_date=start_date.date(),
            end_date=end_date.date(),
        )

        qs = _attendance_base_queryset(request).filter(
            attendanceDate__range=[start_date, end_date + timedelta(days=1)]
        )
        if by_subject == "all":
            qs = qs.filter(bySubject=False)
        else:
            qs = qs.filter(
                Q(bySubject=True, subjectID_id=int(by_subject))
                | Q(bySubject=False, absentReason__istartswith='Approved Leave')
            )

        # Deduplicate by day before month aggregation to prevent inflated counts.
        day_map = {}
        for row in qs.values('attendanceDate', 'isPresent', 'absentReason'):
            attendance_dt = row.get('attendanceDate')
            if not attendance_dt:
                continue
            day_key = attendance_dt.date()
            status = _status_from_attendance_row(row.get('isPresent'), row.get('absentReason'))
            prev = day_map.get(day_key)
            if not prev or _status_priority(status) >= _status_priority(prev):
                day_map[day_key] = status

        month_agg = {}
        for day_key, status in day_map.items():
            month_key = (day_key.year, day_key.month)
            if month_key not in month_agg:
                month_agg[month_key] = {'present': 0, 'absent': 0, 'leave': 0}
            month_agg[month_key][status] += 1

        data = []
        for month_key in sorted(month_agg.keys()):
            year, month = month_key
            month_date = datetime(year, month, 1)
            present = month_agg[month_key]['present']
            absent = month_agg[month_key]['absent']
            leave = month_agg[month_key]['leave']
            total_working = present + absent + leave
            percentage = round((present * 100.0 / total_working), 2) if total_working else 0
            data.append({
                'month': month_date.strftime('%B %Y'),
                'total': total_working,
                'present': present,
                'absent': absent,
                'leave': leave,
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
        stu_obj = StudentData(request)
        student_id = stu_obj.get_student_id()
        class_id = stu_obj.get_student_class()
        current_session_id = request.session.get('current_session', {}).get('Id')
        if not current_session_id or not student_id:
            return SuccessResponse(
                "Subject-wise attendance loaded successfully.",
                data=[],
                extra={'color': 'success'}
            ).to_json_response()

        _backfill_student_leave_attendance(
            request,
            student_id=student_id,
            class_id=class_id,
            session_id=current_session_id,
            start_date=start_date.date(),
            end_date=end_date.date(),
        )

        qs = _attendance_base_queryset(request).filter(
            attendanceDate__range=[start_date, end_date + timedelta(days=1)]
        )
        if by_subject != "all":
            qs = qs.filter(Q(subjectID_id=int(by_subject)) | Q(absentReason__istartswith='Approved Leave'))

        rows = (
            qs.values('subjectID__name')
            .annotate(
                total=Count('id'),
                present=Count('id', filter=Q(isPresent=True)),
                leave=Count('id', filter=Q(isPresent=False, absentReason__istartswith='Approved Leave')),
                absent=Count('id', filter=Q(isPresent=False)),
            )
            .order_by('subjectID__name')
        )

        data = []
        for row in rows:
            total = row.get('total') or 0
            present = row.get('present') or 0
            leave = row.get('leave') or 0
            absent = max((row.get('absent') or 0) - leave, 0)
            total_working = present + absent + leave
            percentage = round((present * 100.0 / total_working), 2) if total_working else 0
            subject_name = row.get('subjectID__name')
            if not subject_name:
                subject_name = 'Approved Leave'
            data.append({
                'subject': subject_name,
                'total': total_working,
                'present': present,
                'absent': absent,
                'leave': leave,
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

            fee_qs = StudentFee.objects.filter(
                isDeleted__exact=False,
                studentID_id=student_id,
                standardID_id=class_id,
                sessionID_id=current_session_id
            )
            return _restrict_fee_queryset_to_session_months(fee_qs, current_session_id).order_by('feeYear', 'feeMonth', 'id')
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

                escape(_compact_fee_month(item.month, item.feeYear)),
                status,
                payDate,
                escape(item.amount if item.amount is not None else 0),
                escape(item.note or ''),

            ])

        return json_data
