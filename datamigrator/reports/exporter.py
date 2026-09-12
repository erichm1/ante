"""Turns a Report's sections into either an on-screen preview (JSON, rows
capped) or the real combined CSV (uncapped) — both walk the exact same
section/field_ids definition, so the preview is a truthful sample of what
the export actually contains, just capped for the page.
"""
import csv
import io

from schemas.discovery import read_all_records
from schemas.models import Field

PREVIEW_ROW_LIMIT = 50


def _ordered_fields(section):
    """section.field_ids in the order they were saved, dropping any id that
    no longer resolves to a real Field (e.g. deleted after being added)."""
    fields_by_id = {f.pk: f for f in Field.objects.filter(pk__in=section.field_ids)}
    return [fields_by_id[fid] for fid in section.field_ids if fid in fields_by_id]


def build_report_preview(report, limit=PREVIEW_ROW_LIMIT):
    sections = []
    for section in report.sections.select_related("entity").order_by("order"):
        fields = _ordered_fields(section)
        records = read_all_records(section.entity)
        sections.append({
            "section_id": section.pk,
            "entity_id": section.entity_id,
            "entity_name": section.entity.name,
            "columns": [f.name for f in fields],
            "rows": [[record.get(f.name, "") for f in fields] for record in records[:limit]],
            "total_records": len(records),
        })
    return sections


def build_report_csv(report) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    for i, section in enumerate(report.sections.select_related("entity").order_by("order")):
        if i:
            writer.writerow([])
        fields = _ordered_fields(section)
        records = read_all_records(section.entity)
        writer.writerow([f"# {section.entity.name}"])
        writer.writerow([f.name for f in fields])
        for record in records:
            writer.writerow([record.get(f.name, "") for f in fields])
    return buf.getvalue()
