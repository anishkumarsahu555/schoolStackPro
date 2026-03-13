from datetime import date, timedelta
import json

from django.db.models import Q, Count
from django.shortcuts import render, get_object_or_404

from homeApp.models import SchoolSession, SchoolDetail
from homeApp.session_utils import build_current_session_payload, build_session_list_item
from homeApp.utils import login_required
from managementApp.models import (
    Student,
    TeacherDetail,
    LeaveApplication,
    AssignSubjectsToTeacher,
    Event,
    Standard,
    ExamTimeTable,
    AssignExamToClass,
    AssignSubjectsToClass,
    MarkOfStudentsByExam,
)
from managementApp.reporting import build_report_cards_for_student
from teacherApp.models import SubjectNote
from utils.custom_decorators import check_groups

# Create your views here.


def _bootstrap_teacher_context(request):
    teacher = TeacherDetail.objects.select_related('sessionID', 'schoolID').filter(
        userID_id=request.user.id,
        isDeleted=False,
    ).order_by('-datetime').first()

    if teacher and teacher.sessionID and 'current_session' not in request.session:
        request.session['current_session'] = build_current_session_payload(teacher.sessionID)
        session_qs = SchoolSession.objects.filter(
            isDeleted=False,
            schoolID_id=teacher.schoolID_id,
        ).order_by('-datetime')
        request.session['session_list'] = [
            build_session_list_item(s)
            for s in session_qs
        ]

    current_session_id = request.session.get('current_session', {}).get('Id') or (teacher.sessionID_id if teacher else None)
    is_class_teacher = False
    if teacher and current_session_id:
        is_class_teacher = Standard.objects.filter(
            isDeleted=False,
            sessionID_id=current_session_id,
            classTeacher_id=teacher.id,
        ).exists()
    request.session['is_class_teacher'] = is_class_teacher
    return teacher, current_session_id, is_class_teacher


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


def _resolve_school_from_context(teacher, current_session_id):
    school = None
    if teacher and teacher.schoolID_id:
        school = SchoolDetail.objects.filter(pk=teacher.schoolID_id, isDeleted=False).first()
    if not school and current_session_id:
        school = SchoolDetail.objects.filter(
            schoolsession__id=current_session_id,
            schoolsession__isDeleted=False,
            isDeleted=False,
        ).distinct().first()
    return school


@login_required
@check_groups('Teaching')
def teacher_school_detail(request):
    teacher, current_session_id, is_class_teacher = _bootstrap_teacher_context(request)
    school = _resolve_school_from_context(teacher, current_session_id)
    return render(request, 'teacherApp/school_detail.html', {
        'school': school,
        'is_class_teacher': is_class_teacher,
    })


@login_required
@check_groups('Teaching')
def teacher_home(request):
    _, _, is_class_teacher = _bootstrap_teacher_context(request)

    teacher = TeacherDetail.objects.select_related('schoolID', 'sessionID').filter(
        userID_id=request.user.id,
        isDeleted=False,
    ).order_by('-datetime').first()

    if not teacher:
        return render(request, 'teacherApp/dashboard.html', {'missing_teacher_profile': True})

    current_session_id = request.session.get('current_session', {}).get('Id') or teacher.sessionID_id

    assignments = AssignSubjectsToTeacher.objects.select_related(
        'assignedSubjectID',
        'assignedSubjectID__standardID',
        'assignedSubjectID__subjectID',
    ).filter(
        isDeleted=False,
        teacherID_id=teacher.id,
        assignedSubjectID__isDeleted=False,
        assignedSubjectID__standardID__isDeleted=False,
        assignedSubjectID__subjectID__isDeleted=False,
        sessionID_id=current_session_id,
    )

    assignment_pairs = list(
        assignments.values_list(
            'assignedSubjectID__standardID_id',
            'assignedSubjectID__subjectID_id',
        ).distinct()
    )
    assigned_class_ids = {row[0] for row in assignment_pairs if row[0]}
    assigned_subject_ids = {row[1] for row in assignment_pairs if row[1]}

    class_count = len(assigned_class_ids)
    subject_count = len(assigned_subject_ids)
    student_count = Student.objects.filter(
        isDeleted=False,
        sessionID_id=current_session_id,
        standardID_id__in=assigned_class_ids,
    ).count() if assigned_class_ids else 0

    class_counter = {}
    gender_counter = {'Male': 0, 'Female': 0, 'Other': 0, 'Unknown': 0}
    if assigned_class_ids:
        class_rows = Student.objects.filter(
            isDeleted=False,
            sessionID_id=current_session_id,
            standardID_id__in=assigned_class_ids,
        ).values('standardID__name', 'standardID__section').annotate(total=Count('id'))

        for row in class_rows:
            class_name = row.get('standardID__name') or 'N/A'
            section = row.get('standardID__section')
            if section:
                class_name = f'{class_name} - {section}'
            class_counter[class_name] = row.get('total', 0)

        gender_rows = Student.objects.filter(
            isDeleted=False,
            sessionID_id=current_session_id,
            standardID_id__in=assigned_class_ids,
        ).values('gender').annotate(total=Count('id'))

        for row in gender_rows:
            gender_value = (row.get('gender') or '').strip().lower()
            total = row.get('total', 0)
            if gender_value in {'male', 'm'}:
                gender_counter['Male'] += total
            elif gender_value in {'female', 'f'}:
                gender_counter['Female'] += total
            elif gender_value:
                gender_counter['Other'] += total
            else:
                gender_counter['Unknown'] += total

    subject_counter = {
        row['assignedSubjectID__subjectID__name'] or 'N/A': row['total']
        for row in assignments.values('assignedSubjectID__subjectID__name').annotate(total=Count('id'))
    }

    upcoming_events = Event.objects.filter(
        isDeleted=False,
        sessionID_id=current_session_id,
        startDate__gte=date.today(),
        startDate__lte=date.today() + timedelta(days=30),
    ).order_by('startDate')[:5]

    recent_assignments = []
    recent_assignment_rows = assignments.order_by('-datetime').values(
        'assignedSubjectID__standardID__name',
        'assignedSubjectID__standardID__section',
        'assignedSubjectID__subjectID__name',
        'subjectBranch',
    )[:6]
    for row in recent_assignment_rows:
        class_name = row.get('assignedSubjectID__standardID__name') or 'N/A'
        section = row.get('assignedSubjectID__standardID__section') or ''
        if section:
            class_name = f'{class_name} - {section}'
        recent_assignments.append({
            'class_name': class_name,
            'subject_name': row.get('assignedSubjectID__subjectID__name') or 'N/A',
            'branch': row.get('subjectBranch') or 'Main',
        })

    context = {
        'missing_teacher_profile': False,
        'teacher': teacher,
        'subject_count': subject_count,
        'class_count': class_count,
        'student_count': student_count,
        'upcoming_events': upcoming_events,
        'recent_assignments': recent_assignments,
        'current_session_year': request.session.get('current_session', {}).get('currentSessionYear', 'N/A'),
        'chart_class_labels': json.dumps(list(class_counter.keys())),
        'chart_class_values': json.dumps(list(class_counter.values())),
        'chart_subject_labels': json.dumps(list(subject_counter.keys())),
        'chart_subject_values': json.dumps(list(subject_counter.values())),
        'chart_gender_labels': json.dumps(list(gender_counter.keys())),
        'chart_gender_values': json.dumps(list(gender_counter.values())),
        'is_class_teacher': is_class_teacher,
    }
    return render(request, 'teacherApp/dashboard.html', context)


@login_required
@check_groups('Teaching')
def teacher_students_list(request):
    _, _, is_class_teacher = _bootstrap_teacher_context(request)
    if 'current_session' not in request.session:
        teacher = TeacherDetail.objects.select_related('sessionID', 'schoolID').filter(
            userID_id=request.user.id,
            isDeleted=False,
        ).order_by('-datetime').first()
        if teacher and teacher.sessionID:
            request.session['current_session'] = build_current_session_payload(teacher.sessionID)
            session_qs = SchoolSession.objects.filter(
                isDeleted=False,
                schoolID_id=teacher.schoolID_id,
            ).order_by('-datetime')
            request.session['session_list'] = [
                build_session_list_item(s)
                for s in session_qs
            ]

    return render(request, 'teacherApp/students_list.html', {'is_class_teacher': is_class_teacher})


@login_required
@check_groups('Teaching')
def teacher_assigned_subjects(request):
    teacher, current_session_id, is_class_teacher = _bootstrap_teacher_context(request)

    assigned_subjects = []
    if teacher:
        queryset = AssignSubjectsToTeacher.objects.select_related(
            'assignedSubjectID',
            'assignedSubjectID__standardID',
            'assignedSubjectID__subjectID',
        ).filter(
            isDeleted=False,
            teacherID_id=teacher.id,
            assignedSubjectID__isDeleted=False,
            assignedSubjectID__standardID__isDeleted=False,
            assignedSubjectID__subjectID__isDeleted=False,
        )

        if current_session_id:
            queryset = queryset.filter(sessionID_id=current_session_id)

        assigned_subjects = queryset.order_by('-datetime')

    return render(request, 'teacherApp/assigned_subjects.html', {
        'is_class_teacher': is_class_teacher,
        'assigned_subjects': assigned_subjects,
    })


@login_required
@check_groups('Teaching')
def teacher_student_attendance(request):
    _, _, is_class_teacher = _bootstrap_teacher_context(request)
    if 'current_session' not in request.session:
        teacher = TeacherDetail.objects.select_related('sessionID', 'schoolID').filter(
            userID_id=request.user.id,
            isDeleted=False,
        ).order_by('-datetime').first()
        if teacher and teacher.sessionID:
            request.session['current_session'] = build_current_session_payload(teacher.sessionID)
            session_qs = SchoolSession.objects.filter(
                isDeleted=False,
                schoolID_id=teacher.schoolID_id,
            ).order_by('-datetime')
            request.session['session_list'] = [
                build_session_list_item(s)
                for s in session_qs
            ]

    return render(request, 'managementApp/attendance/studentAttendance.html', {
        'base_template': 'teacherApp/index.html',
        'is_class_teacher': is_class_teacher,
    })


@login_required
@check_groups('Teaching')
def teacher_attendance_history(request):
    _, _, is_class_teacher = _bootstrap_teacher_context(request)
    return render(request, 'teacherApp/teacher_attendance_history.html', {
        'is_class_teacher': is_class_teacher,
    })


@login_required
@check_groups('Teaching')
def teacher_manage_event(request):
    _, current_session_id, is_class_teacher = _bootstrap_teacher_context(request)
    if 'current_session' not in request.session:
        teacher = TeacherDetail.objects.select_related('sessionID', 'schoolID').filter(
            userID_id=request.user.id,
            isDeleted=False,
        ).order_by('-datetime').first()
        if teacher and teacher.sessionID:
            request.session['current_session'] = build_current_session_payload(teacher.sessionID)
            session_qs = SchoolSession.objects.filter(
                isDeleted=False,
                schoolID_id=teacher.schoolID_id,
            ).order_by('-datetime')
            request.session['session_list'] = [
                build_session_list_item(s)
                for s in session_qs
            ]

    events = Event.objects.select_related('eventID').filter(
        isDeleted=False,
        sessionID_id=current_session_id,
    ).filter(
        Q(eventID__isnull=True) | Q(eventID__audience__in=['general', 'teacherapp', 'all_apps'])
    ).order_by('-startDate', '-datetime') if current_session_id else Event.objects.none()

    return render(request, 'teacherApp/events_list.html', {
        'is_class_teacher': is_class_teacher,
        'events': events,
    })


@login_required
@check_groups('Teaching')
def teacher_exam_timetable(request):
    teacher, current_session_id, is_class_teacher = _bootstrap_teacher_context(request)
    if not teacher:
        return render(request, 'teacherApp/exam_timetable.html', {
            'is_class_teacher': is_class_teacher,
            'timetable_rows': [],
            'class_options': [],
            'exam_options': [],
            'default_class_id': '',
            'default_exam_id': '',
            'school_detail': None,
        })

    assigned_class_ids = list(Standard.objects.filter(
        isDeleted=False,
        sessionID_id=current_session_id,
        classTeacher_id=teacher.id,
    ).values_list('id', flat=True))

    timetable_rows = list(ExamTimeTable.objects.select_related(
        'standardID', 'examID', 'subjectID'
    ).filter(
        isDeleted=False,
        sessionID_id=current_session_id,
        standardID_id__in=assigned_class_ids,
    ).order_by('standardID__name', 'standardID__section', 'examDate', 'startTime', 'examID__name', 'subjectID__name'))

    class_options = []
    seen_classes = set()
    for row in timetable_rows:
        if not row.standardID_id or row.standardID_id in seen_classes:
            continue
        seen_classes.add(row.standardID_id)
        section = f" - {row.standardID.section}" if row.standardID and row.standardID.section else ''
        class_options.append({
            'id': row.standardID_id,
            'label': f"{row.standardID.name or 'N/A'}{section}",
        })

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

    default_class_id = str(class_options[0]['id']) if class_options else ''
    default_exam_id = str(exam_options[0]['id']) if exam_options else ''
    school_detail = teacher.schoolID

    return render(request, 'teacherApp/exam_timetable.html', {
        'is_class_teacher': is_class_teacher,
        'timetable_rows': timetable_rows,
        'class_options': class_options,
        'exam_options': exam_options,
        'default_class_id': default_class_id,
        'default_exam_id': default_exam_id,
        'school_detail': school_detail,
    })


@login_required
@check_groups('Teaching')
def teacher_add_marks(request):
    _, _, is_class_teacher = _bootstrap_teacher_context(request)
    if 'current_session' not in request.session:
        teacher = TeacherDetail.objects.select_related('sessionID', 'schoolID').filter(
            userID_id=request.user.id,
            isDeleted=False,
        ).order_by('-datetime').first()
        if teacher and teacher.sessionID:
            request.session['current_session'] = build_current_session_payload(teacher.sessionID)
            session_qs = SchoolSession.objects.filter(
                isDeleted=False,
                schoolID_id=teacher.schoolID_id,
            ).order_by('-datetime')
            request.session['session_list'] = [
                build_session_list_item(s)
                for s in session_qs
            ]

    return render(request, 'managementApp/marks/addExamMarks.html', {
        'base_template': 'teacherApp/index.html',
        'is_class_teacher': is_class_teacher,
        'is_teacher_context': True,
    })


@login_required
@check_groups('Teaching')
def teacher_marks_details(request):
    _, _, is_class_teacher = _bootstrap_teacher_context(request)
    if 'current_session' not in request.session:
        teacher = TeacherDetail.objects.select_related('sessionID', 'schoolID').filter(
            userID_id=request.user.id,
            isDeleted=False,
        ).order_by('-datetime').first()
        if teacher and teacher.sessionID:
            request.session['current_session'] = build_current_session_payload(teacher.sessionID)
            session_qs = SchoolSession.objects.filter(
                isDeleted=False,
                schoolID_id=teacher.schoolID_id,
            ).order_by('-datetime')
            request.session['session_list'] = [
                build_session_list_item(s)
                for s in session_qs
            ]

    return render(request, 'managementApp/marks/examMarksDetails.html', {
        'base_template': 'teacherApp/index.html',
        'is_class_teacher': is_class_teacher,
    })


@login_required
@check_groups('Teaching')
def teacher_assigned_class(request):
    _, _, is_class_teacher = _bootstrap_teacher_context(request)
    if not is_class_teacher:
        return render(request, 'teacherApp/assigned_class.html', {
            'is_class_teacher': False,
            'not_class_teacher_message': 'This section is available only for assigned class teachers.',
        })
    return render(request, 'teacherApp/assigned_class.html', {'is_class_teacher': True})


@login_required
@check_groups('Teaching')
def teacher_leave_applications(request):
    teacher, current_session_id, is_class_teacher = _bootstrap_teacher_context(request)
    leave_rows = []
    pending_count = 0
    approved_count = 0
    other_count = 0

    if teacher and current_session_id:
        leave_rows = list(LeaveApplication.objects.select_related('leaveTypeID').filter(
            isDeleted=False,
            sessionID_id=current_session_id,
            teacherID_id=teacher.id,
        ).order_by('-datetime'))
        pending_count = sum(1 for row in leave_rows if (row.status or '').lower() == 'pending')
        approved_count = sum(1 for row in leave_rows if (row.status or '').lower() == 'approved')
        other_count = len(leave_rows) - pending_count - approved_count

    return render(request, 'teacherApp/leave_applications.html', {
        'is_class_teacher': is_class_teacher,
        'leave_rows': leave_rows,
        'pending_count': pending_count,
        'approved_count': approved_count,
        'other_count': other_count,
    })


@login_required
@check_groups('Teaching')
def teacher_progress_report_cards(request):
    teacher, current_session_id, is_class_teacher = _bootstrap_teacher_context(request)
    if not teacher:
        return render(request, 'teacherApp/progress_report_cards.html', {
            'is_class_teacher': is_class_teacher,
            'class_map_json': json.dumps([]),
            'students_by_class_json': json.dumps({}),
            'exams_by_class_json': json.dumps({}),
            'selected_class_id': '',
            'selected_student_id': '',
            'selected_exam_id': 'all',
            'selected_student': None,
            'report_cards': [],
        })

    subject_assigned_class_ids = list(AssignSubjectsToTeacher.objects.filter(
        isDeleted=False,
        teacherID_id=teacher.id,
        sessionID_id=current_session_id,
        assignedSubjectID__isDeleted=False,
    ).values_list('assignedSubjectID__standardID_id', flat=True).distinct())
    class_teacher_ids = list(Standard.objects.filter(
        isDeleted=False,
        sessionID_id=current_session_id,
        classTeacher_id=teacher.id,
    ).values_list('id', flat=True))
    assigned_class_ids = sorted({cid for cid in (subject_assigned_class_ids + class_teacher_ids) if cid})

    classes = list(Standard.objects.filter(
        isDeleted=False,
        sessionID_id=current_session_id,
        id__in=assigned_class_ids,
    ).order_by('name', 'section'))

    students = list(Student.objects.select_related('standardID').filter(
        isDeleted=False,
        sessionID_id=current_session_id,
        standardID_id__in=assigned_class_ids,
    ).order_by('name'))

    assigned_exams = list(AssignExamToClass.objects.select_related('examID', 'standardID').filter(
        isDeleted=False,
        sessionID_id=current_session_id,
        standardID_id__in=assigned_class_ids,
    ).order_by('examID__name', 'startDate'))

    class_map = [{
        'id': c.id,
        'name': f"{c.name or 'N/A'}{' - ' + c.section if c.section else ''}"
    } for c in classes]

    students_by_class = {}
    for s in students:
        students_by_class.setdefault(str(s.standardID_id), []).append({
            'id': s.id,
            'name': f"{s.name or 'N/A'}{' (Roll: ' + str(s.roll) + ')' if s.roll else ''}"
        })

    exams_by_class = {}
    for e in assigned_exams:
        exams_by_class.setdefault(str(e.standardID_id), []).append({
            'id': e.id,
            'name': e.examID.name if e.examID else 'N/A'
        })

    selected_class_id = request.GET.get('standard')
    selected_student_id = request.GET.get('student')
    selected_exam_id = request.GET.get('exam')
    report_cards = []
    selected_student = None

    valid_class = selected_class_id and selected_class_id.isdigit() and int(selected_class_id) in set(assigned_class_ids)
    if valid_class and selected_student_id and selected_student_id.isdigit():
        selected_class_int = int(selected_class_id)
        selected_student = Student.objects.select_related('standardID').filter(
            isDeleted=False,
            sessionID_id=current_session_id,
            standardID_id=selected_class_int,
            id=int(selected_student_id),
        ).first()

        if selected_student:
            exam_qs = AssignExamToClass.objects.select_related('examID').filter(
                isDeleted=False,
                sessionID_id=current_session_id,
                standardID_id=selected_class_int,
            )
            if selected_exam_id and selected_exam_id != 'all':
                exam_qs = exam_qs.filter(id=selected_exam_id)
            exam_qs = exam_qs.order_by('startDate', 'examID__name')

            report_cards = build_report_cards_for_student(
                current_session_id=current_session_id,
                student_obj=selected_student,
                standard_id=selected_class_int,
                exam_queryset=exam_qs,
                prefer_published_snapshot=False,
            )

    return render(request, 'teacherApp/progress_report_cards.html', {
        'is_class_teacher': is_class_teacher,
        'class_map_json': json.dumps(class_map),
        'students_by_class_json': json.dumps(students_by_class),
        'exams_by_class_json': json.dumps(exams_by_class),
        'selected_class_id': int(selected_class_id) if selected_class_id and selected_class_id.isdigit() else '',
        'selected_student_id': int(selected_student_id) if selected_student_id and selected_student_id.isdigit() else '',
        'selected_exam_id': selected_exam_id if selected_exam_id else 'all',
        'selected_student': selected_student,
        'report_cards': report_cards,
    })


@login_required
@check_groups('Teaching')
def teacher_student_detail(request, id=None):
    _, _, is_class_teacher = _bootstrap_teacher_context(request)
    queryset = Student.objects.select_related('parentID', 'standardID').filter(isDeleted=False)
    current_session = request.session.get('current_session', {})
    current_session_id = current_session.get('Id')
    if current_session_id:
        queryset = queryset.filter(sessionID_id=current_session_id)

    instance = get_object_or_404(queryset, pk=id)
    context = {
        'instance': instance,
        'parent': instance.parentID,
        'is_teacher_view': True,
        'base_template': 'teacherApp/index.html',
        'is_class_teacher': is_class_teacher,
    }
    return render(request, 'managementApp/student/student_detail.html', context)


@login_required
@check_groups('Teaching')
def teacher_subject_notes(request):
    teacher, current_session_id, is_class_teacher = _bootstrap_teacher_context(request)
    notes_count = {'total': 0, 'draft': 0, 'published': 0}
    if teacher and current_session_id:
        notes_qs = SubjectNote.objects.filter(
            isDeleted=False,
            sessionID_id=current_session_id,
            teacherID_id=teacher.id,
        )
        notes_count = {
            'total': notes_qs.count(),
            'draft': notes_qs.filter(status='draft').count(),
            'published': notes_qs.filter(status='published').count(),
        }

    return render(request, 'teacherApp/subject_notes.html', {
        'is_class_teacher': is_class_teacher,
        'notes_total': notes_count['total'],
        'notes_draft': notes_count['draft'],
        'notes_published': notes_count['published'],
    })
