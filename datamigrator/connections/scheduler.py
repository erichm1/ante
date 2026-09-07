"""Polls enabled TokenRefreshJobs and proactively refreshes their OAuth2
connection's access token when due — same shape as jobs/scheduler.py and
plans/scheduler.py's own poller threads. Model/client imports are kept
local to each function (not at module level) since apps.py's ready() loads
this module before the app registry is guaranteed fully populated.
"""
import threading
import time

from django.db import connections as db_connections
from django.db.models import Q
from django.utils import timezone

POLL_INTERVAL_SECONDS = 30


def run_job_now(job) -> None:
    """Runs one refresh attempt immediately and records the outcome —
    shared by the background poller below and the "Refresh now" button in
    connections/views.py's token_refresh_job_run."""
    from .client import ConnectionClient
    from .models import TokenRefreshJob

    job.last_run_at = timezone.now()
    try:
        ConnectionClient(job.connection).refresh_oauth2_token(force=True)
        job.last_status = TokenRefreshJob.STATUS_SUCCESS
        job.last_error = ""
    except Exception as exc:
        job.last_status = TokenRefreshJob.STATUS_FAILED
        job.last_error = str(exc)[:500]
    job.schedule_next_run()
    job.save(update_fields=["last_run_at", "last_status", "last_error", "next_run_at"])


def _run_due_jobs():
    from .models import TokenRefreshJob

    due = TokenRefreshJob.objects.filter(is_enabled=True).filter(
        Q(next_run_at__isnull=True) | Q(next_run_at__lte=timezone.now())
    ).select_related("connection")
    for job in due:
        run_job_now(job)


def _poll_loop():
    while True:
        time.sleep(POLL_INTERVAL_SECONDS)
        try:
            _run_due_jobs()
        except Exception:
            pass
        finally:
            db_connections.close_all()


def start():
    threading.Thread(target=_poll_loop, daemon=True).start()
