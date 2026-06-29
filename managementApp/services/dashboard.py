import json
from decimal import Decimal
from datetime import date, timedelta

from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncDate
from django.urls import reverse

from financeApp.models import ExpenseVoucher, FeeHead, PaymentReceipt, PayrollLine, StudentCharge
from homeApp.models import AuditLog
from managementApp.access_control import ACTION_FIELD_MAP, get_user_permission_flags, has_management_permission, is_owner_or_admin
from managementApp.models import (
    AssignExamToClass,
    AssignSubjectsToClass,
    AssignSubjectsToTeacher,
    Event,
    ExamTimeTable,
    LeaveApplication,
    MarkOfStudentsByExam,
    Parent,
    StaffAccess,
    StaffRole,
    Standard,
    Student,
    StudentAttendance,
    StudentExamComponentMark,
    StudentFee,
    Subjects,
    TeacherAttendance,
    TeacherDetail,
)


def _percentage(part, whole):
    if not whole:
        return 0
    return round((part / whole) * 100, 1)


def _money_total(queryset, field_name):
    total = queryset.aggregate(total=Sum(field_name)).get('total')
    if total is None:
        return Decimal('0.00')
    return Decimal(str(total))


def _can(user, module_key, action='view', permission_flags=None, full_access=False):
    if full_access:
        return True
    if permission_flags is not None:
        resolved_action = 'report' if action == 'export' else action
        if resolved_action not in ACTION_FIELD_MAP and resolved_action != 'view':
            return False
        return bool(permission_flags.get(module_key, {}).get(resolved_action, False))
    return has_management_permission(user, module_key, action)


def _filter_permission_items(user, items, permission_flags=None, full_access=False):
    if full_access:
        return items
    return [
        item for item in items
        if _can(user, item.get('module'), item.get('action', 'view'), permission_flags=permission_flags)
    ]


def _has_any(user, *permissions, permission_flags=None, full_access=False):
    return any(_can(user, module, action, permission_flags=permission_flags, full_access=full_access) for module, action in permissions)


def build_management_dashboard_context(request):
    current_session_id = request.session['current_session']['Id']
    current_school_id = request.session.get('current_session', {}).get('SchoolID')
    current_session_year = request.session.get('current_session', {}).get('currentSessionYear', 'N/A')
    user = request.user
    full_access = is_owner_or_admin(user)
    permission_flags = None if full_access else get_user_permission_flags(user)
    today = date.today()
    month_start = today.replace(day=1)
    next_week = today + timedelta(days=7)
    next_month = today + timedelta(days=30)

    student_qs = Student.objects.filter(isDeleted=False, sessionID_id=current_session_id)
    active_student_qs = student_qs.filter(isActive='Yes')
    teacher_qs = TeacherDetail.objects.filter(isDeleted=False, sessionID_id=current_session_id)
    active_teacher_qs = teacher_qs.filter(isActive='Yes')
    class_qs = Standard.objects.filter(isDeleted=False, sessionID_id=current_session_id)
    subject_qs = Subjects.objects.filter(isDeleted=False, sessionID_id=current_session_id)
    parent_qs = Parent.objects.filter(isDeleted=False, sessionID_id=current_session_id)
    student_attendance_qs = StudentAttendance.objects.filter(isDeleted=False, sessionID_id=current_session_id)
    teacher_attendance_qs = TeacherAttendance.objects.filter(isDeleted=False, sessionID_id=current_session_id)
    student_fee_qs = StudentFee.objects.filter(isDeleted=False, sessionID_id=current_session_id)
    charge_qs = StudentCharge.objects.filter(isDeleted=False, sessionID_id=current_session_id).exclude(status='cancelled')
    receipt_qs = PaymentReceipt.objects.filter(isDeleted=False, sessionID_id=current_session_id, status='confirmed')
    expense_qs = ExpenseVoucher.objects.filter(isDeleted=False, sessionID_id=current_session_id, approvalStatus='paid')
    payroll_line_qs = PayrollLine.objects.filter(payrollRunID__isDeleted=False, payrollRunID__sessionID_id=current_session_id)
    leave_qs = LeaveApplication.objects.filter(isDeleted=False, sessionID_id=current_session_id)
    exam_assignment_qs = AssignExamToClass.objects.select_related('examID', 'standardID').filter(isDeleted=False, sessionID_id=current_session_id)
    exam_timetable_qs = ExamTimeTable.objects.filter(isDeleted=False, sessionID_id=current_session_id)
    audit_qs = AuditLog.objects.select_related('content_type', 'userID').filter(sessionID_id=current_session_id)

    if current_school_id:
        charge_qs = charge_qs.filter(schoolID_id=current_school_id)
        receipt_qs = receipt_qs.filter(schoolID_id=current_school_id)
        expense_qs = expense_qs.filter(schoolID_id=current_school_id)
        payroll_line_qs = payroll_line_qs.filter(payrollRunID__schoolID_id=current_school_id)
        audit_qs = audit_qs.filter(schoolID_id=current_school_id)

    student_counts = student_qs.aggregate(
        total=Count('id'),
        active=Count('id', filter=Q(isActive='Yes')),
        missing_parent=Count('id', filter=Q(parentID__isnull=True)),
        missing_contact=Count('id', filter=Q(phoneNumber__isnull=True) | Q(phoneNumber='') | Q(email__isnull=True) | Q(email='')),
        missing_photo=Count('id', filter=Q(photo__isnull=True) | Q(photo='')),
        admissions_this_month=Count('id', filter=Q(datetime__date__gte=month_start, datetime__date__lte=today)),
        leaving_this_month=Count('id', filter=Q(dateOfLeaving__gte=month_start, dateOfLeaving__lte=today)),
    )
    teacher_counts = teacher_qs.aggregate(
        total=Count('id'),
        active=Count('id', filter=Q(isActive='Yes')),
    )
    total_students = student_counts.get('total') or 0
    total_teachers = teacher_counts.get('total') or 0
    total_classes = class_qs.count()
    total_subjects = subject_qs.count()
    total_parents = parent_qs.count()
    active_students = student_counts.get('active') or 0
    active_teachers = teacher_counts.get('active') or 0

    student_attendance_today = student_attendance_qs.filter(attendanceDate__date=today, isHoliday=False)
    teacher_attendance_today = teacher_attendance_qs.filter(attendanceDate__date=today, isHoliday=False)
    student_attendance_counts = student_attendance_today.aggregate(
        total=Count('id'),
        present=Count('id', filter=Q(attendanceStatus='present') | Q(isPresent=True)),
        absent=Count('id', filter=Q(attendanceStatus='absent') | Q(isPresent=False, attendanceStatus__isnull=True)),
        leave=Count('id', filter=Q(attendanceStatus='leave')),
        classes_marked=Count('standardID_id', distinct=True),
    )
    teacher_attendance_counts = teacher_attendance_today.aggregate(
        total=Count('id'),
        present=Count('id', filter=Q(attendanceStatus='present') | Q(isPresent=True)),
        absent=Count('id', filter=Q(attendanceStatus='absent') | Q(isPresent=False, attendanceStatus__isnull=True)),
        leave=Count('id', filter=Q(attendanceStatus='leave')),
    )
    student_attendance_today_count = student_attendance_counts.get('total') or 0
    teacher_attendance_today_count = teacher_attendance_counts.get('total') or 0
    student_present_today = student_attendance_counts.get('present') or 0
    student_absent_today = student_attendance_counts.get('absent') or 0
    student_leave_today = student_attendance_counts.get('leave') or 0
    teacher_present_today = teacher_attendance_counts.get('present') or 0
    teacher_absent_today = teacher_attendance_counts.get('absent') or 0
    teacher_leave_today = teacher_attendance_counts.get('leave') or 0
    class_attendance_marked_today = student_attendance_counts.get('classes_marked') or 0
    classes_without_attendance = max(total_classes - class_attendance_marked_today, 0)

    upcoming_events = Event.objects.filter(
        isDeleted=False,
        sessionID_id=current_session_id,
        startDate__gte=today,
        startDate__lte=next_month,
    ).order_by('startDate')[:5]
    recent_students = student_qs.select_related('standardID').order_by('-datetime')[:5]
    recent_receipts = receipt_qs.select_related('studentID').order_by('-receiptDate', '-datetime')[:5]
    recent_audit_logs = audit_qs.order_by('-datetime')[:5]
    upcoming_exams = exam_assignment_qs.filter(
        startDate__gte=today,
        startDate__lte=next_month,
    ).order_by('startDate')[:5]
    pending_leave_count = leave_qs.filter(status='pending').count()
    pending_leaves = leave_qs.filter(status='pending').select_related('teacherID', 'studentID').order_by('startDate')[:5]

    class_distribution = list(
        student_qs.values('standardID__name').annotate(total=Count('id')).order_by('-total')[:8]
    )
    attendance_class_distribution = list(
        student_attendance_today.values('standardID__name')
        .annotate(
            present=Count('id', filter=Q(attendanceStatus='present') | Q(isPresent=True)),
            absent=Count('id', filter=Q(attendanceStatus='absent') | Q(isPresent=False, attendanceStatus__isnull=True)),
        )
        .order_by('standardID__name')[:8]
    )
    seven_day_labels = []
    seven_day_student_values = []
    seven_day_staff_values = []
    week_start = today - timedelta(days=6)
    student_attendance_by_day = {
        row['day']: row
        for row in student_attendance_qs.filter(attendanceDate__date__gte=week_start, attendanceDate__date__lte=today, isHoliday=False)
        .annotate(day=TruncDate('attendanceDate'))
        .values('day')
        .annotate(
            total=Count('id'),
            present=Count('id', filter=Q(attendanceStatus='present') | Q(isPresent=True)),
        )
    }
    staff_attendance_by_day = {
        row['day']: row
        for row in teacher_attendance_qs.filter(attendanceDate__date__gte=week_start, attendanceDate__date__lte=today, isHoliday=False)
        .annotate(day=TruncDate('attendanceDate'))
        .values('day')
        .annotate(
            total=Count('id'),
            present=Count('id', filter=Q(attendanceStatus='present') | Q(isPresent=True)),
        )
    }
    for day_offset in range(6, -1, -1):
        target_day = today - timedelta(days=day_offset)
        student_day = student_attendance_by_day.get(target_day, {})
        staff_day = staff_attendance_by_day.get(target_day, {})
        seven_day_labels.append(target_day.strftime('%d %b'))
        seven_day_student_values.append(_percentage(student_day.get('present', 0), student_day.get('total', 0)))
        seven_day_staff_values.append(_percentage(staff_day.get('present', 0), staff_day.get('total', 0)))

    student_fee_summary = student_fee_qs.aggregate(
        pending_total=Sum('amount', filter=Q(isPaid=False)),
        collected_month=Sum('amount', filter=Q(isPaid=True, payDate__gte=month_start, payDate__lte=today)),
        overdue_count=Count('id', filter=Q(isPaid=False, dueDate__lt=today)),
    )
    charge_summary = charge_qs.aggregate(
        pending_total=Sum('balanceAmount'),
        overdue_count=Count('id', filter=Q(balanceAmount__gt=0, dueDate__lt=today)),
    )
    receipt_summary = receipt_qs.aggregate(
        today_total=Sum('amountReceived', filter=Q(receiptDate=today)),
        month_total=Sum('amountReceived', filter=Q(receiptDate__gte=month_start, receiptDate__lte=today)),
    )
    expense_summary = expense_qs.aggregate(
        month_total=Sum('netAmount', filter=Q(voucherDate__gte=month_start, voucherDate__lte=today)),
    )
    payroll_summary = payroll_line_qs.aggregate(
        pending_total=Sum('netAmount', filter=~Q(paymentStatus='paid')),
    )
    legacy_fee_pending_total = Decimal(str(student_fee_summary.get('pending_total') or '0.00'))
    legacy_fee_collected_month = Decimal(str(student_fee_summary.get('collected_month') or '0.00'))
    charge_pending_total = Decimal(str(charge_summary.get('pending_total') or '0.00'))
    charge_overdue_count = charge_summary.get('overdue_count') or 0
    legacy_fee_overdue_count = student_fee_summary.get('overdue_count') or 0
    receipt_today_total = Decimal(str(receipt_summary.get('today_total') or '0.00'))
    receipt_month_total = Decimal(str(receipt_summary.get('month_total') or '0.00'))
    expense_month_total = Decimal(str(expense_summary.get('month_total') or '0.00'))
    payroll_pending_total = Decimal(str(payroll_summary.get('pending_total') or '0.00'))
    finance_pending_total = charge_pending_total or legacy_fee_pending_total
    finance_collected_month = receipt_month_total or legacy_fee_collected_month

    subject_class_assignment_summary = AssignSubjectsToClass.objects.filter(isDeleted=False, sessionID_id=current_session_id).aggregate(
        total=Count('id'),
        classes_with_subjects=Count('standardID_id', distinct=True),
    )
    subject_teacher_assignment_summary = AssignSubjectsToTeacher.objects.filter(isDeleted=False, sessionID_id=current_session_id).aggregate(
        total=Count('id'),
        teachers_with_subjects=Count('teacherID_id', distinct=True),
    )
    subject_class_assignment_count = subject_class_assignment_summary.get('total') or 0
    subject_teacher_assignment_count = subject_teacher_assignment_summary.get('total') or 0
    classes_with_subjects = subject_class_assignment_summary.get('classes_with_subjects') or 0
    teachers_with_subjects = subject_teacher_assignment_summary.get('teachers_with_subjects') or 0
    classes_without_subjects = max(total_classes - classes_with_subjects, 0)
    teachers_without_subjects = max(active_teachers - teachers_with_subjects, 0)
    upcoming_exam_count = exam_assignment_qs.filter(startDate__gte=today, startDate__lte=next_week).count()
    exam_timetable_entries = exam_timetable_qs.count()
    marks_entered_count = StudentExamComponentMark.objects.filter(isDeleted=False, sessionID_id=current_session_id).count() + MarkOfStudentsByExam.objects.filter(isDeleted=False, sessionID_id=current_session_id).count()

    students_missing_parent = student_counts.get('missing_parent') or 0
    students_missing_contact = student_counts.get('missing_contact') or 0
    students_missing_photo = student_counts.get('missing_photo') or 0
    admissions_this_month = student_counts.get('admissions_this_month') or 0
    leaving_this_month = student_counts.get('leaving_this_month') or 0
    staff_access_enabled = StaffAccess.objects.filter(staffID__sessionID_id=current_session_id, isManagementAccessEnabled=True).count()
    active_roles_count = StaffRole.objects.filter(isDeleted=False, isActive=True).filter(Q(schoolID_id=current_school_id) | Q(schoolID__isnull=True)).count()
    fee_head_count = FeeHead.objects.filter(isDeleted=False, sessionID_id=current_session_id).count()

    setup_checklist = _filter_permission_items(user, [
        {'label': 'Current session selected', 'value': current_session_year, 'complete': bool(current_session_id), 'url': reverse('managementApp:school_detail'), 'module': 'school_settings', 'action': 'view'},
        {'label': 'Classes created', 'value': total_classes, 'complete': total_classes > 0, 'url': reverse('managementApp:manage_class'), 'module': 'classes', 'action': 'view'},
        {'label': 'Subjects created', 'value': total_subjects, 'complete': total_subjects > 0, 'url': reverse('managementApp:manage_subjects'), 'module': 'subjects', 'action': 'view'},
        {'label': 'Students admitted', 'value': total_students, 'complete': total_students > 0, 'url': reverse('managementApp:student_list'), 'module': 'students', 'action': 'view'},
        {'label': 'Teachers added', 'value': total_teachers, 'complete': total_teachers > 0, 'url': reverse('managementApp:teacher_list'), 'module': 'staff', 'action': 'view'},
        {'label': 'Subjects assigned to classes', 'value': subject_class_assignment_count, 'complete': classes_without_subjects == 0 and total_classes > 0, 'url': reverse('managementApp:assign_subjects_to_class'), 'module': 'subjects', 'action': 'edit'},
        {'label': 'Subjects assigned to teachers', 'value': subject_teacher_assignment_count, 'complete': teachers_without_subjects == 0 and active_teachers > 0, 'url': reverse('managementApp:assign_subjects_to_teacher'), 'module': 'subjects', 'action': 'edit'},
        {'label': 'Fee heads configured', 'value': fee_head_count, 'complete': fee_head_count > 0, 'url': reverse('managementApp:manage_fee_heads'), 'module': 'finance', 'action': 'view'},
        {'label': 'Exam timetable entries', 'value': exam_timetable_entries, 'complete': exam_timetable_entries > 0, 'url': reverse('managementApp:manage_exam_timetable'), 'module': 'exams', 'action': 'view'},
        {'label': 'Staff access roles', 'value': staff_access_enabled, 'complete': staff_access_enabled > 0, 'url': reverse('managementApp:manage_staff_access'), 'module': 'access_control', 'action': 'view'},
    ], permission_flags=permission_flags, full_access=full_access)

    attention_items = _filter_permission_items(user, [
        {'label': 'Pending leave applications', 'value': pending_leave_count, 'status': 'warning', 'icon': 'calendar check outline', 'url': reverse('managementApp:manage_leave_applications'), 'module': 'leave', 'action': 'view'},
        {'label': 'Students absent today', 'value': student_absent_today, 'status': 'negative' if student_absent_today else 'positive', 'icon': 'user times', 'url': reverse('managementApp:student_attendance_history'), 'module': 'attendance', 'action': 'view'},
        {'label': 'Staff absent today', 'value': teacher_absent_today, 'status': 'negative' if teacher_absent_today else 'positive', 'icon': 'user md', 'url': reverse('managementApp:staff_attendance_history'), 'module': 'attendance', 'action': 'view'},
        {'label': 'Classes without attendance', 'value': classes_without_attendance, 'status': 'warning' if classes_without_attendance else 'positive', 'icon': 'clipboard outline', 'url': reverse('managementApp:student_attendance'), 'module': 'attendance', 'action': 'add'},
        {'label': 'Overdue fee records', 'value': charge_overdue_count or legacy_fee_overdue_count, 'status': 'negative' if charge_overdue_count else 'warning', 'icon': 'rupee sign', 'url': reverse('managementApp:manage_student_charges'), 'module': 'finance', 'action': 'view'},
        {'label': 'Upcoming exams this week', 'value': upcoming_exam_count, 'status': 'info', 'icon': 'clipboard check', 'url': reverse('managementApp:manage_exam_timetable'), 'module': 'exams', 'action': 'view'},
        {'label': 'Events this week', 'value': Event.objects.filter(isDeleted=False, sessionID_id=current_session_id, startDate__gte=today, startDate__lte=next_week).count(), 'status': 'info', 'icon': 'calendar alternate outline', 'url': reverse('managementApp:manage_event'), 'module': 'events', 'action': 'view'},
    ], permission_flags=permission_flags, full_access=full_access)

    dashboard_kpis = _filter_permission_items(user, [
        {'label': 'Students', 'value': total_students, 'meta': f'{active_students} active in session', 'icon': 'graduation cap', 'tone': 'blue', 'url': reverse('managementApp:student_list'), 'module': 'students', 'action': 'view'},
        {'label': 'Teachers', 'value': total_teachers, 'meta': f'{active_teachers} active staff profiles', 'icon': 'chalkboard teacher', 'tone': 'green', 'url': reverse('managementApp:teacher_list'), 'module': 'staff', 'action': 'view'},
        {'label': 'Classes', 'value': total_classes, 'meta': f'{classes_without_subjects} missing subject setup', 'icon': 'chalkboard', 'tone': 'amber', 'url': reverse('managementApp:manage_class'), 'module': 'classes', 'action': 'view'},
        {'label': 'Today Student Attendance', 'value': f'{_percentage(student_present_today, student_attendance_today_count)}%', 'meta': f'{student_present_today} present, {student_absent_today} absent', 'icon': 'tasks', 'tone': 'teal', 'url': reverse('managementApp:student_attendance_history'), 'module': 'attendance', 'action': 'view'},
        {'label': 'Today Staff Attendance', 'value': f'{_percentage(teacher_present_today, teacher_attendance_today_count)}%', 'meta': f'{teacher_present_today} present, {teacher_absent_today} absent', 'icon': 'user check', 'tone': 'purple', 'url': reverse('managementApp:staff_attendance_history'), 'module': 'attendance', 'action': 'view'},
        {'label': 'Fees Collected', 'value': finance_collected_month, 'meta': 'This month', 'icon': 'receipt', 'tone': 'green', 'url': reverse('managementApp:manage_receipts'), 'is_money': True, 'module': 'finance', 'action': 'view'},
        {'label': 'Pending Fees', 'value': finance_pending_total, 'meta': f'{charge_overdue_count} overdue records', 'icon': 'money bill alternate outline', 'tone': 'red', 'url': reverse('managementApp:manage_student_charges'), 'is_money': True, 'module': 'finance', 'action': 'view'},
        {'label': 'Pending Actions', 'value': sum(item['value'] for item in attention_items if item['value']), 'meta': 'Items needing review', 'icon': 'bell outline', 'tone': 'amber', 'url': reverse('managementApp:admin_home'), 'module': 'dashboard', 'action': 'view'},
    ], permission_flags=permission_flags, full_access=full_access)

    context = {
        'total_students': total_students,
        'total_teachers': total_teachers,
        'total_classes': total_classes,
        'total_subjects': total_subjects,
        'total_parents': total_parents,
        'current_session_year': current_session_year,
        'upcoming_events': upcoming_events if _can(user, 'events', 'view', permission_flags=permission_flags, full_access=full_access) else [],
        'recent_students': recent_students if _can(user, 'students', 'view', permission_flags=permission_flags, full_access=full_access) else [],
        'recent_receipts': recent_receipts if _has_any(user, ('finance', 'view'), ('fees', 'view'), permission_flags=permission_flags, full_access=full_access) else [],
        'recent_audit_logs': recent_audit_logs if _can(user, 'audit', 'view', permission_flags=permission_flags, full_access=full_access) else [],
        'upcoming_exams': upcoming_exams if _can(user, 'exams', 'view', permission_flags=permission_flags, full_access=full_access) else [],
        'pending_leaves': pending_leaves if _can(user, 'leave', 'view', permission_flags=permission_flags, full_access=full_access) else [],
        'dashboard_kpis': dashboard_kpis,
        'pending_action_count': sum(item['value'] for item in attention_items if item['value']),
        'attention_items': attention_items,
        'setup_checklist': setup_checklist,
        'attendance_summary': {
            'student_present': student_present_today,
            'student_absent': student_absent_today,
            'student_leave': student_leave_today,
            'student_rate': _percentage(student_present_today, student_attendance_today_count),
            'staff_present': teacher_present_today,
            'staff_absent': teacher_absent_today,
            'staff_leave': teacher_leave_today,
            'staff_rate': _percentage(teacher_present_today, teacher_attendance_today_count),
            'classes_marked': class_attendance_marked_today,
            'classes_without_attendance': classes_without_attendance,
        },
        'finance_summary': {
            'collected_today': receipt_today_total,
            'collected_month': finance_collected_month,
            'pending_total': finance_pending_total,
            'overdue_count': charge_overdue_count or legacy_fee_overdue_count,
            'expenses_month': expense_month_total,
            'payroll_pending': payroll_pending_total,
            'net_month': finance_collected_month - expense_month_total,
        },
        'academic_summary': {
            'subject_class_assignments': subject_class_assignment_count,
            'subject_teacher_assignments': subject_teacher_assignment_count,
            'classes_without_subjects': classes_without_subjects,
            'teachers_without_subjects': teachers_without_subjects,
            'upcoming_exam_count': upcoming_exam_count,
            'marks_entered_count': marks_entered_count,
        },
        'student_summary': {
            'admissions_this_month': admissions_this_month,
            'leaving_this_month': leaving_this_month,
            'missing_parent': students_missing_parent,
            'missing_contact': students_missing_contact,
            'missing_photo': students_missing_photo,
        },
        'staff_summary': {
            'access_enabled': staff_access_enabled,
            'active_roles': active_roles_count,
            'without_subjects': teachers_without_subjects,
            'pending_payroll': payroll_pending_total,
        },
        'summary_labels_json': json.dumps(['Students', 'Teachers', 'Subjects', 'Classes']),
        'summary_values_json': json.dumps([total_students, total_teachers, total_subjects, total_classes]),
        'class_labels_json': json.dumps([row['standardID__name'] or 'N/A' for row in class_distribution]),
        'class_values_json': json.dumps([row['total'] for row in class_distribution]),
        'attendance_trend_labels_json': json.dumps(seven_day_labels),
        'attendance_student_values_json': json.dumps(seven_day_student_values),
        'attendance_staff_values_json': json.dumps(seven_day_staff_values),
        'attendance_class_labels_json': json.dumps([row['standardID__name'] or 'N/A' for row in attendance_class_distribution]),
        'attendance_class_present_json': json.dumps([row['present'] for row in attendance_class_distribution]),
        'attendance_class_absent_json': json.dumps([row['absent'] for row in attendance_class_distribution]),
        'finance_labels_json': json.dumps(['Collected', 'Pending', 'Expenses', 'Payroll Pending']),
        'finance_values_json': json.dumps([float(finance_collected_month), float(finance_pending_total), float(expense_month_total), float(payroll_pending_total)]),
    }
    return context
