"""Emit transport-safe JSON without changing file encoding or process settings."""
import json


def emit_json(value):
    """Write ASCII JSON; JSON readers recover exact Unicode strings and paths."""
    print(json.dumps(value, ensure_ascii=True, indent=2, allow_nan=False))
