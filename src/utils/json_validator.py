"""JSON Validator — validates JSON data against a simple schema."""

from __future__ import annotations


def validate(data, schema):
    """Validate JSON data against a schema.

    Args:
        data: The data to validate (dict or None).
        schema: The schema to validate against (dict or None).

    Returns:
        dict with keys: valid (bool), errors (list[str])
    """
    errors: list[str] = []

    # Edge case: None inputs or non-dict data — raise TypeError
    if data is None:
        raise TypeError("Data cannot be None")
    if schema is None:
        raise TypeError("Schema cannot be None")
    if not isinstance(data, dict):
        raise TypeError("Data must be a dict")
    if not isinstance(schema, dict):
        raise TypeError("Schema must be a dict")

    properties = schema.get("properties", {})
    required = schema.get("required", [])

    # If no properties, nothing to validate against
    if not properties:
        return {"valid": True, "errors": []}

    # Check required fields
    for field in required:
        if field not in data:
            errors.append(f"Missing required field: {field}")

    # Validate each field in data that has a schema definition
    for field, value in data.items():
        if field not in properties:
            # Extra fields are ignored
            continue

        field_schema = properties[field]
        field_type = field_schema.get("type", "")
        errors.extend(_validate_field(field, value, field_schema, field_type))

    return {"valid": len(errors) == 0, "errors": errors}


def _validate_field(field: str, value, field_schema: dict, field_type: str) -> list[str]:
    """Validate a single field value against its schema definition."""
    errors: list[str] = []

    # Type validation
    if field_type == "string":
        if not isinstance(value, str):
            errors.append(f"Type mismatch for {field}: expected string")
    elif field_type == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            errors.append(f"Type mismatch for {field}: expected int")
        else:
            # Range validation
            if "min" in field_schema and value < field_schema["min"]:
                errors.append(f"Value for {field} is below minimum")
            if "max" in field_schema and value > field_schema["max"]:
                errors.append(f"Value for {field} exceeds maximum")
    elif field_type:
        # Unknown type — treat as pass-through (no validation)
        pass

    return errors
