# SwarmOps — a vendor-neutral audit ledger for AI agent actions

Every consequential thing an AI agent does — investigating, proposing a fix,
asking for approval, executing — is recorded as one normalised, tamper-evident
entry showing **who acted, under what policy, on what evidence, approved by
whom, with what outcome.**

This is not an SRE agent. It wraps an existing one ([HolmesGPT]) and records
what it does. The product is the ledger, the schema, and the compliance surface.

```
[HolmesGPT investigation] --tool calls--> [Adapter] --records--> [Append-only ledger]
                                                                  (Postgres, hash-chained)
                                                                         |
      [Approval gate] <---- propose_fix -------------------------------- +
             |                                                           |
             +--- approve ---> [Executor] --- execute + outcome -------> +
                                                                         |
                                                              [Compliance dashboard]
                                                          filter · incident view · export
```

## Quick start

```bash
bash scripts/setup.sh          # venv, PostgreSQL 16, databases, fixtures, tests
```

Nothing is installed system-wide. PostgreSQL is a conda-forge build kept in
`.pg/`, its data lives in `.pgdata/`, and `make clean` removes both.

```bash
make app         # seeded checkout service on :8081
make dashboard   # compliance dashboard on :8090  <- the product; requires sign-in
make site-serve  # marketing site on :8100        <- swarmops.dev; public homepage
```

Those last two are different things and it is worth being clear about which is which.
The **dashboard** is the product: internal B2B infrastructure that a customer deploys
inside their own network, behind their own SSO. It has no public homepage and no sign-up,
because nobody discovers it — a platform team installs it. Opening it lands you on a
sign-in page, which is correct.

The **marketing site** is `swarmops.dev`: the public homepage, blog, FAQ and waitlist.
That is the one with a front page. In a real deployment these are different hosts
entirely (`ledger.acme.internal` and `swarmops.dev`); locally they are just two ports.

The dashboard can approve and execute changes, so it **refuses to start without
authentication configured**. For a laptop demo, `SWARMOPS_AUTH_MODE=dev` reads identities
from `dev_users.json`. For anything real, point it at your identity provider:

```bash
SWARMOPS_AUTH_MODE=oidc
SWARMOPS_OIDC_DISCOVERY_URL=https://your-org.okta.com/.well-known/openid-configuration
SWARMOPS_APPROVER_ROLES=sre-oncall
```

The authenticated subject becomes `approver_id` in the ledger — `okta|00u4k2x9zQpLmNb7h`,
not `local:alice`. Dev mode is refused outright when `SWARMOPS_ENV=production`.

To run a real investigation, HolmesGPT needs an LLM:

```bash
cp .env.example .env      # set ANTHROPIC_API_KEY
.venv/bin/python scripts/run_incident.py --fault bad_deploy
```

## The three claims, and how to check them yourself

Each of these is a command you can run without trusting anything in this repo.

**1. The ledger is append-only, enforced by the database.**

```bash
bash scripts/prove_append_only.sh
```

Opens a direct SQL session and tries to rewrite history — first as the role the
application actually uses, then as the table owner. Ten mutation attempts, all
refused. The application role is denied by *privilege* (it was never granted
UPDATE or DELETE, which is what protects the ledger if the application is
compromised); the owner is denied by *trigger*, including statement-level
triggers so the guarantee holds on an empty table and `TRUNCATE` is covered.

**2. Tampering is detectable even by someone who defeats those controls.**

```bash
.venv/bin/python scripts/tamper_demo.py
```

Plays the attacker: connects as superuser, disables the triggers, and stages
three attacks — editing an execution's outcome, deleting an approval record, and
rewriting an evidence blob while leaving the ledger untouched. All three are
caught, and the ledger is restored exactly as found.

**3. The chain verifies.**

```bash
.venv/bin/python scripts/verify_chain.py        # or --json for CI
```

Five independent checks: hash integrity, chain linkage, sequence density, schema
validity, and evidence-blob integrity. The result is on the dashboard as a live
badge, never a stale claim.

## Components

| | What it is | Where |
|---|---|---|
| **1. Schema** | JSON Schema for one ledger entry, with conditional rules that make silent auto-remediation unrecordable | `schema/agent_action_record.schema.json` |
| | Worked example: a full investigate → propose → approve → execute chain | `schema/fixtures/` |
| **2. Ledger** | Append-only Postgres table, hash chain, role separation | `ledger/sql/`, `ledger/store.py` |
| | Canonical JSON and the hash function | `ledger/canonical.py` |
| | Content-addressed evidence store | `ledger/evidence_store.py` |
| | Policy engine (default-deny, explicit denies win) | `ledger/policy.py`, `policy/*.yaml` |
| **3. Adapter** | Wraps HolmesGPT; every tool call becomes evidence | `adapter/holmes_adapter.py` |
| | Read-only toolset pointed at the seeded service | `adapter/toolsets/seedapp.yaml` |
| **4. Approval gate** | Pending queue derived from the ledger; CLI and web | `approval/` |
| | Executor — re-reads its authorisation from the ledger | `approval/executor.py` |
| **5. Seeded service** | Flask + Postgres with four real failure modes | `seedapp/` |
| **6. Dashboard** | Filterable ledger, incident view, PDF/CSV export | `dashboard/` |
| | OIDC/SSO, session handling, approver-role enforcement | `dashboard/auth.py` |
| | React islands — ⌘K command palette over the ledger | `dashboard/ui/` |
| **Marketing site** | Static site for swarmops.dev — SEO, blog, FAQ, comparison, waitlist | `site/` |

## The schema

The field that does the most work is `policy_reference.allowed_by`. It must name
a concrete rule — `rule:human_approved_reversible_rollback` — not "RBAC allowed
it", and `policy_digest` pins the sha256 of the exact policy text in force at the
time, so a later edit to the policy cannot retroactively change what an action
was permitted by.

Conditional rules in the schema carry real weight:

- an `investigate` or `propose_fix` record with no evidence is invalid;
- a `propose_fix` must state the exact command or diff;
- an `execute` must carry an approver **and** point at the approval that
  authorised it.

That last rule is the one that matters: there is no way to write a valid
`execute` record for an action nobody approved.

### Design decisions worth knowing about

**Floats are rejected outright.** RFC 8785's hardest requirement is reproducing
ECMAScript number formatting; getting it subtly wrong yields hashes that verify
on one implementation and fail on another. No field needs a float, so the whole
class of bug is removed rather than managed.

**`seq` is not a `BIGSERIAL`.** Postgres sequences are non-transactional — they
advance even when the insert rolls back. A record rejected by validation would
burn a number and leave a permanent hole, and the verifier reads holes as
deleted records. The writer assigns `seq` under the chain advisory lock instead,
so it is gapless and a gap is unambiguous.

**Filterable columns are `GENERATED ALWAYS`.** The `record` JSONB is the only
authoritative copy and is exactly what the hash covers. Every other column is
derived from it by the database, so a dashboard filter and the hash can never
disagree.

**Appends take an advisory lock.** Two concurrent writers reading the same head
hash would fork the chain. A forked chain still verifies record-by-record but is
no longer a total order, and the tamper-evidence argument quietly collapses.

**Policy fails closed.** A condition whose fact is missing does not match. If we
cannot tell whether a change is reversible, it is not treated as reversible.

**The dashboard is a server-rendered app with React islands, not an SPA.** Jinja
owns routing, authentication and the page shell. React mounts only where a rich
interaction earns it — currently the ⌘K command palette, built on
[Watermelon UI](https://ui.watermelon.sh) (MIT) and adapted in
`dashboard/ui/src/components/watermelon/`. A page with no island still renders
completely, and every destination the palette offers is reachable from the sidebar.

**The dashboard makes no third-party requests.** Fonts and scripts are served from
your own host, React and Motion included. A compliance tool that loads assets from a CDN tells that CDN which
audit pages were viewed and when, and breaks outright in the air-gapped environments
this product is most likely to run in.

## Approval, and what "approved" means here

Nothing executes without an `approve` record in the ledger. The executor does
not accept a caller's word that something was approved — it takes an approve
record id, walks `approve → request_approval → propose_fix` in the ledger, and
re-evaluates policy at execution time.

What it runs is read back out of the **immutable evidence blob** attached to the
proposal, not passed in as a parameter. The approver saw that blob; its content
is addressed by its own hash; so the action executed is provably the action
approved.

Approvals made by the unattended trial harness are recorded with
`approval_channel: trial_harness_unattended` and are flagged as such in the
dashboard and the PDF. An automated approval is not dressed up as human review.

The approver identity is never accepted from the request body. It comes from the
session the identity provider issued, and the route that records a decision is
guarded by a role check — a user without an approver role can read every proposal
and its evidence but cannot decide one. An unauthenticated approve record would be
a lie told in a tamper-evident way, which is worse than no record at all.

## Seeded failure modes

| Mode | Mechanism | Fix |
|---|---|---|
| `bad_deploy` | v1.4.2 makes `shipping_cost` nullable without updating the order-total arithmetic; `/checkout` raises `TypeError` | roll back to v1.4.1 |
| `pool_exhaustion` | pool squeezed to one connection while `/orders` holds it; concurrent requests fail on checkout | restore pool size |
| `slow_query` | supporting index dropped and the report issues one query per customer | restore index and the single aggregate (2.2s → 0.08s) |
| `leaked_secret` | a real commit hardcodes an AWS-style key | rotate the credential |

The orchestrator generates real traffic **before** investigating, so the fault
manifests in logs and metrics first. Injecting a fault and immediately asking
about it produces integrations that depend on symptoms real incidents lack.

Secrets are masked before evidence is stored — a ledger that faithfully records
a leaked credential has helpfully made a second copy of it.

## Trial results

```bash
.venv/bin/python scripts/run_trials.py --runs 20
```

Rotates across all four failure modes and reports per-stage outcomes, so a
partial failure ("diagnosed correctly, proposed the wrong remediation") stays
visible instead of collapsing into pass/fail. Results land in `runs/`.

> **Status:** not yet run. Requires an LLM API key; see `.env.example`.

## Dashboard

- **All records** — filter by time, actor, action type, environment, outcome
- **Incidents** — one row per incident
- **Incident view** — answers what broke, what the agent examined, what it
  proposed, who approved it, when it executed, and whether it worked, in that
  order, before any structured detail
- **Approvals** — pending queue; approving writes the decision and applies the change
- **Verification** — live chain check with a 3D rendering of the actual chain
- **Export** — CSV of raw records, or a PDF audit report

Keyboard: `g` records · `i` incidents · `a` approvals · `v` verification · `/` search.

## Tests

```bash
make test     # 115 tests
```

Covers canonicalization determinism and float rejection, every schema
conditional rule, evidence content-addressing and tamper detection, policy
fail-closed behaviour, session forgery and expiry, config that refuses to start
unauthenticated, and — against a real throwaway Postgres — chain linkage,
append-only enforcement including on an empty table, and the verifier correctly
detecting edited and deleted records.

## Deploying

The dashboard runs inside the estate that produces the actions it records, not
as a hosted service. `deploy/` holds a reference Dockerfile and compose file:

```bash
cp deploy/.env.example deploy/.env       # fill in three secrets
docker compose --env-file deploy/.env -f deploy/docker-compose.yml up -d --build
```

The image carries twelve Python packages rather than the 190 in
`requirements.txt` — HolmesGPT and the LLM stack have no business in a service
that reads a database and renders HTML. Evidence blobs need the named volume:
without it the store is empty after every restart and the verifier correctly
reports `evidence_missing` on records nobody touched.

The marketing site is separate and static. `vercel.json` builds it with two
dependencies; the dashboard is not deployable to a serverless host, because it
needs a long-running process, a writable filesystem and a Postgres it owns.

See [deploy/README.md](deploy/README.md), including an explicit note on what is
and is not verified.

## Known gaps

The full list, prioritised, is in [TODO.md](TODO.md). The ones worth knowing before you
read anything else as a finished claim:

- **The 20-run trial loop has not been executed.** No API key was available. Everything up
  to the LLM call is exercised by tests, but the adapter has never driven a real HolmesGPT
  investigation end to end.
- **OIDC is untested against a live identity provider.** The flow, claims mapping and
  session handling are tested; no real Okta/Entra/Auth0 tenant was available.
- **One approver can authorise a production change.** The policy supports
  `approver_roles` but has no two-person rule.
- **A superuser who rewrites the whole chain is undetectable.** The chain makes tampering
  detectable only by someone holding an earlier head. Publishing heads to an external
  trust boundary is the missing piece.
- **No Kubernetes, no Slack, one adapter.** The reference environment runs as local
  processes, the approval gate is web and CLI, and vendor-neutrality currently rests on a
  single integration.


[HolmesGPT]: https://github.com/robusta-dev/holmesgpt
