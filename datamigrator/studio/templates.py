"""Starter templates for the Studio sidebar: pick one, fill a small form, get a
working mapping / chain / plan (or start a run) instead of building it from an
empty canvas.

Each entry in CATALOG is pure metadata — the Studio renders its form from the
`params` schema — and APPLY maps the template's slug to the function that
actually creates the objects. Everything a template creates is an ordinary
Mapping / CallChain / MigrationPlan, editable and deletable like any other;
nothing here is a special object type. Templates that only start something
(`action`) have no server side: the Studio opens the mapping and its run dialog.
"""
import json
import re

from django.db import transaction

from chains.models import CallChain, CallChainStep
from connections.models import Connection
from mappings.models import EntityMapping, FieldMapping, Mapping
from plans.models import MigrationPlan, PlanStep
from schemas.models import Entity, Field


class TemplateError(ValueError):
    """A problem with the user's answers — its message is shown as-is."""


# ── Catalog (metadata the Studio renders) ────────────────────────────────────

def _p(name, label, type="text", **kw):
    return {"name": name, "label": label, "type": type, **kw}


_NAME = _p("name", "Name", required=True, placeholder="e.g. Products → ERP")
_METHOD = _p("write_method", "Operation", "select", options=["POST", "PUT", "PATCH"], default="POST",
             help="HTTP verb used to write each record to the destination.")
_TRANSFORM = _p("transform", "Clean up text fields", "select", default="none", options=[
    {"value": "none", "label": "Leave values as they are"},
    {"value": "trim", "label": "Trim whitespace"},
    {"value": "uppercase", "label": "UPPERCASE"},
    {"value": "lowercase", "label": "lowercase"},
], help="Added as a transform on every text field that is copied to a text field. Editable per wire afterwards.")
_CONNECTION = _p("connection", "Connection", "connection", required=True, help="Every step calls this connection's API.")
_CHAIN_NAME = _p("name", "Name", required=True, placeholder="e.g. Create customer + order")

CATALOG = [
    # ── migrations (mappings) ──
    {"slug": "copy-matching-fields", "kind": "mapping", "icon": "bi-magic", "title": "Copy matching fields",
     "blurb": "Pick a source and a destination entity — every field with the same name is wired for you.",
     "params": [_NAME,
                _p("source_connection", "Source connection", "connection", required=True),
                _p("source_entity", "Source entity", "entity", required=True, connection_from="source_connection"),
                _p("target_connection", "Destination connection", "connection", required=True),
                _p("target_entity", "Destination entity", "entity", required=True, connection_from="target_connection"),
                _METHOD, _TRANSFORM]},
    {"slug": "file-import", "kind": "mapping", "icon": "bi-file-earmark-spreadsheet", "title": "Import a CSV / Excel file",
     "blurb": "Read rows from an uploaded file entity and push them into an API, matching columns by name.",
     "params": [_NAME,
                _p("source_connection", "File connection", "connection", required=True),
                _p("source_entity", "File entity", "entity", required=True, connection_from="source_connection", file_only=True),
                _p("target_connection", "Destination connection", "connection", required=True),
                _p("target_entity", "Destination entity", "entity", required=True, connection_from="target_connection"),
                _METHOD, _TRANSFORM]},
    {"slug": "fan-out", "kind": "mapping", "icon": "bi-diagram-3", "title": "Sync one source to several destinations",
     "blurb": "One source entity feeds any number of destination entities, each wired by matching field names.",
     "params": [_NAME,
                _p("source_connection", "Source connection", "connection", required=True),
                _p("source_entity", "Source entity", "entity", required=True, connection_from="source_connection"),
                _p("target_entities", "Destination entities", "entities", required=True,
                   help="Tick one or more — they can live on different connections."),
                _METHOD, _TRANSFORM]},

    # ── chains ──
    {"slug": "create-then-link", "kind": "chain", "icon": "bi-link-45deg", "title": "Create, then link",
     "blurb": "Create a record, capture its new id, then use that id in a second call (e.g. customer → order).",
     "params": [_CHAIN_NAME, _CONNECTION,
                _p("create_path", "Create — path", required=True, default="/customers"),
                _p("create_body", "Create — JSON body", "textarea", default='{"name": "Example"}'),
                _p("id_path", "Where the new id is in the response", default="id", help="Dotted JSON path, e.g. id or data.id"),
                _p("link_method", "Second call — method", "select", options=["POST", "PUT", "PATCH"], default="POST"),
                _p("link_path", "Second call — path", required=True, default="/orders"),
                _p("link_body", "Second call — JSON body", "textarea", default='{"customer_id": {{record_id}}}',
                   help="{{record_id}} is the id captured from the first call.")]},
    {"slug": "list-then-fetch", "kind": "chain", "icon": "bi-list-check", "title": "List, then fetch one",
     "blurb": "Read a list, capture the first item's id, and fetch that item's full record.",
     "params": [_CHAIN_NAME, _CONNECTION,
                _p("list_path", "List — path", required=True, default="/products"),
                _p("id_path", "Path to the first id in the list", default="0.id", help="0.id = first item; items.0.id if the list is wrapped."),
                _p("detail_path", "Detail — path", required=True, default="/products/{{first_id}}", help="{{first_id}} is the captured id.")]},
    {"slug": "async-export", "kind": "chain", "icon": "bi-hourglass-split", "title": "Start a job and wait for it",
     "blurb": "Kick off a long-running job (export, report, bulk import), poll until it finishes, then fetch the result.",
     "params": [_CHAIN_NAME, _CONNECTION,
                _p("start_method", "Start — method", "select", options=["POST", "GET"], default="POST"),
                _p("start_path", "Start — path", required=True, default="/exports"),
                _p("poll_path", "Status — path", required=True, default="/exports/{{start_job.id}}/status", help="{{start_job.<field>}} reads the start call's response."),
                _p("condition_path", "Status field", default="status"),
                _p("condition_value", "Finished when it equals", default="completed"),
                _p("result_path", "Result — path (optional)", default="/exports/{{start_job.id}}/download")]},

    # ── plans ──
    {"slug": "ordered-plan", "kind": "plan", "icon": "bi-list-ol", "title": "Ordered plan",
     "blurb": "Sequence existing mappings and chains — e.g. migrate, then verify — and run or schedule them together.",
     "params": [_p("name", "Name", required=True, placeholder="e.g. Nightly migration"),
                _p("steps", "Steps, in order", "steps", required=True)]},

    # ── runs (no server side: the Studio opens the mapping's run dialog) ──
    {"slug": "run-now", "kind": "run", "icon": "bi-play-circle", "title": "Run a migration now",
     "blurb": "Choose a mapping and start it immediately.", "action": "run",
     "params": [_p("mapping", "Mapping", "mapping", required=True)]},
    {"slug": "schedule-run", "kind": "run", "icon": "bi-clock", "title": "Schedule a migration",
     "blurb": "Choose a mapping, then set when it runs, a rate limit or input files.", "action": "schedule",
     "params": [_p("mapping", "Mapping", "mapping", required=True)]},
]


# ── Helpers ──────────────────────────────────────────────────────────────────

def _text(p, key, required=False, default=""):
    value = p.get(key)
    value = default if value is None else str(value).strip()
    if required and not value:
        raise TemplateError(f"“{key.replace('_', ' ')}” is required.")
    return value


def _get(model, pk, what):
    try:
        return model.objects.get(pk=int(pk))
    except (TypeError, ValueError, model.DoesNotExist):
        raise TemplateError(f"Choose a valid {what}.")


def _json_body(raw, what):
    """Same rule the chain editor applies: valid JSON once {{placeholders}} are stood in for."""
    if raw.strip():
        try:
            json.loads(re.sub(r"\{\{[^}]*\}\}", "null", raw))
        except ValueError:
            raise TemplateError(f"{what} must be valid JSON ({{{{placeholders}}}} aside).")
    return raw.strip()


def _norm(name):
    return re.sub(r"[^a-z0-9]", "", name.lower())


def auto_map(pair, preset="none"):
    """Wire every target field to the source field with the same (normalised) name —
    ignoring case, underscores and dashes, and falling back to the last segment of a
    dotted target name (precos.preco → preco). Returns (matched, unmatched target names)."""
    source_by_name = {}
    for f in pair.source_entity.fields.all():
        source_by_name.setdefault(_norm(f.name), f)
    matched, unmatched = 0, []
    for target in pair.target_entity.fields.all():
        source = source_by_name.get(_norm(target.name)) or source_by_name.get(_norm(target.name.split(".")[-1]))
        if not source:
            unmatched.append(target.name)
            continue
        text_to_text = source.field_type == Field.TYPE_STRING and target.field_type == Field.TYPE_STRING
        FieldMapping.objects.create(
            entity_mapping=pair, source_field=source, target_field=target,
            transform_rules=[{"op": preset}] if preset in ("trim", "uppercase", "lowercase") and text_to_text else [],
        )
        matched += 1
    return matched, unmatched


def _describe(pair, matched, unmatched):
    note = f"{pair.source_entity.name} → {pair.target_entity.name}: {matched} field(s) matched"
    if unmatched:
        shown = ", ".join(unmatched[:4]) + (f" +{len(unmatched) - 4} more" if len(unmatched) > 4 else "")
        note += f"; not matched: {shown}"
    return note + "."


# ── Appliers ─────────────────────────────────────────────────────────────────

def _write_method(p):
    method = _text(p, "write_method", default="POST").upper()
    if method not in ("POST", "PUT", "PATCH"):
        raise TemplateError("Choose POST, PUT or PATCH.")
    return method


@transaction.atomic
def apply_mapping(slug, p):
    name = _text(p, "name", required=True)
    source_conn = _get(Connection, p.get("source_connection"), "source connection")
    source = _get(Entity, p.get("source_entity"), "source entity")
    if source.connection_id != source_conn.id:
        raise TemplateError("The source entity doesn't belong to the source connection.")
    if slug == "file-import" and not source.source_file:
        raise TemplateError(f"“{source.name}” isn't backed by an uploaded file — add one from its connection's “From file” tab.")

    if slug == "fan-out":
        ids = p.get("target_entities") or []
        targets = [_get(Entity, i, "destination entity") for i in ids]
        if not targets:
            raise TemplateError("Tick at least one destination entity.")
    else:
        targets = [_get(Entity, p.get("target_entity"), "destination entity")]
        target_conn = _get(Connection, p.get("target_connection"), "destination connection")
        if targets[0].connection_id != target_conn.id:
            raise TemplateError("The destination entity doesn't belong to the destination connection.")
    if any(t.connection_id == source_conn.id for t in targets):
        raise TemplateError("A destination can't be on the source connection — pick a different system to write to.")

    mapping = Mapping.objects.create(name=name, source_connection=source_conn)
    mapping.destination_connections.add(*{t.connection for t in targets})
    notes = []
    for target in targets:
        pair = EntityMapping.objects.create(mapping=mapping, source_entity=source, target_entity=target, write_method=_write_method(p))
        matched, unmatched = auto_map(pair, _text(p, "transform", default="none"))
        notes.append(_describe(pair, matched, unmatched))
        if not target.endpoint_path:
            notes.append(f"“{target.name}” has no endpoint path yet — set one (edit entity) before running.")
    return {"kind": "mapping", "id": mapping.id, "notes": notes}


def _chain(p):
    name = _text(p, "name", required=True)
    return CallChain.objects.create(name=name, connection=_get(Connection, p.get("connection"), "connection"))


@transaction.atomic
def apply_chain(slug, p):
    chain = _chain(p)
    if slug == "create-then-link":
        CallChainStep.objects.create(
            chain=chain, order=1, name="create_record", method="POST", path=_text(p, "create_path", required=True),
            body=_json_body(_text(p, "create_body"), "The create body"),
            captures=[{"name": "record_id", "path": _text(p, "id_path", default="id")}])
        CallChainStep.objects.create(
            chain=chain, order=2, name="link_record", method=_text(p, "link_method", default="POST").upper(),
            path=_text(p, "link_path", required=True), body=_json_body(_text(p, "link_body"), "The second call's body"))
        notes = ["Step 1 creates the record and captures its id as {{record_id}}; step 2 uses it."]
    elif slug == "list-then-fetch":
        CallChainStep.objects.create(
            chain=chain, order=1, name="list_items", method="GET", path=_text(p, "list_path", required=True),
            captures=[{"name": "first_id", "path": _text(p, "id_path", default="0.id")}])
        CallChainStep.objects.create(chain=chain, order=2, name="get_item", method="GET", path=_text(p, "detail_path", required=True))
        notes = ["Step 1 lists and captures the first id as {{first_id}}; step 2 fetches that record."]
    elif slug == "async-export":
        result_path = _text(p, "result_path")
        CallChainStep.objects.create(
            chain=chain, order=1, name="start_job", method=_text(p, "start_method", default="POST").upper(),
            path=_text(p, "start_path", required=True), is_async=True,
            async_poll_path=_text(p, "poll_path", required=True), async_poll_method="GET",
            async_condition_path=_text(p, "condition_path", default="status"),
            async_condition_value=_text(p, "condition_value", default="completed"),
            async_interval_seconds=2.0, async_timeout_seconds=120.0, async_result_path=result_path)
        notes = ["One asynchronous step: it starts the job, polls until the status field matches, then reads the result."]
    else:
        raise TemplateError("Unknown chain template.")
    return {"kind": "chain", "id": chain.id, "notes": notes}


@transaction.atomic
def apply_plan(slug, p):
    name = _text(p, "name", required=True)
    steps = p.get("steps") or []
    if not steps:
        raise TemplateError("Add at least one step.")
    plan = MigrationPlan.objects.create(name=name, execution_mode=MigrationPlan.MODE_MIXED)
    for order, step in enumerate(steps, start=1):
        kind, pk = (step or {}).get("kind"), (step or {}).get("id")
        if kind == "chain":
            PlanStep.objects.create(plan=plan, order=order, chain=_get(CallChain, pk, "chain"))
        elif kind == "mapping":
            PlanStep.objects.create(plan=plan, order=order, mapping=_get(Mapping, pk, "mapping"))
        else:
            raise TemplateError("Each step must be a mapping or a chain.")
    return {"kind": "plan", "id": plan.id, "notes": [f"Draft plan with {len(steps)} step(s) — execute it from the toolbar when ready."]}


APPLY = {
    "copy-matching-fields": apply_mapping, "file-import": apply_mapping, "fan-out": apply_mapping,
    "create-then-link": apply_chain, "list-then-fetch": apply_chain, "async-export": apply_chain,
    "ordered-plan": apply_plan,
}


def apply_template(slug, params):
    if slug not in APPLY:
        raise TemplateError("Unknown template.")
    return APPLY[slug](slug, params or {})
