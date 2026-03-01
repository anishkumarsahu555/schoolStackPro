from datetime import date, timedelta
import json

from django.shortcuts import render, get_object_or_404

from homeApp.models import SchoolSession
from homeApp.utils import login_required
from managementApp.models import Student, TeacherDetail, AssignSubjectsToTeacher, Event, Standard, ExamTimeTable
from utils.custom_decorators import check_groups

# Create your views here.


def _bootstrap_teacher_context(request):
    teacher = TeacherDetail.objects.select_related('sessionID', 'schoolID').filter(
        userID_id=request.user.id,
        isDeleted=False,
    ).order_by('-datetime').first()

    if teacher and teacher.sessionID and 'current_session' not in request.session:
        request.session['current_session'] = {
            'currentSessionYear': teacher.sessionID.sessionYear,
            'Id': teacher.sessionID_id,
        }
        session_qs = SchoolSession.objects.filter(
            isDeleted=False,
            schoolID_id=teacher.schoolID_id,
        ).order_by('-datetime')
        request.session['session_list'] = [
            {'currentSessionYear': s.sessionYear, 'Id': s.pk}
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

    assigned_class_ids = set(assignments.values_list('assignedSubjectID__standardID_id', flat=True))
    subject_count = assignments.values_list('assignedSubjectID__subjectID_id', flat=True).distinct().count()
    class_count = len(assigned_class_ids)
    student_count = Student.objects.filter(
        isDeleted=False,
        sessionID_id=current_session_id,
        standardID_id__in=assigned_class_ids,
    ).count() if assigned_class_ids else 0

    students_qs = Student.objects.select_related('standardID').filter(
        isDeleted=False,
        sessionID_id=current_session_id,
        standardID_id__in=assigned_class_ids,
    ) if assigned_class_ids else Student.objects.none()

    class_counter = {}
    gender_counter = {'Male': 0, 'Female': 0, 'Other': 0, 'Unknown': 0}
    for stu in students_qs:
        class_name = 'N/A'
        if stu.standardID:
            class_name = stu.standardID.name or 'N/A'
            if stu.standardID.section:
                class_name = f'{class_name} - {stu.standardID.section}'
        class_counter[class_name] = class_counter.get(class_name, 0) + 1

        gender = (stu.gender or '').strip().lower()
        if gender in {'male', 'm'}:
            gender_counter['Male'] += 1
        elif gender in {'female', 'f'}:
            gender_counter['Female'] += 1
        elif gender:
            gender_counter['Other'] += 1
        else:
            gender_counter['Unknown'] += 1

    subject_counter = {}
    for item in assignments:
        sub_name = item.assignedSubjectID.subjectID.name if item.assignedSubjectID and item.assignedSubjectID.subjectID else 'N/A'
        subject_counter[sub_name] = subject_counter.get(sub_name, 0) + 1

    upcoming_events = Event.objects.filter(
        isDeleted=False,
        sessionID_id=current_session_id,
        startDate__gte=date.today(),
        startDate__lte=date.today() + timedelta(days=30),
    ).order_by('startDate')[:5]

    recent_assignments = []
    for item in assignments.order_by('-datetime')[:6]:
        class_name = item.assignedSubjectID.standardID.name if item.assignedSubjectID.standardID else 'N/A'
        section = item.assignedSubjectID.standardID.section if item.assignedSubjectID.standardID else ''
        if section:
            class_name = f'{class_name} - {section}'
        recent_assignments.append({
            'class_name': class_name,
            'subject_name': item.assignedSubjectID.subjectID.name if item.assignedSubjectID.subjectID else 'N/A',
            'branch': item.subjectBranch or 'Main',
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
            request.session['current_session'] = {
                'currentSessionYear': teacher.sessionID.sessionYear,
                'Id': teacher.sessionID_id,
            }
            session_qs = SchoolSession.objects.filter(
                isDeleted=False,
                schoolID_id=teacher.schoolID_id,
            ).order_by('-datetime')
            request.session['session_list'] = [
                {'currentSessionYear': s.sessionYear, 'Id': s.pk}
                for s in session_qs
            ]

    return render(request, 'teacherApp/students_list.html', {'is_class_teacher': is_class_teacher})


@login_required
@check_groups('Teaching')
def teacher_assigned_subjects(request):
    _, _, is_class_teacher = _bootstrap_teacher_context(request)
    if 'current_session' not in request.session:
        teacher = TeacherDetail.objects.select_related('sessionID', 'schoolID').filter(
            userID_id=request.user.id,
            isDeleted=False,
        ).order_by('-datetime').first()
        if teacher and teacher.sessionID:
            request.session['current_session'] = {
                'currentSessionYear': teacher.sessionID.sessionYear,
                'Id': teacher.sessionID_id,
            }
            session_qs = SchoolSession.objects.filter(
                isDeleted=False,
                schoolID_id=teacher.schoolID_id,
            ).order_by('-datetime')
            request.session['session_list'] = [
                {'currentSessionYear': s.sessionYear, 'Id': s.pk}
                for s in session_qs
            ]

    return render(request, 'teacherApp/assigned_subjects.html', {'is_class_teacher': is_class_teacher})


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
            request.session['current_session'] = {
                'currentSessionYear': teacher.sessionID.sessionYear,
                'Id': teacher.sessionID_id,
            }
            session_qs = SchoolSession.objects.filter(
                isDeleted=False,
                schoolID_id=teacher.schoolID_id,
            ).order_by('-datetime')
            request.session['session_list'] = [
                {'currentSessionYear': s.sessionYear, 'Id': s.pk}
                for s in session_qs
            ]

    return render(request, 'managementApp/attendance/studentAttendance.html', {
        'base_template': 'teacherApp/index.html',
        'is_class_teacher': is_class_teacher,
    })


@login_required
@check_groups('Teaching')
def teacher_manage_event(request):
    _, _, is_class_teacher = _bootstrap_teacher_context(request)
    if 'current_session' not in request.session:
        teacher = TeacherDetail.objects.select_related('sessionID', 'schoolID').filter(
            userID_id=request.user.id,
            isDeleted=False,
        ).order_by('-datetime').first()
        if teacher and teacher.sessionID:
            request.session['current_session'] = {
                'currentSessionYear': teacher.sessionID.sessionYear,
                'Id': teacher.sessionID_id,
            }
            session_qs = SchoolSession.objects.filter(
                isDeleted=False,
                schoolID_id=teacher.schoolID_id,
            ).order_by('-datetime')
            request.session['session_list'] = [
                {'currentSessionYear': s.sessionYear, 'Id': s.pk}
                for s in session_qs
            ]

    return render(request, 'teacherApp/events_list.html', {'is_class_teacher': is_class_teacher})


@login_required
@check_groups('Teaching')
def teacher_exam_timetable(request):
    teacher, current_session_id, is_class_teacher = _bootstrap_teacher_context(request)
    if not teacher:
        return render(request, 'teacherApp/exam_timetable.html', {
            'is_class_teacher': is_class_teacher,
            'timetable_rows': [],
        })

    assigned_class_ids = list(AssignSubjectsToTeacher.objects.filter(
        isDeleted=False,
        teacherID_id=teacher.id,
        sessionID_id=current_session_id,
        assignedSubjectID__isDeleted=False,
    ).values_list('assignedSubjectID__standardID_id', flat=True).distinct())
    assigned_class_ids = [cid for cid in assigned_class_ids if cid]

    timetable_rows = ExamTimeTable.objects.select_related(
        'standardID', 'examID', 'subjectID'
    ).filter(
        isDeleted=False,
        sessionID_id=current_session_id,
        standardID_id__in=assigned_class_ids,
    ).order_by('examID__name', 'examDate', 'startTime', 'standardID__name')

    return render(request, 'teacherApp/exam_timetable.html', {
        'is_class_teacher': is_class_teacher,
        'timetable_rows': timetable_rows,
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
            request.session['current_session'] = {
                'currentSessionYear': teacher.sessionID.sessionYear,
                'Id': teacher.sessionID_id,
            }
            session_qs = SchoolSession.objects.filter(
                isDeleted=False,
                schoolID_id=teacher.schoolID_id,
            ).order_by('-datetime')
            request.session['session_list'] = [
                {'currentSessionYear': s.sessionYear, 'Id': s.pk}
                for s in session_qs
            ]

    return render(request, 'managementApp/marks/addExamMarks.html', {
        'base_template': 'teacherApp/index.html',
        'is_class_teacher': is_class_teacher,
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
            request.session['current_session'] = {
                'currentSessionYear': teacher.sessionID.sessionYear,
                'Id': teacher.sessionID_id,
            }
            session_qs = SchoolSession.objects.filter(
                isDeleted=False,
                schoolID_id=teacher.schoolID_id,
            ).order_by('-datetime')
            request.session['session_list'] = [
                {'currentSessionYear': s.sessionYear, 'Id': s.pk}
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
