"""Ways to populate an Entity's Fields:
  1. discover_from_openapi  — parse components.schemas out of a Swagger/OpenAPI doc,
                              and catalogue every path in it (see the second pass below)
  2. discover_from_sample   — GET a live endpoint and infer field names/types from the response
  3. discover_from_csv      — header row of an uploaded CSV -> fields, typed from the first data row.
                              The file itself is kept on entity.source_file, so it can also be
                              the entity's actual data source at run time (read_all_records_from_source_file,
                              used by jobs/engine.py) — a mapping can use a CSV/XLSX as its source with
                              no real API/connection behind it at all, tagged source='manual' either way.
  4. discover_from_xlsx     — same, from the first sheet of an uploaded .xlsx workbook
  5. manual                 — handled directly by EntityViewSet/FieldViewSet CRUD, no helper needed
"""
import csv
import io
import re

import openpyxl

from .models import Entity, Field

OPENAPI_TYPE_MAP = {
    "string": Field.TYPE_STRING,
    "integer": Field.TYPE_INTEGER,
    "number": Field.TYPE_NUMBER,
    "boolean": Field.TYPE_BOOLEAN,
    "object": Field.TYPE_OBJECT,
    "array": Field.TYPE_ARRAY,
}


def infer_type(value) -> str:
    if isinstance(value, bool):
        return Field.TYPE_BOOLEAN
    if isinstance(value, int):
        return Field.TYPE_INTEGER
    if isinstance(value, float):
        return Field.TYPE_NUMBER
    if isinstance(value, dict):
        return Field.TYPE_OBJECT
    if isinstance(value, list):
        return Field.TYPE_ARRAY
    return Field.TYPE_STRING


def infer_type_from_string(value: str) -> str:
    """CSV cells are always strings — coerce to guess the underlying type,
    same output space as infer_type()."""
    if value is None or value == "":
        return Field.TYPE_STRING
    if value.strip().lower() in ("true", "false"):
        return Field.TYPE_BOOLEAN
    try:
        int(value)
        return Field.TYPE_INTEGER
    except ValueError:
        pass
    try:
        float(value)
        return Field.TYPE_NUMBER
    except ValueError:
        pass
    return Field.TYPE_STRING


# Common list-envelope keys across the APIs this app talks to — "itens" is
# Tiny ERP's own (pt-BR) list key, e.g. GET /produtos -> {"itens": [...]}.
# Kept in one place so extract_records_from_payload and _first_record never
# drift out of sync with each other.
LIST_ENVELOPE_KEYS = ("results", "data", "items", "itens")


def extract_records_from_payload(payload):
    """Normalizes a parsed JSON API response into a flat list of records —
    a list stays a list; a dict is checked for a common list-envelope key
    (see LIST_ENVELOPE_KEYS); anything else becomes a single-record list.
    Shared by read_all_records (below) and jobs/engine.py::records_for's
    live-read branch."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in LIST_ENVELOPE_KEYS:
            if isinstance(payload.get(key), list):
                return payload[key]
        return [payload]
    return []


def _first_record(payload):
    """Unwrap common list/envelope shapes to get one representative record."""
    if isinstance(payload, list):
        return payload[0] if payload else {}
    if isinstance(payload, dict):
        for key in LIST_ENVELOPE_KEYS:
            value = payload.get(key)
            if isinstance(value, list) and value:
                return value[0]
        return payload
    return {}


def discover_from_sample(connection, entity_name: str, endpoint_path: str, client) -> Entity:
    response = client.get(endpoint_path)
    response.raise_for_status()
    record = _first_record(response.json())

    entity, _ = Entity.objects.update_or_create(
        connection=connection, name=entity_name,
        defaults={"endpoint_path": endpoint_path, "source": Entity.SOURCE_SAMPLED},
    )
    for key, value in record.items():
        Field.objects.update_or_create(
            entity=entity, name=key,
            defaults={"field_type": infer_type(value), "sample_value": str(value)[:255]},
        )
    return entity


def discover_from_csv(connection, entity_name: str, endpoint_path: str, file_obj) -> Entity:
    """file_obj: an uploaded file (bytes) — header row becomes field names,
    the first data row is used to infer each field's type. The file is also
    saved onto entity.source_file so it can be re-read in full later (see
    read_all_records_from_source_file) instead of just sampled here."""
    text = io.TextIOWrapper(file_obj, encoding="utf-8-sig", newline="")
    reader = csv.DictReader(text)
    first_row = next(reader, {}) or {}

    entity, _ = Entity.objects.update_or_create(
        connection=connection, name=entity_name,
        defaults={"endpoint_path": endpoint_path, "source": Entity.SOURCE_MANUAL},
    )
    for key in reader.fieldnames or []:
        value = first_row.get(key, "")
        Field.objects.update_or_create(
            entity=entity, name=key,
            defaults={"field_type": infer_type_from_string(value), "sample_value": str(value)[:255]},
        )

    file_obj.seek(0)
    entity.source_file.save(getattr(file_obj, "name", f"{entity_name}.csv"), file_obj, save=True)
    return entity


def discover_from_xlsx(connection, entity_name: str, endpoint_path: str, file_obj) -> Entity:
    """file_obj: an uploaded .xlsx file (bytes) — first sheet, header row +
    first data row, same shape as discover_from_csv (including saving the
    file onto entity.source_file for later full reads)."""
    workbook = openpyxl.load_workbook(file_obj, read_only=True, data_only=True)
    sheet = workbook.worksheets[0]
    rows = sheet.iter_rows(values_only=True)
    header = next(rows, ())
    first_row = next(rows, ())

    entity, _ = Entity.objects.update_or_create(
        connection=connection, name=entity_name,
        defaults={"endpoint_path": endpoint_path, "source": Entity.SOURCE_MANUAL},
    )
    for i, key in enumerate(header):
        if not key:
            continue
        value = first_row[i] if i < len(first_row) else None
        Field.objects.update_or_create(
            entity=entity, name=str(key),
            defaults={"field_type": infer_type(value), "sample_value": str(value)[:255] if value is not None else ""},
        )

    file_obj.seek(0)
    entity.source_file.save(getattr(file_obj, "name", f"{entity_name}.xlsx"), file_obj, save=True)
    return entity


def read_all_records_from_file(file_field, filename: str = None) -> list:
    """Reads every row of a CSV or .xlsx FieldFile as a list of dicts keyed
    by column name. `filename` picks the parser (defaults to file_field's
    own name) — lets a caller read a *different* upload (e.g. a run's own
    input_file, see jobs/engine.py) through whatever extension it was
    actually saved with, independent of the file_field's own filename."""
    filename = (filename or file_field.name).lower()
    file_field.open("rb")
    try:
        if filename.endswith(".csv"):
            text = io.TextIOWrapper(file_field, encoding="utf-8-sig", newline="")
            return list(csv.DictReader(text))
        if filename.endswith(".xlsx"):
            workbook = openpyxl.load_workbook(file_field, read_only=True, data_only=True)
            sheet = workbook.worksheets[0]
            rows = sheet.iter_rows(values_only=True)
            header = list(next(rows, ()))
            records = []
            for row in rows:
                records.append({
                    header[i]: row[i] if i < len(row) else None
                    for i in range(len(header)) if header[i]
                })
            return records
        raise ValueError(f"Unsupported source file type: {filename}")
    finally:
        file_field.close()


def read_all_records_from_source_file(entity) -> list:
    """Reads every row of an Entity's uploaded source_file (CSV or .xlsx) as
    a list of dicts keyed by field name — this is what jobs/engine.py calls
    instead of making an HTTP request when a mapping's source entity has no
    real API/connection behind it at all, just an uploaded file."""
    return read_all_records_from_file(entity.source_file)


def read_all_records(entity) -> list:
    """Every record for this entity, full stop — from its stored file if it
    has one (read_all_records_from_source_file), else one live GET against
    its connection's endpoint_path. Used by reports/exporter.py, which
    needs a plain "give me everything this entity has" with no run/
    throttle bookkeeping (a report issues one read per included entity, not
    a bulk per-record loop) — jobs/engine.py::records_for is the run-scoped
    equivalent (adds throttling, logging, and a run's own input_file
    override) and stays separate rather than this delegating to it."""
    if entity.source_file:
        return read_all_records_from_source_file(entity)

    from connections.client import ConnectionClient  # local import: schemas -> connections is fine, avoids a cycle at module load

    response = ConnectionClient(entity.connection).get(entity.endpoint_path)
    response.raise_for_status()
    return extract_records_from_payload(response.json())


def _resolve_schema_ref(operation: dict) -> str | None:
    """Pulls a schema name out of an operation's 2xx JSON response, direct or
    array ($ref, or items.$ref) — used to backfill endpoint_path on schema
    entities and to decide which bare paths need their own Entity."""
    responses = operation.get("responses") or {}
    for status_code in ("200", "201"):
        content = ((responses.get(status_code) or {}).get("content") or {}).get("application/json") or {}
        schema = content.get("schema") or {}
        ref = schema.get("$ref") or (schema.get("items") or {}).get("$ref")
        if ref:
            return ref.rsplit("/", 1)[-1]
    return None


def _entity_name_from_path(path: str) -> str:
    segment = [p for p in path.split("/") if p and not p.startswith("{")][-1:]
    name = segment[0] if segment else path
    name = re.sub(r"[^a-zA-Z0-9]+", " ", name).strip().title().replace(" ", "")
    return name or "Endpoint"


def discover_from_openapi(connection, spec: dict, schema_names=None, endpoint_paths=None) -> list:
    """endpoint_paths: optional {schema_name: endpoint_path} since OpenAPI schemas
    don't always map 1:1 and obviously to a REST path.

    Beyond components.schemas, also walks `paths` so every endpoint in the
    spec is catalogued as a reusable Entity on this connection, whether or
    not it ends up used in the current mapping: paths whose response schema
    is resolvable backfill that schema entity's endpoint_path (when not
    already set by `endpoint_paths`), and paths with no resolvable schema
    still get a bare Entity (fields can be added to it later)."""
    endpoint_paths = endpoint_paths or {}
    schemas = (spec.get("components") or {}).get("schemas", {})
    created = []
    created_by_schema_name = {}

    for schema_name, schema in schemas.items():
        if schema_names and schema_name not in schema_names:
            continue
        if schema.get("type") not in (None, "object"):
            continue

        entity, _ = Entity.objects.update_or_create(
            connection=connection, name=schema_name,
            defaults={
                "source": Entity.SOURCE_OPENAPI,
                "endpoint_path": endpoint_paths.get(schema_name, ""),
            },
        )
        required = set(schema.get("required", []))
        for prop_name, prop in (schema.get("properties") or {}).items():
            field_type = OPENAPI_TYPE_MAP.get(prop.get("type"), Field.TYPE_STRING)
            Field.objects.update_or_create(
                entity=entity, name=prop_name,
                defaults={"field_type": field_type, "required": prop_name in required},
            )
        created.append(entity)
        created_by_schema_name[schema_name] = entity

    for path, path_item in (spec.get("paths") or {}).items():
        get_operation = (path_item or {}).get("get")
        schema_name = _resolve_schema_ref(get_operation) if get_operation else None
        matched_entity = created_by_schema_name.get(schema_name) if schema_name else None

        if matched_entity:
            if not matched_entity.endpoint_path:
                matched_entity.endpoint_path = path
                matched_entity.save(update_fields=["endpoint_path"])
            continue

        # No schema resolved for this path — still catalogue it, bare, for reuse later.
        entity, was_created = Entity.objects.update_or_create(
            connection=connection, name=_entity_name_from_path(path),
            defaults={"source": Entity.SOURCE_OPENAPI, "endpoint_path": path},
        )
        if was_created:
            created.append(entity)

    return created
