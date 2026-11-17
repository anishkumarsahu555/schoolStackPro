import json
import re
import time
from collections import Counter
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.test import Client

from homeApp.models import SchoolSession
from homeApp.session_utils import build_current_session_payload, build_session_list_item


DEFAULT_PATHS = (
    "/management/home/",
    "/management/student_list/",
    "/management/teacher_list/",
    "/management/student_fee_details/",
    "/management/finance/",
    "/management/student_attendance_history/",
    "/management/exam_marks_details/",
    "/management/progress_report_cards/",
    "/management/manage-class/",
)


class Command(BaseCommand):
    help = "Profile key page loads with an authenticated management session."

    def add_arguments(self, parser):
        parser.add_argument(
            "--path",
            action="append",
            dest="paths",
            help="Path to profile. Repeat for multiple paths. Defaults to key management pages.",
        )
        parser.add_argument(
            "--username",
            help="Specific user to login as. Defaults to the current school's owner, then admin/superuser.",
        )
        parser.add_argument(
            "--session-id",
            type=int,
            help="SchoolSession id to use. Defaults to the latest current non-deleted session.",
        )
        parser.add_argument(
            "--repeat",
            type=int,
            default=1,
            help="Number of times to request each path. The best duration is reported. Default: 1.",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            help="Print machine-readable JSON instead of a table.",
        )
        parser.add_argument(
            "--show-sql-summary",
            action="store_true",
            help="Show repeated SQL patterns for each profiled path.",
        )
        parser.add_argument(
            "--sql-limit",
            type=int,
            default=10,
            help="Number of SQL patterns to show when --show-sql-summary is used. Default: 10.",
        )

    def handle(self, *args, **options):
        paths = options["paths"] or list(DEFAULT_PATHS)
        repeat = max(1, int(options["repeat"] or 1))
        school_session = self._resolve_school_session(options.get("session_id"))
        user = self._resolve_user(options.get("username"), school_session)

        client = Client()
        client.force_login(user)
        self._prime_management_session(client, school_session)

        original_force_debug_cursor = connection.force_debug_cursor
        connection.force_debug_cursor = True
        try:
            results = [
                self._profile_path(client, path, repeat, options["show_sql_summary"], options["sql_limit"])
                for path in paths
            ]
        finally:
            connection.force_debug_cursor = original_force_debug_cursor

        payload = {
            "user": user.username,
            "session": {
                "id": school_session.id,
                "school_id": school_session.schoolID_id,
                "year": school_session.sessionYear,
            },
            "repeat": repeat,
            "results": results,
        }

        if options["json"]:
            self.stdout.write(json.dumps(payload, indent=2, default=str))
            return

        self._print_table(payload)

    def _resolve_school_session(self, session_id):
        queryset = SchoolSession.objects.select_related("schoolID", "schoolID__ownerID").filter(isDeleted=False)
        if session_id:
            session = queryset.filter(pk=session_id).first()
            if not session:
                raise CommandError(f"SchoolSession id={session_id} was not found.")
            return session

        session = queryset.filter(isCurrent=True).order_by("-datetime", "-id").first()
        if not session:
            session = queryset.order_by("-datetime", "-id").first()
        if not session:
            raise CommandError("No active SchoolSession found. Create data first, e.g. manage.py mock_data.")
        return session

    def _resolve_user(self, username, school_session):
        if username:
            user = User.objects.filter(username=username).first()
            if not user:
                raise CommandError(f"User username={username!r} was not found.")
            return user

        school = school_session.schoolID
        owner = getattr(school, "ownerID", None) if school else None
        if owner and owner.userID_id:
            return owner.userID

        if school:
            owner = school.owners.select_related("userID").filter(userID__isnull=False, isDeleted=False).first()
            if owner:
                return owner.userID

        owner_group = Group.objects.filter(name__in=("Owner", "Admin")).first()
        if owner_group:
            user = User.objects.filter(groups=owner_group).order_by("id").first()
            if user:
                return user

        user = User.objects.filter(is_superuser=True).order_by("id").first()
        if user:
            return user

        user = User.objects.order_by("id").first()
        if user:
            return user

        raise CommandError("No user found to authenticate with. Create a user first.")

    def _prime_management_session(self, client, school_session):
        session = client.session
        session["current_session"] = build_current_session_payload(school_session)
        session["session_list"] = [
            build_session_list_item(item)
            for item in SchoolSession.objects.filter(
                schoolID_id=school_session.schoolID_id,
                isDeleted=False,
            ).order_by("-datetime", "-id")
        ]
        session.save()

    def _profile_path(self, client, path, repeat, include_sql_summary=False, sql_limit=10):
        attempts = []
        for _ in range(repeat):
            connection.queries_log.clear()
            start = time.perf_counter()
            response = client.get(path, follow=False)
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            queries = list(connection.queries)
            attempt = {
                "path": path,
                "status": response.status_code,
                "redirect": response.headers.get("Location", ""),
                "duration_ms": duration_ms,
                "query_count": len(queries),
                "query_time_ms": self._query_time_ms(queries),
            }
            if include_sql_summary:
                attempt["sql_summary"] = self._sql_summary(queries, sql_limit)
            attempts.append(attempt)
        return min(attempts, key=lambda item: item["duration_ms"])

    def _sql_summary(self, queries, limit):
        counter = Counter()
        for query in queries:
            sql = query.get("sql", "")
            if not sql:
                continue
            counter[self._normalize_sql(sql)] += 1
        return [
            {"count": count, "sql": sql}
            for sql, count in counter.most_common(limit)
        ]

    def _normalize_sql(self, sql):
        collapsed = " ".join(sql.split())
        collapsed = re.sub(r"'(?:''|[^'])*'", "'?'", collapsed)
        collapsed = re.sub(r"\b\d+\b", "?", collapsed)
        collapsed = re.sub(r"\s+IN\s+\((?:\?,?\s*)+\)", " IN (?)", collapsed, flags=re.IGNORECASE)
        return collapsed[:280]

    def _query_time_ms(self, queries):
        total_seconds = Decimal("0")
        for query in queries:
            try:
                total_seconds += Decimal(str(query.get("time") or "0"))
            except Exception:
                continue
        return round(float(total_seconds * Decimal("1000")), 2)

    def _print_table(self, payload):
        self.stdout.write(
            self.style.NOTICE(
                f"Profiling as {payload['user']} | session={payload['session']['id']} "
                f"({payload['session']['year'] or 'unknown year'}) | repeat={payload['repeat']}"
            )
        )
        headers = ("status", "ms", "queries", "db ms", "path", "redirect")
        rows = [
            (
                str(item["status"]),
                f"{item['duration_ms']:.2f}",
                str(item["query_count"]),
                f"{item['query_time_ms']:.2f}",
                item["path"],
                item["redirect"],
            )
            for item in sorted(payload["results"], key=lambda item: item["duration_ms"], reverse=True)
        ]
        widths = [
            max(len(headers[index]), *(len(row[index]) for row in rows))
            for index in range(len(headers))
        ]

        def line(values):
            return "  ".join(value.ljust(widths[index]) for index, value in enumerate(values))

        self.stdout.write(line(headers))
        self.stdout.write(line(tuple("-" * width for width in widths)))
        for row in rows:
            self.stdout.write(line(row))
        for item in payload["results"]:
            if not item.get("sql_summary"):
                continue
            self.stdout.write("")
            self.stdout.write(self.style.NOTICE(f"Top SQL patterns for {item['path']}:"))
            for row in item["sql_summary"]:
                self.stdout.write(f"{row['count']:>3}x  {row['sql']}")
