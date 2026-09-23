"""Canonical JSON serialization and the record hash function.

The whole tamper-evidence story rests on two parties independently deriving the
same bytes from the same record. That means serialization has to be pinned down
to something narrower than "json.dumps with sorted keys", which is why this
module exists as its own unit with its own tests.

We follow RFC 8785 (JSON Canonicalization Scheme) with one deliberate
restriction: floating point numbers are rejected outright rather than
serialized. RFC 8785's hardest requirement is reproducing ECMAScript's
number-to-string algorithm, and getting it subtly wrong would produce hashes
that verify on one implementation and fail on another -- the worst possible
failure mode for an audit ledger. No field in agent_action_record.schema.json
is a float (durations and sizes are integers), so the restriction costs us
nothing and removes an entire class of interoperability bug. A float reaching
this function is a schema violation and is raised as one.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

# The prev_record_hash of the first record in a chain. Sixty-four zeros is not a
# reachable sha256 output, so a genesis record can never be confused with a
# record whose predecessor was deleted.
GENESIS_HASH = "0" * 64

# Excluded from the hash preimage: a record cannot commit to its own hash.
SELF_HASH_FIELD = "record_hash"


class CanonicalizationError(ValueError):
    """Raised when a value cannot be canonicalized deterministically."""


def _check(value: Any, path: str = "$") -> None:
    """Reject anything whose canonical form would be ambiguous."""
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return
    if isinstance(value, int):
        return
    if isinstance(value, float):
        raise CanonicalizationError(
            f"float at {path}: floats are not permitted in ledger records because "
            f"their canonical form is implementation-sensitive; use an integer "
            f"(e.g. duration_ms, size_bytes) or a string"
        )
    if isinstance(value, dict):
        for key, sub in value.items():
            if not isinstance(key, str):
                raise CanonicalizationError(f"non-string object key at {path}: {key!r}")
            _check(sub, f"{path}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, sub in enumerate(value):
            _check(sub, f"{path}[{index}]")
        return
    raise CanonicalizationError(f"unsupported type {type(value).__name__} at {path}")


def canonicalize(value: Any) -> bytes:
    """Serialize to the canonical byte sequence used for hashing.

    Object keys are sorted by Unicode code point, no insignificant whitespace is
    emitted, and non-ASCII characters are kept as UTF-8 rather than escaped.
    """
    _check(value)
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def hash_payload(value: Any) -> str:
    """sha256 of the canonical form, lowercase hex."""
    return hashlib.sha256(canonicalize(value)).hexdigest()


def record_preimage(record: dict[str, Any]) -> dict[str, Any]:
    """The exact object that gets hashed: the record minus its own hash field.

    Note that prev_record_hash stays in. That inclusion is what chains the
    records together: editing record N changes N's hash, which was an input to
    N+1's hash, and so on to the end of the ledger.
    """
    return {k: v for k, v in record.items() if k != SELF_HASH_FIELD}


def compute_record_hash(record: dict[str, Any]) -> str:
    """Compute the record_hash a record should carry."""
    return hash_payload(record_preimage(record))


def seal(record: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of the record with a correct record_hash attached."""
    sealed = dict(record)
    sealed.pop(SELF_HASH_FIELD, None)
    sealed[SELF_HASH_FIELD] = compute_record_hash(sealed)
    return sealed


def verify_record_hash(record: dict[str, Any]) -> bool:
    """True when the record's stored hash matches its contents."""
    stored = record.get(SELF_HASH_FIELD)
    if not isinstance(stored, str):
        return False
    try:
        expected = compute_record_hash(record)
    except CanonicalizationError:
        return False
    # Not a secret, but constant-time comparison costs nothing and keeps the
    # habit correct if this is ever extended to HMAC-signed records.
    return hmac.compare_digest(stored, expected)
