"""Polls for scheduled plans and starts them when due — same shape as
jobs/scheduler.py's run-level poller, kept separate so `plans` can depend on
`jobs` without `jobs` needing to know `plans` exists.
"""
import threading
import time

from django.db import connections
from django.utils import timezone

from . import executor

POLL_INTERVAL_SECONDS = 5


def _promote_due_plans():
    from .models import MigrationPlan

    due = MigrationPlan.objects.filter(
        status=MigrationPlan.STATUS_SCHEDULED,
        scheduled_at__isnull=False,
        scheduled_at__lte=timezone.now(),
    )
    for plan in due:
        plan.status = MigrationPlan.STATUS_EXECUTING
        plan.save(update_fields=["status"])
        executor.start_plan(plan.id)


def _poll_loop():
    while True:
        time.sleep(POLL_INTERVAL_SECONDS)
        try:
            _promote_due_plans()
        except Exception:
            pass
        finally:
            connections.close_all()


def start():
    threading.Thread(target=_poll_loop, daemon=True).start()
