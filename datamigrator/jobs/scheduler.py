"""Polls for scheduled runs and starts them when due.

Mirrors jobs/engine.py's own "good enough for a demo scaffold, swap for a
real task queue in production" scope: a single daemon thread, started once
by JobsConfig.ready(), wakes up every POLL_INTERVAL_SECONDS and promotes any
MigrationRun that's still `pending` with a `scheduled_at` in the past to
`running`, then hands it to the same background-thread engine a "Run now"
trigger already uses. For production, replace this with Celery beat (or a
cron hitting a management command) — the promotion logic below is exactly
what such a task would do.
"""
import threading
import time

from django.db import connections
from django.utils import timezone

POLL_INTERVAL_SECONDS = 5

# Set at the end of every poll iteration (success or not) — a simple in-process
# heartbeat the status page (home/views.py::status_page) reads to tell "this
# scheduler thread is alive and polling" from "it died/never started", since
# these threads and that view share one process under `runserver`.
LAST_TICK_AT = None


def _promote_due_runs():
    from . import engine
    from .models import MigrationRun

    due = MigrationRun.objects.filter(
        status=MigrationRun.STATUS_PENDING,
        scheduled_at__isnull=False,
        scheduled_at__lte=timezone.now(),
    )
    for run in due:
        run.status = MigrationRun.STATUS_RUNNING
        run.started_at = timezone.now()
        run.save(update_fields=["status", "started_at"])
        threading.Thread(target=engine.run_migration_in_background, args=(run.id,), daemon=True).start()


def _poll_loop():
    global LAST_TICK_AT
    while True:
        time.sleep(POLL_INTERVAL_SECONDS)
        try:
            _promote_due_runs()
        except Exception:
            # A scaffold-level scheduler shouldn't take the whole poll loop down
            # over one bad run — it'll pick the same row up again next tick.
            pass
        finally:
            connections.close_all()
            LAST_TICK_AT = timezone.now()


def start():
    threading.Thread(target=_poll_loop, daemon=True).start()
