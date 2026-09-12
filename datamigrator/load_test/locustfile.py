"""
Hard load test for the datamigrator application.

Usage (headless):
  locust -f load_test/locustfile.py --headless \
    -u 100 -r 10 -t 2m \
    --host http://127.0.0.1:8000 \
    --csv load_test/results/run

Usage (with web UI):
  locust -f load_test/locustfile.py --host http://127.0.0.1:8000
  # then open http://localhost:8089

Credentials: LOAD_TEST_USER / LOAD_TEST_PASS env vars (defaults: admin / admin).

Auth strategy
-------------
A custom LoginRequiredMiddleware gates the entire app before DRF even runs,
so HTTP Basic Auth is intercepted and rejected at the middleware layer.
All users perform a real Django session login at startup. GET requests need
no CSRF token (Django exempts safe methods). Mutating requests (POST/DELETE)
include the csrftoken cookie value as the X-CSRFToken header, which is how
the browser JS in the app already works.
"""

import os
import random

from locust import HttpUser, TaskSet, between, task

# ---------------------------------------------------------------------------
# Fixture IDs
# ---------------------------------------------------------------------------

CONNECTION_IDS = [27, 30, 31]
ENTITY_IDS = [461, 470, 502, 503]
RUN_IDS = [94, 95, 96, 101, 102, 104, 105, 106, 107, 108]
MAPPING_IDS = [24]
CHAIN_IDS = [28]
REPORT_IDS = [4, 311]         # all reports — used for list/detail reads
SAFE_REPORT_IDS = [311]       # CSV-file-only reports — safe for preview/export (no external API)

USERNAME = os.environ.get("LOAD_TEST_USER", "loadtest")
PASSWORD = os.environ.get("LOAD_TEST_PASS", "loadtest123!")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _csrf(client):
    return client.cookies.get("csrftoken", "")


def _login(client):
    """Session login — seeds the csrftoken cookie then POSTs credentials."""
    client.get("/accounts/login/", name="_csrf_seed")
    token = _csrf(client)
    resp = client.post(
        "/accounts/login/",
        data={"username": USERNAME, "password": PASSWORD},
        headers={"X-CSRFToken": token, "Referer": client.base_url + "/accounts/login/"},
        allow_redirects=True,
        name="auth/login",
    )
    return resp.status_code == 200


def _api_get(client, url, name):
    client.get(url, name=name)


def _api_post(client, url, payload, name):
    client.post(
        url,
        json=payload,
        headers={"X-CSRFToken": _csrf(client), "Content-Type": "application/json"},
        name=name,
    )


def _api_delete(client, url, name):
    client.delete(url, headers={"X-CSRFToken": _csrf(client)}, name=name)


# ---------------------------------------------------------------------------
# Task sets
# ---------------------------------------------------------------------------

class APIReadTasks(TaskSet):
    """Hammers every list + detail endpoint with GET requests."""

    @task(5)
    def connections_list(self):
        _api_get(self.client, "/api/connections/", "api/connections [list]")

    @task(3)
    def connection_detail(self):
        _api_get(self.client, f"/api/connections/{random.choice(CONNECTION_IDS)}/", "api/connections [detail]")

    @task(5)
    def entities_list(self):
        _api_get(self.client, "/api/entities/", "api/entities [list]")

    @task(3)
    def entity_detail(self):
        _api_get(self.client, f"/api/entities/{random.choice(ENTITY_IDS)}/", "api/entities [detail]")

    @task(4)
    def mappings_list(self):
        _api_get(self.client, "/api/mappings/", "api/mappings [list]")

    @task(2)
    def mapping_detail(self):
        _api_get(self.client, f"/api/mappings/{random.choice(MAPPING_IDS)}/", "api/mappings [detail]")

    @task(4)
    def runs_list(self):
        _api_get(self.client, "/api/runs/", "api/runs [list]")

    @task(3)
    def run_detail(self):
        _api_get(self.client, f"/api/runs/{random.choice(RUN_IDS)}/", "api/runs [detail]")

    @task(2)
    def chains_list(self):
        _api_get(self.client, "/api/chains/", "api/chains [list]")

    @task(1)
    def chain_detail(self):
        _api_get(self.client, f"/api/chains/{random.choice(CHAIN_IDS)}/", "api/chains [detail]")

    @task(3)
    def reports_list(self):
        _api_get(self.client, "/api/reports/", "api/reports [list]")

    @task(2)
    def report_detail(self):
        _api_get(self.client, f"/api/reports/{random.choice(REPORT_IDS)}/", "api/reports [detail]")

    @task(2)
    def plans_list(self):
        _api_get(self.client, "/api/plans/", "api/plans [list]")


class PageViewTasks(TaskSet):
    """Simulates browser navigation through the main HTML pages."""

    @task(3)
    def jobs_page(self):
        self.client.get("/jobs/", name="page/jobs")

    @task(2)
    def connections_page(self):
        self.client.get("/connections/", name="page/connections")

    @task(2)
    def mappings_page(self):
        self.client.get("/mappings/", name="page/mappings")

    @task(2)
    def plans_page(self):
        self.client.get("/plans/", name="page/plans")

    @task(2)
    def chains_page(self):
        self.client.get("/chains/", name="page/chains")

    @task(2)
    def reports_page(self):
        self.client.get("/reports/", name="page/reports")

    @task(1)
    def home_page(self):
        self.client.get("/home/", name="page/home")

    @task(1)
    def run_detail_page(self):
        self.client.get(f"/jobs/runs/{random.choice(RUN_IDS)}/", name="page/run-detail")

    @task(1)
    def report_detail_page(self):
        self.client.get(f"/reports/{random.choice(REPORT_IDS)}/", name="page/report-detail")

    @task(1)
    def chain_detail_page(self):
        self.client.get(f"/chains/{random.choice(CHAIN_IDS)}/", name="page/chain-detail")

    @task(1)
    def mapping_detail_page(self):
        self.client.get(f"/mappings/{random.choice(MAPPING_IDS)}/", name="page/mapping-detail")


class ReportHeavyTasks(TaskSet):
    """
    Hammers the two most expensive report endpoints:
    - preview  → reads real entity records, builds preview tables
    - export   → same but streams a full CSV
    """

    @task(4)
    def report_preview(self):
        _api_get(self.client, f"/api/reports/{random.choice(SAFE_REPORT_IDS)}/preview/", "api/reports [preview]")

    @task(2)
    def report_export(self):
        pk = random.choice(SAFE_REPORT_IDS)
        with self.client.get(
            f"/api/reports/{pk}/export/",
            name="api/reports [export csv]",
            catch_response=True,
            stream=True,
        ) as resp:
            _ = resp.content
            if resp.status_code == 200:
                resp.success()
            else:
                resp.failure(f"HTTP {resp.status_code}")

    @task(2)
    def entity_fields_list(self):
        _api_get(self.client, f"/api/fields/?entity={random.choice(ENTITY_IDS)}", "api/fields [by-entity]")


class WriteAndCleanTasks(TaskSet):
    """Creates resources then immediately deletes them.
    Excluded: run triggers (those fire real external API calls)."""

    def on_start(self):
        self._created = []
        self._logged_in = bool(self.client.cookies.get("sessionid"))

    @task(3)
    def create_delete_report(self):
        if not self.client.cookies.get("sessionid"):
            return  # skip if not authenticated yet
        resp = self.client.post(
            "/api/reports/",
            json={"name": f"load-test-{random.randint(10000, 99999)}", "description": ""},
            headers={"X-CSRFToken": _csrf(self.client), "Content-Type": "application/json"},
            name="api/reports [create]",
        )
        if resp.status_code == 201:
            pk = resp.json().get("id")
            if pk:
                self._created.append(pk)
                self.client.delete(
                    f"/api/reports/{pk}/",
                    headers={"X-CSRFToken": _csrf(self.client)},
                    name="api/reports [delete]",
                )

    @task(1)
    def purge_leftover_reports(self):
        if not self._created or not self.client.cookies.get("sessionid"):
            return
        pk = self._created.pop()
        with self.client.delete(
            f"/api/reports/{pk}/",
            headers={"X-CSRFToken": _csrf(self.client)},
            name="api/reports [delete-cleanup]",
            catch_response=True,
        ) as resp:
            if resp.status_code in (204, 404):
                resp.success()  # 404 = already deleted; both are fine


# ---------------------------------------------------------------------------
# User types  (all use session auth — middleware gates before DRF)
# ---------------------------------------------------------------------------

class ReadHeavyUser(HttpUser):
    """60 % of virtual users — pure API read load."""
    weight = 6
    wait_time = between(0.05, 0.3)

    def on_start(self):
        _login(self.client)

    tasks = [APIReadTasks]


class BrowserUser(HttpUser):
    """20 % of virtual users — browser-like page navigation."""
    weight = 2
    wait_time = between(0.2, 1.0)

    def on_start(self):
        _login(self.client)

    tasks = [PageViewTasks]


class ReportUser(HttpUser):
    """10 % of virtual users — hammers expensive report endpoints."""
    weight = 1
    wait_time = between(0.1, 0.5)

    def on_start(self):
        _login(self.client)

    tasks = [ReportHeavyTasks]


class WriterUser(HttpUser):
    """10 % of virtual users — create/delete cycles."""
    weight = 1
    wait_time = between(0.3, 1.0)

    def on_start(self):
        _login(self.client)

    tasks = [WriteAndCleanTasks]
