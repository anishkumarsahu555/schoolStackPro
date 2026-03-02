from django.urls import path
from .views import *

urlpatterns = [
    #student home
    path('', student_home, name='student_root'),
    path('home/', student_home, name='student_home'),
    path('school-detail/', student_school_detail, name='student_school_detail'),
    path('my-details/', student_my_details, name='student_my_details'),
    path('id-card/', student_id_card, name='student_id_card'),

    #attendance
    path('attendance_history/', attendance_history, name='attendance_history'),

    #fee
    path('fee_detail/', fee_detail, name='fee_detail'),
    path('exams/', student_exam_details, name='student_exam_details'),
    path('progress-report-cards/', student_progress_report_cards, name='student_progress_report_cards'),
    path('events/', student_events, name='student_events'),
    path('leave-applications/', student_leave_applications, name='student_leave_applications'),

]
