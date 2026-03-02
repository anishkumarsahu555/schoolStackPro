from django.urls import path

from .views import (
    teacher_home,
    teacher_school_detail,
    teacher_students_list,
    teacher_student_detail,
    teacher_assigned_subjects,
    teacher_student_attendance,
    teacher_attendance_history,
    teacher_manage_event,
    teacher_exam_timetable,
    teacher_add_marks,
    teacher_marks_details,
    teacher_progress_report_cards,
    teacher_assigned_class,
    teacher_leave_applications,
)

urlpatterns = [
    path('', teacher_home, name='teacher_root'),
    path('home/', teacher_home, name='teacher_home'),
    path('school-detail/', teacher_school_detail, name='teacher_school_detail'),
    path('students/', teacher_students_list, name='teacher_students_list'),
    path('assigned-class/', teacher_assigned_class, name='teacher_assigned_class'),
    path('assigned-subjects/', teacher_assigned_subjects, name='teacher_assigned_subjects'),
    path('student-attendance/', teacher_student_attendance, name='teacher_student_attendance'),
    path('attendance-history/', teacher_attendance_history, name='teacher_attendance_history'),
    path('manage-event/', teacher_manage_event, name='teacher_manage_event'),
    path('leave-applications/', teacher_leave_applications, name='teacher_leave_applications'),
    path('exam-timetable/', teacher_exam_timetable, name='teacher_exam_timetable'),
    path('add-marks/', teacher_add_marks, name='teacher_add_marks'),
    path('marks-details/', teacher_marks_details, name='teacher_marks_details'),
    path('progress-report-cards/', teacher_progress_report_cards, name='teacher_progress_report_cards'),
    path('students/detail/<int:id>/', teacher_student_detail, name='teacher_student_detail'),
]
