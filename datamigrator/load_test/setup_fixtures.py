"""
Creates or resets all load-test fixtures so the test runs cleanly without
hitting external APIs or relying on pre-existing user data.

Run once before the load test:
  python load_test/setup_fixtures.py

What this does:
  1. Creates/resets the loadtest user
  2. Creates a "load-test-report" with one section using a CSV file entity
     (no external API call during preview/export)
  3. Prints the live fixture IDs to paste into locustfile.py if needed
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "datamigrator.settings")

import django
django.setup()

from django.contrib.auth.models import User
from connections.models import Connection
from schemas.models import Entity, Field
from jobs.models import MigrationRun
from mappings.models import Mapping
from chains.models import CallChain
from reports.models import Report, ReportSection

# --- load-test user ---
user, created = User.objects.get_or_create(username="loadtest")
user.set_password("loadtest123!")
user.is_active = True
user.save()
print(f"{'Created' if created else 'Reset'} user 'loadtest' / 'loadtest123!'")

# --- safe report: use CSV file entity (no external API calls) ---
csv_entity = Entity.objects.filter(connection__name="CSV File").first()
if csv_entity:
    report, _ = Report.objects.get_or_create(name="load-test-report", defaults={"description": "Safe report for load testing"})
    # remove existing sections so we start clean
    report.sections.all().delete()
    fields = list(Field.objects.filter(entity=csv_entity).values_list("pk", flat=True)[:5])
    if fields:
        ReportSection.objects.create(report=report, entity=csv_entity, order=1, field_ids=fields)
        print(f"Report 'load-test-report' (id={report.pk}) created with {len(fields)} fields from entity '{csv_entity.name}'")
    else:
        print(f"Warning: entity '{csv_entity.name}' has no fields — report section skipped")
else:
    report = Report.objects.first()
    print(f"No CSV file entity found — using first report (id={report.pk if report else 'none'})")

# --- print live IDs ---
print()
print("--- Paste into locustfile.py if IDs changed ---")
print("CONNECTION_IDS =", list(Connection.objects.values_list("pk", flat=True)))
print("ENTITY_IDS     =", list(Entity.objects.values_list("pk", flat=True)[:20]))
print("RUN_IDS        =", list(MigrationRun.objects.order_by("-pk").values_list("pk", flat=True)[:10]))
print("MAPPING_IDS    =", list(Mapping.objects.values_list("pk", flat=True)))
print("CHAIN_IDS      =", list(CallChain.objects.values_list("pk", flat=True)))
print("REPORT_IDS     =", list(Report.objects.values_list("pk", flat=True)))
