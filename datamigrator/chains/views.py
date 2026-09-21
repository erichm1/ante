from django.db import IntegrityError
from django.db.models import Max
from django.shortcuts import get_object_or_404, render
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from connections.models import Connection

from . import executor, step_forms
from .models import CallChain, CallChainRun, CallChainStep, CallChainStepResult
from .serializers import CallChainRunSerializer, CallChainSerializer, CallChainStepSerializer



class CallChainViewSet(viewsets.ModelViewSet):
    # A plan's hidden inline chain (its custom-run function blocks) is not a chain of its own.
    queryset = CallChain.objects.filter(hidden=False).select_related("connection").prefetch_related("steps")
    serializer_class = CallChainSerializer

    @action(detail=True, methods=["post"], url_path="steps")
    def add_step(self, request, pk=None):
        chain = self.get_object()
        next_order = (chain.steps.aggregate(m=Max("order"))["m"] or 0) + 1      # max+1, not count+1: a deleted middle step leaves a gap
        fields, error = step_forms.new_step_fields(
            chain, request.data, earlier=list(chain.steps.filter(order__lt=next_order)), order=next_order)
        if error:
            return Response({"error": error}, status=status.HTTP_400_BAD_REQUEST)
        try:
            step = CallChainStep.objects.create(**fields)
        except IntegrityError:
            return Response({"error": f"A step named '{fields['name']}' already exists in this chain."}, status=status.HTTP_400_BAD_REQUEST)
        return Response(CallChainStepSerializer(step).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="execute")
    def execute(self, request, pk=None):
        chain = self.get_object()
        if not chain.steps.exists():
            return Response({"error": "Add at least one step before running this chain."}, status=status.HTTP_400_BAD_REQUEST)

        chain_run = CallChainRun.objects.create(chain=chain, status=CallChainRun.STATUS_FAILED)
        executor.run_chain(chain_run)  # synchronous — a chain is a handful of one-off calls, not a bulk loop
        return Response(
            CallChainRunSerializer(chain_run, context={"request": request}).data,
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["post"], url_path="cancel")
    def cancel(self, request, pk=None):
        """The Kill button. A chain executes inside the request that started it, so the UI has no run id to
        aim at — this stops whatever run of the chain is still unfinished. The runner notices between steps,
        while waiting and while polling (a request already on the wire finishes or times out first)."""
        chain = self.get_object()
        return Response({"cancelled": executor.cancel_runs(CallChainRun.objects.filter(chain=chain, finished_at__isnull=True))})

    @action(detail=True, methods=["get"], url_path="runs")
    def list_runs(self, request, pk=None):
        """Newest-first run history with every step's result — CallChainRun has
        no viewset of its own, and the Studio's results panel needs this list."""
        chain = self.get_object()
        runs = chain.runs.prefetch_related("step_results")[:20]
        return Response(CallChainRunSerializer(runs, many=True, context={"request": request}).data)

    @action(detail=True, methods=["post"], url_path=r"retry-run/(?P<run_pk>[^/.]+)")
    def retry_run(self, request, pk=None, run_pk=None):
        """Re-runs a failed chain run starting from the first failed step,
        pre-loading the context captured by every successful step before it.
        Only steps that errored are re-executed — side-effects from prior
        successful steps (created records, sent webhooks) are not repeated."""
        chain = self.get_object()
        prior_run = get_object_or_404(CallChainRun, pk=run_pk, chain=chain)
        if prior_run.status not in (CallChainRun.STATUS_FAILED, CallChainRun.STATUS_CANCELLED):
            return Response({"error": "Only failed or cancelled runs can be retried."}, status=status.HTTP_400_BAD_REQUEST)

        prior_context, prior_paths, prior_headers = {}, {}, {}
        start_order = 0
        for result in prior_run.step_results.order_by("order"):
            if result.error:
                start_order = result.order
                break
            if result.response_json is not None:
                prior_context[result.name] = result.response_json
            if result.captured_variables:
                prior_context.update(result.captured_variables)
            if result.kind == CallChainStep.KIND_HTTP:      # what next-page / check-header steps read
                prior_paths[result.name] = result.resolved_path
                prior_headers[result.name] = result.response_headers
            start_order = result.order + 1          # a run cancelled *between* steps has no failed one: resume after the last done

        new_run = CallChainRun.objects.create(chain=chain, status=CallChainRun.STATUS_FAILED)
        executor.run_chain_retry(new_run, prior_context, start_order, prior_paths, prior_headers)
        return Response(
            CallChainRunSerializer(new_run, context={"request": request}).data,
            status=status.HTTP_200_OK,
        )


class CallChainStepViewSet(viewsets.ModelViewSet):
    queryset = CallChainStep.objects.select_related("chain")
    serializer_class = CallChainStepSerializer

    def update(self, request, *args, **kwargs):
        step = self.get_object()
        updates, error = step_forms.step_updates(step, request.data, earlier=list(step.chain.steps.filter(order__lt=step.order)))
        if error:
            return Response({"error": error}, status=status.HTTP_400_BAD_REQUEST)
        for field, value in updates.items():
            setattr(step, field, value)
        try:
            step.save()
        except IntegrityError:
            return Response({"error": f"A step named '{updates['name']}' already exists in this chain."}, status=status.HTTP_400_BAD_REQUEST)
        return Response(CallChainStepSerializer(step).data)

    @action(detail=True, methods=["post"], url_path="move")
    def move(self, request, pk=None):
        step = self.get_object()
        direction = request.data.get("direction")
        neighbor_qs = CallChainStep.objects.filter(chain=step.chain)
        neighbor = (
            neighbor_qs.filter(order__lt=step.order).order_by("-order").first() if direction == "up"
            else neighbor_qs.filter(order__gt=step.order).order_by("order").first() if direction == "down"
            else None
        )
        if not neighbor:
            return Response({"error": "Nothing to swap with in that direction."}, status=status.HTTP_400_BAD_REQUEST)

        # Swap through a temporary value first — (chain, order) is unique, so writing
        # either row straight to the other's current order would collide mid-transaction.
        # order is a PositiveIntegerField (DB-level CHECK order >= 0), so the temp value
        # must stay non-negative — TEMP_ORDER is just something no real step count reaches.
        # Capture both original values before mutating anything: reassigning step.order /
        # neighbor.order first (then reading them back for the DB writes) swaps them back
        # to their starting values by the time the third update runs, silently no-opping.
        TEMP_ORDER = 10**9
        step_order, neighbor_order = step.order, neighbor.order
        CallChainStep.objects.filter(pk=step.pk).update(order=TEMP_ORDER)
        CallChainStep.objects.filter(pk=neighbor.pk).update(order=step_order)
        CallChainStep.objects.filter(pk=step.pk).update(order=neighbor_order)
        step.order, neighbor.order = neighbor_order, step_order
        return Response(CallChainStepSerializer(step).data)


# ---- Page views -----------------------------------------------------------

def chain_list(request):
    chains = CallChain.objects.filter(hidden=False).select_related("connection").prefetch_related("steps")
    return render(request, "chains/list.html", {
        "chains": chains, "connections": Connection.objects.order_by("name"),
    })


def chain_run_detail(request, chain_pk, run_pk):
    chain = get_object_or_404(CallChain.objects.select_related("connection"), pk=chain_pk)
    run = get_object_or_404(
        CallChainRun.objects.prefetch_related("step_results"),
        pk=run_pk, chain=chain,
    )
    steps = list(chain.steps.all())
    step_results = {r.name: r for r in run.step_results.all()}
    nodes = []
    nodes_data = []
    for step in steps:
        result = step_results.get(step.name)
        if result:
            # Only an HTTP call has a status code; the other kinds succeed or fail on `error` alone.
            http_bad = result.kind == CallChainStep.KIND_HTTP and (not result.status_code or result.status_code >= 400)
            node_status = "error" if (result.error or http_bad) else "success"
        else:
            node_status = "pending"
        nodes.append({"step": step, "result": result, "node_status": node_status})
        nodes_data.append({
            "order":              step.order,
            "name":               step.name,
            "method":             step.method if step.kind == CallChainStep.KIND_HTTP else step.kind.upper(),
            "path":               step.path or step.get_kind_display(),
            "is_async":           step.is_async,
            "node_status":        node_status,
            "status_code":        result.status_code if result else None,
            "resolved_path":      result.resolved_path if result else "",
            "resolved_body":      result.resolved_body if result else None,
            "response_json":      result.response_json if result else None,
            "captured_variables": result.captured_variables if result else {},
            "poll_attempts":      result.poll_attempts if result else None,
            "error":              result.error if result else None,
        })
    return render(request, "chains/run_detail.html", {
        "chain":      chain,
        "run":        run,
        "nodes":      nodes,
        "nodes_data": nodes_data,
    })


def chain_detail(request, pk):
    chain = get_object_or_404(CallChain.objects.select_related("connection").prefetch_related("steps"), pk=pk)
    last_run = chain.runs.prefetch_related("step_results").first()
    captured_variables = []  # [(name, value, source_step_name), ...] — names are unique chain-wide (see _validate_names)
    if last_run:
        for r in last_run.step_results.all():
            for key, value in (r.captured_variables or {}).items():
                captured_variables.append((key, value, r.name))
    steps = list(chain.steps.all())
    return render(request, "chains/detail.html", {
        "chain": chain,
        "connections": Connection.objects.order_by("name"),
        "steps": steps,
        "steps_json": CallChainStepSerializer(steps, many=True).data,
        "last_run": last_run,
        "captured_variables": captured_variables,
        "runs": chain.runs.all()[:10],
        "method_choices": CallChainStep.METHOD_CHOICES,
    })
