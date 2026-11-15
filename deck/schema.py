"""Tiny JSON Schema validator for the subset deck schemas use (design §1), plus schema loading.

Supported: type, enum, const, required, properties, additionalProperties (bool or schema), pattern,
minLength/maxLength, minimum/maximum/exclusiveMinimum/exclusiveMaximum, items, minItems/maxItems,
uniqueItems, $ref (local #/$defs/...), oneOf/anyOf.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

SCHEMA_DIR = Path(__file__).parent / "schemas"


@lru_cache(maxsize=None)
def load(name: str) -> dict:
    """load('task') -> task.schema.json"""
    return json.loads((SCHEMA_DIR / f"{name}.schema.json").read_text())


def _type_ok(v, t):
    if t == "null":
        return v is None
    if t == "boolean":
        return isinstance(v, bool)
    if t == "integer":
        return isinstance(v, int) and not isinstance(v, bool)
    if t == "number":
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    if t == "string":
        return isinstance(v, str)
    if t == "array":
        return isinstance(v, list)
    if t == "object":
        return isinstance(v, dict)
    return True


def errors(instance, schema, root=None, path="$") -> list[str]:
    root = root if root is not None else schema
    out: list[str] = []
    if "$ref" in schema:
        ref = schema["$ref"]
        node = root
        for part in ref.lstrip("#/").split("/"):
            node = node[part]
        return errors(instance, node, root, path)
    if "oneOf" in schema or "anyOf" in schema:
        subs = schema.get("oneOf") or schema.get("anyOf")
        oks = [s for s in subs if not errors(instance, s, root, path)]
        if not oks or ("oneOf" in schema and len(oks) > 1):
            out.append(f"{path}: does not match {'oneOf' if 'oneOf' in schema else 'anyOf'}")
        return out
    t = schema.get("type")
    if t is not None:
        ts = t if isinstance(t, list) else [t]
        if not any(_type_ok(instance, x) for x in ts):
            return [f"{path}: expected {'/'.join(ts)}"]
    if "const" in schema and instance != schema["const"]:
        out.append(f"{path}: must be {schema['const']!r}")
    if "enum" in schema and instance not in schema["enum"]:
        out.append(f"{path}: must be one of {', '.join(map(str, schema['enum']))}")
    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < schema["minLength"]:
            out.append(f"{path}: shorter than {schema['minLength']}")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            out.append(f"{path}: longer than {schema['maxLength']}")
        if "pattern" in schema and not re.search(schema["pattern"], instance):
            out.append(f"{path}: does not match {schema['pattern']}")
    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            out.append(f"{path}: below {schema['minimum']}")
        if "maximum" in schema and instance > schema["maximum"]:
            out.append(f"{path}: above {schema['maximum']}")
        if "exclusiveMinimum" in schema and instance <= schema["exclusiveMinimum"]:
            out.append(f"{path}: must be > {schema['exclusiveMinimum']}")
        if "exclusiveMaximum" in schema and instance >= schema["exclusiveMaximum"]:
            out.append(f"{path}: must be < {schema['exclusiveMaximum']}")
    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            out.append(f"{path}: fewer than {schema['minItems']} items")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            out.append(f"{path}: more than {schema['maxItems']} items")
        if schema.get("uniqueItems"):
            seen = [json.dumps(x, sort_keys=True) for x in instance]
            if len(seen) != len(set(seen)):
                out.append(f"{path}: items not unique")
        if isinstance(schema.get("items"), dict):
            for i, x in enumerate(instance):
                out += errors(x, schema["items"], root, f"{path}[{i}]")
    if isinstance(instance, dict):
        for k in schema.get("required", []):
            if k not in instance:
                out.append(f"{path}: missing required '{k}'")
        props = schema.get("properties", {})
        addl = schema.get("additionalProperties", True)
        for k, v in instance.items():
            if k in props:
                out += errors(v, props[k], root, f"{path}.{k}")
            elif addl is False:
                out.append(f"{path}: unknown key '{k}'")
            elif isinstance(addl, dict):
                out += errors(v, addl, root, f"{path}.{k}")
    return out


def validate(instance, name: str) -> list[str]:
    return errors(instance, load(name))
