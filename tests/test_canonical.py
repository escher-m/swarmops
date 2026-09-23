"""Tests for the hashing foundation.

If canonicalization is wrong, every other guarantee in the product is
decorative, so these tests are deliberately pedantic about byte-level output.
"""

import json

import pytest

from ledger.canonical import (
    GENESIS_HASH, CanonicalizationError, canonicalize, compute_record_hash,
    hash_payload, record_preimage, seal, verify_record_hash,
)


def test_key_order_does_not_change_the_hash():
    a = {"b": 1, "a": 2, "c": {"z": 1, "y": 2}}
    b = {"c": {"y": 2, "z": 1}, "a": 2, "b": 1}
    assert canonicalize(a) == canonicalize(b)
    assert hash_payload(a) == hash_payload(b)


def test_canonical_form_has_no_insignificant_whitespace():
    assert canonicalize({"a": 1, "b": [1, 2]}) == b'{"a":1,"b":[1,2]}'


def test_non_ascii_is_utf8_not_escaped():
    # Escaping would still be deterministic, but it changes the bytes, so two
    # implementations disagreeing on it would produce different hashes.
    assert canonicalize({"k": "café"}) == '{"k":"café"}'.encode("utf-8")


def test_array_order_is_significant():
    assert hash_payload([1, 2]) != hash_payload([2, 1])


def test_floats_are_rejected_rather_than_serialised():
    with pytest.raises(CanonicalizationError, match="float"):
        canonicalize({"duration": 1.5})


def test_nested_float_is_caught_with_its_path():
    with pytest.raises(CanonicalizationError, match=r"\$\.a\.b\[0\]"):
        canonicalize({"a": {"b": [2.5]}})


def test_preimage_excludes_only_the_self_hash():
    rec = {"a": 1, "prev_record_hash": "x" * 64, "record_hash": "y" * 64}
    pre = record_preimage(rec)
    assert "record_hash" not in pre
    # prev_record_hash must stay in: it is what chains records together.
    assert pre["prev_record_hash"] == "x" * 64


def test_seal_then_verify_round_trips():
    sealed = seal({"a": 1, "prev_record_hash": GENESIS_HASH})
    assert verify_record_hash(sealed)
    assert sealed["record_hash"] == compute_record_hash(sealed)


@pytest.mark.parametrize("mutation", [
    lambda r: r.update({"a": 2}),
    lambda r: r.update({"prev_record_hash": "f" * 64}),
    lambda r: r.update({"added": "field"}),
    lambda r: r.pop("a"),
])
def test_any_mutation_breaks_verification(mutation):
    sealed = seal({"a": 1, "prev_record_hash": GENESIS_HASH})
    mutation(sealed)
    assert not verify_record_hash(sealed)


def test_missing_hash_does_not_verify():
    assert not verify_record_hash({"a": 1})


def test_genesis_hash_is_not_a_reachable_digest():
    # Sixty-four zeros cannot be a sha256 output we produced, so a genesis
    # record can never be confused with one whose predecessor was deleted.
    assert GENESIS_HASH == "0" * 64
    assert hash_payload({}) != GENESIS_HASH
