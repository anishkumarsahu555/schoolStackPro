from django.urls import path
from .views_api import *


urlpatterns = [
    # api
    path('add_class', add_class, name='add_class'),
    path('class_list', StandardListJson.as_view(), name='class_list'),
    path('get_class_detail', get_class_detail, name='get_class_detail'),
    path('delete_class', delete_class, name='delete_class'),

    # subject api
    path('add_subject', add_subject, name='add_subject'),
    path('delete_subject', delete_subject, name='delete_subject'),
    path('get_subject_detail', get_subject_detail, name='get_subject_detail'),
    path('edit_subject', edit_subject, name='edit_subject'),
    path('SubjectListJson', SubjectListJson.as_view(), name='SubjectListJson'),

    ]