from django.urls import path

from .views import (
    teacher_home,
    teacher_students_list,
    teacher_student_detail,
    teacher_assigned_subjects,
    teacher_student_attendance,
    teacher_manage_event,
    teacher_add_marks,
    teacher_marks_details,
)

urlpatterns = [
    path('', teacher_home, name='teacher_root'),
    path('home/', teacher_home, name='teacher_home'),
    path('students/', teacher_students_list, name='teacher_students_list'),
    path('assigned-subjects/', teacher_assigned_subjects, name='teacher_assigned_subjects'),
    path('student-attendance/', teacher_student_attendance, name='teacher_student_attendance'),
    path('manage-event/', teacher_manage_event, name='teacher_manage_event'),
    path('add-marks/', teacher_add_marks, name='teacher_add_marks'),
    path('marks-details/', teacher_marks_details, name='teacher_marks_details'),
    path('students/detail/<int:id>/', teacher_student_detail, name='teacher_student_detail'),
]
