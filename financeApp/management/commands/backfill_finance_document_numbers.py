from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from financeApp.services import backfill_finance_document_numbers


class Command(BaseCommand):
    help = (
        "Backfill missing finance document numbers using the current Finance Settings numbering rules. "
        "Supports receipts, vouchers, refunds, transactions, and payroll runs."
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
            '--type',
            dest='document_types',
            nargs='+',
            choices=['receipt', 'voucher', 'refund', 'transaction', 'payroll'],
            help='Limit the backfill to specific document types.',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Preview how many finance documents would be updated without saving changes.',
        )

    @transaction.atomic
    def handle(self, *args, **options):
        school_id = options.get('school_id')
        session_id = options.get('session_id')
        dry_run = bool(options.get('dry_run'))
        document_types = options.get('document_types') or ['receipt', 'voucher', 'refund', 'transaction']

        if session_id and not school_id:
            self.stdout.write(self.style.WARNING(
                'Running with --session-id only. The command will backfill that session across its school scope.'
            ))

        self.stdout.write(self.style.NOTICE(
            f'Backfilling finance document numbers types={",".join(document_types)} '
            f'school_id={school_id or "ALL"} session_id={session_id or "ALL"} dry_run={dry_run}'
        ))

        try:
            result = backfill_finance_document_numbers(
                document_types=document_types,
                school_id=school_id,
                session_id=session_id,
                user_obj=None,
            )
        except Exception as exc:
            raise CommandError(str(exc))

        summary = (
            f"receipt={result['receipt']}, voucher={result['voucher']}, refund={result['refund']}, "
            f"transaction={result['transaction']}, payroll={result['payroll']}, scopes={result['scopes']}"
        )
        if result.get('skipped'):
            summary += f", skipped={','.join(result['skipped'])}"

        if dry_run:
            transaction.set_rollback(True)
            self.stdout.write(self.style.WARNING(f'DRY-RUN complete: {summary}'))
        else:
            self.stdout.write(self.style.SUCCESS(f'Backfill complete: {summary}'))
