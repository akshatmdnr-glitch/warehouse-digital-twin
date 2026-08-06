"""Authentication & authorization.

- Passwords: PBKDF2-HMAC-SHA256 with per-user salt (stdlib hashlib).
- Tokens: signed HS256 JWT-lite (HMAC-SHA256 over base64url claims).
- Roles: operator < supervisor < administrator. Tokens may carry
  `readonly: true` for read-only dashboard mode (all mutations -> 403).
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
import time

from fastapi import Depends, Request

from . import repository as repo
from .config import get_config

_PBKDF2_ITERATIONS = 120_000
ROLE_LEVEL = {"operator": 1, "supervisor": 2, "administrator": 3}
VALID_ROLES = ("operator", "supervisor", "administrator")


# ── password hashing ──────────────────────────────────────────


def hash_password(password):
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ITERATIONS)
    return "pbkdf2$%d$%s$%s" % (
        _PBKDF2_ITERATIONS,
        base64.b64encode(salt).decode(),
        base64.b64encode(digest).decode(),
    )


def verify_password(password, stored):
    try:
        _, iters, salt_b64, hash_b64 = stored.split("$")
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), base64.b64decode(salt_b64), int(iters)
        )
        return hmac.compare_digest(base64.b64encode(digest).decode(), hash_b64)
    except Exception:
        return False


# ── token signing (HS256 JWT-lite) ────────────────────────────


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def _sign(header: str, payload: str, secret: str) -> str:
    return _b64url(
        hmac.new(
            secret.encode(), f"{header}.{payload}".encode(), hashlib.sha256
        ).digest()
    )


def issue_token(username, role, ttl=None, readonly=False):
    cfg = get_config()
    ttl = ttl or cfg.get("auth.token_ttl_seconds", 86400)
    secret = _secret()
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    now = time.time()
    claims = {
        "sub": username,
        "role": role,
        "iat": now,
        "exp": now + ttl,
        "readonly": bool(readonly),
    }
    payload = _b64url(json.dumps(claims).encode())
    sig = _sign(header, payload, secret)
    token = f"{header}.{payload}.{sig}"
    repo.store_token(token, username, role, now + ttl)
    return token, now + ttl


def verify_token(token):
    """Return claims dict or None."""
    parts = token.split(".")
    if len(parts) != 3:
        return None
    header, payload, sig = parts
    secret = _secret()
    if not hmac.compare_digest(_sign(header, payload, secret), sig):
        return None
    try:
        claims = json.loads(_b64url_decode(payload))
    except Exception:
        return None
    if claims.get("exp", 0) < time.time():
        return None
    if not repo.token_valid(token):
        return None
    return claims


def _secret():
    cfg = get_config()
    secret = cfg.get("auth.secret")
    if not secret:
        raise RuntimeError(
            "auth.secret is not configured — set BACKEND_AUTH_SECRET " "in production"
        )
    return secret


def role_allows(role, min_role):
    return ROLE_LEVEL.get(role, 0) >= ROLE_LEVEL.get(min_role, 0)


def seed_default_user():
    """Create the default administrator if no users exist."""
    if repo.list_users():
        return
    cfg = get_config()
    password = cfg.get("auth.admin_password", "admin")
    repo.create_user("admin", "administrator", hash_password(password))


# ── FastAPI dependencies ──────────────────────────────────────


def get_current_user(request: Request):
    from fastapi import HTTPException

    auth = request.headers.get("Authorization", "")
    token = None
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
    elif "access_token" in request.query_params:
        token = request.query_params["access_token"]
    if not token:
        raise HTTPException(status_code=401, detail="Missing token")
    claims = verify_token(token)
    if not claims:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return claims


def require_role(min_role):
    def dep(claims=Depends(get_current_user)):
        from fastapi import HTTPException

        if not role_allows(claims.get("role", ""), min_role):
            raise HTTPException(status_code=403, detail=f"Requires role >= {min_role}")
        if claims.get("readonly"):
            raise HTTPException(
                status_code=403, detail="Read-only mode: mutations disabled"
            )
        return claims

    return dep
