from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from schemas.models import Entity

from . import exporter
from .models import Report, ReportSection
from .serializers import ReportSectionSerializer, ReportSerializer


class ReportViewSet(viewsets.ModelViewSet):
    queryset = Report.objects.prefetch_related("sections__entity")
    serializer_class = ReportSerializer

    @action(detail=True, methods=["post"], url_path="sections")
    def add_section(self, request, pk=None):
        report = self.get_object()
        entity = get_object_or_404(Entity, pk=request.data.get("entity_id"))
        field_ids = request.data.get("field_ids") or []
        if not isinstance(field_ids, list):
            return Response({"error": "field_ids must be a list."}, status=status.HTTP_400_BAD_REQUEST)

        valid_ids = set(entity.fields.values_list("pk", flat=True))
        unknown = [fid for fid in field_ids if fid not in valid_ids]
        if unknown:
            return Response({"error": f"These field ids don't belong to {entity.name}: {unknown}."}, status=status.HTTP_400_BAD_REQUEST)

        next_order = (report.sections.count() or 0) + 1
        section = ReportSection.objects.create(report=report, entity=entity, order=next_order, field_ids=field_ids)
        return Response(ReportSectionSerializer(section).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get"], url_path="preview")
    def preview(self, request, pk=None):
        report = self.get_object()
        return Response(exporter.build_report_preview(report))

    @action(detail=True, methods=["get"], url_path="export")
    def export(self, request, pk=None):
        report = self.get_object()
        csv_text = exporter.build_report_csv(report)
        response = HttpResponse(csv_text, content_type="text/csv")
        safe_name = "".join(c if c.isalnum() or c in "-_ " else "_" for c in report.name).strip() or "report"
        response["Content-Disposition"] = f'attachment; filename="{safe_name}.csv"'
        return response


class ReportSectionViewSet(viewsets.ModelViewSet):
    queryset = ReportSection.objects.select_related("report", "entity")
    serializer_class = ReportSectionSerializer

    @action(detail=True, methods=["post"], url_path="move")
    def move(self, request, pk=None):
        section = self.get_object()
        direction = request.data.get("direction")
        neighbor_qs = ReportSection.objects.filter(report=section.report)
        neighbor = (
            neighbor_qs.filter(order__lt=section.order).order_by("-order").first() if direction == "up"
            else neighbor_qs.filter(order__gt=section.order).order_by("order").first() if direction == "down"
            else None
        )
        if not neighbor:
            return Response({"error": "Nothing to swap with in that direction."}, status=status.HTTP_400_BAD_REQUEST)

        # Same temp-order swap as CallChainStepViewSet.move / PlanStepViewSet —
        # (report, order) is unique, so writing either row straight to the
        # other's current order would collide mid-transaction.
        TEMP_ORDER = 10**9
        section_order, neighbor_order = section.order, neighbor.order
        ReportSection.objects.filter(pk=section.pk).update(order=TEMP_ORDER)
        ReportSection.objects.filter(pk=neighbor.pk).update(order=section_order)
        ReportSection.objects.filter(pk=section.pk).update(order=neighbor_order)
        section.order, neighbor.order = neighbor_order, section_order
        return Response(ReportSectionSerializer(section).data)


# ---- Page views -----------------------------------------------------------

def report_list(request):
    reports = Report.objects.prefetch_related("sections")
    return render(request, "reports/list.html", {"reports": reports})


def report_detail(request, pk):
    report = get_object_or_404(Report.objects.prefetch_related("sections__entity", "sections__entity__fields"), pk=pk)
    entities = Entity.objects.select_related("connection").prefetch_related("fields").order_by("connection__name", "name")
    entities_data = [
        {
            "id": entity.pk,
            "name": entity.name,
            "connection_name": entity.connection.name,
            "fields": [{"id": f.pk, "name": f.name} for f in entity.fields.all()],
        }
        for entity in entities
    ]
    return render(request, "reports/detail.html", {"report": report, "entities_data": entities_data})
