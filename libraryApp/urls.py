from django.urls import path

from libraryApp import views


urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('books/', views.manage_books, name='manage_books'),
    path('categories/', views.manage_categories, name='manage_categories'),
    path('authors/', views.manage_authors, name='manage_authors'),
    path('publishers/', views.manage_publishers, name='manage_publishers'),
    path('copies/', views.manage_copies, name='manage_copies'),
    path('members/', views.manage_members, name='manage_members'),
    path('members/cards/', views.member_cards, name='member_cards'),
    path('members/card-design/', views.member_card_design, name='member_card_design'),
    path('issue-book/', views.issue_book, name='issue_book'),
    path('issue-history/', views.issue_history, name='issue_history'),
    path('return-book/', views.return_book, name='return_book'),
    path('reservations/', views.manage_reservations, name='manage_reservations'),
    path('fines/', views.manage_fines, name='manage_fines'),
    path('settings/', views.settings, name='settings'),
    path('reports/', views.reports, name='reports'),
]
