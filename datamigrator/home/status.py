"""Ante's status page, built the way the callum_freight_hub one is: every check is a real, timed call (Ante's own pages
and API endpoints are called in-process with Django's test Client — no network hop) classified as Operational /
Degraded / Down, grouped, and shown with a 24h uptime and a small history. A 401/403/redirect from a protected
endpoint is the *correct* answer without credentials, so it counts as operational; a 5xx or an exception is down.

On top of that Ante checks what only it has: the database, its background workers, how recent migration runs and
outbound API calls have gone, and every connection it talks to. Those "activity" rows carry their own history (from
the runs / call log), so they need no stored results; the rest are stored by `manage.py run_status_checks` (or by the
page itself, at most every few minutes) in StatusCheckResult.
"""
from __future__ import annotations

import time
from collections import defaultdict
from datetime import timedelta
from urllib.parse import urlsplit

from django.conf import settings
from django.db import connection as db_connection
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from connections.models import ApiCallLog, Connection
from jobs.models import MigrationRun

from .models import StatusCheckResult

State = StatusCheckResult.State

RECENT_RUNS_WINDOW = 50     # how many of the most recent runs feed the failure-rate check
RECENT_CALLS_HOURS = 1      # window of the "outbound API calls" check
UPTIME_HOURS = 24           # window of every uptime %
HISTORY_POINTS = 20         # bars in a sparkline
DEGRADED_RATE, DOWN_RATE = 0.2, 0.5
CALL_ROWS_LIMIT = 20000     # most recent calls read for the activity rows
RECORD_EVERY = timedelta(minutes=5)
KEEP_RESULTS_FOR = timedelta(days=7)

# What each of Ante's own pages / API endpoints may answer without credentials and still be healthy.
_OK = {200, 301, 302, 401, 403}
# (group, name, method, path, expected status codes)
ENDPOINT_REGISTRY = [
    ("Ante API", "Mappings", "GET", "/api/mappings/", _OK),
    ("Ante API", "Runs", "GET", "/api/runs/", _OK),
    ("Ante API", "Plans", "GET", "/api/plans/", _OK),
    ("Ante API", "Chains", "GET", "/api/chains/", _OK),
    ("Ante API", "Connections", "GET", "/api/connections/", _OK),
    ("Ante API", "Entities", "GET", "/api/entities/", _OK),
    ("Ante API", "Incidents", "GET", "/api/incidents/", _OK),
    ("Ante API", "Tickets", "GET", "/api/tickets/", _OK),
    ("Console", "Front page", "GET", "/", {200}),
    ("Console", "Sign-in page", "GET", "/accounts/login/", {200}),
    ("Console", "Home", "GET", "/home/", _OK),
    ("Console", "Studio", "GET", "/studio/", _OK),
]

GROUP_ORDER = ["Platform", "Ante API", "Console", "Activity", "Connections"]


def _row(group, name, state, *, method="-", path="", http_status=None, latency_ms=None, detail="", persist=False, **extra):
    return {"group": group, "name": name, "method": method, "path": path, "http_status": http_status,
            "latency_ms": None if latency_ms is None else round(latency_ms, 1), "state": state, "detail": detail,
            "persist": persist, "history": [], "uptime_pct": None, **extra}


def _rate_state(rate):
    return State.DOWN if rate >= DOWN_RATE else State.DEGRADED if rate >= DEGRADED_RATE else State.OPERATIONAL


def _worst(*states):
    return State.DOWN if State.DOWN in states else State.DEGRADED if State.DEGRADED in states else State.OPERATIONAL


# ── platform ────────────────────────────────────────────────────────────────────
def check_database():
    started = time.perf_counter()
    try:
        with db_connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        return _row("Platform", "Database", State.OPERATIONAL, latency_ms=(time.perf_counter() - started) * 1000, persist=True)
    except Exception as exc:
        return _row("Platform", "Database", State.DOWN, latency_ms=(time.perf_counter() - started) * 1000,
                    detail=str(exc)[:250], persist=True)


def worker_status(name, module, description):
    """module.LAST_TICK_AT is set at the end of every poll iteration (see jobs/scheduler.py and its plans/connections
    siblings) — this and the view share a process under `runserver`, so reading it directly tells "alive and polling"
    from "never started" or "stuck", no IPC needed. A thread that hasn't ticked yet within ~3 poll intervals of process
    start reads as "starting" rather than "down" — it just hasn't had its first chance to run yet."""
    last_tick = module.LAST_TICK_AT
    interval = module.POLL_INTERVAL_SECONDS
    if last_tick is None:
        return {"name": name, "description": description, "status": "starting", "last_tick_at": None, "poll_interval_seconds": interval}
    seconds_ago = (timezone.now() - last_tick).total_seconds()
    return {
        "name": name, "description": description, "status": "ok" if seconds_ago < interval * 3 else "stalled",
        "last_tick_at": last_tick.isoformat(), "seconds_since_tick": round(seconds_ago), "poll_interval_seconds": interval,
    }


def check_workers():
    import connections.scheduler as connections_scheduler
    import jobs.scheduler as jobs_scheduler
    import plans.scheduler as plans_scheduler

    workers = [
        worker_status("Run scheduler", jobs_scheduler, "Promotes scheduled runs when they come due."),
        worker_status("Plan scheduler", plans_scheduler, "Promotes scheduled plans and starts their sequential execution."),
        worker_status("Token refresh", connections_scheduler, "Proactively refreshes OAuth2 connections on their own schedule."),
    ]
    rows = []
    for w in workers:
        if w["status"] == "ok":
            row = _row("Platform", w["name"], State.OPERATIONAL, path=w["description"], persist=True)
        elif w["status"] == "starting":
            # not a problem: it just hasn't had its first turn yet (and, under several server processes, may never be
            # seen from this one) — shown as such, never turning the page yellow
            row = _row("Platform", w["name"], State.OPERATIONAL, path=w["description"], label="Starting…", persist=True)
        else:
            row = _row("Platform", w["name"], State.DEGRADED, path=w["description"], persist=True,
                       detail=f"Hasn't polled for {w['seconds_since_tick']}s (it polls every {w['poll_interval_seconds']}s).")
        rows.append(row)
    if all(w["status"] == "stalled" for w in workers):
        for row in rows:
            row["state"] = State.DOWN
    return rows


# ── Ante's own pages and API ────────────────────────────────────────────────────
def _classify(http_status, expected):
    if http_status >= 500:
        return State.DOWN
    return State.OPERATIONAL if http_status in expected else State.DEGRADED


def check_endpoint(client, group, name, method, path, expected):
    started = time.perf_counter()
    try:
        response = client.generic(method, path)
    except Exception as exc:
        return _row(group, name, State.DOWN, method=method, path=path, latency_ms=(time.perf_counter() - started) * 1000,
                    detail=str(exc)[:250], persist=True)
    state = _classify(response.status_code, expected)
    return _row(group, name, state, method=method, path=path, http_status=response.status_code,
                latency_ms=(time.perf_counter() - started) * 1000, persist=True,
                detail="" if state == State.OPERATIONAL else f"Unexpected status {response.status_code}")


def check_endpoints(host):
    client = Client(HTTP_HOST=host)
    return [check_endpoint(client, *entry) for entry in ENDPOINT_REGISTRY]


# ── activity: runs, outbound API calls, connections ──────────────────────────────
class _Calls:
    """Calls of one connection: how many, how many errored, average duration, and the latest few outcomes."""

    def __init__(self):
        self.total = self.errors = self.timed = 0
        self.duration = 0.0
        self.recent = []                           # newest first, at most HISTORY_POINTS

    def add(self, errored, duration_ms, at):
        self.total += 1
        self.errors += errored
        if duration_ms is not None:
            self.duration += duration_ms
            self.timed += 1
        if len(self.recent) < HISTORY_POINTS:
            self.recent.append({"state": State.DOWN if errored else State.OPERATIONAL, "at": at})

    @property
    def rate(self):
        return self.errors / self.total if self.total else 0.0

    @property
    def uptime_pct(self):
        return round((self.total - self.errors) / self.total * 100, 2) if self.total else None

    @property
    def avg_ms(self):
        return self.duration / self.timed if self.timed else None

    @property
    def history(self):
        return list(reversed(self.recent))


def _read_calls():
    since = timezone.now() - timedelta(hours=UPTIME_HOURS)
    hour_ago = timezone.now() - timedelta(hours=RECENT_CALLS_HOURS)
    overall, last_hour = _Calls(), _Calls()
    by_connection = defaultdict(_Calls)
    rows = ApiCallLog.objects.filter(created_at__gte=since).order_by("-created_at").values_list(
        "connection_id", "status_code", "error", "duration_ms", "created_at")[:CALL_ROWS_LIMIT]
    for connection_id, status_code, error, duration_ms, at in rows:
        errored = bool(error) or (status_code is not None and status_code >= 400)
        overall.add(errored, duration_ms, at)
        if at >= hour_ago:
            last_hour.add(errored, duration_ms, at)
        if connection_id:
            by_connection[connection_id].add(errored, duration_ms, at)
    return overall, last_hour, by_connection


def check_runs():
    recent = list(MigrationRun.objects.order_by("-started_at")[:RECENT_RUNS_WINDOW])
    finished = [r for r in recent if r.status in (MigrationRun.STATUS_SUCCESS, MigrationRun.STATUS_FAILED)]
    failed = sum(1 for r in finished if r.status == MigrationRun.STATUS_FAILED)
    rate = failed / len(finished) if finished else 0.0
    state = _rate_state(rate)
    row = _row("Activity", "Migration runs", state, path=f"Last {len(recent)} runs" if recent else "No runs yet",
               detail="" if state == State.OPERATIONAL else f"{failed} of {len(finished)} recent migration runs failed ({round(rate * 100)}%).")
    # a bar per finished run (oldest first) and the share that succeeded in the last 24h
    row["history"] = [{"state": State.DOWN if r.status == MigrationRun.STATUS_FAILED else State.OPERATIONAL, "at": r.started_at}
                      for r in reversed(finished[:HISTORY_POINTS])]
    day = [r for r in finished if r.started_at >= timezone.now() - timedelta(hours=UPTIME_HOURS)]
    row["uptime_pct"] = round(sum(1 for r in day if r.status == MigrationRun.STATUS_SUCCESS) / len(day) * 100, 2) if day else None
    return row


def _call_detail(calls, window):
    return f"{calls.errors} of {calls.total} API calls failed in the last {window}h ({round(calls.rate * 100)}%)."


def check_api_calls(last_hour, overall):
    state = _rate_state(last_hour.rate)
    row = _row("Activity", "Outbound API calls", state, path=f"Last {RECENT_CALLS_HOURS}h", latency_ms=last_hour.avg_ms,
               detail="" if state == State.OPERATIONAL else _call_detail(last_hour, RECENT_CALLS_HOURS))
    row["history"], row["uptime_pct"] = overall.history, overall.uptime_pct
    return row


def check_connections(by_connection):
    rows = []
    for c in Connection.objects.filter(is_active=True).order_by("name"):
        calls = by_connection.get(c.pk) or _Calls()
        state, detail = _rate_state(calls.rate), ""
        if state != State.OPERATIONAL:
            detail = _call_detail(calls, UPTIME_HOURS)
        if not c.is_connected:
            state, detail = _worst(state, State.DEGRADED), (detail or "Needs setup.")
        rows.append(_row("Connections", c.name, state, path=urlsplit(c.base_url).netloc or c.base_url, latency_ms=calls.avg_ms,
                         detail=detail, href=reverse("connections:detail", args=[c.pk]), history=calls.history, uptime_pct=calls.uptime_pct))
    return rows


# ── putting it together ─────────────────────────────────────────────────────────
def default_host():
    hosts = [h for h in settings.ALLOWED_HOSTS if h and h != "*" and not h.startswith(".")]
    return hosts[0] if hosts else "localhost"


def run_checks(host=None):
    """Every check, in page order. `host` is the Host header the in-process calls carry (the current request's)."""
    overall, last_hour, by_connection = _read_calls()
    results = [check_database(), *check_workers(), *check_endpoints(host or default_host()),
               check_runs(), check_api_calls(last_hour, overall), *check_connections(by_connection)]
    order = {g: i for i, g in enumerate(GROUP_ORDER)}
    return sorted(results, key=lambda r: order.get(r["group"], len(order)))      # stable: keeps the order inside a group


def overall_state(results):
    return _worst(*[r["state"] for r in results])


def summary(results):
    counts = {s: 0 for s in State.values}
    for r in results:
        counts[r["state"]] += 1
    return counts


def record(results):
    StatusCheckResult.objects.bulk_create([
        StatusCheckResult(group=r["group"], name=r["name"], method=r["method"] or "-", path=r["path"][:255], http_status=r["http_status"],
                          latency_ms=r["latency_ms"], state=r["state"], detail=r["detail"][:255])
        for r in results if r["persist"]
    ])
    StatusCheckResult.objects.filter(created_at__lt=timezone.now() - KEEP_RESULTS_FOR).delete()


def record_if_stale(results):
    """Store this run unless one was stored in the last few minutes — so the history keeps building while people use
    Ante, even without a schedule."""
    last = StatusCheckResult.objects.order_by("-created_at").values_list("created_at", flat=True).first()
    if last is None or timezone.now() - last >= RECORD_EVERY:
        record(results)
        return True
    return False


def attach_history(results):
    """Fill `history` (the last few results, oldest first) and `uptime_pct` (24h, degraded counts as up) of the stored
    checks from StatusCheckResult, in one query. A check with a single stored result has no history yet."""
    since = timezone.now() - timedelta(hours=UPTIME_HOURS)
    stored = defaultdict(list)
    for r in StatusCheckResult.objects.filter(created_at__gte=since).order_by("created_at"):
        stored[(r.group, r.name)].append(r)
    for row in results:
        if not row["persist"]:
            continue
        rows = stored.get((row["group"], row["name"]), [])
        if len(rows) >= 2:                      # one sample says nothing about history or uptime
            row["history"] = [{"state": r.state, "at": r.created_at} for r in rows[-HISTORY_POINTS:]]
            row["uptime_pct"] = round(sum(1 for r in rows if r.state != State.DOWN) / len(rows) * 100, 2)
    return results


def grouped(results):
    groups = {}
    for r in results:
        groups.setdefault(r["group"], []).append(r)
    return groups
