from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase

from financeApp.models import FinanceTransaction, PaymentReceipt, StudentCharge
from financeApp.services import sync_library_fine_finance
from homeApp.models import SchoolDetail, SchoolSession
from libraryApp.models import LibraryFine, LibraryMember
from managementApp.api.urls_api import urlpatterns
from managementApp.models import Standard, Student, TeacherDetail


FINANCE_ROUTE_TERMS = (
    'finance',
    'vendor',
    'receipt',
    'payment',
    'payroll',
    'expense',
    'fee',
    'student_charge',
    'ledger',
    'cash',
    'bank',
    'reconciliation',
    'approval',
    'voucher',
    'refund',
)

EXPECTED_FINANCE_ROUTES = {
    'FeeByStudentJson': 'financeApp.api.legacy_fees',
    'add_student_fee_api': 'financeApp.api.legacy_fees',
    'StudentFeeDetailsByClassJson': 'financeApp.api.legacy_fees',
    'StudentFeeDetailsByStudentJson': 'financeApp.api.legacy_fees',
    'get_finance_account_options_api': 'financeApp.api.settings',
    'get_finance_settings_api': 'financeApp.api.settings',
    'upsert_finance_settings_api': 'financeApp.api.settings',
    'get_finance_payment_mode_options_api': 'financeApp.api.settings',
    'get_vendor_list_api': 'financeApp.api.vendors',
    'search_vendor_suggestions_api': 'financeApp.api.vendors',
    'FinanceVendorListJson': 'financeApp.api.vendors',
    'upsert_vendor_api': 'financeApp.api.vendors',
    'delete_vendor_api': 'financeApp.api.vendors',
    'get_vendor_payables_api': 'financeApp.api.vendor_payables',
    'finance_vendor_payables_rows_api': 'financeApp.api.vendor_payables',
    'finance_vendor_outstanding_voucher_rows_api': 'financeApp.api.vendor_payables',
    'get_vendor_statement_api': 'financeApp.api.vendor_statements',
    'finance_vendor_statement_rows_api': 'financeApp.api.vendor_statements',
    'get_receipt_charge_options_api': 'financeApp.api.receipts',
    'get_receipt_refund_options_api': 'financeApp.api.receipts',
    'FinanceFeeHeadListJson': 'financeApp.api.fee_heads',
    'FinanceReceiptListJson': 'financeApp.api.receipts',
    'FinanceStudentChargeListJson': 'financeApp.api.student_charges',
    'FinanceExpenseCategoryListJson': 'financeApp.api.expense_categories',
    'FinanceExpenseVoucherListJson': 'financeApp.api.expense_vouchers',
    'get_payment_receipt_list_api': 'financeApp.api.receipts',
    'get_finance_reports_api': 'financeApp.api.reports',
    'get_money_ledger_filter_options_api': 'financeApp.api.money_ledger',
    'get_money_ledger_api': 'financeApp.api.money_ledger',
    'money_ledger_rows_api': 'financeApp.api.money_ledger',
    'get_finance_control_center_api': 'financeApp.api.controls',
    'upsert_finance_approval_rule_api': 'financeApp.api.controls',
    'delete_finance_approval_rule_api': 'financeApp.api.controls',
    'get_finance_audit_trail_api': 'financeApp.api.audit_trails',
    'FinanceAuditTrailListJson': 'financeApp.api.audit_trails',
    'get_finance_reconciliation_api': 'financeApp.api.reconciliation',
    'finance_recon_receipt_rows_api': 'financeApp.api.reconciliation',
    'finance_recon_charge_rows_api': 'financeApp.api.reconciliation',
    'finance_recon_voucher_rows_api': 'financeApp.api.reconciliation',
    'finance_recon_payroll_rows_api': 'financeApp.api.reconciliation',
    'get_receipt_adjustment_history_api': 'financeApp.api.receipts',
    'get_payroll_run_list_api': 'financeApp.api.payroll',
    'get_payroll_run_detail_api': 'financeApp.api.payroll',
    'create_manual_payment_receipt_api': 'financeApp.api.receipts',
    'create_payment_refund_api': 'financeApp.api.receipts',
    'upsert_finance_period_api': 'financeApp.api.controls',
    'repair_finance_reconciliation_issue_api': 'financeApp.api.reconciliation',
    'create_payroll_run_api': 'financeApp.api.payroll',
    'approve_expense_voucher_api': 'financeApp.api.expense_vouchers',
    'approve_payment_receipt_api': 'financeApp.api.receipts',
    'approve_payment_refund_api': 'financeApp.api.receipts',
    'approve_payroll_run_api': 'financeApp.api.payroll',
    'approve_payroll_payment_api': 'financeApp.api.payroll',
    'post_payroll_run_api': 'financeApp.api.payroll',
    'pay_payroll_line_api': 'financeApp.api.payroll',
    'reverse_payment_receipt_api': 'financeApp.api.receipts',
    'get_student_charge_list_api': 'financeApp.api.student_charges',
    'get_fee_head_list_api': 'financeApp.api.fee_heads',
    'upsert_fee_head_api': 'financeApp.api.fee_heads',
    'delete_fee_head_api': 'financeApp.api.fee_heads',
    'get_student_finance_ledger_api': 'financeApp.api.student_ledgers',
    'finance_student_ledger_rows_api': 'financeApp.api.student_ledgers',
    'get_expense_category_list_api': 'financeApp.api.expense_categories',
    'upsert_expense_category_api': 'financeApp.api.expense_categories',
    'delete_expense_category_api': 'financeApp.api.expense_categories',
    'get_expense_voucher_list_api': 'financeApp.api.expense_vouchers',
    'upsert_expense_voucher_api': 'financeApp.api.expense_vouchers',
    'delete_expense_voucher_api': 'financeApp.api.expense_vouchers',
    'get_cash_bank_book_api': 'financeApp.api.cash_bank_book',
    'finance_cash_bank_book_rows_api': 'financeApp.api.cash_bank_book',
}


def _route_modules_by_name():
    routes = {}
    for pattern in urlpatterns:
        name = getattr(pattern, 'name', '') or ''
        callback = getattr(pattern, 'callback', None)
        view_class = getattr(callback, 'view_class', None)
        target = view_class or callback
        routes[name] = target.__module__ if target else ''
    return routes


class FinanceApiRouteOwnershipTests(SimpleTestCase):
    def test_expected_finance_routes_resolve_to_finance_app_modules(self):
        routes = _route_modules_by_name()
        self.assertEqual(set(EXPECTED_FINANCE_ROUTES), set(EXPECTED_FINANCE_ROUTES).intersection(routes))
        for route_name, expected_module in EXPECTED_FINANCE_ROUTES.items():
            self.assertEqual(routes[route_name], expected_module, route_name)

    def test_no_finance_named_api_route_points_to_management_views_api(self):
        offenders = []
        for pattern in urlpatterns:
            name = getattr(pattern, 'name', '') or ''
            route = str(getattr(pattern.pattern, '_route', pattern.pattern))
            callback = getattr(pattern, 'callback', None)
            view_class = getattr(callback, 'view_class', None)
            target = view_class or callback
            module = target.__module__ if target else ''
            is_finance_route = any(term in name.lower() or term in route.lower() for term in FINANCE_ROUTE_TERMS)
            if is_finance_route and module == 'managementApp.api.views_api':
                offenders.append(name)
        self.assertEqual(offenders, [])


class LibraryFineFinanceSyncTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='finance-user')
        self.school = SchoolDetail.objects.create(schoolName='Demo School', address='Demo Address')
        self.session = SchoolSession.objects.create(
            schoolID=self.school,
            sessionYear='2026-2027',
            startDate=date(2026, 4, 1),
            endDate=date(2027, 3, 31),
            isCurrent=True,
        )
        self.standard = Standard.objects.create(
            schoolID=self.school,
            sessionID=self.session,
            name='Class 1',
        )
        self.student = Student.objects.create(
            schoolID=self.school,
            sessionID=self.session,
            standardID=self.standard,
            name='Student One',
        )
        self.teacher = TeacherDetail.objects.create(
            schoolID=self.school,
            sessionID=self.session,
            name='Teacher One',
            salary=Decimal('25000.00'),
        )

    def test_student_library_fine_creates_charge_receipt_and_ledger_rows(self):
        member = LibraryMember.objects.create(
            schoolID=self.school,
            sessionID=self.session,
            memberType='student',
            student=self.student,
            memberCode='LIB-STU-1',
        )
        fine = LibraryFine.objects.create(
            schoolID=self.school,
            sessionID=self.session,
            member=member,
            reason='overdue',
            amount=Decimal('25.00'),
            paidAmount=Decimal('10.00'),
            paidDate=date(2026, 5, 1),
            status='pending',
        )

        sync_library_fine_finance(fine_obj=fine, user_obj=self.user)

        charge = StudentCharge.objects.get(sourceModule='library_fine_charge', sourceRecordID=str(fine.id))
        self.assertEqual(charge.netAmount, Decimal('25.00'))
        self.assertEqual(charge.paidAmount, Decimal('10.00'))
        self.assertEqual(charge.feeHeadID.code, 'LIBRARY_FINE')
        receipt = PaymentReceipt.objects.get(sourceModule='library_fine_receipt', sourceRecordID=str(fine.id))
        self.assertEqual(receipt.amountReceived, Decimal('10.00'))
        self.assertTrue(FinanceTransaction.objects.filter(sourceModule='library_fine_charge', sourceRecordID=str(fine.id)).exists())
        self.assertTrue(FinanceTransaction.objects.filter(sourceModule='library_fine_receipt', sourceRecordID=str(fine.id)).exists())

    def test_staff_library_fine_posts_direct_finance_transactions(self):
        member = LibraryMember.objects.create(
            schoolID=self.school,
            sessionID=self.session,
            memberType='staff',
            staff=self.teacher,
            memberCode='LIB-STF-1',
        )
        fine = LibraryFine.objects.create(
            schoolID=self.school,
            sessionID=self.session,
            member=member,
            reason='manual',
            amount=Decimal('40.00'),
            paidAmount=Decimal('40.00'),
            paidDate=date(2026, 5, 2),
            status='paid',
        )

        sync_library_fine_finance(fine_obj=fine, user_obj=self.user)

        charge_txn = FinanceTransaction.objects.get(sourceModule='library_fine_charge', sourceRecordID=str(fine.id))
        receipt_txn = FinanceTransaction.objects.get(sourceModule='library_fine_receipt', sourceRecordID=str(fine.id))
        self.assertEqual(charge_txn.entries.count(), 2)
        self.assertEqual(receipt_txn.entries.count(), 2)
        self.assertFalse(PaymentReceipt.objects.filter(sourceModule='library_fine_receipt', sourceRecordID=str(fine.id)).exists())
