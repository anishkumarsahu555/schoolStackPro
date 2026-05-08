from django.contrib import admin

from .models import CertificateDesign, CertificateIssue, CertificateSequence, CertificateType


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
    list_display = ('certificateNumber', 'certificateTypeID', 'recipientCategory', 'issueDate', 'issueStatus', 'schoolID')
    list_filter = ('recipientCategory', 'issueDate', 'issueStatus')
    search_fields = ('certificateNumber', 'verificationToken')


@admin.register(CertificateSequence)
class CertificateSequenceAdmin(admin.ModelAdmin):
    list_display = ('prefix', 'currentValue', 'certificateTypeID', 'sessionID', 'schoolID')
    list_filter = ('schoolID', 'sessionID')
    search_fields = ('prefix',)
