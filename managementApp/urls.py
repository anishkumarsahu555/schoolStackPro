from django.urls import path
from .views import *

urlpatterns = [
    #admin
    path('', admin_home, name='admin_home'),
    path('manage-class/', manage_class, name='manage_class'),

    # subjects
    path('manage_subjects/', manage_subjects, name='manage_subjects'),
    path('assign_subjects_to_class/', assign_subjects_to_class, name='assign_subjects_to_class'),

    ]
