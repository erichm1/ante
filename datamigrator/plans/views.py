from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from jobs.views import _route_info
from mappings.models import Mapping

from . import executor
from .models import MigrationPlan, PlanStep
from .serializers import MigrationPlanSerializer, PlanStepSerializer


class MigrationPlanViewSet(viewsets.ModelViewSet):
    queryset = MigrationPlan.objects.prefetch_related("steps__mapping", "steps__run")
    serializer_class = MigrationPlanSerializer
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def update(self, request, *args, **kwargs):
        """PATCH edits name/description/rate_limit_per_second (status and
        scheduled_at are read-only on the serializer — those only change via
        `execute` below) — only while the plan is still a draft."""
        plan = self.get_object()
        if not plan.is_editable:
            return Response({"error": "Can't edit a plan once it's left draft."}, status=status.HTTP_400_BAD_REQUEST)
        return super().update(request, *args, **kwargs)

    @action(detail=True, methods=["post"], url_path="steps")
    def add_step(self, request, pk=None):
        plan = self.get_object()
        if not plan.is_editable:
            return Response({"error": "Can't edit a plan once it's left draft."}, status=status.HTTP_400_BAD_REQUEST)

        mapping = get_object_or_404(Mapping, pk=request.data.get("mapping_id"))
        rate_limit_raw = request.data.get("rate_limit_per_second")
        rate_limit = None
        if rate_limit_raw not in (None, ""):
            try:
                rate_limit = float(rate_limit_raw)
                if rate_limit <= 0:
                    raise ValueError
            except (TypeError, ValueError):
                return Response({"error": "rate_limit_per_second must be a number greater than zero."}, status=status.HTTP_400_BAD_REQUEST)

        next_order = (plan.steps.count() or 0) + 1
        step = PlanStep.objects.create(plan=plan, mapping=mapping, order=next_order, rate_limit_per_second=rate_limit)
        return Response(PlanStepSerializer(step).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="execute")
    def execute(self, request, pk=None):
        """Same validation/branching shape as MigrationRunViewSet.trigger in
        jobs/views.py — only difference is this starts the sequential plan
        executor instead of a single run."""
        plan = self.get_object()
        if not plan.steps.exists():
            return Response({"error": "Add at least one step before executing."}, status=status.HTTP_400_BAD_REQUEST)
        if plan.status not in (MigrationPlan.STATUS_DRAFT, MigrationPlan.STATUS_FAILED, MigrationPlan.STATUS_COMPLETED):
            return Response({"error": f"Plan is already {plan.status}."}, status=status.HTTP_400_BAD_REQUEST)

        rate_limit_raw = request.data.get("rate_limit_per_second")
        if rate_limit_raw not in (None, ""):
            try:
                rate_limit = float(rate_limit_raw)
                if rate_limit <= 0:
                    raise ValueError
                plan.rate_limit_per_second = rate_limit
            except (TypeError, ValueError):
                return Response({"error": "rate_limit_per_second must be a number greater than zero."}, status=status.HTTP_400_BAD_REQUEST)

        scheduled_at_raw = request.data.get("scheduled_at")
        if not scheduled_at_raw:
            plan.status = MigrationPlan.STATUS_EXECUTING
            plan.scheduled_at = None
            plan.save()
            executor.start_plan(plan.id)
            return Response(MigrationPlanSerializer(plan).data, status=status.HTTP_200_OK)

        scheduled_at = parse_datetime(scheduled_at_raw)
        if not scheduled_at:
            return Response({"error": "scheduled_at must be an ISO 8601 datetime."}, status=status.HTTP_400_BAD_REQUEST)
        if timezone.is_naive(scheduled_at):
            scheduled_at = timezone.make_aware(scheduled_at)

        if scheduled_at <= timezone.now():
            plan.status = MigrationPlan.STATUS_EXECUTING
            plan.scheduled_at = None
            plan.save()
            executor.start_plan(plan.id)
        else:
            plan.status = MigrationPlan.STATUS_SCHEDULED
            plan.scheduled_at = scheduled_at
            plan.save()
        return Response(MigrationPlanSerializer(plan).data, status=status.HTTP_200_OK)


class PlanStepViewSet(viewsets.ModelViewSet):
    queryset = PlanStep.objects.select_related("plan", "mapping", "run")
    serializer_class = PlanStepSerializer
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def update(self, request, *args, **kwargs):
        """PATCH edits this step's own rate_limit_per_second (mapping/order/
        plan/run are read-only on the serializer) — only while the plan is
        still a draft."""
        step = self.get_object()
        if not step.plan.is_editable:
            return Response({"error": "Can't edit a plan once it's left draft."}, status=status.HTTP_400_BAD_REQUEST)
        return super().update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        step = self.get_object()
        if not step.plan.is_editable:
            return Response({"error": "Can't edit a plan once it's left draft."}, status=status.HTTP_400_BAD_REQUEST)
        return super().destroy(request, *args, **kwargs)

    @action(detail=True, methods=["post"], url_path="move")
    def move(self, request, pk=None):
        step = self.get_object()
        if not step.plan.is_editable:
            return Response({"error": "Can't edit a plan once it's left draft."}, status=status.HTTP_400_BAD_REQUEST)

        direction = request.data.get("direction")
        neighbor_qs = PlanStep.objects.filter(plan=step.plan)
        neighbor = (
            neighbor_qs.filter(order__lt=step.order).order_by("-order").first() if direction == "up"
            else neighbor_qs.filter(order__gt=step.order).order_by("order").first() if direction == "down"
            else None
        )
        if not neighbor:
            return Response({"error": "Nothing to swap with in that direction."}, status=status.HTTP_400_BAD_REQUEST)

        # Swap through a temporary value first — (plan, order) is unique, so writing either
        # row straight to the other's current order would collide mid-transaction. order is
        # a PositiveIntegerField (DB-level CHECK order >= 0), so the temp value must stay
        # non-negative — TEMP_ORDER is just something no real plan's step count reaches.
        # Capture both original values before mutating anything: reassigning step.order /
        # neighbor.order first (then reading them back for the DB writes) swaps them back
        # to their starting values by the time the third update runs, silently no-opping.
        TEMP_ORDER = 10**9
        step_order, neighbor_order = step.order, neighbor.order
        PlanStep.objects.filter(pk=step.pk).update(order=TEMP_ORDER)
        PlanStep.objects.filter(pk=neighbor.pk).update(order=step_order)
        PlanStep.objects.filter(pk=step.pk).update(order=neighbor_order)
        step.order, neighbor.order = neighbor_order, step_order
        return Response(PlanStepSerializer(step).data)


# ---- Page views -----------------------------------------------------------

PAGE_SIZE_CHOICES = (10, 25, 50)
DEFAULT_PAGE_SIZE = 10


def plan_list(request):
    plans = MigrationPlan.objects.prefetch_related("steps")

    q = request.GET.get("q", "").strip()
    status_filter = request.GET.get("status", "").strip()
    if q:
        plans = plans.filter(name__icontains=q)
    if status_filter in dict(MigrationPlan.STATUS_CHOICES):
        plans = plans.filter(status=status_filter)

    try:
        page_size = int(request.GET.get("page_size", DEFAULT_PAGE_SIZE))
    except ValueError:
        page_size = DEFAULT_PAGE_SIZE
    if page_size not in PAGE_SIZE_CHOICES:
        page_size = DEFAULT_PAGE_SIZE

    page_obj = Paginator(plans, page_size).get_page(request.GET.get("page"))

    return render(request, "plans/list.html", {
        "page_obj": page_obj,
        "filters": {"q": q, "status": status_filter},
        "status_choices": MigrationPlan.STATUS_CHOICES,
        "page_size": page_size,
        "page_size_choices": PAGE_SIZE_CHOICES,
    })


def plan_detail(request, pk):
    plan = get_object_or_404(
        MigrationPlan.objects.prefetch_related(
            "steps__mapping__source_connection",
            "steps__mapping__source_connection__integration_install__integration",
            "steps__mapping__destination_connections__integration_install__integration",
            "steps__mapping__entity_mappings__source_entity",
            "steps__mapping__entity_mappings__target_entity",
            "steps__run",
        ),
        pk=pk,
    )
    steps = list(plan.steps.all())
    for step in steps:
        step.route = _route_info(step.mapping)

    return render(request, "plans/detail.html", {
        "plan": plan,
        "steps": steps,
        "mappings": Mapping.objects.order_by("name"),
    })
