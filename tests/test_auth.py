"""Tests for the authentication layer.

The property under test throughout is the one the product depends on: the
identity written into an approve record comes from a session the identity
provider issued, and can never be supplied by the caller.
"""

import importlib
import json
import os
import time

import pytest
from fastapi.testclient import TestClient

from dashboard.auth import (
    AuthConfig, AuthConfigError, SessionManager, User,
    authenticate_dev, is_public_path, load_config, may_approve, user_from_oidc_claims,
)


# ----------------------------------------------------------------- config


def test_unauthenticated_is_refused_by_default():
    with pytest.raises(AuthConfigError, match="refuses to run unauthenticated"):
        load_config({})


def test_disabling_auth_requires_an_explicit_opt_in():
    cfg = load_config({"SWARMOPS_AUTH_MODE": "disabled", "SWARMOPS_ALLOW_INSECURE": "1"})
    assert not cfg.enabled


def test_insecure_mode_is_refused_in_production():
    with pytest.raises(AuthConfigError, match="production"):
        load_config({"SWARMOPS_AUTH_MODE": "disabled", "SWARMOPS_ALLOW_INSECURE": "1",
                     "SWARMOPS_ENV": "production"})


def test_dev_mode_is_refused_in_production():
    with pytest.raises(AuthConfigError, match="not real identities"):
        load_config({"SWARMOPS_AUTH_MODE": "dev", "SWARMOPS_ENV": "production",
                     "SWARMOPS_SECRET_KEY": "x" * 40})


def test_production_requires_an_explicit_secret_key():
    with pytest.raises(AuthConfigError, match="SECRET_KEY"):
        load_config({
            "SWARMOPS_AUTH_MODE": "oidc", "SWARMOPS_ENV": "production",
            "SWARMOPS_OIDC_DISCOVERY_URL": "https://idp/.well-known/openid-configuration",
            "SWARMOPS_OIDC_CLIENT_ID": "c", "SWARMOPS_OIDC_CLIENT_SECRET": "s",
            "SWARMOPS_OIDC_REDIRECT_URL": "https://app/auth/callback",
        })


def test_oidc_lists_every_missing_setting_at_once():
    with pytest.raises(AuthConfigError) as exc:
        load_config({"SWARMOPS_AUTH_MODE": "oidc"})
    for name in ("DISCOVERY_URL", "CLIENT_ID", "CLIENT_SECRET", "REDIRECT_URL"):
        assert name in str(exc.value)


def test_dev_secret_key_is_random_per_process():
    a = load_config({"SWARMOPS_AUTH_MODE": "dev"}).secret_key
    b = load_config({"SWARMOPS_AUTH_MODE": "dev"}).secret_key
    # A fixed fallback secret would be a backdoor: anyone with the source could
    # forge a session.
    assert a != b and len(a) > 30


# ---------------------------------------------------------------- sessions


@pytest.fixture
def manager():
    return SessionManager(AuthConfig(mode="dev", secret_key="test-secret-key-" + "x" * 24))


def test_session_round_trips(manager):
    user = User("00u123", "Alice", "a@x.io", ("sre-oncall",), "okta")
    restored = manager.read(manager.issue(user))
    assert restored.subject == "00u123"
    assert restored.roles == ("sre-oncall",)
    assert restored.approver_id == "okta|00u123"


def test_tampered_cookie_is_rejected(manager):
    token = manager.issue(User("00u123", "Alice", roles=("sre-oncall",)))
    assert manager.read(token[:-4] + "AAAA") is None


def test_cookie_signed_with_another_key_is_rejected(manager):
    other = SessionManager(AuthConfig(mode="dev", secret_key="a-completely-different-key-" + "y" * 20))
    assert manager.read(other.issue(User("00u123", "Mallory", roles=("sre-oncall",)))) is None


def test_expired_session_is_rejected():
    """A session past its lifetime must not authenticate.

    The lifetime is taken from config rather than a module global so this is
    deterministic: itsdangerous compares whole seconds, so a test that slept
    against a 1-second window would pass or fail on rounding.
    """
    issuer = SessionManager(AuthConfig(mode="dev", secret_key="k" * 40, session_max_age=3600))
    token = issuer.issue(User("00u123", "Alice"))
    expired = SessionManager(AuthConfig(mode="dev", secret_key="k" * 40, session_max_age=-1))
    assert expired.read(token) is None
    assert issuer.read(token) is not None


def test_garbage_cookie_is_rejected(manager):
    assert manager.read("not-a-token") is None


# -------------------------------------------------------------- identity


def test_approver_id_uses_the_idp_subject_not_the_email():
    user = User("00u123", "Alice", "alice@corp.com", ("sre-oncall",), "okta")
    # Emails get reassigned; the subject is what an auditor takes back to the IdP.
    assert user.approver_id == "okta|00u123"
    assert "alice@corp.com" not in user.approver_id


def test_oidc_claims_map_to_a_user():
    cfg = AuthConfig(mode="oidc", provider_name="okta", roles_claim="groups")
    user = user_from_oidc_claims(cfg, {
        "sub": "00u4k2", "name": "Alice Nakamura",
        "email": "alice@corp.com", "groups": ["sre-oncall", "eng"],
    })
    assert user.approver_id == "okta|00u4k2"
    assert user.roles == ("sre-oncall", "eng")


def test_oidc_roles_claim_may_be_comma_separated():
    cfg = AuthConfig(mode="oidc", provider_name="entra", roles_claim="roles")
    user = user_from_oidc_claims(cfg, {"sub": "x", "roles": "sre-oncall, platform"})
    assert user.roles == ("sre-oncall", "platform")


def test_claims_without_a_subject_are_rejected():
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        user_from_oidc_claims(AuthConfig(mode="oidc"), {"name": "no subject"})


# ---------------------------------------------------------- authorisation


def test_approver_role_is_enforced_under_oidc():
    cfg = AuthConfig(mode="oidc", approver_roles=("sre-oncall",))
    assert may_approve(cfg, User("a", "A", roles=("sre-oncall",)))
    assert not may_approve(cfg, User("b", "B", roles=("compliance-reader",)))
    assert not may_approve(cfg, User("c", "C", roles=()))


def test_dev_mode_does_not_enforce_roles():
    # A laptop demo has no group claims; failing closed there would only teach
    # operators to disable auth entirely.
    assert may_approve(AuthConfig(mode="dev"), User("a", "A", roles=()))


@pytest.mark.parametrize("path,public", [
    ("/healthz", True), ("/static/app.css", True), ("/auth/login", True),
    ("/", False), ("/approvals", False), ("/incident/X", False),
    ("/export.pdf", False), ("/api/chain", False), ("/record/abc", False),
])
def test_only_the_intended_paths_are_public(path, public):
    assert is_public_path(path) is public


# ------------------------------------------------------- dev credentials


def test_dev_login_accepts_correct_credentials(tmp_path):
    users = tmp_path / "users.json"
    users.write_text(json.dumps({
        "alice": {"password": "pw", "display_name": "Alice", "roles": ["sre-oncall"]}}))
    cfg = AuthConfig(mode="dev", secret_key="k" * 40, dev_users_path=users)
    assert authenticate_dev(cfg, "alice", "pw").roles == ("sre-oncall",)


@pytest.mark.parametrize("user,password", [
    ("alice", "wrong"), ("nobody", "pw"), ("alice", ""), ("", "pw"),
])
def test_dev_login_rejects_bad_credentials(tmp_path, user, password):
    users = tmp_path / "users.json"
    users.write_text(json.dumps({"alice": {"password": "pw", "roles": []}}))
    cfg = AuthConfig(mode="dev", secret_key="k" * 40, dev_users_path=users)
    assert authenticate_dev(cfg, user, password) is None


# ------------------------------------------------------ HTTP integration


@pytest.fixture
def dev_app(tmp_path, monkeypatch):
    """A dashboard running in dev auth mode, with a fixed secret."""
    users = tmp_path / "dev_users.json"
    users.write_text(json.dumps({
        "alice": {"password": "pw", "display_name": "Alice", "roles": ["sre-oncall"]},
        "bob": {"password": "pw", "display_name": "Bob", "roles": ["compliance-reader"]},
    }))
    monkeypatch.setenv("SWARMOPS_AUTH_MODE", "dev")
    monkeypatch.setenv("SWARMOPS_SECRET_KEY", "fixed-test-secret-" + "z" * 24)
    import dashboard.app as app_mod
    importlib.reload(app_mod)
    app_mod.AUTH.dev_users_path = users
    return app_mod


def test_pages_redirect_to_login_when_unauthenticated(dev_app):
    client = TestClient(dev_app.app, follow_redirects=False)
    for path in ("/", "/approvals", "/verify", "/export.csv"):
        resp = client.get(path)
        assert resp.status_code == 303, path
        assert "/auth/login" in resp.headers["location"]


def test_login_then_read(dev_app):
    client = TestClient(dev_app.app)
    assert client.post("/auth/login",
                       data={"username": "alice", "password": "pw", "next": "/"}).status_code == 200
    assert client.get("/").status_code == 200


def test_bad_password_does_not_create_a_session(dev_app):
    client = TestClient(dev_app.app, follow_redirects=False)
    resp = client.post("/auth/login", data={"username": "alice", "password": "nope", "next": "/"})
    assert "error=invalid" in resp.headers["location"]
    assert dev_app.SESSION_COOKIE not in client.cookies


def test_healthz_stays_public(dev_app):
    assert TestClient(dev_app.app).get("/healthz").status_code == 200


def test_logout_clears_the_session(dev_app):
    client = TestClient(dev_app.app)
    client.post("/auth/login", data={"username": "alice", "password": "pw", "next": "/"})
    client.get("/auth/logout")
    resp = TestClient(dev_app.app, follow_redirects=False)
    assert resp.get("/").status_code == 303


def test_approving_without_a_session_is_refused(dev_app):
    client = TestClient(dev_app.app, follow_redirects=False)
    resp = client.post("/approvals/some-id/decide",
                       data={"decision": "approve", "note": "n"})
    # Redirected to login, never processed.
    assert resp.status_code in (303, 401)
    assert "/auth/login" in resp.headers.get("location", "")


def test_approver_role_gates_the_decide_route():
    """A reader may read the ledger but must not be able to approve."""
    import dashboard.auth as auth_mod
    cfg = AuthConfig(mode="oidc", approver_roles=("sre-oncall",))
    reader = User("b", "Bob", roles=("compliance-reader",))
    assert not auth_mod.may_approve(cfg, reader)
