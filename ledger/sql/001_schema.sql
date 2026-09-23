-- Ledger schema: append-only, hash-chained store of agent action records.
--
-- Design notes that matter for audit defensibility:
--
--  1. `record` (jsonb) is the single authoritative copy of the entry, and it is
--     exactly what record_hash covers. Every other column is either derived
--     from it by the database or set by a trigger at insert time. Nothing is
--     independently writable, so no column can silently disagree with the
--     hashed payload -- a dashboard filter and the hash always describe the
--     same fact.
--
--  2. Append-only is enforced in three independent places, because any one of
--     them alone is bypassable by someone who is already inside:
--       * grants (002_roles.sql) -- the application role simply lacks the
--         UPDATE/DELETE privilege, so a compromised app cannot mutate history;
--       * a trigger -- which also stops the table *owner* and anyone with a
--         direct psql session, who grants alone would not stop;
--       * the hash chain -- which makes a mutation that defeated both of the
--         above detectable after the fact by anyone holding an old hash.
--     Only the third survives a determined superuser, which is precisely why
--     it exists.
--
--  3. seq is the chain order. It is a plain bigserial rather than the record
--     timestamp because timestamps come from the agent and can collide, skew,
--     or arrive out of order; the chain needs a total order the database owns.

CREATE TABLE IF NOT EXISTS agent_action_records (
    -- Deliberately NOT a bigserial. A Postgres sequence is non-transactional:
    -- it advances even when the inserting transaction rolls back, so a record
    -- rejected by validation would burn a number and leave a permanent hole.
    -- The verifier treats a hole as evidence of a deleted record, and a trust
    -- tool that cries wolf on routine rejections is worse than no tool at all.
    -- Instead the writer assigns seq as max(seq)+1 while holding the chain
    -- advisory lock (see 003_chain_lock.sql and LedgerStore._append_in), which
    -- is race-free and gapless, so a gap always means something was removed.
    seq                 BIGINT      PRIMARY KEY,

    -- The authoritative record. Everything below is derived from this.
    record              JSONB       NOT NULL,

    record_id           UUID        NOT NULL UNIQUE
                        GENERATED ALWAYS AS ((record ->> 'record_id')::uuid) STORED,
    incident_id         TEXT        NOT NULL
                        GENERATED ALWAYS AS (record ->> 'incident_id') STORED,
    action_type         TEXT        NOT NULL
                        GENERATED ALWAYS AS (record ->> 'action_type') STORED,
    summary             TEXT
                        GENERATED ALWAYS AS (record ->> 'summary') STORED,

    actor_agent_name    TEXT
                        GENERATED ALWAYS AS (record -> 'actor' ->> 'agent_name') STORED,
    actor_platform      TEXT
                        GENERATED ALWAYS AS (record -> 'actor' ->> 'tool_or_platform') STORED,
    actor_invocation_id TEXT
                        GENERATED ALWAYS AS (record -> 'actor' ->> 'invocation_id') STORED,

    resource_type       TEXT
                        GENERATED ALWAYS AS (record -> 'target' ->> 'resource_type') STORED,
    resource_id         TEXT
                        GENERATED ALWAYS AS (record -> 'target' ->> 'resource_id') STORED,
    environment         TEXT
                        GENERATED ALWAYS AS (record -> 'target' ->> 'environment') STORED,

    policy_id           TEXT
                        GENERATED ALWAYS AS (record -> 'policy_reference' ->> 'policy_id') STORED,
    allowed_by          TEXT
                        GENERATED ALWAYS AS (record -> 'policy_reference' ->> 'allowed_by') STORED,

    approver_id         TEXT
                        GENERATED ALWAYS AS (record -> 'approver' ->> 'approver_id') STORED,
    approver_type       TEXT
                        GENERATED ALWAYS AS (record -> 'approver' ->> 'approver_type') STORED,

    outcome_status      TEXT
                        GENERATED ALWAYS AS (record -> 'outcome' ->> 'status') STORED,

    prev_record_hash    CHAR(64)    NOT NULL
                        GENERATED ALWAYS AS (record ->> 'prev_record_hash') STORED,
    record_hash         CHAR(64)    NOT NULL UNIQUE
                        GENERATED ALWAYS AS (record ->> 'record_hash') STORED,

    -- Set by trigger rather than GENERATED because casting text to timestamptz
    -- is not an immutable expression (it can depend on the session TimeZone),
    -- and Postgres rightly refuses non-immutable generation expressions.
    -- Populating it once at insert is equivalent here: UPDATE is forbidden, so
    -- it can never drift from `record`.
    event_time          TIMESTAMPTZ NOT NULL,

    -- When we wrote it, as opposed to when the agent says it happened. A wide
    -- gap between the two is itself an audit signal.
    inserted_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE agent_action_records IS
    'Append-only hash-chained ledger of AI agent actions. Never UPDATE or DELETE.';

-- Indexes serving the compliance dashboard's filters.
CREATE INDEX IF NOT EXISTS aar_event_time_idx   ON agent_action_records (event_time DESC);
CREATE INDEX IF NOT EXISTS aar_incident_idx     ON agent_action_records (incident_id, seq);
CREATE INDEX IF NOT EXISTS aar_action_type_idx  ON agent_action_records (action_type);
CREATE INDEX IF NOT EXISTS aar_environment_idx  ON agent_action_records (environment);
CREATE INDEX IF NOT EXISTS aar_actor_idx        ON agent_action_records (actor_agent_name);
CREATE INDEX IF NOT EXISTS aar_outcome_idx      ON agent_action_records (outcome_status);
CREATE INDEX IF NOT EXISTS aar_prev_hash_idx    ON agent_action_records (prev_record_hash);
CREATE INDEX IF NOT EXISTS aar_record_gin       ON agent_action_records USING gin (record);


-- Populate event_time from the authoritative payload, and refuse anything that
-- does not carry a parseable timestamp.
CREATE OR REPLACE FUNCTION aar_set_event_time() RETURNS trigger AS $$
BEGIN
    IF NEW.record ->> 'timestamp' IS NULL THEN
        RAISE EXCEPTION 'record % has no timestamp', NEW.record ->> 'record_id';
    END IF;
    NEW.event_time := (NEW.record ->> 'timestamp')::timestamptz;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS aar_set_event_time_trg ON agent_action_records;
CREATE TRIGGER aar_set_event_time_trg
    BEFORE INSERT ON agent_action_records
    FOR EACH ROW EXECUTE FUNCTION aar_set_event_time();


-- Append-only enforcement that binds the table owner too, not just the
-- application role. Grants stop a compromised application; this stops a tired
-- engineer with a psql prompt.
CREATE OR REPLACE FUNCTION aar_forbid_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION
        'agent_action_records is append-only: % is not permitted (attempted on record_id %)',
        TG_OP,
        COALESCE(OLD.record ->> 'record_id', '?')
        USING HINT = 'Corrections are made by appending a new record, never by editing history.';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS aar_forbid_update_trg ON agent_action_records;
CREATE TRIGGER aar_forbid_update_trg
    BEFORE UPDATE ON agent_action_records
    FOR EACH ROW EXECUTE FUNCTION aar_forbid_mutation();

DROP TRIGGER IF EXISTS aar_forbid_delete_trg ON agent_action_records;
CREATE TRIGGER aar_forbid_delete_trg
    BEFORE DELETE ON agent_action_records
    FOR EACH ROW EXECUTE FUNCTION aar_forbid_mutation();

-- Row-level triggers only fire when a row actually matches, so `DELETE FROM
-- agent_action_records` against an empty table would quietly succeed and an
-- operator could conclude the table is mutable-but-empty rather than protected.
-- Worse, the control cannot be tested before the first record exists. These
-- statement-level triggers fire on the attempt itself, regardless of how many
-- rows match, which makes the guarantee unconditional and testable from day
-- zero. The row-level pair is kept because it can name the specific record.
CREATE OR REPLACE FUNCTION aar_forbid_mutation_stmt() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'agent_action_records is append-only: % is not permitted', TG_OP
        USING HINT = 'Corrections are made by appending a new record, never by editing history.';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS aar_forbid_update_stmt_trg ON agent_action_records;
CREATE TRIGGER aar_forbid_update_stmt_trg
    BEFORE UPDATE ON agent_action_records
    FOR EACH STATEMENT EXECUTE FUNCTION aar_forbid_mutation_stmt();

DROP TRIGGER IF EXISTS aar_forbid_delete_stmt_trg ON agent_action_records;
CREATE TRIGGER aar_forbid_delete_stmt_trg
    BEFORE DELETE ON agent_action_records
    FOR EACH STATEMENT EXECUTE FUNCTION aar_forbid_mutation_stmt();

-- TRUNCATE bypasses row-level triggers entirely, so it needs its own statement
-- level guard. Without this the whole ledger is one command away from gone.
CREATE OR REPLACE FUNCTION aar_forbid_truncate() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'agent_action_records is append-only: TRUNCATE is not permitted';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS aar_forbid_truncate_trg ON agent_action_records;
CREATE TRIGGER aar_forbid_truncate_trg
    BEFORE TRUNCATE ON agent_action_records
    FOR EACH STATEMENT EXECUTE FUNCTION aar_forbid_truncate();
