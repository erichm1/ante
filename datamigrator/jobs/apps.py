import os
import sys

from django.apps import AppConfig


class JobsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "jobs"

    def ready(self):
        # ready() fires for every manage.py command (migrate, shell, test, ...),
        # not just runserver — and under the dev autoreloader, runserver itself
        # forks into a watcher + a RUN_MAIN=true child that actually serves
        # requests. Only that child should own the scheduler thread, or a
        # migrate/shell invocation would spin up a poller that immediately has
        # nothing to do but never exits, and the watcher process would double it.
        if "runserver" not in sys.argv or os.environ.get("RUN_MAIN") != "true":
            return
        from . import scheduler
        scheduler.start()
