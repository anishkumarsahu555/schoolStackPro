from django.urls import path

from .cached_views import *

urlpatterns = [
    path('get_cached_subjects_list_api', get_cached_subjects_list_api, name='get_cached_subjects_list_api'),
    path('get_cached_standard_list_api', get_cached_standard_list_api, name='get_cached_standard_list_api'),
    path('get_cached_student_list_api', get_cached_student_list_api, name='get_cached_student_list_api'),
    path('get_cached_teacher_list_api', get_cached_teacher_list_api, name='get_cached_teacher_list_api'),
]
