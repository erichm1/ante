"""Runs a CallChain's steps in order, in the calling thread (synchronous —
a chain is a handful of one-off calls, not a bulk record loop like
jobs/engine.py, so there's no need for a background thread here). A step
marked `is_async` blocks this same thread while it polls (see
run_async_step) — bounded by its own async_timeout_seconds, same trade-off
as the rest of this module's "good enough for a scaffold" scope.

Each step's `path`/`body` can reference a prior step's parsed JSON response
via {{step_name.some.json.path}} — resolved against a running `context`
dict, keyed by step name (the whole parsed response) *and* by any variable
names the step explicitly `captures` out of it (see apply_captures below) —
so a later step can use the short {{customer_id}} instead of needing to
know {{create_customer.id}}'s full shape. A `*` path segment (e.g.
itens.*.id) captures/looks up every matching value as a list instead of
just one — see _walk. `context` (both forms together) is what gets written
to CallChainRun.result_file once the chain finishes, so it can be read
back independently of the run's own DB rows.

Besides HTTP calls a step can be another *kind* (CallChainStep.kind): wait,
next page, find in list, check header, file preview. They all read/write the
same context, so e.g. a file preview's rows can be searched by a find step
whose match is captured for the next HTTP call. Every kind is implemented once,
in _Runner, which both run_chain and run_chain_retry drive.
"""
import json
import re
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from django.core.files.base import ContentFile
from django.utils import timezone

from connections.client import ConnectionClient
from schemas import discovery
from schemas.models import Entity

from .models import CallChainRun, CallChainStep, CallChainStepResult

VAR_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_-]*(?:\.[A-Za-z0-9_*-]+)*)\s*\}\}")

# Defense-in-depth alongside async_timeout_seconds — bounds the loop even if
# async_interval_seconds is misconfigured to something tiny.
MAX_POLL_ITERATIONS = 500


MAX_WAIT_SECONDS = 300          # a chain runs inside the web request — don't let one step hold it for an hour
MAX_PAGES_CAP = 100
MAX_FILE_ROWS = 100


class TemplateResolutionError(Exception):
    pass


class StepFailed(Exception):
    """A step ran fine but its own condition wasn't met (no match found, header check failed…)."""


class Cancelled(Exception):
    """The Kill button was pressed: unwind quickly, and record the run as cancelled rather than failed."""


SLICE_SECONDS = 0.5          # long sleeps (wait steps, poll intervals) are cut into slices this long so a kill lands promptly

# Ids of chain runs THIS process is executing right now. A run that is unfinished but not in here was
# orphaned (its thread/request died, e.g. a dev-server restart) and nothing will ever notice a cancel flag
# on it — cancel_runs() closes those on the spot. (Scaffold scope: one process; see README.)
ACTIVE_RUNS = set()


def cancel_runs(runs) -> int:
    """Flag every run in `runs` (a queryset of unfinished CallChainRun) to stop; returns how many.
    Live ones stop themselves at the next check; orphaned ones are closed immediately."""
    count = 0
    for run in runs:
        run.cancel_requested = True
        fields = ["cancel_requested"]
        if run.pk not in ACTIVE_RUNS:
            run.status, run.finished_at = CallChainRun.STATUS_CANCELLED, timezone.now()
            fields += ["status", "finished_at"]
        run.save(update_fields=fields)
        if "status" in fields:                       # an orphaned run (its process died) closed by the Kill
            from notifications.services import notify_chain_run
            notify_chain_run(run, "killed")
        count += 1
    return count


def _walk(value, parts):
    """Dotted-path walk shared by _lookup and _extract: each plain segment
    indexes a dict key or a numeric (optionally negative) list index, same
    as a single-value lookup always worked. A bare `*` segment instead means
    "every item in this list" — it fans the rest of the path out across
    each item and returns a list of results instead of one value, e.g.
    path 'itens.*.id' against {"itens": [{"id": 1}, {"id": 2}]} returns
    [1, 2] (a `*` against a non-list, or past the end of a path, yields
    None/the list itself same as any other unresolved/terminal segment)."""
    if not parts:
        return value
    part, rest = parts[0], parts[1:]
    if part == "*":
        if not isinstance(value, list):
            return None
        return [_walk(item, rest) for item in value]
    if isinstance(value, dict):
        value = value.get(part)
    elif isinstance(value, list) and part.lstrip("-").isdigit():
        idx = int(part)
        value = value[idx] if -len(value) <= idx < len(value) else None
    else:
        value = None
    return _walk(value, rest)


def _lookup(dotted_path: str, context: dict):
    parts = dotted_path.split(".")
    if parts[0] not in context:
        raise TemplateResolutionError(
            f"{{{{{dotted_path}}}}} references step '{parts[0]}', which hasn't run yet (or doesn't exist)."
        )
    return _walk(context[parts[0]], parts[1:])


def _timeout_kwargs(timeout):
    return {} if timeout is None else {"timeout": timeout}


def _request_kwargs(timeout=None, headers=None, params=None):
    """Optional requests kwargs, only the ones actually set (so a plain call is byte-for-byte what it was)."""
    kwargs = _timeout_kwargs(timeout)
    if headers:
        kwargs["headers"] = headers
    if params:
        kwargs["params"] = params
    return kwargs


SENSITIVE_HEADER_PARTS = ("authorization", "cookie", "token", "secret", "key", "password", "signature")


def mask_headers(headers: dict) -> dict:
    """Header values are stored on the step result so a run can be inspected afterwards — but never a secret."""
    return {k: ("••••••" if any(p in k.lower() for p in SENSITIVE_HEADER_PARTS) else v) for k, v in headers.items()}


def _extract(value, dotted_path: str):
    """Same dotted dict/list/`*`-wildcard walk as _lookup (see _walk), but
    starting from an already-known value (a step's own parsed response)
    rather than looking a name up in the context — this is what turns a
    capture's `path` into the value stored under its `name`. A `*` segment
    (e.g. 'itens.*.id') captures every matching value as a list instead of
    just one — e.g. every product id in a list response, to use later."""
    if not dotted_path or not dotted_path.strip():
        return value
    return _walk(value, dotted_path.split("."))


def _extract_with_found(value, dotted_path: str):
    """Same walk as _extract, but also reports whether the path actually
    resolved — a missing status field should read as "not done yet", not
    accidentally match against a blank expected value."""
    if not dotted_path or not dotted_path.strip():
        return value, True
    for part in dotted_path.split("."):
        if isinstance(value, dict):
            if part not in value:
                return None, False
            value = value[part]
        elif isinstance(value, list) and part.lstrip("-").isdigit():
            idx = int(part)
            if not (-len(value) <= idx < len(value)):
                return None, False
            value = value[idx]
        else:
            return None, False
    return value, True


def _condition_met(poll_parsed, dotted_path: str, expected_value: str) -> bool:
    value, found = _extract_with_found(poll_parsed, dotted_path)
    if not found:
        return False
    return str(value).strip().lower() == str(expected_value).strip().lower()


def run_async_step(client: ConnectionClient, step, context: dict, timeout=None, sleep=time.sleep, headers=None):
    """Polls step.async_poll_path until step.async_condition_path in the
    poll response case-insensitively equals step.async_condition_value, then
    optionally makes one more call to step.async_result_path — see
    CallChainStep.is_async's help_text for the whole pattern. Returns
    (final_parsed_response, poll_attempts). Raises TimeoutError if
    async_timeout_seconds elapses (or MAX_POLL_ITERATIONS is hit) first."""
    deadline = time.monotonic() + step.async_timeout_seconds
    attempts = 0
    poll_parsed = None

    while True:
        attempts += 1
        poll_path = resolve_path(step.async_poll_path, context)
        poll_response = client.request(step.async_poll_method, poll_path, **_request_kwargs(timeout, headers))
        poll_response.raise_for_status()
        try:
            poll_parsed = poll_response.json() if poll_response.content else None
        except ValueError:
            poll_parsed = None
        # context[step.name] deliberately stays the *submit* response for the
        # whole loop — async_poll_path (e.g. /exports/{{this_name.job_id}}/
        # status) needs that same job_id on every iteration. Overwriting it
        # with each poll's own response here previously clobbered job_id
        # after the first poll (a real bug: the second poll onward silently
        # resolved to an empty string and 404'd). The caller sets
        # context[step.name] to this function's return value once, after
        # polling finishes.

        if _condition_met(poll_parsed, step.async_condition_path, step.async_condition_value):
            break
        if time.monotonic() >= deadline or attempts >= MAX_POLL_ITERATIONS:
            raise TimeoutError(
                f"'{step.name}' didn't reach {step.async_condition_path!r} == "
                f"{step.async_condition_value!r} within {step.async_timeout_seconds}s ({attempts} poll(s))."
            )
        sleep(step.async_interval_seconds)

    if not step.async_result_path:
        return poll_parsed, attempts

    result_path = resolve_path(step.async_result_path, context)
    result_response = client.request("GET", result_path, **_request_kwargs(timeout, headers))
    result_response.raise_for_status()
    try:
        final_parsed = result_response.json() if result_response.content else None
    except ValueError:
        final_parsed = None
    return final_parsed, attempts


def apply_captures(step, parsed_response, context: dict) -> dict:
    """Runs a step's `captures` ([{"name": ..., "path": ...}, ...]) against
    its just-parsed response, writing each extracted value into `context`
    under its own short name — so a later step can use {{customer_id}}
    instead of needing to know {{create_customer.id}}'s full shape. Returns
    just this step's captured {name: value} for CallChainStepResult."""
    captured = {}
    for capture in step.captures or []:
        name = (capture or {}).get("name")
        if not name:
            continue
        value = _extract(parsed_response, (capture or {}).get("path", ""))
        context[name] = value
        captured[name] = value
    return captured


def resolve_path(template: str, context: dict) -> str:
    """Plain string substitution — {{create_customer.id}} in a URL path
    becomes the literal value, not a quoted JSON string."""
    def repl(match):
        value = _lookup(match.group(1), context)
        return "" if value is None else str(value)
    return VAR_RE.sub(repl, template or "")


QUOTED_VAR_RE = re.compile(r'"\{\{\s*([A-Za-z_][A-Za-z0-9_-]*(?:\.[A-Za-z0-9_-]+)*)\s*\}\}"')


def resolve_body(template: str, context: dict):
    """Placeholders are substituted as JSON literals, so both writing styles
    produce valid JSON either way:
      - bare, e.g. {"customer_id": {{create_customer.id}}} — the resolved
        value's own JSON encoding is spliced in as-is (a number stays a
        number, a string comes out quoted, an object/list stays nested).
      - pre-quoted, e.g. {"customer_name": "{{create_customer.name}}"} —
        treated as "insert this value's text here", so it doesn't come out
        double-quoted; resolves to the same {"customer_name": "Acme"}
        either way.
    The whole thing is then parsed back into a dict/list."""
    if not template or not template.strip():
        return None

    def quoted_repl(match):
        value = _lookup(match.group(1), context)
        return json.dumps("" if value is None else str(value))

    def bare_repl(match):
        return json.dumps(_lookup(match.group(1), context))

    resolved = QUOTED_VAR_RE.sub(quoted_repl, template)
    resolved = VAR_RE.sub(bare_repl, resolved)
    try:
        return json.loads(resolved)
    except json.JSONDecodeError as exc:
        raise TemplateResolutionError(f"Body isn't valid JSON after resolving placeholders: {exc}")


def _as_list(value):
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _compare(actual, operator: str, expected: str) -> bool:
    """Shared by find (an item's field) and check (a header). Text comparisons are on
    the string form, so 1 and "1" match; contains/starts_with ignore case."""
    if operator == "exists":
        return actual is not None
    if operator == "missing":
        return actual is None
    if actual is None:
        return operator == "not_equals"
    text = str(actual)
    if operator == "equals":
        return text == expected
    if operator == "not_equals":
        return text != expected
    if operator == "contains":
        return expected.lower() in text.lower()
    if operator == "starts_with":
        return text.lower().startswith(expected.lower())
    if operator == "matches":
        return re.search(expected, text) is not None
    if operator in ("gt", "lt"):
        try:
            a, b = float(text), float(expected)
        except ValueError:
            return False
        return a > b if operator == "gt" else a < b
    raise ValueError(f"Unknown operator '{operator}'.")


def _file_columns(entity, rows):
    """A file's columns in the file's own order (not the entity's alphabetical field list);
    the declared fields only stand in when there are no rows to read them from."""
    columns = []
    for row in rows:
        columns += [k for k in row if k not in columns]
    return columns or [f.name for f in entity.fields.all()]


def _json_safe(value):
    """File cells can be datetimes/decimals (xlsx) — round-trip through JSON so the result row saves."""
    return json.loads(json.dumps(value, default=str))


class _Runner:
    """Executes a chain's steps against one running context. `paths` and `headers` are
    per-step side tables (the resolved path and response headers of each HTTP step) that a
    next-page or check-header step needs but that don't belong in the JSON `context`.

    Used two ways: a chain runs all its steps (run()); a plan's custom run calls execute_step()
    once per function block, in plan order, on ONE runner — that shared context is what lets a
    find-in-list block search what an earlier request returned. `should_cancel` lets the owner
    decide what "killed" means (a plan checks its own flag; a chain checks its run's)."""

    def __init__(self, chain_run, context=None, paths=None, headers=None, should_cancel=None):
        self.chain_run = chain_run
        self.chain = chain_run.chain
        self._clients = {}
        self.context = dict(context or {})
        self.paths = dict(paths or {})
        self.headers = dict(headers or {})
        self.should_cancel = should_cancel or self._run_flag
        ACTIVE_RUNS.add(chain_run.pk)

    # ── connections ──────────────────────────────────────────────────────
    def connection_of(self, step):
        return step.connection or self.chain.connection

    def client_for(self, connection):
        if connection.pk not in self._clients:
            self._clients[connection.pk] = ConnectionClient(connection)
        return self._clients[connection.pk]

    # ── cancellation ─────────────────────────────────────────────────────
    def _run_flag(self):
        return CallChainRun.objects.filter(pk=self.chain_run.pk, cancel_requested=True).exists()

    def check_cancel(self):
        if self.should_cancel():
            raise Cancelled()

    def sleep(self, seconds):
        """time.sleep in short slices, so a kill during a long wait / poll interval isn't ignored."""
        waited = 0.0
        while waited < seconds:
            self.check_cancel()
            chunk = min(SLICE_SECONDS, seconds - waited)
            time.sleep(chunk)
            waited += chunk
        self.check_cancel()

    # ── driver ───────────────────────────────────────────────────────────
    def run(self, start_order=0) -> CallChainRun:
        outcome = "ok"
        try:
            for step in self.chain.steps.order_by("order"):
                if step.order < start_order:
                    continue
                outcome = self.execute_step(step)
                if outcome != "ok":
                    break        # a later step almost certainly depends on this one
        finally:
            self.finish(outcome)
        return self.chain_run

    def finish(self, outcome="ok"):
        """outcome: 'ok' | 'failed' | 'cancelled'. Safe to call once, after the last step."""
        self.chain_run.status = {"ok": CallChainRun.STATUS_SUCCESS, "cancelled": CallChainRun.STATUS_CANCELLED}.get(outcome, CallChainRun.STATUS_FAILED)
        self.chain_run.finished_at = timezone.now()
        self.chain_run.result_file.save(
            f"chain_{self.chain.id}_run_{self.chain_run.id}.json",
            ContentFile(json.dumps(self.context, indent=2, default=str)),
            save=False,
        )
        self.chain_run.save()
        ACTIVE_RUNS.discard(self.chain_run.pk)
        from notifications.services import notify_chain_run
        notify_chain_run(self.chain_run, {"ok": "success", "cancelled": "killed"}.get(outcome, "failed"))

    def execute_step(self, step) -> str:
        """Run one step and record its result. Returns 'ok', 'failed' or 'cancelled'."""
        handler = {
            CallChainStep.KIND_HTTP: self._http, CallChainStep.KIND_WAIT: self._wait, CallChainStep.KIND_NEXT: self._next,
            CallChainStep.KIND_FIND: self._find, CallChainStep.KIND_CHECK: self._check, CallChainStep.KIND_FILE: self._file,
        }.get(step.kind)
        # `st` is filled in as the step progresses, so a failure still records what it got as far as.
        st = {"method": step.method if step.kind == CallChainStep.KIND_HTTP else "", "resolved_path": "", "resolved_body": None}
        outcome = "ok"
        try:
            if self.should_cancel():
                raise Cancelled()
            if handler is None:
                raise ValueError(f"Unknown step kind '{step.kind}'.")
            parsed = handler(step, st)
            st["response_json"] = parsed
            if step.kind != CallChainStep.KIND_WAIT:
                st["captured"] = apply_captures(step, parsed, self.context)
            error = ""
        except Cancelled:
            error, outcome = "Cancelled by user.", "cancelled"
        except Exception as exc:
            error, outcome = str(exc)[:2000] or exc.__class__.__name__, "failed"
        self._save(step, st, error)
        return outcome

    def _save(self, step, st, error):
        body = st.get("resolved_body")
        CallChainStepResult.objects.create(
            run=self.chain_run, step=step, order=step.order, name=step.name, kind=step.kind, method=st.get("method", ""),
            resolved_path=st.get("resolved_path", ""), resolved_body=json.dumps(body) if body is not None else "",
            status_code=st.get("status_code"), response_json=st.get("response_json"),
            poll_attempts=st.get("poll_attempts"), captured_variables=st.get("captured", {}),
            detail=st.get("detail", {}), response_headers=st.get("headers", {}), error=error,
        )

    def _request_options(self, step):
        """This step's headers and query params with {{placeholders}} filled in from what earlier steps
        fetched/captured. Resolved from the step's *definition* each time (not stored), so a next-page step
        — or a run resumed after a retry — can rebuild them without keeping any secret around."""
        headers = {p["name"]: resolve_path(p["value"], self.context) for p in (step.headers or [])}
        params = {p["name"]: resolve_path(p["value"], self.context) for p in (step.query_params or [])}
        return headers, params

    # ── kinds ────────────────────────────────────────────────────────────
    def _http(self, step, st):
        st["resolved_path"] = resolve_path(step.path, self.context)
        st["resolved_body"] = resolve_body(step.body, self.context)
        kwargs = {} if st["resolved_body"] is None else {"json": st["resolved_body"]}
        headers, params = self._request_options(step)
        kwargs.update(_request_kwargs(step.timeout_seconds, headers, params))
        if headers or params:
            st["detail"] = {"request_headers": mask_headers(headers), "request_params": params}
        client = self.client_for(self.connection_of(step))
        response = client.request(step.method, st["resolved_path"], **kwargs)
        st["status_code"] = response.status_code
        st["headers"] = dict(response.headers)
        response.raise_for_status()
        try:
            parsed = response.json() if response.content else None
        except ValueError:
            parsed = None
        self.context[step.name] = parsed
        self.paths[step.name] = st["resolved_path"]
        self.headers[step.name] = st["headers"]
        if step.is_async:
            parsed, st["poll_attempts"] = run_async_step(client, step, self.context, timeout=step.timeout_seconds, sleep=self.sleep, headers=headers)
            self.context[step.name] = parsed
        return parsed

    def _wait(self, step, st):
        seconds = min(max(float(step.params.get("seconds", 1)), 0), MAX_WAIT_SECONDS)
        self.sleep(seconds)
        st["detail"] = {"waited": seconds}
        self.context[step.name] = {"waited": seconds}
        return self.context[step.name]

    def _upstream(self, step, what):
        name = (step.params.get("from_step") or "").strip()
        if name not in self.headers:
            raise TemplateResolutionError(
                f"{what} reads step '{name}', which hasn't run yet, doesn't exist, or isn't an HTTP call.")
        return name

    def _next_path(self, base_path, cursor, cursor_param, connection):
        """Where to fetch the following page. A cursor/token goes in `cursor_param`; otherwise the
        pointer is a link — absolute URLs are only followed on the connection's own host, since
        this client attaches the connection's credentials to whatever it calls."""
        if cursor_param:
            parts = urlsplit(base_path)
            query = dict(parse_qsl(parts.query))
            query[cursor_param] = str(cursor)
            return urlunsplit(("", "", parts.path, urlencode(query), ""))
        link = str(cursor)
        target = urlsplit(link)
        if target.scheme:
            base = urlsplit(connection.base_url)
            if (target.scheme, target.netloc) != (base.scheme, base.netloc):
                raise StepFailed(f"The next link points to {target.netloc}, not this connection's host ({base.netloc}) — "
                                 "not following it, so credentials aren't sent elsewhere.")
            return link
        if link.startswith("?"):
            return urlsplit(base_path).path + link
        return link

    def _next(self, step, st):
        p = step.params
        name = self._upstream(step, "This next-page step")
        source = self.chain.steps.filter(name=name).first()
        if source is not None and source.method != "GET":
            raise StepFailed(f"Next page repeats a GET request; '{name}' is a {source.method}.")
        first = self.context.get(name)
        base_path = self.paths[name]
        connection = self.connection_of(source) if source is not None else self.chain.connection
        client = self.client_for(connection)
        up_headers, up_params = self._request_options(source) if source is not None else ({}, {})
        items_path, next_path, cursor_param = p.get("items_path", ""), p["next_path"], (p.get("cursor_param") or "").strip()
        max_pages = min(max(int(p.get("max_pages", 5)), 1), MAX_PAGES_CAP)

        items = _as_list(_extract(first, items_path))
        cursor = _extract(first, next_path)
        pages, last_path = 1, base_path
        while cursor not in (None, "", []) and pages < max_pages:
            self.check_cancel()
            last_path = self._next_path(base_path, cursor, cursor_param, connection)
            # Same headers on every page; the original params are only repeated when the pointer is a cursor
            # (a next *link* already carries its own full query string).
            response = client.request("GET", last_path, **_request_kwargs(None, up_headers, up_params if cursor_param else None))
            response.raise_for_status()
            try:
                page = response.json() if response.content else None
            except ValueError:
                page = None
            items += _as_list(_extract(page, items_path))
            cursor = _extract(page, next_path)
            pages += 1
        more = cursor not in (None, "", [])
        st["resolved_path"] = last_path
        st["detail"] = {"from_step": name, "pages": pages, "items": len(items), "more": more, "max_pages": max_pages}
        self.context[step.name] = {"items": items, "pages": pages, "more": more}
        return self.context[step.name]

    def _find(self, step, st):
        p = step.params
        source = (p.get("source") or "").strip().strip("{} ")
        items = _lookup(source, self.context)
        if not isinstance(items, list):
            raise StepFailed(f"'{source}' isn't a list (it's {type(items).__name__}) — point at the list itself, e.g. step_name.items.")
        field, operator = p.get("match_field", ""), p.get("operator", "equals")
        expected = resolve_path(p.get("value", ""), self.context)
        matches = [item for item in items if _compare(_extract(item, field), operator, expected)]
        st["detail"] = {"source": source, "searched": len(items), "matched": len(matches),
                        "criteria": f"{field or '(item)'} {operator} {expected}".strip()}
        if not matches and p.get("on_missing", "fail") == "fail":
            raise StepFailed(f"No item in '{source}' where {st['detail']['criteria']} ({len(items)} searched).")
        found = None if not matches else (matches[0] if p.get("pick", "first") == "first" else matches)
        self.context[step.name] = found
        return found

    def _check(self, step, st):
        p = step.params
        name = self._upstream(step, "This header check")
        wanted, operator = (p.get("header") or "").strip(), p.get("operator", "exists")
        expected = resolve_path(p.get("value", ""), self.context)
        actual = next((v for k, v in self.headers[name].items() if k.lower() == wanted.lower()), None)
        passed = _compare(actual, operator, expected)
        st["detail"] = {"from_step": name, "header": wanted, "operator": operator, "expected": expected, "actual": actual, "passed": passed}
        self.context[step.name] = {"header": wanted, "value": actual, "passed": passed}
        if not passed and p.get("on_fail", "fail") == "fail":
            shown = f" {operator} {expected!r}" if operator not in ("exists", "missing") else f" {operator}"
            raise StepFailed(f"Header {wanted!r} of '{name}' failed the check{shown}: actual is {actual!r}.")
        return self.context[step.name]

    def _file(self, step, st):
        p = step.params
        entity = Entity.objects.filter(pk=p.get("entity")).first()
        if entity is None or not entity.source_file:
            raise StepFailed("This step's file entity no longer exists or has no uploaded file.")
        rows = discovery.read_all_records_from_source_file(entity)
        limit = min(max(int(p.get("limit", 10)), 1), MAX_FILE_ROWS)
        columns = _file_columns(entity, rows[:limit])
        parsed = _json_safe({"entity": entity.name, "columns": columns, "rows": rows[:limit], "count": len(rows)})
        st["detail"] = {"entity": entity.name, "rows_shown": len(parsed["rows"]), "count": len(rows)}
        self.context[step.name] = parsed
        return parsed


Runner = _Runner        # a plan's custom run drives one of these directly


def run_chain(chain_run: CallChainRun) -> CallChainRun:
    return _Runner(chain_run).run()


def run_chain_retry(chain_run: CallChainRun, prior_context: dict, start_order: int,
                    prior_paths: dict = None, prior_headers: dict = None) -> CallChainRun:
    """Re-run a chain starting from `start_order`, with `prior_context` (and the per-step
    resolved paths / response headers a next-page or check-header step reads) pre-populated
    from the prior run's successful steps. Steps before `start_order` are skipped — their
    captured values are already in context. Used by the retry action so a transient failure
    doesn't force re-sending every earlier step's side-effects (created records, sent
    webhooks, etc.)."""
    return _Runner(chain_run, prior_context, prior_paths, prior_headers).run(start_order)
