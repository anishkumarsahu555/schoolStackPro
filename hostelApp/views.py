from datetime import date

from django.shortcuts import render

from homeApp.models import SchoolSession
from homeApp.session_utils import get_session_month_sequence
from homeApp.utils import login_required
from utils.logger import logger


def _current_session_obj(request):
    current_session = request.session.get('current_session', {}) or {}
    session_id = current_session.get('Id') or current_session.get('SessionID')
    if session_id:
        session_obj = SchoolSession.objects.filter(pk=session_id, isDeleted=False).first()
        if session_obj:
            return session_obj

    school_id = current_session.get('SchoolID')
    if school_id:
        return SchoolSession.objects.filter(schoolID_id=school_id, isDeleted=False, isCurrent=True).first()
    return None


def _hostel_fee_period_context(session_obj):
    month_sequence = get_session_month_sequence(session_obj)
    today = date.today()
    periods = []
    default_period = None
    latest_elapsed_period = None

    for month_name, year, month_no, start_date, end_date in month_sequence:
        period = {
            'month': month_no,
            'year': year,
            'label': f'{month_name[:3]} {year}',
            'monthLabel': month_name,
            'monthShortLabel': month_name[:3],
            'startDate': start_date.isoformat(),
            'endDate': end_date.isoformat(),
        }
        periods.append(period)
        if start_date <= today <= end_date:
            default_period = period
        if start_date <= today:
            latest_elapsed_period = period

    if not default_period:
        default_period = latest_elapsed_period or (periods[0] if periods else None)

    return {
        'periods': periods,
        'defaultMonth': default_period['month'] if default_period else today.month,
        'defaultYear': default_period['year'] if default_period else today.year,
        'sessionLabel': getattr(session_obj, 'sessionYear', '') if session_obj else '',
    }


@login_required
def dashboard(request):
    logger.info(f'Hostel dashboard opened by user={request.user.id}')
    return render(request, 'hostelApp/dashboard.html')


@login_required
def manage_admissions(request):
    logger.info(f'Hostel admissions page opened by user={request.user.id}')
    return render(request, 'hostelApp/manage_admissions.html')


@login_required
def manage_buildings(request):
    logger.info(f'Hostel buildings page opened by user={request.user.id}')
    return render(request, 'hostelApp/manage_buildings.html')


@login_required
def manage_rooms(request):
    logger.info(f'Hostel rooms page opened by user={request.user.id}')
    return render(request, 'hostelApp/manage_rooms.html')


@login_required
def manage_beds(request):
    logger.info(f'Hostel beds page opened by user={request.user.id}')
    return render(request, 'hostelApp/manage_beds.html')


@login_required
def manage_assignments(request):
    logger.info(f'Hostel assignments page opened by user={request.user.id}')
    return render(request, 'hostelApp/manage_assignments.html')


@login_required
def manage_fee_mapping(request):
    logger.info(f'Hostel fee mapping page opened by user={request.user.id}')
    return render(request, 'hostelApp/manage_fee_mapping.html')


@login_required
def manage_fee_tracking(request):
    logger.info(f'Hostel fee tracking page opened by user={request.user.id}')
    return render(
        request,
        'hostelApp/manage_fee_tracking.html',
        {'hostel_fee_period_config': _hostel_fee_period_context(_current_session_obj(request))},
    )


@login_required
def manage_reports(request):
    logger.info(f'Hostel reports page opened by user={request.user.id}')
    return render(request, 'hostelApp/manage_reports.html')
