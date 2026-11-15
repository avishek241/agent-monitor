"""Turn the handoff schema into the shape each agent CLI accepts (B2 spikes, docs/v2/B2-spikes.md).

- Claude `--json-schema` rejects the `$schema: draft/2020-12` key ("no schema with key or ref").
- Codex `--output-schema` uses OpenAI strict structured output: every property must be listed in
  `required` and every object needs `additionalProperties: false`. Optional fields become nullable.
Both drop the daemon-only (readOnly) properties, which the worker must not fill in.
"""
import copy
import json

_STRIP_KEYS = ("$schema", "$id", "readOnly", "default", "uniqueItems")


def _clean(node):
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            if k in _STRIP_KEYS:
                continue
            if k == "properties" and isinstance(v, dict):
                out[k] = {pk: _clean(pv) for pk, pv in v.items() if not (isinstance(pv, dict) and pv.get("readOnly"))}
            else:
                out[k] = _clean(v)
        if "required" in out and "properties" in out:
            out["required"] = [r for r in out["required"] if r in out["properties"]]
        return out
    if isinstance(node, list):
        return [_clean(x) for x in node]
    return node


def _nullable(prop):
    p = copy.deepcopy(prop)
    if "const" in p:
        p["enum"] = [p.pop("const")]
    if "enum" in p:
        if None not in p["enum"]:
            p["enum"] = list(p["enum"]) + [None]
        if "type" in p:
            t = p["type"] if isinstance(p["type"], list) else [p["type"]]
            p["type"] = t + ["null"] if "null" not in t else t
        return p
    t = p.get("type")
    if t is None:
        return {"anyOf": [p, {"type": "null"}]}
    t = t if isinstance(t, list) else [t]
    if "null" not in t:
        p["type"] = t + ["null"]
    return p


def _strict(node):
    if isinstance(node, dict):
        node = {k: _strict(v) for k, v in node.items()}
        if "const" in node:
            node["enum"] = [node.pop("const")]
        if node.get("type") == "object" or "properties" in node:
            props = node.get("properties", {})
            req = set(node.get("required", []))
            node["properties"] = {k: (v if k in req else _nullable(v)) for k, v in props.items()}
            node["required"] = list(props.keys())
            node["additionalProperties"] = False
            node.setdefault("type", "object")
        return node
    if isinstance(node, list):
        return [_strict(x) for x in node]
    return node


def for_claude(schema: dict) -> dict:
    """Worker-facing handoff schema for `claude -p --json-schema`."""
    return _clean(schema)


def for_codex(schema: dict) -> dict:
    """Worker-facing handoff schema for `codex exec --output-schema` (OpenAI strict mode)."""
    out = _strict(_clean(schema))
    out.pop("title", None)
    out.pop("description", None)
    return out


def strip_nulls(handoff):
    """Codex returns null for optional fields it did not fill; drop them so the handoff validates."""
    if isinstance(handoff, dict):
        return {k: strip_nulls(v) for k, v in handoff.items() if v is not None}
    if isinstance(handoff, list):
        return [strip_nulls(x) for x in handoff]
    return handoff


def dump(schema: dict) -> str:
    return json.dumps(schema, separators=(",", ":"))


_TYPES = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}


def _is(v, t):
    if t == "integer":
        return isinstance(v, int) and not isinstance(v, bool)
    if t == "number":
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    return isinstance(v, _TYPES[t])


def validate(inst, schema, path="$") -> list:
    """Subset validator (type, enum, const, required, properties, additionalProperties, items, min/maxLength,
    minimum/maximum). Returns a list of error strings; empty = valid. readOnly fields are accepted."""
    errs = []
    t = schema.get("type")
    if t is not None:
        ts = t if isinstance(t, list) else [t]
        if not any(_is(inst, x) for x in ts):
            return [f"{path}: expected {'/'.join(ts)}"]
    if "const" in schema and inst != schema["const"]:
        errs.append(f"{path}: must be {schema['const']!r}")
    if "enum" in schema and inst not in schema["enum"]:
        errs.append(f"{path}: {inst!r} not in {schema['enum']}")
    if isinstance(inst, str):
        if "minLength" in schema and len(inst) < schema["minLength"]:
            errs.append(f"{path}: shorter than {schema['minLength']}")
        if "maxLength" in schema and len(inst) > schema["maxLength"]:
            errs.append(f"{path}: longer than {schema['maxLength']}")
    if isinstance(inst, (int, float)) and not isinstance(inst, bool):
        if "minimum" in schema and inst < schema["minimum"]:
            errs.append(f"{path}: < {schema['minimum']}")
        if "maximum" in schema and inst > schema["maximum"]:
            errs.append(f"{path}: > {schema['maximum']}")
    if isinstance(inst, dict):
        props = schema.get("properties", {})
        for r in schema.get("required", []):
            if r not in inst:
                errs.append(f"{path}: missing {r}")
        for k, v in inst.items():
            if k in props:
                errs += validate(v, props[k], f"{path}.{k}")
            elif schema.get("additionalProperties") is False:
                errs.append(f"{path}: unknown key {k}")
    if isinstance(inst, list) and isinstance(schema.get("items"), dict):
        for i, v in enumerate(inst):
            errs += validate(v, schema["items"], f"{path}[{i}]")
    return errs
