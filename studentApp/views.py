import json
from datetime import date, datetime, timedelta

from django.db.models import Sum, Count, Q
from django.db.models.functions import TruncMonth
from django.http import HttpResponse
from django.shortcuts import render
from django.template.loader import render_to_string

from chatApp.views import inbox as chat_inbox
from homeApp.models import SchoolSession, SchoolDetail
from homeApp.session_utils import build_current_session_payload, build_session_list_item
from homeApp.utils import login_required
from libraryApp.models import LibraryMember
from libraryApp.services import build_member_card_render_context, get_or_create_active_member_card_design
from libraryApp.views import _member_card_context, _school_fallback
from hostelApp.portal_services import build_my_hostel_context
from managementApp.models import *
from managementApp.reporting import build_report_cards_for_student
from managementApp.services.id_cards import build_id_card_context
from teacherApp.models import SubjectNote
from transportApp.portal_services import build_my_transport_context
from utils.custom_decorators import check_groups
from utils.logger import logger


# Create your views here.

def _session_id_from_session_payload(payload):
    if isinstance(payload, dict):
        payload = payload.get('Id') or payload.get('id') or payload.get('SessionID')
    try:
        return int(payload) if payload not in (None, '') else None
    except (TypeError, ValueError):
        return None


def _bootstrap_student_context(request):
    student = Student.objects.select_related('sessionID', 'schoolID', 'standardID', 'parentID').filter(
        userID_id=request.user.id,
        isDeleted=False,
    ).order_by('-datetime').first()

    if student and student.sessionID and 'current_session' not in request.session:
        request.session['current_session'] = build_current_session_payload(student.sessionID)
        session_qs = SchoolSession.objects.filter(
            isDeleted=False,
            schoolID_id=student.schoolID_id,
        ).order_by('-datetime')
        request.session['session_list'] = [
            build_session_list_item(s)
            for s in session_qs
        ]

    current_session_id = _session_id_from_session_payload(request.session.get('current_session')) or (student.sessionID_id if student else None)
    if student and current_session_id and student.sessionID_id != current_session_id:
        student = Student.objects.select_related('sessionID', 'schoolID', 'standardID', 'parentID').filter(
            userID_id=request.user.id,
            isDeleted=False,
            sessionID_id=current_session_id,
        ).order_by('-datetime').first() or student

    return student, current_session_id


def _normalized_timetable_period_type(period):
    period_type = getattr(period, 'periodType', None) or ('break' if getattr(period, 'isBreak', False) else 'teaching')
    if period_type == 'morning_prayer':
        return 'morning_assembly'
    if period_type == 'afternoon_prayer':
        return 'afternoon_assembly'
    if period_type != 'teaching':
        return period_type
    normalized_name = (getattr(period, 'name', '') or '').strip().lower()
    if 'assembly' in normalized_name:
        return 'afternoon_assembly' if 'afternoon' in normalized_name else 'morning_assembly'
    if 'prayer' in normalized_name:
        return 'afternoon_assembly' if 'afternoon' in normalized_name else 'morning_assembly'
    if 'break' in normalized_name or 'lunch' in normalized_name or 'recess' in normalized_name:
        return 'break'
    return period_type


def _build_student_timetable_context(student, current_session_id):
    timetable = None
    rows = []
    days = []
    if student and current_session_id and student.standardID_id:
        timetable = SchoolTimetable.objects.select_related('standardID', 'schoolID').filter(
            isDeleted=False,
            status='published',
            sessionID_id=current_session_id,
            standardID_id=student.standardID_id,
        ).order_by('-publishedOn', '-lastUpdatedOn').first()
        if timetable:
            periods = list(SchoolTimetablePeriod.objects.filter(
                timetableID=timetable,
                isDeleted=False,
            ).order_by('displayOrder', 'id'))
            for period in periods:
                period.periodType = _normalized_timetable_period_type(period)
                period.isBreak = period.periodType != 'teaching'
            entries = SchoolTimetableEntry.objects.select_related(
                'assignedSubjectID__subjectID',
                'teacherID',
            ).filter(timetableID=timetable, isDeleted=False)
            entry_map = {f'{entry.dayOfWeek}_{entry.periodID_id}': entry for entry in entries}
            days = timetable.workingDays or []
            for period in periods:
                rows.append({
                    'period': period,
                    'cells': [entry_map.get(f'{day}_{period.id}') for day in days],
                })
    return {
        'timetable': timetable,
        'days': days,
        'rows': rows,
    }


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
def student_my_transport(request):
    student, current_session_id = _bootstrap_student_context(request)
    context = {
        'profile_missing': not bool(student and current_session_id),
        'portal_role': 'student',
    }
    if student and current_session_id:
        context.update(build_my_transport_context('student', student, current_session_id))
    else:
        context.update({
            'assignment': None,
            'recent_fee_records': [],
            'current_fee_record': None,
            'fee_summary': {'net': 0, 'paid': 0, 'due': 0},
        })
    logger.info(f'Student transport page opened user={request.user.id} student={student.id if student else None}')
    return render(request, 'studentApp/my_transport.html', context)


@login_required
@check_groups('Student')
def student_my_hostel(request):
    student, current_session_id = _bootstrap_student_context(request)
    context = {
        'profile_missing': not bool(student and current_session_id),
    }
    if student and current_session_id:
        context.update(build_my_hostel_context(student, current_session_id))
    else:
        context.update({
            'hostel_assignment': None,
            'hostel_recent_fee_records': [],
            'hostel_current_fee_record': None,
            'hostel_fee_summary': {'net': 0, 'paid': 0, 'due': 0},
        })
    logger.info(f'Student hostel page opened user={request.user.id} student={student.id if student else None}')
    return render(request, 'studentApp/my_hostel.html', context)


@login_required
@check_groups('Student')
def student_library(request):
    student, current_session_id = _bootstrap_student_context(request)
    logger.info(f'Student library page opened user={request.user.id} student={student.id if student else None}')
    return render(request, 'studentApp/library.html', {
        'profile_missing': not bool(student and current_session_id),
    })


@login_required
@check_groups('Student')
def student_library_id_card(request):
    student, current_session_id = _bootstrap_student_context(request)
    current_session = request.session.get('current_session', {}) or {}
    member = None
    if student and current_session_id:
        member = LibraryMember.objects.select_related(
            'schoolID',
            'sessionID',
            'student',
            'student__standardID',
        ).filter(
            isDeleted=False,
            isActive=True,
            memberType='student',
            student_id=student.id,
            schoolID_id=current_session.get('SchoolID') or student.schoolID_id,
            sessionID_id=current_session_id,
        ).first()
    if not member:
        logger.info(f'Student library ID card unavailable user={request.user.id} student={student.id if student else None}')
        return render(request, 'libraryApp/member_card_portal.html', {
            'base_template': 'studentApp/index.html',
            'portal_back_url': 'studentApp:student_library',
            'portal_missing_message': 'Library membership is not active. Please contact the library office.',
            'cards': [],
        })
    school = _school_fallback(SchoolDetail.objects.filter(pk=member.schoolID_id, isDeleted=False).first())
    design = get_or_create_active_member_card_design(member.schoolID_id, member.sessionID_id)
    render_context = build_member_card_render_context(
        cards=[_member_card_context(member)],
        design=design,
        school=school,
        session_id=member.sessionID_id,
    )
    logger.info(f'Student library ID card opened user={request.user.id} member={member.id}')
    return render(request, 'libraryApp/member_card_portal.html', {
        **render_context,
        'base_template': 'studentApp/index.html',
        'portal_back_url': 'studentApp:student_library',
        'portal_missing_message': '',
    })


@login_required
@check_groups('Student')
def student_school_timetable(request):
    student, current_session_id = _bootstrap_student_context(request)
    timetable_context = _build_student_timetable_context(student, current_session_id)
    logger.info(f'Student timetable opened user={request.user.id} student={student.id if student else None}')
    return render(request, 'studentApp/school_timetable.html', {
        'profile_missing': not bool(student and current_session_id),
        'student': student,
        **timetable_context,
    })


@login_required
@check_groups('Student')
def student_school_timetable_pdf(request):
    student, current_session_id = _bootstrap_student_context(request)
    if not student or not current_session_id:
        return HttpResponse('Student profile was not found for the active session.', status=404)
    timetable_context = _build_student_timetable_context(student, current_session_id)
    timetable = timetable_context.get('timetable')
    if not timetable:
        return HttpResponse('Your class timetable has not been published yet.', status=404)
    context = {
        'school': _resolve_school_from_context(student, current_session_id),
        'standard': timetable.standardID,
        'generated_on': datetime.now(),
        **timetable_context,
    }
    html = render_to_string('managementApp/timetable/timetable_pdf.html', context)
    response = HttpResponse(content_type='application/pdf')
    class_label = (timetable.standardID.name or 'class').replace(' ', '-')
    response['Content-Disposition'] = f'attachment; filename="{class_label}-timetable.pdf"'
    try:
        from xhtml2pdf import pisa
        pisa_status = pisa.CreatePDF(html, dest=response)
        if pisa_status.err:
            logger.error(f'Student timetable PDF render failed student={student.id}')
            return HttpResponse(html)
    except Exception as exc:
        logger.error(f'Student timetable PDF export error student={student.id}: {exc}')
        return HttpResponse(html)
    logger.info(f'Student timetable PDF exported student={student.id} user={request.user.id}')
    return response


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
    exam_options = []
    default_exam_id = ''
    exam_year = request.session.get('current_session', {}).get('currentSessionYear') or ''
    if student and student.standardID_id and current_session_id:
        session_obj = SchoolSession.objects.filter(pk=current_session_id, isDeleted=False).first()
        if session_obj and session_obj.sessionYear:
            exam_year = session_obj.sessionYear
        exams = list(AssignExamToClass.objects.select_related('examID', 'standardID').filter(
            isDeleted=False,
            standardID_id=student.standardID_id,
            sessionID_id=current_session_id,
        ).order_by('startDate', 'examID__name'))
        timetable_rows = list(ExamTimeTable.objects.select_related(
            'standardID', 'examID', 'subjectID'
        ).filter(
            isDeleted=False,
            standardID_id=student.standardID_id,
            sessionID_id=current_session_id,
        ).order_by('examDate', 'startTime', 'examID__name', 'subjectID__name'))

        exam_meta = {}
        for row in timetable_rows:
            if not row.examID_id:
                continue
            label = row.examID.name if row.examID else 'N/A'
            row_date = row.examDate
            if row.examID_id not in exam_meta:
                exam_meta[row.examID_id] = {'id': row.examID_id, 'label': label, 'latest_date': row_date}
            else:
                prev_date = exam_meta[row.examID_id]['latest_date']
                if row_date and (prev_date is None or row_date > prev_date):
                    exam_meta[row.examID_id]['latest_date'] = row_date
        exam_options = sorted(
            exam_meta.values(),
            key=lambda x: (x['latest_date'] is None, x['latest_date']),
            reverse=True
        )
        default_exam_id = str(exam_options[0]['id']) if exam_options else ''

    class_label = 'N/A'
    if student and student.standardID:
        class_label = student.standardID.name or 'N/A'
        if student.standardID.section:
            class_label += f' - {student.standardID.section}'

    return render(request, 'studentApp/exam/examDetails.html', {
        'exams': exams,
        'timetable_rows': timetable_rows,
        'exam_options': exam_options,
        'default_exam_id': default_exam_id,
        'school_detail': student.schoolID if student else None,
        'class_label': class_label,
        'exam_year': exam_year or 'Exam Year',
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
def student_holiday_list(request):
    _, current_session_id = _bootstrap_student_context(request)
    holidays = SchoolHoliday.objects.filter(
        isDeleted=False,
        sessionID_id=current_session_id,
        appliesTo__in=['both', 'students'],
    ).order_by('-startDate', '-datetime') if current_session_id else SchoolHoliday.objects.none()

    return render(request, 'studentApp/holiday_list.html', {
        'base_template': 'studentApp/index.html',
        'page_title': 'Holiday List',
        'app_scope': 'student',
        'holidays': holidays,
    })


@login_required
@check_groups('Student')
def student_subject_notes(request):
    student, current_session_id = _bootstrap_student_context(request)
    notes = []
    subjects = []

    if student and student.standardID_id:
        base_qs = SubjectNote.objects.select_related(
            'subjectID',
            'standardID',
            'teacherID',
        ).filter(
            isDeleted=False,
            status='published',
            schoolID_id=student.schoolID_id,
        )

        notes_qs = base_qs.none()
        session_candidates = [sid for sid in {current_session_id, student.sessionID_id} if sid]

        if session_candidates:
            notes_qs = base_qs.filter(
                sessionID_id__in=session_candidates,
                standardID_id=student.standardID_id,
            )

        # Fallback 1: allow legacy notes with missing session for the same class id.
        if not notes_qs.exists():
            notes_qs = base_qs.filter(
                standardID_id=student.standardID_id,
                sessionID__isnull=True,
            )

        # Fallback 2: if class ids differ across sessions, match by class name + section.
        if not notes_qs.exists() and student.standardID:
            class_name = (student.standardID.name or '').strip()
            class_section = (student.standardID.section or '').strip()
            class_match = Q(standardID__name__iexact=class_name)
            if class_section:
                class_match &= Q(standardID__section__iexact=class_section)
            else:
                class_match &= (Q(standardID__section__isnull=True) | Q(standardID__section=''))

            session_match = Q(sessionID__isnull=True)
            if session_candidates:
                session_match = session_match | Q(sessionID_id__in=session_candidates)

            notes_qs = base_qs.filter(class_match).filter(session_match)

        notes_qs = notes_qs.order_by('-publishedAt', '-lastUpdatedOn')
        notes = list(notes_qs)

        subject_map = {}
        for row in notes:
            if row.subjectID_id and row.subjectID and row.subjectID.name:
                subject_map[row.subjectID_id] = row.subjectID.name
        subjects = [{'id': key, 'name': value} for key, value in sorted(subject_map.items(), key=lambda item: item[1].lower())]

    class_label = 'N/A'
    if student and student.standardID:
        class_label = student.standardID.name or 'N/A'
        if student.standardID.section:
            class_label = f'{class_label} - {student.standardID.section}'

    return render(request, 'studentApp/subject_notes.html', {
        'notes': notes,
        'class_label': class_label,
        'subject_filters': subjects,
        'student_not_found': not bool(student and current_session_id),
    })


@login_required
@check_groups('Student')
def student_leave_applications(request):
    student, current_session_id = _bootstrap_student_context(request)
    if not student or not current_session_id:
        return render(request, 'studentApp/leave_applications.html', {
            'leave_rows': [],
            'pending_count': 0,
            'approved_count': 0,
            'other_count': 0,
            'student_not_found': True,
        })

    leave_rows = list(LeaveApplication.objects.select_related('leaveTypeID').filter(
        isDeleted=False,
        sessionID_id=current_session_id,
        studentID_id=student.id,
    ).order_by('-datetime'))

    pending_count = sum(1 for row in leave_rows if (row.status or '').lower() == 'pending')
    approved_count = sum(1 for row in leave_rows if (row.status or '').lower() == 'approved')
    other_count = len(leave_rows) - pending_count - approved_count

    return render(request, 'studentApp/leave_applications.html', {
        'leave_rows': leave_rows,
        'pending_count': pending_count,
        'approved_count': approved_count,
        'other_count': other_count,
        'student_not_found': False,
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

    context = build_id_card_context(student)
    return render(request, 'studentApp/id_card.html', context)


@login_required
@check_groups('Student')
def student_chat(request, room_id=None):
    _bootstrap_student_context(request)
    return chat_inbox(request, room_id=room_id)


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

    published_exam_ids = list(
        ProgressReport.objects.filter(
            isDeleted=False,
            sessionID_id=current_session_id,
            studentID_id=student.id,
            status='published',
        ).values_list('examID_id', flat=True)
    )

    if not published_exam_ids:
        return render(request, 'studentApp/marks/progressReportCards.html', {
            'student': student,
            'exam_options': [],
            'selected_exam_id': 'all',
            'report_cards': [],
            'student_not_found': False,
        })

    exam_qs = AssignExamToClass.objects.select_related('examID').filter(
        isDeleted=False,
        sessionID_id=current_session_id,
        standardID_id=student.standardID_id,
        id__in=published_exam_ids,
    )
    if selected_exam_id != 'all' and str(selected_exam_id).isdigit():
        exam_qs = exam_qs.filter(id=int(selected_exam_id))
    elif selected_exam_id != 'all':
        exam_qs = exam_qs.none()
    exam_qs = exam_qs.order_by('startDate', 'examID__name')

    exam_options = list(AssignExamToClass.objects.select_related('examID').filter(
        isDeleted=False,
        sessionID_id=current_session_id,
        standardID_id=student.standardID_id,
        id__in=published_exam_ids,
    ).order_by('examID__name').values('id', 'examID__name'))

    report_cards = build_report_cards_for_student(
        current_session_id=current_session_id,
        student_obj=student,
        standard_id=student.standardID_id,
        exam_queryset=exam_qs,
    )

    return render(request, 'studentApp/marks/progressReportCards.html', {
        'student': student,
        'exam_options': exam_options,
        'selected_exam_id': selected_exam_id,
        'report_cards': report_cards,
        'student_not_found': False,
    })
