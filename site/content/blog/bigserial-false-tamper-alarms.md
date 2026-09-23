---
title: The BIGSERIAL trap: how a tamper-evident log learns to cry wolf
seo_title: The BIGSERIAL trap in tamper-evident logs
description: Postgres sequences are non-transactional, so a rolled-back insert leaves a permanent gap. If your audit log reads gaps as deletion, it will cry wolf.
published: 2026-09-20
tags: [postgres, audit-logs, integrity]
---

We build an append-only, hash-chained ledger for AI agent actions. Each record commits to the
hash of its predecessor, so editing or removing one breaks every hash after it. Alongside that,
the verifier checks something simpler: **the sequence numbers must be contiguous.**

That second check exists because the first one has a blind spot. If an attacker can delete a
record *and* rewrite the links around it, the chain still validates link-by-link. A hole in the
sequence is what catches the deletion anyway.

So the verifier reports a gap as tampering. Which was correct, until we wrote a test that
inserted a few deliberately invalid records and then asked whether the chain still verified.

It did not.

```
AssertionError: [{'seq': 7, 'kind': 'sequence_gap',
  'detail': 'expected seq 6, found 7 -- 1 record(s) missing from the ledger'}]
```

Nothing had been deleted. Nothing had been tampered with. The ledger was reporting an attack
that had not happened.

## Sequences are not transactional

The `seq` column was a `BIGSERIAL`, which is a `BIGINT` backed by a Postgres sequence. Sequences
are deliberately **non-transactional**: `nextval()` takes effect immediately and is *not* rolled
back when the surrounding transaction aborts.

This is a good design. If sequences rolled back, every `nextval()` would have to hold a lock
until commit, and concurrent inserts would serialise behind each other. Postgres trades gap-free
numbering for concurrency, and documents the trade clearly.

The consequence for us: every record rejected by schema validation burned a sequence value
permanently. Our writer validates *after* sealing the record — deliberately, so that the hash
fields are validated too — which means a malformed record reaches the `INSERT` attempt, fails,
and rolls back, leaving a hole behind.

Reject three bad records and the next good one lands at `seq = 5` instead of `seq = 2`. The
verifier then reports three deleted records, forever, on every subsequent run.

## Why this is worse than it sounds

A monitoring system that fires on real problems and also on routine ones does not degrade
gracefully — it degrades into being ignored. This is the alert-fatigue failure mode, and it is
considerably worse in an integrity tool than in a dashboard.

The entire value proposition of a tamper-evident ledger is that a red light means something. The
first time an engineer investigates a `sequence_gap`, finds a rejected record from three weeks
ago, and closes the ticket, the check has stopped working. Not technically — socially, which is
the only way that matters. The second occurrence gets less attention. By the fifth, `sequence_gap`
is a known-noisy check that people filter out of the report.

At which point a real deletion is invisible, and the tool is worse than not having it, because
it is also producing a green badge that someone believes.

## The fix: stop using a sequence

We already serialise appends. The chain has an inherent race — two concurrent writers reading the
same head hash would both set it as their `prev_record_hash` and fork the ledger — so every append
takes a transaction-scoped advisory lock before reading the chain head:

```sql
CREATE OR REPLACE FUNCTION aar_lock_chain() RETURNS void AS $$
    SELECT pg_advisory_xact_lock(4198237461009112233);
$$ LANGUAGE sql;
```

Inside that lock, computing the next sequence number is trivially safe:

```python
conn.execute("SELECT aar_lock_chain()")
head = conn.execute(
    "SELECT seq, record_hash FROM agent_action_records ORDER BY seq DESC LIMIT 1"
).fetchone()
record["prev_record_hash"] = head["record_hash"] if head else GENESIS_HASH
next_seq = (head["seq"] + 1) if head else 1
```

The column becomes a plain `BIGINT PRIMARY KEY` with no default. Because assignment happens
inside the transaction, a rollback takes the number back with it. A gap now means exactly one
thing.

We paid nothing for this. The lock was already there for a different reason, and the throughput
ceiling it imposes — one append at a time — is irrelevant at the volume of *consequential agent
actions*, which is the only thing this table stores.

## The general lesson

The bug was not in the integrity logic. Every hash was correct; the chain was sound. The bug was
that we borrowed a database primitive whose documented semantics were subtly wrong for the
guarantee we were building on top of it.

`BIGSERIAL` promises unique, ascending identifiers. We read it as promising **dense** ones. It
never did, and Postgres never claimed it did.

It is worth asking, of any check that turns an observation into an accusation: *what else could
produce this signal?* For sequence gaps in Postgres the answer is a long list — rolled-back
transactions, crashes mid-insert, `setval()`, cached sequence blocks lost on restart, logical
replication. Any one of them would have produced the same false alarm.

A check that cannot distinguish an attack from a Tuesday is not a security control. It is a
pager that goes off at random, attached to something important.
