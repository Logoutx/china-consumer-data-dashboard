"""Minimal draft-07 JSON Schema *subset* validator, stdlib only.

Written for this migration because the task requires schema validation but
forbids third-party packages for the migration itself, and no validator
existed in-repo at the time this was written. Not a general-purpose
implementation -- only the keywords actually used by
data/schemas/{series,catalog,panel}.schema.json are supported:

    type, required, properties, additionalProperties, items, enum, const,
    pattern, minItems, maxItems, uniqueItems, minLength, minimum, maximum,
    oneOf, $ref (local "#/..." pointers only)

Deliberately NOT supported (unused by our schemas): format validation,
allOf/anyOf/not, patternProperties, if/then/else, remote $ref.
"""
from __future__ import annotations

import re


def _resolve_ref(ref, root):
    assert ref.startswith("#/"), f"only local refs supported, got {ref!r}"
    node = root
    for part in ref[2:].split("/"):
        node = node[part]
    return node


def _check_type(instance, types):
    for t in types:
        if t == "null" and instance is None:
            return True
        if t == "string" and isinstance(instance, str):
            return True
        if t == "integer" and isinstance(instance, int) and not isinstance(instance, bool):
            return True
        if t == "number" and isinstance(instance, (int, float)) and not isinstance(instance, bool):
            return True
        if t == "boolean" and isinstance(instance, bool):
            return True
        if t == "object" and isinstance(instance, dict):
            return True
        if t == "array" and isinstance(instance, list):
            return True
    return False


def validate(instance, schema, root=None, path="$"):
    """Return a list of human-readable error strings (empty => valid)."""
    if root is None:
        root = schema
    errors = []

    if "$ref" in schema:
        target = _resolve_ref(schema["$ref"], root)
        return validate(instance, target, root, path)

    if "oneOf" in schema:
        match_count = 0
        for sub in schema["oneOf"]:
            if not validate(instance, sub, root, path):
                match_count += 1
        if match_count != 1:
            errors.append(f"{path}: expected exactly 1 oneOf branch to match, {match_count} matched")
        return errors

    if "const" in schema:
        if instance != schema["const"]:
            errors.append(f"{path}: expected const {schema['const']!r}, got {instance!r}")

    if "enum" in schema:
        if instance not in schema["enum"]:
            errors.append(f"{path}: {instance!r} not in enum {schema['enum']}")

    if "type" in schema:
        t = schema["type"]
        types = t if isinstance(t, list) else [t]
        if not _check_type(instance, types):
            errors.append(f"{path}: expected type {types}, got {type(instance).__name__} ({instance!r})")
            return errors  # further structural checks are meaningless if the type itself is wrong

    if isinstance(instance, str):
        if "pattern" in schema and not re.match(schema["pattern"], instance):
            errors.append(f"{path}: {instance!r} does not match pattern {schema['pattern']!r}")
        if "minLength" in schema and len(instance) < schema["minLength"]:
            errors.append(f"{path}: string shorter than minLength {schema['minLength']}")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append(f"{path}: {instance} < minimum {schema['minimum']}")
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append(f"{path}: {instance} > maximum {schema['maximum']}")

    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            errors.append(f"{path}: array shorter than minItems {schema['minItems']}")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            errors.append(f"{path}: array longer than maxItems {schema['maxItems']}")
        if schema.get("uniqueItems"):
            seen = []
            for item in instance:
                if item in seen:
                    errors.append(f"{path}: duplicate item {item!r} where uniqueItems is required")
                    break
                seen.append(item)
        if "items" in schema:
            for i, item in enumerate(instance):
                errors.extend(validate(item, schema["items"], root, f"{path}[{i}]"))

    if isinstance(instance, dict):
        props = schema.get("properties", {})
        for req in schema.get("required", []):
            if req not in instance:
                errors.append(f"{path}: missing required property {req!r}")
        if schema.get("additionalProperties") is False:
            allowed = set(props.keys())
            for k in instance:
                if k not in allowed:
                    errors.append(f"{path}: additional property {k!r} not allowed")
        ap_schema = schema.get("additionalProperties")
        for k, v in instance.items():
            if k in props:
                errors.extend(validate(v, props[k], root, f"{path}.{k}"))
            elif isinstance(ap_schema, dict):
                errors.extend(validate(v, ap_schema, root, f"{path}.{k}"))

    return errors
