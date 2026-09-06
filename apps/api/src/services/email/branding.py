"""The name this instance signs its outgoing mail with.

Upstream keeps three different answers to "what is this platform called?" on
the mail path: ``DEFAULT_SENDER_NAME`` in :mod:`src.services.email.sender`, the
``site_name`` used for page metadata, and the product name written into every
translated string. A fork inherits all three and rebrands whichever it happens
to notice — which is how an instance ends up sending one name in the ``From``
header, another in the heading, and a third in the footer of the same message.

This module is the single answer. Every part of an email that names the sender
resolves it here: the ``From`` display name, the header logo (with its ``alt``
and its text fallback), and the ``{brand}`` placeholder that the translated
copy formats in.

Resolution order, first non-empty wins:

1. ``mailing_config.system_email_sender_name`` — the
   ``LEARNHOUSE_SYSTEM_EMAIL_SENDER_NAME`` env var or the ``config.yaml`` key.
   This is the mail brand proper. It is deliberately a separate setting from
   ``site_name``: deployments suffix that one per environment ("… (local)",
   "… (test2)"), and that suffix must never reach a recipient's inbox.
2. ``site_name`` — for a deployment that never set a mail brand.
3. ``DEFAULT_EMAIL_BRAND`` — only when the configuration cannot be read at all,
   so that a mail still goes out branded rather than crashing or, worse,
   rendering the literal ``None``.

One deliberate divergence from ``format_sender``: an explicitly empty
``system_email_sender_name`` means "send with no ``From`` display name" there
(upstream semantics, preserved), but here it falls through to ``site_name``.
An empty display name is a legitimate header; an empty brand in the body is a
heading that reads "Sign in to ".
"""

from typing import Optional

from src.services.email.sender import DEFAULT_SENDER_NAME, sanitize_sender_name

# Last resort, used only when the configuration itself is unreadable. Shared with
# the ``From`` header's own fallback so the two can never drift apart: one
# literal, defined in sender.py.
DEFAULT_EMAIL_BRAND = DEFAULT_SENDER_NAME


def email_brand() -> str:
    """Display name of this instance in outgoing mail. Never empty.

    Sanitized through ``sanitize_sender_name`` even for body use: the value is
    deployment-supplied text that lands both in an RFC 5322 header and in HTML,
    and stripping control characters and capping the length once, here, means no
    caller has to remember to.
    """
    try:
        from config.config import get_learnhouse_config

        config = get_learnhouse_config()
    except Exception:  # config unavailable — never crash a send over a name
        return DEFAULT_EMAIL_BRAND

    mailing = getattr(config, "mailing_config", None)
    candidates: tuple[Optional[str], ...] = (
        getattr(mailing, "system_email_sender_name", None),
        getattr(config, "site_name", None),
    )
    for candidate in candidates:
        name = sanitize_sender_name(candidate)
        if name:
            return name
    return DEFAULT_EMAIL_BRAND
