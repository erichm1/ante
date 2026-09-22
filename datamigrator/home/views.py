from django.db.models import Count, F, Q
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone

from connections.models import ApiCallLog, Connection
from incidents.models import Incident, IncidentRule
from jobs.models import MigrationRun
from tickets.models import Ticket

import connections.scheduler as connections_scheduler
import jobs.scheduler as jobs_scheduler
import plans.scheduler as plans_scheduler

from . import status as status_engine
from .status import worker_status as _worker_status

ACTIVE_STATUSES = (MigrationRun.STATUS_PENDING, MigrationRun.STATUS_RUNNING)
RECENT_RUNS_WINDOW = 50    # how many of the most recent runs feed the failure-rate signal
RECENT_ERRORS_HOURS = 1    # API-error-rate window


def landing(request):
    """The public front page for anyone who isn't signed in (a product page for Ante). Signed-in users go straight
    to their dashboard."""
    if request.user.is_authenticated:
        return redirect("home:index")
    return render(request, "landing/index.html")


def index(request):
    open_incidents = list(
        Incident.objects.exclude(status=Incident.STATUS_RESOLVED)
            .select_related("connection")
            .order_by("-created_at")[:5]
    )

    my_tickets = list(
        Ticket.objects.filter(created_by=request.user)
            .exclude(status__in=["resolved", "closed"])
            .select_related("incident")
            .order_by("-created_at")[:8]
    )
    my_ticket_counts = {
        "open":        Ticket.objects.filter(created_by=request.user, status="open").count(),
        "in_progress": Ticket.objects.filter(created_by=request.user, status="in_progress").count(),
    }

    return render(request, "home/index.html", {
        "open_incidents":    open_incidents,
        "my_tickets":        my_tickets,
        "my_ticket_counts":  my_ticket_counts,
    })


def _evaluate_rules(user):
    """Evaluate all enabled incident rules for user. Returns list of created Incident objects."""
    from datetime import timedelta
    import json

    now = timezone.now()
    created = []

    for rule in IncidentRule.objects.filter(user=user, enabled=True):
        rule.last_checked_at = now
        triggered = False
        metric_value = 0.0

        window_start = now - timedelta(hours=rule.window_hours)

        if rule.trigger_type == IncidentRule.TRIGGER_RUN_FAIL:
            qs = MigrationRun.objects.filter(started_at__gte=window_start)
            total = qs.count()
            if total > 0:
                failed = qs.filter(status=MigrationRun.STATUS_FAILED).count()
                metric_value = (failed / total) * 100
                triggered = metric_value >= rule.threshold

        elif rule.trigger_type in (IncidentRule.TRIGGER_CONN_ERR, IncidentRule.TRIGGER_API_ERROR):
            qs = ApiCallLog.objects.filter(created_at__gte=window_start)
            if rule.connection_id:
                qs = qs.filter(connection_id=rule.connection_id)
            total = qs.count()
            if total > 0:
                errors = qs.filter(status_code__gte=400).count()
                metric_value = (errors / total) * 100
                triggered = metric_value >= rule.threshold

        if triggered:
            title = rule.auto_title or f"[Auto] {rule.get_trigger_type_display()} — {rule.name}"
            already_open = Incident.objects.filter(
                title=title,
                status__in=[Incident.STATUS_OPEN, Incident.STATUS_INVESTIGATING, Incident.STATUS_IDENTIFIED],
            ).exists()
            if not already_open:
                inc = Incident.objects.create(
                    title=title,
                    description=(
                        f"Auto-created by rule \"{rule.name}\".\n"
                        f"Metric: {metric_value:.1f}% (threshold: {rule.threshold}%)\n"
                        f"Window: last {rule.window_hours}h"
                    ),
                    severity=rule.severity,
                    connection=rule.connection,
                )
                rule.last_triggered_at = now
                created.append(inc)

        rule.save(update_fields=["last_checked_at", "last_triggered_at"])

    return created


def check_rules(request):
    if request.method != "POST":
        return JsonResponse({"error": "POST required."}, status=405)
    created = _evaluate_rules(request.user)
    return JsonResponse({"created": len(created), "incidents": [{"id": i.pk, "title": i.title} for i in created]})


def rules_api(request):
    from incidents.serializers import IncidentRuleSerializer
    import json

    if request.method == "GET":
        rules = IncidentRule.objects.filter(user=request.user).select_related("connection")
        data = IncidentRuleSerializer(rules, many=True, context={"request": request}).data
        return JsonResponse({"results": list(data)})

    if request.method == "POST":
        body = json.loads(request.body)
        ser = IncidentRuleSerializer(data=body, context={"request": request})
        if ser.is_valid():
            ser.save()
            return JsonResponse(ser.data, status=201)
        return JsonResponse(ser.errors, status=400)

    return JsonResponse({"error": "Method not allowed."}, status=405)


def rule_detail_api(request, pk):
    from incidents.serializers import IncidentRuleSerializer
    import json

    rule = IncidentRule.objects.filter(user=request.user, pk=pk).first()
    if not rule:
        return JsonResponse({"error": "Not found."}, status=404)

    if request.method == "GET":
        return JsonResponse(IncidentRuleSerializer(rule, context={"request": request}).data)

    if request.method in ("PATCH", "PUT"):
        body = json.loads(request.body)
        ser = IncidentRuleSerializer(rule, data=body, partial=True, context={"request": request})
        if ser.is_valid():
            ser.save()
            return JsonResponse(ser.data)
        return JsonResponse(ser.errors, status=400)

    if request.method == "DELETE":
        rule.delete()
        return JsonResponse({}, status=204)

    return JsonResponse({"error": "Method not allowed."}, status=405)


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
    })


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
    api_error_rate = (recent_calls_errored / recent_calls_total) if recent_calls_total else 0.0

    # Every distinct thing that can push "overall" off "operational" — same
    # thresholds used below, kept alongside the reason so any page (the
    # Logs page in particular) can say *why*, not just flash a color.
    reasons = []
    worker_states = [w["status"] for w in workers]
    if "stalled" in worker_states:
        reasons.append({"kind": "workers", "severity": "down" if worker_states.count("stalled") == len(workers) else "degraded",
                         "message": "One or more background workers have stopped polling."})
    if failure_rate >= 0.2:
        reasons.append({"kind": "runs", "severity": "down" if failure_rate >= 0.5 else "degraded",
                         "message": f"{recent_failed} of {len(finished)} recent migration runs failed ({round(failure_rate * 100)}%)."})
    if api_error_rate >= 0.2:
        reasons.append({"kind": "api_errors", "severity": "down" if api_error_rate >= 0.5 else "degraded",
                         "message": f"{recent_calls_errored} of {recent_calls_total} API calls failed in the last {RECENT_ERRORS_HOURS}h ({round(api_error_rate * 100)}%)."})
    if connections_total and connections_connected < connections_total:
        reasons.append({"kind": "connections", "severity": "degraded",
                         "message": f"{connections_total - connections_connected} of {connections_total} connections need setup."})

    if any(r["severity"] == "down" for r in reasons):
        overall = "down"
    elif reasons:
        overall = "degraded"
    else:
        overall = "operational"

    return {
        "overall": overall,
        "reasons": reasons,
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
            "errored_calls": recent_calls_errored, "error_rate_pct": round(api_error_rate * 100, 1),
        },
    }


def _checks(request):
    """Run every check, store the results now and then (so history builds up), and add each one's history / uptime."""
    results = status_engine.run_checks(request.get_host())
    status_engine.record_if_stale(results)
    return status_engine.attach_history(results)


def status_page(request):
    results = _checks(request)
    counts = status_engine.summary(results)
    return render(request, "home/status.html", {
        "overall": status_engine.overall_state(results),
        "groups": status_engine.grouped(results),
        "counts": counts,
        "checked_at": timezone.now(),
        "has_history": any(r["history"] for r in results if r["persist"]),      # false until a second sample is stored
    })


def status_data(request):
    """The same checks as JSON — for an uptime monitor or another dashboard to poll (signed-in users only)."""
    results = _checks(request)
    return JsonResponse({
        "status": status_engine.overall_state(results),
        "checked_at": timezone.now().isoformat(),
        "checks": [
            {"group": r["group"], "name": r["name"], "state": r["state"], "http_status": r["http_status"],
             "latency_ms": r["latency_ms"], "uptime_pct": r["uptime_pct"], "detail": r["detail"]}
            for r in results
        ],
    })
