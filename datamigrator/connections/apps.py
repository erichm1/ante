import os
import sys

from django.apps import AppConfig


class ConnectionsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "connections"

    def ready(self):
        # Same guard as jobs.apps.JobsConfig / plans.apps.PlansConfig —
        # only the actual runserver-serving child process (not the
        # autoreload watcher, not migrate/shell/etc.) should own the
        # scheduler thread.
        if "runserver" not in sys.argv or os.environ.get("RUN_MAIN") != "true":
            return
        from . import scheduler
        scheduler.start()
