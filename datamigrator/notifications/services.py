"""Creating notifications. Called from the places a run / chain / plan ends (jobs/engine.py, chains/executor.py,
plans/executor.py and the cancel endpoints). Everything here is best-effort: a notification problem must never
break — or even slow down noticeably — the run it is reporting on, so failures are logged and swallowed.

Who is told: every active user who has the relevant module (jobs for migration runs, chains, plans) — runs have no
owner, so "who may see runs" is the audience. A run/chain run that belongs to a plan is NOT announced on its own:
the plan's notification covers it, so a five-step plan is one notification, not six.
"""
import logging

from accounts import permissions

from .models import Notification

log = logging.getLogger(__name__)

VERB = {"success": "succeeded", "failed": "failed", "cancelled": "was cancelled", "killed": "was killed"}


def notify(kind, outcome, title, message, url, module, source_id=None):
    """Create one Notification per user who may see `module`. Returns how many were created."""
    try:
        rows = [
            Notification(recipient=user, kind=kind, outcome=outcome, title=title[:200], message=message[:500], url=url, source_id=source_id)
            for user in permissions.users_with_module(module)
        ]
        Notification.objects.bulk_create(rows)
        return len(rows)
    except Exception:  # noqa: BLE001 — never let a notification break the run it reports on
        log.exception("Could not create notifications for %s %s", kind, source_id)
        return 0


def notify_run(run, outcome):
    """A migration run ended. `outcome` is success / failed / cancelled / killed."""
    try:
        from plans.models import PlanStep
        if PlanStep.objects.filter(run=run).exists():
            return 0                                # part of a plan: the plan reports it
        scheduled = " (scheduled)" if run.scheduled_at else ""
        return notify(
            Notification.KIND_RUN, outcome, f"Run #{run.pk} · {run.mapping.name}{scheduled} {VERB[outcome]}",
            f"{run.records_read} read · {run.records_written} written · {run.records_failed} failed",
            f"/jobs/runs/{run.pk}/", "jobs", run.pk)
    except Exception:  # noqa: BLE001
        log.exception("notify_run failed for run %s", getattr(run, "pk", None))
        return 0


def notify_chain_run(chain_run, outcome):
    """A chain run ended (a plan's function blocks record on a hidden chain — those are the plan's business)."""
    try:
        from plans.models import MigrationPlan, PlanStep
        chain = chain_run.chain
        if chain.hidden or PlanStep.objects.filter(chain_run=chain_run).exists() or MigrationPlan.objects.filter(inline_run=chain_run).exists():
            return 0
        results = list(chain_run.step_results.all())
        done = sum(1 for r in results if not r.error)
        detail = f"{done} of {chain.steps.count()} step(s) ok"
        failed = next((r for r in results if r.error and r.error != "Cancelled by user."), None)
        if failed and outcome == "failed":
            detail += f" · “{failed.name}”: {failed.error[:120]}"
        return notify(
            Notification.KIND_CHAIN, outcome, f"Chain “{chain.name}” run #{chain_run.pk} {VERB[outcome]}", detail,
            f"/chains/{chain.pk}/runs/{chain_run.pk}/", "chains", chain_run.pk)
    except Exception:  # noqa: BLE001
        log.exception("notify_chain_run failed for run %s", getattr(chain_run, "pk", None))
        return 0


def notify_plan(plan, outcome):
    """A plan (custom run) finished, was killed, or had its schedule cancelled."""
    try:
        steps = list(plan.steps.all())
        started = sum(1 for s in steps if s.run_id or s.chain_run_id or s.finished_at)
        detail = f"{started} of {len(steps)} step(s) reached" if outcome != "success" else f"{len(steps)} step(s) completed"
        return notify(
            Notification.KIND_PLAN, outcome, f"Plan “{plan.name}” {VERB[outcome]}", detail,
            f"/plans/{plan.pk}/", "plans", plan.pk)
    except Exception:  # noqa: BLE001
        log.exception("notify_plan failed for plan %s", getattr(plan, "pk", None))
        return 0
