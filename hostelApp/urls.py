from django.urls import path

from hostelApp import views


urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('admissions/', views.manage_admissions, name='manage_admissions'),
    path('buildings/', views.manage_buildings, name='manage_buildings'),
    path('rooms/', views.manage_rooms, name='manage_rooms'),
    path('beds/', views.manage_beds, name='manage_beds'),
    path('assignments/', views.manage_assignments, name='manage_assignments'),
    path('fee-mapping/', views.manage_fee_mapping, name='manage_fee_mapping'),
    path('fee-tracking/', views.manage_fee_tracking, name='manage_fee_tracking'),
    path('reports/', views.manage_reports, name='manage_reports'),
]
