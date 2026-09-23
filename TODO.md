# Open work

Everything known to be unfinished, wrong, or deliberately deferred, with why it matters
and how you'd know it's done. Ordered by what blocks what.

Items are marked `[ ]` open, `[~]` partially done, `[x]` closed. Closed items stay for a
while so the reasoning survives.

---

## P0 — blocks a claim we're already making

### [ ] Run the 20+ trial loop
**Blocked on:** an LLM API key.
The spec's acceptance criterion for the HolmesGPT integration is 20+ real investigations
with honest success rates. It has never run. Everything up to the LLM call is tested, but
the adapter has never driven a real HolmesGPT investigation end to end — which means the
riskiest assumption in the project is unverified.

```bash
cp .env.example .env    # set ANTHROPIC_API_KEY
.venv/bin/python scripts/run_trials.py --runs 20
```
**Done when:** `runs/` holds real per-run data and the README's trial section has real
numbers, including the failures.

### [ ] Commit the repository
`git log` is empty and there is no remote. Every artifact from both sessions exists only
on one disk, with no history and no backup.
**Done when:** initial commit pushed somewhere, with `.gitignore` confirmed to exclude
`.venv/ .pg/ .pgdata/ .env dev_users.json site/dist/`.

---

## P1 — before anyone outside the team runs this

### [ ] Test OIDC against a live identity provider
Config validation, claims mapping and session handling are unit-tested, and the dev-mode
HTTP flow is tested end to end. The actual OIDC round trip — discovery, redirect,
`authorize_access_token`, userinfo — has only ever run against mocked claims.
The `groups` claim in particular varies a lot between Okta, Entra and Auth0.
**Done when:** a real tenant signs in and the resulting `approver_id` lands in a ledger
record with the roles the IdP actually issued. `dashboard/auth.py`

### [ ] Two-person approval for production
Today one human with `sre-oncall` can authorise a production change alone. The policy
already carries `approver_roles`; it has no `min_approvals`. For the environment where
this matters most, single-approver is probably not the control anyone wants.
**Done when:** `policy/sre_agent_policy_v3.yaml` supports `min_approvals: 2`, the gate
holds a request open until satisfied, and the ledger shows both approvers.

### [ ] Publish the chain head somewhere we don't control
The hash chain makes tampering *detectable*, but only by someone holding an earlier head.
A superuser who rewrites the entire chain and recomputes every hash produces a ledger
that verifies perfectly. Publishing the head periodically to a different trust boundary
(object storage with object-lock, a transparency log, a signed daily email) closes this.
Currently listed in the README as a known gap and referenced on the site's FAQ.
**Done when:** heads are published on a schedule and `verify_chain.py` can check the
current chain against a published head.

### [ ] Session revocation
Sessions are signed cookies with an 8-hour lifetime and no server-side state, so a
compromised session cannot be invalidated before it expires. Acceptable for a demo, not
for the surface that approves production changes.
**Done when:** logout invalidates server-side, and an admin can revoke a subject's
sessions. `dashboard/auth.py`

### [ ] Rate-limit and harden the dev login
`POST /auth/login` has no rate limiting or lockout. Passwords in `dev_users.json` are
plain text — documented as deliberate, but only safe while dev mode stays a laptop demo.
**Done when:** attempts are rate-limited, and either passwords are hashed or dev mode is
removed in favour of OIDC-only.

### [ ] Audit our own reads
The ledger records what agents did. It records nothing about who read or exported it.
"Who pulled the full audit export last Tuesday" is a question an auditor will eventually
ask about the audit tool itself.
**Done when:** reads and exports are logged (separately from the agent-action ledger —
they are not agent actions and must not pollute the chain).

### [ ] Backup and restore procedure
Append-only means damage cannot be repaired in place. There is no documented backup, no
restore drill, and no statement of what a restore does to the chain.
**Done when:** a documented, tested restore produces a ledger that passes
`verify_chain.py`.

---

## P2 — before swarmops.dev goes live

### [ ] Wire up the waitlist form
`site/templates/waitlist.html` has `action="#REPLACE_WITH_YOUR_FORM_ENDPOINT"`.
Submissions currently go nowhere. Formspree, Netlify Forms and a custom endpoint all work
without JavaScript. A waitlist collects personal data — say where it goes and honour
deletion requests.

### [ ] Confirm the domain and set `BASE_URL`
`site/build.py` hardcodes `https://swarmops.dev`, which is baked into canonical tags, the
sitemap and the RSS feed. Wrong domain means wrong canonicals from day one.

### [ ] Decide how far to take the comparison table
`/compare/` is currently **category-level** — it characterises LLM observability, AI SRE
agents and AI GRC as categories, which the research supports. It does not name
per-vendor capabilities. If you want named-vendor columns, every cell needs verifying
against live docs first; this space changes monthly and a wrong cell in a sales deck is a
real problem.

### [ ] Investor page numbers
The placeholder metrics table has been removed. Add real figures — design partners,
actions recorded, raise — when they exist.

### [ ] Replace the illustrative homepage figure
The hero terminal shows a ledger at ~2,500 records, captioned "Illustrative output at
scale" directly beneath. Honest as it stands; replace with a real design-partner figure
as soon as there is one.

### [ ] Vercel header config
`site/dist/_headers` is the Netlify/Cloudflare format. If you deploy to Vercel, the CSP
and security headers need a `vercel.json` instead or they are silently dropped.

### [ ] Accessibility pass
Never audited. Needs: contrast ratios in both themes, keyboard navigation, visible focus
states, screen-reader labels on icon-only buttons, and `prefers-reduced-motion` honoured
everywhere (the reveal animations already check it; the rest has not been reviewed).

### [ ] Hosted read-only demo
The site now shows real screenshots, but a prospect still cannot *use* anything. A demo
instance — seeded incidents, auth bypassed, every write disabled, a persistent banner
saying so — is far more convincing than images. Needs a genuinely safe read-only path
through code that currently assumes a real approver: the approvals form, the decide
route and the executor all have to be inert, not merely hidden.
**Done when:** a public URL serves the dashboard with writes provably impossible, and
the site links to it.

### [ ] The dashboard now ships 133 KB gzipped of React
Loaded on every dashboard page, for one component. Options: code-split so the bundle
loads only when ⌘K is first pressed, or use Preact's compat layer (roughly a third
the size). Worth doing before a second island lands, not after.
**Done when:** the palette costs nothing until it is opened, or the bundle is under
~60 KB gzipped.

### [ ] The homepage now ships 600 KB of three.js
Vendored locally so the no-third-party-requests guarantee holds, and loaded only on
`/`, but it is still by far the largest asset on the site and it blocks nothing useful
until it parses. Options: a custom three.js build with only the modules the scene uses
(realistically ~150 KB), loading it after first paint, or dropping to canvas 2D as the
hero does on the waitlist page.
**Done when:** the homepage's JS payload is under ~200 KB, or the scene loads without
delaying first contentful paint.

### [ ] Small site polish
- No `/favicon.ico` file — only an inline SVG data URI. Some crawlers and older clients
  want the file.
- `og.png` renders its *text* in Helvetica, not Inter: Pillow cannot read woff2. The
  mark itself is now generated from `scripts/logo_mark.py`, so only the type is off-brand.
  Fix by vendoring a TTF for the generator.
- No analytics at all. Deliberate for now — decide whether to add something
  privacy-preserving or stay dark.

---

## P3 — hardening and scale

### [ ] Incremental chain verification
The dashboard re-walks the entire chain every 20 seconds per page load. That is O(n) and
gets slower the more trustworthy the ledger becomes — exactly the wrong incentive. Needs
checkpointing: verify from the last known-good checkpoint forward.
`dashboard/app.py:verification()`, `scripts/verify_chain.py`

### [ ] Paginate the export
`/export.pdf` caps at 5,000 records and `/export.csv` at 10,000, both loaded into memory
with no pagination. A real ledger will time out or OOM.

### [ ] Evidence store beyond the local filesystem
`ledger/evidence_store.py` is local-disk only. The interface is content-addressed
`put`/`get`, so S3/MinIO is a reimplementation of two methods — but it hasn't been done,
and local disk is not a real deployment target.

### [ ] A second adapter
The vendor-neutrality claim is the core of the pitch and currently rests on one adapter.
A second one — another AI SRE agent, or an in-house agent — is what turns the claim into
a demonstration.

### [ ] Exercise a real Kubernetes toolset
The reference environment runs as local processes, so HolmesGPT's k8s toolsets have never
run. The current toolset is logs, metrics, HTTP, git and query plans.

### [ ] Slack approval gate
Scoped out deliberately; `approval_channel` already carries the value and the gate is
channel-agnostic. Worth building when a design partner wants it.

### [ ] Retention and archival
Append-only forever means unbounded growth, and no stated retention policy. Archival has
to preserve verifiability — archived records still need to chain.

### [ ] Revisit the append lock under load
Every append takes a transaction-scoped advisory lock, so appends serialise. Correct, and
irrelevant at agent-action volumes, but it is a hard ceiling if this ever ingests
per-tool-call traffic from many agents at once.

---

## P4 — product direction

- [ ] Multi-tenancy — one deployment, several teams, isolation between them
- [ ] SIEM export — Splunk/Datadog/Sentinel, so the ledger feeds existing alerting
- [ ] Alert on policy refusals — a blocked action is a signal nobody currently sees
- [ ] Schema v2 migration path — `schema_version` exists; the migration story does not

---

## Closed

- [x] **The marketing site never showed the product.** Three pages described a
  compliance dashboard without a single image of one. Added a tabbed screenshot
  section to the homepage and a contextual capture to how-it-works, taken from the
  running dashboard by `scripts/capture_shots.py` (`make shots`) rather than drawn
  as mockups — so a UI regression shows up in them.
- [x] **Nothing linked the two surfaces.** The dashboard's sidebar logo now opens
  swarmops.dev. Deliberately one-directional: every customer runs their own instance
  at their own hostname, so there is no single dashboard URL for the site to point at.

- [x] **Command palette added to the dashboard** (⌘K), built on Watermelon UI's
  `command-search` (MIT) and wired to a new auth-protected `/api/search`. Searches
  incidents and records, navigates on Enter. Adapted on the way in: the upstream
  hotkey was a bare `f`, which fires while reading and collides with the existing
  single-letter navigation; the zinc palette was mapped onto the dashboard's design
  tokens so it follows the theme toggle; and the icon package was replaced with the
  sidebar's own SVGs.
- [x] **React island bundle was shipping React's development build.** Vite's library
  mode does not substitute `process.env.NODE_ENV` inside dependencies, so the bundle
  carried warning strings and devtools hooks: 1,253 KB raw / 289 KB gzipped. Defining
  it explicitly cut that to 540 KB / 133 KB. The build succeeds either way, which is
  what makes it easy to miss.
- [x] **Skipping Tailwind preflight had a visible cost.** Form controls kept their
  user-agent styling, so unselected palette rows rendered with light grey
  `buttonface` backgrounds in a dark UI. Fixed with a minimal reset scoped to
  `[data-island]` rather than re-enabling a global reset that would restyle the
  server-rendered pages.

- [x] **Homepage rebuilt around the product's own imagery.** A three.js hero showing
  the ledger itself — sealed blocks travelling a curve with uncommitted agent actions
  drifting around them — plus staggered word reveal, cursor-spotlight bento cards,
  a scroll-pinned five-step walkthrough, a typewriter terminal, count-up metrics and
  a marquee of real schema field names. All implemented in vanilla JS
  (`site/assets/motion.js`) rather than pulling in a component library, so the site
  still loads nothing third-party.
- [x] **Logo replaced with the orbit mark** (three agents circling one sealed record),
  generated once in `scripts/logo_mark.py` and shared by both stylesheets, all three
  favicons and the social card.
- [x] **Orbit animation moved off the homepage** to `/waitlist/`, where it frames the
  form as a halo instead of competing with body copy. Its placement and scale are now
  per-page data attributes.
- [x] **Metrics on the homepage are properties of the code, not usage.** 115 tests,
  10/10 refused mutations, 5 integrity checks, 0 third-party requests — each one
  checkable by running the repo.

- [x] **Password field was unstyled.** The CSS enumerated which input types to
  style and omitted `input[type="password"]`, so it fell back to the browser's
  native white box in the middle of a dark form. Both stylesheets now style
  inputs by *exclusion*, so a type nobody thought of is covered by default.
- [x] **`color-scheme` followed the OS, not the app.** `color-scheme: dark light`
  means "browser, pick" — so native widget chrome (date-picker icons, select
  popups, scrollbars) rendered light while the page rendered dark. Each theme now
  declares exactly one scheme.
- [x] **Logo was a broken-looking corner bracket.** The mark was a square with two
  transparent borders, intended as stylised and reading as an error. Replaced with
  the orbit mark, generated once in `scripts/logo_mark.py` and shared by the
  dashboard CSS, the site CSS, all three favicons and the social card.
- [x] **The sign-in page had no favicon at all** — blank tab icon.
- [x] **Chain visualisation rendered as a thin ribbon.** Spacing was a fixed
  constant tuned for a square canvas; the panel is wide and short. It now derives
  spacing and block size from the visible world units at the camera plane.
- [x] **Incident view claimed "no conclusion was recorded"** for incidents that
  plainly had one. The conclusion was only recognised via `agent_reasoning`
  evidence, which only the HolmesGPT adapter emits. Now falls back to the last
  investigation step.
- [x] **"Awaiting approval: 4" contradicted "Nothing is waiting on a human"** on
  the next page. The stat counted every `request_approval` row whose status read
  `pending` — but in an append-only ledger that status is never updated; the
  decision is a *later* record. Now a `NOT EXISTS` against approve/reject.

- [x] **Dashboard had no authentication at all.** Anyone reaching the port could approve
  and execute production changes. Now OIDC/dev/disabled modes, refuses to start
  unauthenticated, role-gated approvals, identity from the session rather than the
  request body. 39 auth tests.
- [x] **Dashboard loaded fonts and Three.js from CDNs.** Leaked which audit pages were
  viewed to Google and Cloudflare, and broke in air-gapped deployments. Both self-hosted;
  0 external references.
- [x] **`BIGSERIAL` caused false tamper alarms.** Postgres sequences advance on rollback,
  so a schema-rejected record left a permanent gap the verifier reported as a deletion.
  `seq` is now assigned under the chain lock — gapless.
- [x] **Production policy was looser than staging.** `rule:human_approved_production_change`
  had no `change_type` condition, so an agent could execute an arbitrary production code
  change on one approval. Change kinds now enumerated.
- [x] **No policy rule covered the `approve` action.** Recording a decision was
  default-denied, so the approval path could not write at all. Added
  `rule:human_approval_decision`.
- [x] **OG card carried a fabricated metric.** "2481/2481 records verified" on an image
  seen standalone in feeds, without the page's "illustrative" caveat. Replaced with
  capability claims.
