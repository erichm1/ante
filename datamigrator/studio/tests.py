from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from chains.models import CallChain, CallChainRun
from connections.models import Connection
from jobs.models import MigrationLog, MigrationRun
from mappings.models import Mapping
from integrations.models import InstalledIntegration, Integration
from plans.models import MigrationPlan, PlanStep


class StudioTestBase(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("tester", password="pw")
        self.client.force_login(self.user)
        self.src = Connection.objects.create(name="Source", base_url="https://src.example.com", auth_type=Connection.AUTH_NONE)
        self.dst = Connection.objects.create(name="Dest", base_url="https://dst.example.com", auth_type=Connection.AUTH_NONE)
        self.mapping = Mapping.objects.create(name="Sync", source_connection=self.src)
        self.mapping.destination_connections.add(self.dst)
        self.chain = CallChain.objects.create(name="Create thing", connection=self.dst)


class StudioPageTests(StudioTestBase):
    def test_anonymous_is_redirected_to_login(self):
        self.client.logout()
        resp = self.client.get("/studio/")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/accounts/login", resp["Location"])

    def test_page_renders_shell(self):
        resp = self.client.get("/studio/")
        self.assertContains(resp, 'id="stTree"')
        self.assertContains(resp, "js/studio/boot.js")

    def test_nav_links_to_the_studio_and_to_each_classic_module(self):
        html = self.client.get("/studio/").content.decode()
        for href in ('href="/studio/"', 'href="/mappings/"', 'href="/jobs/"', 'href="/plans/"', 'href="/chains/"'):
            self.assertIn(href, html)
        self.assertNotIn('href="/mappings/canvas/"', html)                     # the old canvas list has no nav entry of its own

    def test_only_the_studio_link_is_active_on_the_studio_page(self):
        html = self.client.get("/studio/").content.decode()
        nav = html[html.index('id="navLinks"'):html.index("</nav>")]
        self.assertEqual(nav.count('class="active'), 1)
        self.assertRegex(nav, r'href="/studio/"\s+class="active')


class StudioMobileTests(StudioTestBase):
    """The Studio is desktop-only: phones get a notice, and the CSS is mobile-first (hidden by
    default, revealed from 768px up) so nothing has to be undone for small screens."""

    def test_page_carries_the_desktop_only_notice(self):
        html = self.client.get("/studio/").content.decode()
        self.assertIn('id="stMobileBlock"', html)
        self.assertIn("desktop or laptop", html)
        self.assertIn("web application", html)

    def test_workspace_is_hidden_by_default_and_only_shown_from_768px(self):
        import re
        from django.conf import settings
        css = (settings.BASE_DIR / "static" / "css" / "studio.css").read_text()
        base = re.search(r"\n\.studio \{(.*?)\n\}", css, re.S).group(1)
        self.assertIn("display: none;", base)                                    # mobile first: off until there's room
        reveal = re.search(r"@media \(min-width: 768px\) \{(.*?)\n\}", css, re.S).group(1)
        self.assertIn(".studio { display: grid; }", reveal)
        self.assertIn(".st-mobile-block { display: none; }", reveal)

    def test_no_bypass_button_in_the_notice(self):
        html = self.client.get("/studio/").content.decode()
        notice = html.split('id="stMobileBlock"')[1].split('id="studio"')[0]
        self.assertNotIn("<button", notice)


class StudioTreeTests(StudioTestBase):
    def test_tree_lists_every_kind(self):
        MigrationPlan.objects.create(name="Nightly", execution_mode=MigrationPlan.MODE_MIXED)
        data = self.client.get("/studio/tree/").json()

        self.assertEqual([m["name"] for m in data["mappings"]], ["Sync"])
        self.assertEqual(data["mappings"][0]["source"], "Source")
        self.assertEqual(data["mappings"][0]["destinations"], ["Dest"])
        self.assertEqual(data["chains"][0]["connection"], "Dest")
        self.assertEqual(data["plans"][0]["status"], "draft")
        self.assertEqual({c["name"] for c in data["connections"]}, {"Source", "Dest"})

    def test_mapping_lists_its_entity_pairs(self):
        from schemas.models import Entity
        src = Entity.objects.create(connection=self.src, name="Items")
        dst = Entity.objects.create(connection=self.dst, name="Products")
        from mappings.models import EntityMapping
        pair = EntityMapping.objects.create(mapping=self.mapping, source_entity=src, target_entity=dst)
        data = self.client.get("/studio/tree/").json()["mappings"][0]
        self.assertEqual(data["pair_list"], [{"id": pair.pk, "source": "Items", "target": "Products"}])

    def test_recent_runs_merges_mapping_and_chain_runs_newest_first(self):
        older = MigrationRun.objects.create(mapping=self.mapping, status=MigrationRun.STATUS_SUCCESS)
        newer = CallChainRun.objects.create(chain=self.chain, status=CallChainRun.STATUS_SUCCESS, finished_at=timezone.now())
        runs = self.client.get("/studio/tree/").json()["recent_runs"]

        self.assertEqual([(r["kind"], r["run_id"]) for r in runs], [("chain", newer.pk), ("mapping", older.pk)])

    def test_unfinished_chain_run_reports_running_not_failed(self):
        # A chain run row is created "failed" and only flipped when it finishes.
        CallChainRun.objects.create(chain=self.chain, status=CallChainRun.STATUS_FAILED)
        runs = self.client.get("/studio/tree/").json()["recent_runs"]
        self.assertEqual(runs[0]["status"], "running")


class SlimRunListTests(StudioTestBase):
    def setUp(self):
        super().setUp()
        self.run = MigrationRun.objects.create(mapping=self.mapping, status=MigrationRun.STATUS_SUCCESS)
        MigrationLog.objects.create(run=self.run, message="hello")

    def test_default_list_still_nests_logs(self):
        row = self.client.get(f"/api/runs/?mapping={self.mapping.pk}").json()["results"][0]
        self.assertEqual(row["logs"][0]["message"], "hello")

    def test_slim_list_omits_logs_and_step_statuses(self):
        row = self.client.get(f"/api/runs/?mapping={self.mapping.pk}&slim=1").json()["results"][0]
        self.assertNotIn("logs", row)
        self.assertNotIn("step_statuses", row)
        self.assertEqual(row["id"], self.run.pk)

    def test_detail_always_returns_full_run(self):
        row = self.client.get(f"/api/runs/{self.run.pk}/?slim=1").json()
        self.assertEqual(row["logs"][0]["message"], "hello")


class ChainRunsEndpointTests(StudioTestBase):
    def test_lists_runs_newest_first_with_step_results(self):
        first = CallChainRun.objects.create(chain=self.chain, status=CallChainRun.STATUS_SUCCESS)
        second = CallChainRun.objects.create(chain=self.chain, status=CallChainRun.STATUS_FAILED)
        rows = self.client.get(f"/api/chains/{self.chain.pk}/runs/").json()

        self.assertEqual([r["id"] for r in rows], [second.pk, first.pk])
        self.assertIn("step_results", rows[0])


class ClassicPagesStillWorkTests(StudioTestBase):
    """The Studio replaces the nav entries, not the pages — every classic page
    stays reachable (run detail/snapshot pages in particular are still what the
    Studio's "Detail"/"Pipeline" links open)."""

    def test_classic_pages_render(self):
        plan = MigrationPlan.objects.create(name="Old style")
        run = MigrationRun.objects.create(mapping=self.mapping, status=MigrationRun.STATUS_SUCCESS)
        urls = [
            "/mappings/", "/mappings/canvas/", f"/mappings/{self.mapping.pk}/?tab=canvas", f"/mappings/{self.mapping.pk}/?tab=raw",
            "/jobs/", f"/jobs/runs/{run.pk}/", f"/jobs/runs/{run.pk}/snapshot/",
            "/plans/", f"/plans/{plan.pk}/", "/chains/", f"/chains/{self.chain.pk}/",
        ]
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)


class ExplorerLogoTests(StudioTestBase):
    def install(self, connection, name, icon, image=None):
        integration = Integration.objects.create(name=name, slug=name.lower(), description="d", icon=icon)
        if image:
            integration.icon_image.name = image
            integration.save()
        InstalledIntegration.objects.create(integration=integration, connection=connection)

    def tree(self):
        return self.client.get("/studio/tree/").json()

    def test_connection_without_an_integration_gets_the_generic_icon(self):
        logo = self.tree()["connections"][0]["logos"][0]
        self.assertEqual((logo["icon"], logo["image"]), ("bi-hdd-network", None))

    def test_connection_uses_its_integrations_icon_and_uploaded_logo(self):
        self.install(self.src, "Shopify", "bi-shop", image="integrations/icons/shopify.png")
        by_name = {c["name"]: c["logos"][0] for c in self.tree()["connections"]}
        self.assertEqual(by_name["Source"]["icon"], "bi-shop")
        self.assertTrue(by_name["Source"]["image"].endswith("integrations/icons/shopify.png"))
        self.assertIsNone(by_name["Dest"]["image"])

    def test_mapping_carries_origin_and_destination_logos(self):
        self.install(self.src, "Shopify", "bi-shop")
        self.install(self.dst, "Tiny", "bi-box")
        m = self.tree()["mappings"][0]
        self.assertEqual([l["icon"] for l in m["logos"]], ["bi-shop"])
        self.assertEqual([l["icon"] for l in m["logos_to"]], ["bi-box"])

    def test_chain_carries_its_connections_logo(self):
        self.install(self.dst, "Tiny", "bi-box")
        self.assertEqual(self.tree()["chains"][0]["logos"][0]["icon"], "bi-box")

    def test_plan_lists_each_system_its_steps_touch_once(self):
        plan = MigrationPlan.objects.create(name="P", execution_mode=MigrationPlan.MODE_MIXED)
        PlanStep.objects.create(plan=plan, mapping=self.mapping, order=1)      # Source -> Dest
        PlanStep.objects.create(plan=plan, chain=self.chain, order=2)          # Dest again
        self.assertEqual([l["name"] for l in self.tree()["plans"][0]["logos"]], ["Source", "Dest"])

    def test_a_plan_with_a_wait_step_still_builds_the_tree(self):
        # Regression: a Wait has neither mapping nor chain — the tree used to crash on it (500).
        plan = MigrationPlan.objects.create(name="P", execution_mode=MigrationPlan.MODE_MIXED)
        PlanStep.objects.create(plan=plan, mapping=self.mapping, order=1)
        PlanStep.objects.create(plan=plan, wait_seconds=30, order=2)
        resp = self.client.get("/studio/tree/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual([l["name"] for l in resp.json()["plans"][0]["logos"]], ["Source", "Dest"])
        self.assertEqual(resp.json()["plans"][0]["steps"], 2)

    def test_recent_runs_carry_their_documents_logos(self):
        MigrationRun.objects.create(mapping=self.mapping, status=MigrationRun.STATUS_SUCCESS)
        run = self.tree()["recent_runs"][0]
        self.assertEqual([l["name"] for l in run["logos"]], ["Source"])
        self.assertEqual([l["name"] for l in run["logos_to"]], ["Dest"])

    def test_reports_how_many_plan_steps_use_each_mapping_and_chain(self):
        plan = MigrationPlan.objects.create(name="P", execution_mode=MigrationPlan.MODE_MIXED)
        for order in (1, 2):
            PlanStep.objects.create(plan=plan, mapping=self.mapping, order=order)
        PlanStep.objects.create(plan=plan, chain=self.chain, order=3)
        data = self.tree()
        self.assertEqual((data["mappings"][0]["plan_steps"], data["chains"][0]["plan_steps"]), (2, 1))


class TemplateTestBase(StudioTestBase):
    def setUp(self):
        super().setUp()
        from schemas.models import Entity, Field
        self.Entity, self.Field = Entity, Field
        self.items = Entity.objects.create(connection=self.src, name="Items", endpoint_path="/items")
        for n, t in [("Full_Name", "string"), ("sku", "string"), ("price", "number"), ("legacy", "string")]:
            Field.objects.create(entity=self.items, name=n, field_type=t)
        self.customers = Entity.objects.create(connection=self.dst, name="Customers", endpoint_path="/customers")
        for n, t in [("fullname", "string"), ("SKU", "string"), ("meta.price", "number"), ("label", "string")]:
            Field.objects.create(entity=self.customers, name=n, field_type=t)

    def apply(self, slug, params, expect=201):
        resp = self.client.post(f"/studio/templates/{slug}/", params, content_type="application/json")
        self.assertEqual(resp.status_code, expect, resp.content)
        return resp.json()

    def copy_params(self, **over):
        p = {"name": "Copy", "source_connection": self.src.pk, "source_entity": self.items.pk,
             "target_connection": self.dst.pk, "target_entity": self.customers.pk}
        return {**p, **over}


class TemplateCatalogTests(TemplateTestBase):
    def test_catalog_lists_every_kind_and_each_server_template_has_an_applier(self):
        from . import templates as tpl
        cat = self.client.get("/studio/templates/").json()["templates"]
        self.assertEqual({t["kind"] for t in cat}, {"mapping", "chain", "plan", "run"})
        for t in cat:
            self.assertTrue(all({"name", "label", "type"} <= set(p) for p in t["params"]), t["slug"])
            self.assertTrue(t["slug"] in tpl.APPLY or t.get("action"), t["slug"])   # nothing dangling
        self.assertEqual(len({t["slug"] for t in cat}), len(cat))

    def test_action_templates_are_client_side_only(self):
        self.apply("run-now", {"mapping": self.mapping.pk}, expect=400)

    def test_requires_login(self):
        self.client.logout()
        self.assertEqual(self.client.post("/studio/templates/fan-out/", {}, content_type="application/json").status_code, 302)


class MappingTemplateTests(TemplateTestBase):
    def test_matches_fields_ignoring_case_and_separators_and_dotted_prefixes(self):
        body = self.apply("copy-matching-fields", self.copy_params())
        from mappings.models import EntityMapping
        pair = EntityMapping.objects.get(mapping_id=body["id"])
        wired = {(fm.source_field.name, fm.target_field.name) for fm in pair.field_mappings.all()}
        self.assertEqual(wired, {("Full_Name", "fullname"), ("sku", "SKU"), ("price", "meta.price")})
        self.assertIn("3 field(s) matched", body["notes"][0])
        self.assertIn("not matched: label", body["notes"][0])          # says what it couldn't wire

    def test_creates_the_mapping_with_its_destination_and_write_method(self):
        body = self.apply("copy-matching-fields", self.copy_params(write_method="PUT"))
        m = Mapping.objects.get(pk=body["id"])
        self.assertEqual(list(m.destination_connections.all()), [self.dst])
        self.assertEqual(m.entity_mappings.get().write_method, "PUT")

    def test_text_preset_only_touches_text_to_text_wires(self):
        body = self.apply("copy-matching-fields", self.copy_params(transform="uppercase"))
        from mappings.models import FieldMapping
        rules = {fm.target_field.name: fm.transform_rules for fm in FieldMapping.objects.filter(entity_mapping__mapping_id=body["id"])}
        self.assertEqual(rules["fullname"], [{"op": "uppercase"}])
        self.assertEqual(rules["meta.price"], [])                       # number → number stays untouched

    def test_rejects_an_entity_from_the_wrong_connection(self):
        err = self.apply("copy-matching-fields", self.copy_params(source_entity=self.customers.pk), expect=400)
        self.assertIn("doesn't belong", err["error"])
        self.assertEqual(Mapping.objects.filter(name="Copy").count(), 0)

    def test_rejects_a_destination_on_the_source_connection(self):
        same = self.Entity.objects.create(connection=self.src, name="Twin", endpoint_path="/twin")
        err = self.apply("copy-matching-fields", self.copy_params(target_connection=self.src.pk, target_entity=same.pk), expect=400)
        self.assertIn("source connection", err["error"])

    def test_name_is_required(self):
        self.assertIn("required", self.apply("copy-matching-fields", self.copy_params(name=" "), expect=400)["error"])

    def test_file_import_needs_a_file_backed_entity(self):
        params = self.copy_params()
        self.assertIn("uploaded file", self.apply("file-import", params, expect=400)["error"])
        self.items.source_file.name = "schemas/source_files/items.csv"
        self.items.save()
        self.assertEqual(self.apply("file-import", params)["kind"], "mapping")

    def test_fan_out_builds_one_pair_per_destination_across_connections(self):
        third = Connection.objects.create(name="Third", base_url="https://t.example.com", auth_type=Connection.AUTH_NONE)
        other = self.Entity.objects.create(connection=third, name="Other", endpoint_path="/other")
        self.Field.objects.create(entity=other, name="sku", field_type="string")
        body = self.apply("fan-out", {"name": "Fan", "source_connection": self.src.pk, "source_entity": self.items.pk,
                                      "target_entities": [self.customers.pk, other.pk]})
        m = Mapping.objects.get(pk=body["id"])
        self.assertEqual(m.entity_mappings.count(), 2)
        self.assertEqual({c.name for c in m.destination_connections.all()}, {"Dest", "Third"})
        self.assertEqual(len(body["notes"]), 2)

    def test_warns_when_a_destination_has_no_endpoint(self):
        self.customers.endpoint_path = ""
        self.customers.save()
        notes = self.apply("copy-matching-fields", self.copy_params())["notes"]
        self.assertTrue(any("no endpoint path" in n for n in notes))

    def test_fan_out_needs_at_least_one_destination(self):
        self.apply("fan-out", {"name": "Fan", "source_connection": self.src.pk, "source_entity": self.items.pk, "target_entities": []}, expect=400)


class ChainTemplateTests(TemplateTestBase):
    def test_create_then_link_captures_the_id_and_reuses_it(self):
        body = self.apply("create-then-link", {"name": "C", "connection": self.dst.pk, "create_path": "/customers", "create_body": '{"name": "x"}',
                                              "id_path": "data.id", "link_path": "/orders", "link_body": '{"customer_id": {{record_id}}}'})
        first, second = CallChain.objects.get(pk=body["id"]).steps.order_by("order")
        self.assertEqual((first.name, first.method, first.captures), ("create_record", "POST", [{"name": "record_id", "path": "data.id"}]))
        self.assertEqual((second.order, second.path, second.body), (2, "/orders", '{"customer_id": {{record_id}}}'))

    def test_bad_json_body_is_rejected_and_nothing_is_left_behind(self):
        err = self.apply("create-then-link", {"name": "Broken", "connection": self.dst.pk, "create_path": "/a", "create_body": "{nope",
                                             "link_path": "/b"}, expect=400)
        self.assertIn("valid JSON", err["error"])
        self.assertFalse(CallChain.objects.filter(name="Broken").exists())    # the whole template rolled back

    def test_list_then_fetch(self):
        body = self.apply("list-then-fetch", {"name": "L", "connection": self.dst.pk, "list_path": "/p", "id_path": "0.id", "detail_path": "/p/{{first_id}}"})
        steps = list(CallChain.objects.get(pk=body["id"]).steps.order_by("order"))
        self.assertEqual([s.name for s in steps], ["list_items", "get_item"])
        self.assertEqual(steps[0].captures, [{"name": "first_id", "path": "0.id"}])

    def test_async_export_is_one_polling_step(self):
        body = self.apply("async-export", {"name": "E", "connection": self.dst.pk, "start_path": "/exports", "poll_path": "/exports/{{start_job.id}}/status",
                                          "condition_path": "status", "condition_value": "done", "result_path": "/exports/{{start_job.id}}/download"})
        step = CallChain.objects.get(pk=body["id"]).steps.get()
        self.assertTrue(step.is_async)
        self.assertEqual((step.async_poll_path, step.async_condition_value, step.async_result_path),
                         ("/exports/{{start_job.id}}/status", "done", "/exports/{{start_job.id}}/download"))

    def test_unknown_connection_is_a_clear_error(self):
        self.assertIn("valid connection", self.apply("list-then-fetch", {"name": "L", "connection": 9999, "list_path": "/p", "detail_path": "/p/1"}, expect=400)["error"])


class PlanTemplateTests(TemplateTestBase):
    def test_orders_mixed_steps_and_stays_a_draft(self):
        body = self.apply("ordered-plan", {"name": "Seq", "steps": [{"kind": "mapping", "id": self.mapping.pk}, {"kind": "chain", "id": self.chain.pk}]})
        plan = MigrationPlan.objects.get(pk=body["id"])
        self.assertEqual((plan.status, plan.execution_mode), ("draft", "mixed"))
        self.assertEqual([(s.order, bool(s.chain_id)) for s in plan.steps.order_by("order")], [(1, False), (2, True)])

    def test_rejects_empty_and_malformed_steps_without_creating_a_plan(self):
        self.apply("ordered-plan", {"name": "Empty", "steps": []}, expect=400)
        self.apply("ordered-plan", {"name": "Bad", "steps": [{"kind": "nonsense", "id": 1}]}, expect=400)
        self.apply("ordered-plan", {"name": "Gone", "steps": [{"kind": "mapping", "id": 9999}]}, expect=400)
        self.assertFalse(MigrationPlan.objects.filter(name__in=["Empty", "Bad", "Gone"]).exists())
