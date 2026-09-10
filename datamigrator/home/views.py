from collections import Counter
from urllib.parse import urlsplit

from django.db.models import Count, F, Q
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone

from connections.models import ApiCallLog, Connection
from jobs.models import MigrationRun

import connections.scheduler as connections_scheduler
import jobs.scheduler as jobs_scheduler
import plans.scheduler as plans_scheduler

ACTIVE_STATUSES = (MigrationRun.STATUS_PENDING, MigrationRun.STATUS_RUNNING)
RECENT_RUNS_WINDOW = 50    # how many of the most recent runs feed the failure-rate signal
RECENT_ERRORS_HOURS = 1    # API-error-rate window


def index(request):
    return render(request, "home/index.html")


def _top_endpoints(limit=5):
    """Ranks every logged outbound API call (see connections.models.ApiCallLog,
    populated by every ConnectionClient request) by method + endpoint,
    ignoring the query string and any trailing slash so e.g. `/produtos/` and
    `/produtos?page=2` count as the same endpoint."""
    counter = Counter()
    for method, url in ApiCallLog.objects.values_list("method", "url"):
        parts = urlsplit(url)
        path = parts.path.rstrip("/") or "/"
        endpoint = f"{parts.scheme}://{parts.netloc}{path}" if parts.netloc else path
        counter[(method, endpoint)] += 1
    return [
        {"method": method, "endpoint": endpoint, "count": count}
        for (method, endpoint), count in counter.most_common(limit)
    ]


def stats(request):
    runs = MigrationRun.objects.all()

    connections = Connection.objects.annotate(
        success_count=Count(
            "mappings_as_source__runs",
            filter=Q(mappings_as_source__runs__status=MigrationRun.STATUS_SUCCESS),
            distinct=True,
        ),
        failed_count=Count(
            "mappings_as_source__runs",
            filter=Q(mappings_as_source__runs__status=MigrationRun.STATUS_FAILED),
            distinct=True,
        ),
    ).order_by("name")

    active_runs = list(
        runs.filter(status__in=ACTIVE_STATUSES)
        .values("id", "status", "records_read", "records_written", "records_failed", mapping_name=F("mapping__name"))
    )
    return JsonResponse({
        "total": runs.count(),
        "active": runs.filter(status__in=ACTIVE_STATUSES).count(),
        "success": runs.filter(status=MigrationRun.STATUS_SUCCESS).count(),
        "failed": runs.filter(status=MigrationRun.STATUS_FAILED).count(),
        "connections": [
            {
                "id": c.id, "name": c.name, "is_connected": c.is_connected,
                "success_count": c.success_count, "failed_count": c.failed_count,
            }
            for c in connections
        ],
        "active_runs": active_runs,
        "total_api_calls": ApiCallLog.objects.count(),
        "top_endpoints": _top_endpoints(),
    })


def _worker_status(name, module, description):
    """module.LAST_TICK_AT is set at the end of every poll iteration (see
    jobs/scheduler.py and its plans/connections siblings) — this and the
    view share a process under `runserver`, so reading it directly tells
    "alive and polling" from "never started" or "stuck", no IPC needed.
    A thread that hasn't ticked yet within ~3 poll intervals of process
    start reads as "starting" rather than "down" — it just hasn't had its
    first chance to run yet."""
    last_tick = module.LAST_TICK_AT
    interval = module.POLL_INTERVAL_SECONDS
    if last_tick is None:
        return {"name": name, "description": description, "status": "starting", "last_tick_at": None, "poll_interval_seconds": interval}
    seconds_ago = (timezone.now() - last_tick).total_seconds()
    healthy = seconds_ago < interval * 3
    return {
        "name": name, "description": description,
        "status": "ok" if healthy else "stalled",
        "last_tick_at": last_tick.isoformat(),
        "seconds_since_tick": round(seconds_ago),
        "poll_interval_seconds": interval,
    }


def _compute_status_data():
    workers = [
        _worker_status("Run scheduler", jobs_scheduler, "Promotes scheduled runs when they come due."),
        _worker_status("Plan scheduler", plans_scheduler, "Promotes scheduled plans and starts their sequential execution."),
        _worker_status("Token refresh", connections_scheduler, "Proactively refreshes OAuth2 connections on their own schedule."),
    ]

    connections_qs = Connection.objects.filter(is_active=True)
    connections_total = connections_qs.count()
    connections_connected = sum(1 for c in connections_qs if c.is_connected)

    recent_runs = list(MigrationRun.objects.order_by("-started_at")[:RECENT_RUNS_WINDOW])
    finished = [r for r in recent_runs if r.status in (MigrationRun.STATUS_SUCCESS, MigrationRun.STATUS_FAILED)]
    recent_failed = sum(1 for r in finished if r.status == MigrationRun.STATUS_FAILED)
    failure_rate = (recent_failed / len(finished)) if finished else 0.0

    error_window_start = timezone.now() - timezone.timedelta(hours=RECENT_ERRORS_HOURS)
    recent_calls = ApiCallLog.objects.filter(created_at__gte=error_window_start)
    recent_calls_total = recent_calls.count()
    recent_calls_errored = recent_calls.filter(Q(error__gt="") | Q(status_code__gte=400)).count()

    worker_states = [w["status"] for w in workers]
    if worker_states.count("stalled") == len(workers) or failure_rate >= 0.5:
        overall = "down"
    elif "stalled" in worker_states or failure_rate >= 0.2 or (connections_total and connections_connected < connections_total):
        overall = "degraded"
    else:
        overall = "operational"

    return {
        "overall": overall,
        "generated_at": timezone.now().isoformat(),
        "workers": workers,
        "connections": {
            "total": connections_total, "connected": connections_connected,
            "disconnected": connections_total - connections_connected,
        },
        "runs": {
            "window": len(recent_runs), "finished": len(finished), "failed": recent_failed,
            "failure_rate_pct": round(failure_rate * 100, 1),
            "active": sum(1 for r in recent_runs if r.status in ACTIVE_STATUSES),
        },
        "api_errors": {
            "window_hours": RECENT_ERRORS_HOURS, "total_calls": recent_calls_total,
            "errored_calls": recent_calls_errored,
        },
    }


def status_page(request):
    return render(request, "home/status.html", _compute_status_data())


def status_data(request):
    return JsonResponse(_compute_status_data())
