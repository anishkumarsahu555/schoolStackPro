from datetime import date

from django.db.models import Sum

from transportApp.models import TransportAssignment, TransportFeeRecord


def build_my_transport_context(assignee_type, assignee, session_id):
    if not assignee or not session_id:
        return {
            'assignment': None,
            'recent_fee_records': [],
            'current_fee_record': None,
            'fee_summary': {'net': 0, 'paid': 0, 'due': 0},
        }

    filters = {
        'assigneeType': assignee_type,
        'sessionID_id': session_id,
        'schoolID_id': assignee.schoolID_id,
        'isDeleted': False,
        'isActive': True,
    }
    if assignee_type == 'student':
        filters['studentID_id'] = assignee.id
    else:
        filters['teacherID_id'] = assignee.id

    assignment = (
        TransportAssignment.objects
        .select_related('routeID', 'pickupStopID', 'dropStopID', 'vehicleID', 'vehicleID__driverID')
        .filter(**filters)
        .order_by('-lastUpdatedOn')
        .first()
    )
    if not assignment:
        return {
            'assignment': None,
            'recent_fee_records': [],
            'current_fee_record': None,
            'fee_summary': {'net': 0, 'paid': 0, 'due': 0},
        }

    recent_fee_records = list(
        TransportFeeRecord.objects
        .filter(assignmentID=assignment, isDeleted=False)
        .order_by('-feeYear', '-feeMonth')[:6]
    )
    today = date.today()
    current_fee_record = next(
        (record for record in recent_fee_records if record.feeMonth == today.month and record.feeYear == today.year),
        None,
    )
    summary = (
        TransportFeeRecord.objects
        .filter(assignmentID=assignment, isDeleted=False)
        .aggregate(
            net=Sum('netAmount'),
            paid=Sum('paidAmount'),
            due=Sum('balanceAmount'),
        )
    )
    return {
        'assignment': assignment,
        'recent_fee_records': recent_fee_records,
        'current_fee_record': current_fee_record,
        'fee_summary': {
            'net': summary.get('net') or 0,
            'paid': summary.get('paid') or 0,
            'due': summary.get('due') or 0,
        },
    }
