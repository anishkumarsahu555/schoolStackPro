from django.shortcuts import render, get_object_or_404

from homeApp.utils import login_required
from managementApp.models import *
from utils.custom_decorators import check_groups


# Create your views here.

@check_groups('Admin', 'Owner')
def admin_home(request):
    totalStudent = Student.objects.filter(isDeleted=False, sessionID_id = request.session['current_session']['Id']).count()
    totalTeacher = TeacherDetail.objects.filter(isDeleted=False, sessionID_id = request.session['current_session']['Id']).count()
    totalClass = Standard.objects.filter(isDeleted=False, sessionID_id = request.session['current_session']['Id']).count()
    totalSubject = Subjects.objects.filter(isDeleted=False, sessionID_id = request.session['current_session']['Id']).count()

    context = {
        'total_students': totalStudent,
        'total_teachers': totalTeacher,
        'total_classes': totalClass,
        'total_subjects': totalSubject,
    }
    return render(request, 'managementApp/dashboard.html', context)


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
    
    # Convert image to base64 if it exists
    photo_base64 = None
    if instance.photo:
        import base64
        
        # Read the image file and encode to base64
        with instance.photo.open('rb') as image_file:
            photo_base64 = base64.b64encode(image_file.read()).decode('utf-8')
    
    # Add base64 data to instance
    instance.photo_base64 = photo_base64
    
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


#events------------------------------------------------------
@login_required
@check_groups('Admin', 'Owner')
def manage_event(request):
    context = {
    }
    return render(request, 'managementApp/events/add_event.html', context)

# ----Parents -------------------
@login_required
@check_groups('Admin', 'Owner')
def manage_parents(request):
    context = {
    }
    return render(request, 'managementApp/parents/parents_list.html', context)
