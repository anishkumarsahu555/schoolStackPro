from django.urls import path

from .views import (
    create_design,
    dashboard,
    design_detail,
    design_library,
    generator,
    generator_live_preview,
    issue_download_pdf,
    issue_preview,
    issue_print,
)


urlpatterns = [
    path('', dashboard, name='dashboard'),
    path('designs/', design_library, name='design_library'),
    path('designs/create/', create_design, name='create_design'),
    path('designs/<int:design_id>/', design_detail, name='design_detail'),
    path('generate/', generator, name='generator'),
    path('generate/live-preview/', generator_live_preview, name='generator_live_preview'),
    path('issue/<int:issue_id>/', issue_preview, name='issue_preview'),
    path('issue/<int:issue_id>/print/', issue_print, name='issue_print'),
    path('issue/<int:issue_id>/download-pdf/', issue_download_pdf, name='issue_download_pdf'),
]
