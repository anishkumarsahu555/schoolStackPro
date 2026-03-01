import json
from datetime import date, timedelta

from django.db.models import Sum, Count, Q
from django.db.models.functions import TruncMonth
from django.shortcuts import render

from homeApp.models import SchoolSession, SchoolDetail
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


def _grade_from_percentage(value):
    if value is None:
        return 'N/A'
    if value >= 90:
        return 'A+'
    if value >= 80:
        return 'A'
    if value >= 70:
        return 'B+'
    if value >= 60:
        return 'B'
    if value >= 50:
        return 'C'
    if value >= 40:
        return 'D'
    return 'F'


def _resolve_school_from_context(student, current_session_id):
    school = None
    if student and student.schoolID_id:
        school = SchoolDetail.objects.filter(pk=student.schoolID_id, isDeleted=False).first()
    if not school and current_session_id:
        school = SchoolDetail.objects.filter(
            schoolsession__id=current_session_id,
            schoolsession__isDeleted=False,
            isDeleted=False,
        ).distinct().first()
    return school


@login_required
@check_groups('Student')
def student_school_detail(request):
    student, current_session_id = _bootstrap_student_context(request)
    school = _resolve_school_from_context(student, current_session_id)
    return render(request, 'studentApp/school_detail.html', {
        'school': school,
    })


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
    attendance_totals = attendance_qs.aggregate(
        total=Count('id'),
        present=Count('id', filter=Q(isPresent=True)),
    )
    total_attendance_days = attendance_totals.get('total') or 0
    present_days = attendance_totals.get('present') or 0
    attendance_percent = round((present_days * 100.0 / total_attendance_days), 2) if total_attendance_days else 0
    today = date.today()
    month_attendance_qs = attendance_qs.filter(
        attendanceDate__year=today.year,
        attendanceDate__month=today.month,
    )
    attendance_month_totals = month_attendance_qs.aggregate(
        total=Count('id'),
        present=Count('id', filter=Q(isPresent=True)),
    )
    attendance_month_total_days = attendance_month_totals.get('total') or 0
    attendance_month_present_days = attendance_month_totals.get('present') or 0
    attendance_month_percent = round(
        (attendance_month_present_days * 100.0 / attendance_month_total_days), 2
    ) if attendance_month_total_days else 0

    fee_qs = StudentFee.objects.filter(
        isDeleted=False,
        studentID_id=student.id,
        standardID_id=student.standardID_id,
        sessionID_id=current_session_id,
    )
    fee_totals = fee_qs.aggregate(
        paid_total=Sum('amount', filter=Q(isPaid=True)),
        paid_months=Count('month', filter=Q(isPaid=True), distinct=True),
    )
    paid_amount = float(fee_totals.get('paid_total') or 0)
    paid_months_count = fee_totals.get('paid_months') or 0
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
    timetable_rows = []
    if student and student.standardID_id and current_session_id:
        exams = AssignExamToClass.objects.select_related('examID', 'standardID').filter(
            isDeleted=False,
            standardID_id=student.standardID_id,
            sessionID_id=current_session_id,
        ).order_by('startDate', 'examID__name')
        timetable_rows = ExamTimeTable.objects.select_related(
            'standardID', 'examID', 'subjectID'
        ).filter(
            isDeleted=False,
            standardID_id=student.standardID_id,
            sessionID_id=current_session_id,
        ).order_by('examID__name', 'examDate', 'startTime', 'subjectID__name')
    return render(request, 'studentApp/exam/examDetails.html', {
        'exams': exams,
        'timetable_rows': timetable_rows,
    })


@login_required
@check_groups('Student')
def student_events(request):
    _, current_session_id = _bootstrap_student_context(request)
    events = Event.objects.select_related('eventID').filter(
        isDeleted=False,
        sessionID_id=current_session_id,
    ).filter(
        Q(eventID__isnull=True) | Q(eventID__audience__in=['general', 'studentapp', 'all_apps'])
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


@login_required
@check_groups('Student')
def student_id_card(request):
    student, _ = _bootstrap_student_context(request)
    if not student:
        return render(request, 'studentApp/dashboard.html', {'student_not_found': True})

    context = {
        'instance': student,
        'school': student.schoolID,
        'school_name': (
            (student.schoolID.schoolName if student.schoolID else '')
            or (student.schoolID.name if student.schoolID else '')
            or 'School Name'
        ),
        'valid_till_label': 'Upto 2026',
    }
    return render(request, 'studentApp/id_card.html', context)


@login_required
@check_groups('Student')
def student_progress_report_cards(request):
    student, current_session_id = _bootstrap_student_context(request)
    if not student or not current_session_id:
        return render(request, 'studentApp/marks/progressReportCards.html', {
            'selected_exam_id': 'all',
            'report_cards': [],
            'student_not_found': True,
        })

    selected_exam_id = request.GET.get('exam') or 'all'

    exam_qs = AssignExamToClass.objects.select_related('examID').filter(
        isDeleted=False,
        sessionID_id=current_session_id,
        standardID_id=student.standardID_id,
    )
    if selected_exam_id != 'all' and str(selected_exam_id).isdigit():
        exam_qs = exam_qs.filter(id=int(selected_exam_id))
    exam_qs = exam_qs.order_by('startDate', 'examID__name')

    exam_options = list(AssignExamToClass.objects.select_related('examID').filter(
        isDeleted=False,
        sessionID_id=current_session_id,
        standardID_id=student.standardID_id,
    ).order_by('examID__name').values('id', 'examID__name'))

    class_subjects = list(AssignSubjectsToClass.objects.select_related('subjectID').filter(
        isDeleted=False,
        sessionID_id=current_session_id,
        standardID_id=student.standardID_id,
    ).order_by('subjectID__name'))

    report_cards = []
    for exam_obj in exam_qs:
        marks_qs = MarkOfStudentsByExam.objects.select_related('subjectID', 'subjectID__subjectID').filter(
            isDeleted=False,
            sessionID_id=current_session_id,
            studentID_id=student.id,
            standardID_id=student.standardID_id,
            examID_id=exam_obj.id,
        )
        mark_map = {m.subjectID_id: m for m in marks_qs}

        subject_rows = []
        total_obtained = 0.0
        entered_marks_count = 0
        for ass_sub in class_subjects:
            mark_obj = mark_map.get(ass_sub.id)
            if mark_obj is not None:
                mark_value = float(mark_obj.mark or 0)
                total_obtained += mark_value
                entered_marks_count += 1
            else:
                mark_value = None

            subject_rows.append({
                'subject_name': ass_sub.subjectID.name if ass_sub.subjectID else 'N/A',
                'mark': mark_value,
                'note': mark_obj.note if mark_obj and mark_obj.note else '',
            })

        full_marks = float(exam_obj.fullMarks or 0)
        pass_marks = float(exam_obj.passMarks or 0)
        percentage = round((total_obtained * 100.0 / full_marks), 2) if full_marks > 0 else None
        grade = _grade_from_percentage(percentage)
        is_complete = entered_marks_count == len(class_subjects) and len(class_subjects) > 0
        if not is_complete:
            result = 'Pending'
        elif total_obtained >= pass_marks:
            result = 'Pass'
        else:
            result = 'Fail'

        report_cards.append({
            'exam_name': exam_obj.examID.name if exam_obj.examID else 'N/A',
            'exam_date': exam_obj.startDate,
            'full_marks': full_marks,
            'pass_marks': pass_marks,
            'total_obtained': round(total_obtained, 2),
            'percentage': percentage,
            'grade': grade,
            'result': result,
            'subject_rows': subject_rows,
            'entered_marks_count': entered_marks_count,
            'subject_count': len(class_subjects),
        })

    return render(request, 'studentApp/marks/progressReportCards.html', {
        'student': student,
        'exam_options': exam_options,
        'selected_exam_id': selected_exam_id,
        'report_cards': report_cards,
        'student_not_found': False,
    })
