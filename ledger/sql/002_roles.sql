-- Role separation for the ledger.
--
-- The application that writes records must not be able to rewrite them. That is
-- the entire premise of the product, and "we don't call UPDATE anywhere" is not
-- a control an auditor can test. This file makes it a database privilege, which
-- an auditor can test in one command:
--
--     psql -U swarmops_app -c "UPDATE agent_action_records SET record = '{}'"
--     ERROR:  permission denied for table agent_action_records
--
-- Two roles:
--   swarmops_owner  owns the table and runs migrations. Used by nobody at runtime.
--   swarmops_app    the only role the application, adapter and dashboard use.
--                   Holds INSERT and SELECT. Not UPDATE. Not DELETE. Not TRUNCATE.

-- The role is created WITHOUT a password on purpose. The local development
-- cluster authenticates over a unix socket with trust auth, so none is needed,
-- and committing a literal password to a repository is how dev credentials end
-- up in production. A real deployment sets one out of band:
--
--     ALTER ROLE swarmops_app LOGIN PASSWORD '<from your secret manager>';
--
-- or, better, authenticates with SCRAM, client certificates or cloud IAM.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'swarmops_app') THEN
        CREATE ROLE swarmops_app LOGIN;
    END IF;
END
$$;

-- Start from nothing rather than assuming the default state.
REVOKE ALL ON agent_action_records FROM swarmops_app;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM swarmops_app;

GRANT USAGE ON SCHEMA public TO swarmops_app;

-- The only two verbs the application ever needs.
GRANT SELECT, INSERT ON agent_action_records TO swarmops_app;

-- Explicit and redundant with the REVOKE above, but it documents intent at the
-- place an auditor will look, and survives someone later running a blanket
-- GRANT ALL in a hurry only to be contradicted here on the next migration.
REVOKE UPDATE, DELETE, TRUNCATE ON agent_action_records FROM swarmops_app;

-- Future tables in this schema must not quietly become writable either.
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    REVOKE UPDATE, DELETE, TRUNCATE ON TABLES FROM swarmops_app;
