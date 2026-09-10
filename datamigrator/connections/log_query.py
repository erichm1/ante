"""A single-line, Grafana/Loki-flavored query over ApiCallLog: space-
separated terms (quote a value that has spaces), AND-combined.

Recognized fields:
  status:200        status:!=200      status:>=400      status:<500
  method:POST        (case-insensitive exact match)
  url:produtos        (substring)
  connection:olist     (substring match on the connection's name)
  run:91               (exact run id)
  error:true / error:false   (has / doesn't have an error)
  duration:>500         (duration_ms, milliseconds)
  json.<dotted.path>:value    e.g. json.id:42, json.data.email:"a@b.com"
      — extracted from the stored response body. This one runs in Python
      (response_body is stored as raw text, not a queryable JSON column),
      so it's applied after every other, DB-level filter narrows things
      down, over a capped window of the most recent matches.

A bare word with no recognized field prefix free-text searches url,
request_body, response_body, and error (OR'd together).
"""
import json
import re
import shlex

from django.db.models import Q

FIELD_LOOKUPS = {
    ":": "exact", "!=": "exact", ">": "gt", ">=": "gte", "<": "lt", "<=": "lte",
}
TERM_RE = re.compile(r'^([A-Za-z_][\w.]*)\s*(:|>=|<=|!=|>|<)\s*(.+)$')
JSON_TERM_SCAN_LIMIT = 2000  # rows pulled before a json.* filter is applied in Python


def _numeric_q(field: str, op: str, raw_value: str, cast):
    try:
        num = cast(raw_value)
    except ValueError:
        return None, f"{field} must be a number, got '{raw_value}'"
    lookup = FIELD_LOOKUPS[op]
    q = Q(**{f"{field}__{lookup}": num})
    return (~q if op == "!=" else q), None


def _coerce(value_str: str):
    """Best-effort typing so e.g. json.id:42 compares as a number, not text."""
    if value_str.lower() in ("true", "false"):
        return value_str.lower() == "true"
    try:
        return int(value_str)
    except ValueError:
        pass
    try:
        return float(value_str)
    except ValueError:
        return value_str


def _extract_json_path(response_body: str, dotted_path: str):
    """Returns (value, found) — found=False for unparsable JSON or a path
    that doesn't exist, so callers can tell "wasn't there" from "was null"."""
    try:
        data = json.loads(response_body)
    except (TypeError, ValueError):
        return None, False
    value = data
    for part in dotted_path.split("."):
        if isinstance(value, dict):
            if part not in value:
                return None, False
            value = value[part]
        elif isinstance(value, list) and part.lstrip("-").isdigit():
            idx = int(part)
            if not (-len(value) <= idx < len(value)):
                return None, False
            value = value[idx]
        else:
            return None, False
    return value, True


def _json_matches(value, op: str, target) -> bool:
    try:
        if op in (">", ">=", "<", "<="):
            value, target = float(value), float(target)
        if op == ":":
            if isinstance(value, str) and isinstance(target, str):
                return target.lower() in value.lower()
            return value == target
        if op == "!=":
            return value != target
        if op == ">":
            return value > target
        if op == ">=":
            return value >= target
        if op == "<":
            return value < target
        if op == "<=":
            return value <= target
    except (TypeError, ValueError):
        return False
    return False


def filter_logs(queryset, query_string: str):
    """Returns (results, error_message). `results` is a lazy QuerySet when
    the query has no json.* term (so pagination stays a real SQL LIMIT/
    OFFSET), or a plain list when it does (already capped + filtered)."""
    qs = queryset.order_by("-created_at")
    if not query_string or not query_string.strip():
        return qs, None

    try:
        tokens = shlex.split(query_string)
    except ValueError as exc:
        return qs.none(), f"Could not parse query: {exc}"

    db_filters = Q()
    json_terms = []       # [(dotted_path, op, raw_value), ...]
    free_text_terms = []

    for token in tokens:
        match = TERM_RE.match(token)
        if not match:
            free_text_terms.append(token)
            continue
        key, op, raw_value = match.groups()
        # "status:>=400" (colon *and* a comparison operator together, per the
        # documented syntax) parses here as op=":" raw_value=">=400" — the
        # colon is just a separator in that form, so the real operator is
        # whatever leads raw_value; only a bare "status:400" keeps op=":".
        if op == ":":
            for candidate in (">=", "<=", "!=", ">", "<"):
                if raw_value.startswith(candidate):
                    op, raw_value = candidate, raw_value[len(candidate):]
                    break
        raw_value = raw_value.strip('"\'')

        if key.startswith("json."):
            json_terms.append((key[len("json."):], op, raw_value))
        elif key == "status":
            q, err = _numeric_q("status_code", op, raw_value, int)
            if err:
                return qs.none(), err
            db_filters &= q
        elif key == "duration":
            q, err = _numeric_q("duration_ms", op, raw_value, float)
            if err:
                return qs.none(), err
            db_filters &= q
        elif key == "method":
            db_filters &= Q(method__iexact=raw_value)
        elif key == "url":
            db_filters &= Q(url__icontains=raw_value)
        elif key == "connection":
            db_filters &= Q(connection__name__icontains=raw_value)
        elif key == "run":
            try:
                db_filters &= Q(run_id=int(raw_value))
            except ValueError:
                return qs.none(), f"run must be a number, got '{raw_value}'"
        elif key == "error":
            wants_error = raw_value.lower() in ("true", "1", "yes")
            db_filters &= (~Q(error="") if wants_error else Q(error=""))
        else:
            # Unrecognized field — don't hard-fail the whole query over a typo,
            # just fall back to treating it as free text.
            free_text_terms.append(token)

    for term in free_text_terms:
        db_filters &= (
            Q(url__icontains=term) | Q(request_body__icontains=term)
            | Q(response_body__icontains=term) | Q(error__icontains=term)
        )

    qs = qs.filter(db_filters)
    if not json_terms:
        return qs, None

    matched = []
    for row in qs[:JSON_TERM_SCAN_LIMIT]:
        ok = True
        for path, op, raw_value in json_terms:
            value, found = _extract_json_path(row.response_body, path)
            if not found or not _json_matches(value, op, _coerce(raw_value)):
                ok = False
                break
        if ok:
            matched.append(row)
    return matched, None
