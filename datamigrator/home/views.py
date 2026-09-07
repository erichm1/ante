from collections import Counter
from urllib.parse import urlsplit

from django.db.models import Count, F, Q
from django.http import JsonResponse
from django.shortcuts import render

from connections.models import ApiCallLog, Connection
from jobs.models import MigrationRun

ACTIVE_STATUSES = (MigrationRun.STATUS_PENDING, MigrationRun.STATUS_RUNNING)


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
