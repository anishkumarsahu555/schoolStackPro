from django.urls import path
from .views import *

urlpatterns = [
    #admin
    path('', admin_home, name='admin_home'),
    path('manage-class/', manage_class, name='manage_class'),

    # subjects
    path('manage_subjects/', manage_subjects, name='manage_subjects'),
    path('assign_subjects_to_class/', assign_subjects_to_class, name='assign_subjects_to_class'),
    path('assign_subjects_to_teacher/', assign_subjects_to_teacher, name='assign_subjects_to_teacher'),

    # Teacher
    path('add_teacher/', add_teacher, name='add_teacher'),
    path('teacher_list/', teacher_list, name='teacher_list'),

    # Student
    path('add_student/', add_student, name='add_student'),
    path('student_list/', student_list, name='student_list'),

    # Exams
    path('manage_exams/', manage_exams, name='manage_exams'),
    path('assign_exams_to_class/', assign_exams_to_class, name='assign_exams_to_class'),

    # Attendance
    path('student_attendance/', student_attendance, name='student_attendance'),
    path('student_attendance_history/', student_attendance_history, name='student_attendance_history'),
    path('staff_attendance/', staff_attendance, name='staff_attendance'),
    path('staff_attendance_history/', staff_attendance_history, name='staff_attendance_history'),

    # Fee
    path('student_fee/', student_fee, name='student_fee'),
    path('student_fee_details/', student_fee_details, name='student_fee_details'),

]
