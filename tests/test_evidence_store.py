"""Evidence must survive the thing it describes, and must not be swappable."""

import pytest

from ledger.evidence_store import INLINE_MAX_BYTES, EvidenceIntegrityError, EvidenceStore


@pytest.fixture
def store(tmp_path):
    return EvidenceStore(tmp_path / "evidence")


def test_same_content_gets_the_same_address(store):
    a = store.put(b"identical bytes")
    b = store.put(b"identical bytes")
    assert a == b


def test_different_content_gets_a_different_address(store):
    assert store.put(b"one") != store.put(b"two")


def test_round_trip(store):
    digest = store.put(b"log line\n")
    assert store.get(digest) == b"log line\n"
    assert store.exists(digest)


def test_get_accepts_the_prefixed_form(store):
    digest = store.put(b"x")
    assert store.get(f"sha256:{digest}") == b"x"


def test_tampering_with_a_blob_is_detected_on_read(store, tmp_path):
    digest = store.put(b"the original evidence")
    path = tmp_path / "evidence" / digest[:2] / digest
    path.chmod(0o644)
    path.write_bytes(b"something else entirely")
    # The address is the hash, so altered bytes can no longer satisfy it.
    with pytest.raises(EvidenceIntegrityError):
        store.get(digest)


def test_missing_blob_raises_not_found(store):
    with pytest.raises(FileNotFoundError):
        store.get("0" * 64)


def test_small_evidence_is_stored_inline(store):
    item = store.store_evidence(
        evidence_type="log_query", source="src", content="short",
        retrieved_at="2026-09-18T00:00:00Z")
    assert item["snapshot_ref_or_inline"]["kind"] == "inline"
    assert item["snapshot_ref_or_inline"]["content"] == "short"


def test_large_evidence_becomes_a_content_addressed_blob(store):
    big = "x" * (INLINE_MAX_BYTES + 1)
    item = store.store_evidence(
        evidence_type="log_query", source="src", content=big,
        retrieved_at="2026-09-18T00:00:00Z")
    ref = item["snapshot_ref_or_inline"]
    assert ref["kind"] == "blob"
    assert ref["sha256"].startswith("sha256:")
    assert ref["size_bytes"] == len(big.encode())
    assert store.get(ref["sha256"]) == big.encode()
    # A preview so the dashboard need not fetch the blob to show the gist.
    assert ref["preview"]


def test_redaction_flag_is_carried_through(store):
    item = store.store_evidence(
        evidence_type="git_history", source="src", content="AKIA****",
        retrieved_at="2026-09-18T00:00:00Z", redacted=True)
    assert item["redacted"] is True


def test_stored_blobs_are_read_only_on_disk(store, tmp_path):
    digest = store.put(b"immutable")
    path = tmp_path / "evidence" / digest[:2] / digest
    assert not (path.stat().st_mode & 0o222), "blob should not be writable"
