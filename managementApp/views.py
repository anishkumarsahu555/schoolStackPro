import json
from datetime import date, timedelta

from django.db.models import Count
from django.shortcuts import render, get_object_or_404, redirect

from homeApp.utils import login_required
from homeApp.models import SchoolDetail
from managementApp.models import *
from managementApp.signals import pre_save_with_user
from utils.custom_decorators import check_groups


# Create your views here.

@check_groups('Admin', 'Owner')
def admin_home(request):
    current_session_id = request.session['current_session']['Id']
    current_session_year = request.session.get('current_session', {}).get('currentSessionYear', 'N/A')

    totalStudent = Student.objects.filter(isDeleted=False, sessionID_id=current_session_id).count()
    totalTeacher = TeacherDetail.objects.filter(isDeleted=False, sessionID_id=current_session_id).count()
    totalClass = Standard.objects.filter(isDeleted=False, sessionID_id=current_session_id).count()
    totalSubject = Subjects.objects.filter(isDeleted=False, sessionID_id=current_session_id).count()
    totalParents = Parent.objects.filter(isDeleted=False, sessionID_id=current_session_id).count()

    upcoming_events = Event.objects.filter(
        isDeleted=False,
        sessionID_id=current_session_id,
        startDate__gte=date.today(),
        startDate__lte=date.today() + timedelta(days=30),
    ).order_by('startDate')[:5]

    recent_students = Student.objects.select_related('standardID').filter(
        isDeleted=False,
        sessionID_id=current_session_id,
    ).order_by('-datetime')[:5]

    class_distribution = list(
        Student.objects.filter(
            isDeleted=False,
            sessionID_id=current_session_id,
        ).values('standardID__name').annotate(total=Count('id')).order_by('-total')[:8]
    )

    context = {
        'total_students': totalStudent,
        'total_teachers': totalTeacher,
        'total_classes': totalClass,
        'total_subjects': totalSubject,
        'total_parents': totalParents,
        'current_session_year': current_session_year,
        'upcoming_events': upcoming_events,
        'recent_students': recent_students,
        'summary_labels_json': json.dumps(['Students', 'Teachers', 'Subjects', 'Classes']),
        'summary_values_json': json.dumps([totalStudent, totalTeacher, totalSubject, totalClass]),
        'class_labels_json': json.dumps([row['standardID__name'] or 'N/A' for row in class_distribution]),
        'class_values_json': json.dumps([row['total'] for row in class_distribution]),
    }
    return render(request, 'managementApp/dashboard.html', context)


@login_required
@check_groups('Admin', 'Owner')
def school_detail(request):
    school_id = request.session.get('current_session', {}).get('SchoolID')
    school = None
    if school_id:
        school = SchoolDetail.objects.filter(pk=school_id, isDeleted=False).first()
    if not school:
        school = SchoolDetail.objects.filter(
            ownerID__userID_id=request.user.id,
            isDeleted=False
        ).order_by('-datetime').first()

    context = {
        'school': school,
    }
    return render(request, 'managementApp/school/school_detail.html', context)


@login_required
@check_groups('Admin', 'Owner')
def manage_class(request):
    context = {
    }
    return render(request, 'managementApp/class.html', context)


@login_required
@check_groups('Admin', 'Owner')
def manage_subjects(request):
    context = {
    }
    return render(request, 'managementApp/subjects/addEditListSubjects.html', context)


@login_required
@check_groups('Admin', 'Owner')
def assign_subjects_to_class(request):
    context = {
    }
    return render(request, 'managementApp/subjects/assignSubjectsToClass.html', context)


@login_required
@check_groups('Admin', 'Owner')
def assign_subjects_to_teacher(request):
    context = {
    }
    return render(request, 'managementApp/subjects/assignSubjectsToTeacher.html', context)


# Teacher --------------------
@login_required
@check_groups('Admin', 'Owner')
def add_teacher(request):
    context = {
    }
    return render(request, 'managementApp/teacher/add_teacher.html', context)

@login_required
@check_groups('Admin', 'Owner')
def edit_teacher(request,id=None):
    instance = get_object_or_404(TeacherDetail, pk=id)

    context = {
        'instance': instance,
    }
    return render(request, 'managementApp/teacher/edit_teacher.html', context)



@login_required
@check_groups('Admin', 'Owner')
def teacher_list(request):
    context = {
    }
    return render(request, 'managementApp/teacher/teacher_list.html', context)

@login_required
@check_groups('Admin', 'Owner')
def teacher_detail(request, id=None):
    instance = get_object_or_404(TeacherDetail, pk=id)
    context = {
        'instance': instance,
    }
    return render(request, 'managementApp/teacher/teacher_detail.html', context)



# student

@login_required
@check_groups('Admin', 'Owner')
def add_student(request):
    context = {
    }
    return render(request, 'managementApp/student/add_student.html', context)


@login_required
@check_groups('Admin', 'Owner')
def student_list(request):
    context = {
    }
    return render(request, 'managementApp/student/student_list.html', context)


@login_required
@check_groups('Admin', 'Owner')
def student_detail(request, id=None):
    instance = get_object_or_404(Student, pk=id)
    parent = instance.parentID
    context = {
        'instance': instance,
        'parent': parent,
    }
    return render(request, 'managementApp/student/student_detail.html', context)

@login_required
@check_groups('Admin', 'Owner')
def edit_student_detail(request, id=None):
    instance = get_object_or_404(Student, pk=id)
    context = {
        'instance': instance,
    }
    return render(request, 'managementApp/student/edit_student.html', context)


@login_required
@check_groups('Admin', 'Owner')
def student_id_cards(request):
    context = {}
    return render(request, 'managementApp/student/student_id_cards.html', context)


@login_required
@check_groups('Admin', 'Owner')
def student_id_card_detail(request, id=None):
    current_session_id = request.session['current_session']['Id']
    instance = get_object_or_404(
        Student.objects.select_related('standardID', 'parentID'),
        pk=id,
        isDeleted=False,
        sessionID_id=current_session_id,
    )
    embed_mode = request.GET.get('embed') == '1'
    partial_mode = request.GET.get('partial') == '1'
    context = {
        'instance': instance,
        'school': instance.schoolID,
        'school_name': (
            (instance.schoolID.schoolName if instance.schoolID else '')
            or (instance.schoolID.name if instance.schoolID else '')
            or 'School Name'
        ),
        'valid_till_label': 'Upto 2026',
        'embed_mode': embed_mode,
    }
    if partial_mode:
        return render(request, 'managementApp/student/student_id_card_embed.html', context)
    return render(request, 'managementApp/student/student_id_card_detail.html', context)


# Exam ----------------------------------------------
@login_required
@check_groups('Admin', 'Owner')
def manage_exams(request):
    context = {
    }
    return render(request, 'managementApp/exam/addEditListExams.html', context)


@login_required
@check_groups('Admin', 'Owner')
def assign_exams_to_class(request):
    context = {
    }
    return render(request, 'managementApp/exam/assignExamToClass.html', context)


@login_required
@check_groups('Admin', 'Owner')
def manage_exam_timetable(request):
    context = _exam_timetable_preview_context(request)
    return render(request, 'managementApp/exam/examTimeTable.html', context)


def _exam_timetable_preview_context(request):
    current_session_id = request.session.get('current_session', {}).get('Id')
    timetable_rows = ExamTimeTable.objects.select_related(
        'standardID', 'examID', 'subjectID'
    ).filter(
        isDeleted=False,
        sessionID_id=current_session_id,
    ).order_by('examID__name', 'examDate', 'startTime', 'standardID__name') if current_session_id else ExamTimeTable.objects.none()

    school_detail = None
    school_id = request.session.get('current_session', {}).get('SchoolID')
    if school_id:
        school_detail = SchoolDetail.objects.filter(pk=school_id, isDeleted=False).first()
    if not school_detail and current_session_id:
        school_detail = SchoolDetail.objects.filter(
            schoolsession__id=current_session_id,
            schoolsession__isDeleted=False,
            isDeleted=False
        ).distinct().first()

    context = {
        'timetable_rows': timetable_rows,
        'school_detail': school_detail,
        'exam_year': request.session.get('current_session', {}).get('currentSessionYear') or 'Exam Year',
    }
    return context


@login_required
@check_groups('Admin', 'Owner')
def manage_exam_timetable_preview(request):
    context = _exam_timetable_preview_context(request)
    return render(request, 'managementApp/exam/examTimeTablePreview.html', context)


# attendance
@login_required
@check_groups('Admin', 'Owner')
def student_attendance(request):
    context = {
    }
    return render(request, 'managementApp/attendance/studentAttendance.html', context)


@login_required
@check_groups('Admin', 'Owner')
def student_attendance_history(request):
    context = {
    }
    return render(request, 'managementApp/attendance/studentAttendanceHistory.html', context)


@login_required
@check_groups('Admin', 'Owner')
def staff_attendance(request):
    context = {
    }
    return render(request, 'managementApp/attendance/staffAttendance.html', context)


@login_required
@check_groups('Admin', 'Owner')
def staff_attendance_history(request):
    context = {
    }
    return render(request, 'managementApp/attendance/staffAttendanceHistory.html', context)


# student Fee --------------------------------------------------
@login_required
@check_groups('Admin', 'Owner')
def student_fee(request):
    context = {
    }
    return render(request, 'managementApp/fee/addStudentFee.html', context)


@login_required
@check_groups('Admin', 'Owner')
def student_fee_details(request):
    context = {
    }
    return render(request, 'managementApp/fee/feeDetails.html', context)


# Marks -------------------------------------------------------
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


@login_required
@check_groups('Admin', 'Owner')
def student_marks(request):
    context = {
    }
    return render(request, 'managementApp/marks/addExamMarks.html', context)


@login_required
@check_groups('Admin', 'Owner')
def exam_marks_details(request):
    context = {
    }
    return render(request, 'managementApp/marks/examMarksDetails.html', context)


@login_required
@check_groups('Admin', 'Owner')
def progress_report_cards(request):
    current_session_id = request.session['current_session']['Id']

    class_qs = Standard.objects.filter(
        isDeleted=False,
        sessionID_id=current_session_id
    ).order_by('name', 'section')
    classes = list(class_qs)

    students = list(Student.objects.select_related('standardID').filter(
        isDeleted=False,
        sessionID_id=current_session_id
    ).order_by('name'))

    assigned_exams = list(AssignExamToClass.objects.select_related('examID', 'standardID').filter(
        isDeleted=False,
        sessionID_id=current_session_id
    ).order_by('examID__name', 'startDate'))

    class_map = []
    for c in classes:
        class_map.append({
            'id': c.id,
            'name': f"{c.name or 'N/A'}{' - ' + c.section if c.section else ''}"
        })

    students_by_class = {}
    for s in students:
        if not s.standardID_id:
            continue
        students_by_class.setdefault(str(s.standardID_id), []).append({
            'id': s.id,
            'name': f"{s.name or 'N/A'}{' (Roll: ' + str(s.roll) + ')' if s.roll else ''}"
        })

    exams_by_class = {}
    for e in assigned_exams:
        if not e.standardID_id:
            continue
        exams_by_class.setdefault(str(e.standardID_id), []).append({
            'id': e.id,
            'name': e.examID.name if e.examID else 'N/A'
        })

    selected_class_id = request.GET.get('standard')
    selected_student_id = request.GET.get('student')
    selected_exam_id = request.GET.get('exam')

    report_cards = []
    selected_student = None

    if selected_class_id and selected_student_id:
        student_obj = Student.objects.select_related('standardID').filter(
            isDeleted=False,
            sessionID_id=current_session_id,
            standardID_id=selected_class_id,
            id=selected_student_id
        ).first()

        if student_obj:
            selected_student = student_obj
            exam_queryset = AssignExamToClass.objects.select_related('examID').filter(
                isDeleted=False,
                sessionID_id=current_session_id,
                standardID_id=selected_class_id,
            )
            if selected_exam_id and selected_exam_id != 'all':
                exam_queryset = exam_queryset.filter(id=selected_exam_id)
            exam_queryset = exam_queryset.order_by('startDate', 'examID__name')

            class_subjects = list(AssignSubjectsToClass.objects.select_related('subjectID').filter(
                isDeleted=False,
                sessionID_id=current_session_id,
                standardID_id=selected_class_id,
            ).order_by('subjectID__name'))

            for exam_obj in exam_queryset:
                marks_qs = MarkOfStudentsByExam.objects.select_related('subjectID', 'subjectID__subjectID').filter(
                    isDeleted=False,
                    sessionID_id=current_session_id,
                    studentID_id=student_obj.id,
                    standardID_id=selected_class_id,
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
                    'is_complete': is_complete,
                    'entered_marks_count': entered_marks_count,
                    'subject_count': len(class_subjects),
                    'subject_rows': subject_rows,
                })

    context = {
        'class_map_json': json.dumps(class_map),
        'students_by_class_json': json.dumps(students_by_class),
        'exams_by_class_json': json.dumps(exams_by_class),
        'selected_class_id': int(selected_class_id) if selected_class_id and selected_class_id.isdigit() else '',
        'selected_student_id': int(selected_student_id) if selected_student_id and selected_student_id.isdigit() else '',
        'selected_exam_id': selected_exam_id if selected_exam_id else 'all',
        'selected_student': selected_student,
        'report_cards': report_cards,
    }
    return render(request, 'managementApp/marks/progressReportCards.html', context)


#events------------------------------------------------------
@login_required
@check_groups('Admin', 'Owner')
def manage_event(request):
    context = {
    }
    return render(request, 'managementApp/events/add_event.html', context)


@login_required
@check_groups('Admin', 'Owner')
def manage_event_type(request):
    context = {
    }
    return render(request, 'managementApp/events/manage_event_type.html', context)


@login_required
@check_groups('Admin', 'Owner')
def manage_leave_types(request):
    return render(request, 'managementApp/leave/manage_leave_types.html', {})


@login_required
@check_groups('Admin', 'Owner')
def manage_leave_applications(request):
    return render(request, 'managementApp/leave/manage_leave_applications.html', {})

# ----Parents -------------------
@login_required
@check_groups('Admin', 'Owner')
def manage_parents(request):
    context = {
    }
    return render(request, 'managementApp/parents/parents_list.html', context)


@login_required
@check_groups('Admin', 'Owner')
def parent_detail(request, id=None):
    parent = get_object_or_404(Parent, pk=id, isDeleted=False)
    current_session_id = request.session['current_session']['Id']
    wards = Student.objects.select_related('standardID').filter(
        parentID_id=parent.id,
        isDeleted=False,
        sessionID_id=current_session_id,
    ).order_by('name')
    context = {
        'parent': parent,
        'wards': wards,
    }
    return render(request, 'managementApp/parents/parent_detail.html', context)


@login_required
@check_groups('Admin', 'Owner')
def edit_parent(request, id=None):
    parent = get_object_or_404(
        Parent,
        pk=id,
        isDeleted=False,
        sessionID_id=request.session['current_session']['Id'],
    )

    if request.method == 'POST':
        parent.fatherName = request.POST.get('fatherName')
        parent.fatherPhone = request.POST.get('fatherPhone')
        parent.fatherEmail = request.POST.get('fatherEmail')
        parent.fatherOccupation = request.POST.get('fatherOccupation')
        parent.fatherAddress = request.POST.get('fatherAddress')

        parent.motherName = request.POST.get('motherName')
        parent.motherPhone = request.POST.get('motherPhone')
        parent.motherEmail = request.POST.get('motherEmail')
        parent.motherOccupation = request.POST.get('motherOccupation')
        parent.motherAddress = request.POST.get('motherAddress')

        parent.guardianName = request.POST.get('guardianName')
        parent.guardianPhone = request.POST.get('guardianPhone')
        parent.guardianOccupation = request.POST.get('guardianOccupation')

        parent.familyType = request.POST.get('familyType')
        parent.totalFamilyMembers = request.POST.get('totalFamilyMembers') or None
        parent.annualIncome = request.POST.get('annualIncome') or 0
        parent.phoneNumber = request.POST.get('primaryPhone')
        parent.email = request.POST.get('primaryEmail')

        pre_save_with_user.send(sender=Parent, instance=parent, user=request.user.pk)
        parent.save()
        return redirect('managementApp:parent_detail', id=parent.id)

    context = {
        'parent': parent,
    }
    return render(request, 'managementApp/parents/edit_parent.html', context)
