"""`LEARNHOUSE_EMAIL_LOGO_URL` outranks an organization's own logo.

An org logo is drawn for the INTERFACE. campus.hi-ha.be's is a white PNG, which
is correct on the product's dark chrome and invisible on the white card every
email is laid out on. The operator needs a mark dedicated to email without
touching the one the app shows, and this variable — which already existed, and
which the instance mark already honored — is it.

What these tests pin, in order:

1. the resolution order (variable → org logo → instance mark);
2. that the variable only outranks an org logo in ``tenancy == "single"``, so a
   multi-tenant deployment cannot have one instance-wide image silently replace
   every customer's branding;
3. that a deployment which does NOT set the variable is completely unaffected —
   every one of the eight org-scoped emails still renders the org's own logo.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

# Imported here, not inside a test: importing this module pulls in the security
# layer, which reads the real configuration at import time and would explode
# under the stub config the tests patch in.
import src.services.auth.magic_login as ml
from src.db.organizations import OrganizationRead
from src.db.users import UserRead
from src.services.email.branding import email_brand
from src.services.users.emails import (
    _brand_logo_html,
    _email_logo_overrides_org_logo,
    _email_logo_url,
    _logo_or_brand,
    send_account_creation_email,
    send_email_verification_email,
    send_invitation_email,
    send_nudge_email,
    send_org_join_email,
    send_password_reset_email,
    send_role_changed_email,
)

ENV = "LEARNHOUSE_EMAIL_LOGO_URL"
MAIL_LOGO = "https://campus.hi-ha.be/hiha-logo-email.png"
ORG_LOGO = "https://campus.hi-ha.be/content/orgs/org_uuid/logos/white.png"


def _config(tenancy="single"):
    """Only what the logo path reads: the tenancy, plus what the instance mark
    resolves its own URL from."""
    return SimpleNamespace(
        hosting_config=SimpleNamespace(
            tenancy=tenancy, domain="campus.hi-ha.be", ssl=True
        )
    )


def _patched_config(tenancy="single"):
    return patch("config.config.get_learnhouse_config", return_value=_config(tenancy))


def _user(**overrides):
    data = dict(
        id=1,
        username="mario",
        first_name="Mario",
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


class TestTheVariableIsReadInExactlyOneWay:
    """One helper answers "is it set?", so no two call sites can disagree."""

    def test_unset_reads_as_empty(self, monkeypatch):
        monkeypatch.delenv(ENV, raising=False)
        assert _email_logo_url() == ""

    def test_whitespace_is_not_a_url(self, monkeypatch):
        """A blank value used to reach the header as ``<img src="   ">``.

        Only reachable by explicitly setting the variable to whitespace, so no
        deployment that leaves it alone can notice; a broken image where the
        mark belongs is never what that was meant to express.
        """
        monkeypatch.setenv(ENV, "   ")
        assert _email_logo_url() == ""
        assert "<img" not in _brand_logo_html()

    def test_a_real_value_is_stripped_not_mangled(self, monkeypatch):
        monkeypatch.setenv(ENV, f"  {MAIL_LOGO}  ")
        assert _email_logo_url() == MAIL_LOGO


class TestWhenTheOverrideApplies:
    """The tenancy condition, which is the whole judgement call of this change."""

    def test_unset_never_overrides(self, monkeypatch):
        monkeypatch.delenv(ENV, raising=False)
        with _patched_config("single"):
            assert _email_logo_overrides_org_logo() is False

    def test_set_and_single_tenancy_overrides(self, monkeypatch):
        monkeypatch.setenv(ENV, MAIL_LOGO)
        with _patched_config("single"):
            assert _email_logo_overrides_org_logo() is True

    def test_set_but_multi_tenancy_does_not_override(self, monkeypatch):
        """An instance variable must not repaint every tenant's mail.

        In multi-tenancy the org logo belongs to a customer, not to the
        operator who set the variable.
        """
        monkeypatch.setenv(ENV, MAIL_LOGO)
        with _patched_config("multi"):
            assert _email_logo_overrides_org_logo() is False

    def test_unreadable_config_degrades_to_the_old_behaviour(self, monkeypatch):
        """A config failure must not be what changes who a mail is branded as."""
        monkeypatch.setenv(ENV, MAIL_LOGO)
        with patch("config.config.get_learnhouse_config", side_effect=RuntimeError):
            assert _email_logo_overrides_org_logo() is False
        with patch("config.config.get_learnhouse_config", side_effect=RuntimeError):
            assert ORG_LOGO in _logo_or_brand(ORG_LOGO, "Org & Co")


class TestTheResolutionOrder:
    """Variable → org logo → instance mark, proven one step at a time."""

    def test_1_the_variable_wins_over_an_org_logo(self, monkeypatch):
        monkeypatch.setenv(ENV, MAIL_LOGO)
        with _patched_config("single"):
            html = _logo_or_brand(ORG_LOGO, "Org & Co")
        assert MAIL_LOGO in html
        assert ORG_LOGO not in html
        assert f'alt="{email_brand()}"' in html

    def test_2_without_the_variable_the_org_logo_wins(self, monkeypatch):
        monkeypatch.delenv(ENV, raising=False)
        with _patched_config("single"):
            html = _logo_or_brand(ORG_LOGO, "Org & Co")
        assert ORG_LOGO in html
        assert 'alt="Org &amp; Co"' in html

    def test_3_with_neither_the_instance_mark_is_used(self, monkeypatch):
        monkeypatch.delenv(ENV, raising=False)
        with _patched_config("single"):
            html = _logo_or_brand(None, "Org & Co")
            # Compared under the same config: the instance mark derives its own
            # URL from the hosting domain, so the two calls have to see one.
            assert html == _brand_logo_html()
        assert ORG_LOGO not in html
        assert "black_logo.png" in html

    def test_the_variable_also_fills_the_no_org_logo_slot(self, monkeypatch):
        """Step 3 has always honored it; the point is that 1 and 3 now agree.

        Before this change the same variable produced two different marks on
        two emails of the same deployment, depending only on whether the org
        happened to have uploaded a logo.
        """
        monkeypatch.setenv(ENV, MAIL_LOGO)
        with _patched_config("single"):
            with_logo = _logo_or_brand(ORG_LOGO, "Org & Co")
            without_logo = _logo_or_brand(None, "Org & Co")
        assert with_logo == without_logo == _brand_logo_html()
        assert MAIL_LOGO in with_logo


def _send_all_eight(logo_url):
    """Every send site that resolves an ORG logo. Returns {name: body}.

    The census is the point: a ninth site added later that open-codes its own
    logo resolution will not appear here, and the sweep below will not cover
    it. ``send_magic_login_email`` lives in another module and is the one that
    used to resolve its logo inline.
    """
    bodies = {}
    with patch("src.services.users.emails.send_email", return_value=True) as sent:
        send_invitation_email(
            "a@test.com", "Org & Co", "admin", "https://o.test/signup",
            logo_url=logo_url,
        )
        bodies["invitation"] = sent.call_args.kwargs["body"]

        send_password_reset_email(
            "code", _user(), _org(), "a@test.com", "https://o.test",
            logo_url=logo_url,
        )
        bodies["password_reset"] = sent.call_args.kwargs["body"]

        send_email_verification_email(
            "tok", _user(), _org(), "a@test.com", "https://o.test",
            logo_url=logo_url,
        )
        bodies["email_verification"] = sent.call_args.kwargs["body"]

        send_role_changed_email(
            "a@test.com", "mario", "Org & Co", "Maintainer", logo_url=logo_url,
        )
        bodies["role_changed"] = sent.call_args.kwargs["body"]

        send_account_creation_email(
            _user(), "a@test.com", org_name="Org & Co", logo_url=logo_url,
        )
        bodies["account_creation"] = sent.call_args.kwargs["body"]

        send_org_join_email(
            "a@test.com", "mario", "Org & Co", "https://o.test",
            logo_url=logo_url,
        )
        bodies["org_join"] = sent.call_args.kwargs["body"]

        send_nudge_email(
            "n1", "a@test.com", "Org & Co", "https://o.test",
            "https://o.test/unsub", logo_url=logo_url,
        )
        bodies["nudge"] = sent.call_args.kwargs["body"]

    with patch.object(ml, "send_email", return_value=True) as magic:
        ml.send_magic_login_email(
            _user(), "a@test.com", "https://o.test", "tok",
            org_name="Org & Co", logo_url=logo_url,
        )
        bodies["magic_login"] = magic.call_args.kwargs["body"]

    assert len(bodies) == 8
    return bodies


EIGHT = [
    "invitation",
    "password_reset",
    "email_verification",
    "role_changed",
    "account_creation",
    "org_join",
    "nudge",
    "magic_login",
]


class TestEveryOrgScopedEmailObeysTheOverride:
    """The sweep. Eight send sites, none allowed to keep its own answer."""

    @pytest.mark.parametrize("name", EIGHT)
    def test_the_mail_logo_replaces_the_org_logo(self, name, monkeypatch):
        monkeypatch.setenv(ENV, MAIL_LOGO)
        with _patched_config("single"):
            bodies = _send_all_eight(ORG_LOGO)
        assert MAIL_LOGO in bodies[name], name
        assert ORG_LOGO not in bodies[name], name

    @pytest.mark.parametrize("name", EIGHT)
    def test_without_the_variable_nothing_moves(self, name, monkeypatch):
        """The no-regression half: an untouched deployment keeps its behaviour."""
        monkeypatch.delenv(ENV, raising=False)
        with _patched_config("single"):
            bodies = _send_all_eight(ORG_LOGO)
        assert ORG_LOGO in bodies[name], name
        assert MAIL_LOGO not in bodies[name], name

    @pytest.mark.parametrize("name", EIGHT)
    def test_multi_tenancy_keeps_the_white_label(self, name, monkeypatch):
        monkeypatch.setenv(ENV, MAIL_LOGO)
        with _patched_config("multi"):
            bodies = _send_all_eight(ORG_LOGO)
        assert ORG_LOGO in bodies[name], name
        assert MAIL_LOGO not in bodies[name], name

    @pytest.mark.parametrize("name", EIGHT)
    def test_no_org_logo_falls_back_to_the_mail_logo(self, name, monkeypatch):
        monkeypatch.setenv(ENV, MAIL_LOGO)
        with _patched_config("single"):
            bodies = _send_all_eight(None)
        assert MAIL_LOGO in bodies[name], name
