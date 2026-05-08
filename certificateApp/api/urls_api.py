from django.urls import path

from .views_api import create_certificate_issue_api, create_certificate_issues_bulk_api, get_certificate_generator_meta_api


urlpatterns = [
    path('get_certificate_generator_meta_api', get_certificate_generator_meta_api, name='get_certificate_generator_meta_api'),
    path('create_certificate_issue_api', create_certificate_issue_api, name='create_certificate_issue_api'),
    path('create_certificate_issues_bulk_api', create_certificate_issues_bulk_api, name='create_certificate_issues_bulk_api'),
]
