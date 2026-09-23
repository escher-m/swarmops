"""Content-addressed store for evidence snapshots.

Evidence has to survive the thing it describes. A log excerpt that justified a
production rollback is worthless at audit time if the log backend rotated it
away, and worse than worthless if someone could quietly edit it afterwards.

So evidence is stored by the sha256 of its own bytes. The hash is the filename,
which gives three properties for free:

  * Immutability. Changing the content changes the address, so an edit cannot
    masquerade as the original -- the record still points at the old address,
    which now has no matching file.
  * Deduplication. The same log excerpt cited by five records is stored once.
  * Independent verification. An auditor re-hashes the file and compares it to
    the address; no trust in our code is required.

Writes are atomic (temp file plus rename) and existing blobs are never
overwritten, so a concurrent writer racing on the same content is harmless.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

# Evidence larger than this goes to a blob; smaller stays inline in the record,
# where record_hash already covers it. 8 KiB matches the schema's inline cap.
INLINE_MAX_BYTES = 8192


class EvidenceIntegrityError(RuntimeError):
    """Raised when stored bytes do not match the address they were fetched by."""


class EvidenceStore:
    """A local-filesystem content-addressed blob store.

    The layout (two-character shard directory, then the full digest) is the same
    one git and most object stores use: it keeps any single directory from
    accumulating millions of entries. Swapping this for S3/MinIO means
    reimplementing put/get against the same addresses; nothing else changes.
    """

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path_for(self, digest: str) -> Path:
        return self.root / digest[:2] / digest

    @staticmethod
    def digest_of(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def put(self, data: bytes) -> str:
        """Store bytes, returning the bare hex digest that addresses them."""
        digest = self.digest_of(data)
        target = self._path_for(digest)
        if target.exists():
            # Same content, same address. Rewriting would be a no-op at best and
            # a window for corruption at worst.
            return digest
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=target.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, target)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        # Read-only on disk: a slip in calling code cannot truncate an existing
        # blob. Not a security boundary (the owner can chmod it back), but it
        # turns an accidental overwrite into a loud failure.
        try:
            target.chmod(0o444)
        except OSError:
            pass
        return digest

    def get(self, digest: str) -> bytes:
        """Fetch bytes by digest, re-verifying integrity on the way out."""
        digest = digest.removeprefix("sha256:")
        path = self._path_for(digest)
        if not path.exists():
            raise FileNotFoundError(f"evidence blob {digest} not found at {path}")
        data = path.read_bytes()
        actual = self.digest_of(data)
        if actual != digest:
            raise EvidenceIntegrityError(
                f"evidence blob at {path} has digest {actual}, expected {digest}: "
                f"the stored bytes have been altered"
            )
        return data

    def exists(self, digest: str) -> bool:
        return self._path_for(digest.removeprefix("sha256:")).exists()

    def uri_for(self, digest: str) -> str:
        """Where to fetch the blob, relative to the store root.

        Deliberately NOT an absolute path. A ledger record is meant to outlive
        the machine that wrote it and to be handed to an auditor, so baking in
        someone's home directory makes the record both non-portable and a small
        information leak. Fetching uses the sha256, never this string; the URI
        is a human-readable pointer, and an object-store backend would emit an
        s3:// URL here instead.
        """
        digest = digest.removeprefix("sha256:")
        return f"file://evidence_store/{digest[:2]}/{digest}"

    def store_evidence(
        self,
        *,
        evidence_type: str,
        source: str,
        content: str | bytes,
        retrieved_at: str,
        content_type: str = "text/plain",
        redacted: bool = False,
        force_blob: bool = False,
    ) -> dict:
        """Build a schema-valid evidence item, choosing inline vs blob by size."""
        data = content.encode("utf-8") if isinstance(content, str) else content
        item: dict = {
            "evidence_type": evidence_type,
            "source": source,
            "retrieved_at": retrieved_at,
        }
        if redacted:
            item["redacted"] = True

        if not force_blob and len(data) <= INLINE_MAX_BYTES:
            item["snapshot_ref_or_inline"] = {
                "kind": "inline",
                "content_type": content_type,
                "content": data.decode("utf-8", errors="replace"),
            }
        else:
            digest = self.put(data)
            preview = data[:900].decode("utf-8", errors="replace")
            item["snapshot_ref_or_inline"] = {
                "kind": "blob",
                "content_type": content_type,
                "sha256": f"sha256:{digest}",
                "uri": self.uri_for(digest),
                "size_bytes": len(data),
                "preview": preview + ("\n... [truncated, full blob in store]" if len(data) > 900 else ""),
            }
        return item
