"""Read-only "dry run" of a mapping for the Studio's data preview: pull a small
sample of each source entity, run it through every field mapping's transforms
exactly as jobs/engine.py would, and hand back both sides — the input rows and
the payloads that would be written — without writing anything.

The only outbound traffic is one GET per distinct API-backed source entity
(file-backed entities read their stored file); target systems are never called.
"""
from jobs.engine import build_payload, transform_field
from schemas import discovery

DEFAULT_LIMIT = 10
MAX_LIMIT = 50


def clamp_limit(raw):
    try:
        return max(1, min(int(raw), MAX_LIMIT))
    except (TypeError, ValueError):
        return DEFAULT_LIMIT


def preview_mapping(mapping, limit=DEFAULT_LIMIT, include_drafts=False):
    """One entry per entity pair, in canvas order. A source that can't be read
    yields an entry with `error` set rather than failing the whole preview —
    one unreachable system shouldn't blank the pairs that are fine."""
    source_cache = {}    # source entity id -> (records, error) — read once even if it fans out
    entries = []

    pairs = mapping.entity_mappings.select_related(
        "source_entity", "source_entity__connection", "target_entity", "target_entity__connection",
    ).prefetch_related("field_mappings__source_field", "field_mappings__target_field", "source_entity__fields")

    for pair in pairs:
        source, target = pair.source_entity, pair.target_entity
        wires = list(pair.field_mappings.all())
        drafts = [fm for fm in wires if fm.status == "draft"]
        # A run only uses confirmed wires; `include_drafts` shows what the suggestions would add.
        field_mappings = wires if include_drafts else [fm for fm in wires if fm.status != "draft"]

        if source.id not in source_cache:
            try:
                source_cache[source.id] = (discovery.read_all_records(source), None)
            except Exception as exc:  # network, auth, bad file — anything the user needs to see
                source_cache[source.id] = ([], str(exc)[:300])
        records, error = source_cache[source.id]
        sample = records[:limit]

        # Declared fields first (stable order), then any extra keys the data actually carries.
        columns = [f.name for f in source.fields.all()]
        for record in sample:
            columns += [k for k in record if k not in columns]

        output, changed = [], []
        for record in sample:
            row, row_changed = {}, []
            for fm in field_mappings:
                raw, value = transform_field(record, fm)
                row[fm.target_field.name] = value
                if (fm.transform_rules or fm.transform) and value != raw:
                    row_changed.append(fm.target_field.name)
            output.append(row)
            changed.append(row_changed)

        entries.append({
            "entity_mapping": pair.id,
            "source_entity": source.name,
            "source_connection": source.connection.name,
            "target_entity": target.name,
            "target_connection": target.connection.name,
            "target_endpoint": target.endpoint_path,
            "write_method": pair.write_method,
            "draft_count": len(drafts),
            "drafts_included": include_drafts,
            "total": len(records),
            "sample_size": len(sample),
            "source_columns": columns,
            "mapped_source": sorted({n for fm in field_mappings for n in (fm.source_field.name, fm.source_field.name.split(".")[0])}),   # a nested field also marks its top-level column as used
            "target_columns": [fm.target_field.name for fm in field_mappings],
            "source_of": {fm.target_field.name: fm.source_field.name for fm in field_mappings},   # target column -> where it came from
            "rows": sample,
            "output": output,
            "changed": changed,
            "payloads": [build_payload(r, field_mappings) for r in sample],
            "error": error,
        })
    return entries
