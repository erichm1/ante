"""Dotted field paths — how a field deep inside an object is named ("address.geo.lat", "items.0.sku").

A discovered entity lists every level of a nested object as its own Field: the object itself
(`address`, type object) *and* each field inside it (`address.city`). Reading a source value and building a
destination payload both follow these paths, so a mapping can wire a leaf of one object to a leaf of another.
"""
import copy

MAX_DEPTH = 5          # how many object levels discovery unfolds (address.geo.lat.x… stops here)
MAX_NAME = 120         # Field.name is a CharField(120)


def get_path(record, name):
    """The value at `name` in `record`. A key that literally contains dots (a flat CSV column called "a.b")
    wins over walking into "a" → "b"; a numeric segment indexes a list. Missing anywhere → None."""
    if isinstance(record, dict) and name in record:
        return record[name]
    current = record
    for part in name.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            return None
    return current


def _grow(container, index):
    while len(container) <= index:
        container.append(None)


def _child(container, key, default):
    """The child of `container` at `key`, created as `default` when missing."""
    if isinstance(container, list):
        index = int(key)
        _grow(container, index)
        if container[index] is None:
            container[index] = default
        return container[index]
    return container.setdefault(key, default)


def set_path(out, name, value):
    """Store `value` at the dotted `name` inside `out` ("a.b" → {"a": {"b": value}}; a numeric segment makes a
    list). A plain name is a single top-level key. Objects/lists are copied so later writes into a nested path
    never reach back into the source record."""
    parts = name.split(".")
    container = out
    for i, part in enumerate(parts[:-1]):
        container = _child(container, part, [] if parts[i + 1].isdigit() else {})
    last = parts[-1]
    value = copy.deepcopy(value) if isinstance(value, (dict, list)) else value
    if isinstance(container, list):
        index = int(last)
        _grow(container, index)
        container[index] = value
    else:
        container[last] = value


def leaf_fields(fields):
    """The fields that are not the parent of another field in the list — the ones worth wiring automatically
    (an object and its own members would otherwise be mapped twice)."""
    names = [f.name for f in fields]
    return [f for f in fields if not any(other.startswith(f.name + ".") for other in names)]
