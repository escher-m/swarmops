"""The compliance dashboard.

The product surface is this view, not the investigation. Someone who has never
seen the system should be able to open it and answer, without narration: what
broke, what the agent looked at, what it proposed, who approved it, when it
executed, and whether it worked -- with the tamper-evidence result visible on
the page rather than buried in a CLI.

Server-rendered on purpose. An audit tool that cannot render without a working
JavaScript bundle is a liability, so every page is complete HTML and the
client-side work is enhancement only.
"""

from __future__ import annotations

import io
import json
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from dashboard.auth import SESSION_MAX_AGE as SESSION_MAX_AGE_SECONDS
from dashboard.auth import (
    ANONYMOUS, SESSION_COOKIE, AuthConfig, SessionManager, User,
    authenticate_dev, is_public_path, load_config, may_approve,
    require_approver, user_from_oidc_claims,
)

from approval.executor import Executor
from approval.gate import ApprovalGate
from ledger.evidence_store import default_store
from ledger.store import LedgerStore
from scripts.verify_chain import verify

ROOT = Path(__file__).resolve().parent.parent

app = FastAPI(title="SwarmOps Audit Ledger", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=ROOT / "dashboard" / "static"), name="static")
templates = Jinja2Templates(directory=str(ROOT / "dashboard" / "templates"))

# Configuration is read at import time so that a misconfigured deployment fails
# to start rather than serving an unauthenticated approval surface.
AUTH: AuthConfig = load_config()
app.state.auth_config = AUTH
sessions = SessionManager(AUTH)

# Starlette's session cookie holds only the transient OAuth state and nonce.
# The authenticated identity lives in our own signed cookie with its own salt
# and lifetime, so the two concerns cannot be confused.
app.add_middleware(SessionMiddleware, secret_key=AUTH.secret_key,
                   session_cookie="swarmops_oauth", max_age=600, same_site="lax")

oauth = None
if AUTH.mode == "oidc":
    from authlib.integrations.starlette_client import OAuth

    oauth = OAuth()
    oauth.register(
        name="idp",
        server_metadata_url=AUTH.discovery_url,
        client_id=AUTH.client_id,
        client_secret=AUTH.client_secret,
        client_kwargs={"scope": "openid profile email"},
    )


@app.middleware("http")
async def attach_user(request: Request, call_next):
    """Resolve the session into request.state.user, or bounce to login.

    Applied as middleware rather than per-route dependencies so that adding a
    new route cannot accidentally leave it unauthenticated -- the failure mode
    of a dependency-based approach is silent, and this is the surface where a
    silent failure means an anonymous approval.
    """
    if not AUTH.enabled:
        request.state.user = ANONYMOUS
        return await call_next(request)

    token = request.cookies.get(SESSION_COOKIE)
    user = sessions.read(token) if token else None
    request.state.user = user

    if user is None and not is_public_path(request.url.path):
        if request.headers.get("accept", "").startswith("application/json"):
            return JSONResponse({"error": "not authenticated"}, status_code=401)
        nxt = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        return RedirectResponse(f"/auth/login?next={quote(nxt, safe='')}", status_code=303)

    return await call_next(request)

store = LedgerStore()
gate = ApprovalGate()
evidence_store = default_store()

ACTION_ORDER = [
    "investigate", "propose_fix", "request_approval",
    "approve", "reject", "execute", "rollback",
]

# ---------------------------------------------------------------- verification
# Walking the whole chain on every request would make the dashboard slower the
# more trustworthy it gets, which is the wrong incentive. The result is cached
# briefly and the page states when it was computed, so the badge is never a
# stale claim presented as current.

_VERIFY_CACHE: dict[str, Any] = {"at": 0.0, "report": None}
_VERIFY_TTL_SECONDS = 20.0


def verification(force: bool = False) -> dict[str, Any]:
    now = time.time()
    if force or _VERIFY_CACHE["report"] is None or now - _VERIFY_CACHE["at"] > _VERIFY_TTL_SECONDS:
        report = verify()
        _VERIFY_CACHE.update({
            "at": now,
            "report": report.as_dict(),
            "checked_at": datetime.now(timezone.utc),
        })
    data = dict(_VERIFY_CACHE["report"])
    data["checked_at"] = _VERIFY_CACHE["checked_at"]
    data["age_seconds"] = int(now - _VERIFY_CACHE["at"])
    return data


# ---------------------------------------------------------------- jinja filters


def _fmt_time(value: Any, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    if value is None:
        return "—"
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
    return value.astimezone(timezone.utc).strftime(fmt) + " UTC"


def _short(value: str | None, n: int = 12) -> str:
    return (value or "")[:n]


def _relative(value: Any) -> str:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
    if not isinstance(value, datetime):
        return "—"
    delta = datetime.now(timezone.utc) - value.astimezone(timezone.utc)
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"


def _duration(ms: Any) -> str:
    # Jinja hands an Undefined (not None) for a missing key, and Undefined is
    # not None, so a `is not none` guard in a template does not protect this.
    # Being tolerant here is cheaper than auditing every call site.
    try:
        ms = int(ms)
    except (TypeError, ValueError):
        return "—"
    if ms < 1000:
        return f"{ms}ms"
    if ms < 60000:
        return f"{ms / 1000:.1f}s"
    return f"{ms // 60000}m {(ms % 60000) // 1000}s"


def _evidence_text(item: dict) -> str:
    ref = item.get("snapshot_ref_or_inline") or {}
    if ref.get("kind") == "inline":
        return ref.get("content", "")
    return ref.get("preview", "(blob — open to download)")


templates.env.filters["fmt_time"] = _fmt_time
templates.env.filters["short"] = _short
templates.env.filters["relative"] = _relative
templates.env.filters["duration"] = _duration
templates.env.filters["evidence_text"] = _evidence_text
templates.env.globals["ACTION_ORDER"] = ACTION_ORDER


def base_context(request: Request) -> dict[str, Any]:
    user = getattr(request.state, "user", None) or ANONYMOUS
    return {
        "request": request,
        "verification": verification(),
        "pending_count": len(gate.pending()),
        "now": datetime.now(timezone.utc),
        "user": user,
        "auth_mode": AUTH.mode,
        "may_approve": may_approve(AUTH, user),
        "approver_roles": AUTH.approver_roles,
    }


# ----------------------------------------------------------------------- routes


@app.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    start: str | None = None,
    end: str | None = None,
    actor: str | None = None,
    action_type: str | None = None,
    environment: str | None = None,
    outcome: str | None = None,
    incident_id: str | None = None,
    search: str | None = None,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    filters = {
        "start": start or None, "end": end or None, "actor": actor or None,
        "action_type": action_type or None, "environment": environment or None,
        "outcome": outcome or None, "incident_id": incident_id or None,
        "search": search or None,
    }
    records = store.query(**filters, limit=limit, offset=offset)
    ctx = base_context(request)
    ctx.update({
        "records": records,
        "filters": filters,
        "limit": limit,
        "offset": offset,
        "has_more": len(records) == limit,
        "stats": store.stats(),
        "options": {
            "actor": store.distinct("actor_agent_name"),
            "action_type": store.distinct("action_type"),
            "environment": store.distinct("environment"),
            "outcome": store.distinct("outcome_status"),
        },
        "query_string": request.url.query,
    })
    return templates.TemplateResponse(request, "index.html", ctx)


@app.get("/incidents", response_class=HTMLResponse)
def incidents(request: Request):
    with store.connect() as conn:
        rows = conn.execute(
            """
            SELECT incident_id,
                   min(event_time)                                    AS started,
                   max(event_time)                                    AS ended,
                   count(*)                                           AS records,
                   max(environment)                                   AS environment,
                   max(resource_id)                                   AS resource_id,
                   bool_or(action_type = 'execute')                   AS executed,
                   bool_or(action_type = 'approve')                   AS approved,
                   bool_or(action_type = 'reject')                    AS rejected,
                   bool_or(action_type = 'request_approval'
                           AND outcome_status = 'pending')            AS has_request,
                   bool_or(action_type = 'execute'
                           AND outcome_status = 'success')            AS execute_ok,
                   bool_or(outcome_status = 'failure')                AS any_failure
            FROM agent_action_records
            GROUP BY incident_id
            ORDER BY min(event_time) DESC
            """
        ).fetchall()
    ctx = base_context(request)
    ctx["incidents"] = rows
    return templates.TemplateResponse(request, "incidents.html", ctx)


@app.get("/incident/{incident_id}", response_class=HTMLResponse)
def incident(request: Request, incident_id: str):
    records = store.incident(incident_id)
    if not records:
        raise HTTPException(404, f"no records for incident {incident_id}")
    ctx = base_context(request)
    ctx.update({
        "incident_id": incident_id,
        "records": records,
        "summary": _incident_summary(records),
    })
    return templates.TemplateResponse(request, "incident.html", ctx)


def _incident_summary(records: list[dict]) -> dict[str, Any]:
    """Reduce a chain to the six answers the acceptance test demands."""
    by_type: dict[str, list[dict]] = {}
    for r in records:
        by_type.setdefault(r["action_type"], []).append(r)

    # The conclusion is the investigate record carrying the agent's reasoning.
    # Only the HolmesGPT adapter emits agent_reasoning evidence, so anything
    # else writing to the ledger would leave this None and the page would claim
    # no conclusion was recorded for an incident that plainly has one. Fall back
    # to the last investigate record, which is the closest thing to a conclusion
    # any producer can be relied on to write.
    investigations = by_type.get("investigate", [])
    conclusion = next(
        (r for r in reversed(investigations)
         if any(e.get("evidence_type") == "agent_reasoning" for e in r.get("evidence", []))),
        investigations[-1] if investigations else None,
    )
    proposal = (by_type.get("propose_fix") or [None])[-1]
    approval = (by_type.get("approve") or [None])[-1]
    rejection = (by_type.get("reject") or [None])[-1]
    execution = (by_type.get("execute") or [None])[-1]

    evidence_total = sum(len(r.get("evidence", [])) for r in records)
    return {
        "what_broke": (conclusion or records[0])["outcome"]["details"],
        "steps": len(by_type.get("investigate", [])),
        "evidence_total": evidence_total,
        "conclusion": conclusion,
        "proposal": proposal,
        "approval": approval,
        "rejection": rejection,
        "execution": execution,
        "environment": records[0]["target"]["environment"],
        "resource": records[0]["target"]["resource_id"],
        "started": records[0]["timestamp"],
        "ended": records[-1]["timestamp"],
        "status": (
            "executed" if execution and execution["outcome"]["status"] == "success"
            else "execution_failed" if execution
            else "rejected" if rejection
            else "awaiting_approval" if by_type.get("request_approval")
            else "investigated"
        ),
    }


@app.get("/record/{record_id}", response_class=HTMLResponse)
def record_detail(request: Request, record_id: str):
    rec = store.get(record_id)
    if rec is None:
        raise HTTPException(404, f"no such record {record_id}")
    ctx = base_context(request)
    ctx["record"] = rec
    ctx["raw"] = json.dumps(rec, indent=2)
    return templates.TemplateResponse(request, "record.html", ctx)


@app.get("/evidence/{digest}")
def evidence_blob(digest: str, download: bool = False):
    try:
        data = evidence_store.get(digest)
    except FileNotFoundError:
        raise HTTPException(404, "evidence blob not found")
    except Exception as exc:
        # An integrity failure is a finding, not a 500. Say so plainly.
        raise HTTPException(409, f"evidence integrity check failed: {exc}")
    disposition = "attachment" if download else "inline"
    return Response(
        content=data,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'{disposition}; filename="{digest[:16]}.txt"'},
    )


@app.get("/approvals", response_class=HTMLResponse)
def approvals(request: Request):
    ctx = base_context(request)
    ctx["pending"] = gate.pending()
    return templates.TemplateResponse(request, "approvals.html", ctx)


@app.post("/approvals/{request_id}/decide")
def decide(
    request_id: str,
    decision: str = Form(...),
    note: str = Form(...),
    override: str | None = Form(None),
    execute: str | None = Form(None),
    user: User = Depends(require_approver),
):
    """Record a decision, made by the authenticated human, not by the server.

    require_approver is what makes the approver field trustworthy: the identity
    written into the ledger comes from the session the IdP issued, and is never
    accepted from the request body.
    """
    approve = decision == "approve"
    try:
        record_id = gate.decide(
            request_record_id=request_id,
            approve=approve,
            note=note,
            approver_id=user.approver_id,
            display_name=user.display_name,
            channel="web_dashboard",
            override=bool(override),
        )
    except (ValueError, LookupError) as exc:
        raise HTTPException(400, str(exc))

    if approve and execute:
        try:
            Executor().execute(record_id)
        except Exception as exc:
            # The decision is already recorded; an execution failure must not
            # roll it back (it cannot) nor be swallowed.
            return RedirectResponse(f"/record/{record_id}?exec_error={exc}", status_code=303)
    return RedirectResponse(f"/record/{record_id}", status_code=303)


@app.get("/verify", response_class=HTMLResponse)
def verify_page(request: Request, force: bool = False):
    ctx = base_context(request)
    if force:
        ctx["verification"] = verification(force=True)
    with store.connect() as conn:
        ctx["chain"] = conn.execute(
            "SELECT seq, record_id::text, action_type, record_hash, prev_record_hash, "
            "event_time, incident_id FROM agent_action_records ORDER BY seq ASC"
        ).fetchall()
    return templates.TemplateResponse(request, "verify.html", ctx)


@app.get("/api/verify")
def api_verify(force: bool = False):
    return JSONResponse(jsonable(verification(force=force)))


@app.get("/api/search")
def api_search(limit: int = Query(60, ge=1, le=400)):
    """Contents for the command palette.

    Behind the auth middleware like every other non-public route, so an
    unauthenticated caller cannot enumerate incidents through it. Returns only
    identifiers and summaries -- never evidence, approver identities or policy
    detail, none of which belong in a search index that sits in browser memory.
    """
    with store.connect() as conn:
        incidents = conn.execute(
            "SELECT incident_id, max(resource_id) AS resource_id, "
            "       max(environment) AS environment, max(event_time) AS last_seen "
            "FROM agent_action_records GROUP BY incident_id "
            "ORDER BY max(event_time) DESC LIMIT %s",
            (limit,),
        ).fetchall()
        records = conn.execute(
            "SELECT record_id::text, action_type, summary, event_time "
            "FROM agent_action_records ORDER BY seq DESC LIMIT %s",
            (limit,),
        ).fetchall()
    return JSONResponse(jsonable({"incidents": incidents, "records": records}))


@app.get("/api/chain")
def api_chain(limit: int = Query(400, ge=1, le=5000)):
    """Chain shape for the visualisation. Hashes only -- no payloads."""
    with store.connect() as conn:
        rows = conn.execute(
            "SELECT seq, action_type, outcome_status, record_hash, incident_id "
            "FROM agent_action_records ORDER BY seq DESC LIMIT %s", (limit,)
        ).fetchall()
    return JSONResponse(list(reversed(rows)))


def jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [jsonable(v) for v in value]
    return value


# ------------------------------------------------------------------- exporting


@app.get("/export.csv")
def export_csv(
    start: str | None = None, end: str | None = None, actor: str | None = None,
    action_type: str | None = None, environment: str | None = None,
    outcome: str | None = None, incident_id: str | None = None, search: str | None = None,
):
    from dashboard.export import records_csv

    records = store.query(
        start=start, end=end, actor=actor, action_type=action_type,
        environment=environment, outcome=outcome, incident_id=incident_id,
        search=search, limit=10000,
    )
    buf = io.StringIO()
    records_csv(records, buf)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return StreamingResponse(
        io.BytesIO(buf.getvalue().encode("utf-8")),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="audit-ledger-{stamp}.csv"'},
    )


@app.get("/export.pdf")
def export_pdf(
    start: str | None = None, end: str | None = None, actor: str | None = None,
    action_type: str | None = None, environment: str | None = None,
    outcome: str | None = None, incident_id: str | None = None, search: str | None = None,
):
    from dashboard.export import audit_pdf

    filters = {
        "start": start, "end": end, "actor": actor, "action_type": action_type,
        "environment": environment, "outcome": outcome,
        "incident_id": incident_id, "search": search,
    }
    records = store.query(**filters, limit=5000)
    pdf = audit_pdf(
        records=records,
        filters={k: v for k, v in filters.items() if v},
        verification=verification(force=True),
        incident_chain=store.incident(incident_id) if incident_id else None,
    )
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    name = f"audit-report-{incident_id or 'ledger'}-{stamp}.pdf"
    return Response(
        content=pdf, media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


# ---------------------------------------------------------------------- auth


@app.get("/auth/login", response_class=HTMLResponse)
async def login(request: Request, next: str = "/", error: str | None = None):
    if not AUTH.enabled:
        return RedirectResponse(next or "/", status_code=303)
    if AUTH.mode == "oidc":
        request.session["next"] = next or "/"
        return await oauth.idp.authorize_redirect(request, AUTH.redirect_url)
    return templates.TemplateResponse(
        request, "login.html",
        {"request": request, "next": next, "error": error,
         "auth_mode": AUTH.mode, "verification": verification(), "pending_count": 0,
         "user": ANONYMOUS, "may_approve": False, "approver_roles": AUTH.approver_roles},
    )


@app.post("/auth/login")
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form("/"),
):
    if AUTH.mode != "dev":
        raise HTTPException(404)
    user = authenticate_dev(AUTH, username, password)
    if user is None:
        # One message for both cases: naming which was wrong enumerates users.
        return RedirectResponse(
            f"/auth/login?next={quote(next, safe='')}&error=invalid", status_code=303)
    response = RedirectResponse(next or "/", status_code=303)
    _set_session(response, user, request)
    return response


@app.get("/auth/callback")
async def auth_callback(request: Request):
    if AUTH.mode != "oidc":
        raise HTTPException(404)
    try:
        token = await oauth.idp.authorize_access_token(request)
    except Exception as exc:
        raise HTTPException(401, f"identity provider rejected the sign-in: {exc}")
    claims = token.get("userinfo") or {}
    if not claims:
        claims = await oauth.idp.userinfo(token=token)
    user = user_from_oidc_claims(AUTH, dict(claims))
    nxt = request.session.pop("next", "/")
    response = RedirectResponse(nxt or "/", status_code=303)
    _set_session(response, user, request)
    return response


@app.get("/auth/logout")
def logout(request: Request):
    response = RedirectResponse("/auth/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE)
    request.session.clear()
    return response


def _set_session(response, user: User, request: Request) -> None:
    # secure=True whenever the request arrived over TLS. Forcing it on always
    # would silently break a plain-HTTP laptop demo; leaving it off always would
    # leak the session on a real deployment.
    response.set_cookie(
        SESSION_COOKIE, sessions.issue(user),
        max_age=AUTH.session_max_age, httponly=True, samesite="lax",
        secure=request.url.scheme == "https",
    )


@app.get("/healthz")
def healthz():
    return {"status": "ok", "records": store.count()}
