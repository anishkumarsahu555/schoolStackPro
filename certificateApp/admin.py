from django.contrib import admin

from .models import CertificateDesign, CertificateIssue, CertificateType


@admin.register(CertificateType)
class CertificateTypeAdmin(admin.ModelAdmin):
    list_display = ('name', 'recipientCategory', 'schoolID', 'isActive', 'isSystem')
    list_filter = ('recipientCategory', 'isActive', 'isSystem')
    search_fields = ('name', 'slug')


@admin.register(CertificateDesign)
class CertificateDesignAdmin(admin.ModelAdmin):
    list_display = ('name', 'certificateTypeID', 'schoolID', 'pageSize', 'orientation', 'isCustom', 'isActive')
    list_filter = ('pageSize', 'orientation', 'isCustom', 'isActive')
    search_fields = ('name', 'slug')


@admin.register(CertificateIssue)
class CertificateIssueAdmin(admin.ModelAdmin):
    list_display = ('certificateNumber', 'certificateTypeID', 'recipientCategory', 'issueDate', 'schoolID')
    list_filter = ('recipientCategory', 'issueDate')
    search_fields = ('certificateNumber',)

