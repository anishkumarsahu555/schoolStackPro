from django.urls import path
from .views import *

urlpatterns = [
    #student home
    path('home/', student_home, name='student_home'),

    #attendance
    path('attendance_history/', attendance_history, name='attendance_history'),

    #fee
    path('fee_detail/', fee_detail, name='fee_detail'),

]
