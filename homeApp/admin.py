from django.contrib import admin
from django.contrib import messages
from django.contrib.admin.widgets import AdminDateWidget
from django.conf import settings
from django.forms import Textarea
from django.http import HttpResponseRedirect
from django.urls import path, reverse
from django.db import models
from django.utils.html import format_html
from django.utils import timezone
import os
import subprocess
import sys
from pathlib import Path

from .license import build_license_context
from .models import SchoolDetail, SchoolOwner, SchoolSession, SchoolSocialLink, WebPushSubscription


def _all_concrete_fields(model):
    return tuple(field.name for field in model._meta.concrete_fields)


def _editable_concrete_fields(model):
    return tuple(
        field.name
        for field in model._meta.concrete_fields
        if getattr(field, 'editable', False) and not getattr(field, 'auto_created', False)
    )


class AllFieldsAdmin(admin.ModelAdmin):
    list_per_page = 50

    def get_list_display(self, request):
        return _all_concrete_fields(self.model)


@admin.register(SchoolOwner)
class SchoolOwnerAdmin(AllFieldsAdmin):
    search_fields = ('=id', 'name', 'email', 'username', 'phoneNumber')


@admin.register(SchoolDetail)
class SchoolDetailAdmin(AllFieldsAdmin):
    search_fields = ('=id', 'schoolName', 'name', 'email', 'phoneNumber', 'city', 'state')
    list_filter = ('activationEnabled', 'isDeleted')
    readonly_fields = ('id', 'datetime', 'lastUpdatedOn', 'license_status_badge', 'license_summary')
    formfield_overrides = {
        models.TextField: {'widget': Textarea(attrs={'rows': 4, 'cols': 100})},
        models.DateField: {'widget': AdminDateWidget(attrs={'type': 'date'})},
    }

    def get_list_display(self, request):
        return _all_concrete_fields(self.model) + ('license_status_badge',)

    def get_fieldsets(self, request, obj=None):
        fields = list(_editable_concrete_fields(self.model))
        activation_fields = [
            'activationEnabled', 'activationStartDate', 'activationEndDate', 'activationMessage',
        ]
        general_fields = [field for field in fields if field not in activation_fields]
        return (
            ('School Profile', {'fields': ['id'] + general_fields + ['datetime', 'lastUpdatedOn']}),
            ('License Control', {'fields': activation_fields + ['license_status_badge', 'license_summary']}),
        )

    @admin.display(description='License status')
    def license_status_badge(self, obj):
        license_info = build_license_context(obj)
        palette = {
            'positive': ('#ecfdf5', '#047857', '#10b981'),
            'warning': ('#fff7ed', '#b45309', '#f59e0b'),
            'negative': ('#fef2f2', '#b91c1c', '#ef4444'),
            'neutral': ('#eff6ff', '#1d4ed8', '#60a5fa'),
        }
        background, color, border = palette.get(license_info['badge_class'], palette['neutral'])
        return format_html(
            '<span style="display:inline-flex;align-items:center;padding:0.32rem 0.7rem;'
            'border-radius:999px;border:1px solid {};background:{};color:{};font-weight:700;">{}</span>',
            border,
            background,
            color,
            license_info['label'],
        )

    @admin.display(description='License summary')
    def license_summary(self, obj):
        license_info = build_license_context(obj)
        parts = [license_info['detail']]
        if license_info.get('valid_from'):
            parts.append(f"Start: {license_info['valid_from']:%d %b %Y}")
        if license_info.get('valid_until'):
            parts.append(f"End: {license_info['valid_until']:%d %b %Y}")
        return " | ".join(parts)


@admin.register(SchoolSocialLink)
class SchoolSocialLinkAdmin(AllFieldsAdmin):
    search_fields = ('=id',)


@admin.register(SchoolSession)
class SchoolSessionAdmin(AllFieldsAdmin):
    list_filter = ['schoolID', 'isCurrent', 'feeResyncStatus', 'isDeleted']
    search_fields = ['=id', 'sessionYear', 'schoolID__schoolName']
    formfield_overrides = {
        models.DateField: {'widget': AdminDateWidget(attrs={'type': 'date'})},
    }
    readonly_fields = [
        'feeResyncRequestedAt', 'feeResyncStartedAt', 'feeResyncFinishedAt',
        'feeResyncUpdatedCount', 'feeResyncCreatedCount', 'feeResyncError',
    ]
    actions = ['run_fee_resync_now']

    def get_list_display(self, request):
        return _all_concrete_fields(self.model) + ('run_resync_button',)

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                '<path:object_id>/run-resync/',
                self.admin_site.admin_view(self.run_resync_view),
                name='homeApp_schoolsession_run_resync',
            ),
        ]
        return custom_urls + urls

    @admin.display(description='Resync')
    def run_resync_button(self, obj):
        if not obj or not obj.pk:
            return '-'
        url = reverse('admin:homeApp_schoolsession_run_resync', args=[obj.pk])
        return format_html('<a class="button" href="{}">Run Resync</a>', url)

    def _start_background_resync(self, *, session_count):
        manage_py = Path(settings.BASE_DIR) / 'manage.py'
        logs_dir = Path(settings.BASE_DIR) / 'logs'
        logs_dir.mkdir(parents=True, exist_ok=True)
        log_path = logs_dir / 'fee_resync_worker.log'

        env = os.environ.copy()
        env.setdefault('DJANGO_SETTINGS_MODULE', 'schoolStackPro.settings')

        with open(log_path, 'a', encoding='utf-8') as log_file:
            subprocess.Popen(
                [
                    sys.executable,
                    str(manage_py),
                    'process_pending_fee_resyncs',
                    '--max-sessions', str(max(1, session_count)),
                    '--batch-size', '200',
                ],
                cwd=str(settings.BASE_DIR),
                env=env,
                stdout=log_file,
                stderr=log_file,
                start_new_session=True,
            )

    def run_resync_view(self, request, object_id):
        obj = self.get_object(request, object_id)
        if not obj:
            self.message_user(request, 'Session not found.', level=messages.ERROR)
            return HttpResponseRedirect(reverse('admin:homeApp_schoolsession_changelist'))

        SchoolSession.objects.filter(pk=obj.pk).update(
            feeResyncStatus='pending',
            feeResyncRequestedAt=timezone.now(),
            feeResyncStartedAt=None,
            feeResyncFinishedAt=None,
            feeResyncUpdatedCount=0,
            feeResyncCreatedCount=0,
            feeResyncError='',
        )
        try:
            self._start_background_resync(session_count=1)
            self.message_user(request, f'Session {obj.pk} queued and worker started.', level=messages.SUCCESS)
        except Exception as exc:
            self.message_user(request, f'Failed to start background worker: {exc}', level=messages.ERROR)

        return HttpResponseRedirect(reverse('admin:homeApp_schoolsession_change', args=[obj.pk]))

    @admin.action(description='Run Resync Now (background)')
    def run_fee_resync_now(self, request, queryset):
        session_ids = list(queryset.values_list('id', flat=True))
        if not session_ids:
            self.message_user(request, 'No session selected.', level=messages.WARNING)
            return

        updated_count = queryset.update(
            feeResyncStatus='pending',
            feeResyncRequestedAt=timezone.now(),
            feeResyncStartedAt=None,
            feeResyncFinishedAt=None,
            feeResyncUpdatedCount=0,
            feeResyncCreatedCount=0,
            feeResyncError='',
        )

        try:
            self._start_background_resync(session_count=len(session_ids))
            self.message_user(
                request,
                f'Marked {updated_count} session(s) as pending and started background worker.',
                level=messages.SUCCESS,
            )
        except Exception as exc:
            self.message_user(
                request,
                f'Failed to start background worker: {exc}',
                level=messages.ERROR,
            )


@admin.register(WebPushSubscription)
class WebPushSubscriptionAdmin(AllFieldsAdmin):
    search_fields = ('=id', 'endpointHash', 'endpoint', 'userID__username', 'schoolID__schoolName')
    list_filter = ('appName', 'isActive')
