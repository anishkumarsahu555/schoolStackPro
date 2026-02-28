from django.urls import path
from .views import *

urlpatterns = [
    #student home
    path('', student_home, name='student_root'),
    path('home/', student_home, name='student_home'),
    path('my-details/', student_my_details, name='student_my_details'),

    #attendance
    path('attendance_history/', attendance_history, name='attendance_history'),

    #fee
    path('fee_detail/', fee_detail, name='fee_detail'),
    path('exams/', student_exam_details, name='student_exam_details'),
    path('events/', student_events, name='student_events'),

]
