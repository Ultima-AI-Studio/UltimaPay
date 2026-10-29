"""Independent user, administrator, and merchant identity checks."""

from __future__ import annotations

import hashlib
import hmac

from .. import config
from ..contracts import now_iso


def sign_user(username: str, secret: str | None = None) -> str:
    key = secret if secret is not None else config.user_auth_secret()
    if not username or not key:
        return ""
    return hmac.new(key.encode(), username.encode(), hashlib.sha256).hexdigest()


def user_principal(username: str, signature: str):
    expected = sign_user(username)
    if not expected or not signature or not hmac.compare_digest(expected, signature):
        return None, "用户签名无效"
    return {"role": "user", "user_id": username, "username": username}, None


def principal_from_session(db, token):
    """Resolve the package API's compact `username:signature` bearer token."""
    if not token or ":" not in token:
        return None
    username, signature = token.split(":", 1)
    principal, _ = user_principal(username, signature)
    if not principal:
        return None
    row = db.execute("SELECT username FROM users WHERE username=?", (username,)).fetchone()
    return principal if row else None


def merchant_principal(db, merchant, api_key):
    if not merchant or not api_key:
        return None, "缺少商户凭据"
    row = db.execute(
        "SELECT code, api_key, enabled FROM payment_merchants WHERE code=?", (merchant,)
    ).fetchone()
    if not row or not row[2] or not hmac.compare_digest(row[1] or "", api_key):
        return None, "商户凭据无效"
    return {"role": "merchant", "merchant": merchant, "username": merchant}, None


def require_admin(token):
    expected = config.admin_token()
    return bool(expected and token and hmac.compare_digest(expected, token))


def authorize_object(principal, action, resource, owner_key):
    if not principal:
        return False, "未认证"
    if principal.get("role") == "admin":
        return True, "ok"
    if resource != "merchant" or action != "read_own":
        return False, "资源动作未声明权限（默认拒绝）"
    if principal.get("role") == "merchant" and principal.get("merchant") == owner_key:
        return True, "ok"
    return False, "无权访问他人对象（横向越权）"


def audit_sensitive(db, principal, action, resource, detail="", raw_secrets=()):
    safe = detail or ""
    for secret in raw_secrets:
        if secret:
            safe = safe.replace(secret, "****")
    db.execute(
        "INSERT INTO audit_logs(actor, role, action, resource, detail, created_at) VALUES(?,?,?,?,?,?)",
        ((principal or {}).get("user_id") or (principal or {}).get("merchant") or "",
         (principal or {}).get("role") or "anonymous", action, resource, safe, now_iso()),
    )
    db.commit()
    return safe

