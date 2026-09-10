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

from django.db import connections
from django.utils import timezone

from chains import executor as chains_executor
from chains.models import CallChainRun
from jobs import engine
from jobs.models import MigrationRun


def execute_plan_in_background(plan_id: int):
    from .models import MigrationPlan  # local import: plans -> jobs/chains is fine, the reverse isn't

    try:
        plan = MigrationPlan.objects.get(pk=plan_id)
        all_succeeded = True

        if plan.execution_mode == MigrationPlan.MODE_CHAIN:
            for step in plan.steps.select_related("chain").order_by("order"):
                chain_run = CallChainRun.objects.create(chain=step.chain, status=CallChainRun.STATUS_FAILED)
                step.chain_run = chain_run
                step.save(update_fields=["chain_run"])

                chains_executor.run_chain(chain_run)  # blocks this thread until the step finishes

                if chain_run.status != CallChainRun.STATUS_SUCCESS:
                    all_succeeded = False
        else:
            for step in plan.steps.select_related("mapping").order_by("order"):
                run = MigrationRun.objects.create(
                    mapping=step.mapping,
                    status=MigrationRun.STATUS_RUNNING,
                    rate_limit_per_second=step.rate_limit_per_second or plan.rate_limit_per_second,
                )
                step.run = run
                step.save(update_fields=["run"])

                engine.run_migration(run)  # blocks this thread until the step finishes

                if run.status != MigrationRun.STATUS_SUCCESS:
                    all_succeeded = False

        plan.status = MigrationPlan.STATUS_COMPLETED if all_succeeded else MigrationPlan.STATUS_FAILED
        plan.save(update_fields=["status"])
    finally:
        connections.close_all()


def start_plan(plan_id: int):
    threading.Thread(target=execute_plan_in_background, args=(plan_id,), daemon=True).start()
