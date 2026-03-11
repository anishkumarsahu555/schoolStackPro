from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from homeApp.models import SchoolSession
from managementApp.models import Student
from managementApp.services.fee_period_sync import sync_session_fee_periods


class Command(BaseCommand):
    help = (
        "Process pending session fee resyncs in batches. "
        "Use this in cron/worker for scalable sync after session date edits."
    )

    def add_arguments(self, parser):
        parser.add_argument('--session-id', type=int, help='Process only one session id.')
        parser.add_argument('--batch-size', type=int, default=200, help='Student pairs per batch (default: 200).')
        parser.add_argument('--max-sessions', type=int, default=20, help='Max sessions to process in one run.')

    def handle(self, *args, **options):
        session_id = options.get('session_id')
        batch_size = max(10, int(options.get('batch_size') or 200))
        max_sessions = max(1, int(options.get('max_sessions') or 20))

        sessions_qs = SchoolSession.objects.filter(isDeleted=False)
        if session_id:
            sessions_qs = sessions_qs.filter(pk=session_id)
        else:
            sessions_qs = sessions_qs.filter(feeResyncStatus__in=['pending', 'running'])

        sessions = list(sessions_qs.order_by('feeResyncRequestedAt', 'id')[:max_sessions])
        if not sessions:
            self.stdout.write(self.style.WARNING('No pending session fee resync jobs found.'))
            return

        for session in sessions:
            self._process_session(session, batch_size=batch_size)

    def _process_session(self, session_obj, *, batch_size):
        self.stdout.write(self.style.NOTICE(f'Processing session {session_obj.pk} ({session_obj.sessionYear or "N/A"})'))

        SchoolSession.objects.filter(pk=session_obj.pk).update(
            feeResyncStatus='running',
            feeResyncStartedAt=timezone.now(),
            feeResyncFinishedAt=None,
            feeResyncError='',
            feeResyncUpdatedCount=0,
            feeResyncCreatedCount=0,
        )

        pairs = list(Student.objects.filter(
            sessionID_id=session_obj.pk,
            isDeleted=False,
            standardID__isnull=False,
        ).values_list('id', 'standardID_id').order_by('id'))

        total_updated = 0
        total_created = 0

        try:
            for start in range(0, len(pairs), batch_size):
                batch_pairs = pairs[start:start + batch_size]
                with transaction.atomic():
                    result = sync_session_fee_periods(
                        session_obj=session_obj,
                        create_missing=True,
                        dry_run=False,
                        target_pairs=batch_pairs,
                    )
                total_updated += result.get('updated', 0)
                total_created += result.get('created', 0)

            SchoolSession.objects.filter(pk=session_obj.pk).update(
                feeResyncStatus='done',
                feeResyncFinishedAt=timezone.now(),
                feeResyncUpdatedCount=total_updated,
                feeResyncCreatedCount=total_created,
                feeResyncError='',
            )
            self.stdout.write(self.style.SUCCESS(
                f'Session {session_obj.pk} completed: updated={total_updated}, created={total_created}'
            ))
        except Exception as exc:
            SchoolSession.objects.filter(pk=session_obj.pk).update(
                feeResyncStatus='failed',
                feeResyncFinishedAt=timezone.now(),
                feeResyncUpdatedCount=total_updated,
                feeResyncCreatedCount=total_created,
                feeResyncError=str(exc)[:2000],
            )
            self.stdout.write(self.style.ERROR(
                f'Session {session_obj.pk} failed after updated={total_updated}, created={total_created}: {exc}'
            ))
