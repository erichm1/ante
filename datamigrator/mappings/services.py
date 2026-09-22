"""Auto-mapping and draft review for a mapping's entity pairs (the API in views.py and the Studio call these)."""
from django.db import transaction

from . import automap
from .models import FieldMapping


def _flag(value, default):
    if value is None:
        return default
    return value if isinstance(value, bool) else str(value).lower() in ("1", "true", "yes", "on")


def clamp_score(value):
    try:
        return max(30, min(int(value), 100))
    except (TypeError, ValueError):
        return automap.DEFAULT_MIN_SCORE


def _field_info(f):
    return {"id": f.pk, "name": f.name, "type": f.field_type}


def auto_map_pair(pair, min_score=None, only_unmapped=None, replace_drafts=None, dry_run=False):
    """Suggest field wires for one entity pair and save them as DRAFTS (unless `dry_run`).

      min_score       lowest confidence to suggest (30-100, default 60)
      only_unmapped   leave target fields that already have a confirmed wire alone (default True)
      replace_drafts  throw away the pair's earlier drafts first, so a re-run doesn't pile up duplicates (default True)
      dry_run         only report what would be suggested
    """
    min_score = clamp_score(min_score)
    only_unmapped, replace_drafts = _flag(only_unmapped, True), _flag(replace_drafts, True)
    existing = list(pair.field_mappings.all())
    confirmed = [fm for fm in existing if fm.status != FieldMapping.STATUS_DRAFT]
    kept = confirmed if replace_drafts else existing            # wires the new suggestions must not duplicate

    suggestions, unmatched_targets, unmatched_sources = automap.suggest(
        pair.source_entity.fields.all(), pair.target_entity.fields.all(), min_score=min_score,
        skip_targets={fm.target_field_id for fm in kept} if only_unmapped else (),
        skip_pairs={(fm.source_field_id, fm.target_field_id) for fm in kept})

    created = []
    if not dry_run:
        with transaction.atomic():
            if replace_drafts:
                pair.field_mappings.filter(status=FieldMapping.STATUS_DRAFT).delete()
            for s in suggestions:
                created.append(FieldMapping.objects.create(
                    entity_mapping=pair, source_field=s["source"], target_field=s["target"], status=FieldMapping.STATUS_DRAFT,
                    match_score=s["score"], match_reason=s["reason"][:200]))
    ids = {(fm.source_field_id, fm.target_field_id): fm.pk for fm in created}
    return {
        "entity_mapping": pair.pk, "dry_run": dry_run, "min_score": min_score, "created": len(created),
        "suggestions": [{"id": ids.get((s["source"].pk, s["target"].pk)), "source": _field_info(s["source"]), "target": _field_info(s["target"]),
                         "score": s["score"], "reason": s["reason"]} for s in suggestions],
        "unmatched_targets": [_field_info(f) for f in unmatched_targets],
        "unmatched_sources": [_field_info(f) for f in unmatched_sources],
    }


def confirm_drafts(pairs, ids=None):
    """Accept drafts (all of them, or just those with these FieldMapping ids) — they become ordinary wires that run."""
    qs = FieldMapping.objects.filter(entity_mapping__in=pairs, status=FieldMapping.STATUS_DRAFT)
    if ids is not None:
        qs = qs.filter(pk__in=ids)
    return qs.update(status=FieldMapping.STATUS_CONFIRMED)


def discard_drafts(pairs, ids=None):
    """Delete drafts (all, or just those ids). Confirmed wires are never touched."""
    qs = FieldMapping.objects.filter(entity_mapping__in=pairs, status=FieldMapping.STATUS_DRAFT)
    if ids is not None:
        qs = qs.filter(pk__in=ids)
    count = qs.count()
    qs.delete()
    return count
