import json
from decimal import Decimal
from datetime import date, timedelta, datetime

from django.db.models import Count, Q, Sum
from django.shortcuts import render, get_object_or_404, redirect

from financeApp.models import ExpenseCategory, ExpenseVoucher, FeeHead, FinanceAccount, FinanceApprovalRule, FinanceConfiguration, FinanceEntry, FinanceParty, FinancePeriod, PaymentReceipt, PayrollRun, StudentCharge
from financeApp.services import bootstrap_expense_categories, bootstrap_school_finance, get_finance_configuration
from homeApp.models import SchoolDetail, SchoolSession
from homeApp.session_utils import get_session_month_sequence
from homeApp.utils import login_required
from managementApp.models import *
from managementApp.reporting import build_report_cards_for_student
from managementApp.signals import pre_save_with_user
from teacherApp.models import SubjectNote
from utils.custom_decorators import check_groups


# Create your views here.

@check_groups('Admin', 'Owner')
def admin_home(request):
    current_session_id = request.session['current_session']['Id']
    current_session_year = request.session.get('current_session', {}).get('currentSessionYear', 'N/A')

    totalStudent = Student.objects.filter(isDeleted=False, sessionID_id=current_session_id).count()
    totalTeacher = TeacherDetail.objects.filter(isDeleted=False, sessionID_id=current_session_id).count()
    totalClass = Standard.objects.filter(isDeleted=False, sessionID_id=current_session_id).count()
    totalSubject = Subjects.objects.filter(isDeleted=False, sessionID_id=current_session_id).count()
    totalParents = Parent.objects.filter(isDeleted=False, sessionID_id=current_session_id).count()

    upcoming_events = Event.objects.filter(
        isDeleted=False,
        sessionID_id=current_session_id,
        startDate__gte=date.today(),
        startDate__lte=date.today() + timedelta(days=30),
    ).order_by('startDate')[:5]

    recent_students = Student.objects.select_related('standardID').filter(
        isDeleted=False,
        sessionID_id=current_session_id,
    ).order_by('-datetime')[:5]

    class_distribution = list(
        Student.objects.filter(
            isDeleted=False,
            sessionID_id=current_session_id,
        ).values('standardID__name').annotate(total=Count('id')).order_by('-total')[:8]
    )

    context = {
        'total_students': totalStudent,
        'total_teachers': totalTeacher,
        'total_classes': totalClass,
        'total_subjects': totalSubject,
        'total_parents': totalParents,
        'current_session_year': current_session_year,
        'upcoming_events': upcoming_events,
        'recent_students': recent_students,
        'summary_labels_json': json.dumps(['Students', 'Teachers', 'Subjects', 'Classes']),
        'summary_values_json': json.dumps([totalStudent, totalTeacher, totalSubject, totalClass]),
        'class_labels_json': json.dumps([row['standardID__name'] or 'N/A' for row in class_distribution]),
        'class_values_json': json.dumps([row['total'] for row in class_distribution]),
    }
    return render(request, 'managementApp/dashboard.html', context)


@login_required
@check_groups('Admin', 'Owner')
def school_detail(request):
    school_id = request.session.get('current_session', {}).get('SchoolID')
    school = None
    if school_id:
        school = SchoolDetail.objects.filter(pk=school_id, isDeleted=False).first()
    if not school:
        school = SchoolDetail.objects.filter(
            ownerID__userID_id=request.user.id,
            isDeleted=False
        ).order_by('-datetime').first()

    context = {
        'school': school,
    }
    return render(request, 'managementApp/school/school_detail.html', context)


@login_required
@check_groups('Admin', 'Owner')
def manage_session_import(request):
    current_session_id = request.session.get('current_session', {}).get('Id')
    session_qs = list(SchoolSession.objects.filter(
        schoolID_id=request.session.get('current_session', {}).get('SchoolID'),
        isDeleted=False,
    ).order_by('startDate', 'datetime', 'id'))
    previous_session_id = None
    if current_session_id:
        ordered_ids = [item.id for item in session_qs]
        if current_session_id in ordered_ids:
            current_index = ordered_ids.index(current_session_id)
            if current_index > 0:
                previous_session_id = ordered_ids[current_index - 1]
    context = {
        'session_choices': session_qs,
        'current_session_id': current_session_id,
        'previous_session_id': previous_session_id,
    }
    return render(request, 'managementApp/school/session_import.html', context)


@login_required
@check_groups('Admin', 'Owner')
def manage_class(request):
    context = {
    }
    return render(request, 'managementApp/class.html', context)


@login_required
@check_groups('Admin', 'Owner')
def manage_subjects(request):
    context = {
    }
    return render(request, 'managementApp/subjects/addEditListSubjects.html', context)


@login_required
@check_groups('Admin', 'Owner')
def assign_subjects_to_class(request):
    context = {
    }
    return render(request, 'managementApp/subjects/assignSubjectsToClass.html', context)


@login_required
@check_groups('Admin', 'Owner')
def assign_subjects_to_teacher(request):
    context = {
    }
    return render(request, 'managementApp/subjects/assignSubjectsToTeacher.html', context)


@login_required
@check_groups('Admin', 'Owner')
def manage_subject_notes(request):
    current_session_id = request.session.get('current_session', {}).get('Id')
    current_school_id = request.session.get('current_session', {}).get('SchoolID')

    note_qs = SubjectNote.objects.filter(
        isDeleted=False,
    )
    if current_session_id:
        note_qs = note_qs.filter(sessionID_id=current_session_id)
    if current_school_id:
        note_qs = note_qs.filter(schoolID_id=current_school_id)

    context = {
        'notes_total': note_qs.count(),
        'notes_draft': note_qs.filter(status='draft').count(),
        'notes_published': note_qs.filter(status='published').count(),
    }
    return render(request, 'managementApp/subjects/manage_subject_notes.html', context)


# Teacher --------------------
@login_required
@check_groups('Admin', 'Owner')
def add_teacher(request):
    context = {
    }
    return render(request, 'managementApp/teacher/add_teacher.html', context)

@login_required
@check_groups('Admin', 'Owner')
def edit_teacher(request,id=None):
    instance = get_object_or_404(TeacherDetail, pk=id)

    context = {
        'instance': instance,
    }
    return render(request, 'managementApp/teacher/edit_teacher.html', context)



@login_required
@check_groups('Admin', 'Owner')
def teacher_list(request):
    context = {
    }
    return render(request, 'managementApp/teacher/teacher_list.html', context)

@login_required
@check_groups('Admin', 'Owner')
def teacher_detail(request, id=None):
    instance = get_object_or_404(TeacherDetail, pk=id)
    context = {
        'instance': instance,
    }
    return render(request, 'managementApp/teacher/teacher_detail.html', context)



# student

@login_required
@check_groups('Admin', 'Owner')
def add_student(request):
    context = {
    }
    return render(request, 'managementApp/student/add_student.html', context)


@login_required
@check_groups('Admin', 'Owner')
def student_list(request):
    context = {
    }
    return render(request, 'managementApp/student/student_list.html', context)


@login_required
@check_groups('Admin', 'Owner')
def student_detail(request, id=None):
    instance = get_object_or_404(Student, pk=id)
    parent = instance.parentID
    context = {
        'instance': instance,
        'parent': parent,
    }
    return render(request, 'managementApp/student/student_detail.html', context)

@login_required
@check_groups('Admin', 'Owner')
def edit_student_detail(request, id=None):
    instance = get_object_or_404(Student, pk=id)
    admission_receipt = PaymentReceipt.objects.select_related('paymentModeID').filter(
        studentID_id=instance.id,
        schoolID_id=instance.schoolID_id or request.session.get('current_session', {}).get('SchoolID'),
        sourceModule='student_admission_receipt',
        sourceRecordID=f'{instance.id}:admission',
        isDeleted=False,
    ).order_by('-datetime').first()
    context = {
        'instance': instance,
        'admission_receipt': admission_receipt,
    }
    return render(request, 'managementApp/student/edit_student.html', context)


@login_required
@check_groups('Admin', 'Owner')
def finance_dashboard(request):
    current_session = request.session.get('current_session', {})
    session_id = current_session.get('Id')
    school_id = current_session.get('SchoolID')
    if school_id and session_id:
        bootstrap_school_finance(school_id=school_id, session_id=session_id, user_obj=request.user)
        bootstrap_expense_categories(school_id=school_id, session_id=session_id, user_obj=request.user)

    fee_heads_qs = FeeHead.objects.filter(isDeleted=False)
    charge_qs = StudentCharge.objects.filter(isDeleted=False)
    receipt_qs = PaymentReceipt.objects.filter(isDeleted=False)
    expense_qs = ExpenseVoucher.objects.filter(isDeleted=False)
    finance_accounts_qs = FinanceAccount.objects.filter(isDeleted=False)

    if school_id:
        fee_heads_qs = fee_heads_qs.filter(schoolID_id=school_id)
        charge_qs = charge_qs.filter(schoolID_id=school_id)
        receipt_qs = receipt_qs.filter(schoolID_id=school_id)
        expense_qs = expense_qs.filter(schoolID_id=school_id)
        finance_accounts_qs = finance_accounts_qs.filter(schoolID_id=school_id)
    if session_id:
        fee_heads_qs = fee_heads_qs.filter(sessionID_id=session_id)
        charge_qs = charge_qs.filter(sessionID_id=session_id)
        receipt_qs = receipt_qs.filter(sessionID_id=session_id)
        expense_qs = expense_qs.filter(sessionID_id=session_id)
        finance_accounts_qs = finance_accounts_qs.filter(sessionID_id=session_id)

    charge_summary = charge_qs.aggregate(
        total_due=Sum('netAmount'),
        total_paid=Sum('paidAmount'),
        total_balance=Sum('balanceAmount'),
    )
    confirmed_receipt_qs = receipt_qs.filter(status='confirmed')
    paid_expense_qs = expense_qs.filter(approvalStatus='paid')

    cash_account = finance_accounts_qs.filter(accountCode='CASH_ON_HAND').first()
    bank_account = finance_accounts_qs.filter(accountCode='BANK_MAIN').first()

    def account_balance(account_obj):
        if not account_obj:
            return Decimal('0.00')
        totals = FinanceEntry.objects.filter(
            accountID=account_obj,
            transactionID__isDeleted=False,
            transactionID__status='posted',
        ).aggregate(
            debit_total=Sum('amount', filter=Q(entryType='debit')),
            credit_total=Sum('amount', filter=Q(entryType='credit')),
        )
        return (totals.get('debit_total') or Decimal('0.00')) - (totals.get('credit_total') or Decimal('0.00'))

    context = {
        'fee_heads_count': fee_heads_qs.count(),
        'open_charges_count': charge_qs.exclude(status__in=['paid', 'cancelled']).count(),
        'charge_total_due': charge_summary.get('total_due') or Decimal('0.00'),
        'charge_total_paid': charge_summary.get('total_paid') or Decimal('0.00'),
        'charge_total_balance': charge_summary.get('total_balance') or Decimal('0.00'),
        'receipt_count': confirmed_receipt_qs.count(),
        'receipt_total': confirmed_receipt_qs.aggregate(total=Sum('amountReceived')).get('total') or Decimal('0.00'),
        'expense_voucher_count': paid_expense_qs.count(),
        'expense_total': paid_expense_qs.aggregate(total=Sum('netAmount')).get('total') or Decimal('0.00'),
        'cash_balance': account_balance(cash_account),
        'bank_balance': account_balance(bank_account),
        'recent_receipts': confirmed_receipt_qs.select_related('studentID', 'paymentModeID').order_by('-receiptDate', '-id')[:5],
        'recent_expenses': expense_qs.select_related('expenseCategoryID', 'paymentModeID').order_by('-voucherDate', '-id')[:5],
    }
    return render(request, 'managementApp/finance/dashboard.html', context)


@login_required
@check_groups('Admin', 'Owner')
def manage_fee_heads(request):
    current_session = request.session.get('current_session', {})
    session_id = current_session.get('Id')
    school_id = current_session.get('SchoolID')
    if school_id and session_id:
        bootstrap_school_finance(school_id=school_id, session_id=session_id, user_obj=request.user)
    context = {
        'fee_head_categories': FeeHead.CATEGORY_CHOICES,
        'fee_head_recurrence_types': FeeHead.RECURRENCE_CHOICES,
    }
    return render(request, 'managementApp/finance/manage_fee_heads.html', context)


@login_required
@check_groups('Admin', 'Owner')
def manage_receipts(request):
    current_session = request.session.get('current_session', {})
    session_id = current_session.get('Id')
    school_id = current_session.get('SchoolID')
    if school_id and session_id:
        bootstrap_school_finance(school_id=school_id, session_id=session_id, user_obj=request.user)
    context = {}
    return render(request, 'managementApp/finance/manage_receipts.html', context)


@login_required
@check_groups('Admin', 'Owner')
def finance_reports(request):
    current_session = request.session.get('current_session', {})
    session_id = current_session.get('Id')
    school_id = current_session.get('SchoolID')
    if school_id and session_id:
        bootstrap_school_finance(school_id=school_id, session_id=session_id, user_obj=request.user)
    context = {}
    return render(request, 'managementApp/finance/reports.html', context)


@login_required
@check_groups('Admin', 'Owner')
def finance_controls(request):
    current_session = request.session.get('current_session', {})
    session_id = current_session.get('Id')
    school_id = current_session.get('SchoolID')
    if school_id and session_id:
        bootstrap_school_finance(school_id=school_id, session_id=session_id, user_obj=request.user)
    context = {
        'period_status_choices': FinancePeriod.STATUS_CHOICES,
        'approval_rule_document_choices': FinanceApprovalRule.DOCUMENT_TYPE_CHOICES,
        'approval_rule_mode_choices': FinanceApprovalRule.APPROVAL_MODE_CHOICES,
    }
    return render(request, 'managementApp/finance/controls.html', context)


@login_required
@check_groups('Admin', 'Owner')
def finance_settings(request):
    current_session = request.session.get('current_session', {})
    session_id = current_session.get('Id')
    school_id = current_session.get('SchoolID')
    context = {}
    if school_id and session_id:
        setup = bootstrap_school_finance(school_id=school_id, session_id=session_id, user_obj=request.user)
        config_obj = get_finance_configuration(school_id=school_id, session_id=session_id, user_obj=request.user)
        account_choices = FinanceAccount.objects.filter(
            schoolID_id=school_id,
            sessionID_id=session_id,
            isDeleted=False,
            isActive=True,
        ).order_by('accountCode', 'accountName')
        context = {
            'finance_config': config_obj,
            'finance_account_choices': account_choices,
            'default_cash_account': config_obj.defaultCashAccountID or setup['accounts'].get('CASH_ON_HAND'),
            'default_bank_account': config_obj.defaultBankAccountID or setup['accounts'].get('BANK_MAIN'),
        }
    return render(request, 'managementApp/finance/settings.html', context)


@login_required
@check_groups('Admin', 'Owner')
def finance_audit_trail(request):
    current_session = request.session.get('current_session', {})
    session_id = current_session.get('Id')
    school_id = current_session.get('SchoolID')
    if school_id and session_id:
        bootstrap_school_finance(school_id=school_id, session_id=session_id, user_obj=request.user)
    return render(request, 'managementApp/finance/audit_trail.html', {})


@login_required
@check_groups('Admin', 'Owner')
def finance_reconciliation(request):
    current_session = request.session.get('current_session', {})
    session_id = current_session.get('Id')
    school_id = current_session.get('SchoolID')
    if school_id and session_id:
        bootstrap_school_finance(school_id=school_id, session_id=session_id, user_obj=request.user)
    return render(request, 'managementApp/finance/reconciliation.html', {})


@login_required
@check_groups('Admin', 'Owner')
def finance_payroll(request):
    current_session = request.session.get('current_session', {})
    session_id = current_session.get('Id')
    school_id = current_session.get('SchoolID')
    if school_id and session_id:
        bootstrap_school_finance(school_id=school_id, session_id=session_id, user_obj=request.user)
    context = {}
    return render(request, 'managementApp/finance/payroll.html', context)


@login_required
@check_groups('Admin', 'Owner')
def manage_vendors(request):
    current_session = request.session.get('current_session', {})
    session_id = current_session.get('Id')
    school_id = current_session.get('SchoolID')
    if school_id and session_id:
        bootstrap_expense_categories(school_id=school_id, session_id=session_id, user_obj=request.user)
    return render(request, 'managementApp/finance/manage_vendors.html', {})


@login_required
@check_groups('Admin', 'Owner')
def vendor_payables(request):
    current_session = request.session.get('current_session', {})
    session_id = current_session.get('Id')
    school_id = current_session.get('SchoolID')
    if school_id and session_id:
        bootstrap_expense_categories(school_id=school_id, session_id=session_id, user_obj=request.user)
    return render(request, 'managementApp/finance/vendor_payables.html', {})


@login_required
@check_groups('Admin', 'Owner')
def vendor_statement(request):
    current_session = request.session.get('current_session', {})
    session_id = current_session.get('Id')
    school_id = current_session.get('SchoolID')
    if school_id and session_id:
        bootstrap_expense_categories(school_id=school_id, session_id=session_id, user_obj=request.user)
    context = {
        'selected_vendor_id': request.GET.get('vendor') or '',
    }
    return render(request, 'managementApp/finance/vendor_statement.html', context)


@login_required
@check_groups('Admin', 'Owner')
def manage_student_charges(request):
    current_session = request.session.get('current_session', {})
    session_id = current_session.get('Id')
    school_id = current_session.get('SchoolID')
    if school_id and session_id:
        bootstrap_school_finance(school_id=school_id, session_id=session_id, user_obj=request.user)
    context = {}
    return render(request, 'managementApp/finance/manage_student_charges.html', context)


@login_required
@check_groups('Admin', 'Owner')
def student_finance_ledger(request):
    context = {}
    return render(request, 'managementApp/finance/student_ledger.html', context)


@login_required
@check_groups('Admin', 'Owner')
def finance_receipt_detail(request, id=None):
    receipt = get_object_or_404(
        PaymentReceipt.objects.select_related(
            'studentID',
            'studentID__standardID',
            'studentID__parentID',
            'partyID',
            'paymentModeID',
            'schoolID',
            'sessionID',
            'depositAccountID',
        ),
        pk=id,
        isDeleted=False,
    )
    allocations = list(
        receipt.allocations.select_related('studentChargeID', 'studentChargeID__feeHeadID').all().order_by('id')
    )
    student_obj = receipt.studentID
    charge_ids = [row.studentChargeID_id for row in allocations]
    charges = list(StudentCharge.objects.filter(id__in=charge_ids, isDeleted=False))
    total_charged = sum((row.netAmount for row in charges), start=0)
    total_paid = sum((row.paidAmount for row in charges), start=0)
    total_balance = sum((row.balanceAmount for row in charges), start=0)
    finance_config = get_finance_configuration(
        school_id=receipt.schoolID_id,
        session_id=receipt.sessionID_id,
        user_obj=request.user,
    )
    context = {
        'receipt': receipt,
        'allocations': allocations,
        'student': student_obj,
        'school': receipt.schoolID,
        'finance_config': finance_config,
        'total_charged': total_charged,
        'total_paid': total_paid,
        'total_balance': total_balance,
    }
    return render(request, 'managementApp/finance/receipt_detail.html', context)


@login_required
@check_groups('Admin', 'Owner')
def manage_expense_vouchers(request):
    current_session = request.session.get('current_session', {})
    session_id = current_session.get('Id')
    school_id = current_session.get('SchoolID')
    if school_id and session_id:
        bootstrap_expense_categories(school_id=school_id, session_id=session_id, user_obj=request.user)
    context = {}
    return render(request, 'managementApp/finance/manage_expense_vouchers.html', context)


@login_required
@check_groups('Admin', 'Owner')
def cash_bank_book(request):
    current_session = request.session.get('current_session', {})
    session_id = current_session.get('Id')
    school_id = current_session.get('SchoolID')
    if school_id and session_id:
        bootstrap_expense_categories(school_id=school_id, session_id=session_id, user_obj=request.user)
    context = {}
    return render(request, 'managementApp/finance/cash_bank_book.html', context)


@login_required
@check_groups('Admin', 'Owner')
def student_id_cards(request):
    context = {}
    return render(request, 'managementApp/student/student_id_cards.html', context)


@login_required
@check_groups('Admin', 'Owner')
def student_id_card_detail(request, id=None):
    current_session_id = request.session['current_session']['Id']
    instance = get_object_or_404(
        Student.objects.select_related('standardID', 'parentID'),
        pk=id,
        isDeleted=False,
        sessionID_id=current_session_id,
    )
    embed_mode = request.GET.get('embed') == '1'
    partial_mode = request.GET.get('partial') == '1'
    context = {
        'instance': instance,
        'school': instance.schoolID,
        'school_name': (
            (instance.schoolID.schoolName if instance.schoolID else '')
            or (instance.schoolID.name if instance.schoolID else '')
            or 'School Name'
        ),
        'valid_till_label': 'Upto 2026',
        'embed_mode': embed_mode,
    }
    if partial_mode:
        return render(request, 'managementApp/student/student_id_card_embed.html', context)
    return render(request, 'managementApp/student/student_id_card_detail.html', context)


# Exam ----------------------------------------------
@login_required
@check_groups('Admin', 'Owner')
def manage_exams(request):
    context = {
    }
    return render(request, 'managementApp/exam/addEditListExams.html', context)


@login_required
@check_groups('Admin', 'Owner')
def assign_exams_to_class(request):
    context = {
    }
    return render(request, 'managementApp/exam/assignExamToClass.html', context)


@login_required
@check_groups('Admin', 'Owner')
def manage_exam_timetable(request):
    context = _exam_timetable_preview_context(request)
    return render(request, 'managementApp/exam/examTimeTable.html', context)


def _exam_timetable_preview_context(request):
    current_session_id = request.session.get('current_session', {}).get('Id')
    timetable_rows = ExamTimeTable.objects.select_related(
        'standardID', 'examID', 'subjectID'
    ).filter(
        isDeleted=False,
        sessionID_id=current_session_id,
    ).order_by('examID__name', 'examDate', 'startTime', 'standardID__name') if current_session_id else ExamTimeTable.objects.none()

    school_detail = None
    school_id = request.session.get('current_session', {}).get('SchoolID')
    if school_id:
        school_detail = SchoolDetail.objects.filter(pk=school_id, isDeleted=False).first()
    if not school_detail and current_session_id:
        school_detail = SchoolDetail.objects.filter(
            schoolsession__id=current_session_id,
            schoolsession__isDeleted=False,
            isDeleted=False
        ).distinct().first()

    context = {
        'timetable_rows': timetable_rows,
        'school_detail': school_detail,
        'exam_year': request.session.get('current_session', {}).get('currentSessionYear') or 'Exam Year',
    }
    return context


@login_required
@check_groups('Admin', 'Owner')
def manage_exam_timetable_preview(request):
    context = _exam_timetable_preview_context(request)
    return render(request, 'managementApp/exam/examTimeTablePreview.html', context)


# attendance
@login_required
@check_groups('Admin', 'Owner')
def student_attendance(request):
    context = {
    }
    return render(request, 'managementApp/attendance/studentAttendance.html', context)


@login_required
@check_groups('Admin', 'Owner')
def student_attendance_history(request):
    context = {
    }
    return render(request, 'managementApp/attendance/studentAttendanceHistory.html', context)


@login_required
@check_groups('Admin', 'Owner')
def staff_attendance(request):
    context = {
    }
    return render(request, 'managementApp/attendance/staffAttendance.html', context)


@login_required
@check_groups('Admin', 'Owner')
def staff_attendance_history(request):
    context = {
    }
    return render(request, 'managementApp/attendance/staffAttendanceHistory.html', context)


# student Fee --------------------------------------------------
@login_required
@check_groups('Admin', 'Owner')
def student_fee(request):
    context = {
    }
    return render(request, 'managementApp/fee/addStudentFee.html', context)


@login_required
@check_groups('Admin', 'Owner')
def student_fee_details(request):
    current_session_id = request.session.get('current_session', {}).get('Id')
    session_obj = SchoolSession.objects.filter(pk=current_session_id, isDeleted=False).first() if current_session_id else None
    month_headers = [
        datetime(year_value, month_no, 1).strftime('%b-%Y')
        for _, year_value, month_no, _, _ in get_session_month_sequence(session_obj)
    ]
    context = {
        'fee_month_headers': month_headers,
    }
    return render(request, 'managementApp/fee/feeDetails.html', context)


# Marks -------------------------------------------------------
def _grade_from_percentage(value):
    if value is None:
        return 'N/A'
    if value >= 90:
        return 'A+'
    if value >= 80:
        return 'A'
    if value >= 70:
        return 'B+'
    if value >= 60:
        return 'B'
    if value >= 50:
        return 'C'
    if value >= 40:
        return 'D'
    return 'F'


@login_required
@check_groups('Admin', 'Owner')
def student_marks(request):
    context = {
    }
    return render(request, 'managementApp/marks/addExamMarks.html', context)


@login_required
@check_groups('Admin', 'Owner')
def exam_marks_details(request):
    context = {
    }
    return render(request, 'managementApp/marks/examMarksDetails.html', context)


@login_required
@check_groups('Admin', 'Owner')
def progress_report_cards(request):
    current_session_id = request.session['current_session']['Id']
    current_school_id = request.session.get('current_session', {}).get('SchoolID')
    school_detail = None
    if current_school_id:
        school_detail = SchoolDetail.objects.filter(pk=current_school_id, isDeleted=False).first()

    class_qs = Standard.objects.filter(
        isDeleted=False,
        sessionID_id=current_session_id
    ).order_by('name', 'section')
    classes = list(class_qs)

    students = list(Student.objects.select_related('standardID').filter(
        isDeleted=False,
        sessionID_id=current_session_id
    ).order_by('name'))

    assigned_exams = list(AssignExamToClass.objects.select_related('examID', 'standardID').filter(
        isDeleted=False,
        sessionID_id=current_session_id
    ).order_by('examID__name', 'startDate'))

    class_map = []
    for c in classes:
        class_map.append({
            'id': c.id,
            'name': f"{c.name or 'N/A'}{' - ' + c.section if c.section else ''}"
        })

    students_by_class = {}
    for s in students:
        if not s.standardID_id:
            continue
        students_by_class.setdefault(str(s.standardID_id), []).append({
            'id': s.id,
            'name': f"{s.name or 'N/A'}{' (Roll: ' + str(s.roll) + ')' if s.roll else ''}"
        })

    exams_by_class = {}
    for e in assigned_exams:
        if not e.standardID_id:
            continue
        exams_by_class.setdefault(str(e.standardID_id), []).append({
            'id': e.id,
            'name': e.examID.name if e.examID else 'N/A'
        })

    selected_class_id = request.GET.get('standard')
    selected_student_id = request.GET.get('student')
    selected_exam_id = request.GET.get('exam')

    report_cards = []
    selected_student = None

    if selected_class_id and selected_student_id:
        student_obj = Student.objects.select_related('standardID').filter(
            isDeleted=False,
            sessionID_id=current_session_id,
            standardID_id=selected_class_id,
            id=selected_student_id
        ).first()

        if student_obj:
            selected_student = student_obj
            exam_queryset = AssignExamToClass.objects.select_related('examID').filter(
                isDeleted=False,
                sessionID_id=current_session_id,
                standardID_id=selected_class_id,
            )
            if selected_exam_id and selected_exam_id != 'all':
                exam_queryset = exam_queryset.filter(id=selected_exam_id)
            exam_queryset = exam_queryset.order_by('startDate', 'examID__name')

            report_cards = build_report_cards_for_student(
                current_session_id=current_session_id,
                student_obj=student_obj,
                standard_id=selected_class_id,
                exam_queryset=exam_queryset,
                prefer_published_snapshot=False,
            )

    context = {
        'class_map_json': json.dumps(class_map),
        'students_by_class_json': json.dumps(students_by_class),
        'exams_by_class_json': json.dumps(exams_by_class),
        'selected_class_id': int(selected_class_id) if selected_class_id and selected_class_id.isdigit() else '',
        'selected_student_id': int(selected_student_id) if selected_student_id and selected_student_id.isdigit() else '',
        'selected_exam_id': selected_exam_id if selected_exam_id else 'all',
        'selected_student': selected_student,
        'school_detail': school_detail,
        'session_year': request.session.get('current_session', {}).get('currentSessionYear', ''),
        'report_cards': report_cards,
    }
    return render(request, 'managementApp/marks/progressReportCards.html', context)


#events------------------------------------------------------
@login_required
@check_groups('Admin', 'Owner')
def manage_event(request):
    context = {
    }
    return render(request, 'managementApp/events/add_event.html', context)


@login_required
@check_groups('Admin', 'Owner')
def manage_event_type(request):
    context = {
    }
    return render(request, 'managementApp/events/manage_event_type.html', context)


@login_required
@check_groups('Admin', 'Owner')
def manage_leave_types(request):
    return render(request, 'managementApp/leave/manage_leave_types.html', {})


@login_required
@check_groups('Admin', 'Owner')
def manage_leave_applications(request):
    return render(request, 'managementApp/leave/manage_leave_applications.html', {})

# ----Parents -------------------
@login_required
@check_groups('Admin', 'Owner')
def manage_parents(request):
    context = {
    }
    return render(request, 'managementApp/parents/parents_list.html', context)


@login_required
@check_groups('Admin', 'Owner')
def parent_detail(request, id=None):
    parent = get_object_or_404(Parent, pk=id, isDeleted=False)
    current_session_id = request.session['current_session']['Id']
    wards = Student.objects.select_related('standardID').filter(
        parentID_id=parent.id,
        isDeleted=False,
        sessionID_id=current_session_id,
    ).order_by('name')
    context = {
        'parent': parent,
        'wards': wards,
    }
    return render(request, 'managementApp/parents/parent_detail.html', context)


@login_required
@check_groups('Admin', 'Owner')
def edit_parent(request, id=None):
    parent = get_object_or_404(
        Parent,
        pk=id,
        isDeleted=False,
        sessionID_id=request.session['current_session']['Id'],
    )

    if request.method == 'POST':
        parent.fatherName = request.POST.get('fatherName')
        parent.fatherPhone = request.POST.get('fatherPhone')
        parent.fatherEmail = request.POST.get('fatherEmail')
        parent.fatherOccupation = request.POST.get('fatherOccupation')
        parent.fatherAddress = request.POST.get('fatherAddress')

        parent.motherName = request.POST.get('motherName')
        parent.motherPhone = request.POST.get('motherPhone')
        parent.motherEmail = request.POST.get('motherEmail')
        parent.motherOccupation = request.POST.get('motherOccupation')
        parent.motherAddress = request.POST.get('motherAddress')

        parent.guardianName = request.POST.get('guardianName')
        parent.guardianPhone = request.POST.get('guardianPhone')
        parent.guardianOccupation = request.POST.get('guardianOccupation')

        parent.familyType = request.POST.get('familyType')
        parent.totalFamilyMembers = request.POST.get('totalFamilyMembers') or None
        parent.annualIncome = request.POST.get('annualIncome') or 0
        parent.phoneNumber = request.POST.get('primaryPhone')
        parent.email = request.POST.get('primaryEmail')

        pre_save_with_user.send(sender=Parent, instance=parent, user=request.user.pk)
        parent.save()
        return redirect('managementApp:parent_detail', id=parent.id)

    context = {
        'parent': parent,
    }
    return render(request, 'managementApp/parents/edit_parent.html', context)
