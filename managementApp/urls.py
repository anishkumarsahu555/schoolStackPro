from django.urls import path
from .views import *

urlpatterns = [
    #admin
    path('home/', admin_home, name='admin_home'),
    path('school-detail/', school_detail, name='school_detail'),
    path('session-import/', manage_session_import, name='manage_session_import'),
    path('manage-class/', manage_class, name='manage_class'),

    # subjects
    path('manage_subjects/', manage_subjects, name='manage_subjects'),
    path('assign_subjects_to_class/', assign_subjects_to_class, name='assign_subjects_to_class'),
    path('assign_subjects_to_teacher/', assign_subjects_to_teacher, name='assign_subjects_to_teacher'),
    path('subject-notes/', manage_subject_notes, name='manage_subject_notes'),

    # Teacher
    path('add_teacher/', add_teacher, name='add_teacher'),
    path('edit_teacher/<int:id>/', edit_teacher, name='edit_teacher'),
    path('teacher_list/', teacher_list, name='teacher_list'),
    path('teacher_detail/<int:id>/', teacher_detail, name='teacher_detail'),

    # Student
    path('add_student/', add_student, name='add_student'),
    path('student_list/', student_list, name='student_list'),
    path('student_detail/<int:id>/', student_detail, name='student_detail'),
    path('edit_student/<int:id>/', edit_student_detail, name='edit_student_detail'),
    path('student_id_cards/', student_id_cards, name='student_id_cards'),
    path('student_id_card/<int:id>/', student_id_card_detail, name='student_id_card_detail'),

    # Exams
    path('manage_exams/', manage_exams, name='manage_exams'),
    path('assign_exams_to_class/', assign_exams_to_class, name='assign_exams_to_class'),
    path('manage_exam_timetable/', manage_exam_timetable, name='manage_exam_timetable'),
    path('manage_exam_timetable_preview/', manage_exam_timetable_preview, name='manage_exam_timetable_preview'),

    # Attendance
    path('student_attendance/', student_attendance, name='student_attendance'),
    path('student_attendance_history/', student_attendance_history, name='student_attendance_history'),
    path('staff_attendance/', staff_attendance, name='staff_attendance'),
    path('staff_attendance_history/', staff_attendance_history, name='staff_attendance_history'),

    # Fee
    path('student_fee/', student_fee, name='student_fee'),
    path('student_fee_details/', student_fee_details, name='student_fee_details'),
    path('finance/', finance_dashboard, name='finance_dashboard'),
    path('finance/receipts/', manage_receipts, name='manage_receipts'),
    path('finance/reports/', finance_reports, name='finance_reports'),
    path('finance/controls/', finance_controls, name='finance_controls'),
    path('finance/settings/', finance_settings, name='finance_settings'),
    path('finance/audit-trail/', finance_audit_trail, name='finance_audit_trail'),
    path('finance/reconciliation/', finance_reconciliation, name='finance_reconciliation'),
    path('finance/payroll/', finance_payroll, name='finance_payroll'),
    path('finance/vendors/', manage_vendors, name='manage_vendors'),
    path('finance/vendor-payables/', vendor_payables, name='vendor_payables'),
    path('finance/vendor-statement/', vendor_statement, name='vendor_statement'),
    path('finance/student-charges/', manage_student_charges, name='manage_student_charges'),
    path('finance/fee-heads/', manage_fee_heads, name='manage_fee_heads'),
    path('finance/student-ledger/', student_finance_ledger, name='student_finance_ledger'),
    path('finance/receipt/<int:id>/', finance_receipt_detail, name='finance_receipt_detail'),
    path('finance/expense-vouchers/', manage_expense_vouchers, name='manage_expense_vouchers'),
    path('finance/cash-bank-book/', cash_bank_book, name='cash_bank_book'),

    #marks
    path('student_marks/', student_marks, name='student_marks'),
    path('exam_marks_details/', exam_marks_details, name='exam_marks_details'),
    path('progress_report_cards/', progress_report_cards, name='progress_report_cards'),

    #events
    path('manage_event/', manage_event, name='manage_event'),
    path('manage_event_type/', manage_event_type, name='manage_event_type'),
    path('manage_leave_types/', manage_leave_types, name='manage_leave_types'),
    path('manage_leave_applications/', manage_leave_applications, name='manage_leave_applications'),
    # path('event_list/', event_list, name='event_list'),

    #parents
    path('manage_parents/', manage_parents, name='manage_parents'),
    path('parent_detail/<int:id>/', parent_detail, name='parent_detail'),
    path('edit_parent/<int:id>/', edit_parent, name='edit_parent'),

]
