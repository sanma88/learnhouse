"""User-facing passwordless "magic link" login.

Distinct from the admin/integration magic link (``purpose: "magic_link"`` in
:mod:`src.services.admin.admin`), which an API-token integration mints for a
specific user and is delivered out-of-band. This one is requested by the end user
from the login page ("email me a login link"), is emailed by this instance, and
carries ``purpose: "magic_login"``.

Security posture mirrors the admin link:
* short TTL,
* a random ``jti`` enforced single-use via a Redis ``SETNX`` marker at consume,
* consumption goes through :func:`issue_session_or_challenge`, so a user with 2FA
  still gets a second-factor challenge — a magic link is a first factor, not a
  bypass.

The request endpoint never reveals whether an address has an account (always a
200), so it cannot be used to enumerate users.
"""

import html
import logging
import secrets
from datetime import timedelta
from typing import Optional, Tuple
from urllib.parse import quote

from fastapi import HTTPException
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.organizations import Organization
from src.db.users import UserRead
from src.security.auth import create_access_token, decode_jwt
from src.services.email.utils import send_email

logger = logging.getLogger(__name__)

MAGIC_LOGIN_PURPOSE = "magic_login"
# Long enough to leave the app, open email and click; short enough that an
# intercepted-but-unused link expires quickly.
MAGIC_LOGIN_TTL = timedelta(minutes=15)


def issue_magic_login_token(email: str, org_id: Optional[int]) -> str:
    """Mint a single-use magic-login JWT for ``email`` (optionally org-bound)."""
    payload = {
        "sub": email,
        "purpose": MAGIC_LOGIN_PURPOSE,
        "jti": secrets.token_urlsafe(16),
    }
    if org_id is not None:
        payload["org_id"] = org_id
    return create_access_token(data=payload, expires_delta=MAGIC_LOGIN_TTL)


def _redis():  # pragma: no cover - thin import shim; patched out in tests
    try:
        from src.core.redis import get_redis_client

        return get_redis_client()
    except Exception:
        return None


def _burn_jti(jti: str) -> bool:
    """Atomically consume ``jti``. True on first use, False on replay.

    Fails **closed**: if Redis is unavailable we refuse the link rather than risk
    a replayable login. (The admin link fails open because it is already
    single-recipient and integration-gated; a self-service emailed link is a
    softer target, so single-use must hold even without Redis.)
    """
    r = _redis()
    if r is None:
        logger.error("Magic-login: Redis unavailable, refusing to consume (fail closed)")
        return False
    try:
        ok = r.set(
            f"magic_login_used:{jti}",
            "1",
            nx=True,
            ex=int(MAGIC_LOGIN_TTL.total_seconds()) + 60,
        )
        return bool(ok)
    except Exception:
        logger.exception("Magic-login: Redis error during single-use check")
        return False


async def resolve_org(org_slug: Optional[str], db_session: AsyncSession) -> Optional[Organization]:
    if not org_slug:
        return None
    return (
        await db_session.execute(select(Organization).where(Organization.slug == org_slug))
    ).scalars().first()


def send_magic_login_email(
    user: UserRead,
    email: str,
    base_url: str,
    token: str,
    lang: str = "en",
    org_name: Optional[str] = None,
    logo_url: Optional[str] = None,
) -> bool:
    """Email the clickable login link. Link points at the frontend consume page,
    which posts the token back to the verify endpoint.

    Copy comes from the shared ``magic_login.*`` bundle rather than the literals
    this used to carry: it is the only email in the codebase that named the
    upstream product instead of the instance, and the only one that ignored the
    ``lang`` it is handed. Both were invisible until the passwordless login was
    switched on in production.

    ``org_name`` / ``logo_url`` white-label the BODY when the login was
    requested from an organization's own login page: an org with a French
    default language and its own logo used to receive an English link under the
    instance wordmark, because this was the last org-scoped mail to resolve
    neither.

    There is deliberately no ``sender_name``, unlike the other org-scoped mails.
    This message authenticates against the platform rather than acting on any
    organization's behalf, so its ``From`` name stays the deployment's own —
    the value the operator configured and verified on a delivered message. An
    org display name would override that deployment setting on the single
    message a locked-out user has to recognise and trust, so the parameter does
    not exist rather than merely going unused.

    Both remaining decorations are optional and degrade on their own: no
    ``logo_url`` falls back to the instance mark, an unknown or mistyped
    ``lang`` to English inside ``t()``. A magic link is the user's only way in
    — it must go out even when the org lookup that decorates it did not.
    """
    from src.services.email.translations import t
    from src.services.users.emails import (
        STYLES,
        _email_layout,
        _logo_or_brand,
    )

    safe_token = quote(token, safe="")
    login_url = f"{base_url.rstrip('/')}/auth/magic?token={safe_token}"
    safe_name = html.escape(user.username or user.email)

    heading = t(lang, "magic_login.heading")
    body_text = t(lang, "magic_login.body", username=safe_name)
    cta = t(lang, "magic_login.cta")
    copy_paste = t(lang, "magic_login.copy_paste")

    body_content = f"""
        <h1 style="{STYLES['h1']}">{heading}</h1>
        <p style="{STYLES['p']}">
            {body_text}
        </p>
        <a href="{login_url}" style="{STYLES['button']}">{cta}</a>
        <p style="{STYLES['link_text']}">
            {copy_paste}<br />{login_url}
        </p>
    """
    # Resolved through the shared `_logo_or_brand` rather than open-coded here,
    # so the operator's LEARNHOUSE_EMAIL_LOGO_URL applies to the login link on
    # the same terms as to every other org-scoped mail. The previous inline
    # expression produced exactly what `_logo_or_brand` produces when that
    # variable is unset — the org's <img> with the org name as alt, and the
    # instance mark when there is no org logo — so this is a no-op for any
    # deployment that has not set it.
    logo_html = _logo_or_brand(logo_url, org_name)

    return send_email(
        to=email,
        subject=t(lang, "magic_login.subject"),
        body=_email_layout(
            title=heading,
            body_content=body_content,
            footer_note=t(lang, "magic_login.footer"),
            logo_html=logo_html,
        ),
    )


def consume_magic_login_token(token: str) -> Tuple[str, Optional[int]]:
    """Validate + burn a magic-login token. Returns ``(email, org_id)``.

    Raises 401/410 on an invalid, expired, wrong-purpose, or already-used token.
    """
    try:
        payload = decode_jwt(token)
    except Exception:
        raise HTTPException(status_code=401, detail={"code": "MAGIC_LINK_INVALID", "message": "This login link is invalid or has expired."})

    if not payload or payload.get("purpose") != MAGIC_LOGIN_PURPOSE:
        raise HTTPException(status_code=410, detail={"code": "MAGIC_LINK_INVALID", "message": "This is not a valid login link."})

    jti = payload.get("jti")
    if not jti or not _burn_jti(jti):
        raise HTTPException(
            status_code=410,
            detail={"code": "MAGIC_LINK_USED", "message": "This login link has already been used. Please request a new one."},
        )

    email = payload.get("sub")
    if not email:
        raise HTTPException(status_code=401, detail={"code": "MAGIC_LINK_INVALID", "message": "This login link is invalid."})

    org_raw = payload.get("org_id")
    try:
        org_id = int(org_raw) if org_raw is not None else None
    except (TypeError, ValueError):
        org_id = None
    return str(email), org_id
