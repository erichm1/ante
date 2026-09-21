from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import F, Max
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from chains import step_forms
from chains.models import CallChain, CallChainRun, CallChainStep
from connections.models import Connection
from notifications.services import notify_plan
from jobs import engine as jobs_engine
from jobs.models import MigrationRun
from jobs.views import _route_info
from mappings.models import Mapping

from chains import executor as chains_executor

from . import executor
from .models import MigrationPlan, PlanStep
from .serializers import MigrationPlanSerializer, PlanStepSerializer


LOCK_MSG = "Can't change a plan while it's executing or scheduled."
MAX_WAIT_SECONDS = 3600


def _positive_rate(raw):
    """(value_or_None, error) for an optional requests/second cap."""
    if raw in (None, ""):
        return None, None
    try:
        value = float(raw)
        if value <= 0:
            raise ValueError
    except (TypeError, ValueError):
        return None, "rate_limit_per_second must be a number greater than zero."
    return value, None


def _pair_ids(mapping, raw):
    """(sorted unique ids, error) — a step may run only some of its mapping's entity pairs."""
    if raw in (None, ""):
        return [], None
    if not isinstance(raw, list):
        return None, "entity_mapping_ids must be a list of entity pair ids."
    try:
        ids = sorted({int(i) for i in raw})
    except (TypeError, ValueError):
        return None, "entity_mapping_ids must be a list of entity pair ids."
    valid = set(mapping.entity_mappings.filter(pk__in=ids).values_list("pk", flat=True))
    if valid != set(ids):
        return None, "Every chosen entity pair must belong to this mapping."
    # Choosing every pair is the same as choosing none: the whole mapping.
    return ([] if len(ids) == mapping.entity_mappings.count() else ids), None


def _wait_seconds(raw):
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None, "Wait seconds must be a number."
    if not 0 <= value <= MAX_WAIT_SECONDS:
        return None, f"Wait seconds must be between 0 and {MAX_WAIT_SECONDS}."
    return value, None


class MigrationPlanViewSet(viewsets.ModelViewSet):
    queryset = MigrationPlan.objects.select_related("inline_run").prefetch_related(
        "steps__mapping__entity_mappings", "steps__run", "steps__chain", "steps__chain_run", "steps__inline_step__connection",
        "inline_run__step_results")
    serializer_class = MigrationPlanSerializer
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def update(self, request, *args, **kwargs):
        """PATCH edits name/description/rate_limit_per_second (status and
        scheduled_at are read-only on the serializer — those only change via
        `execute` below) — only while the plan isn't executing or scheduled.
        execution_mode is chosen once at creation and can't be changed
        afterward, draft or not — every step already committed to being a
        Mapping or a Chain based on it."""
        plan = self.get_object()
        if not plan.is_editable:
            return Response({"error": LOCK_MSG}, status=status.HTTP_400_BAD_REQUEST)
        new_mode = request.data.get("execution_mode")
        if new_mode is not None and new_mode != plan.execution_mode:
            return Response({"error": "Can't change execution mode after a plan is created."}, status=status.HTTP_400_BAD_REQUEST)
        return super().update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        plan = self.get_object()
        if plan.status == MigrationPlan.STATUS_EXECUTING:
            return Response({"error": "Can't delete a plan while it's executing."}, status=status.HTTP_400_BAD_REQUEST)
        inline_chain = plan.inline_chain
        response = super().destroy(request, *args, **kwargs)
        if inline_chain is not None:
            inline_chain.delete()       # the plan's hidden chain of function blocks goes with it
        return response

    @action(detail=True, methods=["post"], url_path="steps")
    def add_step(self, request, pk=None):
        """Append a step — or, with `position` (1-based), insert it there and push the rest down.
        A step is a mapping (`mapping_id`, optionally just some pairs via `entity_mapping_ids`),
        a chain (`chain_id`), or a pause (`wait_seconds`, mixed plans only)."""
        plan = self.get_object()
        if not plan.is_editable:
            return Response({"error": LOCK_MSG}, status=status.HTTP_400_BAD_REQUEST)
        data = request.data
        fields = {"plan": plan}
        function = data.get("function")

        if isinstance(function, dict):
            return self._add_function_step(plan, function, data.get("position"))
        if data.get("wait_seconds") not in (None, ""):
            if plan.execution_mode != MigrationPlan.MODE_MIXED:
                return Response({"error": "Wait steps need a mixed plan."}, status=status.HTTP_400_BAD_REQUEST)
            seconds, error = _wait_seconds(data.get("wait_seconds"))
            if error:
                return Response({"error": error}, status=status.HTTP_400_BAD_REQUEST)
            fields["wait_seconds"] = seconds
        elif (plan.execution_mode == MigrationPlan.MODE_CHAIN
              or (plan.execution_mode == MigrationPlan.MODE_MIXED and data.get("chain_id") not in (None, ""))):
            fields["chain"] = get_object_or_404(CallChain, pk=data.get("chain_id"))
        else:
            mapping = get_object_or_404(Mapping, pk=data.get("mapping_id"))
            rate_limit, error = _positive_rate(data.get("rate_limit_per_second"))
            pairs, pair_error = _pair_ids(mapping, data.get("entity_mapping_ids"))
            if error or pair_error:
                return Response({"error": error or pair_error}, status=status.HTTP_400_BAD_REQUEST)
            fields.update(mapping=mapping, rate_limit_per_second=rate_limit, entity_mapping_ids=pairs)

        with transaction.atomic():
            last = plan.steps.aggregate(m=Max("order"))["m"] or 0
            try:
                position = int(data.get("position"))
            except (TypeError, ValueError):
                position = None
            if position is not None and 1 <= position <= last:
                # Shift from the bottom up so no two steps ever share an order mid-way.
                for later in plan.steps.filter(order__gte=position).order_by("-order"):
                    later.order += 1
                    later.save(update_fields=["order"])
                fields["order"] = position
            else:
                fields["order"] = last + 1      # max+1, not count+1: deleting a middle step leaves a gap
            step = PlanStep.objects.create(**fields)
        return Response(PlanStepSerializer(step).data, status=status.HTTP_201_CREATED)

    def _add_function_step(self, plan, function, position):
        """A function block — a request, next page, find in list, check header, file preview, async job — is
        a step of the plan's hidden inline chain, plus a PlanStep pointing at it. All of a plan's blocks live
        in that ONE chain so they share a context when the plan runs."""
        if plan.execution_mode != MigrationPlan.MODE_MIXED:
            return Response({"error": "Function blocks need a mixed plan."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            with transaction.atomic():
                last = plan.steps.aggregate(m=Max("order"))["m"] or 0
                try:
                    position = int(position)
                except (TypeError, ValueError):
                    position = None
                insert = position is not None and 1 <= position <= last
                target = position if insert else last + 1

                chain = plan.inline_chain
                if chain is None:
                    connection = Connection.objects.filter(pk=function.get("connection")).first() or Connection.objects.order_by("pk").first()
                    if connection is None:
                        raise ValueError("Create a connection first — function blocks call APIs through one.")
                    chain = CallChain.objects.create(name=f"Plan {plan.pk} · function blocks", connection=connection, hidden=True)
                    plan.inline_chain = chain
                    plan.save(update_fields=["inline_chain"])

                earlier = [ps.inline_step for ps in plan.steps.filter(inline_step__isnull=False, order__lt=target).select_related("inline_step")]
                chain_order = (chain.steps.aggregate(m=Max("order"))["m"] or 0) + 1
                step_fields, error = step_forms.new_step_fields(chain, function, earlier=earlier, order=chain_order, allow_connection=True)
                if error:
                    raise ValueError(error)
                inline_step = CallChainStep.objects.create(**step_fields)

                if insert:
                    for later in plan.steps.filter(order__gte=position).order_by("-order"):
                        later.order += 1
                        later.save(update_fields=["order"])
                step = PlanStep.objects.create(plan=plan, inline_step=inline_step, order=target)
        except ValueError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(PlanStepSerializer(step).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="cancel")
    def cancel(self, request, pk=None):
        """The Kill button. A running plan is asked to stop: the current mapping run / chain / function block is
        flagged too, and the executor stops before the next step (and mid-wait). A plan that only *scheduled*
        is simply cancelled. Records already written stay written. An "executing" plan whose thread is gone
        (a server restart) is closed immediately."""
        plan = self.get_object()
        if plan.status == MigrationPlan.STATUS_SCHEDULED:
            plan.status, plan.scheduled_at = MigrationPlan.STATUS_CANCELLED, None
            plan.save(update_fields=["status", "scheduled_at"])
            notify_plan(plan, "cancelled")            # it never started: cancelled, not killed
            return Response(MigrationPlanSerializer(plan).data)
        if plan.status != MigrationPlan.STATUS_EXECUTING:
            return Response({"error": f"Plan is {plan.status} — nothing to stop."}, status=status.HTTP_400_BAD_REQUEST)

        plan.cancel_requested = True
        fields = ["cancel_requested"]
        orphaned = plan.pk not in executor.ACTIVE_PLANS
        if orphaned:
            plan.status = MigrationPlan.STATUS_CANCELLED
            fields.append("status")
        plan.save(update_fields=fields)
        if orphaned:
            notify_plan(plan, "killed")

        # Reach whatever the plan is running right now, not just the plan itself.
        step_run_ids = [r for r in plan.steps.values_list("run_id", flat=True) if r]
        jobs_engine.cancel_runs(MigrationRun.objects.filter(pk__in=step_run_ids, status__in=[MigrationRun.STATUS_PENDING, MigrationRun.STATUS_RUNNING]))
        chain_run_ids = [r for r in plan.steps.values_list("chain_run_id", flat=True) if r] + ([plan.inline_run_id] if plan.inline_run_id else [])
        chains_executor.cancel_runs(CallChainRun.objects.filter(pk__in=chain_run_ids, finished_at__isnull=True))
        plan.refresh_from_db()
        return Response(MigrationPlanSerializer(plan).data)

    @action(detail=True, methods=["post"], url_path="reorder")
    def reorder(self, request, pk=None):
        """Set the whole sequence at once — what dragging a step to a new place on the canvas sends.
        `order` is every step id of the plan, in the new order."""
        plan = self.get_object()
        if not plan.is_editable:
            return Response({"error": LOCK_MSG}, status=status.HTTP_400_BAD_REQUEST)
        try:
            ids = [int(i) for i in request.data.get("order", [])]
        except (TypeError, ValueError):
            ids = []
        if sorted(ids) != sorted(plan.steps.values_list("pk", flat=True)):
            return Response({"error": "order must list every step of the plan exactly once."}, status=status.HTTP_400_BAD_REQUEST)
        with transaction.atomic():
            # (plan, order) is unique: park everything above any real order, then number 1..n.
            top = plan.steps.aggregate(m=Max("order"))["m"] or 0
            PlanStep.objects.filter(plan=plan).update(order=F("order") + top + len(ids))
            for position, step_id in enumerate(ids, start=1):
                PlanStep.objects.filter(pk=step_id).update(order=position)
        plan.refresh_from_db()
        return Response(MigrationPlanSerializer(plan).data)

    @action(detail=True, methods=["post"], url_path="execute")
    def execute(self, request, pk=None):
        """Same validation/branching shape as MigrationRunViewSet.trigger in
        jobs/views.py — only difference is this starts the sequential plan
        executor instead of a single run. rate_limit_per_second only matters
        in simple mode (chain mode has no rate limiting), but there's no
        harm accepting/storing it either way."""
        plan = self.get_object()
        if not plan.steps.exists():
            return Response({"error": "Add at least one step before executing."}, status=status.HTTP_400_BAD_REQUEST)
        if plan.status not in (MigrationPlan.STATUS_DRAFT, MigrationPlan.STATUS_FAILED, MigrationPlan.STATUS_COMPLETED, MigrationPlan.STATUS_CANCELLED):
            return Response({"error": f"Plan is already {plan.status}."}, status=status.HTTP_400_BAD_REQUEST)
        plan.cancel_requested = False        # a kill from the previous execution must not stop this one

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
    queryset = PlanStep.objects.select_related("plan", "plan__inline_run", "mapping", "run", "chain", "chain_run", "inline_step", "inline_step__connection")
    serializer_class = PlanStepSerializer
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def update(self, request, *args, **kwargs):
        """PATCH edits this step's own rate_limit_per_second (mapping/order/
        plan/run are read-only on the serializer) — only while the plan is
        still a draft."""
        step = self.get_object()
        if not step.plan.is_editable:
            return Response({"error": LOCK_MSG}, status=status.HTTP_400_BAD_REQUEST)
        data = request.data
        if isinstance(data.get("function"), dict):
            if step.kind != "function":
                return Response({"error": "Only a function block has function settings."}, status=status.HTTP_400_BAD_REQUEST)
            earlier = [ps.inline_step for ps in step.plan.steps.filter(inline_step__isnull=False, order__lt=step.order).select_related("inline_step")]
            updates, error = step_forms.step_updates(step.inline_step, data["function"], earlier=earlier, allow_connection=True)
            if error:
                return Response({"error": error}, status=status.HTTP_400_BAD_REQUEST)
            for field, value in updates.items():
                setattr(step.inline_step, field, value)
            step.inline_step.save()
            return Response(PlanStepSerializer(step).data)
        if "wait_seconds" in data:
            if step.kind != "wait":
                return Response({"error": "Only a wait step has wait seconds."}, status=status.HTTP_400_BAD_REQUEST)
            seconds, error = _wait_seconds(data.get("wait_seconds"))
            if error:
                return Response({"error": error}, status=status.HTTP_400_BAD_REQUEST)
            data["wait_seconds"] = seconds
        if "entity_mapping_ids" in data:
            if step.kind != "mapping":
                return Response({"error": "Only a mapping step can run a subset of entity pairs."}, status=status.HTTP_400_BAD_REQUEST)
            pairs, error = _pair_ids(step.mapping, data.get("entity_mapping_ids"))
            if error:
                return Response({"error": error}, status=status.HTTP_400_BAD_REQUEST)
            data["entity_mapping_ids"] = pairs
        return super().update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        step = self.get_object()
        if not step.plan.is_editable:
            return Response({"error": LOCK_MSG}, status=status.HTTP_400_BAD_REQUEST)
        if step.inline_step_id:
            step.inline_step.delete()       # its PlanStep row cascades with it
            return Response(status=status.HTTP_204_NO_CONTENT)
        return super().destroy(request, *args, **kwargs)

    @action(detail=True, methods=["post"], url_path="move")
    def move(self, request, pk=None):
        step = self.get_object()
        if not step.plan.is_editable:
            return Response({"error": LOCK_MSG}, status=status.HTTP_400_BAD_REQUEST)

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

PAGE_SIZE_CHOICES = (10, 25, 50, 100)
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
        "mode_choices": MigrationPlan.MODE_CHOICES,
        "page_size": page_size,
        "page_size_choices": PAGE_SIZE_CHOICES,
    })


def plan_detail(request, pk):
    plan_mode = MigrationPlan.objects.filter(pk=pk).values_list("execution_mode", flat=True).first()
    if plan_mode == MigrationPlan.MODE_MIXED:
        # This page renders a plan as all-mappings or all-chains; a mixed plan only makes
        # sense on the Studio canvas, which draws each step by its own kind.
        return redirect(f"/studio/?open=plan:{pk}")

    plan = get_object_or_404(
        MigrationPlan.objects.prefetch_related(
            "steps__mapping__source_connection",
            "steps__mapping__source_connection__integration_install__integration",
            "steps__mapping__destination_connections__integration_install__integration",
            "steps__mapping__entity_mappings__source_entity",
            "steps__mapping__entity_mappings__target_entity",
            "steps__run",
            "steps__chain__connection",
            "steps__chain_run",
        ),
        pk=pk,
    )
    steps = list(plan.steps.all())
    for step in steps:
        if step.mapping_id:
            step.route = _route_info(step.mapping)

    return render(request, "plans/detail.html", {
        "plan": plan,
        "steps": steps,
        "mappings": Mapping.objects.order_by("name"),
        "chains": CallChain.objects.order_by("name"),
    })
