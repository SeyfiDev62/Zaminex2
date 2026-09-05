"""Delete activity log rows and sessions that have outlived their use.

Both tables grow without bound. ``apps.activity.signals`` writes an
``ActivityLog`` row on every create, update and delete of a property, listing,
task, follow-up, appraisal report and consultant profile, so a busy office
appends continuously and nothing ever removes a row. Sessions are worse per
request: ``SESSION_SAVE_EVERY_REQUEST`` is on (the 12-hour idle timeout needs
a sliding expiry), so every authenticated request writes ``django_session``,
and expired rows are only removed by ``clearsessions`` — which nothing
schedules.

Neither is a correctness problem until the table is large enough to slow the
queries that read it, which is why it tends to be found late.

Run it from cron, nightly:

    python manage.py prune_history --days 180

Deletes are batched: a single ``DELETE`` over a few million rows holds a lock
and writes a WAL entry for every row in one transaction, which on a busy
database is a stall, not a cleanup.
"""

from datetime import timedelta

from django.conf import settings
from django.contrib.sessions.models import Session
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from importlib import import_module

from apps.activity.models import ActivityLog

DEFAULT_RETENTION_DAYS = 180
DEFAULT_BATCH_SIZE = 5000


class Command(BaseCommand):
    help = (
        "Delete activity log entries older than --days and clear expired "
        "sessions. Both tables grow without bound otherwise."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=DEFAULT_RETENTION_DAYS,
            help=(
                "Keep activity log entries for this many days "
                f"(default {DEFAULT_RETENTION_DAYS})."
            ),
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=DEFAULT_BATCH_SIZE,
            help=(
                "Rows per DELETE statement (default "
                f"{DEFAULT_BATCH_SIZE}). Larger is faster but holds its lock "
                "longer."
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be deleted without deleting anything.",
        )

    def handle(self, *args, **options):
        days = options["days"]
        if days < 0:
            raise CommandError("--days must not be negative")
        batch_size = options["batch_size"]
        if batch_size < 1:
            raise CommandError("--batch-size must be at least 1")
        dry_run = options["dry_run"]

        cutoff = timezone.now() - timedelta(days=days)
        now = timezone.now()

        stale_logs = ActivityLog.objects.filter(created_at__lt=cutoff)
        log_count = stale_logs.count()
        expired_sessions = Session.objects.filter(expire_date__lt=now).count()

        verb = "Would delete" if dry_run else "Deleted"
        self.stdout.write(
            f"Activity log: {verb.lower()} {log_count} of "
            f"{ActivityLog.objects.count()} entries older than "
            f"{cutoff:%Y-%m-%d %H:%M}"
        )
        self.stdout.write(
            f"Sessions: {verb.lower()} {expired_sessions} of "
            f"{Session.objects.count()} expired rows"
        )

        if dry_run:
            return

        # Batched so a large backlog is cleared without one transaction that
        # locks the table and replays as a single WAL entry.
        deleted_logs = 0
        while True:
            ids = list(
                ActivityLog.objects.filter(created_at__lt=cutoff).values_list(
                    "pk", flat=True
                )[:batch_size]
            )
            if not ids:
                break
            with transaction.atomic():
                removed, _ = ActivityLog.objects.filter(pk__in=ids).delete()
            deleted_logs += removed
            self.stdout.write(f"  ... {deleted_logs} activity entries so far")

        # Django's own routine, so a cached_db backend clears its cache half
        # as well as the table.
        # SESSION_ENGINE names the module, not the class — the same
        # distinction Django's own clearsessions command makes.
        engine = import_module(settings.SESSION_ENGINE)
        engine.SessionStore.clear_expired()
        remaining = Session.objects.filter(expire_date__lt=timezone.now()).count()

        self.stdout.write(
            self.style.SUCCESS(
                f"Pruned {deleted_logs} activity entries and "
                f"{expired_sessions - remaining} expired sessions."
            )
        )
