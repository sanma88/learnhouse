"""Tests for src/services/users/emails.py."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.db.organizations import OrganizationRead
from src.db.users import UserRead
from src.services.email.branding import email_brand
from src.services.users.emails import (
    send_account_creation_email,
    send_account_deleted_email,
    send_email_verification_email,
    send_invitation_email,
    send_org_created_email,
    send_org_deleted_email,
    send_org_join_email,
    send_password_reset_email,
    send_password_reset_email_platform,
    send_role_changed_email,
)


def _user(**overrides):
    data = dict(
        id=1,
        username="user<script>",
        first_name="User",
        last_name="Test",
        email="user@test.com",
        user_uuid="user_uuid",
        email_verified=True,
        avatar_image="",
        bio="",
    )
    data.update(overrides)
    return UserRead(**data)


def _org(**overrides):
    data = dict(
        id=1,
        name="Org & Co",
        slug="org",
        email="org@test.com",
        org_uuid="org_uuid",
        creation_date="2024-01-01",
        update_date="2024-01-01",
    )
    data.update(overrides)
    return OrganizationRead(**data)


class TestEmailsService:
    def test_lifecycle_confirmation_emails(self):
        with patch("src.services.users.emails.send_email", return_value=True) as send_email:
            assert send_org_created_email("a@test.com", "Org & Co", "https://learnhouse.io/home") is True
            created = send_email.call_args
            assert "Org &amp; Co" in created.kwargs["body"]  # name html-escaped
            assert "https://learnhouse.io/home" in created.kwargs["body"]  # CTA link
            assert "Org &amp; Co" in created.kwargs["subject"]

            assert send_org_deleted_email("a@test.com", "Org & Co") is True
            assert "deleted" in send_email.call_args.kwargs["subject"].lower()

            assert send_account_deleted_email("a@test.com", "user<script>") is True
            assert "deleted" in send_email.call_args.kwargs["subject"].lower()

    def test_send_account_creation_email_escapes_username(self):
        with patch("src.services.users.emails.send_email", return_value=True) as send_email:
            result = send_account_creation_email(_user(), "user@test.com")

        assert result is True
        body = send_email.call_args.kwargs["body"]
        assert "user&lt;script&gt;" in body
        assert "Get Started" in body

    def test_orgless_welcome_uses_cta_url_and_instance_branding(self):
        with patch("src.services.users.emails.send_email", return_value=True) as send_email:
            send_account_creation_email(
                _user(), "user@test.com", cta_url="https://platform.test/organizations"
            )
        call = send_email.call_args.kwargs
        assert "https://platform.test/organizations" in call["body"]
        # Org-less keeps the instance-branded subject + academy footer, no org logo.
        brand = email_brand()
        assert f"Welcome to {brand}" in call["subject"]
        assert brand in call["body"]
        assert "LearnHouse" not in call["body"]
        assert "<img" not in call["body"]

    def test_welcome_is_whitelabeled_when_org_supplied(self):
        with patch("src.services.users.emails.send_email", return_value=True) as send_email:
            send_account_creation_email(
                _user(),
                "user@test.com",
                cta_url="https://acme.test/home",
                org_name="Acme & Co",
                logo_url="https://api.test/content/orgs/org_uuid/logos/logo.png",
            )
        call = send_email.call_args.kwargs
        # Subject/body name the org (html-escaped), not the instance.
        brand = email_brand()
        assert "Acme &amp; Co" in call["subject"]
        assert f"Welcome to {brand}" not in call["subject"]
        assert "Acme &amp; Co" in call["body"]
        # Org logo replaces the mark; academy link is gone; powered-by remains.
        assert '<img src="https://api.test/content/orgs/org_uuid/logos/logo.png"' in call["body"]
        assert f"Powered by {brand}" in call["body"]
        assert "LearnHouse" not in call["body"]
        assert "https://acme.test/home" in call["body"]

    def test_whitelabel_without_logo_falls_back_to_the_instance_mark(self):
        with patch("src.services.users.emails.send_email", return_value=True) as send_email:
            send_account_creation_email(
                _user(), "user@test.com", org_name="Acme", logo_url=None
            )
        call = send_email.call_args.kwargs
        # No org logo → the instance mark, but the text stays white-labeled.
        # No public URL is resolvable under test, so the mark degrades to text.
        brand = email_brand()
        assert "<img" not in call["body"]
        assert f">{brand}</span>" in call["body"]
        assert "Acme" in call["subject"]
        assert f"Powered by {brand}" in call["body"]

    def test_role_changed_email_links_back_to_the_org(self):
        """Telling someone their permissions changed is useless without a way
        to go use them."""
        with patch("src.services.users.emails.send_email", return_value=True) as send_email:
            send_role_changed_email(
                email="user@test.com",
                username="learner",
                org_name="Acme & Co",
                new_role_name="Admin",
                cta_url="https://learn.acme.test",
            )
        body = send_email.call_args.kwargs["body"]
        assert 'href="https://learn.acme.test"' in body
        assert "Acme &amp; Co" in body

    def test_role_changed_email_without_a_link_renders_no_button(self):
        with patch("src.services.users.emails.send_email", return_value=True) as send_email:
            send_role_changed_email(
                email="user@test.com",
                username="learner",
                org_name="Acme",
                new_role_name="Admin",
            )
        assert "<a href" not in send_email.call_args.kwargs["body"]

    def test_org_join_email_is_whitelabeled_and_links_to_the_org(self):
        with patch("src.services.users.emails.send_email", return_value=True) as send_email:
            assert send_org_join_email(
                email="user@test.com",
                username="user<script>",
                org_name="Acme & Co",
                cta_url="https://acme.test/home",
                logo_url="https://api.test/content/orgs/org_uuid/logos/logo.png",
            ) is True
        call = send_email.call_args.kwargs
        # Named after the org, with the org's own logo, not the LearnHouse mark.
        assert "Acme &amp; Co" in call["subject"]
        assert '<img src="https://api.test/content/orgs/org_uuid/logos/logo.png"' in call["body"]
        # The whole point of the email: a working way back into the org.
        assert "https://acme.test/home" in call["body"]
        # Hostile username/org names are escaped, never rendered as markup.
        assert "<script>" not in call["body"]

    def test_org_join_email_falls_back_to_the_instance_mark_without_logo(self):
        with patch("src.services.users.emails.send_email", return_value=True) as send_email:
            send_org_join_email(
                email="user@test.com",
                username="learner",
                org_name="Acme",
                cta_url="https://acme.test/home",
            )
        call = send_email.call_args.kwargs
        assert "<img" not in call["body"]
        assert f">{email_brand()}</span>" in call["body"]

    def test_org_join_email_translates(self):
        with patch("src.services.users.emails.send_email", return_value=True) as send_email:
            send_org_join_email(
                email="user@test.com",
                username="learner",
                org_name="Acme",
                cta_url="https://acme.test/home",
                lang="fr",
            )
        call = send_email.call_args.kwargs
        assert "Bienvenue" in call["subject"]

    def test_send_password_reset_email_variants_encode_params(self):
        with patch("src.services.users.emails.send_email", return_value=True) as send_email:
            send_password_reset_email(
                "code 123",
                _user(),
                _org(),
                "user+tag@test.com",
                "https://app.test",
            )
            send_password_reset_email_platform(
                "code 123",
                _user(),
                "user+tag@test.com",
                "https://app.test",
            )

        first_body = send_email.call_args_list[0].kwargs["body"]
        second_body = send_email.call_args_list[1].kwargs["body"]
        # Both variants now point at the real .io route `/reset` (the platform
        # variant previously used `/reset-password`, which 404s on .io).
        assert "/reset?email=user%2Btag%40test.com&amp;resetCode=code%20123" in first_body
        assert "/reset?email=user%2Btag%40test.com&amp;resetCode=code%20123" in second_body

    def test_send_invitation_role_change_and_verification_email(self):
        with patch("src.services.users.emails.send_email", return_value=True) as send_email:
            send_invitation_email(
                "invitee@test.com",
                "Org & Co",
                "owner<script>",
                "https://app.test/signup",
                invite_code="INV-123",
            )
            send_role_changed_email(
                "invitee@test.com",
                "member<script>",
                "Org & Co",
                "Admin",
            )
            send_email_verification_email(
                "token 123",
                _user(),
                _org(),
                "invitee@test.com",
                "https://app.test",
            )

        invite_body = send_email.call_args_list[0].kwargs["body"]
        role_body = send_email.call_args_list[1].kwargs["body"]
        verification_body = send_email.call_args_list[2].kwargs["body"]
        assert "INV-123" in invite_body
        assert "@owner&lt;script&gt;" in invite_body
        assert "member&lt;script&gt;" in role_body
        assert "verify-email?token=token%20123&amp;user=user_uuid&amp;org=org_uuid" in verification_body

    def test_send_invitation_email_without_invite_code(self):
        with patch("src.services.users.emails.send_email", return_value=True) as send_email:
            send_invitation_email(
                "invitee@test.com",
                "Test Org",
                "inviter",
                "https://app.test/signup",
            )
        invite_body = send_email.call_args.kwargs["body"]
        assert "Click the button below" in invite_body

    def test_send_emails_in_french_when_lang_is_fr(self):
        with patch("src.services.users.emails.send_email", return_value=True) as send_email:
            send_invitation_email(
                "invitee@test.com",
                "Org & Co",
                "owner",
                "https://app.test/signup",
                invite_code="INV-123",
                lang="fr",
            )
            send_password_reset_email(
                "abcd1234",
                _user(),
                _org(),
                "user@test.com",
                "https://app.test",
                lang="fr",
            )
            send_role_changed_email(
                "user@test.com",
                "member",
                "Org & Co",
                "Admin",
                lang="fr",
            )

        invite_call = send_email.call_args_list[0].kwargs
        reset_call = send_email.call_args_list[1].kwargs
        role_call = send_email.call_args_list[2].kwargs

        assert "Vous êtes invité" in invite_call["body"]
        assert "Vous êtes invité à rejoindre Org &amp; Co" == invite_call["subject"]
        assert "Réinitialisez votre mot de passe" in reset_call["body"]
        assert "Réinitialisez votre mot de passe" == reset_call["subject"]
        assert "Votre rôle a été mis à jour" in role_call["body"]

    def test_send_emails_falls_back_to_english_for_unknown_lang(self):
        with patch("src.services.users.emails.send_email", return_value=True) as send_email:
            send_invitation_email(
                "invitee@test.com",
                "Org",
                "owner",
                "https://app.test/signup",
                lang="xx",
            )
        body = send_email.call_args.kwargs["body"]
        assert "You've been invited" in body


class TestNotificationEmailResilience:
    """Lifecycle mail must never take the request down with it."""

    def test_notification_email_failure_does_not_propagate(self):
        from fastapi import HTTPException

        with patch(
            "src.services.users.emails.send_email",
            side_effect=HTTPException(status_code=503, detail="Email service temporarily unavailable"),
        ):
            # A signup whose welcome email fails still returns — the account is
            # already created, so a dead mail provider must not 5xx the caller.
            assert send_account_creation_email(_user(), "user@test.com") is False

    def test_password_reset_email_still_raises(self):
        from fastapi import HTTPException

        from src.services.users.emails import send_password_reset_email

        with patch(
            "src.services.users.emails.send_email",
            side_effect=HTTPException(status_code=503, detail="down"),
        ):
            with pytest.raises(HTTPException):
                send_password_reset_email(
                    "code 123",
                    _user(),
                    _org(),
                    "user@test.com",
                    "https://app.test",
                )


class TestResendTransientRetry:
    def test_timeout_is_retried_once_then_succeeds(self, monkeypatch):
        from src.services.email import utils as email_utils

        monkeypatch.setattr(email_utils.time, "sleep", lambda _s: None)
        calls = []

        def flaky(payload):
            calls.append(payload)
            if len(calls) == 1:
                raise RuntimeError("Read timed out. (read timeout=30)")
            return {"id": "sent"}

        monkeypatch.setattr(email_utils.resend.Emails, "send", staticmethod(flaky))

        result = email_utils._send_email_resend(
            "LearnHouse <no-reply@test>", "user@test.com", "hi", "<p>hi</p>",
            SimpleNamespace(resend_api_key="key"),
        )

        assert result == {"id": "sent"}
        assert len(calls) == 2

    def test_quota_error_is_not_retried(self, monkeypatch):
        from fastapi import HTTPException

        from src.services.email import utils as email_utils

        monkeypatch.setattr(email_utils.time, "sleep", lambda _s: None)
        calls = []

        def over_quota(payload):
            calls.append(payload)
            raise RuntimeError("You have reached your daily email sending quota.")

        monkeypatch.setattr(email_utils.resend.Emails, "send", staticmethod(over_quota))

        with pytest.raises(HTTPException) as exc_info:
            email_utils._send_email_resend(
                "LearnHouse <no-reply@test>", "user@test.com", "hi", "<p>hi</p>",
                SimpleNamespace(resend_api_key="key"),
            )

        assert exc_info.value.status_code == 503
        assert len(calls) == 1  # a quota error will not clear on retry


class TestSenderNameRouting:
    """Which emails carry an organization's display name, and which never do.

    Org-scoped mail is *about* one organization, so it may go out under that
    organization's name. Platform mail (org-less signup, platform password
    reset, account deletion) must not borrow one — the recipient has no
    relationship with any org in that moment.
    """

    def test_org_scoped_emails_forward_the_org_name(self):
        with patch("src.services.users.emails.send_email", return_value=True) as sent:
            send_password_reset_email(
                generated_reset_code="code",
                user=_user(),
                organization=_org(),
                email="user@test.com",
                base_url="https://org.test",
                sender_name="Acme Academy",
            )
            assert sent.call_args.kwargs["sender_name"] == "Acme Academy"

            send_invitation_email(
                email="user@test.com",
                org_name="Org & Co",
                inviter_username="admin",
                signup_url="https://org.test/signup",
                sender_name="Acme Academy",
            )
            assert sent.call_args.kwargs["sender_name"] == "Acme Academy"

            send_email_verification_email(
                token="tok",
                user=_user(),
                organization=_org(),
                email="user@test.com",
                base_url="https://org.test",
                sender_name="Acme Academy",
            )
            assert sent.call_args.kwargs["sender_name"] == "Acme Academy"

    def test_org_scoped_notifications_forward_the_org_name(self):
        with patch(
            "src.services.users.emails.send_email", return_value=True
        ) as sent:
            send_org_join_email(
                email="user@test.com",
                username="user",
                org_name="Org & Co",
                cta_url="https://org.test",
                sender_name="Acme Academy",
            )
            assert sent.call_args.kwargs["sender_name"] == "Acme Academy"

            send_role_changed_email(
                email="user@test.com",
                username="user",
                org_name="Org & Co",
                new_role_name="Admin",
                sender_name="Acme Academy",
            )
            assert sent.call_args.kwargs["sender_name"] == "Acme Academy"

    def test_platform_emails_send_no_org_name(self):
        with patch("src.services.users.emails.send_email", return_value=True) as sent:
            send_password_reset_email_platform(
                generated_reset_code="code",
                user=_user(),
                email="user@test.com",
                base_url="https://platform.test",
            )
            assert "sender_name" not in sent.call_args.kwargs

            send_account_deleted_email("user@test.com", "user")
            assert "sender_name" not in sent.call_args.kwargs

    def test_org_scoped_default_is_still_no_name(self):
        """Callers without org context keep the platform default by omission,
        so nothing had to change at the platform-scoped call sites."""
        with patch("src.services.users.emails.send_email", return_value=True) as sent:
            send_invitation_email(
                email="user@test.com",
                org_name="Org & Co",
                inviter_username="admin",
                signup_url="https://org.test/signup",
            )
            assert sent.call_args.kwargs["sender_name"] is None


class TestBrandMark:
    """Every email carries a mark, and it is this instance's.

    The layout's default was ``None``, interpolated straight into the header
    div, so the nine emails that never passed a logo — password reset,
    invitation, verification, the lifecycle confirmations, the magic link —
    rendered the word "None" where the logo belongs.
    """

    def test_layout_without_a_logo_renders_the_instance_mark(self):
        from src.services.users.emails import _email_layout

        html = _email_layout("Title", "<p>body</p>")
        assert "None" not in html
        assert email_brand() in html

    def test_layout_accepts_an_explicit_no_mark(self):
        """`""` is how a caller asks for a bare header; only the default changed."""
        from src.services.users.emails import _email_layout

        html = _email_layout("Title", "<p>body</p>", logo_html="")
        assert "None" not in html
        assert "<img" not in html

    def test_every_transactional_email_carries_the_mark(self):
        """Sweeps the senders that pass no logo of their own."""
        brand = email_brand()
        with patch("src.services.users.emails.send_email", return_value=True) as sent:
            calls = [
                lambda: send_org_created_email("a@test.com", "Org", "https://o.test"),
                lambda: send_org_deleted_email("a@test.com", "Org"),
                lambda: send_account_deleted_email("a@test.com", "user"),
                lambda: send_password_reset_email(
                    "code", _user(), _org(), "a@test.com", "https://o.test"
                ),
                lambda: send_password_reset_email_platform(
                    "code", _user(), "a@test.com", "https://o.test"
                ),
                lambda: send_invitation_email(
                    "a@test.com", "Org", "admin", "https://o.test/signup"
                ),
                lambda: send_role_changed_email(
                    email="a@test.com", username="u", org_name="Org", new_role_name="Admin"
                ),
                lambda: send_email_verification_email(
                    "tok", _user(), _org(), "a@test.com", "https://o.test"
                ),
            ]
            for call in calls:
                call()
                body = sent.call_args.kwargs["body"]
                assert "None" not in body, sent.call_args.kwargs["subject"]
                assert brand in body, sent.call_args.kwargs["subject"]
                assert "LearnHouse" not in body

    def test_an_org_logo_still_wins_over_the_instance_mark(self):
        """White labelling is the point of the parameter — only the fallback moved."""
        with patch("src.services.users.emails.send_email", return_value=True) as sent:
            send_org_join_email(
                email="a@test.com",
                username="learner",
                org_name="Acme",
                cta_url="https://acme.test/home",
                logo_url="https://api.test/content/orgs/o/logos/acme.png",
            )
        body = sent.call_args.kwargs["body"]
        assert '<img src="https://api.test/content/orgs/o/logos/acme.png"' in body
        assert body.count("<img") == 1


class TestEmailBrandResolution:
    """Order: the mail brand, then site_name, then the built-in default."""

    def _config(self, sender_name, site_name="Site Name"):
        return SimpleNamespace(
            mailing_config=SimpleNamespace(system_email_sender_name=sender_name),
            site_name=site_name,
        )

    def test_mail_brand_wins(self):
        from src.services.email.branding import email_brand as brand

        with patch(
            "config.config.get_learnhouse_config",
            return_value=self._config("Mail Brand"),
        ):
            assert brand() == "Mail Brand"

    def test_falls_back_to_site_name_when_unset(self):
        from src.services.email.branding import email_brand as brand

        with patch(
            "config.config.get_learnhouse_config", return_value=self._config(None)
        ):
            assert brand() == "Site Name"

    def test_empty_mail_brand_does_not_leave_the_body_unbranded(self):
        """`""` means "no From display name", not "no name in the heading"."""
        from src.services.email.branding import email_brand as brand

        with patch(
            "config.config.get_learnhouse_config", return_value=self._config("")
        ):
            assert brand() == "Site Name"

    def test_unreadable_config_still_yields_a_brand(self):
        from src.services.email.branding import DEFAULT_EMAIL_BRAND
        from src.services.email.branding import email_brand as brand

        with patch("config.config.get_learnhouse_config", side_effect=RuntimeError):
            assert brand() == DEFAULT_EMAIL_BRAND
        assert "LearnHouse" not in DEFAULT_EMAIL_BRAND


class TestOrgLogoOnOrgScopedTransactionalMail:
    """The four transactional mails that spoke the org's language but wore ours.

    Password reset, invitation, role change and address verification already
    resolved the org's default language and its ``From`` display name — and then
    headed the message with the instance wordmark. A message white-labeled in
    the subject, the body and the sender but not the mark reads like a
    forwarded one, so each now takes a ``logo_url``.

    The fallback is what those mails did unconditionally before: no logo on the
    org (or no resolvable media host) still renders the instance mark, never an
    empty header and never the string "None".
    """

    LOGO = "https://api.test/content/orgs/org_uuid/logos/acme.png"

    def _senders_with_logo(self, logo_url):
        return [
            (
                "password_reset",
                lambda: send_password_reset_email(
                    "code", _user(), _org(), "a@test.com", "https://o.test",
                    logo_url=logo_url,
                ),
            ),
            (
                "invitation",
                lambda: send_invitation_email(
                    "a@test.com", "Org & Co", "admin", "https://o.test/signup",
                    logo_url=logo_url,
                ),
            ),
            (
                "role_changed",
                lambda: send_role_changed_email(
                    email="a@test.com", username="u", org_name="Org & Co",
                    new_role_name="Admin", logo_url=logo_url,
                ),
            ),
            (
                "email_verification",
                lambda: send_email_verification_email(
                    "tok", _user(), _org(), "a@test.com", "https://o.test",
                    logo_url=logo_url,
                ),
            ),
        ]

    def test_each_one_renders_the_orgs_logo(self):
        with patch("src.services.users.emails.send_email", return_value=True) as sent:
            for name, call in self._senders_with_logo(self.LOGO):
                call()
                body = sent.call_args.kwargs["body"]
                assert f'<img src="{self.LOGO}"' in body, name
                assert "black_logo.png" not in body, name
                assert body.count("<img") == 1, name

    def test_each_one_falls_back_to_the_instance_mark_without_a_logo(self):
        brand = email_brand()
        with patch("src.services.users.emails.send_email", return_value=True) as sent:
            for name, call in self._senders_with_logo(None):
                call()
                body = sent.call_args.kwargs["body"]
                assert "None" not in body, name
                assert brand in body, name

    def test_the_alt_text_is_the_org_name_not_an_empty_string(self):
        """A client that strips images shows the alt; it must name the org."""
        with patch("src.services.users.emails.send_email", return_value=True) as sent:
            send_invitation_email(
                "a@test.com", "Org & Co", "admin", "https://o.test/signup",
                logo_url=self.LOGO,
            )
        assert 'alt="Org &amp; Co"' in sent.call_args.kwargs["body"]

    def test_a_logo_with_no_org_name_still_gets_a_usable_alt(self):
        """`_logo_or_brand` falls back to the instance name rather than "".

        Reachable through the magic link, which may hold a logo for an org
        whose name could not be read.
        """
        from src.services.users.emails import _logo_or_brand

        assert f'alt="{email_brand()}"' in _logo_or_brand(self.LOGO, None)


class TestLifecycleConfirmationsSpeakTheReadersLanguage:
    """`org_created` / `org_deleted` stay platform-BRANDED but not English-only.

    Both confirm an action to the acting admin, and both are deliberately not
    white-labeled: the org has no logo yet in one case and no longer exists in
    the other. Language is a separate question with a different answer — the
    reader's — and both senders already accepted a `lang`; nobody passed one.
    """

    def test_org_created_translates(self):
        with patch("src.services.users.emails.send_email", return_value=True) as sent:
            send_org_created_email("a@test.com", "Acme", "https://o.test/dash", lang="fr")
        kwargs = sent.call_args.kwargs
        assert kwargs["subject"] == "Votre organisation Acme est prête"
        assert "Ouvrir le tableau de bord" in kwargs["body"]

    def test_org_deleted_translates(self):
        with patch("src.services.users.emails.send_email", return_value=True) as sent:
            send_org_deleted_email("a@test.com", "Acme", lang="fr")
        assert sent.call_args.kwargs["subject"] == "Votre organisation Acme a été supprimée"
