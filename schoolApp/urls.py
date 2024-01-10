from django.urls import path
from .views import *

urlpatterns = [
    #admin
    path('', admin_home, name='admin_home'),
    path('manage-class/', manage_class, name='manage_class'),

    ]
