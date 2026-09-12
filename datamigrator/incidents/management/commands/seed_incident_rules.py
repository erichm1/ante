from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from incidents.models import IncidentRule

User = get_user_model()

RULES = [
    {
        "name": "High migration fail rate",
        "trigger_type": IncidentRule.TRIGGER_RUN_FAIL,
        "threshold": 30.0,
        "window_hours": 1,
        "severity": "high",
        "auto_title": "High migration failure rate ({threshold}% in last hour)",
    },
    {
        "name": "Critical migration fail rate",
        "trigger_type": IncidentRule.TRIGGER_RUN_FAIL,
        "threshold": 60.0,
        "window_hours": 1,
        "severity": "critical",
        "auto_title": "Critical: majority of migrations failing ({threshold}%+)",
    },
    {
        "name": "Connection error spike",
        "trigger_type": IncidentRule.TRIGGER_CONN_ERR,
        "threshold": 20.0,
        "window_hours": 1,
        "severity": "high",
        "auto_title": "Connection error spike on {connection} ({threshold}% error rate)",
    },
    {
        "name": "API error rate elevated",
        "trigger_type": IncidentRule.TRIGGER_API_ERROR,
        "threshold": 10.0,
        "window_hours": 2,
        "severity": "medium",
        "auto_title": "Elevated API error rate on {connection} ({threshold}%+ in 2h)",
    },
    {
        "name": "API errors critical",
        "trigger_type": IncidentRule.TRIGGER_API_ERROR,
        "threshold": 40.0,
        "window_hours": 1,
        "severity": "critical",
        "auto_title": "Critical API error rate on {connection} ({threshold}%+)",
    },
]


class Command(BaseCommand):
    help = "Seeds sample IncidentRules for the first staff user. Skips rules that already exist by name for that user."

    def handle(self, *args, **options):
        user = User.objects.filter(is_staff=True).order_by("pk").first()
        if not user:
            self.stderr.write("No staff user found — run this after creating a superuser.")
            return

        created = 0
        skipped = 0
        for rule_data in RULES:
            _, was_created = IncidentRule.objects.get_or_create(
                user=user,
                name=rule_data["name"],
                defaults={**rule_data, "enabled": True},
            )
            if was_created:
                created += 1
                self.stdout.write(f"  Created: {rule_data['name']}")
            else:
                skipped += 1

        self.stdout.write(self.style.SUCCESS(
            f"Done — {created} rule(s) created, {skipped} already existed."
        ))
