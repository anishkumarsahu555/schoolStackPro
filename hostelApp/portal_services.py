from datetime import date

from django.db.models import Sum

from hostelApp.models import HostelAssignment, HostelFeeRecord


def build_my_hostel_context(student, session_id):
    empty = {
        'hostel_assignment': None,
        'hostel_recent_fee_records': [],
        'hostel_current_fee_record': None,
        'hostel_fee_summary': {'net': 0, 'paid': 0, 'due': 0},
    }
    if not student or not session_id:
        return empty

    assignment = (
        HostelAssignment.objects
        .select_related('buildingID', 'roomID', 'roomID__roomTypeID', 'bedID', 'admissionID')
        .filter(
            studentID_id=student.id,
            sessionID_id=session_id,
            schoolID_id=student.schoolID_id,
            isDeleted=False,
            isActive=True,
        )
        .order_by('-lastUpdatedOn')
        .first()
    )
    if not assignment:
        return empty

    recent_fee_records = list(
        HostelFeeRecord.objects
        .filter(assignmentID=assignment, isDeleted=False)
        .order_by('-feeYear', '-feeMonth')[:6]
    )
    today = date.today()
    current_fee_record = next(
        (record for record in recent_fee_records if record.feeMonth == today.month and record.feeYear == today.year),
        None,
    )
    summary = (
        HostelFeeRecord.objects
        .filter(assignmentID=assignment, isDeleted=False)
        .aggregate(net=Sum('netAmount'), paid=Sum('paidAmount'), due=Sum('balanceAmount'))
    )
    return {
        'hostel_assignment': assignment,
        'hostel_recent_fee_records': recent_fee_records,
        'hostel_current_fee_record': current_fee_record,
        'hostel_fee_summary': {
            'net': summary.get('net') or 0,
            'paid': summary.get('paid') or 0,
            'due': summary.get('due') or 0,
        },
    }
