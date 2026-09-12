import re

from django.db import IntegrityError
from django.shortcuts import get_object_or_404, render
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from connections.models import Connection

from . import executor
from .models import CallChain, CallChainRun, CallChainStep, CallChainStepResult
from .serializers import CallChainRunSerializer, CallChainSerializer, CallChainStepSerializer

STEP_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")  # matches executor.VAR_RE's step-name segment exactly


def _parse_captures(raw):
    """Normalizes+validates the raw `captures` list from a request body into
    [{"name": str, "path": str}, ...] — raises ValueError with a user-facing
    message on anything malformed."""
    if raw in (None, ""):
        return []
    if not isinstance(raw, list):
        raise ValueError("captures must be a list of {name, path} objects.")
    cleaned = []
    seen = set()
    for entry in raw:
        if not isinstance(entry, dict):
            raise ValueError("Each capture must be an object with 'name' and 'path'.")
        name = (entry.get("name") or "").strip()
        path = (entry.get("path") or "").strip()
        if not name:
            raise ValueError("Every capture needs a variable name.")
        if not STEP_NAME_RE.match(name):
            raise ValueError(f"Capture name '{name}' must start with a letter or underscore, and contain only letters, digits, _ or -.")
        if name in seen:
            raise ValueError(f"Capture name '{name}' is used more than once on this step.")
        seen.add(name)
        cleaned.append({"name": name, "path": path})
    return cleaned


def _parse_async_fields(data, current_step=None):
    """Reads is_async + the async_* fields from a request body, defaulting
    to `current_step`'s existing values for anything not present (so a
    partial PATCH that only changes e.g. async_timeout_seconds doesn't need
    to resend everything else) or to sensible new-step defaults otherwise.
    Returns (fields_dict, error_message) — exactly one is None."""
    def _get(key, default):
        if key in data:
            return data[key]
        if current_step is not None:
            return getattr(current_step, key)
        return default

    is_async = bool(_get("is_async", False))
    fields = {
        "is_async": is_async,
        "async_poll_path": (_get("async_poll_path", "") or "").strip(),
        "async_poll_method": _get("async_poll_method", CallChainStep.METHOD_GET) or CallChainStep.METHOD_GET,
        "async_condition_path": (_get("async_condition_path", "") or "").strip(),
        "async_condition_value": _get("async_condition_value", "") or "",
        "async_result_path": (_get("async_result_path", "") or "").strip(),
    }

    if not is_async:
        # Off just leaves whatever's already configured unused — same
        # pattern as connections' use_custom_headers/params — so it doesn't
        # need to be valid while switched off.
        fields["async_interval_seconds"] = _get("async_interval_seconds", 2.0)
        fields["async_timeout_seconds"] = _get("async_timeout_seconds", 60.0)
        return fields, None

    if not fields["async_poll_path"]:
        return None, "Async steps need a poll path."
    if not fields["async_condition_path"]:
        return None, "Async steps need a condition path (e.g. status)."
    if fields["async_condition_value"] == "":
        return None, "Async steps need a condition value to wait for (e.g. completed)."

    try:
        interval = float(_get("async_interval_seconds", 2.0))
    except (TypeError, ValueError):
        return None, "async_interval_seconds must be a number."
    if interval < 0.5:
        return None, "async_interval_seconds must be at least 0.5 seconds."

    try:
        timeout = float(_get("async_timeout_seconds", 60.0))
    except (TypeError, ValueError):
        return None, "async_timeout_seconds must be a number."
    if timeout <= 0:
        return None, "async_timeout_seconds must be greater than zero."
    if timeout < interval:
        return None, "async_timeout_seconds must be at least as long as async_interval_seconds."

    fields["async_interval_seconds"] = interval
    fields["async_timeout_seconds"] = timeout
    return fields, None


def _validate_names(chain, step_name, captures, exclude_step_id=None):
    """A step's own name and every capture's name share one flat `context`
    namespace at execution time (chains/executor.py) — a collision would
    silently let one value clobber another mid-run, so every name in the
    chain (step names + capture names, this step included) must be unique.
    Returns an error message, or None if everything's fine."""
    other_steps = chain.steps.exclude(pk=exclude_step_id) if exclude_step_id else chain.steps.all()
    taken = set(other_steps.values_list("name", flat=True))
    for other in other_steps:
        for c in other.captures or []:
            if c.get("name"):
                taken.add(c["name"])

    if step_name in taken:
        return f"'{step_name}' is already used as a step or capture name in this chain."

    seen_here = set()
    for c in captures:
        name = c["name"]
        if name == step_name:
            return f"Capture name '{name}' can't be the same as this step's own name."
        if name in taken or name in seen_here:
            return f"'{name}' is already used as a step or capture name in this chain."
        seen_here.add(name)
    return None


class CallChainViewSet(viewsets.ModelViewSet):
    queryset = CallChain.objects.select_related("connection").prefetch_related("steps")
    serializer_class = CallChainSerializer

    @action(detail=True, methods=["post"], url_path="steps")
    def add_step(self, request, pk=None):
        chain = self.get_object()
        name = (request.data.get("name") or "").strip()
        path = (request.data.get("path") or "").strip()
        if not name:
            return Response({"error": "Name is required — later steps reference it as {{name.field}}."}, status=status.HTTP_400_BAD_REQUEST)
        if not STEP_NAME_RE.match(name):
            return Response({"error": "Name must start with a letter or underscore, and contain only letters, digits, _ or -."}, status=status.HTTP_400_BAD_REQUEST)
        if not path:
            return Response({"error": "Path is required."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            captures = _parse_captures(request.data.get("captures"))
        except ValueError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        name_error = _validate_names(chain, name, captures)
        if name_error:
            return Response({"error": name_error}, status=status.HTTP_400_BAD_REQUEST)

        async_fields, async_error = _parse_async_fields(request.data)
        if async_error:
            return Response({"error": async_error}, status=status.HTTP_400_BAD_REQUEST)

        next_order = (chain.steps.count() or 0) + 1
        try:
            step = CallChainStep.objects.create(
                chain=chain, order=next_order, name=name, path=path, captures=captures,
                method=request.data.get("method") or CallChainStep.METHOD_GET,
                body=request.data.get("body") or "",
                **async_fields,
            )
        except IntegrityError:
            return Response({"error": f"A step named '{name}' already exists in this chain."}, status=status.HTTP_400_BAD_REQUEST)
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


class CallChainStepViewSet(viewsets.ModelViewSet):
    queryset = CallChainStep.objects.select_related("chain")
    serializer_class = CallChainStepSerializer

    def update(self, request, *args, **kwargs):
        step = self.get_object()
        name = request.data.get("name", step.name)
        name = (name or "").strip()
        if not name:
            return Response({"error": "Name is required."}, status=status.HTTP_400_BAD_REQUEST)
        if not STEP_NAME_RE.match(name):
            return Response({"error": "Name must start with a letter or underscore, and contain only letters, digits, _ or -."}, status=status.HTTP_400_BAD_REQUEST)

        if "captures" in request.data:
            try:
                captures = _parse_captures(request.data.get("captures"))
            except ValueError as exc:
                return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
            request.data["captures"] = captures
        else:
            captures = step.captures or []

        name_error = _validate_names(step.chain, name, captures, exclude_step_id=step.pk)
        if name_error:
            return Response({"error": name_error}, status=status.HTTP_400_BAD_REQUEST)

        async_fields, async_error = _parse_async_fields(request.data, current_step=step)
        if async_error:
            return Response({"error": async_error}, status=status.HTTP_400_BAD_REQUEST)
        request.data.update(async_fields)

        try:
            return super().update(request, *args, **kwargs)
        except IntegrityError:
            return Response({"error": f"A step named '{name}' already exists in this chain."}, status=status.HTTP_400_BAD_REQUEST)

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
    chains = CallChain.objects.select_related("connection").prefetch_related("steps")
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
    for step in steps:
        result = step_results.get(step.name)
        if result:
            if result.error:
                node_status = "error"
            elif result.status_code and result.status_code < 400:
                node_status = "success"
            else:
                node_status = "error"
        else:
            node_status = "pending"
        nodes.append({
            "step": step,
            "result": result,
            "node_status": node_status,
        })
    return render(request, "chains/run_detail.html", {
        "chain": chain,
        "run": run,
        "nodes": nodes,
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
