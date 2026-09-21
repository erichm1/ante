"""Sequential plan execution. Mirrors jobs/engine.py's own scope ("good
enough for a demo scaffold, swap for a real task queue in production"): a
single daemon thread walks a plan's steps in order, and — unlike a normal
trigger — calls jobs.engine.run_migration() (simple mode) or
chains.executor.run_chain() (chain mode) directly and synchronously for
each one, since this thread *is* the sequencer and must block on step N
before starting step N+1. For production, this loop is exactly what a
Celery chain (or chord-of-chains) would express instead.
"""
import threading
import time

from django.db import connections
from django.utils import timezone

from chains import executor as chains_executor
from chains.models import CallChainRun
from jobs import engine
from jobs.models import MigrationRun

MAX_WAIT_SECONDS = 3600      # plans run in a background thread, so a long pause is fine — but not forever


# Ids of plans THIS process is executing. An "executing" plan that isn't in here was orphaned (its thread died,
# e.g. a dev-server restart), so a kill closes it immediately rather than waiting for a thread that isn't there.
ACTIVE_PLANS = set()
SLICE_SECONDS = 0.5


def _cancelled(plan_id: int) -> bool:
    from .models import MigrationPlan
    return MigrationPlan.objects.filter(pk=plan_id, cancel_requested=True).exists()


def _pause(plan_id: int, seconds: float) -> bool:
    """Sleep for `seconds` in short slices; returns False (early) if the plan was killed meanwhile."""
    waited = 0.0
    while waited < seconds:
        if _cancelled(plan_id):
            return False
        chunk = min(SLICE_SECONDS, seconds - waited)
        time.sleep(chunk)
        waited += chunk
    return not _cancelled(plan_id)


def execute_plan_in_background(plan_id: int):
    from .models import MigrationPlan  # local import: plans -> jobs/chains is fine, the reverse isn't

    ACTIVE_PLANS.add(plan_id)
    runner, runner_open = None, False
    try:
        plan = MigrationPlan.objects.get(pk=plan_id)
        all_succeeded, cancelled = True, False
        # A fresh execution starts from a clean slate: no step shows the previous run's result.
        plan.steps.update(run=None, chain_run=None, started_at=None, finished_at=None)
        plan.inline_run = None
        plan.save(update_fields=["inline_run"])

        # Dispatch on each step's own target rather than the plan's execution_mode:
        # simple/chain plans only ever hold one kind anyway, and a mixed plan
        # holds both, so this one loop covers all three.
        for step in plan.steps.select_related("mapping", "chain", "inline_step", "inline_step__chain").order_by("order"):
            if _cancelled(plan_id):
                cancelled = True
                break

            if step.wait_seconds is not None:
                step.started_at = timezone.now()
                step.save(update_fields=["started_at"])
                if not _pause(plan_id, min(max(step.wait_seconds, 0), MAX_WAIT_SECONDS)):
                    cancelled = True
                    break
                step.finished_at = timezone.now()
                step.save(update_fields=["finished_at"])

            elif step.inline_step_id:
                # A function block (request / find / check / next page / file preview…). They all run on ONE
                # shared runner, so a later block can use what an earlier one fetched or captured.
                step.started_at = timezone.now()
                step.save(update_fields=["started_at"])
                if runner is None:
                    inline_run = CallChainRun.objects.create(chain=step.inline_step.chain, status=CallChainRun.STATUS_FAILED)
                    plan.inline_run = inline_run
                    plan.save(update_fields=["inline_run"])
                    runner = chains_executor.Runner(inline_run, should_cancel=lambda: _cancelled(plan_id))
                    runner_open = True
                outcome = runner.execute_step(step.inline_step)
                step.finished_at = timezone.now()
                step.save(update_fields=["finished_at"])
                if outcome == "cancelled":
                    cancelled = True
                    break
                if outcome != "ok":
                    all_succeeded = False
                    break               # the blocks depend on each other's results: a failed one stops the run

            elif step.chain_id:
                chain_run = CallChainRun.objects.create(chain=step.chain, status=CallChainRun.STATUS_FAILED)
                step.chain_run = chain_run
                step.save(update_fields=["chain_run"])

                chains_executor.run_chain(chain_run)  # blocks this thread until the step finishes

                if chain_run.status == CallChainRun.STATUS_CANCELLED:
                    cancelled = True
                    break
                if chain_run.status != CallChainRun.STATUS_SUCCESS:
                    all_succeeded = False
            else:
                run = MigrationRun.objects.create(
                    mapping=step.mapping,
                    status=MigrationRun.STATUS_RUNNING,
                    rate_limit_per_second=step.rate_limit_per_second or plan.rate_limit_per_second,
                )
                step.run = run
                step.save(update_fields=["run"])

                # A step can run only some of the mapping's entity pairs (empty = all of them).
                engine.run_migration(run, entity_mapping_ids=step.entity_mapping_ids or None)  # blocks until the step finishes

                if run.status == MigrationRun.STATUS_CANCELLED:
                    cancelled = True
                    break
                if run.status != MigrationRun.STATUS_SUCCESS:
                    all_succeeded = False

        if runner is not None:
            runner.finish("cancelled" if cancelled else "ok" if all_succeeded else "failed")
            runner_open = False
        plan.status = (MigrationPlan.STATUS_CANCELLED if cancelled
                       else MigrationPlan.STATUS_COMPLETED if all_succeeded else MigrationPlan.STATUS_FAILED)
        plan.save(update_fields=["status"])
        from notifications.services import notify_plan
        notify_plan(plan, "killed" if cancelled else "success" if all_succeeded else "failed")
    finally:
        if runner is not None and runner_open:          # a crash mid-way: don't leave the inline run "running" forever
            runner.finish("failed")
        ACTIVE_PLANS.discard(plan_id)
        connections.close_all()


def start_plan(plan_id: int):
    threading.Thread(target=execute_plan_in_background, args=(plan_id,), daemon=True).start()
