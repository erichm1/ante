from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from chains.models import CallChain, CallChainRun, CallChainStep
from connections.models import Connection
from jobs.models import MigrationRun
from mappings.models import EntityMapping, Mapping
from schemas.models import Entity

from . import executor
from .models import MigrationPlan, PlanStep


class PlanTestBase(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_user("tester", password="pw"))
        conn = Connection.objects.create(name="Conn", base_url="https://x.example.com", auth_type=Connection.AUTH_NONE)
        self.mapping = Mapping.objects.create(name="M", source_connection=conn)
        self.chain = CallChain.objects.create(name="C", connection=conn)

    def plan(self, mode):
        return MigrationPlan.objects.create(name=f"{mode} plan", execution_mode=mode)

    def add(self, plan, **body):
        return self.client.post(f"/api/plans/{plan.pk}/steps/", body, content_type="application/json")


class AddStepTests(PlanTestBase):
    def test_mixed_plan_takes_mappings_and_chains_in_any_order(self):
        plan = self.plan(MigrationPlan.MODE_MIXED)
        self.assertEqual(self.add(plan, mapping_id=self.mapping.pk).status_code, 201)
        self.assertEqual(self.add(plan, chain_id=self.chain.pk).status_code, 201)
        self.assertEqual(self.add(plan, mapping_id=self.mapping.pk).status_code, 201)

        steps = list(plan.steps.order_by("order"))
        self.assertEqual([s.order for s in steps], [1, 2, 3])
        self.assertEqual([bool(s.chain_id) for s in steps], [False, True, False])

    def test_simple_plan_still_only_takes_mappings(self):
        plan = self.plan(MigrationPlan.MODE_SIMPLE)
        self.assertEqual(self.add(plan, mapping_id=self.mapping.pk).status_code, 201)
        # chain_id is ignored in simple mode, so no mapping is found — same 404 as before.
        self.assertEqual(self.add(plan, chain_id=self.chain.pk).status_code, 404)

    def test_chain_plan_still_only_takes_chains(self):
        plan = self.plan(MigrationPlan.MODE_CHAIN)
        self.assertEqual(self.add(plan, chain_id=self.chain.pk).status_code, 201)
        self.assertEqual(self.add(plan, mapping_id=self.mapping.pk).status_code, 404)

    def test_can_create_a_mixed_plan_through_the_api(self):
        resp = self.client.post("/api/plans/", {"name": "P", "execution_mode": "mixed"}, content_type="application/json")
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.json()["execution_mode"], "mixed")

    def test_step_serializer_exposes_chain_run_finished_at(self):
        plan = self.plan(MigrationPlan.MODE_MIXED)
        step = PlanStep.objects.create(plan=plan, chain=self.chain, order=1)
        run = CallChainRun.objects.create(chain=self.chain, status=CallChainRun.STATUS_FAILED)
        step.chain_run = run
        step.save()

        body = self.client.get(f"/api/plans/{plan.pk}/").json()
        self.assertIsNone(body["steps"][0]["chain_run"]["finished_at"])  # still running


class ExecutorTests(PlanTestBase):
    """The executor dispatches on each step's own target, so one loop covers
    simple, chain and mixed plans alike."""

    def run_plan(self, plan, migration_status, chain_status):
        def fake_migration(run, entity_mapping_ids=None):
            run.status = migration_status
            run.save()

        def fake_chain(chain_run):
            chain_run.status = chain_status
            chain_run.finished_at = timezone.now()
            chain_run.save()

        with mock.patch("plans.executor.engine.run_migration", side_effect=fake_migration) as mig, \
                mock.patch("plans.executor.chains_executor.run_chain", side_effect=fake_chain) as chn:
            executor.execute_plan_in_background(plan.pk)
        plan.refresh_from_db()
        return mig, chn

    def test_mixed_plan_runs_each_step_by_its_kind_in_order(self):
        plan = self.plan(MigrationPlan.MODE_MIXED)
        PlanStep.objects.create(plan=plan, mapping=self.mapping, order=1)
        PlanStep.objects.create(plan=plan, chain=self.chain, order=2)
        PlanStep.objects.create(plan=plan, mapping=self.mapping, order=3)

        mig, chn = self.run_plan(plan, MigrationRun.STATUS_SUCCESS, CallChainRun.STATUS_SUCCESS)

        self.assertEqual(mig.call_count, 2)
        self.assertEqual(chn.call_count, 1)
        self.assertEqual(plan.status, MigrationPlan.STATUS_COMPLETED)
        steps = list(plan.steps.order_by("order"))
        self.assertIsNotNone(steps[0].run)
        self.assertIsNotNone(steps[1].chain_run)
        self.assertIsNone(steps[1].run)
        self.assertIsNotNone(steps[2].run)

    def test_a_failing_chain_step_fails_the_plan_but_later_steps_still_run(self):
        plan = self.plan(MigrationPlan.MODE_MIXED)
        PlanStep.objects.create(plan=plan, chain=self.chain, order=1)
        PlanStep.objects.create(plan=plan, mapping=self.mapping, order=2)

        mig, chn = self.run_plan(plan, MigrationRun.STATUS_SUCCESS, CallChainRun.STATUS_FAILED)

        self.assertEqual(plan.status, MigrationPlan.STATUS_FAILED)
        self.assertEqual(mig.call_count, 1)

    def test_step_rate_limit_overrides_plan_default(self):
        plan = self.plan(MigrationPlan.MODE_SIMPLE)
        plan.rate_limit_per_second = 5
        plan.save()
        PlanStep.objects.create(plan=plan, mapping=self.mapping, order=1, rate_limit_per_second=2)
        PlanStep.objects.create(plan=plan, mapping=self.mapping, order=2)

        self.run_plan(plan, MigrationRun.STATUS_SUCCESS, CallChainRun.STATUS_SUCCESS)

        self.assertEqual([s.run.rate_limit_per_second for s in plan.steps.order_by("order")], [2, 5])

    def test_legacy_chain_mode_plan_still_runs(self):
        plan = self.plan(MigrationPlan.MODE_CHAIN)
        PlanStep.objects.create(plan=plan, chain=self.chain, order=1)

        mig, chn = self.run_plan(plan, MigrationRun.STATUS_SUCCESS, CallChainRun.STATUS_SUCCESS)

        self.assertEqual((mig.call_count, chn.call_count), (0, 1))
        self.assertEqual(plan.status, MigrationPlan.STATUS_COMPLETED)


class ClassicDetailPageTests(PlanTestBase):
    def test_mixed_plan_redirects_to_the_studio(self):
        plan = self.plan(MigrationPlan.MODE_MIXED)
        resp = self.client.get(f"/plans/{plan.pk}/")
        self.assertRedirects(resp, f"/studio/?open=plan:{plan.pk}", fetch_redirect_response=False)

    def test_simple_plan_still_renders_the_classic_page(self):
        plan = self.plan(MigrationPlan.MODE_SIMPLE)
        self.assertEqual(self.client.get(f"/plans/{plan.pk}/").status_code, 200)


class CustomRunTestBase(PlanTestBase):
    def setUp(self):
        super().setUp()
        e = Entity.objects.create(connection=self.mapping.source_connection, name="E", endpoint_path="/e")
        self.pairs = [EntityMapping.objects.create(mapping=self.mapping, source_entity=e, target_entity=Entity.objects.create(connection=self.mapping.source_connection, name=f"T{i}", endpoint_path=f"/t{i}")) for i in range(3)]
        self.plan_ = self.plan(MigrationPlan.MODE_MIXED)

    def add_step(self, **body):
        return self.client.post(f"/api/plans/{self.plan_.pk}/steps/", body, content_type="application/json")

    def order(self):
        return [(s.order, s.kind) for s in self.plan_.steps.order_by("order")]


class WaitAndSubsetStepTests(CustomRunTestBase):
    def test_a_wait_step_needs_no_mapping_or_chain(self):
        resp = self.add_step(wait_seconds=30)
        self.assertEqual(resp.status_code, 201)
        body = resp.json()
        self.assertEqual((body["kind"], body["wait_seconds"], body["mapping"], body["chain"]), ("wait", 30, None, None))

    def test_wait_steps_are_only_for_mixed_plans_and_are_bounded(self):
        self.assertEqual(self.client.post(f"/api/plans/{self.plan(MigrationPlan.MODE_SIMPLE).pk}/steps/", {"wait_seconds": 5}, content_type="application/json").status_code, 400)
        for bad in (-1, 99999, "soon"):
            self.assertEqual(self.add_step(wait_seconds=bad).status_code, 400, bad)

    def test_a_mapping_step_can_run_only_some_entity_pairs(self):
        body = self.add_step(mapping_id=self.mapping.pk, entity_mapping_ids=[self.pairs[2].pk, self.pairs[0].pk]).json()
        self.assertEqual((body["entity_mapping_ids"], body["pairs_total"]), (sorted([self.pairs[0].pk, self.pairs[2].pk]), 3))

    def test_choosing_every_pair_means_the_whole_mapping(self):
        body = self.add_step(mapping_id=self.mapping.pk, entity_mapping_ids=[p.pk for p in self.pairs]).json()
        self.assertEqual(body["entity_mapping_ids"], [])

    def test_pairs_from_another_mapping_are_rejected(self):
        other = Mapping.objects.create(name="Other", source_connection=self.mapping.source_connection)
        foreign = EntityMapping.objects.create(mapping=other, source_entity=self.pairs[0].source_entity, target_entity=self.pairs[0].target_entity)
        resp = self.add_step(mapping_id=self.mapping.pk, entity_mapping_ids=[foreign.pk])
        self.assertEqual(resp.status_code, 400)
        self.assertIn("belong to this mapping", resp.json()["error"])

    def test_wait_seconds_and_pairs_can_be_edited_on_the_right_kind_of_step_only(self):
        wait = self.add_step(wait_seconds=5).json()
        mapped = self.add_step(mapping_id=self.mapping.pk).json()
        patch = lambda step, body: self.client.patch(f"/api/plan-steps/{step['id']}/", body, content_type="application/json")
        self.assertEqual(patch(wait, {"wait_seconds": 45}).json()["wait_seconds"], 45)
        self.assertEqual(patch(mapped, {"wait_seconds": 45}).status_code, 400)
        self.assertEqual(patch(mapped, {"entity_mapping_ids": [self.pairs[1].pk]}).json()["entity_mapping_ids"], [self.pairs[1].pk])
        self.assertEqual(patch(wait, {"entity_mapping_ids": [1]}).status_code, 400)


class PlanOrderingTests(CustomRunTestBase):
    def test_position_inserts_and_pushes_the_rest_down(self):
        a = self.add_step(mapping_id=self.mapping.pk).json()
        b = self.add_step(chain_id=self.chain.pk).json()
        self.add_step(wait_seconds=1, position=2)
        self.assertEqual(self.order(), [(1, "mapping"), (2, "wait"), (3, "chain")])
        self.assertEqual(self.plan_.steps.get(pk=b["id"]).order, 3)

    def test_a_deleted_middle_step_no_longer_breaks_adding(self):
        first = self.add_step(mapping_id=self.mapping.pk).json()
        self.add_step(mapping_id=self.mapping.pk)
        self.add_step(mapping_id=self.mapping.pk)
        self.client.delete(f"/api/plan-steps/{self.plan_.steps.get(order=2).pk}/")           # leaves orders 1, 3
        resp = self.add_step(wait_seconds=1)                                                  # used to collide on order 3
        self.assertEqual(resp.status_code, 201)
        self.assertEqual([o for o, _ in self.order()], [1, 3, 4])

    def test_reorder_sets_the_whole_sequence_atomically(self):
        ids = [self.add_step(mapping_id=self.mapping.pk).json()["id"], self.add_step(chain_id=self.chain.pk).json()["id"], self.add_step(wait_seconds=1).json()["id"]]
        resp = self.client.post(f"/api/plans/{self.plan_.pk}/reorder/", {"order": [ids[2], ids[0], ids[1]]}, content_type="application/json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.order(), [(1, "wait"), (2, "mapping"), (3, "chain")])

    def test_reorder_must_name_every_step_exactly_once(self):
        ids = [self.add_step(wait_seconds=1).json()["id"], self.add_step(wait_seconds=2).json()["id"]]
        for bad in ([ids[0]], [ids[0], ids[0]], [ids[0], 9999], "nope"):
            resp = self.client.post(f"/api/plans/{self.plan_.pk}/reorder/", {"order": bad}, content_type="application/json")
            self.assertEqual(resp.status_code, 400, bad)
        self.assertEqual([o for o, _ in self.order()], [1, 2])                            # untouched


class PlanEditLockTests(CustomRunTestBase):
    def test_a_finished_plan_can_be_edited_and_run_again_but_an_executing_one_cannot(self):
        for status_, allowed in [("draft", True), ("completed", True), ("failed", True), ("executing", False), ("scheduled", False)]:
            with self.subTest(status=status_):
                MigrationPlan.objects.filter(pk=self.plan_.pk).update(status=status_)
                resp = self.add_step(wait_seconds=1)
                self.assertEqual(resp.status_code, 201 if allowed else 400)
                if not allowed:
                    self.assertIn("executing or scheduled", resp.json()["error"])


class CustomRunExecutionTests(CustomRunTestBase):
    def execute(self, **patches):
        calls = []

        def fake_migration(run, entity_mapping_ids=None):
            calls.append(("mapping", entity_mapping_ids))
            run.status = MigrationRun.STATUS_SUCCESS
            run.save()

        def fake_chain(chain_run):
            calls.append(("chain",))
            chain_run.status = CallChainRun.STATUS_SUCCESS
            chain_run.finished_at = timezone.now()
            chain_run.save()

        with mock.patch("plans.executor.engine.run_migration", side_effect=fake_migration), \
                mock.patch("plans.executor.chains_executor.run_chain", side_effect=fake_chain), \
                mock.patch("plans.executor.time.sleep", side_effect=lambda s: calls.append(("sleep", s))):
            executor.execute_plan_in_background(self.plan_.pk)
        self.plan_.refresh_from_db()
        # A pause sleeps in short slices (so a kill lands promptly); fold them back into one entry for readability.
        merged = []
        for call in calls:
            if merged and call[0] == "sleep" and merged[-1][0] == "sleep":
                merged[-1] = ("sleep", merged[-1][1] + call[1])
            else:
                merged.append(call)
        return merged

    def test_runs_steps_in_order_sleeping_and_running_only_the_chosen_pairs(self):
        self.add_step(mapping_id=self.mapping.pk, entity_mapping_ids=[self.pairs[1].pk])
        self.add_step(wait_seconds=12)
        self.add_step(chain_id=self.chain.pk)
        self.add_step(mapping_id=self.mapping.pk)
        calls = self.execute()
        self.assertEqual(calls, [("mapping", [self.pairs[1].pk]), ("sleep", 12), ("chain",), ("mapping", None)])
        self.assertEqual(self.plan_.status, MigrationPlan.STATUS_COMPLETED)

    def test_a_wait_step_records_that_it_waited(self):
        self.add_step(wait_seconds=3)
        self.execute()
        step = self.plan_.steps.get()
        self.assertIsNotNone(step.started_at)
        self.assertIsNotNone(step.finished_at)

    def test_pause_is_capped(self):
        self.add_step(wait_seconds=3600)
        PlanStep.objects.update(wait_seconds=10 ** 6)          # bypass the API's own limit
        self.assertEqual(self.execute(), [("sleep", executor.MAX_WAIT_SECONDS)])

    def test_reexecuting_starts_from_a_clean_slate(self):
        step = self.add_step(mapping_id=self.mapping.pk).json()
        stale = MigrationRun.objects.create(mapping=self.mapping, status=MigrationRun.STATUS_FAILED)
        PlanStep.objects.filter(pk=step["id"]).update(run=stale)
        seen = []
        with mock.patch("plans.executor.engine.run_migration", side_effect=lambda run, entity_mapping_ids=None: seen.append(PlanStep.objects.get(pk=step["id"]).run_id)):
            executor.execute_plan_in_background(self.plan_.pk)
        self.assertNotEqual(seen[0], stale.pk)               # the old run was cleared before the new one started


# ═══ Function blocks in a custom run ═════════════════════════════════════════════════════════════
class FakeResponse:
    def __init__(self, body=None, status=200, headers=None):
        self.status_code, self._body, self.headers = status, body, headers or {}
        self.content = b"x" if body is not None else b""

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"{self.status_code} error")


class FakeClient:
    routes, calls = {}, []

    def __init__(self, connection, run=None):
        self.connection = connection

    def request(self, method, path, **kwargs):
        FakeClient.calls.append((self.connection.name, method, path))
        return FakeClient.routes[(self.connection.name, path)]


class FunctionBlockTests(CustomRunTestBase):
    def setUp(self):
        super().setUp()
        self.conn = self.mapping.source_connection
        self.other = Connection.objects.create(name="Other", base_url="https://o.example.com", auth_type=Connection.AUTH_NONE)
        FakeClient.routes, FakeClient.calls = {}, []
        patcher = mock.patch("chains.executor.ConnectionClient", FakeClient)
        patcher.start()
        self.addCleanup(patcher.stop)

    def fn(self, **body):
        position = body.pop("position", None)
        payload = {"function": body}
        if position:
            payload["position"] = position
        return self.add_step(**payload)

    def get(self, name, path, conn=None, **kw):
        return self.fn(kind="http", name=name, method="GET", path=path, connection=(conn or self.conn).pk, **kw)

    def test_adding_a_function_creates_a_hidden_inline_chain_shared_by_all_blocks(self):
        first = self.get("list", "/list")
        second = self.fn(kind="find", name="look", params={"source": "list", "match_field": "id", "value": "1"})
        self.assertEqual((first.status_code, second.status_code), (201, 201))
        self.plan_.refresh_from_db()
        self.assertTrue(self.plan_.inline_chain.hidden)
        self.assertEqual(self.plan_.inline_chain.steps.count(), 2)
        self.assertEqual(first.json()["kind"], "function")
        self.assertEqual(first.json()["function"]["connection"], self.conn.pk)

    def test_a_hidden_chain_is_not_a_chain_of_its_own(self):
        self.get("list", "/list")
        self.assertEqual([c["name"] for c in self.client.get("/studio/tree/").json()["chains"]], ["C"])
        self.assertEqual({c["name"] for c in self.client.get("/api/chains/").json()["results"]}, {"C"})

    def test_function_blocks_need_a_mixed_plan_and_a_request_needs_a_connection(self):
        simple = self.plan(MigrationPlan.MODE_SIMPLE)
        resp = self.client.post(f"/api/plans/{simple.pk}/steps/", {"function": {"kind": "wait", "name": "w", "params": {"seconds": 1}}}, content_type="application/json")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(self.fn(kind="http", name="x", path="/x").status_code, 400)          # no connection chosen
        self.assertFalse(self.plan_.steps.exists())                                            # nothing half-created

    def test_validation_errors_leave_no_trace(self):
        self.get("list", "/list")
        for body, expected in [
            ({"kind": "find", "name": "look", "params": {"source": ""}}, "which list"),
            ({"kind": "check", "name": "chk", "params": {"from_step": "ghost", "header": "X"}}, "isn't an earlier step"),
            ({"kind": "http", "name": "list", "path": "/again", "connection": self.conn.pk}, "already used"),
        ]:
            resp = self.fn(**body)
            self.assertEqual(resp.status_code, 400, body)
            self.assertIn(expected, resp.json()["error"])
        self.assertEqual(self.plan_.steps.count(), 1)

    def test_a_block_can_only_read_blocks_that_run_before_it(self):
        self.get("a", "/a")
        self.fn(kind="wait", name="w", params={"seconds": 1})
        # insert a check at position 1: "a" does not run before it any more
        resp = self.fn(kind="check", name="chk", params={"from_step": "a", "header": "X"}, position=1)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(self.fn(kind="check", name="chk", params={"from_step": "a", "header": "X"}).status_code, 201)

    def test_blocks_share_data_in_plan_order_across_connections_and_mapping_steps(self):
        FakeClient.routes = {
            ("Conn", "/products"): FakeResponse({"items": [{"sku": "a", "id": 1}, {"sku": "b", "id": 2}]}, headers={"X-Total": "2"}),
            ("Other", "/products/2"): FakeResponse({"detail": True}),
        }
        self.get("list", "/products")
        self.fn(kind="check", name="has_total", params={"from_step": "list", "header": "X-Total", "operator": "exists"})
        self.fn(kind="find", name="look", params={"source": "list.items", "match_field": "sku", "value": "b"}, captures=[{"name": "pid", "path": "id"}])
        self.add_step(mapping_id=self.mapping.pk)
        self.get("detail", "/products/{{pid}}", conn=self.other)
        with mock.patch("plans.executor.engine.run_migration", side_effect=lambda run, entity_mapping_ids=None: setattr(run, "status", "success") or run.save()):
            executor.execute_plan_in_background(self.plan_.pk)
        self.plan_.refresh_from_db()
        self.assertEqual(self.plan_.status, MigrationPlan.STATUS_COMPLETED)
        self.assertEqual(FakeClient.calls, [("Conn", "GET", "/products"), ("Other", "GET", "/products/2")])   # detail used the FOUND id, on ITS connection
        body = self.client.get(f"/api/plans/{self.plan_.pk}/").json()
        self.assertEqual([s["result"]["kind"] if s["result"] else None for s in body["steps"]], ["http", "check", "find", None, "http"])
        self.assertTrue(all(s["finished_at"] for s in body["steps"] if s["kind"] == "function"))
        self.assertEqual(self.plan_.inline_run.status, "success")

    def test_a_failing_block_stops_the_run_and_later_steps_do_not_execute(self):
        FakeClient.routes = {("Conn", "/list"): FakeResponse({"items": []})}
        self.get("list", "/list")
        self.fn(kind="find", name="look", params={"source": "list.items", "match_field": "id", "value": "1"})
        self.add_step(mapping_id=self.mapping.pk)
        with mock.patch("plans.executor.engine.run_migration") as migrate:
            executor.execute_plan_in_background(self.plan_.pk)
        self.plan_.refresh_from_db()
        self.assertEqual(self.plan_.status, MigrationPlan.STATUS_FAILED)
        migrate.assert_not_called()
        self.assertIn("No item in", self.plan_.inline_run.step_results.get(name="look").error)
        self.assertEqual(self.plan_.inline_run.status, "failed")

    def test_editing_and_removing_a_block_goes_through_the_plan_step(self):
        step = self.fn(kind="wait", name="w", params={"seconds": 3}).json()
        patched = self.client.patch(f"/api/plan-steps/{step['id']}/", {"function": {"params": {"seconds": 9}}}, content_type="application/json")
        self.assertEqual(patched.json()["function"]["params"], {"seconds": 9.0})
        self.assertEqual(self.client.patch(f"/api/plan-steps/{step['id']}/", {"function": {"params": {"seconds": 99999}}}, content_type="application/json").status_code, 400)
        self.assertEqual(self.client.delete(f"/api/plan-steps/{step['id']}/").status_code, 204)
        self.assertFalse(CallChainStep.objects.filter(chain=self.plan_.inline_chain).exists())          # the block itself went too

    def test_deleting_the_plan_deletes_its_hidden_chain(self):
        self.get("list", "/list")
        chain_id = MigrationPlan.objects.get(pk=self.plan_.pk).inline_chain_id
        self.assertEqual(self.client.delete(f"/api/plans/{self.plan_.pk}/").status_code, 204)
        self.assertFalse(CallChain.objects.filter(pk=chain_id).exists())

    def test_a_request_block_in_a_custom_run_sends_headers_and_params_on_its_own_connection(self):
        seen = []

        class Recording(FakeClient):
            def request(self, method, path, **kwargs):
                seen.append((self.connection.name, path, kwargs.get("headers"), kwargs.get("params")))
                return FakeResponse({})

        with mock.patch("chains.executor.ConnectionClient", Recording):
            resp = self.get("list", "/list", headers=[{"name": "X-Auth", "value": "t"}], query_params=[{"name": "limit", "value": "5"}])
            self.assertEqual(resp.status_code, 201)
            self.assertEqual(resp.json()["function"]["headers"], [{"name": "X-Auth", "value": "t"}])
            executor.execute_plan_in_background(self.plan_.pk)
        self.assertEqual(seen, [("Conn", "/list", {"X-Auth": "t"}, {"limit": "5"})])
        result = self.client.get(f"/api/plans/{self.plan_.pk}/").json()["steps"][0]["result"]
        self.assertEqual(result["detail"]["request_headers"], {"X-Auth": "t"})

    def test_editing_a_block_can_change_them(self):
        step = self.get("list", "/list", headers=[{"name": "X-A", "value": "1"}]).json()
        patched = self.client.patch(f"/api/plan-steps/{step['id']}/", {"function": {"headers": [{"name": "X-B", "value": "2"}]}}, content_type="application/json")
        self.assertEqual(patched.json()["function"]["headers"], [{"name": "X-B", "value": "2"}])


# ═══ Kill button ═════════════════════════════════════════════════════════════════════════════════
class KillPlanTests(CustomRunTestBase):
    def cancel(self, plan=None):
        return self.client.post(f"/api/plans/{(plan or self.plan_).pk}/cancel/")

    def test_a_scheduled_plan_is_simply_cancelled(self):
        MigrationPlan.objects.filter(pk=self.plan_.pk).update(status="scheduled", scheduled_at=timezone.now())
        body = self.cancel().json()
        self.assertEqual((body["status"], body["scheduled_at"]), ("cancelled", None))

    def test_there_is_nothing_to_kill_on_an_idle_plan(self):
        resp = self.cancel()
        self.assertEqual(resp.status_code, 400)
        self.assertIn("nothing to stop", resp.json()["error"])

    def test_an_orphaned_executing_plan_is_closed_immediately(self):
        MigrationPlan.objects.filter(pk=self.plan_.pk).update(status="executing")           # no thread is running it here
        self.assertEqual(self.cancel().json()["status"], "cancelled")

    def test_killing_a_running_plan_flags_it_and_the_run_it_is_in_the_middle_of(self):
        step = PlanStep.objects.create(plan=self.plan_, mapping=self.mapping, order=1)
        run = MigrationRun.objects.create(mapping=self.mapping, status=MigrationRun.STATUS_RUNNING)
        PlanStep.objects.filter(pk=step.pk).update(run=run)
        MigrationPlan.objects.filter(pk=self.plan_.pk).update(status="executing")
        executor.ACTIVE_PLANS.add(self.plan_.pk)                                            # pretend its thread is alive
        self.addCleanup(executor.ACTIVE_PLANS.discard, self.plan_.pk)
        from jobs import engine
        engine.ACTIVE_RUNS.add(run.pk)
        self.addCleanup(engine.ACTIVE_RUNS.discard, run.pk)

        self.assertEqual(self.cancel().json()["status"], "executing")                      # still winding down…
        self.plan_.refresh_from_db(); run.refresh_from_db()
        self.assertTrue(self.plan_.cancel_requested)
        self.assertTrue(run.cancel_requested)                                               # …and its current run was told to stop too

    def test_the_executor_stops_before_the_next_step_when_killed(self):
        for _ in range(3):
            self.add_step(mapping_id=self.mapping.pk)
        ran = []

        def migrate(run, entity_mapping_ids=None):
            ran.append(run.pk)
            MigrationPlan.objects.filter(pk=self.plan_.pk).update(cancel_requested=True)     # the Kill arrives during step 1
            run.status = MigrationRun.STATUS_SUCCESS
            run.save()

        with mock.patch("plans.executor.engine.run_migration", side_effect=migrate):
            executor.execute_plan_in_background(self.plan_.pk)
        self.plan_.refresh_from_db()
        self.assertEqual((len(ran), self.plan_.status), (1, "cancelled"))
        self.assertIn(self.plan_.pk, {self.plan_.pk} - executor.ACTIVE_PLANS)               # registry cleaned up

    def test_a_kill_interrupts_a_long_wait(self):
        self.add_step(wait_seconds=600)
        self.add_step(mapping_id=self.mapping.pk)
        slept = []

        def sleep(seconds):
            slept.append(seconds)
            if len(slept) == 3:
                MigrationPlan.objects.filter(pk=self.plan_.pk).update(cancel_requested=True)

        with mock.patch("plans.executor.time.sleep", side_effect=sleep), mock.patch("plans.executor.engine.run_migration") as migrate:
            executor.execute_plan_in_background(self.plan_.pk)
        self.plan_.refresh_from_db()
        self.assertEqual(self.plan_.status, "cancelled")
        self.assertLess(sum(slept), 10)                                                     # gave up after a few slices, not 600s
        migrate.assert_not_called()

    def test_re_executing_a_cancelled_plan_is_allowed_and_clears_the_kill(self):
        MigrationPlan.objects.filter(pk=self.plan_.pk).update(status="cancelled", cancel_requested=True)
        self.add_step(wait_seconds=0)
        with mock.patch("plans.executor.start_plan") as start:
            resp = self.client.post(f"/api/plans/{self.plan_.pk}/execute/", {}, content_type="application/json")
        self.assertEqual(resp.status_code, 200)
        self.plan_.refresh_from_db()
        self.assertFalse(self.plan_.cancel_requested)
        start.assert_called_once()

