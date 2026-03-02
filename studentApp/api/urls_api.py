from django.urls import path

from .views_api import *
from .leave_views_api import *

urlpatterns = [
    # api
    path('get_subjects_to_class_assign_list_for_student_in_class_api',
         get_subjects_to_class_assign_list_for_student_in_class_api,
         name='get_subjects_to_class_assign_list_for_student_in_class_api'),
    path('StudentAttendanceHistoryByDateRangeJson', StudentAttendanceHistoryByDateRangeJson.as_view(),
         name='StudentAttendanceHistoryByDateRangeJson'),
    path('StudentAttendanceMonthWiseSummaryApi', StudentAttendanceMonthWiseSummaryApi,
         name='StudentAttendanceMonthWiseSummaryApi'),
    path('StudentAttendanceSubjectWiseSummaryApi', StudentAttendanceSubjectWiseSummaryApi,
         name='StudentAttendanceSubjectWiseSummaryApi'),
    path('StudentFeeDetailsJson', StudentFeeDetailsJson.as_view(), name='StudentFeeDetailsJson'),
    path('get_student_leave_type_list_api', get_student_leave_type_list_api, name='get_student_leave_type_list_api'),
    path('StudentLeaveApplicationListJson', StudentLeaveApplicationListJson.as_view(), name='StudentLeaveApplicationListJson'),
    path('student_apply_leave_api', student_apply_leave_api, name='student_apply_leave_api'),
    path('student_update_leave_api', student_update_leave_api, name='student_update_leave_api'),
    path('student_cancel_leave_api', student_cancel_leave_api, name='student_cancel_leave_api'),
    path('get_student_leave_detail_api', get_student_leave_detail_api, name='get_student_leave_detail_api'),

]
