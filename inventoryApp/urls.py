from django.urls import path
from . import views

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('dashboard/', views.dashboard, name='dashboard'),
    path('categories/', views.manage_categories, name='manage_categories'),
    path('items/', views.manage_items, name='manage_items'),
    path('suppliers/', views.manage_suppliers, name='manage_suppliers'),
    path('stock/', views.manage_stock, name='manage_stock'),
    path('assets/', views.manage_assets, name='manage_assets'),
    path('allocations/', views.manage_allocations, name='manage_allocations'),
]
