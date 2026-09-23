"""A small checkout service with reproducible failure modes.

This exists to give the agent something real to investigate. Everything the
agent sees -- log lines, latency, HTTP status, database plans, git history --
comes from this service genuinely misbehaving, not from a canned scenario. That
distinction matters: an investigation that only works against prerecorded
symptoms proves nothing about the adapter.

Four failure modes, each with a genuine mechanism and a genuine fix:

  bad_deploy        v1.4.2 makes shipping_cost nullable but never updates the
                    order-total arithmetic, so /checkout raises TypeError on
                    carts created after the change. Fix: roll back to v1.4.1.
  pool_exhaustion   The connection pool is squeezed to one connection while
                    /orders holds it for seconds, so concurrent requests fail
                    on pool checkout. Fix: restore the pool size.
  slow_query        /reports/revenue drops its supporting index and issues one
                    query per customer. Fix: restore the index and the single
                    aggregate query.
  leaked_secret     A recent commit hardcodes a live-looking credential.
                    Fix: rotate it and purge the value.
"""

from __future__ import annotations

import os
import random
import threading
import time
import traceback
from collections import deque
from decimal import Decimal
from typing import Any

from flask import Flask, g, jsonify, request
from psycopg_pool import ConnectionPool

from . import config
from .logging_setup import configure

log = configure()
app = Flask(__name__)

ROOT = config.APP_DIR.parent
SEED_DSN = os.environ.get(
    "SEEDAPP_DSN",
    f"host={ROOT / '.pgdata' / 'sockets'} port=5433 user=postgres dbname=seedapp",
)

# --------------------------------------------------------------------- metrics

_metrics_lock = threading.Lock()
_latencies: dict[str, deque[float]] = {}
_counters: dict[tuple[str, int], int] = {}


def record_metric(endpoint: str, status: int, duration_s: float) -> None:
    with _metrics_lock:
        _latencies.setdefault(endpoint, deque(maxlen=500)).append(duration_s)
        key = (endpoint, status)
        _counters[key] = _counters.get(key, 0) + 1


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(round((p / 100.0) * (len(ordered) - 1))))
    return ordered[idx]


# ------------------------------------------------------------------- db access

_pool: ConnectionPool | None = None
_pool_size: int | None = None
_pool_lock = threading.Lock()


def get_pool() -> ConnectionPool:
    """Return the pool, rebuilding it when the configured size changes.

    Rebuilding on a size change is what makes pool_exhaustion injectable and
    fixable at runtime without restarting the service -- which is the whole
    point, since the agent must be able to observe the fix take effect.
    """
    global _pool, _pool_size
    want = config.load()["db_pool_max"]
    with _pool_lock:
        if _pool is not None and _pool_size == want:
            return _pool
        if _pool is not None:
            _pool.close()
        _pool = ConnectionPool(
            SEED_DSN,
            min_size=1,
            max_size=want,
            timeout=2.0,          # fail fast rather than queue forever
            max_lifetime=300,
            open=True,
        )
        _pool_size = want
        log.info("database pool (re)initialised max_size=%d timeout=2.0s", want)
        return _pool


# ------------------------------------------------------------------ middleware


@app.before_request
def _start_timer() -> None:
    g._t0 = time.perf_counter()
    g._request_id = f"req-{random.randint(0, 99999):05d}"


@app.after_request
def _log_request(response):
    duration = time.perf_counter() - getattr(g, "_t0", time.perf_counter())
    endpoint = request.path
    record_metric(endpoint, response.status_code, duration)
    level = log.warning if response.status_code >= 500 else log.info
    level(
        "%s %s -> %d in %.1fms request_id=%s",
        request.method, endpoint, response.status_code, duration * 1000,
        getattr(g, "_request_id", "-"),
    )
    if duration > 1.0:
        log.warning(
            "SLOW REQUEST %s %s took %.2fs (threshold 1.00s) request_id=%s",
            request.method, endpoint, duration, getattr(g, "_request_id", "-"),
        )
    return response


# -------------------------------------------------------------------- handlers


@app.get("/health")
def health():
    state = config.load()
    try:
        with get_pool().connection(timeout=1.0) as conn:
            conn.execute("SELECT 1")
        db_ok = True
    except Exception as exc:
        log.error("health check could not reach the database: %s", exc)
        db_ok = False
    status = 200 if db_ok else 503
    return jsonify({
        "status": "ok" if db_ok else "degraded",
        "version": state["version"],
        "database": "up" if db_ok else "down",
    }), status


@app.post("/checkout")
def checkout():
    """Create an order. Under v1.4.2 this raises on carts with null shipping."""
    state = config.load()
    payload = request.get_json(silent=True) or {}
    cart_id = payload.get("cart_id", f"cart-{random.randint(1000, 9999)}")
    digital_only = bool(payload.get("digital_only", True))

    subtotal = Decimal(str(payload.get("subtotal", "49.99")))

    # The behavioural difference between the two releases, in one place.
    # v1.4.2 made the column nullable (see repo commit 4f2c9a1) without
    # updating the arithmetic below.
    if state["version"] == "v1.4.2" and digital_only:
        shipping_cost: Decimal | None = None
    else:
        shipping_cost = Decimal(str(payload.get("shipping_cost", "0.00")))

    try:
        total = subtotal + shipping_cost  # TypeError when shipping_cost is None
    except TypeError:
        log.error(
            "Unhandled exception serving POST /checkout request_id=%s cart_id=%s\n%s",
            g._request_id, cart_id,
            "".join(
                "    " + line
                for line in traceback.format_exc().splitlines(keepends=True)
            ),
        )
        return jsonify({
            "error": "internal server error",
            "request_id": g._request_id,
        }), 500

    try:
        with get_pool().connection() as conn:
            row = conn.execute(
                "INSERT INTO orders (cart_id, subtotal, shipping_cost, total, customer_email) "
                "VALUES (%s, %s, %s, %s, %s) RETURNING order_id",
                (cart_id, subtotal, shipping_cost, total,
                 f"customer{random.randint(1, 500)}@example.com"),
            ).fetchone()
            conn.commit()
    except Exception as exc:
        log.error(
            "POST /checkout failed at persistence request_id=%s: %s: %s",
            g._request_id, type(exc).__name__, exc,
        )
        return jsonify({"error": "internal server error", "request_id": g._request_id}), 500

    return jsonify({
        "order_id": row[0], "cart_id": cart_id,
        "total": str(total), "version": state["version"],
    }), 201


@app.get("/orders")
def orders():
    """List recent orders. Holds its connection long enough to starve a
    squeezed pool, which is how pool_exhaustion becomes observable."""
    state = config.load()
    hold_s = 2.5 if "pool_exhaustion" in state["faults"] else 0.0
    try:
        with get_pool().connection() as conn:
            rows = conn.execute(
                "SELECT order_id, cart_id, total, created_at FROM orders "
                "ORDER BY order_id DESC LIMIT 20"
            ).fetchall()
            if hold_s:
                # Stands in for a slow downstream call made while holding the
                # connection -- the actual anti-pattern that exhausts pools.
                time.sleep(hold_s)
    except Exception as exc:
        log.error(
            "GET /orders could not obtain a database connection request_id=%s: %s: %s "
            "(pool max_size=%d) -- connection pool exhausted",
            g._request_id, type(exc).__name__, exc, state["db_pool_max"],
        )
        return jsonify({
            "error": "service unavailable",
            "detail": "database connection pool exhausted",
            "request_id": g._request_id,
        }), 503

    return jsonify({"orders": [
        {"order_id": r[0], "cart_id": r[1], "total": str(r[2]), "created_at": r[3].isoformat()}
        for r in rows
    ]}), 200


@app.get("/reports/revenue")
def revenue_report():
    """Revenue by customer. Slow when the index is gone and N+1 is enabled."""
    state = config.load()
    t0 = time.perf_counter()
    try:
        with get_pool().connection() as conn:
            if state["revenue_n_plus_one"]:
                emails = conn.execute(
                    "SELECT DISTINCT customer_email FROM orders_archive LIMIT 200"
                ).fetchall()
                total = Decimal("0")
                for (email,) in emails:
                    # One query per customer, on a lower() predicate that a
                    # plain b-tree index cannot serve. Both halves of the bug.
                    row = conn.execute(
                        "SELECT COALESCE(sum(total), 0) FROM orders_archive "
                        "WHERE lower(customer_email) = lower(%s)",
                        (email,),
                    ).fetchone()
                    total += row[0]
                customers = len(emails)
            else:
                row = conn.execute(
                    "SELECT count(DISTINCT customer_email), COALESCE(sum(total), 0) "
                    "FROM orders_archive"
                ).fetchone()
                customers, total = row[0], row[1]
    except Exception as exc:
        log.error("GET /reports/revenue failed request_id=%s: %s", g._request_id, exc)
        return jsonify({"error": "internal server error"}), 500

    elapsed = time.perf_counter() - t0
    if elapsed > 1.0:
        log.warning(
            "slow query detected in /reports/revenue: %.2fs across %d customer lookups "
            "(index on lower(customer_email): %s) request_id=%s",
            elapsed, customers, "present" if state["revenue_index_enabled"] else "MISSING",
            g._request_id,
        )
    return jsonify({
        "customers": customers, "revenue": str(total),
        "query_seconds": round(elapsed, 3),
    }), 200


@app.get("/metrics")
def metrics():
    """Prometheus-style exposition, kept deliberately small and readable."""
    with _metrics_lock:
        lines = [
            "# HELP checkout_requests_total Total HTTP requests by endpoint and status.",
            "# TYPE checkout_requests_total counter",
        ]
        for (endpoint, status), count in sorted(_counters.items()):
            lines.append(
                f'checkout_requests_total{{endpoint="{endpoint}",status="{status}"}} {count}'
            )
        lines += [
            "# HELP checkout_request_duration_seconds Request latency percentiles.",
            "# TYPE checkout_request_duration_seconds summary",
        ]
        for endpoint, samples in sorted(_latencies.items()):
            values = list(samples)
            for q in (50, 95, 99):
                lines.append(
                    f'checkout_request_duration_seconds{{endpoint="{endpoint}",'
                    f'quantile="0.{q:02d}"}} {percentile(values, q):.4f}'
                )
        errors = sum(c for (_, s), c in _counters.items() if s >= 500)
        total = sum(_counters.values()) or 1
        lines += [
            "# HELP checkout_error_ratio Fraction of requests returning 5xx.",
            "# TYPE checkout_error_ratio gauge",
            f"checkout_error_ratio {errors / total:.4f}",
        ]
    return "\n".join(lines) + "\n", 200, {"Content-Type": "text/plain; version=0.0.4"}


# ----------------------------------------------------------------- admin plane
# Separated under /admin because this is the fault-injection and remediation
# surface, not part of the service the agent is investigating.


@app.get("/admin/state")
def admin_state():
    return jsonify(config.load()), 200


@app.post("/admin/reset")
def admin_reset():
    state = config.reset()
    log.info("service reset to healthy baseline version=%s", state["version"])
    return jsonify(state), 200


@app.post("/admin/inject")
def admin_inject():
    """Inject one named failure mode."""
    mode = (request.get_json(silent=True) or {}).get("mode")
    state = config.load()
    faults = set(state["faults"])

    if mode == "bad_deploy":
        state["version"] = "v1.4.2"
        log.warning("DEPLOY checkout-api v1.4.1 -> v1.4.2 rollout complete (3/3 replicas)")
    elif mode == "pool_exhaustion":
        state["db_pool_max"] = 1
        log.warning("config change applied: db_pool_max 10 -> 1")
    elif mode == "slow_query":
        state["revenue_index_enabled"] = False
        state["revenue_n_plus_one"] = True
        _drop_revenue_index()
        log.warning("config change applied: revenue report switched to per-customer lookups")
    elif mode == "leaked_secret":
        state["active_credential_id"] = "AKIAIOSFODNN7LEAKED1"
        log.warning("credential rotation skipped; continuing with existing key")
    else:
        return jsonify({"error": f"unknown mode {mode!r}"}), 400

    faults.add(mode)
    state["faults"] = sorted(faults)
    config.save(state)
    log.error("FAULT INJECTED mode=%s state=%s", mode, {k: state[k] for k in
              ("version", "db_pool_max", "revenue_index_enabled", "active_credential_id")})
    return jsonify(state), 200


@app.post("/admin/apply_fix")
def admin_apply_fix():
    """Apply one named remediation. Called only by the executor, only after a
    recorded human approval -- the service itself enforces nothing here, which
    is precisely why the approval gate lives upstream in the ledger."""
    body = request.get_json(silent=True) or {}
    action = body.get("action")
    state = config.load()
    faults = set(state["faults"])
    detail: str

    if action == "rollback_deploy":
        target = body.get("target_version", "v1.4.1")
        state["version"] = target
        faults.discard("bad_deploy")
        detail = f"deployment.apps/checkout-api rolled back to {target}"
        log.info("DEPLOY rollback to %s complete (3/3 replicas)", target)
    elif action == "increase_pool_size":
        target = int(body.get("db_pool_max", 10))
        state["db_pool_max"] = target
        faults.discard("pool_exhaustion")
        detail = f"db_pool_max raised to {target}; pool rebuilt"
        log.info("config change applied: db_pool_max -> %d", target)
    elif action == "restore_revenue_index":
        state["revenue_index_enabled"] = True
        state["revenue_n_plus_one"] = False
        _create_revenue_index()
        faults.discard("slow_query")
        detail = "functional index on lower(customer_email) created; single-aggregate query restored"
        log.info("index restored on orders_archive(lower(customer_email))")
    elif action == "rotate_credential":
        new_id = body.get("new_credential_id", "AKIA2F7QXVEXAMPLE002")
        state["active_credential_id"] = new_id
        faults.discard("leaked_secret")
        detail = f"credential rotated to {new_id}; previous key revoked"
        log.info("credential rotated -> %s", new_id)
    else:
        return jsonify({"error": f"unknown action {action!r}"}), 400

    state["faults"] = sorted(faults)
    config.save(state)
    get_pool()  # force the pool to pick up any size change immediately
    return jsonify({"applied": action, "detail": detail, "state": state}), 200


def _drop_revenue_index() -> None:
    try:
        with get_pool().connection() as conn:
            conn.execute("DROP INDEX IF EXISTS orders_archive_email_lower_idx")
            conn.commit()
    except Exception as exc:
        log.error("could not drop revenue index: %s", exc)


def _create_revenue_index() -> None:
    try:
        with get_pool().connection() as conn:
            conn.execute(
                "CREATE INDEX IF NOT EXISTS orders_archive_email_lower_idx "
                "ON orders_archive (lower(customer_email))"
            )
            conn.commit()
    except Exception as exc:
        log.error("could not create revenue index: %s", exc)


if __name__ == "__main__":
    log.info("checkout-api starting version=%s", config.load()["version"])
    app.run(host="127.0.0.1", port=int(os.environ.get("SEEDAPP_PORT", 8081)), threaded=True)
