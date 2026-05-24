from django.shortcuts import render

from homeApp.utils import login_required
from utils.logger import logger


@login_required
def dashboard(request):
    logger.info(f'Transport dashboard opened by user={request.user.id}')
    return render(request, 'transportApp/dashboard.html')


@login_required
def manage_routes(request):
    logger.info(f'Transport routes page opened by user={request.user.id}')
    return render(request, 'transportApp/manage_routes.html')


@login_required
def manage_vehicles(request):
    logger.info(f'Transport vehicles page opened by user={request.user.id}')
    return render(request, 'transportApp/manage_vehicles.html')


@login_required
def manage_drivers(request):
    logger.info(f'Transport drivers page opened by user={request.user.id}')
    return render(request, 'transportApp/manage_drivers.html')


@login_required
def manage_assignments(request):
    logger.info(f'Transport assignments page opened by user={request.user.id}')
    return render(request, 'transportApp/manage_assignments.html')


@login_required
def manage_fee_mapping(request):
    logger.info(f'Transport fee mapping page opened by user={request.user.id}')
    return render(request, 'transportApp/manage_fee_mapping.html')


@login_required
def manage_fee_tracking(request):
    logger.info(f'Transport fee tracking page opened by user={request.user.id}')
    return render(request, 'transportApp/manage_fee_tracking.html')


@login_required
def manage_reports(request):
    logger.info(f'Transport reports page opened by user={request.user.id}')
    return render(request, 'transportApp/manage_reports.html')
