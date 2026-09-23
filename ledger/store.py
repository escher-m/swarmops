"""The append-only ledger: writing records and reading them back.

The only way a record enters the ledger is through `LedgerStore.append`, which
is deliberately the single narrow path where validation, chaining and sealing
all happen together. Splitting those steps across callers is how chains end up
with unvalidated or unsealed entries.
"""

from __future__ import annotations

import json
import os
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row

from .canonical import GENESIS_HASH, seal
from .validation import SCHEMA_VERSION, validate_record

ROOT = Path(__file__).resolve().parent.parent

DEFAULT_DSN = os.environ.get(
    "SWARMOPS_LEDGER_DSN",
    f"host={ROOT / '.pgdata' / 'sockets'} port=5433 user=swarmops_app dbname=swarmops_ledger",
)


def utcnow() -> str:
    """Current time in the exact ISO-8601 UTC form the schema requires."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_record_id() -> str:
    return str(uuid.uuid4())


@dataclass(frozen=True)
class AppendResult:
    seq: int
    record_id: str
    record_hash: str
    prev_record_hash: str
    record: dict[str, Any]


class LedgerStore:
    """Append-only access to agent_action_records.

    Connects as swarmops_app, which holds only INSERT and SELECT. That is not a
    convention this class upholds -- it is a privilege the database enforces, so
    a bug here cannot mutate history even if it tries.
    """

    def __init__(self, dsn: str | None = None):
        self.dsn = dsn or DEFAULT_DSN

    @contextmanager
    def connect(self) -> Iterator[psycopg.Connection]:
        with psycopg.connect(self.dsn, row_factory=dict_row) as conn:
            yield conn

    # ---------------------------------------------------------------- writing

    def append(self, record: dict[str, Any], *, conn: psycopg.Connection | None = None) -> AppendResult:
        """Validate, chain, seal and insert one record.

        The advisory lock is taken before the head is read and held until
        commit, so concurrent writers serialize here rather than both chaining
        off the same predecessor and forking the ledger.
        """
        if conn is not None:
            return self._append_in(conn, record)
        with self.connect() as own:
            result = self._append_in(own, record)
            own.commit()
            return result

    def _append_in(self, conn: psycopg.Connection, record: dict[str, Any]) -> AppendResult:
        record = dict(record)
        record.setdefault("schema_version", SCHEMA_VERSION)
        record.setdefault("record_id", new_record_id())
        record.setdefault("timestamp", utcnow())
        record.pop("prev_record_hash", None)
        record.pop("record_hash", None)

        conn.execute("SELECT aar_lock_chain()")
        head = conn.execute(
            "SELECT seq, record_hash FROM agent_action_records ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        record["prev_record_hash"] = head["record_hash"] if head else GENESIS_HASH
        # Assigned here rather than by a sequence so that it is gapless: a
        # rolled-back insert leaves no hole, and therefore a hole in the
        # sequence is unambiguous evidence that a record was removed.
        next_seq = (head["seq"] + 1) if head else 1

        sealed = seal(record)
        # Validate after sealing so the hash fields are checked too. A record
        # that fails here never reaches the table, which matters because there
        # is no way to take it back out again.
        validate_record(sealed)

        row = conn.execute(
            "INSERT INTO agent_action_records (seq, record) VALUES (%s, %s) "
            "RETURNING seq, record_id::text, record_hash, prev_record_hash",
            (next_seq, json.dumps(sealed)),
        ).fetchone()

        return AppendResult(
            seq=row["seq"],
            record_id=row["record_id"],
            record_hash=row["record_hash"],
            prev_record_hash=row["prev_record_hash"],
            record=sealed,
        )

    def append_many(self, records: list[dict[str, Any]]) -> list[AppendResult]:
        """Append several records in one transaction, preserving their order."""
        with self.connect() as conn:
            results = [self._append_in(conn, r) for r in records]
            conn.commit()
            return results

    # ---------------------------------------------------------------- reading

    def iter_chain(self, *, batch: int = 500) -> Iterator[dict[str, Any]]:
        """Yield every record in chain order, streaming rather than loading all.

        Verification has to walk the entire ledger, which will not fit in memory
        at audit scale. A server-side cursor keeps that walk constant-memory.
        """
        with self.connect() as conn:
            with conn.cursor(name="aar_chain_walk") as cur:
                cur.itersize = batch
                cur.execute(
                    "SELECT seq, record, record_hash, prev_record_hash "
                    "FROM agent_action_records ORDER BY seq ASC"
                )
                for row in cur:
                    yield row

    def count(self) -> int:
        with self.connect() as conn:
            return conn.execute("SELECT count(*) AS n FROM agent_action_records").fetchone()["n"]

    def head(self) -> dict[str, Any] | None:
        with self.connect() as conn:
            return conn.execute(
                "SELECT seq, record_id::text, record_hash, event_time "
                "FROM agent_action_records ORDER BY seq DESC LIMIT 1"
            ).fetchone()

    def get(self, record_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT record FROM agent_action_records WHERE record_id = %s", (record_id,)
            ).fetchone()
            return row["record"] if row else None

    def incident(self, incident_id: str) -> list[dict[str, Any]]:
        """Every record for one incident, in ledger order."""
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT seq, record FROM agent_action_records "
                "WHERE incident_id = %s ORDER BY seq ASC",
                (incident_id,),
            ).fetchall()
            return [{"seq": r["seq"], **r["record"]} for r in rows]

    def query(
        self,
        *,
        start: str | None = None,
        end: str | None = None,
        actor: str | None = None,
        action_type: str | None = None,
        environment: str | None = None,
        outcome: str | None = None,
        incident_id: str | None = None,
        search: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Filtered record listing backing the compliance dashboard."""
        where: list[str] = []
        args: list[Any] = []
        for clause, value in (
            ("event_time >= %s", start),
            ("event_time <= %s", end),
            ("actor_agent_name = %s", actor),
            ("action_type = %s", action_type),
            ("environment = %s", environment),
            ("outcome_status = %s", outcome),
            ("incident_id = %s", incident_id),
        ):
            if value:
                where.append(clause)
                args.append(value)
        if search:
            where.append("(summary ILIKE %s OR resource_id ILIKE %s OR incident_id ILIKE %s)")
            args.extend([f"%{search}%"] * 3)

        sql = (
            "SELECT seq, record, event_time, record_hash FROM agent_action_records"
            + (" WHERE " + " AND ".join(where) if where else "")
            + " ORDER BY seq DESC LIMIT %s OFFSET %s"
        )
        args.extend([limit, offset])
        with self.connect() as conn:
            rows = conn.execute(sql, args).fetchall()
            return [{"seq": r["seq"], "event_time": r["event_time"], **r["record"]} for r in rows]

    def distinct(self, column: str) -> list[str]:
        """Distinct values for a filter dropdown. Column name is allow-listed."""
        allowed = {
            "actor_agent_name", "action_type", "environment",
            "outcome_status", "incident_id", "resource_type", "policy_id",
        }
        if column not in allowed:
            raise ValueError(f"column {column!r} is not filterable")
        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT DISTINCT {column} AS v FROM agent_action_records "
                f"WHERE {column} IS NOT NULL ORDER BY v"
            ).fetchall()
            return [r["v"] for r in rows]

    def stats(self) -> dict[str, Any]:
        with self.connect() as conn:
            return {
                "total": conn.execute("SELECT count(*) n FROM agent_action_records").fetchone()["n"],
                "incidents": conn.execute(
                    "SELECT count(DISTINCT incident_id) n FROM agent_action_records"
                ).fetchone()["n"],
                "by_action": conn.execute(
                    "SELECT action_type, count(*) n FROM agent_action_records "
                    "GROUP BY action_type ORDER BY n DESC"
                ).fetchall(),
                "by_outcome": conn.execute(
                    "SELECT outcome_status, count(*) n FROM agent_action_records "
                    "GROUP BY outcome_status ORDER BY n DESC"
                ).fetchall(),
                "by_environment": conn.execute(
                    "SELECT environment, count(*) n FROM agent_action_records "
                    "GROUP BY environment ORDER BY n DESC"
                ).fetchall(),
                # A request_approval record keeps outcome_status 'pending' for
                # ever: the ledger is append-only, so the decision is recorded
                # by a LATER approve/reject record rather than by updating this
                # one. Counting rows whose status reads 'pending' therefore
                # counts every approval ever requested. What the dashboard means
                # by pending is "not yet answered", which is a NOT EXISTS.
                "pending_approvals": conn.execute(
                    """
                    SELECT count(*) AS n
                    FROM agent_action_records r
                    WHERE r.action_type = 'request_approval'
                      AND NOT EXISTS (
                        SELECT 1 FROM agent_action_records d
                        WHERE d.action_type IN ('approve', 'reject')
                          AND d.record ->> 'in_response_to' = r.record_id::text
                      )
                    """
                ).fetchone()["n"],
            }
