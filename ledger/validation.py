"""Schema validation for ledger records.

Every record is validated before it reaches the database. Validation failure is
a hard error, never a warning: a malformed record in an append-only ledger
cannot be fixed later by definition, so the only chance to reject it is before
the insert.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema" / "agent_action_record.schema.json"
SCHEMA_VERSION = "1.0.0"


class RecordValidationError(ValueError):
    """Raised when a record does not satisfy the schema."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        joined = "\n  - ".join(errors)
        super().__init__(f"record failed schema validation:\n  - {joined}")


@lru_cache(maxsize=1)
def _validator() -> Draft202012Validator:
    schema = json.loads(SCHEMA_PATH.read_text())
    # format_checker turns "format": "date-time"/"uuid" from documentation into
    # an enforced constraint. Without it jsonschema silently ignores them.
    return Draft202012Validator(schema, format_checker=FormatChecker())


def validation_errors(record: dict[str, Any]) -> list[str]:
    """Return human-readable validation errors, empty when the record is valid."""
    out = []
    for err in sorted(_validator().iter_errors(record), key=lambda e: list(e.path)):
        location = "/".join(str(p) for p in err.path) or "(root)"
        out.append(f"{location}: {err.message}")
    return out


def validate_record(record: dict[str, Any]) -> None:
    """Raise RecordValidationError unless the record satisfies the schema."""
    errors = validation_errors(record)
    if errors:
        raise RecordValidationError(errors)


def is_valid(record: dict[str, Any]) -> bool:
    return not validation_errors(record)
