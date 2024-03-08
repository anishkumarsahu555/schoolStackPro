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

]
