from django.urls import path

from .views import (
    create_design,
    dashboard,
    design_detail,
    design_library,
    design_quick_preview,
    duplicate_design,
    edit_design,
    generator,
    generator_live_preview,
    issue_cancel,
    issue_download_pdf,
    issue_preview,
    issue_print,
    issue_reissue,
    set_default_design,
    verify_certificate,
)


urlpatterns = [
    path('', dashboard, name='dashboard'),
    path('designs/', design_library, name='design_library'),
    path('designs/create/', create_design, name='create_design'),
    path('designs/<int:design_id>/edit/', edit_design, name='edit_design'),
    path('designs/<int:design_id>/duplicate/', duplicate_design, name='duplicate_design'),
    path('designs/<int:design_id>/set-default/', set_default_design, name='set_default_design'),
    path('designs/<int:design_id>/quick-preview/', design_quick_preview, name='design_quick_preview'),
    path('designs/<int:design_id>/', design_detail, name='design_detail'),
    path('generate/', generator, name='generator'),
    path('generate/live-preview/', generator_live_preview, name='generator_live_preview'),
    path('issue/<int:issue_id>/', issue_preview, name='issue_preview'),
    path('issue/<int:issue_id>/print/', issue_print, name='issue_print'),
    path('issue/<int:issue_id>/download-pdf/', issue_download_pdf, name='issue_download_pdf'),
    path('issue/<int:issue_id>/cancel/', issue_cancel, name='issue_cancel'),
    path('issue/<int:issue_id>/reissue/', issue_reissue, name='issue_reissue'),
    path('verify/<str:token>/', verify_certificate, name='verify_certificate'),
]
