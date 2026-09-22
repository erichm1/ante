from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, render
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from connections.models import Connection
from jobs import engine
from jobs.models import MigrationRun
from jobs.views import _connection_integration, _route_info
from schemas.models import Entity, Field
from schemas.serializers import EntitySerializer

from . import services
from .models import EntityMapping, FieldMapping, Mapping
from .preview import clamp_limit, preview_mapping
from .serializers import EntityMappingSerializer, FieldMappingSerializer, MappingSerializer


class MappingViewSet(viewsets.ModelViewSet):
    """Full CRUD for mappings, plus preview / cancel / duplicate.

      GET    /api/mappings/            list  (?q= searches name and description; ?source_connection=, ?destination=)
      POST   /api/mappings/            create
      GET    /api/mappings/<id>/       read
      PATCH  /api/mappings/<id>/       update (the origin is locked once entity pairs exist — see the serializer)
      DELETE /api/mappings/<id>/       delete (refused while one of its migrations is running)
      POST   /api/mappings/<id>/duplicate/   copy it with its entity pairs and field wires
    """

    queryset = Mapping.objects.select_related("source_connection").prefetch_related(
        "destination_connections", "entity_mappings__target_entity").annotate(runs_total=Count("runs", distinct=True)).order_by("-created_at", "-id")
    serializer_class = MappingSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        params = self.request.query_params
        if params.get("q"):
            qs = qs.filter(Q(name__icontains=params["q"].strip()) | Q(description__icontains=params["q"].strip()))
        if params.get("source_connection"):
            qs = qs.filter(source_connection_id=params["source_connection"])
        if params.get("destination"):
            qs = qs.filter(destination_connections__pk=params["destination"])
        return qs

    def destroy(self, request, *args, **kwargs):
        mapping = self.get_object()
        running = MigrationRun.objects.filter(mapping=mapping).filter(
            Q(status=MigrationRun.STATUS_RUNNING) | Q(status=MigrationRun.STATUS_PENDING, scheduled_at__isnull=True))
        if running.exists():
            return Response({"error": f"“{mapping.name}” has a migration running or queued — stop it first (Kill), then delete."}, status=400)
        return super().destroy(request, *args, **kwargs)

    @action(detail=True, methods=["post"], url_path="duplicate")
    def duplicate(self, request, pk=None):
        """A copy of this mapping: same origin and destinations, every entity pair and every field wire (with its
        transforms). Runs and plan steps are not copied. Optional body: {"name": "…"}."""
        original = self.get_object()
        name = (request.data.get("name") or f"{original.name} (copy)").strip()[:120]
        with transaction.atomic():
            copy = Mapping.objects.create(name=name, description=original.description, source_connection=original.source_connection)
            copy.destination_connections.set(original.destination_connections.all())
            for pair in original.entity_mappings.all():
                new_pair = EntityMapping.objects.create(
                    mapping=copy, source_entity=pair.source_entity, target_entity=pair.target_entity, write_method=pair.write_method)
                FieldMapping.objects.bulk_create([
                    FieldMapping(entity_mapping=new_pair, source_field=fm.source_field, target_field=fm.target_field,
                                 transform_rules=fm.transform_rules, transform=fm.transform,
                                 status=fm.status, match_score=fm.match_score, match_reason=fm.match_reason)
                    for fm in pair.field_mappings.all()])
        fresh = self.get_queryset().get(pk=copy.pk)
        return Response(MappingSerializer(fresh, context={"request": request}).data, status=201)

    @action(detail=True, methods=["post"], url_path="cancel")
    def cancel(self, request, pk=None):
        """The Kill button on a mapping: stop everything of this mapping that is running right now, and any
        queued (not-yet-started) runs. Runs *scheduled* for later are left alone — cancel those one by one."""
        mapping = self.get_object()
        active = MigrationRun.objects.filter(mapping=mapping).filter(
            Q(status=MigrationRun.STATUS_RUNNING) | Q(status=MigrationRun.STATUS_PENDING, scheduled_at__isnull=True))
        return Response({"cancelled": engine.cancel_runs(active)})

    @action(detail=True, methods=["get"], url_path="preview")
    def preview(self, request, pk=None):
        """Read-only dry run: a sample of each pair's source records next to the
        payloads the field mappings would produce from them (see mappings/
        preview.py). Nothing is written to any target system."""
        mapping = self.get_object()
        limit = clamp_limit(request.query_params.get("limit"))
        drafts = request.query_params.get("drafts") in ("1", "true")        # also show what the unreviewed suggestions would add
        return Response({"limit": limit, "pairs": preview_mapping(mapping, limit, include_drafts=drafts)})

    # -- auto-mapping: suggestions saved as drafts, reviewed, then accepted or discarded ------------------------
    def _pairs(self, mapping):
        return list(mapping.entity_mappings.select_related("source_entity", "target_entity"))

    @action(detail=True, methods=["post"], url_path="auto-map")
    def auto_map(self, request, pk=None):
        """Suggest field wires for every entity pair of the mapping from the fields' names, saved as DRAFTS that
        don't run until accepted. Body (all optional): min_score 30-100, only_unmapped, replace_drafts, dry_run."""
        mapping = self.get_object()
        opts = {k: request.data.get(k) for k in ("min_score", "only_unmapped", "replace_drafts")}
        results = [services.auto_map_pair(p, dry_run=bool(request.data.get("dry_run")), **opts) for p in self._pairs(mapping)]
        return Response({"created": sum(r["created"] for r in results), "pairs": results})

    @action(detail=True, methods=["post"], url_path="confirm-drafts")
    def confirm_drafts(self, request, pk=None):
        return Response({"confirmed": services.confirm_drafts(self._pairs(self.get_object()), request.data.get("ids"))})

    @action(detail=True, methods=["post"], url_path="discard-drafts")
    def discard_drafts(self, request, pk=None):
        return Response({"discarded": services.discard_drafts(self._pairs(self.get_object()), request.data.get("ids"))})


class EntityMappingViewSet(viewsets.ModelViewSet):
    queryset = EntityMapping.objects.select_related(
        "source_entity", "source_entity__connection", "target_entity", "target_entity__connection",
    ).prefetch_related("field_mappings")
    serializer_class = EntityMappingSerializer
    filterset_fields = ["mapping"]

    @action(detail=True, methods=["post"], url_path="auto-map")
    def auto_map(self, request, pk=None):
        """Suggest field wires for this entity pair from its fields' names, as DRAFTS (see MappingViewSet.auto_map)."""
        pair = self.get_object()
        opts = {k: request.data.get(k) for k in ("min_score", "only_unmapped", "replace_drafts")}
        return Response(services.auto_map_pair(pair, dry_run=bool(request.data.get("dry_run")), **opts))

    @action(detail=True, methods=["post"], url_path="confirm-drafts")
    def confirm_drafts(self, request, pk=None):
        return Response({"confirmed": services.confirm_drafts([self.get_object()], request.data.get("ids"))})

    @action(detail=True, methods=["post"], url_path="discard-drafts")
    def discard_drafts(self, request, pk=None):
        return Response({"discarded": services.discard_drafts([self.get_object()], request.data.get("ids"))})


class FieldMappingViewSet(viewsets.ModelViewSet):
    queryset = FieldMapping.objects.all()
    serializer_class = FieldMappingSerializer
    filterset_fields = ["entity_mapping"]


# ---- Page views -----------------------------------------------------------

PAGE_SIZE_CHOICES = (10, 25, 50, 100)
DEFAULT_PAGE_SIZE = 10
TABS = ("canvas", "raw", "runs", "connections")


def _list_context(request):
    """Shared by the two list pages: the (optionally searched) mappings with their counts, plus the JSON the
    create / edit dialog (static/js/mappings_crud.js) works from."""
    mappings = Mapping.objects.select_related("source_connection").prefetch_related(
        "destination_connections", "entity_mappings__target_entity").annotate(runs_total=Count("runs", distinct=True)).order_by("-created_at", "-id")
    q = request.GET.get("q", "").strip()
    if q:
        mappings = mappings.filter(Q(name__icontains=q) | Q(description__icontains=q))
    connections = Connection.objects.all()
    return {
        "mappings": mappings, "connections": connections, "q": q,
        "mappings_json": MappingSerializer(mappings, many=True).data,
        "connections_json": [{"id": c.pk, "name": c.name} for c in connections],
    }


def mapping_list(request):
    return render(request, "mappings/list.html", {**_list_context(request), "open_tab": "raw"})


def canvas_list(request):
    """Same underlying Mapping objects as Mappings — its own sidebar entry
    per the user's request, opening straight into a mapping's canvas tab
    instead of its raw table."""
    return render(request, "mappings/canvas_list.html", {**_list_context(request), "open_tab": "canvas"})


def mapping_detail(request, pk):
    """One workspace per mapping: Canvas / Raw / Runs / Connections as tabs
    (?tab=, same plain-link pattern as jobs:run_list) — replaces the old
    separate mappings:raw / mappings:canvas pages, and folds in this
    mapping's own run history and connection summaries so there's no need
    to jump out to /jobs/ or /connections/ just to check on it."""
    mapping = get_object_or_404(
        Mapping.objects.select_related("source_connection").prefetch_related("destination_connections"), pk=pk,
    )
    tab = request.GET.get("tab", "canvas")
    if tab not in TABS:
        tab = "canvas"

    context = {
        "mapping": mapping, "tab": tab, "write_methods": EntityMapping.METHOD_CHOICES,
        "route": _route_info(mapping),
        "mappings_json": [MappingSerializer(mapping).data],
        "connections": Connection.objects.all(),
        "connections_json": [{"id": c.pk, "name": c.name} for c in Connection.objects.all()],
        "pairs_count": mapping.entity_mappings.count(),
        "draft_count": FieldMapping.objects.filter(
            entity_mapping__mapping=mapping, status=FieldMapping.STATUS_DRAFT).count(),
    }

    if tab == "canvas":
        source_entities = Entity.objects.filter(connection=mapping.source_connection).prefetch_related("fields")
        entity_mappings = mapping.entity_mappings.select_related(
            "source_entity", "source_entity__connection", "target_entity", "target_entity__connection",
        ).prefetch_related("field_mappings")
        context["canvas_data"] = {
            "mapping": MappingSerializer(mapping).data,
            "source_entities": EntitySerializer(source_entities, many=True).data,
            "entity_mappings": EntityMappingSerializer(entity_mappings, many=True).data,
            "field_types": list(Field.TYPE_CHOICES),
        }

    elif tab == "raw":
        context["entity_mappings"] = mapping.entity_mappings.select_related(
            "source_entity", "source_entity__connection", "target_entity", "target_entity__connection",
        ).prefetch_related("field_mappings__source_field", "field_mappings__target_field")

    elif tab == "runs":
        runs = MigrationRun.objects.filter(mapping=mapping).order_by("-started_at")
        try:
            page_size = int(request.GET.get("page_size", DEFAULT_PAGE_SIZE))
        except ValueError:
            page_size = DEFAULT_PAGE_SIZE
        if page_size not in PAGE_SIZE_CHOICES:
            page_size = DEFAULT_PAGE_SIZE
        context["page_obj"] = Paginator(runs, page_size).get_page(request.GET.get("page"))
        context["page_size"] = page_size
        context["page_size_choices"] = PAGE_SIZE_CHOICES

    elif tab == "connections":
        connections = [mapping.source_connection] + list(mapping.destination_connections.all())
        context["connection_roles"] = [
            {
                "connection": c,
                "integration": _connection_integration(c),
                "role": "Source" if c.id == mapping.source_connection_id else "Destination",
                "entity_count": Entity.objects.filter(connection=c).count(),
            }
            for c in connections
        ]

    return render(request, "mappings/detail.html", context)
