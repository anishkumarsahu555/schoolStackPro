from django.urls import path

from .views_api import *

urlpatterns = [
    # api
    path('get_subjects_to_class_assign_list_for_student_in_class_api',
         get_subjects_to_class_assign_list_for_student_in_class_api,
         name='get_subjects_to_class_assign_list_for_student_in_class_api'),
    path('StudentAttendanceHistoryByDateRangeJson', StudentAttendanceHistoryByDateRangeJson.as_view(),
         name='StudentAttendanceHistoryByDateRangeJson'),
    path('StudentFeeDetailsJson', StudentFeeDetailsJson.as_view(), name='StudentFeeDetailsJson'),

]
