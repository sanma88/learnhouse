import html
import logging
import os
from typing import Optional
from urllib.parse import quote

from pydantic import EmailStr
from src.db.organizations import OrganizationRead
from src.db.users import UserRead
from src.services.email.branding import email_brand
from src.services.email.translations import t
from src.services.email.utils import send_email

logger = logging.getLogger(__name__)


def _send_notification_email(**kwargs):
    """Send mail whose failure must not fail the caller's request.

    Welcome/lifecycle notifications are a side effect of an action that has
    already happened and been committed — the account exists, the org was
    created, the role changed. When the provider is rate-limited or times out,
    raising here turned "no welcome email" into "signup returned 503", which the
    user then retried. Delivery failures are logged and swallowed instead.

    Mail the user is actively waiting on (password reset, invitation, address
    verification) still calls ``send_email`` directly and still raises.
    """
    try:
        return send_email(**kwargs)
    except Exception as e:
        logger.warning("Non-critical email to %s not sent: %s", kwargs.get("to"), e)
        return False



# HI-HA: the email layer runs outside a request context, so it cannot use
# get_media_base_url(request). Resolve the instance's public URL the same way:
# an explicit override first, then the configured hosting domain.
def _public_base_url() -> str:
    override = (
        os.environ.get("LEARNHOUSE_MEDIA_URL")
        or os.environ.get("LEARNHOUSE_BACKEND_URL")
        or ""
    ).strip().rstrip("/")
    if override:
        return override
    try:
        from config.config import get_learnhouse_config

        config = get_learnhouse_config()
        domain = (config.hosting_config.domain or "").strip().rstrip("/")
        if not domain or "localhost" in domain:
            return ""
        scheme = "https" if config.hosting_config.ssl else "http"
        return f"{scheme}://{domain}"
    except Exception:  # config unavailable — degrade to a text mark, never crash a send
        return ""


# HI-HA: kept as a name for what the mark *says*, but the value now comes from
# the one resolver every part of an email shares (From header, logo, body copy)
# instead of reading site_name directly — that setting is suffixed per
# environment ("… (local)"), which has no business in a recipient's inbox.
def _site_name() -> str:
    return email_brand()


# HI-HA: upstream pointed this at university.learnhouse.io — a third party's site,
# used both as the footer "learn more" target and as the last-resort CTA fallback.
# It now defaults to this instance's own public URL; set LEARNHOUSE_ACADEMY_URL to
# override. When no public URL can be resolved the academy link is omitted entirely
# rather than sending users somewhere that isn't ours.
ACADEMY_URL = os.environ.get("LEARNHOUSE_ACADEMY_URL") or _public_base_url() or ""


# HI-HA: the operator's explicit choice of mark FOR EMAIL, independent of the
# logo shown in the interface. The two have different backgrounds and cannot
# always be the same file: campus.hi-ha.be's org logo is a white PNG, correct on
# the dark interface chrome and invisible on the white card every email is laid
# out on. Read through one helper so "is it set?" has exactly one answer in THIS
# module — `_brand_logo_html` used the raw value, and a second, differently
# spelled test beside it would eventually disagree. The scope of that promise is
# the module and no wider: the Next.js mail path has its own, separately spelled
# NEXT_PUBLIC_LEARNHOUSE_EMAIL_LOGO_URL (apps/web/components/Emails/
# LearnHouseEmail.tsx), and _org_logo_img does not strip its own argument either
# — an org logo URL comes from the database, not from a deployment's shell.
#
# Whitespace is stripped: a value of "  " is not a URL, and treating it as one
# only produces <img src="  "> — a broken image where the mark belongs. Unset
# (the case every deployment that never heard of this variable is in) is
# unaffected in the only way that matters: the raw read returned None and this
# returns "", and every reader of the value tests it for truthiness.
def _email_logo_url() -> str:
    return (os.environ.get("LEARNHOUSE_EMAIL_LOGO_URL") or "").strip()


def _email_logo_overrides_org_logo() -> bool:
    """True when the operator's email logo must win over an ORG's own logo.

    ``LEARNHOUSE_EMAIL_LOGO_URL`` is an INSTANCE variable, and LearnHouse is
    multi-org by design: on a multi-tenant deployment an org's logo is that
    tenant's property, and one instance-wide image silently replacing it in
    every tenant's mail is a white-label regression, not a feature. Worse, it
    would be a *retroactive* one — the variable already exists and already means
    "the instance's own mark on email", so an operator who set it to stop
    hotlinking a third party's wordmark would find it stomping their customers'
    branding after an upgrade they did not ask for.

    In ``tenancy == "single"`` there is no third party: the sole organization
    and the instance are the same entity addressing the same recipients, so an
    operator saying "this image is my email logo" can only mean it. That is the
    only mode in which this override applies.

    Unreadable configuration degrades to False — i.e. to the behaviour that
    shipped before this override existed. A configuration failure must not be
    the thing that changes who an email is branded as.
    """
    if not _email_logo_url():
        return False
    try:
        from config.config import get_learnhouse_config

        return get_learnhouse_config().hosting_config.tenancy == "single"
    except Exception:  # config unavailable — keep the pre-existing behaviour
        # Logged, unlike the other config guards on this path: this one silently
        # decides WHOSE brand a message carries, and an operator who set the
        # variable and sees the org logo anyway needs somewhere to look.
        logger.debug(
            "Could not read tenancy; LEARNHOUSE_EMAIL_LOGO_URL will not "
            "outrank an organization logo on this send.",
            exc_info=True,
        )
        return False


# HI-HA: upstream inlined the LearnHouse wordmark as an SVG here. Two problems:
# it is a third party's mark on our mail, and this module's own _org_logo_img()
# docstring notes that inline SVG is stripped by some clients (e.g. Gmail).
# We serve this instance's PNG wordmark instead — reliable in every mail client —
# and degrade to the configured site name as styled text when no public URL is
# resolvable, so a recipient never sees a broken image.
def _brand_logo_html() -> str:
    base = _email_logo_url()
    if base:
        src = base
    else:
        root = _public_base_url()
        src = f"{root}/black_logo.png" if root else ""
    name = _site_name()
    if not src:
        return (
            f'<span style="font-size: 20px; font-weight: 700; color: #363B41;">'
            f'{html.escape(name)}</span>'
        )
    return (
        f'<img src="{html.escape(src)}" alt="{html.escape(name)}" '
        'style="max-height: 40px; max-width: 180px; height: auto; width: auto;" />'
    )


# Shared email styles matching the platform's design system
STYLES = {
    "body": "margin: 0; padding: 0; background-color: #f5f5f5; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;",
    "wrapper": "padding: 48px 24px;",
    "container": "max-width: 480px; margin: 0 auto; background-color: #ffffff; border-radius: 16px; overflow: hidden; border: 1px solid #e5e5e5;",
    "header": "padding: 48px 48px 0 48px; text-align: center;",
    "content": "padding: 36px 48px 48px 48px; text-align: center;",
    "h1": "margin: 0 0 12px 0; font-size: 22px; font-weight: 900; color: #000000; letter-spacing: -0.02em; line-height: 1.3;",
    "p": "margin: 0 0 20px 0; font-size: 14px; color: rgba(0,0,0,0.45); font-weight: 500; line-height: 1.7;",
    "button": "display: inline-block; padding: 14px 32px; background-color: #000000; color: #ffffff; text-decoration: none; border-radius: 10px; font-size: 14px; font-weight: 700; line-height: 1;",
    "link_text": "margin: 24px 0 0 0; font-size: 11px; color: rgba(0,0,0,0.2); word-break: break-all; font-weight: 500; line-height: 1.6;",
    "divider": "margin: 28px 0; border: none; border-top: 1px solid #f0f0f0;",
    "footer": "padding: 0 48px 40px 48px; text-align: center;",
    "footer_text": "margin: 0; font-size: 12px; color: rgba(0,0,0,0.2); font-weight: 500; line-height: 1.6;",
    "code": "display: inline-block; padding: 14px 28px; background-color: #fafafa; border: 1px solid #e5e5e5; border-radius: 10px; font-size: 28px; font-weight: 900; letter-spacing: 0.12em; color: #000000; font-family: monospace;",
}


def _org_logo_img(logo_url: str, alt: str) -> str:
    """<img> for a white-labeled org logo.

    Bounded to the same footprint as the instance wordmark. Raster logos
    (PNG/JPG) render in every mail client; an SVG logo may be stripped by some
    (e.g. Gmail), in which case the ``alt`` (the org name) shows instead — still
    org-branded, never a broken instance mark.
    """
    return (
        f'<img src="{html.escape(logo_url)}" alt="{html.escape(alt)}" '
        'style="max-height: 40px; max-width: 180px; height: auto; width: auto;" />'
    )


def _logo_or_brand(logo_url: Optional[str], alt: Optional[str] = None) -> str:
    """Header mark for an ORG-SCOPED email. THE resolver — see the order below.

    Every mail that already carries the org's name in its ``From`` header and
    the org's language in its copy should also carry the org's mark — a message
    that is white-labeled in three places out of four reads as a forwarded one.
    The four transactional mails that resolved the language but not the logo
    (password reset, invitation, role change, address verification) now route
    through here, and so does the magic-login link.

    Resolution order, first hit wins:

    1. ``LEARNHOUSE_EMAIL_LOGO_URL`` — the operator's explicit choice of mark
       for email, and *only* in ``tenancy == "single"``. See
       ``_email_logo_overrides_org_logo`` for why the tenancy condition is not
       optional. This exists because an org logo is drawn for the interface,
       which does not have to share the email's white background: a mark that
       is correct in the product can be invisible in the inbox, and the fix
       must not be "change the logo everyone sees in the app".
    2. The organization's own logo (``logo_url``) — the white-label default.
    3. This instance's mark (``_brand_logo_html``), when the org has none or no
       absolute media host is resolvable. Note that step 3 has *always* honored
       ``LEARNHOUSE_EMAIL_LOGO_URL``; step 1 is what makes the variable mean the
       same thing whether or not the org happens to have uploaded a logo.

    ``alt`` applies to step 2 only, where it falls back to the instance name so
    the ``<img>`` never carries an empty alternative text — a client that strips
    images would otherwise show a blank header. Steps 1 and 3 both render the
    INSTANCE mark, whose ``alt`` is the instance brand by construction: an
    operator who chooses an email logo has chosen the image a recipient sees,
    and labelling that image with an organization's name would misdescribe it.
    The org name therefore disappears from the alternative text when step 1
    fires — deliberately, and only in the single-org mode where the two names
    describe the same entity anyway.
    """
    if _email_logo_overrides_org_logo():
        return _brand_logo_html()
    if not logo_url:
        return _brand_logo_html()
    return _org_logo_img(logo_url, alt or _site_name())


def _first_sentence(text: str, limit: int = 110) -> str:
    """Opening sentence of a body string, for use as preheader text.

    Handles the full stops of every locale we ship — the CJK ideographic
    period, the Arabic and Devanagari terminators — then falls back to a word
    boundary. Inbox previews are cut around 100 characters anyway.
    """
    if not text:
        return ""

    for terminator in ("。", "۔", "।", ". ", "! ", "? ", "؟ "):
        head, sep, _tail = text.partition(terminator)
        if sep and len(head) <= limit:
            return (head + sep).strip()

    if len(text) <= limit:
        return text.strip()
    return text[:limit].rsplit(" ", 1)[0].strip() + "…"


def _reply_to_address() -> str:
    """Where a reply to a lifecycle email should land, or "" if unconfigured."""
    try:
        from config.config import get_learnhouse_config

        return (get_learnhouse_config().contact_email or "").strip()
    except Exception:  # pragma: no cover - config is always present in practice
        return ""


def _stat_strip(stats: list[tuple[str, int]]) -> str:
    """A row of label/value pairs, e.g. "LESSONS 8   LEARNERS 0".

    Deliberately not prose. Writing "8 lessons" into copy means solving plural
    agreement in twenty languages — Russian has three forms, Arabic six — and
    without an ICU library the result is "1 lessons" in production. A label
    beside a bare figure needs no agreement in any of them, and it reads faster
    than a sentence anyway.
    """
    if not stats:
        return ""

    cells = []
    for label, value in stats:
        cells.append(
            '<td style="padding: 0 14px; text-align: center;">'
            '<div style="font-size: 22px; font-weight: 900; color: #000000; '
            f'line-height: 1.2;">{value}</div>'
            '<div style="font-size: 10px; font-weight: 700; letter-spacing: 0.08em; '
            'text-transform: uppercase; color: rgba(0,0,0,0.35); margin-top: 2px;">'
            f"{html.escape(label)}</div>"
            "</td>"
        )

    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
        'align="center" style="margin: 0 auto 24px auto; border-collapse: collapse;">'
        f"<tr>{''.join(cells)}</tr>"
        "</table>"
    )


def _preheader_block(text: str) -> str:
    """The grey line an inbox list shows after the subject.

    Without one, clients scrape the first visible text — which here is the
    heading, so the list entry reads as the subject said twice. Setting it
    explicitly buys a second line of information in the only place a reader
    looks before deciding to open.

    The zero-width padding after the text stops the client continuing into the
    body copy once the preheader runs out.
    """
    if not text:
        return ""
    padding = "&#847;&zwnj;&nbsp;" * 60
    hide = (
        "display:none;max-height:0;overflow:hidden;mso-hide:all;"
        "font-size:1px;line-height:1px;color:#ffffff;opacity:0;"
    )
    return (
        f'<div style="{hide}">{html.escape(text)}</div>'
        f'<div style="{hide}">{padding}</div>'
    )


def _email_layout(
    title: str,
    body_content: str,
    footer_note: str = "",
    logo_html: Optional[str] = None,
    unsubscribe_url: str = "",
    unsubscribe_label: str = "Unsubscribe from these emails",
    preheader: str = "",
) -> str:
    """Wrap content in the standard email layout.

    ``logo_html`` defaults to this instance's brand mark; white-labeled emails
    pass the org's logo <img> instead. Passing ``""`` is the way to ask for no
    mark at all — the distinction matters because the default used to be the
    string ``None``, interpolated straight into the header, and the nine emails
    that never passed a logo (password reset, invitation, verification, the
    lifecycle confirmations, the magic link) each rendered the word "None"
    where the mark belongs.

    ``unsubscribe_url`` is set only by bulk lifecycle mail. Transactional email
    (password reset, invitation, verification) leaves it empty and renders
    byte-identically to before — you cannot unsubscribe from a password reset.
    The link is deliberately legible rather than hidden: someone who wants out
    and can't find the exit reports spam instead, which costs the sending domain
    far more than the opt-out does.
"""
    if logo_html is None:
        logo_html = _brand_logo_html()

    note_html = ""
    if footer_note:
        note_html = f'\n            <p style="{STYLES["footer_text"]}">{footer_note}</p>'

    unsub_html = ""
    if unsubscribe_url:
        unsub_html = (
            f'\n            <p style="{STYLES["footer_text"]} margin-top: 12px;">'
            f'<a href="{html.escape(unsubscribe_url)}" '
            'style="color: rgba(0,0,0,0.35); text-decoration: underline;">'
            f'{html.escape(unsubscribe_label)}</a></p>'
        )

    footer_html = ""
    if note_html or unsub_html:
        footer_html = f"""
        <div style="{STYLES['footer']}">
            <hr style="{STYLES['divider']}" />{note_html}{unsub_html}
        </div>"""

    # Prefixed with its own newline so that an absent preheader leaves the
    # document byte-identical to before — the twelve transactional emails
    # share this layout and none of their output may shift.
    block = _preheader_block(preheader)
    preheader_html = f"\n    {block}" if block else ""

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body style="{STYLES['body']}">{preheader_html}
    <div style="{STYLES['wrapper']}">
        <div style="{STYLES['container']}">
            <div style="{STYLES['header']}">
                {logo_html}
            </div>
            <div style="{STYLES['content']}">
                {body_content}
            </div>
            {footer_html}
        </div>
    </div>
</body>
</html>"""


def send_account_creation_email(
    user: UserRead,
    email: EmailStr,
    lang: str = "en",
    cta_url: str | None = None,
    org_name: str | None = None,
    logo_url: str | None = None,
    sender_name: str | None = None,
):
    """Welcome email sent once an account exists.

    ``cta_url`` is where "Get Started" lands: the org-scoped URL when the
    account was created inside an org, or the platform org-picker for org-less
    signups. Falls back to the public academy when no URL is supplied.

    When ``org_name`` is set the email is WHITE-LABELED to that organization:
    the subject and body name the org (not the instance), the org's ``logo_url``
    replaces the instance mark when present, and the footer is reduced to a
    subtle "Powered by <brand>". Org-less signups keep the instance-branded
    variant with the academy footer link.
    """
    safe_username = html.escape(user.username)
    white_label = bool(org_name)
    safe_org = html.escape(org_name) if org_name else ""

    heading = t(lang, "account_creation.heading", username=safe_username)
    cta = t(lang, "account_creation.cta")

    if white_label:
        subject = t(lang, "account_creation.subject_org", org_name=safe_org, username=safe_username)
        body_text = t(lang, "account_creation.body_in_org", org_name=safe_org)
        footer_note = t(lang, "account_creation.footer_powered")
        logo_html = _logo_or_brand(logo_url, org_name)
    else:
        subject = t(lang, "account_creation.subject", username=safe_username)
        body_text = t(lang, "account_creation.body")
        academy_link = (
            f'<a href="{html.escape(ACADEMY_URL)}" '
            'style="color: rgba(0,0,0,0.35); text-decoration: underline;">'
            f'{t(lang, "academy_link_text")}</a>'
        ) if ACADEMY_URL else t(lang, "academy_link_text")
        footer_note = t(lang, "account_creation.footer", academy_link=academy_link)
        logo_html = _brand_logo_html()

    body_content = f"""
        <h1 style="{STYLES['h1']}">{heading}</h1>
        <p style="{STYLES['p']}">
            {body_text}
        </p>
        <a href="{html.escape(cta_url or ACADEMY_URL)}" style="{STYLES['button']}">
            {cta}
        </a>
    """

    return _send_notification_email(
        to=email,
        subject=subject,
        body=_email_layout(
            title=heading,
            body_content=body_content,
            footer_note=footer_note,
            logo_html=logo_html,
        ),
        sender_name=sender_name,
    )


def send_org_created_email(
    email: EmailStr,
    org_name: str,
    dashboard_url: str,
    lang: str = "en",
):
    """Confirmation email when a user creates a new organization."""
    safe_name = html.escape(org_name)
    heading = t(lang, "org_created.heading", org_name=safe_name)
    body_text = t(lang, "org_created.body")
    cta = t(lang, "org_created.cta")

    body_content = f"""
        <h1 style="{STYLES['h1']}">{heading}</h1>
        <p style="{STYLES['p']}">{body_text}</p>
        <a href="{html.escape(dashboard_url)}" style="{STYLES['button']}">{cta}</a>
    """
    return _send_notification_email(
        to=email,
        subject=t(lang, "org_created.subject", org_name=safe_name),
        body=_email_layout(
            title=heading,
            body_content=body_content,
            footer_note=t(lang, "org_created.footer"),
        ),
    )


def send_org_deleted_email(
    email: EmailStr,
    org_name: str,
    lang: str = "en",
):
    """Confirmation email sent to org admins after an organization is deleted."""
    safe_name = html.escape(org_name)
    heading = t(lang, "org_deleted.heading", org_name=safe_name)
    body_text = t(lang, "org_deleted.body")

    body_content = f"""
        <h1 style="{STYLES['h1']}">{heading}</h1>
        <p style="{STYLES['p']}">{body_text}</p>
    """
    return _send_notification_email(
        to=email,
        subject=t(lang, "org_deleted.subject", org_name=safe_name),
        body=_email_layout(
            title=heading,
            body_content=body_content,
            footer_note=t(lang, "org_deleted.footer"),
        ),
    )


def send_account_deleted_email(
    email: EmailStr,
    username: str = "",
    lang: str = "en",
):
    """Confirmation ('goodbye') email sent after an account is deleted."""
    heading = t(lang, "account_deleted.heading")
    body_text = t(lang, "account_deleted.body")

    body_content = f"""
        <h1 style="{STYLES['h1']}">{heading}</h1>
        <p style="{STYLES['p']}">{body_text}</p>
    """
    return _send_notification_email(
        to=email,
        subject=t(lang, "account_deleted.subject"),
        body=_email_layout(
            title=heading,
            body_content=body_content,
            footer_note=t(lang, "account_deleted.footer"),
        ),
    )


def send_password_reset_email(
    generated_reset_code: str,
    user: UserRead,
    organization: OrganizationRead,
    email: EmailStr,
    base_url: str,
    lang: str = "en",
    sender_name: str | None = None,
    logo_url: str | None = None,
):
    """Reset code + link for an account inside an ORGANIZATION.

    White-labeled to that org: its language, its ``From`` name and — since the
    org logo was wired through — its mark. ``logo_url`` absent keeps the
    instance mark, which is what every one of these sent before.
    """
    safe_username = html.escape(user.username)
    safe_code = html.escape(generated_reset_code)
    safe_email = quote(str(email), safe='')
    safe_code_param = quote(generated_reset_code, safe='')
    reset_url = f"{base_url}/reset?email={safe_email}&amp;resetCode={safe_code_param}"

    heading = t(lang, "password_reset.heading")
    body_text = t(lang, "password_reset.body", username=safe_username)
    cta = t(lang, "password_reset.cta")

    body_content = f"""
        <h1 style="{STYLES['h1']}">{heading}</h1>
        <p style="{STYLES['p']}">
            {body_text}
        </p>
        <div style="margin: 28px 0;">
            <span style="{STYLES['code']}">{safe_code}</span>
        </div>
        <a href="{reset_url}" style="{STYLES['button']}">
            {cta}
        </a>
    """

    return send_email(
        to=email,
        subject=t(lang, "password_reset.subject"),
        body=_email_layout(
            title=heading,
            body_content=body_content,
            footer_note=t(lang, "password_reset.footer_org"),
            logo_html=_logo_or_brand(logo_url, getattr(organization, "name", None)),
        ),
        sender_name=sender_name,
    )


def send_password_reset_email_platform(
    generated_reset_code: str,
    user: UserRead,
    email: EmailStr,
    base_url: str,
    lang: str = "en",
):
    safe_username = html.escape(user.username)
    safe_code = html.escape(generated_reset_code)
    safe_email = quote(str(email), safe='')
    safe_code_param = quote(generated_reset_code, safe='')
    reset_url = f"{base_url}/reset?email={safe_email}&amp;resetCode={safe_code_param}"

    heading = t(lang, "password_reset.heading")
    body_text = t(lang, "password_reset.body", username=safe_username)
    cta = t(lang, "password_reset.cta")

    body_content = f"""
        <h1 style="{STYLES['h1']}">{heading}</h1>
        <p style="{STYLES['p']}">
            {body_text}
        </p>
        <div style="margin: 28px 0;">
            <span style="{STYLES['code']}">{safe_code}</span>
        </div>
        <a href="{reset_url}" style="{STYLES['button']}">
            {cta}
        </a>
    """

    return send_email(
        to=email,
        subject=t(lang, "password_reset.subject"),
        body=_email_layout(
            title=heading,
            body_content=body_content,
            footer_note=t(lang, "password_reset.footer_platform"),
        ),
    )


def send_invitation_email(
    email: EmailStr,
    org_name: str,
    inviter_username: str,
    signup_url: str,
    invite_code: Optional[str] = None,
    lang: str = "en",
    sender_name: str | None = None,
    logo_url: str | None = None,
):
    safe_org_name = html.escape(org_name)
    safe_inviter = html.escape(inviter_username)

    code_section = ""
    if invite_code:
        safe_code = html.escape(invite_code)
        code_hint = t(lang, "invitation.code_hint")
        code_section = f"""
        <div style="margin: 28px 0;">
            <span style="{STYLES['code']}">{safe_code}</span>
        </div>
        <p style="{STYLES['p']}">
            {code_hint}
        </p>"""
    else:
        code_section = f"""
        <p style="{STYLES['p']}">
            {t(lang, "invitation.no_code_hint")}
        </p>"""

    heading = t(lang, "invitation.heading")
    intro = t(lang, "invitation.intro", inviter=safe_inviter, org_name=safe_org_name)
    cta = t(lang, "invitation.cta", org_name=safe_org_name)

    body_content = f"""
        <h1 style="{STYLES['h1']}">{heading}</h1>
        <p style="{STYLES['p']}">
            {intro}
        </p>
        {code_section}
        <a href="{signup_url}" style="{STYLES['button']}">
            {cta}
        </a>
    """

    return send_email(
        to=email,
        subject=t(lang, "invitation.subject", org_name=safe_org_name),
        body=_email_layout(
            title=heading,
            body_content=body_content,
            footer_note=t(lang, "invitation.footer", inviter=safe_inviter),
            logo_html=_logo_or_brand(logo_url, org_name),
        ),
        sender_name=sender_name,
    )


def send_org_join_email(
    email: EmailStr,
    username: str,
    org_name: str,
    cta_url: str,
    lang: str = "en",
    logo_url: str | None = None,
    sender_name: str | None = None,
):
    """Greeting sent when an EXISTING account becomes a member of an organization.

    Complements ``send_account_creation_email``, which only fires for brand new
    accounts: a user who already had an account and then joined a second org
    (invite code, open join, OAuth invite, admin provisioning) previously got no
    mail at all and had to find their way to the org on their own.

    Always white-labeled to the org — the user is being welcomed into that
    academy, not onto the platform — with the org's logo when it has one.
    """
    safe_username = html.escape(username)
    safe_org_name = html.escape(org_name)

    heading = t(lang, "org_join.heading", username=safe_username)
    body_text = t(lang, "org_join.body", org_name=safe_org_name)
    cta = t(lang, "org_join.cta")

    body_content = f"""
        <h1 style="{STYLES['h1']}">{heading}</h1>
        <p style="{STYLES['p']}">
            {body_text}
        </p>
        <a href="{html.escape(cta_url)}" style="{STYLES['button']}">
            {cta}
        </a>
        <p style="{STYLES['link_text']}">{html.escape(cta_url)}</p>
    """

    return _send_notification_email(
        to=email,
        subject=t(lang, "org_join.subject", org_name=safe_org_name),
        body=_email_layout(
            title=heading,
            body_content=body_content,
            footer_note=t(lang, "org_join.footer", org_name=safe_org_name),
            logo_html=_logo_or_brand(logo_url, org_name),
        ),
        sender_name=sender_name,
    )


def send_role_changed_email(
    email: EmailStr,
    username: str,
    org_name: str,
    new_role_name: str,
    lang: str = "en",
    cta_url: str | None = None,
    sender_name: str | None = None,
    logo_url: str | None = None,
):
    """
    Send an email notifying a user that their role has changed in an organization.

    ``cta_url`` is the org's own landing page, on the org's host (verified
    custom domain when it has one). Without it the mail told someone their
    permissions had changed and then gave them nowhere to go.
    """
    safe_username = html.escape(username)
    safe_org_name = html.escape(org_name)
    safe_role_name = html.escape(new_role_name)

    heading = t(lang, "role_changed.heading")
    body_1 = t(
        lang, "role_changed.body_1",
        username=safe_username, org_name=safe_org_name, role=safe_role_name,
    )
    body_2 = t(lang, "role_changed.body_2")

    cta_html = ""
    if cta_url:
        cta_html = (
            f'<a href="{html.escape(cta_url)}" style="{STYLES["button"]}">'
            f'{t(lang, "role_changed.cta", org_name=safe_org_name)}</a>'
        )

    body_content = f"""
        <h1 style="{STYLES['h1']}">{heading}</h1>
        <p style="{STYLES['p']}">
            {body_1}
        </p>
        <p style="{STYLES['p']}">
            {body_2}
        </p>
        {cta_html}
    """

    return _send_notification_email(
        to=email,
        subject=t(lang, "role_changed.subject", org_name=safe_org_name),
        body=_email_layout(
            title=heading,
            body_content=body_content,
            footer_note=t(lang, "role_changed.footer", org_name=safe_org_name),
            logo_html=_logo_or_brand(logo_url, org_name),
        ),
        sender_name=sender_name,
    )


def send_email_verification_email(
    token: str,
    user: UserRead,
    organization: OrganizationRead | None,
    email: EmailStr,
    base_url: str,
    lang: str = "en",
    sender_name: str | None = None,
    logo_url: str | None = None,
):
    """
    Send email verification email with verification link.

    Args:
        token: Verification token
        user: User receiving the email
        organization: Organization context (can be None for no-org signups)
        email: Email address to send to
        base_url: Base URL for constructing the verification link
        lang: ISO 639-1 language code for email content (defaults to 'en')

    Returns:
        Boolean indicating if email was sent successfully
    """
    safe_username = html.escape(user.username)
    safe_token = quote(token, safe='')
    safe_user_uuid = quote(user.user_uuid, safe='')
    org_uuid = organization.org_uuid if organization else "none"
    safe_org_uuid = quote(org_uuid, safe='')
    verification_url = f"{base_url}/verify-email?token={safe_token}&amp;user={safe_user_uuid}&amp;org={safe_org_uuid}"

    heading = t(lang, "email_verification.heading")
    body_text = t(lang, "email_verification.body", username=safe_username)
    cta = t(lang, "email_verification.cta")
    copy_paste = t(lang, "email_verification.copy_paste")

    body_content = f"""
        <h1 style="{STYLES['h1']}">{heading}</h1>
        <p style="{STYLES['p']}">
            {body_text}
        </p>
        <a href="{verification_url}" style="{STYLES['button']}">
            {cta}
        </a>
        <p style="{STYLES['link_text']}">
            {copy_paste}<br />{verification_url}
        </p>
    """

    return send_email(
        to=email,
        subject=t(lang, "email_verification.subject"),
        body=_email_layout(
            title=heading,
            body_content=body_content,
            footer_note=t(lang, "email_verification.footer"),
            logo_html=_logo_or_brand(
                logo_url, getattr(organization, "name", None)
            ),
        ),
        sender_name=sender_name,
    )


def send_nudge_email(
    nudge_id: str,
    email: EmailStr,
    org_name: str,
    cta_url: str,
    unsubscribe_url: str,
    lang: str = "en",
    logo_url: str | None = None,
    has_cta: bool = True,
    track: str = "",
    stats: list[tuple[str, int]] | None = None,
    sender_name: str | None = None,
    **copy_vars,
):
    """Send one lifecycle nudge.

    Generic over the catalog: the nudge id selects its copy from the
    ``nudge.<id>.*`` namespace, so adding a nudge never means adding a function
    here. ``copy_vars`` fills the placeholders that nudge's strings declare
    (course name, plan name, and so on) — every value is escaped before it
    reaches the template.

    ``track`` selects the illustration. It is drawn as a table mosaic rather
    than an image because Gmail strips inline SVG and both Gmail and Outlook
    block ``data:`` URIs, so an embedded picture would render as a blank gap
    for a large share of readers.

    Unlike transactional mail this always carries an unsubscribe link and the
    matching ``List-Unsubscribe`` headers. Gmail and Outlook expect them on
    bulk mail, and without them a run at any real volume puts the sending
    domain at risk.

    Failures are swallowed via ``_send_notification_email``: a nudge nobody
    asked for must never be the reason a batch job dies.
    """
    safe_org_name = html.escape(org_name)
    raw_vars = {key: str(value) for key, value in copy_vars.items() if value is not None}
    raw_vars.setdefault("org_name", org_name)
    safe_vars = {key: html.escape(value) for key, value in raw_vars.items()}

    heading = t(lang, f"nudge.{nudge_id}.heading", **safe_vars)
    body_text = t(lang, f"nudge.{nudge_id}.body", **safe_vars)
    # The subject is plain text, not HTML: escaping it would deliver
    # "Maths &amp; Physics" to the inbox, and ampersands in course names are
    # common enough that this is not an edge case.
    subject = t(lang, f"nudge.{nudge_id}.subject", **raw_vars)

    from src.services.nudges.illustrations import render_illustration

    stat_html = _stat_strip(
        [(t(lang, f"nudge.stat.{key}"), value) for key, value in (stats or [])]
    )

    body_content = f"""
        {render_illustration(track)}
        <h1 style="{STYLES['h1']}">{heading}</h1>
        <p style="{STYLES['p']}">
            {body_text}
        </p>
        {stat_html}"""
    if has_cta and cta_url:
        cta = t(lang, f"nudge.{nudge_id}.cta", **safe_vars)
        body_content += f"""
        <a href="{html.escape(cta_url)}" style="{STYLES['button']}">
            {cta}
        </a>
        <p style="{STYLES['link_text']}">{html.escape(cta_url)}</p>
    """

    # The preheader is the body's opening sentence rather than a thirty-first
    # set of translated strings. It is already localised, and it gives the
    # inbox list a second line of information instead of echoing the subject.
    preheader = _first_sentence(t(lang, f"nudge.{nudge_id}.body", **raw_vars))

    headers: dict[str, str] = {}
    if unsubscribe_url:
        headers["List-Unsubscribe"] = f"<{unsubscribe_url}>"
        headers["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"

    # Several nudges invite a reply. Without this they would arrive from the
    # no-reply sender and the invitation would be a lie.
    reply_to = _reply_to_address()
    if reply_to:
        headers["Reply-To"] = reply_to

    return _send_notification_email(
        to=email,
        subject=subject,
        body=_email_layout(
            title=heading,
            body_content=body_content,
            footer_note=t(lang, "nudge.common.footer", org_name=safe_org_name),
            logo_html=_logo_or_brand(logo_url, org_name),
            unsubscribe_url=unsubscribe_url,
            unsubscribe_label=t(lang, "nudge.common.unsubscribe"),
            preheader=preheader,
        ),
        headers=headers or None,
        sender_name=sender_name,
    )
