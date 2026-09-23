-- Serialization for chain appends.
--
-- The hash chain has a race that is easy to miss: two concurrent writers both
-- read the same current head hash, both set it as their prev_record_hash, and
-- the chain forks. A forked chain still "verifies" record-by-record but is no
-- longer a total order, and the tamper-evidence argument collapses -- you can
-- no longer say a missing record would have been noticed.
--
-- A transaction-scoped advisory lock makes the read-head/append pair atomic.
-- It is released automatically at commit or rollback, so a crashed writer
-- cannot wedge the ledger. The key is an arbitrary constant that only has to be
-- unique within this database.

CREATE OR REPLACE FUNCTION aar_lock_chain() RETURNS void AS $$
    SELECT pg_advisory_xact_lock(4_198_237_461_009_112_233);
$$ LANGUAGE sql;

COMMENT ON FUNCTION aar_lock_chain() IS
    'Take the ledger append lock for the current transaction. Must be called before reading the chain head.';

GRANT EXECUTE ON FUNCTION aar_lock_chain() TO swarmops_app;

-- The current head of the chain, or NULL when the ledger is empty.
CREATE OR REPLACE VIEW aar_chain_head AS
    SELECT seq, record_id, record_hash, event_time
    FROM agent_action_records
    ORDER BY seq DESC
    LIMIT 1;

GRANT SELECT ON aar_chain_head TO swarmops_app;
