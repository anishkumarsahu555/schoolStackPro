from django.urls import path
from .views_api import *


urlpatterns = [
    # api
    path('add_class', add_class, name='add_class'),
    path('class_list', StandardListJson.as_view(), name='class_list'),

    path('get_class_detail', get_class_detail, name='get_class_detail'),
    path('delete_class', delete_class, name='delete_class'),
    path('get_standard_list_api', get_standard_list_api, name='get_standard_list_api'),

    # subject api
    path('add_subject', add_subject, name='add_subject'),
    path('delete_subject', delete_subject, name='delete_subject'),
    path('get_subject_detail', get_subject_detail, name='get_subject_detail'),
    path('edit_subject', edit_subject, name='edit_subject'),
    path('get_subjects_list_api', get_subjects_list_api, name='get_subjects_list_api'),
    path('SubjectListJson', SubjectListJson.as_view(), name='SubjectListJson'),

    # subjects to class
    path('add_subject_to_class', add_subject_to_class, name='add_subject_to_class'),
    path('delete_assign_subject_to_class', delete_assign_subject_to_class, name='delete_assign_subject_to_class'),
    path('get_assigned_subject_to_class_detail', get_assigned_subject_to_class_detail, name='get_assigned_subject_to_class_detail'),
    path('update_subject_to_class', update_subject_to_class, name='update_subject_to_class'),
    path('AssignSubjectToClassListJson', AssignSubjectToClassListJson.as_view(), name='AssignSubjectToClassListJson'),

    ]