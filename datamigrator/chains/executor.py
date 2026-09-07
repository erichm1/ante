"""Runs a CallChain's steps in order, in the calling thread (synchronous —
a chain is a handful of one-off calls, not a bulk record loop like
jobs/engine.py, so there's no need for a background thread + polling here).

Each step's `path`/`body` can reference a prior step's parsed JSON response
via {{step_name.some.json.path}} — resolved against a running `context`
dict, keyed by step name (the whole parsed response) *and* by any variable
names the step explicitly `captures` out of it (see apply_captures below) —
so a later step can use the short {{customer_id}} instead of needing to
know {{create_customer.id}}'s full shape. `context` (both forms together)
is what gets written to CallChainRun.result_file once the chain finishes,
so it can be read back independently of the run's own DB rows.
"""
import json
import re

from django.core.files.base import ContentFile
from django.utils import timezone

from connections.client import ConnectionClient

from .models import CallChainRun, CallChainStepResult

VAR_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_-]*(?:\.[A-Za-z0-9_-]+)*)\s*\}\}")


class TemplateResolutionError(Exception):
    pass


def _lookup(dotted_path: str, context: dict):
    parts = dotted_path.split(".")
    if parts[0] not in context:
        raise TemplateResolutionError(
            f"{{{{{dotted_path}}}}} references step '{parts[0]}', which hasn't run yet (or doesn't exist)."
        )
    value = context[parts[0]]
    for part in parts[1:]:
        if isinstance(value, dict):
            value = value.get(part)
        elif isinstance(value, list) and part.lstrip("-").isdigit():
            idx = int(part)
            value = value[idx] if -len(value) <= idx < len(value) else None
        else:
            value = None
    return value


def _extract(value, dotted_path: str):
    """Same dotted dict/list walk as _lookup, but starting from an already-
    known value (a step's own parsed response) rather than looking a name
    up in the context — this is what turns a capture's `path` into the
    value that gets stored under its `name`."""
    if not dotted_path or not dotted_path.strip():
        return value
    for part in dotted_path.split("."):
        if isinstance(value, dict):
            value = value.get(part)
        elif isinstance(value, list) and part.lstrip("-").isdigit():
            idx = int(part)
            value = value[idx] if -len(value) <= idx < len(value) else None
        else:
            value = None
    return value


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


def run_chain(chain_run: CallChainRun) -> CallChainRun:
    chain = chain_run.chain
    client = ConnectionClient(chain.connection)
    context = {}
    all_ok = True

    for step in chain.steps.order_by("order"):
        resolved_path = ""
        resolved_body = None
        try:
            resolved_path = resolve_path(step.path, context)
            resolved_body = resolve_body(step.body, context)

            kwargs = {} if resolved_body is None else {"json": resolved_body}
            response = client.request(step.method, resolved_path, **kwargs)
            response.raise_for_status()
            try:
                parsed = response.json() if response.content else None
            except ValueError:
                parsed = None

            context[step.name] = parsed
            captured = apply_captures(step, parsed, context)
            CallChainStepResult.objects.create(
                run=chain_run, step=step, order=step.order, name=step.name, method=step.method,
                resolved_path=resolved_path,
                resolved_body=json.dumps(resolved_body) if resolved_body is not None else "",
                status_code=response.status_code, response_json=parsed, captured_variables=captured,
            )
        except Exception as exc:
            CallChainStepResult.objects.create(
                run=chain_run, step=step, order=step.order, name=step.name, method=step.method,
                resolved_path=resolved_path,
                resolved_body=json.dumps(resolved_body) if resolved_body is not None else "",
                error=str(exc)[:2000],
            )
            all_ok = False
            break  # a later step almost certainly depends on this one

    chain_run.status = CallChainRun.STATUS_SUCCESS if all_ok else CallChainRun.STATUS_FAILED
    chain_run.finished_at = timezone.now()
    chain_run.result_file.save(
        f"chain_{chain.id}_run_{chain_run.id}.json",
        ContentFile(json.dumps(context, indent=2, default=str)),
        save=False,
    )
    chain_run.save()
    return chain_run
