"""Authentication for the dashboard.

Until this module existed, anyone who could reach the port could click Approve
and execute a production change. That is the most serious possible hole in an
approval system, because the product's entire claim is that a specific human
authorised a specific action. An unauthenticated approve record is a lie told
in a tamper-evident way.

Three modes, chosen by SWARMOPS_AUTH_MODE:

  oidc      The real one. Any OpenID Connect provider (Okta, Entra, Google
            Workspace, Auth0, Keycloak) via discovery. The authenticated
            subject becomes approver_id, so the ledger records an identity the
            customer's own IdP can vouch for rather than an OS username.

  dev       Username and password from a local file, for running the thing on a
            laptop. Refuses to start if SWARMOPS_ENV=production, and the UI
            carries a permanent banner saying the identities are not real.

  disabled  No authentication. Refuses to start unless SWARMOPS_ALLOW_INSECURE
            is explicitly set, and then shouts about it on every page. It exists
            because a demo on a laptop should not require an IdP, and because an
            operator who turns it off should have to mean it.

Authorisation is deliberately thin: a set of roles carried on the session, and
one check (`may_approve`). The heavy decisions live in ledger/policy.py, which
is evaluated again server-side on every write. Nothing here is the last line of
defence.
"""

from __future__ import annotations

import json
import os
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

ROOT = Path(__file__).resolve().parent.parent

SESSION_COOKIE = "swarmops_session"
SESSION_MAX_AGE = int(os.environ.get("SWARMOPS_SESSION_MAX_AGE", 8 * 3600))

# Paths reachable without a session. Kept short and explicit: every addition
# here is a place the ledger can be read by an unauthenticated caller.
PUBLIC_PATHS = ("/healthz", "/static/", "/auth/login", "/auth/callback", "/favicon.ico")


class AuthConfigError(RuntimeError):
    """Raised at startup when the configuration is unsafe or incomplete."""


@dataclass
class User:
    subject: str            # stable id from the IdP ('sub'), never the email
    display_name: str
    email: str | None = None
    roles: tuple[str, ...] = ()
    provider: str = "dev"

    @property
    def approver_id(self) -> str:
        """The identity written into the ledger.

        Provider-qualified and built from the IdP subject rather than the email,
        because emails get reassigned and display names change. This is the
        value an auditor takes back to the customer's identity provider.
        """
        return f"{self.provider}|{self.subject}"

    def as_session(self) -> dict[str, Any]:
        return {
            "sub": self.subject, "name": self.display_name, "email": self.email,
            "roles": list(self.roles), "provider": self.provider, "at": int(time.time()),
        }

    @classmethod
    def from_session(cls, data: dict[str, Any]) -> "User":
        return cls(
            subject=data["sub"], display_name=data.get("name") or data["sub"],
            email=data.get("email"), roles=tuple(data.get("roles") or ()),
            provider=data.get("provider", "dev"),
        )


@dataclass
class AuthConfig:
    mode: str = "disabled"
    secret_key: str = ""
    provider_name: str = "oidc"
    approver_roles: tuple[str, ...] = ("sre-oncall",)
    roles_claim: str = "groups"
    # oidc
    discovery_url: str | None = None
    client_id: str | None = None
    client_secret: str | None = None
    redirect_url: str | None = None
    # dev
    dev_users_path: Path = field(default_factory=lambda: ROOT / "dev_users.json")
    session_max_age: int = SESSION_MAX_AGE

    @property
    def enabled(self) -> bool:
        return self.mode != "disabled"

    @property
    def enforce_roles(self) -> bool:
        """Whether approving requires holding one of `approver_roles`.

        Off in dev/disabled mode, because a laptop demo has no group claims to
        carry and failing closed there would only teach operators to disable
        auth entirely.
        """
        return self.mode == "oidc"


def load_config(env: dict[str, str] | None = None) -> AuthConfig:
    env = dict(os.environ if env is None else env)
    mode = env.get("SWARMOPS_AUTH_MODE", "disabled").strip().lower()
    if mode not in ("oidc", "dev", "disabled"):
        raise AuthConfigError(
            f"SWARMOPS_AUTH_MODE={mode!r} is not one of: oidc, dev, disabled"
        )

    production = env.get("SWARMOPS_ENV", "").lower() in ("prod", "production")

    if mode == "disabled":
        if not env.get("SWARMOPS_ALLOW_INSECURE"):
            raise AuthConfigError(
                "The dashboard can approve and execute production changes, so it refuses "
                "to run unauthenticated by default.\n"
                "  For a real deployment:  SWARMOPS_AUTH_MODE=oidc (see .env.example)\n"
                "  For a laptop demo:      SWARMOPS_AUTH_MODE=dev\n"
                "  To disable anyway:      SWARMOPS_ALLOW_INSECURE=1 (do not do this in production)"
            )
        if production:
            raise AuthConfigError(
                "SWARMOPS_ALLOW_INSECURE is set but SWARMOPS_ENV=production. "
                "Refusing to serve an unauthenticated approval surface in production."
            )
    if mode == "dev" and production:
        raise AuthConfigError(
            "SWARMOPS_AUTH_MODE=dev with SWARMOPS_ENV=production. Development identities "
            "are not real identities; use SWARMOPS_AUTH_MODE=oidc."
        )

    secret = env.get("SWARMOPS_SECRET_KEY", "")
    if not secret:
        if production:
            raise AuthConfigError("SWARMOPS_SECRET_KEY must be set in production")
        # Ephemeral: sessions do not survive a restart, which is correct for a
        # dev default. A fixed fallback secret would be a backdoor.
        secret = secrets.token_urlsafe(48)

    roles = tuple(
        r.strip() for r in env.get("SWARMOPS_APPROVER_ROLES", "sre-oncall").split(",") if r.strip()
    )

    cfg = AuthConfig(
        mode=mode,
        secret_key=secret,
        session_max_age=int(env.get("SWARMOPS_SESSION_MAX_AGE", SESSION_MAX_AGE)),
        provider_name=env.get("SWARMOPS_OIDC_PROVIDER_NAME", "oidc"),
        approver_roles=roles,
        roles_claim=env.get("SWARMOPS_OIDC_ROLES_CLAIM", "groups"),
        discovery_url=env.get("SWARMOPS_OIDC_DISCOVERY_URL"),
        client_id=env.get("SWARMOPS_OIDC_CLIENT_ID"),
        client_secret=env.get("SWARMOPS_OIDC_CLIENT_SECRET"),
        redirect_url=env.get("SWARMOPS_OIDC_REDIRECT_URL"),
    )

    if mode == "oidc":
        missing = [
            name for name, value in (
                ("SWARMOPS_OIDC_DISCOVERY_URL", cfg.discovery_url),
                ("SWARMOPS_OIDC_CLIENT_ID", cfg.client_id),
                ("SWARMOPS_OIDC_CLIENT_SECRET", cfg.client_secret),
                ("SWARMOPS_OIDC_REDIRECT_URL", cfg.redirect_url),
            ) if not value
        ]
        if missing:
            raise AuthConfigError(
                "SWARMOPS_AUTH_MODE=oidc requires: " + ", ".join(missing)
            )
    return cfg


# ------------------------------------------------------------------- sessions


class SessionManager:
    def __init__(self, config: AuthConfig):
        self.config = config
        self._serializer = URLSafeTimedSerializer(config.secret_key, salt="swarmops-session")

    def issue(self, user: User) -> str:
        return self._serializer.dumps(user.as_session())

    def read(self, token: str) -> User | None:
        try:
            data = self._serializer.loads(token, max_age=self.config.session_max_age)
        except (BadSignature, SignatureExpired):
            # A tampered or expired cookie is simply not a session. Saying which
            # would tell an attacker whether their forgery was well-formed.
            return None
        try:
            return User.from_session(data)
        except (KeyError, TypeError):
            return None


# ------------------------------------------------------------ dev credentials


def load_dev_users(path: Path) -> dict[str, dict[str, Any]]:
    """Read dev_users.json, creating a starter file when absent."""
    if not path.exists():
        starter = {
            "_comment": (
                "Development identities only. These are not real identities and must never "
                "be used with SWARMOPS_ENV=production. Passwords are stored in plain text "
                "here on purpose: pretending otherwise would imply a security property this "
                "file does not have."
            ),
            "alice": {
                "password": "alice-dev-password",
                "display_name": "Alice Nakamura (on-call SRE)",
                "email": "alice@example.internal",
                "roles": ["sre-oncall"],
            },
            "bob": {
                "password": "bob-dev-password",
                "display_name": "Bob Oyelaran (compliance)",
                "email": "bob@example.internal",
                "roles": ["compliance-reader"],
            },
        }
        path.write_text(json.dumps(starter, indent=2) + "\n")
    data = json.loads(path.read_text())
    return {k: v for k, v in data.items() if not k.startswith("_")}


def authenticate_dev(config: AuthConfig, username: str, password: str) -> User | None:
    users = load_dev_users(config.dev_users_path)
    entry = users.get(username)
    if entry is None:
        # Compare against a dummy anyway so a missing user and a wrong password
        # take the same time.
        secrets.compare_digest(password, "dummy-value-of-similar-length")
        return None
    if not secrets.compare_digest(password, str(entry.get("password", ""))):
        return None
    return User(
        subject=username,
        display_name=entry.get("display_name", username),
        email=entry.get("email"),
        roles=tuple(entry.get("roles", ())),
        provider="dev",
    )


def user_from_oidc_claims(config: AuthConfig, claims: dict[str, Any]) -> User:
    subject = claims.get("sub")
    if not subject:
        raise HTTPException(502, "identity provider returned no subject claim")
    raw_roles = claims.get(config.roles_claim) or []
    if isinstance(raw_roles, str):
        raw_roles = [r.strip() for r in raw_roles.split(",") if r.strip()]
    return User(
        subject=str(subject),
        display_name=claims.get("name") or claims.get("preferred_username") or str(subject),
        email=claims.get("email"),
        roles=tuple(str(r) for r in raw_roles),
        provider=config.provider_name,
    )


# ---------------------------------------------------------------- authorisation

ANONYMOUS = User(subject="anonymous", display_name="Unauthenticated",
                 roles=("sre-oncall",), provider="insecure")


def current_user(request: Request) -> User:
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(401, "not authenticated")
    return user


def may_approve(config: AuthConfig, user: User) -> bool:
    if not config.enforce_roles:
        return True
    return any(role in config.approver_roles for role in user.roles)


def require_approver(request: Request) -> User:
    """Guard for the one route that can cause a production change."""
    config: AuthConfig = request.app.state.auth_config
    user = current_user(request)
    if not may_approve(config, user):
        raise HTTPException(
            403,
            f"{user.display_name} does not hold an approver role. "
            f"Required one of: {', '.join(config.approver_roles)}.",
        )
    return user


def safe_next(target: str | None) -> str:
    """The post-login redirect target, if it stays on this site; otherwise "/".

    `next` comes from the URL, so a crafted sign-in link could otherwise send
    someone to another site straight after they authenticate. Only a plain
    local path is accepted. "//host" and "/\\host" are refused because browsers
    read both as another host. Only printable ASCII is allowed, because browsers
    strip control characters before parsing and a real path arrives
    percent-encoded anyway. The /auth/ routes are refused too, so a link cannot
    sign someone in and straight back out.
    """
    if (
        not target
        or not target.startswith("/")
        or target.startswith("//")
        or "\\" in target
        or not all(0x21 <= ord(ch) <= 0x7E for ch in target)
        or target == "/auth"
        or target.startswith("/auth/")
    ):
        return "/"
    return target


def is_public_path(path: str) -> bool:
    return any(path == p or path.startswith(p) for p in PUBLIC_PATHS)
