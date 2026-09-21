import datetime
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from connections.models import Connection
from schemas.models import Entity, Field

from . import executor
from .models import CallChain, CallChainRun, CallChainStep


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
    """Stands in for ConnectionClient: routes by path, records every call (with kwargs)."""
    routes, calls = {}, []

    def __init__(self, connection, run=None):
        self.connection = connection

    def request(self, method, path, **kwargs):
        FakeClient.calls.append((method, path, kwargs))
        route = FakeClient.routes[path]
        return route() if callable(route) else route


class ChainTestBase(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_user("tester", password="pw"))
        self.conn = Connection.objects.create(name="Api", base_url="https://api.example.com", auth_type=Connection.AUTH_NONE)
        self.chain = CallChain.objects.create(name="C", connection=self.conn)
        FakeClient.routes, FakeClient.calls = {}, []
        patcher = mock.patch.object(executor, "ConnectionClient", FakeClient)
        patcher.start()
        self.addCleanup(patcher.stop)

    def step(self, name, kind="http", **kw):
        order = self.chain.steps.count() + 1
        kw.setdefault("method", "GET")
        if kind == "http":
            kw.setdefault("path", f"/{name}")
        return CallChainStep.objects.create(chain=self.chain, order=order, name=name, kind=kind, **kw)

    def run_chain(self):
        run = CallChainRun.objects.create(chain=self.chain, status=CallChainRun.STATUS_FAILED)
        executor.run_chain(run)
        run.refresh_from_db()
        return run

    def results(self, run):
        return {r.name: r for r in run.step_results.all()}


class TimeoutTests(ChainTestBase):
    def test_request_timeout_is_passed_to_the_client(self):
        FakeClient.routes = {"/a": FakeResponse({}), "/b": FakeResponse({})}
        self.step("a", timeout_seconds=5)
        self.step("b")
        self.run_chain()
        self.assertEqual(FakeClient.calls[0][2].get("timeout"), 5)
        self.assertNotIn("timeout", FakeClient.calls[1][2])          # blank = the client's own default

    def test_timeout_also_covers_async_polling(self):
        FakeClient.routes = {"/start": FakeResponse({"id": 1}), "/poll": FakeResponse({"status": "done"})}
        self.step("start", is_async=True, async_poll_path="/poll", async_condition_path="status", async_condition_value="done", timeout_seconds=7)
        self.run_chain()
        self.assertEqual([c[2].get("timeout") for c in FakeClient.calls], [7, 7])

    def test_a_request_error_is_recorded_with_the_status_code_and_stops_the_chain(self):
        FakeClient.routes = {"/a": FakeResponse({"e": 1}, status=503), "/b": FakeResponse({})}
        self.step("a"); self.step("b")
        run = self.run_chain()
        self.assertEqual(run.status, "failed")
        self.assertEqual(self.results(run)["a"].status_code, 503)
        self.assertNotIn("b", self.results(run))


class WaitTests(ChainTestBase):
    def test_sleeps_for_the_configured_seconds(self):
        self.step("pause", kind="wait", params={"seconds": 2.5})
        with mock.patch.object(executor.time, "sleep") as sleep:
            run = self.run_chain()
        self.assertEqual(sum(c.args[0] for c in sleep.call_args_list), 2.5)       # slept in short slices, 2.5s in all
        self.assertEqual((run.status, self.results(run)["pause"].detail), ("success", {"waited": 2.5}))

    def test_is_capped_so_a_chain_cannot_hold_the_request_for_ages(self):
        self.step("pause", kind="wait", params={"seconds": 99999})
        with mock.patch.object(executor.time, "sleep") as sleep:
            self.run_chain()
        self.assertEqual(sum(c.args[0] for c in sleep.call_args_list), executor.MAX_WAIT_SECONDS)


class FindTests(ChainTestBase):
    ITEMS = {"items": [{"sku": "a", "id": 1, "qty": 5}, {"sku": "b", "id": 2, "qty": 50}, {"sku": "b2", "id": 3, "qty": 500}]}

    def setUp(self):
        super().setUp()
        FakeClient.routes = {"/list": FakeResponse(self.ITEMS), "/products/2": FakeResponse({"ok": True})}
        self.step("list", path="/list")

    def find(self, **params):
        base = {"source": "list.items", "match_field": "sku", "operator": "equals", "value": "b"}
        return self.step("look", kind="find", params={**base, **params})

    def test_finds_an_item_and_a_capture_feeds_the_next_call(self):
        self.find()
        CallChainStep.objects.filter(name="look").update(captures=[{"name": "pid", "path": "id"}])
        self.step("get", path="/products/{{pid}}")
        run = self.run_chain()
        self.assertEqual(run.status, "success")
        self.assertEqual(self.results(run)["look"].response_json["id"], 2)
        self.assertEqual(FakeClient.calls[-1][1], "/products/2")

    def test_value_can_use_a_variable_from_an_earlier_step(self):
        CallChainStep.objects.filter(name="list").update(captures=[{"name": "wanted", "path": "items.0.sku"}])
        self.find(value="{{wanted}}")
        self.assertEqual(self.results(self.run_chain())["look"].response_json["id"], 1)

    def test_not_found_fails_the_step_and_reports_what_was_searched(self):
        self.find(value="zzz")
        run = self.run_chain()
        r = self.results(run)["look"]
        self.assertEqual(run.status, "failed")
        self.assertIn("No item in 'list.items'", r.error)
        self.assertEqual((r.detail["searched"], r.detail["matched"]), (3, 0))

    def test_on_missing_null_lets_the_chain_continue(self):
        self.find(value="zzz", on_missing="null")
        self.assertEqual(self.run_chain().status, "success")

    def test_pick_all_returns_every_match(self):
        self.find(operator="starts_with", value="b", pick="all")
        self.assertEqual([i["id"] for i in self.results(self.run_chain())["look"].response_json], [2, 3])

    def test_operators(self):
        for op, value, expected in [("contains", "2", [3]), ("gt", "40", [2, 3]), ("lt", "10", [1]), ("matches", r"^b\d$", [3]), ("not_equals", "b", [1, 3])]:
            with self.subTest(op=op):
                field = "sku" if op in ("contains", "matches", "not_equals") else "qty"
                self.chain.steps.filter(name="look").delete()
                self.find(match_field=field, operator=op, value=value, pick="all")
                found = self.results(self.run_chain())["look"].response_json
                self.assertEqual([i["id"] for i in found], expected)

    def test_source_must_be_a_list(self):
        self.find(source="list")
        self.assertIn("isn't a list", self.results(self.run_chain())["look"].error)

    def test_can_search_an_earlier_steps_own_variable_and_wrapped_placeholders(self):
        self.find(source="{{ list.items }}")
        self.assertEqual(self.run_chain().status, "success")


class CheckHeaderTests(ChainTestBase):
    def setUp(self):
        super().setUp()
        FakeClient.routes = {"/a": FakeResponse({}, headers={"Content-Type": "application/json", "X-Total": "42"})}
        self.step("a")

    def check(self, **params):
        base = {"from_step": "a", "header": "content-type", "operator": "exists"}
        return self.step("chk", kind="check", params={**base, **params})

    def test_header_name_is_case_insensitive(self):
        self.check()
        r = self.results(self.run_chain())["chk"]
        self.assertEqual((r.detail["passed"], r.detail["actual"]), (True, "application/json"))

    def test_equals_contains_and_matches(self):
        for op, value, ok in [("equals", "application/json", True), ("equals", "text/html", False), ("contains", "JSON", True),
                              ("matches", r"^application/", True), ("missing", "", False)]:
            with self.subTest(op=op, value=value):
                self.chain.steps.filter(name="chk").delete()
                self.check(operator=op, value=value)
                self.assertEqual(self.run_chain().status, "success" if ok else "failed")

    def test_a_failed_check_stops_the_chain_and_says_why(self):
        self.check(operator="equals", value="text/html")
        self.step("after")
        FakeClient.routes["/after"] = FakeResponse({})
        run = self.run_chain()
        r = self.results(run)["chk"]
        self.assertEqual(run.status, "failed")
        self.assertIn("failed the check", r.error)
        self.assertIn("application/json", r.error)
        self.assertNotIn("after", self.results(run))

    def test_on_fail_warn_records_the_miss_but_carries_on(self):
        self.check(header="X-Missing", on_fail="warn")
        run = self.run_chain()
        r = self.results(run)["chk"]
        self.assertEqual((run.status, r.error, r.detail["passed"]), ("success", "", False))

    def test_the_header_value_can_be_captured_for_later_steps(self):
        self.check(header="X-Total", operator="exists")
        CallChainStep.objects.filter(name="chk").update(captures=[{"name": "total", "path": "value"}])
        FakeClient.routes["/n/42"] = FakeResponse({})
        self.step("n", path="/n/{{total}}")
        self.assertEqual(self.run_chain().status, "success")

    def test_reading_a_step_that_isnt_an_http_call_is_an_error(self):
        self.step("pause", kind="wait", params={"seconds": 0})
        self.check(from_step="pause")
        with mock.patch.object(executor.time, "sleep"):
            self.assertIn("isn't an HTTP call", self.results(self.run_chain())["chk"].error)

    def test_headers_are_saved_on_the_http_result(self):
        self.assertEqual(self.results(self.run_chain())["a"].response_headers["X-Total"], "42")


class NextPageTests(ChainTestBase):
    def paged(self, pages, **params):
        FakeClient.routes = {p: FakeResponse(b) for p, b in pages.items()}
        self.step("pg", path="/p")
        base = {"from_step": "pg", "next_path": "next", "items_path": "data", "max_pages": 5}
        return self.step("more", kind="next", params={**base, **params})

    def test_follows_the_next_link_and_collects_every_item(self):
        self.paged({"/p": {"data": [1, 2], "next": "/p?page=2"}, "/p?page=2": {"data": [3], "next": None}})
        run = self.run_chain()
        r = self.results(run)["more"]
        self.assertEqual(r.response_json["items"], [1, 2, 3])
        self.assertEqual((r.detail["pages"], r.detail["more"]), (2, False))
        self.assertEqual([c[1] for c in FakeClient.calls], ["/p", "/p?page=2"])

    def test_a_cursor_goes_in_the_named_query_parameter_keeping_the_existing_query(self):
        FakeClient.routes = {"/p?limit=2": FakeResponse({"data": [1], "cur": "abc"}), "/p?limit=2&cursor=abc": FakeResponse({"data": [2], "cur": None})}
        self.step("pg", path="/p?limit=2")
        self.step("more", kind="next", params={"from_step": "pg", "next_path": "cur", "items_path": "data", "cursor_param": "cursor"})
        self.assertEqual(self.results(self.run_chain())["more"].response_json["items"], [1, 2])

    def test_stops_at_max_pages_and_says_there_was_more(self):
        self.paged({"/p": {"data": [1], "next": "/p?page=2"}, "/p?page=2": {"data": [2], "next": "/p?page=3"}}, max_pages=2)
        r = self.results(self.run_chain())["more"]
        self.assertEqual((r.response_json["pages"], r.detail["more"]), (2, True))

    def test_never_follows_a_link_to_another_host(self):
        self.paged({"/p": {"data": [1], "next": "https://evil.example.net/steal"}})
        r = self.results(self.run_chain())["more"]
        self.assertIn("not following it", r.error)
        self.assertEqual(len(FakeClient.calls), 1)                   # the second request was never sent

    def test_follows_an_absolute_link_on_the_connections_own_host(self):
        self.paged({"/p": {"data": [1], "next": "https://api.example.com/p?page=2"}, "https://api.example.com/p?page=2": {"data": [2], "next": None}})
        self.assertEqual(self.results(self.run_chain())["more"].response_json["items"], [1, 2])

    def test_a_query_only_link_keeps_the_path(self):
        self.paged({"/p": {"data": [1], "next": "?page=2"}, "/p?page=2": {"data": [2], "next": None}})
        self.assertEqual(self.results(self.run_chain())["more"].response_json["items"], [1, 2])

    def test_a_non_get_source_is_refused(self):
        FakeClient.routes = {"/p": FakeResponse({"data": [], "next": "x"})}
        self.step("pg", path="/p", method="POST")
        self.step("more", kind="next", params={"from_step": "pg", "next_path": "next"})
        self.assertIn("repeats a GET", self.results(self.run_chain())["more"].error)


class FilePreviewTests(ChainTestBase):
    def setUp(self):
        super().setUp()
        self.entity = Entity.objects.create(connection=self.conn, name="Upload")
        self.entity.source_file.name = "schemas/source_files/u.csv"
        self.entity.save()
        Field.objects.create(entity=self.entity, name="sku", field_type="string")
        self.rows = [{"sku": f"s{i}", "when": datetime.date(2026, 1, i), "n": i} for i in range(1, 6)]
        patcher = mock.patch.object(executor.discovery, "read_all_records_from_source_file", return_value=self.rows)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_previews_the_first_rows_with_columns_and_total_and_is_json_safe(self):
        self.step("preview", kind="file", params={"entity": self.entity.pk, "limit": 2})
        r = self.results(self.run_chain())["preview"]
        self.assertEqual((len(r.response_json["rows"]), r.response_json["count"]), (2, 5))
        self.assertEqual(r.response_json["columns"], ["sku", "when", "n"])
        self.assertEqual(r.response_json["rows"][0]["when"], "2026-01-01")      # a date cell survives saving

    def test_columns_follow_the_files_order_not_the_alphabetical_field_list(self):
        Field.objects.create(entity=self.entity, name="alpha", field_type="string")     # declared fields sort a…, the file starts with sku
        self.step("preview", kind="file", params={"entity": self.entity.pk})
        self.assertEqual(self.results(self.run_chain())["preview"].response_json["columns"], ["sku", "when", "n"])

    def test_its_rows_can_be_searched_by_a_find_step(self):
        self.step("preview", kind="file", params={"entity": self.entity.pk, "limit": 10})
        self.step("look", kind="find", params={"source": "preview.rows", "match_field": "sku", "operator": "equals", "value": "s3"})
        self.assertEqual(self.results(self.run_chain())["look"].response_json["n"], 3)

    def test_a_missing_file_is_a_clear_error(self):
        self.entity.source_file = None
        self.entity.save()
        self.step("preview", kind="file", params={"entity": self.entity.pk})
        self.assertIn("no uploaded file", self.results(self.run_chain())["preview"].error)


class RetryTests(ChainTestBase):
    def test_retry_resumes_at_the_failed_step_using_the_earlier_steps_headers(self):
        FakeClient.routes = {"/a": FakeResponse({}, headers={"X-Ok": "1"})}
        self.step("a")
        self.step("chk", kind="check", params={"from_step": "a", "header": "X-Missing", "operator": "exists"})
        first = self.run_chain()
        self.assertEqual(first.status, "failed")

        CallChainStep.objects.filter(name="chk").update(params={"from_step": "a", "header": "X-Ok", "operator": "exists", "on_fail": "fail"})
        FakeClient.calls.clear()
        resp = self.client.post(f"/api/chains/{self.chain.pk}/retry-run/{first.pk}/")
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()["status"], "success")
        self.assertEqual(FakeClient.calls, [])                       # step "a" was not re-sent; its headers were carried over


class StepApiTests(ChainTestBase):
    def add(self, **body):
        return self.client.post(f"/api/chains/{self.chain.pk}/steps/", body, content_type="application/json")

    def test_adds_each_kind_and_lists_it_with_its_params(self):
        self.assertEqual(self.add(name="a", path="/a").status_code, 201)
        self.assertEqual(self.add(name="w", kind="wait", params={"seconds": 3}).status_code, 201)
        self.assertEqual(self.add(name="n", kind="next", params={"from_step": "a", "next_path": "next"}).status_code, 201)
        self.assertEqual(self.add(name="f", kind="find", params={"source": "n.items", "match_field": "id", "value": "1"}).status_code, 201)
        self.assertEqual(self.add(name="c", kind="check", params={"from_step": "a", "header": "Content-Type"}).status_code, 201)
        steps = self.client.get(f"/api/chains/{self.chain.pk}/").json()["steps"]
        self.assertEqual([s["kind"] for s in steps], ["http", "wait", "next", "find", "check"])
        self.assertEqual(steps[1]["params"], {"seconds": 3.0})
        self.assertEqual(steps[2]["params"]["max_pages"], 5)            # defaults are filled in

    def test_path_is_required_only_for_http_steps(self):
        missing = self.add(name="a")
        self.assertEqual((missing.status_code, missing.json()["error"]), (400, "Path is required."))
        self.assertEqual(self.add(name="w", kind="wait", params={"seconds": 1}).status_code, 201)

    def test_rejects_bad_settings_with_a_plain_message(self):
        self.add(name="a", path="/a")
        self.add(name="post", path="/p", method="POST")
        cases = [
            ({"name": "x", "kind": "nonsense"}, "Unknown step kind"),
            ({"name": "x", "kind": "wait", "params": {"seconds": 999}}, "between 0 and 300"),
            ({"name": "x", "kind": "wait", "params": {"seconds": "soon"}}, "must be a number"),
            ({"name": "x", "kind": "next", "params": {"from_step": "ghost", "next_path": "n"}}, "isn't an earlier step"),
            ({"name": "x", "kind": "next", "params": {"from_step": "a"}}, "where the next link"),
            ({"name": "x", "kind": "next", "params": {"from_step": "post", "next_path": "n"}}, "repeats a GET"),
            ({"name": "x", "kind": "find", "params": {"match_field": "id"}}, "which list"),
            ({"name": "x", "kind": "find", "params": {"source": "a.items", "operator": "matches", "value": "("}}, "regular expression"),
            ({"name": "x", "kind": "check", "params": {"from_step": "a"}}, "header name"),
            ({"name": "x", "kind": "check", "params": {"from_step": "a", "header": "H", "operator": "zzz"}}, "operator must be"),
            ({"name": "x", "kind": "file", "params": {}}, "uploaded CSV"),
            ({"name": "x", "path": "/x", "timeout_seconds": 0}, "Timeout must be between"),
        ]
        for body, expected in cases:
            with self.subTest(body=body):
                resp = self.add(**body)
                self.assertEqual(resp.status_code, 400)
                self.assertIn(expected, resp.json()["error"])
        self.assertEqual(self.chain.steps.count(), 2)                    # nothing half-created

    def test_kind_cannot_be_changed_but_settings_can_be_edited(self):
        step = self.add(name="w", kind="wait", params={"seconds": 1}).json()
        url = f"/api/chain-steps/{step['id']}/"
        self.assertEqual(self.client.patch(url, {"kind": "http"}, content_type="application/json").status_code, 400)
        ok = self.client.patch(url, {"params": {"seconds": 9}}, content_type="application/json")
        self.assertEqual((ok.status_code, ok.json()["params"]), (200, {"seconds": 9.0}))
        self.assertEqual(self.client.patch(url, {"params": {"seconds": 9999}}, content_type="application/json").status_code, 400)

    def test_http_timeout_is_saved_and_can_be_cleared(self):
        step = self.add(name="a", path="/a", timeout_seconds=12).json()
        self.assertEqual(step["timeout_seconds"], 12)
        cleared = self.client.patch(f"/api/chain-steps/{step['id']}/", {"timeout_seconds": None}, content_type="application/json")
        self.assertIsNone(cleared.json()["timeout_seconds"])

    def test_file_step_needs_an_entity_with_a_file(self):
        entity = Entity.objects.create(connection=self.conn, name="U")
        self.assertEqual(self.add(name="f", kind="file", params={"entity": entity.pk}).status_code, 400)
        entity.source_file.name = "schemas/source_files/u.csv"
        entity.save()
        self.assertEqual(self.add(name="f", kind="file", params={"entity": entity.pk, "limit": 5}).status_code, 201)


class FilePreviewEndpointTests(ChainTestBase):
    def test_returns_columns_rows_and_total(self):
        entity = Entity.objects.create(connection=self.conn, name="U")
        entity.source_file.name = "schemas/source_files/u.csv"
        entity.save()
        Field.objects.create(entity=entity, name="sku", field_type="string")
        Field.objects.create(entity=entity, name="a_declared_but_not_first", field_type="string")
        rows = [{"sku": f"s{i}", "extra": i} for i in range(12)]
        with mock.patch("schemas.views.discovery.read_all_records_from_source_file", return_value=rows):
            body = self.client.get(f"/api/entities/{entity.pk}/file-preview/?limit=3").json()
        self.assertEqual((body["columns"], len(body["rows"]), body["count"]), (["sku", "extra"], 3, 12))   # the file's order

    def test_400_without_a_file(self):
        entity = Entity.objects.create(connection=self.conn, name="U")
        self.assertEqual(self.client.get(f"/api/entities/{entity.pk}/file-preview/").status_code, 400)



class KillChainTests(ChainTestBase):
    """The Kill button on a chain: it stops between steps, mid-wait, mid-poll and between pages."""

    def flag(self, run_holder):
        CallChainRun.objects.filter(pk=run_holder["run"].pk).update(cancel_requested=True)

    def run_killing(self, kill_on):
        """Run the chain; `kill_on(path)` decides which request triggers the Kill."""
        holder = {}
        original = FakeClient.routes

        def wrap(response, path):
            def route():
                if kill_on(path):
                    self.flag(holder)
                return response
            return route

        FakeClient.routes = {path: wrap(resp, path) for path, resp in original.items()}
        holder["run"] = CallChainRun.objects.create(chain=self.chain, status=CallChainRun.STATUS_FAILED)
        executor.run_chain(holder["run"])
        holder["run"].refresh_from_db()
        return holder["run"]

    def test_killed_between_steps_the_later_steps_never_run(self):
        FakeClient.routes = {"/a": FakeResponse({}), "/b": FakeResponse({}), "/c": FakeResponse({})}
        for n in "abc":
            self.step(n)
        run = self.run_killing(lambda path: path == "/a")
        self.assertEqual(run.status, CallChainRun.STATUS_CANCELLED)
        self.assertEqual([c[1] for c in FakeClient.calls], ["/a"])
        self.assertEqual(self.results(run)["b"].error, "Cancelled by user.")
        self.assertNotIn("c", self.results(run))

    def test_killed_during_a_wait_it_stops_after_a_slice_not_after_the_whole_wait(self):
        self.step("pause", kind="wait", params={"seconds": 200})
        self.step("after")
        FakeClient.routes = {"/after": FakeResponse({})}
        slept = []
        run = CallChainRun.objects.create(chain=self.chain, status=CallChainRun.STATUS_FAILED)

        def sleep(seconds):
            slept.append(seconds)
            if len(slept) == 2:
                CallChainRun.objects.filter(pk=run.pk).update(cancel_requested=True)

        with mock.patch.object(executor.time, "sleep", side_effect=sleep):
            executor.run_chain(run)
        run.refresh_from_db()
        self.assertEqual(run.status, CallChainRun.STATUS_CANCELLED)
        self.assertLess(sum(slept), 5)
        self.assertEqual(FakeClient.calls, [])

    def test_killed_while_polling_an_async_step(self):
        FakeClient.routes = {"/start": FakeResponse({"id": 1}), "/poll": FakeResponse({"status": "running"})}
        self.step("job", path="/start", is_async=True, async_poll_path="/poll", async_condition_path="status", async_condition_value="done",
                  async_interval_seconds=2, async_timeout_seconds=600)
        polls = []
        run = CallChainRun.objects.create(chain=self.chain, status=CallChainRun.STATUS_FAILED)

        def sleep(seconds):
            polls.append(seconds)
            if len(polls) >= 3:
                CallChainRun.objects.filter(pk=run.pk).update(cancel_requested=True)

        with mock.patch.object(executor.time, "sleep", side_effect=sleep):
            executor.run_chain(run)
        run.refresh_from_db()
        self.assertEqual(run.status, CallChainRun.STATUS_CANCELLED)
        self.assertLess(len(FakeClient.calls), 6)                 # nowhere near the 300 polls a 600s timeout would allow

    def test_killed_between_pages_of_a_next_page_step(self):
        FakeClient.routes = {f"/p?page={i}": FakeResponse({"data": [i], "next": f"/p?page={i + 1}"}) for i in range(1, 60)}
        FakeClient.routes["/p"] = FakeResponse({"data": [0], "next": "/p?page=1"})
        self.step("pg", path="/p")
        self.step("more", kind="next", params={"from_step": "pg", "next_path": "next", "items_path": "data", "max_pages": 50})
        run = self.run_killing(lambda path: path == "/p?page=3")
        self.assertEqual(run.status, CallChainRun.STATUS_CANCELLED)
        self.assertLess(len(FakeClient.calls), 8)                 # it did not walk all 50 pages

    def test_the_run_is_no_longer_active_afterwards(self):
        FakeClient.routes = {"/a": FakeResponse({})}
        self.step("a")
        run = self.run_killing(lambda path: True)
        self.assertNotIn(run.pk, executor.ACTIVE_RUNS)

    def test_cancel_endpoint_closes_an_orphaned_unfinished_run_but_only_flags_a_live_one(self):
        orphan = CallChainRun.objects.create(chain=self.chain, status=CallChainRun.STATUS_FAILED)
        live = CallChainRun.objects.create(chain=self.chain, status=CallChainRun.STATUS_FAILED)
        done = CallChainRun.objects.create(chain=self.chain, status=CallChainRun.STATUS_SUCCESS, finished_at=timezone.now())
        executor.ACTIVE_RUNS.add(live.pk)
        self.addCleanup(executor.ACTIVE_RUNS.discard, live.pk)

        self.assertEqual(self.client.post(f"/api/chains/{self.chain.pk}/cancel/").json(), {"cancelled": 2})

        for r in (orphan, live, done):
            r.refresh_from_db()
        self.assertEqual((orphan.status, orphan.finished_at is not None), ("cancelled", True))
        self.assertEqual((live.cancel_requested, live.finished_at), (True, None))
        self.assertEqual((done.status, done.cancel_requested), ("success", False))

    def test_a_cancelled_run_is_retried_from_the_step_that_did_not_run(self):
        FakeClient.routes = {"/a": FakeResponse({"v": 1}), "/b": FakeResponse({}), "/c": FakeResponse({})}
        for n in "abc":
            self.step(n)
        cancelled = self.run_killing(lambda path: path == "/a")
        FakeClient.calls.clear()
        FakeClient.routes = {"/a": FakeResponse({}), "/b": FakeResponse({}), "/c": FakeResponse({})}          # no more kills
        resp = self.client.post(f"/api/chains/{self.chain.pk}/retry-run/{cancelled.pk}/")
        self.assertEqual((resp.status_code, resp.json()["status"]), (200, "success"))
        self.assertEqual([c[1] for c in FakeClient.calls], ["/b", "/c"])          # step "a" was NOT sent again


class PerStepConnectionTests(ChainTestBase):
    def test_a_step_can_call_a_different_connection_than_its_chain(self):
        other = Connection.objects.create(name="Other", base_url="https://o.example.com", auth_type=Connection.AUTH_NONE)
        seen = []

        class Recording(FakeClient):
            def request(self, method, path, **kwargs):
                seen.append((self.connection.name, path))
                return FakeResponse({})

        with mock.patch.object(executor, "ConnectionClient", Recording):
            self.step("mine", path="/x")
            self.step("theirs", path="/y", connection=other)
            self.run_chain()
        self.assertEqual(seen, [("Api", "/x"), ("Other", "/y")])


class HeadersAndParamsTests(ChainTestBase):
    """Request steps (in a chain, and in a plan's custom run) can send headers and query parameters."""

    def sent(self):
        return FakeClient.calls[-1][2]

    def test_headers_and_params_are_sent_with_placeholders_filled_in(self):
        FakeClient.routes = {"/login": FakeResponse({"token": "abc123"}), "/items": FakeResponse({})}
        self.step("login", path="/login")
        CallChainStep.objects.filter(name="login").update(captures=[{"name": "tok", "path": "token"}])
        self.step("items", path="/items", headers=[{"name": "Authorization", "value": "Bearer {{tok}}"}, {"name": "X-Trace", "value": "run-1"}],
                  query_params=[{"name": "limit", "value": "50"}, {"name": "since", "value": "{{login.token}}"}])
        run = self.run_chain()
        self.assertEqual(run.status, "success")
        kw = self.sent()
        self.assertEqual(kw["headers"], {"Authorization": "Bearer abc123", "X-Trace": "run-1"})
        self.assertEqual(kw["params"], {"limit": "50", "since": "abc123"})

    def test_a_plain_step_sends_neither(self):
        FakeClient.routes = {"/a": FakeResponse({})}
        self.step("a")
        self.run_chain()
        self.assertNotIn("headers", self.sent())
        self.assertNotIn("params", self.sent())

    def test_secret_header_values_are_masked_in_the_stored_result(self):
        FakeClient.routes = {"/a": FakeResponse({})}
        self.step("a", headers=[{"name": "Authorization", "value": "Bearer supersecret"}, {"name": "X-Api-Key", "value": "k"}, {"name": "Accept", "value": "application/json"}],
                  query_params=[{"name": "q", "value": "1"}])
        detail = self.results(self.run_chain())["a"].detail
        self.assertEqual(detail["request_headers"], {"Authorization": "••••••", "X-Api-Key": "••••••", "Accept": "application/json"})
        self.assertEqual(detail["request_params"], {"q": "1"})
        self.assertNotIn("supersecret", str(detail))

    def test_an_unknown_placeholder_fails_the_step_clearly(self):
        FakeClient.routes = {"/a": FakeResponse({})}
        self.step("a", headers=[{"name": "X-Id", "value": "{{ghost.id}}"}])
        r = self.results(self.run_chain())["a"]
        self.assertIn("ghost", r.error)
        self.assertEqual(FakeClient.calls, [])

    def test_async_polls_carry_the_headers_but_not_the_params(self):
        FakeClient.routes = {"/start": FakeResponse({"id": 1}), "/poll": FakeResponse({"status": "done"})}
        self.step("job", path="/start", is_async=True, async_poll_path="/poll", async_condition_path="status", async_condition_value="done",
                  headers=[{"name": "X-Auth", "value": "t"}], query_params=[{"name": "q", "value": "1"}])
        self.run_chain()
        start, poll = FakeClient.calls
        self.assertEqual((start[2]["headers"], start[2]["params"]), ({"X-Auth": "t"}, {"q": "1"}))
        self.assertEqual(poll[2].get("headers"), {"X-Auth": "t"})
        self.assertNotIn("params", poll[2])

    def test_next_page_repeats_headers_and_params_when_the_pointer_is_a_cursor(self):
        FakeClient.routes = {"/p": FakeResponse({"data": [1], "cur": "c2"}), "/p?cursor=c2": FakeResponse({"data": [2], "cur": None})}
        self.step("pg", path="/p", headers=[{"name": "X-Auth", "value": "t"}], query_params=[{"name": "limit", "value": "10"}])
        self.step("more", kind="next", params={"from_step": "pg", "next_path": "cur", "items_path": "data", "cursor_param": "cursor"})
        self.run_chain()
        second = FakeClient.calls[1]
        self.assertEqual((second[1], second[2]["headers"], second[2]["params"]), ("/p?cursor=c2", {"X-Auth": "t"}, {"limit": "10"}))

    def test_next_page_following_a_link_keeps_headers_but_does_not_repeat_params(self):
        FakeClient.routes = {"/p": FakeResponse({"data": [1], "next": "/p?page=2&limit=10"}), "/p?page=2&limit=10": FakeResponse({"data": [2], "next": None})}
        self.step("pg", path="/p", headers=[{"name": "X-Auth", "value": "t"}], query_params=[{"name": "limit", "value": "10"}])
        self.step("more", kind="next", params={"from_step": "pg", "next_path": "next", "items_path": "data"})
        self.run_chain()
        second = FakeClient.calls[1]
        self.assertEqual(second[2]["headers"], {"X-Auth": "t"})
        self.assertNotIn("params", second[2])

    def api_add(self, **body):
        return self.client.post(f"/api/chains/{self.chain.pk}/steps/", {"name": "s", "path": "/x", **body}, content_type="application/json")

    def test_api_stores_cleans_and_returns_them(self):
        resp = self.api_add(headers=[{"name": " X-A ", "value": "1"}, {"name": "", "value": ""}], query_params=[{"name": "q", "value": "v"}])
        self.assertEqual(resp.status_code, 201)
        body = resp.json()
        self.assertEqual((body["headers"], body["query_params"]), ([{"name": "X-A", "value": "1"}], [{"name": "q", "value": "v"}]))   # blank row dropped

    def test_api_rejects_bad_headers_and_params(self):
        for body, expected in [
            ({"headers": [{"name": "Bad Header!", "value": "x"}]}, "isn't a valid header name"),
            ({"headers": [{"name": "X-A", "value": "1"}, {"name": "x-a", "value": "2"}]}, "more than once"),
            ({"query_params": [{"name": "", "value": "orphan"}]}, "value but no name"),
            ({"query_params": "limit=5"}, "list of"),
            ({"headers": [{"name": f"H{i}", "value": "1"} for i in range(31)]}, "At most 30"),
        ]:
            with self.subTest(body=body):
                resp = self.api_add(**body)
                self.assertEqual(resp.status_code, 400)
                self.assertIn(expected, resp.json()["error"])

    def test_api_can_edit_and_clear_them(self):
        step = self.api_add(headers=[{"name": "X-A", "value": "1"}]).json()
        url = f"/api/chain-steps/{step['id']}/"
        patched = self.client.patch(url, {"headers": [{"name": "X-B", "value": "2"}], "query_params": [{"name": "q", "value": "1"}]}, content_type="application/json")
        self.assertEqual((patched.json()["headers"], patched.json()["query_params"]), ([{"name": "X-B", "value": "2"}], [{"name": "q", "value": "1"}]))
        cleared = self.client.patch(url, {"headers": []}, content_type="application/json")
        self.assertEqual((cleared.json()["headers"], cleared.json()["query_params"]), ([], [{"name": "q", "value": "1"}]))     # only what was sent changes
