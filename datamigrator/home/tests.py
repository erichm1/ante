import html as html_lib
import pathlib
import re

from django.contrib.auth import get_user_model
from django.contrib.staticfiles import finders
from django.test import TestCase

User = get_user_model()


class LandingPageTests(TestCase):
    def test_visitors_see_the_product_page(self):
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertIn("Ante", html)
        self.assertIn('href="/accounts/login/"', html)                       # the way in
        self.assertNotIn("/accounts/register/", html)                        # there is no public sign-up
        for section in ('id="features"', 'id="how"', 'id="control"'):
            self.assertIn(section, html)

    def test_it_shows_the_main_functionality_with_pictures(self):
        html = self.client.get("/").content.decode()
        images = re.findall(r'static/(landing/[\w-]+\.webp)', html)
        self.assertGreaterEqual(len(set(images)), 10)
        for name in set(images):
            self.assertIsNotNone(finders.find(name), f"{name} is referenced by the landing page but missing")
        for feature in ("Studio", "Data preview", "Chains", "Plans", "Templates", "permissions", "Logs"):
            self.assertIn(feature, html)

    def test_every_picture_has_alt_text_and_a_size(self):
        html = self.client.get("/").content.decode()
        for tag in re.findall(r"<img [^>]*landing/[^>]*>", html):
            self.assertRegex(tag, r'alt="[^"]{10,}"')
            self.assertRegex(tag, r'width="\d+" height="\d+"')

    def test_signed_in_users_go_straight_to_the_dashboard(self):
        self.client.force_login(User.objects.create_user("ada", password="pw"))
        self.assertRedirects(self.client.get("/"), "/home/", fetch_redirect_response=False)

    def test_everything_else_still_needs_a_login(self):
        for path in ("/home/", "/studio/", "/mappings/", "/notifications/"):
            resp = self.client.get(path)
            self.assertEqual(resp.status_code, 302, path)
            self.assertIn("/accounts/login/", resp["Location"])
        self.assertEqual(self.client.get("/api/mappings/").status_code, 401)

    def test_the_logo_and_favicon_are_on_every_kind_of_page(self):
        self.client.force_login(User.objects.create_user("ada", password="pw"))
        app_page = self.client.get("/home/").content.decode()
        self.client.logout()
        for html in (self.client.get("/").content.decode(), self.client.get("/accounts/login/").content.decode(), app_page):
            self.assertIn("img/logo.svg", html)
            self.assertRegex(html, r'<link rel="icon"[^>]*logo\.svg')
        # …and the sign-in page carries the same header (logo linking back to the front page) as the landing page
        login = self.client.get("/accounts/login/").content.decode()
        self.assertIn('class="lp-brand"', login)
        # …plus its own branded logo inside the card itself
        self.assertIn('class="auth-brand mb-4"', login)
        self.assertIn('class="auth-logo"', login)

    def test_the_login_page_links_to_the_status_page_and_it_opens_with_no_sign_in(self):
        html = self.client.get("/accounts/login/").content.decode()
        self.assertIn('href="/home/status/"', html)
        self.assertIn("System status", html)
        self.assertEqual(self.client.get("/home/status/").status_code, 200)   # the link isn't a dead end

    def test_the_landing_page_is_translated_like_everything_else(self):
        """Every visible string on the landing page has a translation in all seven other languages."""
        import importlib.util
        spec = importlib.util.spec_from_file_location("build", pathlib.Path(__file__).resolve().parent.parent / "i18n" / "build.py")
        build = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(build)
        known = build.tables(build.load_rows())["ja"]
        html = self.client.get("/").content.decode()
        body = re.sub(r"<(script|style)[\s\S]*?</\1>", "", html)
        texts = set()
        for chunk in re.findall(r">([^<>]+)<", body):
            t = re.sub(r"\s+", " ", html_lib.unescape(chunk)).strip()
            if re.search(r"[A-Za-z]{3,}", t):
                texts.add(t)
        for alt in re.findall(r'alt="([^"]+)"', body):
            texts.add(html_lib.unescape(alt))
        texts.add("Ante — Move data between systems, visually")            # <title>
        missing = sorted(t for t in texts if t not in known and t not in ("Ante", "REST APIs", "OAuth2, API keys, JWT"))
        self.assertEqual(missing, [], "landing text without a translation")


# ── Home, Incidents rules modal, status page ────────────────────────────────────────────────────────────────────
import json
from datetime import timedelta
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.utils import timezone

from connections.models import ApiCallLog, Connection
from incidents.models import Incident
from jobs.models import MigrationRun
from mappings.models import Mapping

from . import status
from .models import StatusCheckResult


class HomePageTests(TestCase):
    def setUp(self):
        self.client.force_login(User.objects.create_superuser("boss", password="pw"))

    def test_home_no_longer_carries_the_automation_rules_or_the_top_api_endpoints(self):
        page = self.client.get("/home/").content.decode()
        for gone in ("Automation rules", "newRuleModal", "editRuleModal", "topEndpointsBox", "Top API endpoints", "createRule"):
            self.assertFalse(gone in page, gone)
        self.assertTrue("connectionStatsBody" in page, "connectionStatsBody")                       # the rest of the dashboard is still there

    def test_the_stats_feed_drops_the_endpoint_ranking(self):
        data = self.client.get("/home/stats/").json()
        self.assertNotIn("top_endpoints", data)
        self.assertNotIn("total_api_calls", data)
        self.assertEqual(set(data), {"total", "active", "success", "failed", "connections", "active_runs"})


class IncidentRulesModalTests(TestCase):
    def setUp(self):
        self.client.force_login(User.objects.create_superuser("boss", password="pw"))

    def test_the_incidents_page_has_the_rules_modal_with_its_options(self):
        page = self.client.get("/incidents/").content.decode()
        self.assertTrue('data-bs-target="#rulesModal"' in page, 'data-bs-target="#rulesModal"')
        for hook in ("rulesCheckBtn", "rulesNewBtn", "rf_trigger", "rf_threshold", "rf_window", "rf_severity", "rf_connection", "rf_enabled"):
            self.assertTrue(f'id="{hook}"' in page, f'id="{hook}"')
        self.assertTrue("Run failure rate exceeds threshold" in page, "Run failure rate exceeds threshold")         # the trigger choices come from the model
        self.assertTrue("/home/rules/" in page, "/home/rules/")

    def test_the_rules_the_modal_manages_round_trip_through_the_api(self):
        made = self.client.post("/home/rules/", json.dumps({"name": "Fails", "trigger_type": "run_fail_rate", "threshold": 25, "window_hours": 2,
                                                            "severity": "high", "auto_title": "", "enabled": True, "connection": None}),
                                content_type="application/json")
        self.assertEqual(made.status_code, 201, made.content)
        rule = made.json()
        self.assertEqual((rule["threshold"], rule["window_hours"], rule["trigger_type_display"]), (25, 2, "Run failure rate exceeds threshold"))
        listed = self.client.get("/home/rules/").json()["results"]
        self.assertEqual([r["name"] for r in listed], ["Fails"])
        self.assertEqual(self.client.patch(f"/home/rules/{rule['id']}/", json.dumps({"enabled": False}), content_type="application/json").json()["enabled"], False)
        self.assertEqual(self.client.delete(f"/home/rules/{rule['id']}/").status_code, 204)
        self.assertEqual(self.client.get("/home/rules/").json()["results"], [])

    def test_check_now_opens_an_incident_when_a_rule_is_breached(self):
        mapping = Mapping.objects.create(name="M", source_connection=Connection.objects.create(name="S", base_url="https://s.example.com", auth_type="none"))
        for _ in range(3):
            MigrationRun.objects.create(mapping=mapping, status=MigrationRun.STATUS_FAILED)
        self.client.post("/home/rules/", json.dumps({"name": "Fails", "trigger_type": "run_fail_rate", "threshold": 50, "window_hours": 1, "severity": "high"}),
                         content_type="application/json")
        self.assertEqual(self.client.post("/home/check-rules/").json()["created"], 1)


class StatusEngineTests(TestCase):
    def conn(self, name="Api", **kw):
        return Connection.objects.create(name=name, base_url="https://api.example.com/v1", auth_type=kw.pop("auth_type", "none"), **kw)

    def rows(self, **where):
        return [r for r in status.run_checks() if all(r[k] == v for k, v in where.items())]

    def test_every_group_is_present_and_ordered(self):
        groups = list(dict.fromkeys(r["group"] for r in status.run_checks()))
        self.assertEqual(groups, ["Platform", "Ante API", "Console", "Activity"])            # no connections / calls yet
        self.assertEqual(status.overall_state(status.run_checks()), "OPERATIONAL")

    def test_pages_and_api_are_called_in_process_and_auth_walls_count_as_healthy(self):
        api = self.rows(group="Ante API")
        self.assertEqual(len(api), 8)
        self.assertTrue(all(r["state"] == "OPERATIONAL" and r["http_status"] in (401, 403, 302) and r["latency_ms"] is not None for r in api))
        (front,) = self.rows(name="Front page")
        self.assertEqual(front["http_status"], 200)

    def test_a_server_error_is_down_an_unexpected_code_is_degraded_an_exception_is_down(self):
        class Boom:
            def generic(self, method, path):
                if path == "/api/runs/":
                    raise RuntimeError("kaput")
                return mock.Mock(status_code={"/api/plans/": 500, "/api/chains/": 404}.get(path, 200))
        with mock.patch.object(status, "Client", lambda **kw: Boom()):
            by = {r["name"]: r for r in status.run_checks() if r["group"] == "Ante API"}
        self.assertEqual((by["Runs"]["state"], by["Runs"]["detail"]), ("DOWN", "kaput"))
        self.assertEqual(by["Plans"]["state"], "DOWN")
        self.assertEqual((by["Chains"]["state"], by["Chains"]["detail"]), ("DEGRADED", "Unexpected status 404"))
        self.assertEqual(by["Mappings"]["state"], "OPERATIONAL")

    def test_a_stalled_worker_degrades_and_all_stalled_is_down(self):
        stale = timezone.now() - timedelta(hours=1)
        import jobs.scheduler as jobs_scheduler
        import plans.scheduler as plans_scheduler
        import connections.scheduler as connections_scheduler
        with mock.patch.object(jobs_scheduler, "LAST_TICK_AT", stale):
            states = {r["name"]: r["state"] for r in self.rows(group="Platform")}
            self.assertEqual(states["Run scheduler"], "DEGRADED")
            self.assertEqual(status.overall_state(status.run_checks()), "DEGRADED")
            with mock.patch.object(plans_scheduler, "LAST_TICK_AT", stale), mock.patch.object(connections_scheduler, "LAST_TICK_AT", stale):
                self.assertEqual({r["state"] for r in self.rows(group="Platform") if "scheduler" in r["name"] or r["name"] == "Token refresh"}, {"DOWN"})

    def test_a_worker_that_has_not_ticked_yet_is_starting_not_a_problem(self):
        starting = [r for r in self.rows(group="Platform") if r.get("label")]
        self.assertTrue(starting and all(r["state"] == "OPERATIONAL" and r["label"] == "Starting…" for r in starting))

    def test_connections_show_setup_and_error_state_with_their_own_history(self):
        needs_setup = self.conn("Needs token", auth_type="bearer")
        healthy = self.conn("Healthy")
        flaky = self.conn("Flaky")
        for code in (200, 200, 200):
            ApiCallLog.objects.create(connection=healthy, method="GET", url="https://api.example.com/v1/items", status_code=code, duration_ms=100)
        for code in (200, 500, 500):
            ApiCallLog.objects.create(connection=flaky, method="GET", url="https://api.example.com/v1/items", status_code=code, duration_ms=300)
        by = {r["name"]: r for r in self.rows(group="Connections")}
        self.assertEqual((by["Needs token"]["state"], by["Needs token"]["detail"]), ("DEGRADED", "Needs setup."))
        self.assertEqual((by["Healthy"]["state"], by["Healthy"]["uptime_pct"], by["Healthy"]["latency_ms"]), ("OPERATIONAL", 100.0, 100.0))
        self.assertEqual((by["Flaky"]["state"], by["Flaky"]["uptime_pct"]), ("DOWN", 33.33))
        self.assertIn("2 of 3 API calls failed", by["Flaky"]["detail"])
        self.assertEqual([h["state"] for h in by["Flaky"]["history"]], ["OPERATIONAL", "DOWN", "DOWN"])   # oldest first
        self.assertTrue(by["Healthy"]["href"].endswith(f"/connections/{healthy.pk}/"))

    def test_run_and_api_call_rows_follow_the_failure_thresholds(self):
        mapping = Mapping.objects.create(name="M", source_connection=self.conn())
        for s in ("success", "success", "success", "failed"):
            MigrationRun.objects.create(mapping=mapping, status=s)
        (runs,) = self.rows(name="Migration runs")
        self.assertEqual((runs["state"], runs["uptime_pct"]), ("DEGRADED", 75.0))               # 25% failed ≥ 20%
        self.assertIn("1 of 4 recent migration runs failed (25%)", runs["detail"])
        self.assertEqual(status.overall_state(status.run_checks()), "DEGRADED")

    def test_history_is_stored_at_most_every_few_minutes_and_feeds_uptime(self):
        results = status.run_checks()
        self.assertTrue(status.record_if_stale(results))
        self.assertFalse(status.record_if_stale(results))                                        # too soon
        stored = StatusCheckResult.objects.count()
        self.assertEqual(stored, len([r for r in results if r["persist"]]))                      # activity rows are not stored
        StatusCheckResult.objects.update(created_at=timezone.now() - timedelta(minutes=10))
        StatusCheckResult.objects.filter(name="Database").update(state="DOWN")
        self.assertTrue(status.record_if_stale(results))
        (db,) = status.attach_history([r for r in status.run_checks() if r["name"] == "Database"])
        self.assertEqual([h["state"] for h in db["history"]], ["DOWN", "OPERATIONAL"])
        self.assertEqual(db["uptime_pct"], 50.0)

    def test_old_results_are_pruned(self):
        StatusCheckResult.objects.create(group="Platform", name="Database", state="OPERATIONAL")
        StatusCheckResult.objects.update(created_at=timezone.now() - timedelta(days=8))
        status.record(status.run_checks())
        self.assertFalse(StatusCheckResult.objects.filter(created_at__lt=timezone.now() - timedelta(days=7)).exists())

    def test_the_management_command_stores_the_checks_and_reports_the_overall_state(self):
        out = StringIO()
        call_command("run_status_checks", stdout=out)
        self.assertIn("Overall status: OPERATIONAL", out.getvalue())
        self.assertTrue(StatusCheckResult.objects.filter(group="Ante API", name="Mappings").exists())


class StatusPageTests(TestCase):
    def setUp(self):
        self.client.force_login(User.objects.create_superuser("boss", password="pw"))

    def test_it_is_a_standalone_page_with_the_callum_assets(self):
        page = self.client.get("/home/status/").content.decode()
        self.assertFalse('class="app-navbar"' in page, 'class="app-navbar"')
        self.assertTrue('<meta http-equiv="refresh" content="60">' in page, '<meta http-equiv="refresh" content="60">')
        for asset in ("css/status.css", "Space+Grotesk", "Inter", "IBM+Plex+Mono", "bootstrap@5.3.3", "bootstrap-icons@1.11.3", "js/i18n.js"):
            self.assertTrue(asset in page, asset)
        self.assertTrue("clm-banner st-operational" in page, "clm-banner st-operational")
        self.assertTrue("All systems operational" in page, "All systems operational")
        for group in ("Platform", "Ante API", "Console", "Activity"):
            self.assertTrue(f'<h2 class="h6 clm-display mb-3">{group}</h2>' in page, f'<h2 class="h6 clm-display mb-3">{group}</h2>')
        self.assertTrue("Refresh now" in page, "Refresh now")
        self.assertTrue("GET /home/status/data/" in page, "GET /home/status/data/")
        self.assertTrue("run_status_checks" in page, "run_status_checks")                                                  # no history yet → says how to build it

    def test_the_stylesheet_is_served_and_carries_the_ported_tokens(self):
        path = finders.find("css/status.css")
        self.assertIsNotNone(path)
        css = pathlib.Path(path).read_text()
        for token in ("--clm-ink: #0f1b2d", "--clm-success: #1d9a6c", "--clm-danger: #d64545", ".clm-kpi", ".clm-spark-bar", ".clm-banner.st-down",
                      ':root[data-bs-theme="dark"]', "Space Grotesk"):
            self.assertIn(token, css)

    def test_the_banner_and_rows_reflect_a_problem(self):
        c = Connection.objects.create(name="Broken", base_url="https://api.example.com", auth_type="none")
        for _ in range(3):
            ApiCallLog.objects.create(connection=c, method="GET", url="https://api.example.com/x", status_code=500)
        page = self.client.get("/home/status/").content.decode()
        self.assertTrue("clm-banner st-down" in page, "clm-banner st-down")
        self.assertTrue("Service disruption detected" in page, "Service disruption detected")
        self.assertTrue("Broken" in page, "Broken")
        self.assertTrue("clm-badge st-down" in page, "clm-badge st-down")
        self.assertTrue("3 of 3 API calls failed" in page, "3 of 3 API calls failed")
        self.assertTrue(f"/connections/{c.pk}/" in page, f"/connections/{c.pk}/")

    def test_a_second_visit_shows_history_and_uptime(self):
        self.client.get("/home/status/")
        StatusCheckResult.objects.update(created_at=timezone.now() - timedelta(minutes=10))
        page = self.client.get("/home/status/").content.decode()
        self.assertTrue("clm-spark-bar" in page, "clm-spark-bar")
        self.assertTrue("100.0% / 24h" in page, "100.0% / 24h")
        self.assertFalse("No historical uptime data yet" in page, "No historical uptime data yet")

    def test_the_json_feed_has_the_same_checks(self):
        data = self.client.get("/home/status/data/").json()
        self.assertEqual(data["status"], "OPERATIONAL")
        self.assertIn("checked_at", data)
        first = data["checks"][0]
        self.assertEqual(set(first), {"group", "name", "state", "http_status", "latency_ms", "uptime_pct", "detail"})
        self.assertIn(("Ante API", "Mappings"), {(c["group"], c["name"]) for c in data["checks"]})

    def test_the_page_itself_is_public_but_its_json_feed_still_needs_a_signed_in_user(self):
        """A status page only a signed-in user could reach would be useless exactly when it matters — when
        someone can't sign in. The JSON feed stays behind login, same as before."""
        self.client.logout()
        page = self.client.get("/home/status/")
        self.assertEqual(page.status_code, 200)
        self.assertIn("All systems operational", page.content.decode())
        self.assertEqual(self.client.get("/home/status/data/").status_code, 302)


class NavStatusTests(TestCase):
    """The navbar's Status dot (templates/base.html, home.context_processors.nav_status, /home/nav-status/) —
    a cheap, DB-only signal, deliberately simpler than the full status page: green with nothing going on,
    yellow for a worker/run/API-error/connection issue, red the moment any incident isn't resolved yet."""

    def setUp(self):
        self.client.force_login(User.objects.create_user("ada", password="pw"))

    def test_green_with_nothing_going_on(self):
        self.assertEqual(self.client.get("/home/nav-status/").json(), {"level": "green"})
        self.assertIn("nav-status-dot green", self.client.get("/home/").content.decode())

    def test_yellow_when_a_worker_has_stalled(self):
        import jobs.scheduler as jobs_scheduler
        with mock.patch.object(jobs_scheduler, "LAST_TICK_AT", timezone.now() - timedelta(hours=1)):
            self.assertEqual(self.client.get("/home/nav-status/").json(), {"level": "yellow"})
            self.assertIn("nav-status-dot yellow", self.client.get("/home/").content.decode())

    def test_red_when_an_incident_is_open_even_with_nothing_else_wrong(self):
        Incident.objects.create(title="Outage")
        self.assertEqual(self.client.get("/home/nav-status/").json(), {"level": "red"})
        self.assertIn("nav-status-dot red", self.client.get("/home/").content.decode())

    def test_a_resolved_incident_does_not_count(self):
        Incident.objects.create(title="Old", status=Incident.STATUS_RESOLVED)
        self.assertEqual(self.client.get("/home/nav-status/").json(), {"level": "green"})

    def test_red_beats_yellow(self):
        import jobs.scheduler as jobs_scheduler
        Incident.objects.create(title="Outage")
        with mock.patch.object(jobs_scheduler, "LAST_TICK_AT", timezone.now() - timedelta(hours=1)):
            self.assertEqual(self.client.get("/home/nav-status/").json(), {"level": "red"})

    def test_it_is_not_gated_by_the_status_module_unlike_the_status_page_itself(self):
        from accounts.models import Profile

        self.client.logout()
        nobody = User.objects.create_user("nobody", password="pw")
        Profile.objects.get_or_create(user=nobody)
        Profile.objects.filter(user=nobody).update(denied_modules=["status"])         # explicitly locked out of it
        self.client.force_login(nobody)
        self.assertEqual(self.client.get("/home/nav-status/").status_code, 200)
        self.assertEqual(self.client.get("/home/status/").status_code, 302)           # redirected home, no module

    def test_it_still_needs_a_signed_in_user(self):
        self.client.logout()
        self.assertEqual(self.client.get("/home/nav-status/").status_code, 302)
