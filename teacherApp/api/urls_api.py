from django.urls import path

from .views_api import TeacherStudentsListJson, TeacherAssignedSubjectsListJson, TeacherEventListJson

urlpatterns = [
    path('TeacherStudentsListJson', TeacherStudentsListJson.as_view(), name='TeacherStudentsListJson'),
    path('TeacherAssignedSubjectsListJson', TeacherAssignedSubjectsListJson.as_view(), name='TeacherAssignedSubjectsListJson'),
    path('TeacherEventListJson', TeacherEventListJson.as_view(), name='TeacherEventListJson'),
]
