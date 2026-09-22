from django.core.management.base import BaseCommand

from home import status


class Command(BaseCommand):
    help = ("Runs the status page's checks (database, workers, Ante's own pages and API) and stores the results, so the "
            "page can show a 24h uptime and a history for each. Schedule it every 1–5 minutes (cron, a systemd timer…).")

    def handle(self, *args, **options):
        results = status.run_checks()
        status.record(results)
        state = status.overall_state(results)
        marks = {"OPERATIONAL": "OK", "DEGRADED": "!!", "DOWN": "XX"}
        for r in results:
            if r["persist"]:
                self.stdout.write(f"[{marks[r['state']]}] {r['group']:<10} {r['name']:<18} {r['http_status'] or '-':<5} {r['latency_ms'] or '-'}ms")
        style = {"OPERATIONAL": self.style.SUCCESS, "DEGRADED": self.style.WARNING}.get(state, self.style.ERROR)
        self.stdout.write(style(f"\nOverall status: {state}"))
