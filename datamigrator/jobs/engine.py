"""Migration engine. Runs in a background thread (see run_migration_in_background)
so the canvas can poll a run's progress — including requests_made, for the live
rq/s counter — while it's still in flight.

A mapping has one source connection and one or more destination connections,
so a target client is built per destination connection (cached per run), and
each source entity is read at most once per run even if it fans out to
several destinations.

Good enough for a scaffold and small/medium data sets; for large volumes swap
this for a real task queue (Celery) — the per-record logic doesn't change,
only who calls it and how progress gets reported.
"""

from simpleeval import simple_eval
from django.db import connections
from django.utils import timezone

from connections.client import ConnectionClient

from .models import MigrationLog, MigrationRun


def _log(run: MigrationRun, message: str, level=MigrationLog.LEVEL_INFO):
    MigrationLog.objects.create(run=run, message=message, level=level)


def _apply_transform(value, expression: str):
    if not expression:
        return value
    try:
        return simple_eval(expression, names={"value": value})
    except Exception:
        # A bad transform shouldn't take down the whole run — fall back to the raw value.
        return value


def _extract_records(payload):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("results", "data", "items"):
            if isinstance(payload.get(key), list):
                return payload[key]
        return [payload]
    return []


def run_migration(run: MigrationRun) -> MigrationRun:
    """Executes `run` in place. `run` must already exist (status=running) so
    its id is known to the caller before this function's progress updates land."""
    mapping = run.mapping
    source_client = ConnectionClient(mapping.source_connection)
    target_clients = {}       # connection_id -> ConnectionClient
    source_records = {}       # source_entity_id -> extracted records, read once per run

    def note_request():
        run.requests_made += 1
        run.save(update_fields=["requests_made"])

    def target_client_for(connection):
        if connection.id not in target_clients:
            target_clients[connection.id] = ConnectionClient(connection)
        return target_clients[connection.id]

    def records_for(entity):
        if entity.id not in source_records:
            _log(run, f"Reading {entity} ...")
            response = source_client.get(entity.endpoint_path)
            note_request()
            response.raise_for_status()
            records = _extract_records(response.json())
            source_records[entity.id] = records
            run.records_read += len(records)
            run.save(update_fields=["records_read"])
            _log(run, f"Fetched {len(records)} record(s).")
        return source_records[entity.id]

    try:
        entity_mappings = mapping.entity_mappings.select_related(
            "source_entity", "target_entity", "target_entity__connection",
        )
        if not entity_mappings:
            _log(run, "No entity mappings configured — nothing to migrate.", level=MigrationLog.LEVEL_WARNING)

        for entity_mapping in entity_mappings:
            records = records_for(entity_mapping.source_entity)
            target_connection = entity_mapping.target_entity.connection
            target_client = target_client_for(target_connection)

            field_mappings = list(entity_mapping.field_mappings.select_related("source_field", "target_field"))
            if not field_mappings:
                _log(
                    run,
                    f"No field mappings for {entity_mapping} — skipping records.",
                    level=MigrationLog.LEVEL_WARNING,
                )
                continue

            _log(
                run,
                f"Writing {len(records)} record(s) from {entity_mapping.source_entity} "
                f"to {entity_mapping.target_entity} on {target_connection.name} ...",
            )
            for record in records:
                out = {}
                for field_mapping in field_mappings:
                    raw_value = record.get(field_mapping.source_field.name)
                    out[field_mapping.target_field.name] = _apply_transform(raw_value, field_mapping.transform)
                try:
                    write_response = target_client.post(entity_mapping.target_entity.endpoint_path, json=out)
                    note_request()
                    write_response.raise_for_status()
                    run.records_written += 1
                except Exception as exc:
                    run.records_failed += 1
                    _log(
                        run,
                        f"Failed to write record {out} to {target_connection.name}: {exc}",
                        level=MigrationLog.LEVEL_ERROR,
                    )
                finally:
                    run.save(update_fields=["records_written", "records_failed"])

        run.status = MigrationRun.STATUS_FAILED if run.records_failed and not run.records_written else MigrationRun.STATUS_SUCCESS
    except Exception as exc:
        run.status = MigrationRun.STATUS_FAILED
        _log(run, f"Migration aborted: {exc}", level=MigrationLog.LEVEL_ERROR)
    finally:
        run.finished_at = timezone.now()
        run.save()

    return run


def run_migration_in_background(run_id: int):
    """Entry point for a background thread: fetches its own copy of the run
    and always releases the thread's DB connection when done."""
    try:
        run = MigrationRun.objects.select_related("mapping", "mapping__source_connection").get(pk=run_id)
        run_migration(run)
    finally:
        connections.close_all()
