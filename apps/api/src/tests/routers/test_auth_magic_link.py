"""Endpoint tests for the passwordless magic-link flow in src/routers/auth.py.

Covers POST /magic-link/request (generic 200 responses that never reveal whether
an account exists, the org-policy short-circuit, and rate limiting) and POST
/magic-link/verify (session issuance, the second-factor branch, and the 410 for
an invalid/used link). Also pins that /auth/refresh carries a session's
provenance (amr / sorg) across rotation.
"""

from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from src.core.events.database import get_db_session
from src.db.organization_config import OrganizationConfig
from src.db.user_mfa import UserMFA
from src.db.users import AnonymousUser, User
from src.routers.auth import JWT_REFRESH_COOKIE_NAME, router as auth_router
from src.security.auth import decode_jwt, get_current_user
from src.security.session_context import AMR_CLAIM, SORG_CLAIM


@pytest.fixture
def app(db):
    app = FastAPI()
    app.include_router(auth_router, prefix="/api/v1/auth")
    app.dependency_overrides[get_db_session] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: AnonymousUser()
    yield app
    app.dependency_overrides.clear()


@pytest.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.fixture
async def verified_user(db):
    user = User(
        id=21,
        username="magic",
        first_name="Magic",
        last_name="User",
        email="magic@test.com",
        password="hashed_password",
        user_uuid="user_magic",
        email_verified=True,
        creation_date=str(datetime.now()),
        update_date=str(datetime.now()),
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def _enroll_confirmed(db, user_id):
    now = str(datetime.now())
    db.add(
        UserMFA(
            user_id=user_id,
            secret_encrypted="ciphertext",
            confirmed_at=now,
            creation_date=now,
            update_date=now,
        )
    )
    await db.commit()


async def _set_allowed_methods(db, org_id, methods):
    db.add(
        OrganizationConfig(
            org_id=org_id,
            config={
                "config_version": "2.0",
                "admin_toggles": {"security": {"allowed_auth_methods": methods}},
            },
            creation_date=str(datetime.now()),
            update_date=str(datetime.now()),
        )
    )
    await db.commit()


class TestMagicLinkRequest:
    async def test_unknown_email_is_generic_and_sends_nothing(self, client):
        with patch(
            "src.routers.auth.check_login_rate_limit", return_value=(True, None)
        ), patch(
            "src.services.auth.magic_login.send_magic_login_email"
        ) as send_mock, patch(
            "src.services.auth.magic_login.issue_magic_login_token", return_value="tok"
        ):
            response = await client.post(
                "/api/v1/auth/magic-link/request",
                json={"email": "nobody@test.com"},
            )
        assert response.status_code == 200
        assert "login link has been sent" in response.json()["detail"]
        send_mock.assert_not_called()

    async def test_known_email_sends_link(self, client, verified_user):
        with patch(
            "src.routers.auth.check_login_rate_limit", return_value=(True, None)
        ), patch(
            "src.services.auth.magic_login.issue_magic_login_token", return_value="tok"
        ), patch(
            "src.services.auth.magic_login.send_magic_login_email"
        ) as send_mock, patch(
            "src.services.email.utils.get_base_url_from_request",
            return_value="http://test",
        ):
            response = await client.post(
                "/api/v1/auth/magic-link/request",
                json={"email": verified_user.email},
            )
        assert response.status_code == 200
        send_mock.assert_called_once()

    async def test_org_disallowing_magic_login_sends_nothing(self, client, db, org, verified_user):
        # The org restricts sign-in to password only; a magic link would be
        # refused at the org gate anyway, so none is sent.
        await _set_allowed_methods(db, org.id, ["password"])
        with patch(
            "src.routers.auth.check_login_rate_limit", return_value=(True, None)
        ), patch(
            "src.services.auth.magic_login.send_magic_login_email"
        ) as send_mock, patch(
            "src.services.auth.magic_login.issue_magic_login_token", return_value="tok"
        ):
            response = await client.post(
                "/api/v1/auth/magic-link/request",
                json={"email": verified_user.email, "org_slug": org.slug},
            )
        assert response.status_code == 200
        send_mock.assert_not_called()

    async def test_saas_unverified_user_sends_nothing(self, client, db):
        # In SaaS mode an unverified account can't log in by password; a magic
        # link must not become a verification-bypass side channel.
        unverified = User(
            id=22,
            username="unverified",
            first_name="Un",
            last_name="Verified",
            email="unverified@test.com",
            password="hashed_password",
            user_uuid="user_unverified",
            email_verified=False,
            creation_date=str(datetime.now()),
            update_date=str(datetime.now()),
        )
        db.add(unverified)
        await db.commit()
        with patch(
            "src.routers.auth.check_login_rate_limit", return_value=(True, None)
        ), patch(
            "src.routers.auth.get_deployment_mode", return_value="saas"
        ), patch(
            "src.services.auth.magic_login.send_magic_login_email"
        ) as send_mock, patch(
            "src.services.auth.magic_login.issue_magic_login_token", return_value="tok"
        ):
            response = await client.post(
                "/api/v1/auth/magic-link/request",
                json={"email": unverified.email},
            )
        assert response.status_code == 200
        send_mock.assert_not_called()

    async def test_rate_limited(self, client):
        with patch(
            "src.routers.auth.check_login_rate_limit", return_value=(False, 60)
        ):
            response = await client.post(
                "/api/v1/auth/magic-link/request",
                json={"email": "x@test.com"},
            )
        assert response.status_code == 429
        assert response.json()["detail"]["code"] == "RATE_LIMITED"

    async def test_send_failure_is_swallowed_and_generic(self, client, verified_user):
        # A mail-transport error must not turn into a 500 that leaks the address
        # exists; the endpoint still returns the generic 200.
        with patch(
            "src.routers.auth.check_login_rate_limit", return_value=(True, None)
        ), patch(
            "src.services.auth.magic_login.issue_magic_login_token", return_value="tok"
        ), patch(
            "src.services.auth.magic_login.send_magic_login_email",
            side_effect=RuntimeError("smtp down"),
        ), patch(
            "src.services.email.utils.get_base_url_from_request",
            return_value="http://test",
        ):
            response = await client.post(
                "/api/v1/auth/magic-link/request",
                json={"email": verified_user.email},
            )
        assert response.status_code == 200


class TestMagicLinkVerify:
    async def test_success_mints_session(self, client, verified_user):
        with patch(
            "src.services.auth.magic_login.consume_magic_login_token",
            return_value=(verified_user.email, None),
        ), patch("src.routers.auth.set_auth_cookies") as cookies_mock, patch(
            "src.routers.auth.get_token_expiry_ms", return_value=12345
        ):
            response = await client.post(
                "/api/v1/auth/magic-link/verify", json={"token": "good-token"}
            )
        assert response.status_code == 200
        body = response.json()
        assert body["user"]["email"] == verified_user.email
        assert body["tokens"]["access_token"]
        cookies_mock.assert_called_once()

    async def test_mfa_required_branch(self, client, db, verified_user):
        await _enroll_confirmed(db, verified_user.id)
        with patch(
            "src.services.auth.magic_login.consume_magic_login_token",
            return_value=(verified_user.email, None),
        ):
            response = await client.post(
                "/api/v1/auth/magic-link/verify", json={"token": "good-token"}
            )
        assert response.status_code == 200
        body = response.json()
        assert body["mfa_required"] is True
        assert body["mfa_token"]

    async def test_unknown_user_410(self, client):
        with patch(
            "src.services.auth.magic_login.consume_magic_login_token",
            return_value=("ghost@test.com", None),
        ):
            response = await client.post(
                "/api/v1/auth/magic-link/verify", json={"token": "good-token"}
            )
        assert response.status_code == 410
        assert response.json()["detail"]["code"] == "MAGIC_LINK_INVALID"

    async def test_invalid_or_used_link_410(self, client):
        with patch(
            "src.services.auth.magic_login.consume_magic_login_token",
            side_effect=HTTPException(
                status_code=410,
                detail={"code": "MAGIC_LINK_USED", "message": "already used"},
            ),
        ):
            response = await client.post(
                "/api/v1/auth/magic-link/verify", json={"token": "used-token"}
            )
        assert response.status_code == 410
        assert response.json()["detail"]["code"] == "MAGIC_LINK_USED"


class TestRefreshPreservesProvenance:
    """Rotation must not launder a method-bound/org-bound session into a
    claim-less one — otherwise a refresh would silently bypass the org
    auth-method / session-sharing policy. Tokens are minted for real here (not
    mocked) so the carried claims are asserted on the actual rotated token."""

    async def test_rotated_token_carries_amr_and_sorg(self, client, verified_user):
        with patch(
            "src.routers.auth.check_refresh_rate_limit", return_value=(True, None)
        ), patch(
            "src.routers.auth.decode_refresh_token",
            return_value={
                "sub": verified_user.email,
                "iat": 1,
                "exp": 9_999_999_999,
                "jti": "legit-jti",
                AMR_CLAIM: "magic_login",
                SORG_CLAIM: 1,
            },
        ), patch(
            "src.routers.auth.security_get_user",
            new_callable=AsyncMock,
            return_value=verified_user,
        ), patch(
            "src.routers.auth._is_token_revoked_for_user", return_value=False
        ), patch(
            "src.routers.auth._mark_refresh_jti_used", return_value=True
        ), patch(
            "src.routers.auth.get_cookie_domain_for_request", return_value=None
        ), patch(
            "src.routers.auth.is_request_secure", return_value=False
        ):
            response = await client.get(
                "/api/v1/auth/refresh",
                cookies={JWT_REFRESH_COOKIE_NAME: "refresh-token"},
            )
        assert response.status_code == 200
        rotated = decode_jwt(response.json()["access_token"])
        assert rotated[AMR_CLAIM] == "magic_login"
        assert rotated[SORG_CLAIM] == 1


class TestMagicLinkCarriesTheOrgsLanguageAndLogo:
    """The magic link is sent from an ORG's login page, so it must arrive in
    that org's language and under that org's mark.

    Its ``From`` NAME is a separate question and the answer is different: see
    ``TestMagicLinkKeepsTheInstanceFromName``. The body is the organization's;
    the envelope is the platform's.

    Before this, the router called ``send_magic_login_email`` with neither
    ``lang`` nor a logo: an organization whose config says
    ``default_language: fr`` and which has uploaded a logo still received an
    English link headed by the instance wordmark. The other org-scoped mails
    (invitation, password reset, verification) had resolved all of this for a
    while; this endpoint was the one that never did.

    These go through the real endpoint and assert on the rendered message at
    the transport boundary — patching ``send_email`` inside the magic-login
    module rather than the sender above it, so the assertions cover the
    template too, not just the arguments.
    """

    @staticmethod
    async def _org_with(db, *, config, logo_image=None, name="Académie Test"):
        from src.db.organizations import Organization

        org = Organization(
            id=77,
            name=name,
            slug="acad",
            email="acad@test.com",
            org_uuid="org_acad",
            logo_image=logo_image,
            creation_date=str(datetime.now()),
            update_date=str(datetime.now()),
        )
        db.add(org)
        db.add(
            OrganizationConfig(
                org_id=77,
                config=config,
                creation_date=str(datetime.now()),
                update_date=str(datetime.now()),
            )
        )
        await db.commit()
        return org

    _FR_CONFIG = {
        "config_version": "2.0",
        "customization": {
            "general": {
                "default_language": "fr",
                "email_sender_name": "Académie Test",
            }
        },
    }

    async def _request(self, client, payload):
        with patch(
            "src.routers.auth.check_login_rate_limit", return_value=(True, None)
        ), patch(
            "src.services.auth.magic_login.issue_magic_login_token", return_value="tok"
        ), patch(
            "src.services.auth.magic_login.send_email", return_value=True
        ) as sent, patch.dict(
            "os.environ", {"LEARNHOUSE_MEDIA_URL": "https://api.test"}
        ):
            response = await client.post(
                "/api/v1/auth/magic-link/request", json=payload
            )
        return response, sent

    async def test_french_org_with_a_logo_gets_french_copy_and_its_own_mark(
        self, client, db, verified_user
    ):
        await self._org_with(
            db, config=self._FR_CONFIG, logo_image="620e84b0_logo.png"
        )
        response, sent = await self._request(
            client, {"email": verified_user.email, "org_slug": "acad"}
        )

        assert response.status_code == 200
        sent.assert_called_once()
        kwargs = sent.call_args.kwargs
        body = kwargs["body"]

        # (i) the org's language
        assert "Se connecter" in body
        assert "Ou copiez et collez ce lien" in body
        assert "Sign in" not in body

        # (ii) the org's logo, and NOT the instance wordmark
        assert (
            '<img src="https://api.test/content/orgs/org_acad/logos/620e84b0_logo.png"'
            in body
        )
        assert "black_logo.png" not in body
        assert body.count("<img") == 1

        # (iii) …and NOT the org's own From name, even though this org has one
        # configured. A login link is an instance email: overriding the
        # deployment's configured sender on it is the regression this asserts
        # against. The header itself is proved in
        # TestMagicLinkKeepsTheInstanceFromName below.
        assert "sender_name" not in kwargs

    async def test_org_without_a_logo_keeps_the_instance_mark(
        self, client, db, verified_user
    ):
        """Language is the org's; the mark falls back — it has none to use."""
        await self._org_with(db, config=self._FR_CONFIG, logo_image=None)
        response, sent = await self._request(
            client, {"email": verified_user.email, "org_slug": "acad"}
        )

        assert response.status_code == 200
        body = sent.call_args.kwargs["body"]
        assert "Se connecter" in body
        assert "black_logo.png" in body
        assert "None" not in body

    async def test_without_an_org_the_link_still_goes_out_unbranded(
        self, client, verified_user
    ):
        """The org-less fallback: default language, instance mark, no From name.

        This is also the path every misconfiguration degrades to, so it is the
        one that must never break — a magic link is the user's only way in.
        """
        response, sent = await self._request(client, {"email": verified_user.email})

        assert response.status_code == 200
        sent.assert_called_once()
        kwargs = sent.call_args.kwargs
        assert "Sign in" in kwargs["body"]
        assert "black_logo.png" in kwargs["body"]
        assert "sender_name" not in kwargs

    async def test_a_failing_org_lookup_does_not_stop_the_link(
        self, client, db, verified_user
    ):
        """The decoration is best-effort; the send is not.

        If reading the org's config raises, the mail must still leave with the
        instance defaults rather than the user being locked out.
        """
        await self._org_with(
            db, config=self._FR_CONFIG, logo_image="620e84b0_logo.png"
        )
        with patch(
            "src.routers.auth.check_login_rate_limit", return_value=(True, None)
        ), patch(
            "src.services.auth.magic_login.issue_magic_login_token", return_value="tok"
        ), patch(
            "src.services.auth.magic_login.send_email", return_value=True
        ) as sent, patch(
            "src.services.orgs.orgs.get_org_default_language",
            side_effect=RuntimeError("config unreadable"),
        ):
            response = await client.post(
                "/api/v1/auth/magic-link/request",
                json={"email": verified_user.email, "org_slug": "acad"},
            )

        assert response.status_code == 200
        sent.assert_called_once()
        body = sent.call_args.kwargs["body"]
        assert "Sign in" in body
        assert "None" not in body


class TestMagicLinkKeepsTheInstanceFromName:
    """The login link's ``From`` name is the deployment's, never an org's.

    Every other org-scoped mail (invitation, password reset, role change,
    address verification) signs with ``resolve_org_sender_name`` — it acts on
    an organization's behalf. A login link does not: it authenticates against
    the platform. Reading the org's ``email_sender_name`` here would silently
    override ``LEARNHOUSE_SYSTEM_EMAIL_SENDER_NAME`` — the value the operator
    configured and verified on a delivered message — on the single message a
    locked-out user has to recognise before clicking.

    That is not hypothetical: the organization actually running on this
    deployment has ``email_sender_name: "hi-ha.be"`` while the deployment is
    configured to send as "Campus hi-ha.be", so wiring the org name in changed
    the header on the real production login mail. This class exists so no
    future change can do that again without a red test.

    Unlike the tests above, these do NOT stub ``send_email``: only the provider
    transport underneath it is replaced, so ``send_email`` and ``format_sender``
    really run and the captured ``From`` is the header that would be sent.
    """

    _ORG_WITH_ITS_OWN_SENDER = {
        "config_version": "2.0",
        "customization": {
            "general": {
                "default_language": "fr",
                # A different string from the deployment's, exactly as in
                # production — otherwise the assertion below proves nothing.
                "email_sender_name": "Nom de l'organisation",
            }
        },
    }

    async def _capture_from_header(self, client, db, verified_user, *, org_slug):
        from src.db.organizations import Organization

        db.add(
            Organization(
                id=78,
                name="Organisation Test",
                slug="orgsender",
                email="org@test.com",
                org_uuid="org_sender",
                logo_image="620e84b0_logo.png",
                creation_date=str(datetime.now()),
                update_date=str(datetime.now()),
            )
        )
        db.add(
            OrganizationConfig(
                org_id=78,
                config=self._ORG_WITH_ITS_OWN_SENDER,
                creation_date=str(datetime.now()),
                update_date=str(datetime.now()),
            )
        )
        await db.commit()

        seen = {}

        def transport(sender, to, subject, body, mailing, headers=None):
            seen["From"] = sender
            seen["subject"] = subject
            seen["body"] = body
            return {"id": "stub"}

        payload = {"email": verified_user.email}
        if org_slug:
            payload["org_slug"] = org_slug

        with patch(
            "src.routers.auth.check_login_rate_limit", return_value=(True, None)
        ), patch(
            "src.services.auth.magic_login.issue_magic_login_token", return_value="tok"
        ), patch(
            "src.services.email.utils._send_email_resend", transport
        ), patch(
            "src.services.email.utils._send_email_smtp", transport
        ), patch.dict(
            "os.environ",
            {
                "LEARNHOUSE_MEDIA_URL": "https://api.test",
                "LEARNHOUSE_SYSTEM_EMAIL_ADDRESS": "no-reply@example.test",
                "LEARNHOUSE_SYSTEM_EMAIL_SENDER_NAME": "Nom de l'instance",
            },
        ):
            response = await client.post(
                "/api/v1/auth/magic-link/request", json=payload
            )

        assert response.status_code == 200
        assert seen, "no email reached the transport at all"
        return seen

    async def test_from_is_the_instance_even_when_the_org_configured_its_own(
        self, client, db, verified_user
    ):
        seen = await self._capture_from_header(
            client, db, verified_user, org_slug="orgsender"
        )

        assert seen["From"] == "Nom de l'instance <no-reply@example.test>"
        assert "Nom de l'organisation" not in seen["From"]
        # The body is still the org's: this is about the envelope only.
        assert "Se connecter" in seen["body"]
        assert "content/orgs/org_sender/logos/" in seen["body"]

    async def test_from_is_the_instance_without_an_org_too(
        self, client, db, verified_user
    ):
        seen = await self._capture_from_header(client, db, verified_user, org_slug=None)

        assert seen["From"] == "Nom de l'instance <no-reply@example.test>"


class TestAStoredLanguageOfTheWrongTypeStillSendsTheLink:
    """A ``default_language`` that is not a string must cost the language only.

    The column is plain JSON: a restore, an import or a row edited before the
    setting was validated can leave a number, a list or an object in there.
    Resolving it used to hand that value straight to ``normalize_language``,
    whose ``lang.split`` then raised ``AttributeError`` *inside* the send —
    where the router's ``except`` swallowed it. The caller got the same generic
    200 as a success and the login link was never sent, locking the user out
    with no signal anywhere.

    ``send_email`` is asserted by CALL COUNT, not by absence of an exception:
    the swallowing except is exactly what made the old failure invisible.
    """

    @staticmethod
    async def _org_with_language(db, value):
        from src.db.organizations import Organization

        db.add(
            Organization(
                id=79,
                name="Organisation Test",
                slug="badlang",
                email="bad@test.com",
                org_uuid="org_badlang",
                logo_image="620e84b0_logo.png",
                creation_date=str(datetime.now()),
                update_date=str(datetime.now()),
            )
        )
        db.add(
            OrganizationConfig(
                org_id=79,
                config={
                    "config_version": "2.0",
                    "customization": {"general": {"default_language": value}},
                },
                creation_date=str(datetime.now()),
                update_date=str(datetime.now()),
            )
        )
        await db.commit()

    @pytest.mark.parametrize(
        "stored", [42, ["fr"], {"code": "fr"}, True, 3.5], ids=
        ["int", "list", "dict", "bool", "float"]
    )
    async def test_the_link_still_goes_out_in_english(
        self, client, db, verified_user, stored
    ):
        await self._org_with_language(db, stored)

        with patch(
            "src.routers.auth.check_login_rate_limit", return_value=(True, None)
        ), patch(
            "src.services.auth.magic_login.issue_magic_login_token", return_value="tok"
        ), patch(
            "src.services.auth.magic_login.send_email", return_value=True
        ) as sent, patch.dict(
            "os.environ", {"LEARNHOUSE_MEDIA_URL": "https://api.test"}
        ):
            response = await client.post(
                "/api/v1/auth/magic-link/request",
                json={"email": verified_user.email, "org_slug": "badlang"},
            )

        assert response.status_code == 200
        assert sent.call_count == 1, "the login link was NOT sent"
        body = sent.call_args.kwargs["body"]
        # Falls back to English rather than to nothing…
        assert "Sign in" in body
        # …and the rest of the decoration is untouched: only the language is lost.
        assert "content/orgs/org_badlang/logos/" in body


class TestANullConfigSectionOnlyCostsTheLanguage:
    """``{"customization": null}`` must not take the logo down with it.

    ``get_org_default_language`` is resolved first in the router, so before it
    was hardened, an explicit ``null`` section made it raise and the single
    ``except`` around the whole block dropped the org's logo as well — a
    failure to read one setting silently discarded another that was perfectly
    readable.
    """

    @pytest.mark.parametrize(
        "config",
        [
            {"customization": None},
            {"customization": {"general": None}},
            "not a dict at all",
        ],
        ids=["customization_null", "general_null", "config_not_a_dict"],
    )
    async def test_logo_survives_an_unreadable_language_section(
        self, client, db, verified_user, config
    ):
        from src.db.organizations import Organization

        db.add(
            Organization(
                id=80,
                name="Organisation Test",
                slug="nullcfg",
                email="null@test.com",
                org_uuid="org_nullcfg",
                logo_image="620e84b0_logo.png",
                creation_date=str(datetime.now()),
                update_date=str(datetime.now()),
            )
        )
        db.add(
            OrganizationConfig(
                org_id=80,
                config=config,
                creation_date=str(datetime.now()),
                update_date=str(datetime.now()),
            )
        )
        await db.commit()

        with patch(
            "src.routers.auth.check_login_rate_limit", return_value=(True, None)
        ), patch(
            "src.services.auth.magic_login.issue_magic_login_token", return_value="tok"
        ), patch(
            "src.services.auth.magic_login.send_email", return_value=True
        ) as sent, patch.dict(
            "os.environ", {"LEARNHOUSE_MEDIA_URL": "https://api.test"}
        ):
            response = await client.post(
                "/api/v1/auth/magic-link/request",
                json={"email": verified_user.email, "org_slug": "nullcfg"},
            )

        assert response.status_code == 200
        assert sent.call_count == 1, "the login link was NOT sent"
        body = sent.call_args.kwargs["body"]
        assert "Sign in" in body
        assert "content/orgs/org_nullcfg/logos/" in body
