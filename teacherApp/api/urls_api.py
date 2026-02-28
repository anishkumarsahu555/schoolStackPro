from django.urls import path

from .views_api import (
    TeacherStudentsListJson,
    TeacherAssignedSubjectsListJson,
    TeacherEventListJson,
    TeacherAssignedClassFeeListJson,
    TeacherAssignedClassStudentsJson,
    get_assigned_class_list_api,
    get_assigned_student_list_by_class_api,
    TeacherStudentFeeDetailsByClassJson,
    TeacherStudentFeeDetailsByStudentJson,
)

urlpatterns = [
    path('TeacherStudentsListJson', TeacherStudentsListJson.as_view(), name='TeacherStudentsListJson'),
    path('TeacherAssignedSubjectsListJson', TeacherAssignedSubjectsListJson.as_view(), name='TeacherAssignedSubjectsListJson'),
    path('get_assigned_class_list_api', get_assigned_class_list_api, name='get_assigned_class_list_api'),
    path('get_assigned_student_list_by_class_api', get_assigned_student_list_by_class_api, name='get_assigned_student_list_by_class_api'),
    path('TeacherAssignedClassStudentsJson', TeacherAssignedClassStudentsJson.as_view(), name='TeacherAssignedClassStudentsJson'),
    path('TeacherAssignedClassFeeListJson', TeacherAssignedClassFeeListJson.as_view(), name='TeacherAssignedClassFeeListJson'),
    path('TeacherStudentFeeDetailsByClassJson', TeacherStudentFeeDetailsByClassJson.as_view(), name='TeacherStudentFeeDetailsByClassJson'),
    path('TeacherStudentFeeDetailsByStudentJson', TeacherStudentFeeDetailsByStudentJson.as_view(), name='TeacherStudentFeeDetailsByStudentJson'),
    path('TeacherEventListJson', TeacherEventListJson.as_view(), name='TeacherEventListJson'),
]
