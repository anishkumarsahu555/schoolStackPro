from functools import wraps

from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect

from homeApp.models import SchoolSession
from homeApp.session_utils import build_current_session_payload, build_session_list_item


OWNER_GROUPS = {'Admin', 'Owner'}
STAFF_GROUP = 'Teaching'

MANAGEMENT_ACTIONS = (
    ('view', 'View'),
    ('add', 'Add'),
    ('edit', 'Edit'),
    ('delete', 'Delete'),
    ('approve', 'Approve'),
    ('report', 'Report'),
)

ACTION_FIELD_MAP = {
    'view': 'canView',
    'add': 'canAdd',
    'edit': 'canEdit',
    'delete': 'canDelete',
    'approve': 'canApprove',
    'report': 'canReport',
    'export': 'canReport',
}

MANAGEMENT_MODULES = (
    ('dashboard', 'Dashboard', 'tachometer alternate'),
    ('school_settings', 'School Settings', 'school'),
    ('classes', 'Classes', 'chalkboard'),
    ('timetable', 'Timetable', 'calendar alternate outline'),
    ('staff', 'Teachers & Staff', 'chalkboard teacher'),
    ('subjects', 'Subjects', 'book'),
    ('students', 'Students', 'users'),
    ('certificates', 'Certificates', 'certificate'),
    ('parents', 'Parents', 'users'),
    ('attendance', 'Attendance', 'tasks'),
    ('fees', 'Fees', 'hand holding usd'),
    ('exams', 'Exams', 'clipboard outline'),
    ('marks', 'Marks', 'edit outline'),
    ('events', 'Events', 'calendar plus outline'),
    ('holidays', 'Holidays', 'calendar alternate outline'),
    ('leave', 'Leave', 'calendar check outline'),
    ('finance', 'Finance & Accounts', 'chart bar'),
    ('communication', 'Communication', 'comments outline'),
    ('library', 'Library', 'book'),
    ('transport', 'Transport', 'bus'),
    ('hostel', 'Hostel', 'home'),
    ('audit', 'Audit Logs', 'history'),
    ('access_control', 'Access Control', 'user shield'),
)

MODULE_LABELS = {key: label for key, label, icon in MANAGEMENT_MODULES}
MODULE_ICONS = {key: icon for key, label, icon in MANAGEMENT_MODULES}

SYSTEM_ROLE_PRESETS = {
    'Principal': {
        'dashboard': ['view'],
        'school_settings': ['view', 'edit'],
        'classes': ['view', 'add', 'edit'],
        'timetable': ['view', 'add', 'edit', 'report'],
        'staff': ['view', 'add', 'edit', 'report'],
        'subjects': ['view', 'add', 'edit'],
        'students': ['view', 'add', 'edit', 'report'],
        'certificates': ['view', 'add', 'edit', 'approve', 'report'],
        'parents': ['view', 'add', 'edit'],
        'attendance': ['view', 'add', 'edit', 'report'],
        'fees': ['view', 'report'],
        'exams': ['view', 'add', 'edit'],
        'marks': ['view', 'add', 'edit', 'approve', 'report'],
        'events': ['view', 'add', 'edit'],
        'holidays': ['view', 'add', 'edit'],
        'leave': ['view', 'approve'],
        'finance': ['view', 'approve', 'report'],
        'communication': ['view', 'add', 'approve'],
        'library': ['view'],
        'transport': ['view'],
        'hostel': ['view'],
        'audit': ['view'],
    },
    'Accountant': {
        'dashboard': ['view'],
        'students': ['view'],
        'parents': ['view'],
        'fees': ['view', 'add', 'edit', 'report'],
        'finance': ['view', 'add', 'edit', 'approve', 'report'],
        'staff': ['view'],
    },
    'Receptionist': {
        'dashboard': ['view'],
        'students': ['view', 'add', 'edit'],
        'parents': ['view', 'add', 'edit'],
        'attendance': ['view', 'add'],
        'events': ['view'],
        'holidays': ['view'],
    },
    'Exam Controller': {
        'dashboard': ['view'],
        'classes': ['view'],
        'subjects': ['view'],
        'students': ['view'],
        'exams': ['view', 'add', 'edit', 'report'],
        'marks': ['view', 'add', 'edit', 'approve', 'report'],
    },
    'Librarian': {
        'dashboard': ['view'],
        'students': ['view'],
        'staff': ['view'],
        'library': ['view', 'add', 'edit', 'delete', 'report'],
    },
    'Transport Manager': {
        'dashboard': ['view'],
        'students': ['view'],
        'transport': ['view', 'add', 'edit', 'delete', 'report'],
    },
    'Hostel Warden': {
        'dashboard': ['view'],
        'students': ['view'],
        'parents': ['view'],
        'hostel': ['view', 'add', 'edit', 'delete', 'report'],
    },
    'Data Entry Operator': {
        'dashboard': ['view'],
        'classes': ['view'],
        'staff': ['view', 'add', 'edit'],
        'students': ['view', 'add', 'edit'],
        'parents': ['view', 'add', 'edit'],
        'subjects': ['view'],
    },
    'Read Only': {module: ['view'] for module, label, icon in MANAGEMENT_MODULES if module != 'access_control'},
}

URL_PERMISSION_MAP = {
    'admin_home': ('dashboard', 'view'),
    'school_detail': ('school_settings', 'view'),
    'manage_session_import': ('school_settings', 'edit'),
    'manage_class': ('classes', 'view'),
    'manage_school_timetable': ('timetable', 'view'),
    'school_timetable_pdf': ('timetable', 'report'),
    'teacher_school_timetable_pdf': ('timetable', 'report'),
    'add_teacher': ('staff', 'add'),
    'edit_teacher': ('staff', 'edit'),
    'teacher_list': ('staff', 'view'),
    'teacher_detail': ('staff', 'view'),
    'manage_staff_access': ('access_control', 'view'),
    'edit_staff_role': ('access_control', 'edit'),
    'manage_subjects': ('subjects', 'view'),
    'assign_subjects_to_class': ('subjects', 'edit'),
    'assign_subjects_to_teacher': ('subjects', 'edit'),
    'manage_subject_notes': ('subjects', 'view'),
    'add_student': ('students', 'add'),
    'student_list': ('students', 'view'),
    'student_detail': ('students', 'view'),
    'edit_student_detail': ('students', 'edit'),
    'student_id_cards': ('students', 'report'),
    'student_id_card_design': ('students', 'edit'),
    'student_id_card_detail': ('students', 'report'),
    'manage_parents': ('parents', 'view'),
    'parent_detail': ('parents', 'view'),
    'edit_parent': ('parents', 'edit'),
    'student_attendance': ('attendance', 'add'),
    'student_attendance_history': ('attendance', 'view'),
    'staff_attendance': ('attendance', 'add'),
    'staff_attendance_history': ('attendance', 'view'),
    'student_fee': ('fees', 'add'),
    'student_fee_details': ('fees', 'view'),
    'finance_dashboard': ('finance', 'view'),
    'manage_receipts': ('finance', 'add'),
    'finance_receipt_detail': ('finance', 'view'),
    'manage_student_charges': ('finance', 'edit'),
    'manage_fee_heads': ('finance', 'edit'),
    'money_ledger': ('finance', 'view'),
    'student_finance_ledger': ('finance', 'view'),
    'cash_bank_book': ('finance', 'view'),
    'manage_expense_vouchers': ('finance', 'add'),
    'manage_vendors': ('finance', 'edit'),
    'vendor_payables': ('finance', 'view'),
    'vendor_statement': ('finance', 'view'),
    'finance_payroll': ('finance', 'view'),
    'finance_reports': ('finance', 'report'),
    'finance_reconciliation': ('finance', 'view'),
    'finance_audit_trail': ('finance', 'view'),
    'finance_settings': ('finance', 'edit'),
    'finance_controls': ('finance', 'edit'),
    'manage_exams': ('exams', 'view'),
    'assign_exams_to_class': ('exams', 'edit'),
    'manage_exam_timetable': ('exams', 'edit'),
    'manage_exam_timetable_preview': ('exams', 'view'),
    'student_marks': ('marks', 'add'),
    'exam_marks_details': ('marks', 'view'),
    'progress_report_cards': ('marks', 'report'),
    'manage_event': ('events', 'view'),
    'manage_event_type': ('events', 'edit'),
    'manage_holidays': ('holidays', 'view'),
    'manage_leave_types': ('leave', 'edit'),
    'manage_leave_applications': ('leave', 'approve'),
    'audit_manager': ('audit', 'view'),
}

API_PERMISSION_MAP = {
    'get_school_detail_api': ('school_settings', 'view'),
    'update_school_detail_api': ('school_settings', 'edit'),
    'get_session_import_meta_api': ('school_settings', 'view'),
    'preview_session_import_api': ('school_settings', 'view'),
    'run_session_import_api': ('school_settings', 'edit'),
    'audit_log_list_api': ('audit', 'view'),
    'audit_log_detail_api': ('audit', 'view'),
    'AuditLogListJson': ('audit', 'view'),

    'add_class': ('classes', 'add'),
    'class_list': ('classes', 'view'),
    'get_class_detail': ('classes', 'view'),
    'delete_class': ('classes', 'delete'),
    'update_class': ('classes', 'edit'),

    'add_subject': ('subjects', 'add'),
    'delete_subject': ('subjects', 'delete'),
    'get_subject_detail': ('subjects', 'view'),
    'edit_subject': ('subjects', 'edit'),
    'SubjectListJson': ('subjects', 'view'),
    'add_subject_to_class': ('subjects', 'edit'),
    'delete_assign_subject_to_class': ('subjects', 'delete'),
    'get_assigned_subject_to_class_detail': ('subjects', 'view'),
    'update_subject_to_class': ('subjects', 'edit'),
    'get_subjects_to_class_assign_list_api': ('subjects', 'view'),
    'get_subjects_to_class_assign_list_with_given_class_api': ('subjects', 'view'),
    'AssignSubjectToClassListJson': ('subjects', 'view'),
    'add_subject_to_teacher': ('subjects', 'edit'),
    'delete_assign_teacher_to_subject': ('subjects', 'delete'),
    'get_assigned_subject_to_teacher_detail': ('subjects', 'view'),
    'update_subject_to_teacher': ('subjects', 'edit'),
    'AssignSubjectToTeacherListJson': ('subjects', 'view'),
    'get_management_subject_note_filter_meta_api': ('subjects', 'view'),
    'get_management_subject_note_list_api': ('subjects', 'view'),
    'get_management_subject_note_detail_api': ('subjects', 'view'),
    'toggle_management_subject_note_publish_api': ('subjects', 'approve'),

    'get_school_timetable_meta_api': ('timetable', 'view'),
    'get_school_timetable_api': ('timetable', 'view'),
    'save_school_timetable_settings_api': ('timetable', 'edit'),
    'copy_school_timetable_from_class_api': ('timetable', 'edit'),
    'copy_school_timetable_day_api': ('timetable', 'edit'),
    'get_teacher_school_timetable_api': ('timetable', 'view'),
    'save_school_timetable_entry_api': ('timetable', 'edit'),
    'validate_school_timetable_api': ('timetable', 'view'),
    'publish_school_timetable_api': ('timetable', 'approve'),
    'unpublish_school_timetable_api': ('timetable', 'approve'),

    'add_teacher_api': ('staff', 'add'),
    'delete_teacher': ('staff', 'delete'),
    'get_teacher_list_api': ('staff', 'view'),
    'TeacherListJson': ('staff', 'view'),
    'update_teacher_api': ('staff', 'edit'),

    'student_import_registration_suggestions_api': ('students', 'view'),
    'import_student_from_previous_session_api': ('students', 'add'),
    'add_student_api': ('students', 'add'),
    'delete_student': ('students', 'delete'),
    'get_student_list_by_class_api': ('students', 'view'),
    'StudentListJson': ('students', 'view'),
    'edit_student_api': ('students', 'edit'),
    'StudentIdCardRecordListJson': ('students', 'report'),
    'add_student_id_card_record_api': ('students', 'report'),
    'save_student_id_card_design_api': ('students', 'edit'),

    'add_exam': ('exams', 'add'),
    'delete_exam': ('exams', 'delete'),
    'get_exam_detail': ('exams', 'view'),
    'edit_exam': ('exams', 'edit'),
    'get_exams_list_api': ('exams', 'view'),
    'ExamListJson': ('exams', 'view'),
    'add_exam_timetable': ('exams', 'edit'),
    'ExamTimeTableListJson': ('exams', 'view'),
    'get_exam_timetable_detail': ('exams', 'view'),
    'update_exam_timetable': ('exams', 'edit'),
    'delete_exam_timetable': ('exams', 'delete'),
    'add_exam_to_class': ('exams', 'edit'),
    'delete_assign_exam_to_class': ('exams', 'delete'),
    'get_assigned_exam_to_class_detail': ('exams', 'view'),
    'update_exam_to_class': ('exams', 'edit'),
    'get_exam_list_by_class_api': ('exams', 'view'),
    'AssignExamToClassListJson': ('exams', 'view'),

    'TakeStudentAttendanceByClassJson': ('attendance', 'view'),
    'add_student_attendance_by_class': ('attendance', 'add'),
    'add_student_attendance_bulk_by_class': ('attendance', 'add'),
    'StudentAttendanceHistoryByDateRangeJson': ('attendance', 'view'),
    'StudentAttendanceHistoryByDateRangeAndStudentJson': ('attendance', 'view'),
    'TakeTeacherAttendanceJson': ('attendance', 'view'),
    'add_staff_attendance_api': ('attendance', 'add'),
    'add_staff_attendance_bulk_api': ('attendance', 'add'),
    'StaffAttendanceHistoryByDateRangeJson': ('attendance', 'view'),
    'StaffAttendanceHistoryByDateRangeAndStaffJson': ('attendance', 'view'),
    'HolidayListJson': ('holidays', 'view'),
    'add_holiday_api': ('holidays', 'add'),
    'get_holiday_detail': ('holidays', 'view'),
    'update_holiday_api': ('holidays', 'edit'),
    'delete_holiday': ('holidays', 'delete'),

    'FeeByStudentJson': ('fees', 'view'),
    'add_student_fee_api': ('fees', 'add'),
    'StudentFeeDetailsByClassJson': ('fees', 'view'),
    'StudentFeeDetailsByStudentJson': ('fees', 'view'),

    'get_finance_account_options_api': ('finance', 'view'),
    'get_finance_settings_api': ('finance', 'view'),
    'upsert_finance_settings_api': ('finance', 'edit'),
    'get_finance_payment_mode_options_api': ('finance', 'view'),
    'get_vendor_list_api': ('finance', 'view'),
    'search_vendor_suggestions_api': ('finance', 'view'),
    'FinanceVendorListJson': ('finance', 'view'),
    'upsert_vendor_api': ('finance', 'edit'),
    'delete_vendor_api': ('finance', 'delete'),
    'get_vendor_payables_api': ('finance', 'view'),
    'finance_vendor_payables_rows_api': ('finance', 'view'),
    'finance_vendor_outstanding_voucher_rows_api': ('finance', 'view'),
    'get_vendor_statement_api': ('finance', 'view'),
    'finance_vendor_statement_rows_api': ('finance', 'view'),
    'get_receipt_charge_options_api': ('finance', 'view'),
    'get_receipt_refund_options_api': ('finance', 'view'),
    'FinanceFeeHeadListJson': ('finance', 'view'),
    'FinanceReceiptListJson': ('finance', 'view'),
    'FinanceStudentChargeListJson': ('finance', 'view'),
    'FinanceExpenseCategoryListJson': ('finance', 'view'),
    'FinanceExpenseVoucherListJson': ('finance', 'view'),
    'get_payment_receipt_list_api': ('finance', 'view'),
    'get_finance_dashboard_api': ('finance', 'view'),
    'get_finance_reports_api': ('finance', 'report'),
    'get_money_ledger_filter_options_api': ('finance', 'view'),
    'get_money_ledger_api': ('finance', 'view'),
    'money_ledger_rows_api': ('finance', 'view'),
    'get_finance_control_center_api': ('finance', 'view'),
    'upsert_finance_approval_rule_api': ('finance', 'edit'),
    'delete_finance_approval_rule_api': ('finance', 'delete'),
    'get_finance_audit_trail_api': ('finance', 'view'),
    'FinanceAuditTrailListJson': ('finance', 'view'),
    'get_finance_reconciliation_api': ('finance', 'view'),
    'finance_recon_receipt_rows_api': ('finance', 'view'),
    'finance_recon_charge_rows_api': ('finance', 'view'),
    'finance_recon_voucher_rows_api': ('finance', 'view'),
    'finance_recon_payroll_rows_api': ('finance', 'view'),
    'get_receipt_adjustment_history_api': ('finance', 'view'),
    'get_payroll_run_list_api': ('finance', 'view'),
    'get_payroll_run_detail_api': ('finance', 'view'),
    'create_manual_payment_receipt_api': ('finance', 'add'),
    'create_payment_refund_api': ('finance', 'add'),
    'upsert_finance_period_api': ('finance', 'edit'),
    'repair_finance_reconciliation_issue_api': ('finance', 'edit'),
    'create_payroll_run_api': ('finance', 'add'),
    'approve_expense_voucher_api': ('finance', 'approve'),
    'approve_payment_receipt_api': ('finance', 'approve'),
    'approve_payment_refund_api': ('finance', 'approve'),
    'approve_payroll_run_api': ('finance', 'approve'),
    'approve_payroll_payment_api': ('finance', 'approve'),
    'post_payroll_run_api': ('finance', 'approve'),
    'pay_payroll_line_api': ('finance', 'edit'),
    'reverse_payment_receipt_api': ('finance', 'approve'),
    'get_student_charge_list_api': ('finance', 'view'),
    'get_fee_head_list_api': ('finance', 'view'),
    'upsert_fee_head_api': ('finance', 'edit'),
    'delete_fee_head_api': ('finance', 'delete'),
    'get_student_finance_ledger_api': ('finance', 'view'),
    'finance_student_ledger_rows_api': ('finance', 'view'),
    'get_expense_category_list_api': ('finance', 'view'),
    'upsert_expense_category_api': ('finance', 'edit'),
    'delete_expense_category_api': ('finance', 'delete'),
    'get_expense_voucher_list_api': ('finance', 'view'),
    'upsert_expense_voucher_api': ('finance', 'edit'),
    'delete_expense_voucher_api': ('finance', 'delete'),
    'get_cash_bank_book_api': ('finance', 'view'),
    'finance_cash_bank_book_rows_api': ('finance', 'view'),

    'get_exam_component_type_list_api': ('marks', 'view'),
    'add_exam_component_type_api': ('marks', 'add'),
    'get_exam_subject_component_rules_api': ('marks', 'view'),
    'save_exam_subject_component_rules_api': ('marks', 'edit'),
    'MarksOfSubjectsByStudentJson': ('marks', 'view'),
    'add_subject_mark_api': ('marks', 'add'),
    'StudentMarksDetailsByClassAndExamJson': ('marks', 'view'),
    'StudentMarksDetailsByStudentJson': ('marks', 'view'),
    'publish_progress_report_api': ('marks', 'approve'),
    'set_progress_report_ready_state_api': ('marks', 'approve'),
    'management_upsert_term_remark_api': ('marks', 'edit'),

    'get_event_type_list_api': ('events', 'view'),
    'EventTypeListJson': ('events', 'view'),
    'add_event_type_api': ('events', 'add'),
    'get_event_type_detail': ('events', 'view'),
    'update_event_type_api': ('events', 'edit'),
    'delete_event_type': ('events', 'delete'),
    'add_event_api': ('events', 'add'),
    'EventListJson': ('events', 'view'),
    'delete_event': ('events', 'delete'),
    'get_event_detail': ('events', 'view'),
    'update_event_api': ('events', 'edit'),

    'ParentsListJson': ('parents', 'view'),
    'get_leave_type_list_api': ('leave', 'view'),
    'LeaveTypeListJson': ('leave', 'view'),
    'add_leave_type_api': ('leave', 'add'),
    'get_leave_type_detail': ('leave', 'view'),
    'update_leave_type_api': ('leave', 'edit'),
    'delete_leave_type': ('leave', 'delete'),
    'LeaveApplicationListJson': ('leave', 'view'),
    'review_leave_application_api': ('leave', 'approve'),
}

APP_URL_PERMISSION_MAP = {
    'certificateApp': {
        'dashboard': ('certificates', 'view'),
        'design_library': ('certificates', 'view'),
        'create_design': ('certificates', 'add'),
        'edit_design': ('certificates', 'edit'),
        'duplicate_design': ('certificates', 'edit'),
        'set_default_design': ('certificates', 'edit'),
        'design_quick_preview': ('certificates', 'view'),
        'design_detail': ('certificates', 'view'),
        'generator': ('certificates', 'add'),
        'generator_live_preview': ('certificates', 'view'),
        'issue_preview': ('certificates', 'view'),
        'issue_print': ('certificates', 'report'),
        'issue_download_pdf': ('certificates', 'report'),
        'issue_cancel': ('certificates', 'delete'),
        'issue_reissue': ('certificates', 'add'),
        'verify_certificate': ('certificates', 'view'),
    },
    'libraryApp': {
        'dashboard': ('library', 'view'),
        'manage_books': ('library', 'view'),
        'manage_categories': ('library', 'view'),
        'manage_authors': ('library', 'view'),
        'manage_publishers': ('library', 'view'),
        'manage_copies': ('library', 'view'),
        'manage_members': ('library', 'view'),
        'member_cards': ('library', 'report'),
        'member_card_design': ('library', 'edit'),
        'issue_book': ('library', 'add'),
        'issue_history': ('library', 'view'),
        'return_book': ('library', 'edit'),
        'manage_reservations': ('library', 'view'),
        'manage_fines': ('library', 'view'),
        'settings': ('library', 'edit'),
        'reports': ('library', 'report'),
    },
    'transportApp': {
        'dashboard': ('transport', 'view'),
        'manage_routes': ('transport', 'view'),
        'manage_vehicles': ('transport', 'view'),
        'manage_drivers': ('transport', 'view'),
        'manage_assignments': ('transport', 'view'),
        'manage_fee_mapping': ('transport', 'view'),
        'manage_fee_tracking': ('transport', 'view'),
        'manage_reports': ('transport', 'report'),
    },
    'hostelApp': {
        'dashboard': ('hostel', 'view'),
        'manage_admissions': ('hostel', 'view'),
        'manage_buildings': ('hostel', 'view'),
        'manage_rooms': ('hostel', 'view'),
        'manage_beds': ('hostel', 'view'),
        'manage_assignments': ('hostel', 'view'),
        'manage_fee_mapping': ('hostel', 'view'),
        'manage_fee_tracking': ('hostel', 'view'),
        'manage_reports': ('hostel', 'report'),
    },
}

APP_API_PERMISSION_MAP = {
    'certificateAppAPI': {
        'get_certificate_generator_meta_api': ('certificates', 'view'),
        'create_certificate_issue_api': ('certificates', 'add'),
        'create_certificate_issues_bulk_api': ('certificates', 'add'),
    },
    'libraryAppAPI': {
        'dashboard_summary': ('library', 'view'),
        'library_options_api': ('library', 'view'),
        'available_copies_api': ('library', 'view'),
        'member_eligibility_api': ('library', 'view'),
        'settings_detail_api': ('library', 'view'),
        'settings_api': ('library', 'edit'),
        'detail_api': ('library', 'view'),
        'issue_history_api': ('library', 'view'),
        'issue_detail_api': ('library', 'view'),
        'delete_api': ('library', 'delete'),
        'library_report_csv': ('library', 'report'),
        'CategoryListJson': ('library', 'view'),
        'AuthorListJson': ('library', 'view'),
        'PublisherListJson': ('library', 'view'),
        'BookListJson': ('library', 'view'),
        'BookCopyListJson': ('library', 'view'),
        'MemberListJson': ('library', 'view'),
        'IssueListJson': ('library', 'view'),
        'ReservationListJson': ('library', 'view'),
        'FineListJson': ('library', 'view'),
        'OverdueReportListJson': ('library', 'report'),
        'category_api': ('library', 'edit'),
        'author_api': ('library', 'edit'),
        'publisher_api': ('library', 'edit'),
        'book_api': ('library', 'edit'),
        'copy_api': ('library', 'edit'),
        'bulk_copy_api': ('library', 'add'),
        'member_api': ('library', 'edit'),
        'issue_book_api': ('library', 'add'),
        'issue_reservation_api': ('library', 'add'),
        'return_book_api': ('library', 'edit'),
        'renew_book_api': ('library', 'edit'),
        'reservation_api': ('library', 'edit'),
        'fine_api': ('library', 'edit'),
        'pay_fine_api': ('library', 'edit'),
        'waive_fine_api': ('library', 'approve'),
        'save_member_card_design_api': ('library', 'edit'),
    },
    'transportAppAPI': {
        'dashboard_summary': ('transport', 'view'),
        'transport_report_summary_api': ('transport', 'report'),
        'passenger_manifest_csv': ('transport', 'report'),
        'RouteListJson': ('transport', 'view'),
        'StopListJson': ('transport', 'view'),
        'DriverListJson': ('transport', 'view'),
        'FeeMappingListJson': ('transport', 'view'),
        'VehicleListJson': ('transport', 'view'),
        'AssignmentListJson': ('transport', 'view'),
        'FeeRecordListJson': ('transport', 'view'),
        'routes_api': {'GET': ('transport', 'view'), 'POST': ('transport', 'edit')},
        'route_detail_api': ('transport', 'view'),
        'delete_route_api': ('transport', 'delete'),
        'stops_api': {'GET': ('transport', 'view'), 'POST': ('transport', 'edit')},
        'stop_detail_api': ('transport', 'view'),
        'delete_stop_api': ('transport', 'delete'),
        'drivers_api': {'GET': ('transport', 'view'), 'POST': ('transport', 'edit')},
        'driver_detail_api': ('transport', 'view'),
        'delete_driver_api': ('transport', 'delete'),
        'fee_mappings_api': {'GET': ('transport', 'view'), 'POST': ('transport', 'edit')},
        'fee_mapping_detail_api': ('transport', 'view'),
        'delete_fee_mapping_api': ('transport', 'delete'),
        'vehicles_api': {'GET': ('transport', 'view'), 'POST': ('transport', 'edit')},
        'vehicle_detail_api': ('transport', 'view'),
        'delete_vehicle_api': ('transport', 'delete'),
        'assignments_api': {'GET': ('transport', 'view'), 'POST': ('transport', 'edit')},
        'assignment_detail_api': ('transport', 'view'),
        'delete_assignment_api': ('transport', 'delete'),
        'generate_transport_fee_records_api': ('transport', 'add'),
        'transport_fee_record_detail_api': ('transport', 'view'),
        'record_transport_fee_payment_api': ('transport', 'edit'),
        'update_transport_fee_status_api': ('transport', 'edit'),
        'transport_options_api': ('transport', 'view'),
        'assignee_options_api': ('transport', 'view'),
    },
    'hostelAppAPI': {
        'dashboard_summary': ('hostel', 'view'),
        'hostel_report_summary_api': ('hostel', 'report'),
        'resident_manifest_csv': ('hostel', 'report'),
        'AdmissionListJson': ('hostel', 'view'),
        'BuildingListJson': ('hostel', 'view'),
        'FloorListJson': ('hostel', 'view'),
        'RoomTypeListJson': ('hostel', 'view'),
        'RoomListJson': ('hostel', 'view'),
        'BedListJson': ('hostel', 'view'),
        'AssignmentListJson': ('hostel', 'view'),
        'FeeMappingListJson': ('hostel', 'view'),
        'FeeRecordListJson': ('hostel', 'view'),
        'hostel_options_api': ('hostel', 'view'),
        'student_options_api': ('hostel', 'view'),
        'resident_options_api': ('hostel', 'view'),
        'admissions_api': {'GET': ('hostel', 'view'), 'POST': ('hostel', 'edit')},
        'admission_detail_api': ('hostel', 'view'),
        'delete_admission_api': ('hostel', 'delete'),
        'buildings_api': {'GET': ('hostel', 'view'), 'POST': ('hostel', 'edit')},
        'building_detail_api': ('hostel', 'view'),
        'delete_building_api': ('hostel', 'delete'),
        'floors_api': {'GET': ('hostel', 'view'), 'POST': ('hostel', 'edit')},
        'floor_detail_api': ('hostel', 'view'),
        'delete_floor_api': ('hostel', 'delete'),
        'room_types_api': {'GET': ('hostel', 'view'), 'POST': ('hostel', 'edit')},
        'room_type_detail_api': ('hostel', 'view'),
        'delete_room_type_api': ('hostel', 'delete'),
        'rooms_api': {'GET': ('hostel', 'view'), 'POST': ('hostel', 'edit')},
        'room_detail_api': ('hostel', 'view'),
        'delete_room_api': ('hostel', 'delete'),
        'beds_api': {'GET': ('hostel', 'view'), 'POST': ('hostel', 'edit')},
        'bed_detail_api': ('hostel', 'view'),
        'delete_bed_api': ('hostel', 'delete'),
        'assignments_api': {'GET': ('hostel', 'view'), 'POST': ('hostel', 'edit')},
        'assignment_detail_api': ('hostel', 'view'),
        'delete_assignment_api': ('hostel', 'delete'),
        'fee_mappings_api': {'GET': ('hostel', 'view'), 'POST': ('hostel', 'edit')},
        'fee_mapping_detail_api': ('hostel', 'view'),
        'delete_fee_mapping_api': ('hostel', 'delete'),
        'generate_hostel_fee_records_api': ('hostel', 'add'),
        'hostel_fee_record_detail_api': ('hostel', 'view'),
        'record_hostel_fee_payment_api': ('hostel', 'edit'),
        'update_hostel_fee_status_api': ('hostel', 'edit'),
    },
    'chatApp': {
        'inbox': {'GET': ('communication', 'view'), 'POST': ('communication', 'add')},
        'room': {'GET': ('communication', 'view'), 'POST': ('communication', 'add')},
        'moderation': {'GET': ('communication', 'approve'), 'POST': ('communication', 'approve')},
        'moderation_export': ('communication', 'report'),
        'unread_summary_api': ('communication', 'view'),
        'room_messages_api': ('communication', 'view'),
        'room_typing_api': ('communication', 'add'),
        'room_search_api': ('communication', 'view'),
        'send_message_api': ('communication', 'add'),
        'edit_message_api': ('communication', 'edit'),
        'delete_message_api': ('communication', 'delete'),
        'message_reaction_api': ('communication', 'add'),
        'message_pin_api': ('communication', 'approve'),
        'message_save_api': ('communication', 'add'),
        'message_forward_api': ('communication', 'add'),
        'notification_preference_api': ('communication', 'edit'),
        'participant_add_api': ('communication', 'add'),
        'participant_update_api': ('communication', 'approve'),
        'room_messages_export': ('communication', 'report'),
    },
}

CHAT_POST_ACTION_PERMISSION_MAP = {
    'create_direct': ('communication', 'add'),
    'create_group': ('communication', 'add'),
    'send_message': ('communication', 'add'),
    'edit_message': ('communication', 'edit'),
    'delete_message': ('communication', 'delete'),
    'participant_update': ('communication', 'approve'),
    'participant_add': ('communication', 'add'),
    'toggle_notifications': ('communication', 'edit'),
    'report_message': ('communication', 'report'),
}

NAMESPACE_PERMISSION_MAP = {
    'certificateApp': ('certificates', 'view'),
    'certificateAppAPI': ('certificates', 'edit'),
    'libraryApp': ('library', 'view'),
    'libraryAppAPI': ('library', 'edit'),
    'transportApp': ('transport', 'view'),
    'transportAppAPI': ('transport', 'edit'),
    'hostelApp': ('hostel', 'view'),
    'hostelAppAPI': ('hostel', 'edit'),
    'chatApp': ('communication', 'view'),
}


def _models():
    from managementApp.models import StaffAccess, StaffRole, StaffRolePermission, TeacherDetail
    return StaffAccess, StaffRole, StaffRolePermission, TeacherDetail


def user_group_names(user):
    if not getattr(user, 'is_authenticated', False):
        return set()
    return set(user.groups.values_list('name', flat=True))


def is_owner_or_admin(user):
    return bool(user_group_names(user) & OWNER_GROUPS) or getattr(user, 'is_superuser', False)


def get_staff_profile(user):
    if not getattr(user, 'is_authenticated', False):
        return None
    StaffAccess, StaffRole, StaffRolePermission, TeacherDetail = _models()
    return TeacherDetail.objects.select_related('schoolID', 'sessionID', 'userID').filter(
        userID_id=user.id,
        isDeleted=False,
    ).order_by('-datetime').first()


def get_staff_access(user):
    staff = get_staff_profile(user)
    if not staff:
        return None
    StaffAccess, StaffRole, StaffRolePermission, TeacherDetail = _models()
    return StaffAccess.objects.select_related('staffID', 'roleID').filter(
        staffID=staff,
        isManagementAccessEnabled=True,
        roleID__isActive=True,
        roleID__isDeleted=False,
    ).first()


def user_has_management_access(user):
    return is_owner_or_admin(user) or bool(get_staff_access(user))


def ensure_staff_login_group(user):
    if not user:
        return
    group, created = Group.objects.get_or_create(name=STAFF_GROUP)
    if not group.user_set.filter(id=user.pk).exists():
        group.user_set.add(user.pk)


def has_management_permission(user, module_key, action='view'):
    if is_owner_or_admin(user):
        return True
    action_field = ACTION_FIELD_MAP.get(action)
    if not action_field:
        return False
    access = get_staff_access(user)
    if not access or not access.roleID_id:
        return False
    StaffAccess, StaffRole, StaffRolePermission, TeacherDetail = _models()
    if action == 'report':
        return StaffRolePermission.objects.filter(
            roleID_id=access.roleID_id,
            moduleKey=module_key,
        ).filter(canReport=True).exists()
    return StaffRolePermission.objects.filter(
        roleID_id=access.roleID_id,
        moduleKey=module_key,
        **{action_field: True},
    ).exists()


def get_user_permission_flags(user):
    flags = {module: {action: False for action, label in MANAGEMENT_ACTIONS} for module, label, icon in MANAGEMENT_MODULES}
    if is_owner_or_admin(user):
        for module in flags:
            for action in flags[module]:
                flags[module][action] = True
        return flags
    access = get_staff_access(user)
    if not access or not access.roleID_id:
        return flags
    StaffAccess, StaffRole, StaffRolePermission, TeacherDetail = _models()
    for perm in StaffRolePermission.objects.filter(roleID_id=access.roleID_id):
        if perm.moduleKey not in flags:
            continue
        for action, label in MANAGEMENT_ACTIONS:
            flags[perm.moduleKey][action] = bool(getattr(perm, ACTION_FIELD_MAP[action], False))
    return flags


def module_visible(flags, module_key):
    module_flags = flags.get(module_key, {})
    return any(module_flags.values())


def bootstrap_staff_roles(school_id, user=None):
    StaffAccess, StaffRole, StaffRolePermission, TeacherDetail = _models()
    created_roles = []
    for role_name, modules in SYSTEM_ROLE_PRESETS.items():
        role, created = StaffRole.objects.get_or_create(
            schoolID_id=school_id,
            name=role_name,
            isDeleted=False,
            defaults={
                'description': f'Default {role_name} access profile',
                'isSystemRole': True,
                'updatedByUserID': user,
                'lastEditedBy': getattr(user, 'username', None) if user else None,
            },
        )
        if created:
            created_roles.append(role)
        for module_key, actions in modules.items():
            values = {field: False for field in ACTION_FIELD_MAP.values()}
            for action in actions:
                field = ACTION_FIELD_MAP.get(action)
                if field:
                    values[field] = True
            StaffRolePermission.objects.update_or_create(
                roleID=role,
                moduleKey=module_key,
                defaults=values,
            )
    return created_roles


def init_staff_management_session(request):
    staff = get_staff_profile(request.user)
    if not staff:
        return False
    current = SchoolSession.objects.filter(
        schoolID_id=staff.schoolID_id,
        isCurrent=True,
        isDeleted=False,
    ).order_by('-datetime').first()
    if not current and staff.sessionID_id:
        current = SchoolSession.objects.filter(pk=staff.sessionID_id, isDeleted=False).first()
    if not current:
        return False
    request.session['current_session'] = build_current_session_payload(current)
    sessions = SchoolSession.objects.filter(
        schoolID_id=current.schoolID_id,
        isDeleted=False,
    ).order_by('-datetime')
    request.session['session_list'] = [build_session_list_item(session) for session in sessions]
    return True


def _permission_for_method(permission, request_method=None):
    if isinstance(permission, dict):
        method = (request_method or 'GET').upper()
        return permission.get(method) or permission.get('DEFAULT') or permission.get('GET')
    return permission


def permission_for_resolver(resolver_match, request_method=None):
    if not resolver_match:
        return None
    namespace = resolver_match.namespace
    url_name = resolver_match.url_name
    if namespace in ('managementApp', 'managementAppAPI', 'managementAppCachedAPI'):
        permission = (
            URL_PERMISSION_MAP.get(url_name)
            or API_PERMISSION_MAP.get(url_name)
            or infer_permission_from_url_name(url_name)
            or ('dashboard', 'view')
        )
        return _permission_for_method(permission, request_method)
    namespace_url_map = APP_URL_PERMISSION_MAP.get(namespace)
    if namespace_url_map:
        permission = namespace_url_map.get(url_name)
        if permission:
            return _permission_for_method(permission, request_method)
    namespace_api_map = APP_API_PERMISSION_MAP.get(namespace)
    if namespace_api_map:
        permission = namespace_api_map.get(url_name)
        if permission:
            return _permission_for_method(permission, request_method)
    namespace_permission = NAMESPACE_PERMISSION_MAP.get(namespace)
    if namespace_permission and url_name and 'report' in url_name.lower():
        return namespace_permission[0], 'report'
    return namespace_permission


def infer_permission_from_url_name(url_name):
    name = (url_name or '').lower()
    action = 'view'
    if any(token in name for token in ('delete', 'remove')):
        action = 'delete'
    elif any(token in name for token in ('report', 'export', 'pdf', 'print', 'csv')):
        action = 'report'
    elif any(token in name for token in ('approve', 'approval')):
        action = 'approve'
    elif any(token in name for token in ('add', 'create', 'save', 'update', 'edit', 'upsert', 'bulk', 'copy', 'import', 'publish', 'toggle')):
        action = 'edit'

    module_tokens = (
        ('audit', 'audit'),
        ('session_import', 'school_settings'),
        ('school', 'school_settings'),
        ('class', 'classes'),
        ('standard', 'classes'),
        ('timetable', 'timetable'),
        ('teacher', 'staff'),
        ('staff', 'staff'),
        ('subject_note', 'subjects'),
        ('subject', 'subjects'),
        ('student_id_card', 'students'),
        ('student', 'students'),
        ('parent', 'parents'),
        ('attendance', 'attendance'),
        ('holiday', 'holidays'),
        ('leave', 'leave'),
        ('fee', 'fees'),
        ('receipt', 'finance'),
        ('finance', 'finance'),
        ('ledger', 'finance'),
        ('voucher', 'finance'),
        ('vendor', 'finance'),
        ('payroll', 'finance'),
        ('exam', 'exams'),
        ('mark', 'marks'),
        ('report_card', 'marks'),
        ('event', 'events'),
    )
    for token, module_key in module_tokens:
        if token in name:
            if action == 'edit' and any(token in name for token in ('add_', 'create')):
                action = 'add'
            return module_key, action
    return None


def management_permission_required(module_key, action='view'):
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if not getattr(request.user, 'is_authenticated', False):
                return redirect('homeApp:login_page')
            if not has_management_permission(request.user, module_key, action):
                raise PermissionDenied
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator


def can_pass_group_gate(request, groups):
    if any(request.user.groups.filter(name=group).exists() for group in groups):
        return True
    if STAFF_GROUP in groups and user_has_management_access(request.user):
        return True
    if set(groups).issubset(OWNER_GROUPS):
        permission = permission_for_resolver(getattr(request, 'resolver_match', None))
        if not permission:
            return False
        return has_management_permission(request.user, permission[0], permission[1])
    return False
