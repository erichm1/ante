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

import time

from simpleeval import simple_eval
from django.db import connections
from django.utils import timezone

from connections.client import ConnectionClient
from schemas import discovery
from schemas.models import Field

from .models import MigrationLog, MigrationRun, RunStepStatus


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


def _apply_rules(value, rules: list):
    """The no-code alternative to writing a raw `transform` expression — a
    field mapping's transform_rules, built and edited entirely from the UI
    (mappings/raw.html's rule builder) by someone with no Python background.
    Applied in order, each rule transforming the running value; unknown/
    malformed rules are skipped rather than failing the record."""
    for rule in rules or []:
        op = rule.get("op")
        try:
            if op == "uppercase":
                value = value.upper() if isinstance(value, str) else value
            elif op == "lowercase":
                value = value.lower() if isinstance(value, str) else value
            elif op == "trim":
                value = value.strip() if isinstance(value, str) else value
            elif op == "map":
                matched = False
                for case in rule.get("cases") or []:
                    if str(value) == str(case.get("from", "")):
                        value = case.get("to")
                        matched = True
                        break
                if not matched and rule.get("default_mode") == "value":
                    value = rule.get("default_value")
            elif op == "default_if_empty":
                if value is None or value == "":
                    value = rule.get("value")
        except Exception:
            pass  # a bad rule shouldn't take down the whole run — leave value as-is
    return value


def _coerce_to_field_type(value, field_type: str):
    """CSV/XLSX rows are read back as strings for every cell (or, for xlsx,
    whatever openpyxl's own guess is) — a target field declared integer/
    number/boolean still needs a real JSON int/float/bool in the write
    payload, not "123"/"true", or a strict API (like Tiny ERP's) will reject
    it. A no-op for values already the right Python type (the common case
    for a live JSON API source) and for string/object/array targets."""
    if value is None or value == "":
        return value
    try:
        if field_type == Field.TYPE_INTEGER:
            return value if isinstance(value, int) and not isinstance(value, bool) else int(float(value))
        if field_type == Field.TYPE_NUMBER:
            return value if isinstance(value, float) else float(value)
        if field_type == Field.TYPE_BOOLEAN:
            return value if isinstance(value, bool) else str(value).strip().lower() in ("true", "1", "yes")
    except (TypeError, ValueError):
        pass
    return value


def _assign_nested(out: dict, dotted_name: str, value) -> None:
    """A target field named e.g. 'precos.preco' builds {"precos": {"preco": value}}
    in the write payload instead of a literal 'precos.preco' key — lets a flat
    CSV/XLSX source (or any flat source) map into an API that expects nested
    JSON (Tiny ERP's /produtos, e.g. marca.id, categoria.id, precos.preco),
    without needing a real nested source to walk. A plain name (no dot) keeps
    today's exact behavior: a single top-level key."""
    parts = dotted_name.split(".")
    container = out
    for part in parts[:-1]:
        container = container.setdefault(part, {})
    container[parts[-1]] = value


def run_migration(run: MigrationRun, entity_mapping_ids=None) -> MigrationRun:
    """Executes `run` in place. `run` must already exist (status=running) so
    its id is known to the caller before this function's progress updates land.
    Pass `entity_mapping_ids` (a list/set of ids) to restrict execution to
    those entity mappings only — used by the retry action to skip already-
    successful mappings and only re-run the ones that failed."""
    mapping = run.mapping
    source_client = ConnectionClient(mapping.source_connection, run=run)
    target_clients = {}       # connection_id -> ConnectionClient
    source_records = {}       # source_entity_id -> extracted records, read once per run
    last_request_at = None    # monotonic timestamp of the last throttled call, across reads and writes alike

    def throttle():
        """Sleeps just long enough to keep this run's combined read+write rate
        under run.rate_limit_per_second — the user's own flow-control knob,
        set at trigger time (see MigrationRunViewSet.trigger) — falling back
        to the mapping's connections' own configured rate_limit_per_second
        (set on a connection's edit page) when the run didn't set one of its
        own, so a rate-limited API stays protected by default. Null/no limit
        anywhere in that chain means unthrottled."""
        nonlocal last_request_at
        limit = run.rate_limit_per_second or mapping.default_rate_limit_per_second
        if not limit:
            return
        min_interval = 1.0 / limit
        now = time.monotonic()
        if last_request_at is not None:
            wait = min_interval - (now - last_request_at)
            if wait > 0:
                time.sleep(wait)
        last_request_at = time.monotonic()

    def note_request():
        run.requests_made += 1
        run.save(update_fields=["requests_made"])

    def target_client_for(connection):
        if connection.id not in target_clients:
            target_clients[connection.id] = ConnectionClient(connection, run=run)
        return target_clients[connection.id]

    def records_for(entity):
        if entity.id not in source_records:
            if run.input_file and entity.source_file:
                # This run brought its own fresh data — same already-built
                # mapping (fields, transforms, entity pairing), different
                # rows, without touching entity.source_file (which stays
                # whatever was uploaded at discovery/mapping time).
                _log(run, f"Reading {entity} from this run's uploaded input file ({run.input_file.name}) ...")
                records = discovery.read_all_records_from_file(run.input_file)
                _log(run, f"Read {len(records)} record(s) from file.")
            elif entity.source_file:
                # No real API behind this entity at all — every row comes
                # straight from the uploaded CSV/XLSX, no HTTP call, no
                # throttling, no requests_made bump (there's no request).
                _log(run, f"Reading {entity} from its uploaded file ({entity.source_file.name}) ...")
                records = discovery.read_all_records_from_source_file(entity)
                _log(run, f"Read {len(records)} record(s) from file.")
            else:
                _log(run, f"Reading {entity} ...")
                throttle()
                response = source_client.get(entity.endpoint_path)
                note_request()
                response.raise_for_status()
                records = discovery.extract_records_from_payload(response.json())
                _log(run, f"Fetched {len(records)} record(s).")
            source_records[entity.id] = records
            run.records_read += len(records)
            run.save(update_fields=["records_read"])
        return source_records[entity.id]

    run_had_step_failure = False  # a step can fail outright (no field mappings, no endpoint_path)
                                   # without ever touching run.records_failed — tracked separately so
                                   # the run-level status below doesn't misreport "success".

    try:
        entity_mappings = mapping.entity_mappings.select_related(
            "source_entity", "target_entity", "target_entity__connection",
        )
        if entity_mapping_ids is not None:
            entity_mappings = entity_mappings.filter(id__in=entity_mapping_ids)
        if not entity_mappings:
            _log(run, "No entity mappings configured — nothing to migrate.", level=MigrationLog.LEVEL_WARNING)

        for entity_mapping in entity_mappings:
            step, _ = RunStepStatus.objects.update_or_create(
                run=run, entity_mapping=entity_mapping,
                defaults={"status": RunStepStatus.STATUS_RUNNING, "started_at": timezone.now()},
            )
            step_written = 0
            step_failed = 0

            try:
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
                    step.status = RunStepStatus.STATUS_FAILED
                    step.error_message = "No field mappings configured."
                    step.records_read = len(records)
                    step.finished_at = timezone.now()
                    step.save()
                    run_had_step_failure = True
                    continue

                if not entity_mapping.target_entity.endpoint_path:
                    # A blank endpoint_path would otherwise silently POST/PUT/... to
                    # the connection's bare base_url for every record — a confusing
                    # 404 with no clue why, instead of a clear, immediate failure.
                    _log(
                        run,
                        f"{entity_mapping.target_entity} has no endpoint_path configured — "
                        f"set one before running this mapping.",
                        level=MigrationLog.LEVEL_ERROR,
                    )
                    step.status = RunStepStatus.STATUS_FAILED
                    step.error_message = f"{entity_mapping.target_entity} has no endpoint_path configured."
                    step.records_read = len(records)
                    step.finished_at = timezone.now()
                    step.save()
                    run_had_step_failure = True
                    continue

                _log(
                    run,
                    f"Writing {len(records)} record(s) from {entity_mapping.source_entity} "
                    f"to {entity_mapping.target_entity} on {target_connection.name} "
                    f"via {entity_mapping.write_method} ...",
                )
                for record in records:
                    out = {}
                    for field_mapping in field_mappings:
                        raw_value = record.get(field_mapping.source_field.name)
                        value = _apply_rules(raw_value, field_mapping.transform_rules)
                        value = _apply_transform(value, field_mapping.transform)
                        value = _coerce_to_field_type(value, field_mapping.target_field.field_type)
                        _assign_nested(out, field_mapping.target_field.name, value)
                    try:
                        throttle()
                        write_response = target_client.request(
                            entity_mapping.write_method, entity_mapping.target_entity.endpoint_path, json=out,
                        )
                        note_request()
                        write_response.raise_for_status()
                        run.records_written += 1
                        step_written += 1
                    except Exception as exc:
                        run.records_failed += 1
                        step_failed += 1
                        _log(
                            run,
                            f"Failed to write record {out} to {target_connection.name}: {exc}",
                            level=MigrationLog.LEVEL_ERROR,
                        )
                    finally:
                        run.save(update_fields=["records_written", "records_failed"])

                step.status = RunStepStatus.STATUS_FAILED if step_failed and not step_written else RunStepStatus.STATUS_SUCCESS
                step.records_read = len(records)
                step.records_written = step_written
                step.records_failed = step_failed
                step.finished_at = timezone.now()
                step.save()
            except Exception as exc:
                step.status = RunStepStatus.STATUS_FAILED
                step.error_message = str(exc)[:500]
                step.records_written = step_written
                step.records_failed = step_failed
                step.finished_at = timezone.now()
                step.save()
                raise

        run.status = (
            MigrationRun.STATUS_FAILED
            if run_had_step_failure or (run.records_failed and not run.records_written)
            else MigrationRun.STATUS_SUCCESS
        )
    except Exception as exc:
        run.status = MigrationRun.STATUS_FAILED
        _log(run, f"Migration aborted: {exc}", level=MigrationLog.LEVEL_ERROR)
    finally:
        run.finished_at = timezone.now()
        run.save()

    return run


def run_migration_in_background(run_id: int, entity_mapping_ids=None):
    """Entry point for a background thread: fetches its own copy of the run
    and always releases the thread's DB connection when done.
    `entity_mapping_ids` is forwarded to run_migration when retrying failed mappings.
    When a retry run finishes successfully, its parent (retry_of) is automatically
    marked retry_resolved=True so the UI can show "resolved by retry" on the original."""
    try:
        run = MigrationRun.objects.select_related("mapping", "mapping__source_connection").get(pk=run_id)
        run_migration(run, entity_mapping_ids=entity_mapping_ids)
        if run.retry_of_id and run.status == MigrationRun.STATUS_SUCCESS:
            MigrationRun.objects.filter(pk=run.retry_of_id).update(
                retry_resolved=True, status=MigrationRun.STATUS_SUCCESS,
            )
    finally:
        connections.close_all()


def run_batch_in_background(run_ids):
    """Entry point for a background thread running a batch of MigrationRuns
    (see MigrationRunViewSet.trigger_batch — one uploaded file per run, same
    mapping) one at a time, in the given order. Deliberately sequential, not
    one thread per file: each run only throttles itself (see throttle()
    above), so N runs firing in parallel against the same connection would
    multiply straight past whatever rate_limit_per_second it's configured
    with — running them one after another is what keeps the whole batch
    under the connection's real cap, the same reasoning as plans/executor.py's
    sequential step loop."""
    try:
        for run_id in run_ids:
            run = MigrationRun.objects.select_related("mapping", "mapping__source_connection").get(pk=run_id)
            run.status = MigrationRun.STATUS_RUNNING
            run.save(update_fields=["status"])
            run_migration(run)
    finally:
        connections.close_all()
