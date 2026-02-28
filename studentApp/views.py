import json
from datetime import date, timedelta

from django.db.models import Sum, Count, Q
from django.db.models.functions import TruncMonth
from django.shortcuts import render

from homeApp.models import SchoolSession
from homeApp.utils import login_required
from managementApp.models import *
from utils.custom_decorators import check_groups


# Create your views here.

def _bootstrap_student_context(request):
    student = Student.objects.select_related('sessionID', 'schoolID', 'standardID', 'parentID').filter(
        userID_id=request.user.id,
        isDeleted=False,
    ).order_by('-datetime').first()

    if student and student.sessionID and 'current_session' not in request.session:
        request.session['current_session'] = {
            'currentSessionYear': student.sessionID.sessionYear,
            'Id': student.sessionID_id,
        }
        session_qs = SchoolSession.objects.filter(
            isDeleted=False,
            schoolID_id=student.schoolID_id,
        ).order_by('-datetime')
        request.session['session_list'] = [
            {'currentSessionYear': s.sessionYear, 'Id': s.pk}
            for s in session_qs
        ]

    current_session_id = request.session.get('current_session', {}).get('Id') or (student.sessionID_id if student else None)
    if student and current_session_id and student.sessionID_id != current_session_id:
        student = Student.objects.select_related('sessionID', 'schoolID', 'standardID', 'parentID').filter(
            userID_id=request.user.id,
            isDeleted=False,
            sessionID_id=current_session_id,
        ).order_by('-datetime').first() or student

    return student, current_session_id


@login_required
@check_groups('Student')
def student_home(request):
    student, current_session_id = _bootstrap_student_context(request)
    if not student or not current_session_id:
        return render(request, 'studentApp/dashboard.html', {
            'student_not_found': True,
            'attendance_percent': 0,
            'attendance_month_label': date.today().strftime('%B %Y'),
            'attendance_month_percent': 0,
            'attendance_month_total_days': 0,
            'attendance_month_present_days': 0,
            'total_attendance_days': 0,
            'present_days': 0,
            'subjects_count': 0,
            'paid_amount': 0,
            'pending_amount': 0,
            'paid_months_count': 0,
            'total_fee_expected': 0,
            'upcoming_exams': [],
            'upcoming_events': [],
            'attendance_chart_labels': json.dumps([]),
            'attendance_chart_values': json.dumps([]),
        })

    attendance_qs = StudentAttendance.objects.filter(
        isDeleted=False,
        isHoliday=False,
        studentID_id=student.id,
        sessionID_id=current_session_id,
    )
    total_attendance_days = attendance_qs.count()
    present_days = attendance_qs.filter(isPresent=True).count()
    attendance_percent = round((present_days * 100.0 / total_attendance_days), 2) if total_attendance_days else 0
    today = date.today()
    month_attendance_qs = attendance_qs.filter(
        attendanceDate__year=today.year,
        attendanceDate__month=today.month,
    )
    attendance_month_total_days = month_attendance_qs.count()
    attendance_month_present_days = month_attendance_qs.filter(isPresent=True).count()
    attendance_month_percent = round(
        (attendance_month_present_days * 100.0 / attendance_month_total_days), 2
    ) if attendance_month_total_days else 0

    fee_qs = StudentFee.objects.filter(
        isDeleted=False,
        studentID_id=student.id,
        standardID_id=student.standardID_id,
        sessionID_id=current_session_id,
    )
    paid_amount = float(fee_qs.filter(isPaid=True).aggregate(total=Sum('amount')).get('total') or 0)
    paid_months_count = fee_qs.filter(isPaid=True).values_list('month', flat=True).distinct().count()
    total_fee_expected = float(student.totalFee or 0)
    pending_amount = max(total_fee_expected - paid_amount, 0)

    subjects_count = AssignSubjectsToClass.objects.filter(
        isDeleted=False,
        standardID_id=student.standardID_id,
        sessionID_id=current_session_id,
    ).values('subjectID_id').distinct().count()

    upcoming_exams = AssignExamToClass.objects.select_related('examID').filter(
        isDeleted=False,
        standardID_id=student.standardID_id,
        sessionID_id=current_session_id,
        startDate__gte=date.today(),
    ).order_by('startDate')[:5]

    upcoming_events = Event.objects.filter(
        isDeleted=False,
        sessionID_id=current_session_id,
        startDate__gte=date.today(),
        startDate__lte=date.today() + timedelta(days=30),
    ).order_by('startDate')[:5]

    attendance_trend_rows = list(
        attendance_qs.annotate(month=TruncMonth('attendanceDate'))
        .values('month')
        .annotate(
            total=Count('id'),
            present=Count('id', filter=Q(isPresent=True)),
        )
        .order_by('-month')[:6]
    )
    attendance_trend_rows.reverse()
    attendance_chart_labels = [
        row['month'].strftime('%b %Y') if row['month'] else 'N/A'
        for row in attendance_trend_rows
    ]
    attendance_chart_values = [
        round((row['present'] * 100.0 / row['total']), 2) if row['total'] else 0
        for row in attendance_trend_rows
    ]

    context = {
        'student': student,
        'current_session_id': current_session_id,
        'attendance_percent': attendance_percent,
        'attendance_month_label': today.strftime('%B %Y'),
        'attendance_month_percent': attendance_month_percent,
        'attendance_month_total_days': attendance_month_total_days,
        'attendance_month_present_days': attendance_month_present_days,
        'total_attendance_days': total_attendance_days,
        'present_days': present_days,
        'subjects_count': subjects_count,
        'paid_amount': paid_amount,
        'pending_amount': pending_amount,
        'paid_months_count': paid_months_count,
        'total_fee_expected': total_fee_expected,
        'upcoming_exams': upcoming_exams,
        'upcoming_events': upcoming_events,
        'attendance_chart_labels': json.dumps(attendance_chart_labels),
        'attendance_chart_values': json.dumps(attendance_chart_values),
    }
    return render(request, 'studentApp/dashboard.html', context)


@login_required
@check_groups('Student')
def attendance_history(request):
    _bootstrap_student_context(request)
    context = {
    }
    return render(request, 'studentApp/attendance/attendanceHistory.html', context)


@login_required
@check_groups('Student')
def fee_detail(request):
    _bootstrap_student_context(request)
    context = {
    }
    return render(request, 'studentApp/fee/feeDetails.html', context)


@login_required
@check_groups('Student')
def student_exam_details(request):
    student, current_session_id = _bootstrap_student_context(request)
    exams = []
    if student and student.standardID_id and current_session_id:
        exams = AssignExamToClass.objects.select_related('examID', 'standardID').filter(
            isDeleted=False,
            standardID_id=student.standardID_id,
            sessionID_id=current_session_id,
        ).order_by('startDate', 'examID__name')
    return render(request, 'studentApp/exam/examDetails.html', {
        'exams': exams,
    })


@login_required
@check_groups('Student')
def student_events(request):
    _, current_session_id = _bootstrap_student_context(request)
    events = Event.objects.filter(
        isDeleted=False,
        sessionID_id=current_session_id,
    ).order_by('-startDate', '-datetime') if current_session_id else Event.objects.none()
    return render(request, 'studentApp/events/eventsList.html', {
        'events': events,
    })


@login_required
@check_groups('Student')
def student_my_details(request):
    student, _ = _bootstrap_student_context(request)
    if not student:
        return render(request, 'studentApp/dashboard.html', {'student_not_found': True})

    context = {
        'instance': student,
        'parent': student.parentID,
        'is_teacher_view': True,
        'is_student_view': True,
        'base_template': 'studentApp/index.html',
    }
    return render(request, 'managementApp/student/student_detail.html', context)
