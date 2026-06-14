from django.urls import path
from .views_api import (
    categories_api,
    category_detail_api,
    delete_category_api,
    items_api,
    item_detail_api,
    delete_item_api,
    suppliers_api,
    supplier_detail_api,
    delete_supplier_api,
    stock_ledger_api,
    allocations_api,
    return_allocation_api,
    delete_allocation_api,
    low_stock_api,
    assets_api,
    asset_detail_api,
    delete_asset_api,
    dashboard_summary,
    
    CategoryListJson,
    SupplierListJson,
    ItemListJson,
    StockLedgerListJson,
    AllocationListJson,
    AssetListJson,
)

urlpatterns = [
    # Metadata and Low Stock Alerts
    path('dashboard-summary/', dashboard_summary, name='dashboard_summary'),
    path('low-stock/', low_stock_api, name='low_stock_api'),

    # Category Endpoints
    path('categories/', categories_api, name='categories_api'),
    path('categories/detail/', category_detail_api, name='category_detail_api'),
    path('categories/delete/', delete_category_api, name='delete_category_api'),

    # Supplier Endpoints
    path('suppliers/', suppliers_api, name='suppliers_api'),
    path('suppliers/detail/', supplier_detail_api, name='supplier_detail_api'),
    path('suppliers/delete/', delete_supplier_api, name='delete_supplier_api'),

    # Item Registry Endpoints
    path('items/', items_api, name='items_api'),
    path('items/detail/', item_detail_api, name='item_detail_api'),
    path('items/delete/', delete_item_api, name='delete_item_api'),

    # Stock Ledger Endpoints
    path('stock/', stock_ledger_api, name='stock_ledger_api'),

    # Asset Registry Endpoints
    path('assets/', assets_api, name='assets_api'),
    path('assets/detail/', asset_detail_api, name='asset_detail_api'),
    path('assets/delete/', delete_asset_api, name='delete_asset_api'),

    # Allocations Endpoints
    path('allocations/', allocations_api, name='allocations_api'),
    path('allocations/return/', return_allocation_api, name='return_allocation_api'),
    path('allocations/delete/', delete_allocation_api, name='delete_allocation_api'),

    # Datatables Server Side JSON Endpoints
    path('dt/categories/', CategoryListJson.as_view(), name='CategoryListJson'),
    path('dt/suppliers/', SupplierListJson.as_view(), name='SupplierListJson'),
    path('dt/items/', ItemListJson.as_view(), name='ItemListJson'),
    path('dt/stock/', StockLedgerListJson.as_view(), name='StockLedgerListJson'),
    path('dt/allocations/', AllocationListJson.as_view(), name='AllocationListJson'),
    path('dt/assets/', AssetListJson.as_view(), name='AssetListJson'),
]
