"""
Print fixture IDs from the live DB so locustfile.py can be kept in sync.
Run with the venv active:  python load_test/seed_ids.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "datamigrator.settings")

import django
django.setup()

from connections.models import Connection
from schemas.models import Entity
from jobs.models import MigrationRun
from mappings.models import Mapping
from chains.models import CallChain
from reports.models import Report

def ids(qs):
    return list(qs.values_list("pk", flat=True))

print("CONNECTION_IDS =", ids(Connection.objects.all()))
print("ENTITY_IDS     =", ids(Entity.objects.all()[:20]))
print("RUN_IDS        =", ids(MigrationRun.objects.order_by("-pk")[:20]))
print("MAPPING_IDS    =", ids(Mapping.objects.all()))
print("CHAIN_IDS      =", ids(CallChain.objects.all()))
print("REPORT_IDS     =", ids(Report.objects.all()))
