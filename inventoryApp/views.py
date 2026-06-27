from django.shortcuts import render
from homeApp.utils import login_required
from utils.logger import logger

@login_required
def dashboard(request):
    logger.info(f'Inventory dashboard opened by user={request.user.id}')
    return render(request, 'inventoryApp/dashboard.html')

@login_required
def manage_categories(request):
    logger.info(f'Inventory categories page opened by user={request.user.id}')
    return render(request, 'inventoryApp/manage_categories.html')

@login_required
def manage_items(request):
    logger.info(f'Inventory items page opened by user={request.user.id}')
    return render(request, 'inventoryApp/manage_items.html')

@login_required
def manage_suppliers(request):
    logger.info(f'Inventory suppliers page opened by user={request.user.id}')
    return render(request, 'inventoryApp/manage_suppliers.html')

@login_required
def manage_stock(request):
    logger.info(f'Inventory stock ledger page opened by user={request.user.id}')
    return render(request, 'inventoryApp/manage_stock.html')

@login_required
def manage_assets(request):
    logger.info(f'Inventory assets page opened by user={request.user.id}')
    return render(request, 'inventoryApp/manage_assets.html')

@login_required
def manage_allocations(request):
    logger.info(f'Inventory allocations page opened by user={request.user.id}')
    return render(request, 'inventoryApp/manage_allocations.html')
