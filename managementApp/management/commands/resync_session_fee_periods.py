from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from homeApp.models import SchoolSession
from managementApp.services.fee_period_sync import sync_session_fee_periods


class Command(BaseCommand):
    help = (
        "Resync StudentFee period fields for a session after changing session start/end dates. "
        "Updates month/feeMonth/feeYear/periodStartDate/periodEndDate/dueDate."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--session-id",
            type=int,
            required=True,
            help="SchoolSession id to resync.",
        )
        parser.add_argument(
            "--create-missing",
            action="store_true",
            help="Create missing month fee rows for each student+class pair in the session.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview changes without committing to the database.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        session_id = int(options["session_id"])
        create_missing = bool(options["create_missing"])
        dry_run = bool(options["dry_run"])

        session_obj = SchoolSession.objects.filter(pk=session_id, isDeleted=False).first()
        if not session_obj:
            raise CommandError(f"Session not found or deleted: {session_id}")

        self.stdout.write(self.style.NOTICE(
            f"Resyncing fee periods for session={session_obj.pk} ({session_obj.sessionYear or 'N/A'}) "
            f"create_missing={create_missing} dry_run={dry_run}"
        ))
        result = sync_session_fee_periods(
            session_obj=session_obj,
            create_missing=create_missing,
            dry_run=dry_run,
        )
        summary = (
            f"groups={result['groups']}, updated={result['updated']}, created={result['created']}, "
            f"unchanged={result['unchanged']}, missing={result['missing']}"
        )

        if dry_run:
            transaction.set_rollback(True)
            self.stdout.write(self.style.WARNING(f"DRY-RUN complete: {summary}"))
        else:
            self.stdout.write(self.style.SUCCESS(f"Resync complete: {summary}"))
