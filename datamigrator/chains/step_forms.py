"""Validation + normalisation of a chain step's settings, shared by the chain API and by a plan's
function blocks (plans/views.py), which are chain steps living in the plan's hidden inline chain.

`earlier` is always "the steps that run before this one" — a list of CallChainStep-like objects — so
the same rules serve both places. Everything here returns (value, error) / raises ValueError with a
message meant to be shown to the user as-is.
"""
import re

from connections.models import Connection
from schemas.models import Entity

from . import executor
from .models import CallChainStep

STEP_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")  # matches executor.VAR_RE's step-name segment exactly
HEADER_NAME_RE = re.compile(r"^[A-Za-z0-9!#$%&'*+.^_`|~-]+$")   # RFC 9110 token
MAX_PAIRS = 30


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


FIND_OPERATORS = ("equals", "not_equals", "contains", "starts_with", "matches", "gt", "lt")
CHECK_OPERATORS = ("exists", "missing", "equals", "not_equals", "contains", "starts_with", "matches")
FILE_ROW_LIMIT = executor.MAX_FILE_ROWS


def _number(raw, what, low, high):
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise ValueError(f"{what} must be a number.")
    if not low <= value <= high:
        raise ValueError(f"{what} must be between {low:g} and {high:g}.")
    return value


def _upstream_http_step(earlier, name, what, get_only=False):
    """The earlier HTTP call a next-page / check-header step reads from. `earlier` is the steps that
    run before this one — for a chain that's the lower-ordered steps, for a plan's function block it's
    the function blocks earlier in the *plan* — so this module doesn't need to know which."""
    name = (name or "").strip()
    if not name:
        raise ValueError(f"{what}: pick the step to read from.")
    source = next((x for x in earlier if x.name == name), None)
    if source is None:
        raise ValueError(f"{what}: '{name}' isn't an earlier step in this chain.")
    if source.kind != CallChainStep.KIND_HTTP:
        raise ValueError(f"{what}: '{name}' isn't an HTTP call.")
    if get_only and source.method != CallChainStep.METHOD_GET:
        raise ValueError(f"{what}: '{name}' is a {source.method} — paging repeats a GET request.")
    return source


def parse_kind_params(kind, raw, earlier):
    """Validate + normalise the settings of a non-HTTP step. Returns (params, error) —
    exactly one is None. `raw` is the merged params dict (existing values + the request's)."""
    raw = raw if isinstance(raw, dict) else {}
    text = lambda key: str(raw.get(key) or "").strip()
    try:
        if kind == CallChainStep.KIND_HTTP:
            return {}, None
        if kind == CallChainStep.KIND_WAIT:
            return {"seconds": _number(raw.get("seconds", 1), "Wait seconds", 0, executor.MAX_WAIT_SECONDS)}, None

        if kind == CallChainStep.KIND_NEXT:
            _upstream_http_step(earlier, text("from_step"), "Next page", get_only=True)
            if not text("next_path"):
                raise ValueError("Next page: say where the next link/cursor is in the response (e.g. links.next).")
            return {
                "from_step": text("from_step"), "next_path": text("next_path"), "items_path": text("items_path"),
                "cursor_param": text("cursor_param"),
                "max_pages": int(_number(raw.get("max_pages", 5), "Max pages", 1, executor.MAX_PAGES_CAP)),
            }, None

        if kind == CallChainStep.KIND_FIND:
            source = text("source").strip("{} ")
            if not source:
                raise ValueError("Find in list: say which list to search (e.g. list_items.items).")
            operator = text("operator") or "equals"
            if operator not in FIND_OPERATORS:
                raise ValueError(f"Find in list: operator must be one of {', '.join(FIND_OPERATORS)}.")
            if operator == "matches":
                try:
                    re.compile(text("value"))
                except re.error:
                    raise ValueError("Find in list: the value isn't a valid regular expression.")
            pick = text("pick") or "first"
            on_missing = text("on_missing") or "fail"
            if pick not in ("first", "all") or on_missing not in ("fail", "null"):
                raise ValueError("Find in list: pick must be first/all and on_missing fail/null.")
            return {"source": source, "match_field": text("match_field"), "operator": operator,
                    "value": str(raw.get("value") or ""), "pick": pick, "on_missing": on_missing}, None

        if kind == CallChainStep.KIND_CHECK:
            _upstream_http_step(earlier, text("from_step"), "Check header")
            if not text("header"):
                raise ValueError("Check header: enter the header name (e.g. Content-Type).")
            operator = text("operator") or "exists"
            if operator not in CHECK_OPERATORS:
                raise ValueError(f"Check header: operator must be one of {', '.join(CHECK_OPERATORS)}.")
            if operator == "matches":
                try:
                    re.compile(text("value"))
                except re.error:
                    raise ValueError("Check header: the value isn't a valid regular expression.")
            on_fail = text("on_fail") or "fail"
            if on_fail not in ("fail", "warn"):
                raise ValueError("Check header: on_fail must be fail or warn.")
            return {"from_step": text("from_step"), "header": text("header"), "operator": operator,
                    "value": str(raw.get("value") or ""), "on_fail": on_fail}, None

        if kind == CallChainStep.KIND_FILE:
            entity = Entity.objects.filter(pk=raw.get("entity")).first() if str(raw.get("entity") or "").isdigit() else None
            if entity is None or not entity.source_file:
                raise ValueError("File preview: choose an entity that has an uploaded CSV/XLSX file.")
            return {"entity": entity.pk, "limit": int(_number(raw.get("limit", 10), "Rows to preview", 1, FILE_ROW_LIMIT))}, None
    except ValueError as exc:
        return None, str(exc)
    return None, f"Unknown step kind '{kind}'."


def _parse_timeout(data, current=None):
    """HTTP steps' optional per-request timeout. Returns (value_or_None, error)."""
    raw = data.get("timeout_seconds", getattr(current, "timeout_seconds", None) if current else None)
    if raw in (None, ""):
        return None, None
    try:
        return _number(raw, "Timeout", 0.5, 600), None
    except ValueError as exc:
        return None, str(exc)


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


def _parse_pairs(raw, what, header=False):
    """A request step's headers / query params: [{name, value}, …] → cleaned list. Blank rows are dropped;
    a header name must be a valid HTTP token; names are unique (case-insensitively for headers)."""
    if raw in (None, ""):
        return []
    if not isinstance(raw, list):
        raise ValueError(f"{what}s must be a list of {{name, value}} pairs.")
    out, seen = [], set()
    for entry in raw:
        if not isinstance(entry, dict):
            raise ValueError(f"Each {what.lower()} needs a name and a value.")
        name = str(entry.get("name") or "").strip()
        value = "" if entry.get("value") is None else str(entry.get("value"))
        if not name:
            if value.strip():
                raise ValueError(f"A {what.lower()} has a value but no name.")
            continue
        if header and not HEADER_NAME_RE.match(name):
            raise ValueError(f"'{name}' isn't a valid header name (letters, digits and - _ . only).")
        key = name.lower() if header else name
        if key in seen:
            raise ValueError(f"{what} '{name}' is set more than once.")
        seen.add(key)
        out.append({"name": name, "value": value})
    if len(out) > MAX_PAIRS:
        raise ValueError(f"At most {MAX_PAIRS} {what.lower()}s per step.")
    return out


def new_step_fields(chain, data, *, earlier, order, allow_connection=False):
    """Validate a brand-new step's request data. Returns (model_kwargs, error): exactly one is None."""
    kind = data.get("kind") or CallChainStep.KIND_HTTP
    if kind not in dict(CallChainStep.KIND_CHOICES):
        return None, f"Unknown step kind '{kind}'."
    is_http = kind == CallChainStep.KIND_HTTP
    name = (data.get("name") or "").strip()
    path = (data.get("path") or "").strip()
    if not name:
        return None, "Name is required — later steps reference it as {{name.field}}."
    if not STEP_NAME_RE.match(name):
        return None, "Name must start with a letter or underscore, and contain only letters, digits, _ or -."
    if is_http and not path:
        return None, "Path is required."

    params, error = parse_kind_params(kind, data.get("params"), earlier)
    if error:
        return None, error
    try:
        captures = _parse_captures(data.get("captures")) if kind != CallChainStep.KIND_WAIT else []
    except ValueError as exc:
        return None, str(exc)
    error = _validate_names(chain, name, captures)
    if error:
        return None, error

    async_fields, timeout, connection, headers, query = {}, None, None, [], []
    if is_http:
        try:
            headers = _parse_pairs(data.get("headers"), "Header", header=True)
            query = _parse_pairs(data.get("query_params"), "Query parameter")
        except ValueError as exc:
            return None, str(exc)
        async_fields, error = _parse_async_fields(data)
        if error:
            return None, error
        timeout, error = _parse_timeout(data)
        if error:
            return None, error
        if allow_connection:
            connection, error = _parse_connection(data.get("connection"), required=True)
            if error:
                return None, error
    return {
        "chain": chain, "order": order, "name": name, "kind": kind, "params": params, "timeout_seconds": timeout,
        "connection": connection, "headers": headers, "query_params": query, "path": path if is_http else "", "captures": captures,
        "method": (data.get("method") or CallChainStep.METHOD_GET) if is_http else CallChainStep.METHOD_GET,
        "body": (data.get("body") or "") if is_http else "", **async_fields,
    }, None


def step_updates(step, data, *, earlier, allow_connection=False):
    """Validate an edit to an existing step. Returns ({field: value} to apply, error). Only the keys
    present in `data` change; settings that depend on each other (params, async) are re-validated whole."""
    if data.get("kind", step.kind) != step.kind:
        return None, "A step's kind can't be changed — remove it and add a new one."
    is_http = step.kind == CallChainStep.KIND_HTTP
    updates = {}
    if is_http and "path" in data and not str(data.get("path") or "").strip():
        return None, "Path is required."
    name = (data.get("name", step.name) or "").strip()
    if not name:
        return None, "Name is required."
    if not STEP_NAME_RE.match(name):
        return None, "Name must start with a letter or underscore, and contain only letters, digits, _ or -."
    updates["name"] = name

    if "captures" in data:
        try:
            captures = _parse_captures(data.get("captures"))
        except ValueError as exc:
            return None, str(exc)
        updates["captures"] = captures
    else:
        captures = step.captures or []
    error = _validate_names(step.chain, name, captures, exclude_step_id=step.pk)
    if error:
        return None, error

    if is_http:
        for key in ("method", "path", "body"):
            if key in data:
                updates[key] = data[key] if key == "method" else str(data[key] or "").strip() if key == "path" else str(data[key] or "")
        async_fields, error = _parse_async_fields(data, current_step=step)
        if error:
            return None, error
        updates.update(async_fields)
        timeout, error = _parse_timeout(data, current=step)
        if error:
            return None, error
        updates["timeout_seconds"] = timeout
        try:
            if "headers" in data:
                updates["headers"] = _parse_pairs(data.get("headers"), "Header", header=True)
            if "query_params" in data:
                updates["query_params"] = _parse_pairs(data.get("query_params"), "Query parameter")
        except ValueError as exc:
            return None, str(exc)
        if allow_connection and "connection" in data:
            updates["connection"], error = _parse_connection(data.get("connection"), required=True)
            if error:
                return None, error
    else:
        merged = {**(step.params or {}), **(data.get("params") or {})}
        params, error = parse_kind_params(step.kind, merged, earlier)
        if error:
            return None, error
        updates["params"] = params
    return updates, None


def _parse_connection(raw, required=False):
    """A request step of a plan's function block names the connection it calls."""
    if raw in (None, ""):
        return None, ("Choose the connection this request calls." if required else None)
    connection = Connection.objects.filter(pk=raw).first() if str(raw).isdigit() else None
    return (connection, None) if connection else (None, "That connection doesn't exist.")
