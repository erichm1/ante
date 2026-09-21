import itertools
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase

from connections.models import Connection
from jobs import engine
from jobs.models import MigrationRun, RunStepStatus
from schemas.models import Entity, Field

from .models import EntityMapping, FieldMapping, Mapping

RECORDS = [{"sku": f"a-{i}", "qty": str(i), "note": "x", "unused": "u"} for i in range(1, 13)]


class PreviewTestBase(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_user("tester", password="pw"))
        src = Connection.objects.create(name="Src", base_url="https://src.example.com", auth_type=Connection.AUTH_NONE)
        dst = Connection.objects.create(name="Dst", base_url="https://dst.example.com", auth_type=Connection.AUTH_NONE)
        self.source = Entity.objects.create(connection=src, name="Items", endpoint_path="/items")
        self.f = {n: Field.objects.create(entity=self.source, name=n, field_type=t)
                  for n, t in [("sku", "string"), ("qty", "string"), ("note", "string"), ("unused", "string")]}
        self.target = Entity.objects.create(connection=dst, name="Products", endpoint_path="/products")
        self.t = {n: Field.objects.create(entity=self.target, name=n, field_type=t)
                  for n, t in [("code", "string"), ("stock", "integer"), ("meta.note", "string")]}
        self.mapping = Mapping.objects.create(name="M", source_connection=src)
        self.mapping.destination_connections.add(dst)
        self.pair = EntityMapping.objects.create(mapping=self.mapping, source_entity=self.source, target_entity=self.target)
        # sku -> code (uppercased), qty -> stock (coerced to int), note -> meta.note (untouched, nested)
        FieldMapping.objects.create(entity_mapping=self.pair, source_field=self.f["sku"], target_field=self.t["code"], transform_rules=[{"op": "uppercase"}])
        FieldMapping.objects.create(entity_mapping=self.pair, source_field=self.f["qty"], target_field=self.t["stock"])
        FieldMapping.objects.create(entity_mapping=self.pair, source_field=self.f["note"], target_field=self.t["meta.note"])

    def preview(self, records=RECORDS, query=""):
        with mock.patch("mappings.preview.discovery.read_all_records", return_value=records) as read:
            body = self.client.get(f"/api/mappings/{self.mapping.pk}/preview/{query}").json()
        return body, read


class PreviewEndpointTests(PreviewTestBase):
    def test_returns_input_rows_and_the_payloads_a_run_would_write(self):
        body, _ = self.preview()
        pair = body["pairs"][0]

        self.assertEqual(pair["rows"][0]["sku"], "a-1")
        self.assertEqual(pair["output"][0], {"code": "A-1", "stock": 1, "meta.note": "x"})
        self.assertEqual(pair["payloads"][0], {"code": "A-1", "stock": 1, "meta": {"note": "x"}})
        self.assertEqual((pair["write_method"], pair["target_endpoint"]), ("POST", "/products"))

    def test_samples_ten_by_default_but_reports_the_true_total(self):
        pair = self.preview()[0]["pairs"][0]
        self.assertEqual((pair["sample_size"], pair["total"], len(pair["rows"])), (10, 12, 10))

    def test_limit_is_honoured_and_clamped(self):
        self.assertEqual(self.preview(query="?limit=3")[0]["pairs"][0]["sample_size"], 3)
        self.assertEqual(self.preview(query="?limit=9999")[0]["limit"], 50)
        self.assertEqual(self.preview(query="?limit=junk")[0]["limit"], 10)

    def test_flags_only_the_cells_a_transform_actually_changed(self):
        pair = self.preview()[0]["pairs"][0]
        # code was uppercased; stock is only type-coerced and note passes straight through
        self.assertEqual(pair["changed"][0], ["code"])

    def test_a_rule_that_leaves_the_value_alone_is_not_flagged(self):
        self.preview()  # warm-up so the fixture is exercised twice with different data
        body, _ = self.preview(records=[{"sku": "ALREADY", "qty": "1", "note": "x"}])
        self.assertEqual(body["pairs"][0]["changed"][0], [])

    def test_says_which_source_column_each_output_column_came_from(self):
        pair = self.preview()[0]["pairs"][0]
        self.assertEqual(pair["source_of"], {"code": "sku", "stock": "qty", "meta.note": "note"})

    def test_lists_which_source_columns_are_mapped_and_keeps_unmapped_ones_visible(self):
        pair = self.preview()[0]["pairs"][0]
        self.assertEqual(pair["mapped_source"], ["note", "qty", "sku"])
        self.assertIn("unused", pair["source_columns"])          # shown, so the user sees what is dropped
        self.assertNotIn("unused", pair["mapped_source"])

    def test_an_unreadable_source_reports_the_error_instead_of_failing_the_request(self):
        with mock.patch("mappings.preview.discovery.read_all_records", side_effect=RuntimeError("connection refused")):
            resp = self.client.get(f"/api/mappings/{self.mapping.pk}/preview/")
        self.assertEqual(resp.status_code, 200)
        pair = resp.json()["pairs"][0]
        self.assertIn("connection refused", pair["error"])
        self.assertEqual(pair["rows"], [])

    def test_a_source_that_fans_out_to_two_targets_is_read_once(self):
        other = Entity.objects.create(connection=self.target.connection, name="Other", endpoint_path="/other")
        code = Field.objects.create(entity=other, name="code", field_type="string")
        pair2 = EntityMapping.objects.create(mapping=self.mapping, source_entity=self.source, target_entity=other)
        FieldMapping.objects.create(entity_mapping=pair2, source_field=self.f["sku"], target_field=code)

        body, read = self.preview()

        self.assertEqual(len(body["pairs"]), 2)
        self.assertEqual(read.call_count, 1)

    def test_never_calls_a_target_system(self):
        with mock.patch("connections.client.ConnectionClient.request") as request:
            self.preview()
        request.assert_not_called()

    def test_pair_without_field_mappings_previews_input_with_empty_output(self):
        self.pair.field_mappings.all().delete()
        pair = self.preview()[0]["pairs"][0]
        self.assertEqual(pair["output"][0], {})
        self.assertEqual(pair["rows"][0]["sku"], "a-1")


class PreviewMatchesARealRunTests(PreviewTestBase):
    """The preview shares engine.build_payload with run_migration, so the
    bodies it shows must be byte-for-byte what a real run sends."""

    def test_payloads_shown_equal_payloads_written(self):
        sent = []

        class FakeClient:
            def __init__(self, connection, run=None):
                self.connection = connection

            def get(self, path, **kw):
                return mock.Mock(raise_for_status=lambda: None, json=lambda: RECORDS[:10])

            def request(self, method, path, **kw):
                sent.append(kw["json"])
                return mock.Mock(raise_for_status=lambda: None)

        run = MigrationRun.objects.create(mapping=self.mapping, status=MigrationRun.STATUS_RUNNING)
        with mock.patch.object(engine, "ConnectionClient", FakeClient):
            engine.run_migration(run)
        previewed = self.preview(records=RECORDS[:10])[0]["pairs"][0]["payloads"]

        self.assertEqual(run.records_written, 10)
        self.assertEqual(sent, previewed)


class LivePairCountsTests(PreviewTestBase):
    """While a run is in flight each pair's own counts must move (the Studio's
    per-entity meters read them), not sit at zero until the pair finishes."""

    def test_pair_counts_advance_during_the_run(self):
        seen = []

        class FakeClient:
            def __init__(self, connection, run=None):
                pass

            def get(self, path, **kw):
                return mock.Mock(raise_for_status=lambda: None, json=lambda: RECORDS[:6])

            def request(self, method, path, **kw):
                # What a poller would see in the DB at the moment of this write.
                seen.append(tuple(RunStepStatus.objects.values_list("records_read", "records_written").get()))
                return mock.Mock(raise_for_status=lambda: None)

        run = MigrationRun.objects.create(mapping=self.mapping, status=MigrationRun.STATUS_RUNNING)
        ticks = itertools.count(step=1)          # every record is "a second later", so every record flushes
        with mock.patch.object(engine, "ConnectionClient", FakeClient), \
                mock.patch.object(engine.time, "monotonic", side_effect=lambda: next(ticks)):
            engine.run_migration(run)

        self.assertEqual(seen[0][0], 6)                              # read count is published before any write
        self.assertEqual([w for _, w in seen], [0, 1, 2, 3, 4, 5])   # written climbs one at a time
        final = RunStepStatus.objects.get()
        self.assertEqual((final.records_read, final.records_written, final.records_failed), (6, 6, 0))


class KillMigrationTests(PreviewTestBase):
    """The Kill button: a running migration stops between records (written records stay written),
    a queued/orphaned run is closed straight away."""

    def fake_client(self, kill_after):
        writes = []
        test = self

        class FakeClient:
            def __init__(self, connection, run=None):
                self.run = run

            def get(self, path, **kw):
                return mock.Mock(raise_for_status=lambda: None, json=lambda: RECORDS[:10])

            def request(self, method, path, **kw):
                writes.append(kw["json"])
                if len(writes) == kill_after:
                    MigrationRun.objects.filter(pk=test.run.pk).update(cancel_requested=True)      # the Kill arrives mid-run
                return mock.Mock(raise_for_status=lambda: None)

        return FakeClient, writes

    def start(self, **fake):
        self.run = MigrationRun.objects.create(mapping=self.mapping, status=MigrationRun.STATUS_RUNNING)
        client, writes = self.fake_client(**fake)
        with mock.patch.object(engine, "ConnectionClient", client), mock.patch.object(engine, "CANCEL_CHECK_SECONDS", 0):
            engine.run_migration(self.run)
        self.run.refresh_from_db()
        return writes

    def test_a_running_migration_stops_between_records_and_is_marked_cancelled(self):
        writes = self.start(kill_after=3)
        self.assertEqual(self.run.status, MigrationRun.STATUS_CANCELLED)
        self.assertEqual((len(writes), self.run.records_written), (3, 3))            # 7 of 10 records never sent; the 3 written stay written
        self.assertIsNotNone(self.run.finished_at)

    def test_the_log_and_the_pair_record_that_it_was_cancelled(self):
        self.start(kill_after=2)
        self.assertTrue(self.run.logs.filter(message__contains="cancelled by user", level="warning").exists())
        step = RunStepStatus.objects.get(run=self.run)
        self.assertEqual((step.status, step.error_message), ("failed", "Cancelled by user."))

    def test_the_run_is_no_longer_tracked_as_active_afterwards(self):
        self.start(kill_after=1)
        self.assertNotIn(self.run.pk, engine.ACTIVE_RUNS)

    def test_a_run_that_is_not_killed_is_unaffected(self):
        writes = self.start(kill_after=999)
        self.assertEqual((self.run.status, len(writes)), (MigrationRun.STATUS_SUCCESS, 10))

    def test_cancel_runs_closes_queued_and_orphaned_runs_but_only_flags_a_live_one(self):
        queued = MigrationRun.objects.create(mapping=self.mapping, status=MigrationRun.STATUS_PENDING)
        orphan = MigrationRun.objects.create(mapping=self.mapping, status=MigrationRun.STATUS_RUNNING)      # its thread is gone
        live = MigrationRun.objects.create(mapping=self.mapping, status=MigrationRun.STATUS_RUNNING)
        engine.ACTIVE_RUNS.add(live.pk)
        self.addCleanup(engine.ACTIVE_RUNS.discard, live.pk)

        self.assertEqual(engine.cancel_runs(MigrationRun.objects.filter(pk__in=[queued.pk, orphan.pk, live.pk])), 3)

        for r in (queued, orphan, live):
            r.refresh_from_db()
        self.assertEqual((queued.status, orphan.status), ("cancelled", "cancelled"))
        self.assertIsNotNone(queued.finished_at)
        self.assertEqual((live.status, live.cancel_requested), ("running", True))          # it will stop itself at the next check

    def test_run_cancel_endpoint(self):
        run = MigrationRun.objects.create(mapping=self.mapping, status=MigrationRun.STATUS_PENDING)
        self.assertEqual(self.client.post(f"/api/runs/{run.pk}/cancel/").json()["status"], "cancelled")
        again = self.client.post(f"/api/runs/{run.pk}/cancel/")
        self.assertEqual(again.status_code, 400)
        self.assertIn("nothing to stop", again.json()["error"])

    def test_a_cancelled_run_can_be_retried_like_a_failed_one(self):
        run = MigrationRun.objects.create(mapping=self.mapping, status=MigrationRun.STATUS_CANCELLED)
        RunStepStatus.objects.create(run=run, entity_mapping=self.pair, status="failed")
        with mock.patch("jobs.views.threading.Thread"):
            self.assertEqual(self.client.post(f"/api/runs/{run.pk}/retry/").status_code, 201)

    def test_mapping_kill_stops_running_and_queued_runs_but_leaves_scheduled_ones(self):
        running = MigrationRun.objects.create(mapping=self.mapping, status=MigrationRun.STATUS_RUNNING)
        queued = MigrationRun.objects.create(mapping=self.mapping, status=MigrationRun.STATUS_PENDING)
        scheduled = MigrationRun.objects.create(mapping=self.mapping, status=MigrationRun.STATUS_PENDING, scheduled_at="2099-01-01T00:00:00Z")
        self.assertEqual(self.client.post(f"/api/mappings/{self.mapping.pk}/cancel/").json(), {"cancelled": 2})
        for r in (running, queued, scheduled):
            r.refresh_from_db()
        self.assertEqual((running.status, queued.status, scheduled.status), ("cancelled", "cancelled", "pending"))

    def test_a_batch_skips_a_run_that_was_killed_while_it_waited_its_turn(self):
        first = MigrationRun.objects.create(mapping=self.mapping, status=MigrationRun.STATUS_PENDING)
        second = MigrationRun.objects.create(mapping=self.mapping, status=MigrationRun.STATUS_CANCELLED)
        with mock.patch.object(engine, "run_migration") as migrate:
            engine.run_batch_in_background([first.pk, second.pk])
        self.assertEqual([c.args[0].pk for c in migrate.call_args_list], [first.pk])
