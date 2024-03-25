from django.urls import path

from .views_api import *

urlpatterns = [
    # api
    path('add_class', add_class, name='add_class'),
    path('class_list', StandardListJson.as_view(), name='class_list'),

    path('get_class_detail', get_class_detail, name='get_class_detail'),
    path('delete_class', delete_class, name='delete_class'),
    path('get_standard_list_api', get_standard_list_api, name='get_standard_list_api'),

    # subject api
    path('add_subject', add_subject, name='add_subject'),
    path('delete_subject', delete_subject, name='delete_subject'),
    path('get_subject_detail', get_subject_detail, name='get_subject_detail'),
    path('edit_subject', edit_subject, name='edit_subject'),
    path('get_subjects_list_api', get_subjects_list_api, name='get_subjects_list_api'),
    path('SubjectListJson', SubjectListJson.as_view(), name='SubjectListJson'),

    # subjects to class
    path('add_subject_to_class', add_subject_to_class, name='add_subject_to_class'),
    path('delete_assign_subject_to_class', delete_assign_subject_to_class, name='delete_assign_subject_to_class'),
    path('get_assigned_subject_to_class_detail', get_assigned_subject_to_class_detail,
         name='get_assigned_subject_to_class_detail'),
    path('update_subject_to_class', update_subject_to_class, name='update_subject_to_class'),
    path('get_subjects_to_class_assign_list_api', get_subjects_to_class_assign_list_api,
         name='get_subjects_to_class_assign_list_api'),
    path('get_subjects_to_class_assign_list_with_given_class_api',
         get_subjects_to_class_assign_list_with_given_class_api,
         name='get_subjects_to_class_assign_list_with_given_class_api'),

    path('AssignSubjectToClassListJson', AssignSubjectToClassListJson.as_view(), name='AssignSubjectToClassListJson'),

    # subjects to teacher
    path('add_subject_to_teacher', add_subject_to_teacher, name='add_subject_to_teacher'),
    path('delete_assign_teacher_to_subject', delete_assign_teacher_to_subject, name='delete_assign_teacher_to_subject'),
    path('get_assigned_subject_to_teacher_detail', get_assigned_subject_to_teacher_detail,
         name='get_assigned_subject_to_teacher_detail'),
    path('update_subject_to_teacher', update_subject_to_teacher, name='update_subject_to_teacher'),
    path('AssignSubjectToTeacherListJson', AssignSubjectToTeacherListJson.as_view(),
         name='AssignSubjectToTeacherListJson'),
    # Teacher Staff
    path('add_teacher_api', add_teacher_api, name='add_teacher_api'),
    path('delete_teacher', delete_teacher, name='delete_teacher'),
    path('get_teacher_list_api', get_teacher_list_api, name='get_teacher_list_api'),
    path('TeacherListJson', TeacherListJson.as_view(), name='TeacherListJson'),

    # student
    path('add_student_api', add_student_api, name='add_student_api'),
    path('delete_student', delete_student, name='delete_student'),
    path('get_student_list_by_class_api', get_student_list_by_class_api, name='get_student_list_by_class_api'),
    path('StudentListJson', StudentListJson.as_view(), name='StudentListJson'),

    # Exam
    path('add_exam', add_exam, name='add_exam'),
    path('delete_exam', delete_exam, name='delete_exam'),
    path('get_exam_detail', get_exam_detail, name='get_exam_detail'),
    path('edit_exam', edit_exam, name='edit_exam'),
    path('get_exams_list_api', get_exams_list_api, name='get_exams_list_api'),
    path('ExamListJson', ExamListJson.as_view(), name='ExamListJson'),

    # assign Exam to class
    path('add_exam_to_class', add_exam_to_class, name='add_exam_to_class'),
    path('delete_assign_exam_to_class', delete_assign_exam_to_class, name='delete_assign_exam_to_class'),
    path('get_assigned_exam_to_class_detail', get_assigned_exam_to_class_detail,
         name='get_assigned_exam_to_class_detail'),
    path('update_exam_to_class', update_exam_to_class, name='update_exam_to_class'),
    # path('get_exams_to_class_assign_list_api', get_exams_to_class_assign_list_api, name='get_exams_to_class_assign_list_api'),
    path('AssignExamToClassListJson', AssignExamToClassListJson.as_view(), name='AssignExamToClassListJson'),

    # Attendance
    path('TakeStudentAttendanceByClassJson', TakeStudentAttendanceByClassJson.as_view(),
         name='TakeStudentAttendanceByClassJson'),
    path('add_student_attendance_by_class', add_student_attendance_by_class, name='add_student_attendance_by_class'),
    path('StudentAttendanceHistoryByDateRangeJson', StudentAttendanceHistoryByDateRangeJson.as_view(),
         name='StudentAttendanceHistoryByDateRangeJson'),
    path('StudentAttendanceHistoryByDateRangeAndStudentJson',
         StudentAttendanceHistoryByDateRangeAndStudentJson.as_view(),
         name='StudentAttendanceHistoryByDateRangeAndStudentJson'),
    path('TakeTeacherAttendanceJson', TakeTeacherAttendanceJson.as_view(), name='TakeTeacherAttendanceJson'),
    path('add_staff_attendance_api', add_staff_attendance_api, name='add_staff_attendance_api'),
    path('StaffAttendanceHistoryByDateRangeJson', StaffAttendanceHistoryByDateRangeJson.as_view(),
         name='StaffAttendanceHistoryByDateRangeJson'),
    path('StaffAttendanceHistoryByDateRangeAndStaffJson', StaffAttendanceHistoryByDateRangeAndStaffJson.as_view(),
         name='StaffAttendanceHistoryByDateRangeAndStaffJson'),

    # user Fee
    path('FeeByStudentJson', FeeByStudentJson.as_view(), name='FeeByStudentJson'),
    path('add_student_fee_api', add_student_fee_api, name='add_student_fee_api'),
    path('StudentFeeDetailsByClassJson', StudentFeeDetailsByClassJson.as_view(), name='StudentFeeDetailsByClassJson'),
    path('StudentFeeDetailsByStudentJson', StudentFeeDetailsByStudentJson.as_view(), name='StudentFeeDetailsByStudentJson'),

]
