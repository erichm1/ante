"""Three ways to populate an Entity's Fields, per the user's requirements:
  1. discover_from_openapi  — parse components.schemas out of a Swagger/OpenAPI doc
  2. discover_from_sample   — GET a live endpoint and infer field names/types from the response
  3. manual                 — handled directly by EntityViewSet/FieldViewSet CRUD, no helper needed
"""

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


def _first_record(payload):
    """Unwrap common list/envelope shapes to get one representative record."""
    if isinstance(payload, list):
        return payload[0] if payload else {}
    if isinstance(payload, dict):
        for key in ("results", "data", "items"):
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


def discover_from_openapi(connection, spec: dict, schema_names=None, endpoint_paths=None) -> list:
    """endpoint_paths: optional {schema_name: endpoint_path} since OpenAPI schemas
    don't always map 1:1 and obviously to a REST path."""
    endpoint_paths = endpoint_paths or {}
    schemas = (spec.get("components") or {}).get("schemas", {})
    created = []

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

    return created
