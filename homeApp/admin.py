from django.contrib import admin
from django.contrib import messages
from django.contrib.admin.widgets import AdminDateWidget
from django.conf import settings
from django.http import HttpResponseRedirect
from django.urls import path, reverse
from django.db import models
from django.utils.html import format_html
from django.utils import timezone
import os
import subprocess
import sys
from pathlib import Path

from .models import SchoolDetail, SchoolOwner, SchoolSession, SchoolSocialLink, WebPushSubscription


def _all_concrete_fields(model):
    return tuple(field.name for field in model._meta.concrete_fields)


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
