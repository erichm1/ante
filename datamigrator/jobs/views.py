import threading

from django.shortcuts import get_object_or_404, render
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from mappings.models import Mapping

from . import engine
from .models import MigrationRun
from .serializers import MigrationRunSerializer


class MigrationRunViewSet(viewsets.ModelViewSet):
    queryset = MigrationRun.objects.select_related("mapping").prefetch_related("logs")
    serializer_class = MigrationRunSerializer
    filterset_fields = ["mapping", "status"]
    http_method_names = ["get", "post", "head", "options"]  # runs are immutable once created

    @action(detail=False, methods=["post"], url_path="trigger")
    def trigger(self, request):
        """Creates the run and starts it in a background thread, then returns
        immediately with the run's id — the caller polls GET /api/runs/<id>/
        for live progress (records_*, requests_made) instead of waiting here."""
        mapping = get_object_or_404(Mapping, pk=request.data.get("mapping_id"))
        run = MigrationRun.objects.create(mapping=mapping, status=MigrationRun.STATUS_RUNNING)
        threading.Thread(target=engine.run_migration_in_background, args=(run.id,), daemon=True).start()
        return Response(MigrationRunSerializer(run).data, status=status.HTTP_201_CREATED)


def run_detail(request, pk):
    run = get_object_or_404(MigrationRun.objects.select_related("mapping").prefetch_related("logs"), pk=pk)
    return render(request, "jobs/run_detail.html", {"run": run})
