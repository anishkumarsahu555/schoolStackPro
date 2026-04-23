from django.core.management.base import BaseCommand
from django.db import transaction

from financeApp.services import backfill_payroll_run_numbers


class Command(BaseCommand):
    help = (
        "Backfill missing payroll run numbers using the current Finance Settings numbering rules. "
        "Only payroll runs without a payrollRunNo are updated."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--school-id',
            type=int,
            help='Limit the backfill to one school id.',
        )
        parser.add_argument(
            '--session-id',
            type=int,
            help='Limit the backfill to one school session id.',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Preview how many payroll runs would be updated without saving changes.',
        )

    @transaction.atomic
    def handle(self, *args, **options):
        school_id = options.get('school_id')
        session_id = options.get('session_id')
        dry_run = bool(options.get('dry_run'))

        self.stdout.write(self.style.NOTICE(
            f'Backfilling payroll run numbers school_id={school_id or "ALL"} '
            f'session_id={session_id or "ALL"} dry_run={dry_run}'
        ))

        result = backfill_payroll_run_numbers(
            school_id=school_id,
            session_id=session_id,
            user_obj=None,
        )

        summary = f'updated={result["updated"]}, scopes={result["scopes"]}'
        if dry_run:
            transaction.set_rollback(True)
            self.stdout.write(self.style.WARNING(f'DRY-RUN complete: {summary}'))
        else:
            self.stdout.write(self.style.SUCCESS(f'Backfill complete: {summary}'))
