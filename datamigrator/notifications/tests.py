from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from accounts.models import AccessGroup
from chains import executor as chain_executor
from chains.models import CallChain, CallChainRun, CallChainStep
from connections.models import Connection
from jobs import engine
from jobs.models import MigrationRun
from mappings.models import Mapping
from plans import executor as plan_executor
from plans.models import MigrationPlan, PlanStep

from .models import Notification
from .services import notify_chain_run, notify_plan, notify_run

User = get_user_model()


class FakeResponse:
    status_code, headers, content = 200, {}, b"x"

    def json(self):
        return {}

    def raise_for_status(self):
        pass


class FakeClient:
    fail = False

    def __init__(self, connection, run=None):
        pass

    def request(self, method, path, **kwargs):
        if FakeClient.fail:
            raise RuntimeError("boom")
        return FakeResponse()


class NotificationTestBase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("ada", password="pw")            # gets the default group: jobs, chains, plans…
        self.client.force_login(self.user)
        self.conn = Connection.objects.create(name="Conn", base_url="https://x.example.com", auth_type=Connection.AUTH_NONE)
        self.mapping = Mapping.objects.create(name="Orders", source_connection=self.conn)
        self.chain = CallChain.objects.create(name="Sync", connection=self.conn)
        CallChainStep.objects.create(chain=self.chain, order=1, name="one", method="GET", path="/one")
        FakeClient.fail = False
        patcher = mock.patch.object(chain_executor, "ConnectionClient", FakeClient)
        patcher.start()
        self.addCleanup(patcher.stop)

    def mine(self, **filters):
        return Notification.objects.filter(recipient=self.user, **filters)


class WhoIsToldTests(NotificationTestBase):
    def test_everyone_with_the_module_is_told_and_nobody_else(self):
        grace = User.objects.create_user("grace", password="pw")
        AccessGroup.objects.filter(is_default=True).first().members.remove(grace.profile)
        grace.profile.groups.clear()                                            # grace has no modules at all
        run = MigrationRun.objects.create(mapping=self.mapping, status=MigrationRun.STATUS_SUCCESS)

        notify_run(run, "success")

        self.assertEqual(self.mine().count(), 1)
        self.assertFalse(Notification.objects.filter(recipient=grace).exists())

    def test_a_per_user_denial_is_respected(self):
        self.user.profile.denied_modules = ["jobs"]
        self.user.profile.save()
        notify_run(MigrationRun.objects.create(mapping=self.mapping, status=MigrationRun.STATUS_SUCCESS), "success")
        self.assertEqual(self.mine().count(), 0)

    def test_inactive_users_are_not_told(self):
        User.objects.filter(pk=self.user.pk).update(is_active=False)
        notify_run(MigrationRun.objects.create(mapping=self.mapping, status=MigrationRun.STATUS_SUCCESS), "success")
        self.assertEqual(Notification.objects.count(), 0)


class ChainNotificationTests(NotificationTestBase):
    def run_chain(self):
        run = CallChainRun.objects.create(chain=self.chain, status=CallChainRun.STATUS_FAILED)
        chain_executor.run_chain(run)
        return run

    def test_success(self):
        run = self.run_chain()
        n = self.mine(kind="chain").get()
        self.assertEqual((n.outcome, n.level, n.source_id), ("success", "ok", run.pk))
        self.assertIn("Sync", n.title)
        self.assertEqual(n.url, f"/chains/{self.chain.pk}/runs/{run.pk}/")

    def test_failure_names_the_step_that_broke(self):
        FakeClient.fail = True
        self.run_chain()
        n = self.mine(kind="chain").get()
        self.assertEqual((n.outcome, n.level), ("failed", "err"))
        self.assertIn("boom", n.message)

    def test_killing_a_run_whose_process_died_reports_killed(self):
        run = CallChainRun.objects.create(chain=self.chain, status=CallChainRun.STATUS_FAILED)
        CallChainRun.objects.filter(pk=run.pk).update(finished_at=None)
        chain_executor.cancel_runs(CallChainRun.objects.filter(pk=run.pk))
        self.assertEqual(self.mine(kind="chain").get().outcome, "killed")

    def test_the_hidden_chain_behind_a_plans_function_blocks_is_silent(self):
        self.chain.hidden = True
        self.chain.save()
        run = CallChainRun.objects.create(chain=self.chain, status=CallChainRun.STATUS_SUCCESS)
        self.assertEqual(notify_chain_run(run, "success"), 0)
        self.assertEqual(Notification.objects.count(), 0)

    def test_a_chain_run_that_belongs_to_a_plan_is_reported_by_the_plan_only(self):
        plan = MigrationPlan.objects.create(name="P", execution_mode=MigrationPlan.MODE_MIXED)
        PlanStep.objects.create(plan=plan, chain=self.chain, order=1)
        plan_executor.execute_plan_in_background(plan.pk)
        self.assertEqual(list(self.mine().values_list("kind", "outcome")), [("plan", "success")])


class RunNotificationTests(NotificationTestBase):
    def test_a_queued_run_that_is_cancelled_says_cancelled(self):
        run = MigrationRun.objects.create(mapping=self.mapping, status=MigrationRun.STATUS_PENDING)
        engine.cancel_runs(MigrationRun.objects.filter(pk=run.pk))
        n = self.mine().get()
        self.assertEqual((n.kind, n.outcome, n.level), ("run", "cancelled", "warn"))
        self.assertEqual(n.url, f"/jobs/runs/{run.pk}/")

    def test_a_run_that_was_running_and_is_stopped_says_killed(self):
        run = MigrationRun.objects.create(mapping=self.mapping, status=MigrationRun.STATUS_RUNNING)      # orphaned: not in ACTIVE_RUNS
        engine.cancel_runs(MigrationRun.objects.filter(pk=run.pk))
        self.assertEqual(self.mine().get().outcome, "killed")

    def test_a_scheduled_run_is_labelled_as_such(self):
        run = MigrationRun.objects.create(mapping=self.mapping, status=MigrationRun.STATUS_SUCCESS, scheduled_at=timezone.now())
        notify_run(run, "success")
        self.assertIn("(scheduled)", self.mine().get().title)

    def test_a_run_inside_a_plan_is_not_announced_on_its_own(self):
        plan = MigrationPlan.objects.create(name="P", execution_mode=MigrationPlan.MODE_MIXED)
        run = MigrationRun.objects.create(mapping=self.mapping, status=MigrationRun.STATUS_SUCCESS)
        PlanStep.objects.create(plan=plan, mapping=self.mapping, order=1, run=run)
        self.assertEqual(notify_run(run, "success"), 0)

    def test_a_notification_problem_never_breaks_the_run(self):
        run = MigrationRun.objects.create(mapping=self.mapping, status=MigrationRun.STATUS_SUCCESS)
        with mock.patch("notifications.services.Notification.objects.bulk_create", side_effect=RuntimeError("db down")):
            with self.assertLogs("notifications.services", level="ERROR"):
                self.assertEqual(notify_run(run, "success"), 0)


class PlanNotificationTests(NotificationTestBase):
    def test_a_killed_plan(self):
        plan = MigrationPlan.objects.create(name="Nightly", execution_mode=MigrationPlan.MODE_MIXED, status="executing")
        resp = self.client.post(f"/api/plans/{plan.pk}/cancel/")
        self.assertEqual(resp.json()["status"], "cancelled")
        n = self.mine(kind="plan").get()
        self.assertEqual((n.outcome, n.url), ("killed", f"/plans/{plan.pk}/"))

    def test_a_scheduled_plan_that_is_called_off_is_cancelled_not_killed(self):
        plan = MigrationPlan.objects.create(name="Later", execution_mode=MigrationPlan.MODE_MIXED, status="scheduled",
                                            scheduled_at=timezone.now() + timezone.timedelta(hours=1))
        self.client.post(f"/api/plans/{plan.pk}/cancel/")
        self.assertEqual(self.mine(kind="plan").get().outcome, "cancelled")

    def test_a_failed_plan(self):
        FakeClient.fail = True
        plan = MigrationPlan.objects.create(name="P", execution_mode=MigrationPlan.MODE_MIXED)
        PlanStep.objects.create(plan=plan, chain=self.chain, order=1)
        plan_executor.execute_plan_in_background(plan.pk)
        self.assertEqual(self.mine(kind="plan").get().outcome, "failed")

    def test_notify_plan_directly(self):
        plan = MigrationPlan.objects.create(name="P", execution_mode=MigrationPlan.MODE_MIXED)
        self.assertEqual(notify_plan(plan, "success"), 1)


class FeedAndPageTests(NotificationTestBase):
    def make(self, user=None, **kw):
        return Notification.objects.create(recipient=user or self.user, kind="run", outcome=kw.pop("outcome", "success"),
                                           title=kw.pop("title", "Run #1"), url=kw.pop("url", "/jobs/"), **kw)

    def test_feed_has_the_unread_count_and_the_latest_items(self):
        self.make(title="a")
        read = self.make(title="b")
        Notification.objects.filter(pk=read.pk).update(read_at=timezone.now())
        body = self.client.get("/notifications/feed/").json()
        self.assertEqual(body["unread"], 1)
        self.assertEqual([i["title"] for i in body["items"]], ["b", "a"])
        self.assertEqual(body["items"][0]["read"], True)

    def test_nobody_sees_anyone_elses(self):
        other = User.objects.create_user("bob", password="pw")
        secret = self.make(user=other, title="secret")
        self.assertEqual(self.client.get("/notifications/feed/").json()["items"], [])
        self.assertEqual(self.client.get(f"/notifications/{secret.pk}/go/").status_code, 404)
        secret.refresh_from_db()
        self.assertIsNone(secret.read_at)
        self.client.post("/notifications/read/")
        secret.refresh_from_db()
        self.assertIsNone(secret.read_at)                                       # mark-all only touches the caller's

    def test_opening_one_marks_it_read_and_redirects(self):
        n = self.make(url="/jobs/runs/7/")
        resp = self.client.get(f"/notifications/{n.pk}/go/")
        self.assertRedirects(resp, "/jobs/runs/7/", fetch_redirect_response=False)
        n.refresh_from_db()
        self.assertIsNotNone(n.read_at)

    def test_mark_all_read(self):
        self.make(), self.make()
        self.assertEqual(self.client.post("/notifications/read/").json()["updated"], 2)
        self.assertEqual(self.client.get("/notifications/feed/").json()["unread"], 0)

    def test_the_page_filters(self):
        self.make(title="good", outcome="success")
        self.make(title="bad", outcome="failed")
        html = self.client.get("/notifications/?outcome=failed").content.decode()
        self.assertIn("bad", html)
        self.assertNotIn(">good<", html.replace("\n", ""))
        self.assertEqual(self.client.get("/notifications/?outcome=nonsense&kind=zzz").status_code, 200)

    def test_it_needs_a_login(self):
        self.client.logout()
        self.assertEqual(self.client.get("/notifications/feed/").status_code, 302)

    def test_writing_to_the_feed_is_not_possible(self):
        self.assertEqual(self.client.post("/notifications/feed/").status_code, 405)
        self.assertEqual(self.client.get("/notifications/read/").status_code, 405)
