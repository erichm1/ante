import threading

from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from integrations.models import Integration
from mappings.models import Mapping
from mappings.serializers import EntityMappingSerializer
from plans.models import MigrationPlan
from schemas.models import Entity
from schemas.serializers import EntitySerializer

from . import engine
from .models import MigrationRun
from .serializers import MigrationRunSerializer, RunStepStatusSerializer


def _start_now(mapping, rate_limit_per_second=None, input_file=None):
    run = MigrationRun.objects.create(
        mapping=mapping, status=MigrationRun.STATUS_RUNNING, rate_limit_per_second=rate_limit_per_second,
        input_file=input_file,
    )
    threading.Thread(target=engine.run_migration_in_background, args=(run.id,), daemon=True).start()
    return run


def _parse_rate_limit(raw):
    """Shared by trigger/trigger_batch: (parsed_value, error_response) — the
    caller returns error_response as-is when it isn't None, else proceeds
    with parsed_value (None means unthrottled, same as leaving it blank)."""
    if raw in (None, ""):
        return None, None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None, Response({"error": "rate_limit_per_second must be a number."}, status=status.HTTP_400_BAD_REQUEST)
    if value <= 0:
        return None, Response({"error": "rate_limit_per_second must be greater than zero."}, status=status.HTTP_400_BAD_REQUEST)
    return value, None


class MigrationRunViewSet(viewsets.ModelViewSet):
    queryset = MigrationRun.objects.select_related("mapping").prefetch_related("logs", "step_statuses")
    serializer_class = MigrationRunSerializer
    filterset_fields = ["mapping", "status"]
    http_method_names = ["get", "post", "head", "options"]  # runs are immutable once created

    @action(detail=False, methods=["post"], url_path="trigger")
    def trigger(self, request):
        """Creates the run and, by default, starts it in a background thread
        right away — the caller polls GET /api/runs/<id>/ for live progress
        (records_*, requests_made) instead of waiting here. Pass an ISO
        `scheduled_at` in the future instead to queue it: the run is created
        `pending` and jobs/scheduler.py's poller promotes and starts it once
        that time arrives. Pass `rate_limit_per_second` (any positive number)
        to cap how fast jobs/engine.py fires source reads + destination
        writes for this run — it rides along on the row either way, so a
        scheduled run is throttled the same as an immediate one once it starts.
        Attach `input_file` (multipart/form-data, not JSON) to feed this one
        run fresh CSV/XLSX data instead of the mapping's source entity's own
        stored file — see jobs/engine.py::records_for."""
        mapping = get_object_or_404(Mapping, pk=request.data.get("mapping_id"))
        scheduled_at_raw = request.data.get("scheduled_at")
        input_file = request.FILES.get("input_file")

        rate_limit, error_response = _parse_rate_limit(request.data.get("rate_limit_per_second"))
        if error_response:
            return error_response

        if not scheduled_at_raw:
            run = _start_now(mapping, rate_limit_per_second=rate_limit, input_file=input_file)
            return Response(MigrationRunSerializer(run).data, status=status.HTTP_201_CREATED)

        scheduled_at = parse_datetime(scheduled_at_raw)
        if not scheduled_at:
            return Response({"error": "scheduled_at must be an ISO 8601 datetime."}, status=status.HTTP_400_BAD_REQUEST)
        if timezone.is_naive(scheduled_at):
            scheduled_at = timezone.make_aware(scheduled_at)

        if scheduled_at <= timezone.now():
            # Already due (or a naive "now") — nothing meaningful to schedule, just run it.
            run = _start_now(mapping, rate_limit_per_second=rate_limit, input_file=input_file)
        else:
            run = MigrationRun.objects.create(
                mapping=mapping, status=MigrationRun.STATUS_PENDING, scheduled_at=scheduled_at,
                rate_limit_per_second=rate_limit, input_file=input_file,
            )
        return Response(MigrationRunSerializer(run).data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["post"], url_path="trigger-batch")
    def trigger_batch(self, request):
        """Batch variant of trigger(): the same mapping run once per uploaded
        file — each file its own CSV/XLSX with a header row followed by data
        rows, exactly like a single run's input_file (see
        schemas/discovery.py::read_all_records_from_file), just N of them at
        once instead of hand-triggering one run per file. Runs execute
        sequentially in one background thread (jobs.engine.run_batch_in_background),
        not one thread per file — see that function's docstring for why.
        `rate_limit_per_second`, if given, applies to every run in the batch."""
        mapping = get_object_or_404(Mapping, pk=request.data.get("mapping_id"))
        files = request.FILES.getlist("input_files")
        if not files:
            return Response({"error": "Attach at least one file."}, status=status.HTTP_400_BAD_REQUEST)

        rate_limit, error_response = _parse_rate_limit(request.data.get("rate_limit_per_second"))
        if error_response:
            return error_response

        runs = [
            MigrationRun.objects.create(
                mapping=mapping, status=MigrationRun.STATUS_PENDING, rate_limit_per_second=rate_limit, input_file=f,
            )
            for f in files
        ]
        threading.Thread(target=engine.run_batch_in_background, args=([r.id for r in runs],), daemon=True).start()
        return Response(MigrationRunSerializer(runs, many=True).data, status=status.HTTP_201_CREATED)


def _connection_integration(connection):
    """The App Store Integration a Connection was installed from, or None for
    a hand-configured one (see connections:detail's own manual-add form)."""
    install = getattr(connection, "integration_install", None)
    return install.integration if install else None


def _route_info(mapping):
    """Icon-first summary of a mapping's source -> destination(s) for the
    Runs page: which connection(s) (via their App Store Integration, when
    installed from one) and which entity pairs are actually wired up —
    replaces showing the raw mapping name."""
    source_connection = mapping.source_connection
    destinations = [
        {"connection": conn, "integration": _connection_integration(conn)}
        for conn in mapping.destination_connections.all()
    ]
    entity_pairs = [
        {"source": em.source_entity.name, "target": em.target_entity.name}
        for em in mapping.entity_mappings.all()
    ]
    return {
        "source": {"connection": source_connection, "integration": _connection_integration(source_connection)},
        "destinations": destinations,
        "entity_pairs": entity_pairs,
    }


def _attach_routes(runs):
    runs = list(runs)
    for run in runs:
        run.route = _route_info(run.mapping)
    return runs


DETAIL_PAGE_SIZE = 10


def run_detail(request, pk):
    run = get_object_or_404(
        MigrationRun.objects.select_related(
            "mapping", "mapping__source_connection", "mapping__source_connection__integration_install__integration",
        ).prefetch_related(
            "mapping__destination_connections__integration_install__integration",
            "mapping__entity_mappings__source_entity", "mapping__entity_mappings__target_entity",
        ),
        pk=pk,
    )
    run.route = _route_info(run.mapping)

    # Two independent paginators on the same page — separate query params
    # (logs_page/calls_page) so paging through one doesn't reset the other.
    logs_page = Paginator(run.logs.all(), DETAIL_PAGE_SIZE).get_page(request.GET.get("logs_page"))
    api_calls_page = Paginator(
        run.api_call_logs.select_related("connection"), DETAIL_PAGE_SIZE,
    ).get_page(request.GET.get("calls_page"))

    return render(request, "jobs/run_detail.html", {"run": run, "logs_page": logs_page, "api_calls_page": api_calls_page})


PAGE_SIZE_CHOICES = (10, 25, 50, 100)
DEFAULT_PAGE_SIZE = 10


def _filter_runs(runs, request):
    """Search + filters shared by every run tab (Active/Pending/Scheduled/
    Failed/Finished — Plans is a different model entirely and skips this) —
    everything here is opt-in (blank params match everything), and combined
    with .distinct() since the integration filter and free-text search both
    join through to-many relations (destination_connections, entity_mappings)
    that can otherwise fan a single run out into duplicate rows. Status
    itself is fixed by the caller (the tab you're on), not a filter param here."""
    q = request.GET.get("q", "").strip()
    integration_id = request.GET.get("integration", "").strip()
    date_from = request.GET.get("date_from", "").strip()
    date_to = request.GET.get("date_to", "").strip()

    if q:
        query = Q(mapping__name__icontains=q) | Q(mapping__source_connection__name__icontains=q) \
            | Q(mapping__destination_connections__name__icontains=q) \
            | Q(mapping__entity_mappings__source_entity__name__icontains=q) \
            | Q(mapping__entity_mappings__target_entity__name__icontains=q)
        if q.isdigit():
            query |= Q(pk=int(q))
        runs = runs.filter(query)

    if integration_id.isdigit():
        runs = runs.filter(
            Q(mapping__source_connection__integration_install__integration_id=integration_id)
            | Q(mapping__destination_connections__integration_install__integration_id=integration_id)
        )

    if date_from:
        runs = runs.filter(started_at__date__gte=date_from)
    if date_to:
        runs = runs.filter(started_at__date__lte=date_to)

    if q or integration_id.isdigit():
        runs = runs.distinct()

    return runs, {"q": q, "integration": integration_id, "date_from": date_from, "date_to": date_to}


TABS = ("active", "pending", "scheduled", "failed", "finished", "plans")


def run_list(request):
    tab = request.GET.get("tab", "active")
    if tab not in TABS:
        tab = "active"

    base_qs = MigrationRun.objects.select_related(
        "mapping", "mapping__source_connection", "mapping__source_connection__integration_install__integration",
    ).prefetch_related(
        "mapping__destination_connections__integration_install__integration",
        "mapping__entity_mappings__source_entity", "mapping__entity_mappings__target_entity",
    )

    context = {
        "tab": tab,
        "integrations": Integration.objects.filter(is_active=True).order_by("name"),
        "mappings": Mapping.objects.select_related("source_connection").order_by("name"),
    }

    if tab == "plans":
        context["plans"] = MigrationPlan.objects.prefetch_related("steps").order_by("-created_at")
        return render(request, "jobs/run_list.html", context)

    # Search/integration/date-range apply the same way regardless of which
    # run tab you're on — only the status split below differs per tab.
    filtered_qs, filters = _filter_runs(base_qs, request)
    context["filters"] = filters

    try:
        page_size = int(request.GET.get("page_size", DEFAULT_PAGE_SIZE))
    except ValueError:
        page_size = DEFAULT_PAGE_SIZE
    if page_size not in PAGE_SIZE_CHOICES:
        page_size = DEFAULT_PAGE_SIZE
    context.update({"page_size": page_size, "page_size_choices": PAGE_SIZE_CHOICES})

    if tab == "active":
        tab_qs = filtered_qs.filter(status=MigrationRun.STATUS_RUNNING)
    elif tab == "pending":
        tab_qs = filtered_qs.filter(status=MigrationRun.STATUS_PENDING).filter(
            Q(scheduled_at__isnull=True) | Q(scheduled_at__lte=timezone.now())
        )
    elif tab == "scheduled":
        tab_qs = filtered_qs.filter(
            status=MigrationRun.STATUS_PENDING, scheduled_at__gt=timezone.now(),
        ).order_by("scheduled_at")
    elif tab == "failed":
        tab_qs = filtered_qs.filter(status=MigrationRun.STATUS_FAILED)
    else:  # finished
        tab_qs = filtered_qs.filter(status=MigrationRun.STATUS_SUCCESS)

    page_obj = Paginator(tab_qs, page_size).get_page(request.GET.get("page"))
    page_obj.object_list = _attach_routes(page_obj.object_list)
    context["page_obj"] = page_obj

    return render(request, "jobs/run_list.html", context)


def run_snapshot(request, pk):
    """GitHub-Actions-style live pipeline view: the same entity/field layout
    as the mapping canvas tab (see mappings/views.py::mapping_detail), rendered
    read-only with per-entity-pair status badges sourced from this run's
    RunStepStatus rows (nested on MigrationRunSerializer as step_statuses),
    polled live via GET /api/runs/<id>/ while the run is in flight."""
    run = get_object_or_404(MigrationRun.objects.select_related("mapping").prefetch_related("step_statuses"), pk=pk)
    mapping = run.mapping
    source_entities = Entity.objects.filter(connection=mapping.source_connection).prefetch_related("fields")
    entity_mappings = mapping.entity_mappings.select_related(
        "source_entity", "source_entity__connection", "target_entity", "target_entity__connection",
    ).prefetch_related("field_mappings")

    canvas_data = {
        "source_entities": EntitySerializer(source_entities, many=True).data,
        "entity_mappings": EntityMappingSerializer(entity_mappings, many=True).data,
    }
    initial_steps = RunStepStatusSerializer(run.step_statuses.all(), many=True).data
    return render(request, "jobs/run_snapshot.html", {
        "run": run, "canvas_data": canvas_data, "initial_steps": initial_steps,
    })
