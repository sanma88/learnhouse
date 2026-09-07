"""Tests for the per-organization default language config (services + router wrapper)."""

from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from sqlmodel import select

from src.db.organization_config import OrganizationConfig
from src.routers.orgs.orgs import api_update_org_default_language_config
from src.services.orgs.orgs import (
    get_org_default_language,
    update_org_default_language_config,
)


async def _make_org_config(db, org, config):
    row = OrganizationConfig(
        org_id=org.id,
        config=config,
        creation_date=str(datetime.now()),
        update_date=str(datetime.now()),
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


class TestUpdateOrgDefaultLanguageConfig:
    @pytest.mark.asyncio
    async def test_updates_v2_customization_general(
        self, mock_request, db, other_org, admin_user
    ):
        await _make_org_config(
            db,
            other_org,
            {
                "config_version": "2.0",
                "plan": "free",
                "customization": {"general": {"color": "#000"}},
            },
        )

        with patch(
            "src.services.orgs.orgs.rbac_check",
            new_callable=AsyncMock,
            return_value=True,
        ):
            result = await update_org_default_language_config(
                mock_request, "fr", other_org.id, admin_user, db
            )

        assert result == {"detail": "Default language updated"}

        stmt = select(OrganizationConfig).where(OrganizationConfig.org_id == other_org.id)
        stored = (await db.execute(stmt)).scalars().first()
        assert stored.config["customization"]["general"]["default_language"] == "fr"
        # Pre-existing customization keys are preserved.
        assert stored.config["customization"]["general"]["color"] == "#000"

    @pytest.mark.asyncio
    async def test_updates_v1_general_branch(
        self, mock_request, db, org, admin_user
    ):
        await _make_org_config(
            db,
            org,
            {
                "config_version": "1.4",
                "general": {"enabled": True, "color": ""},
                "features": {},
                "cloud": {"plan": "free"},
            },
        )

        with patch(
            "src.services.orgs.orgs.rbac_check",
            new_callable=AsyncMock,
            return_value=True,
        ):
            await update_org_default_language_config(
                mock_request, "de", org.id, admin_user, db
            )

        stmt = select(OrganizationConfig).where(OrganizationConfig.org_id == org.id)
        stored = (await db.execute(stmt)).scalars().first()
        assert stored.config["general"]["default_language"] == "de"

    @pytest.mark.asyncio
    async def test_rejects_unsupported_language_with_400(
        self, mock_request, db, org, admin_user
    ):
        with pytest.raises(HTTPException) as exc:
            await update_org_default_language_config(
                mock_request, "klingon", org.id, admin_user, db
            )
        assert exc.value.status_code == 400
        assert "Unsupported language" in exc.value.detail

    @pytest.mark.asyncio
    async def test_raises_404_when_org_missing(
        self, mock_request, db, admin_user
    ):
        with pytest.raises(HTTPException) as exc:
            await update_org_default_language_config(
                mock_request, "en", 99999, admin_user, db
            )
        assert exc.value.status_code == 404
        assert exc.value.detail == "Organization not found"

    @pytest.mark.asyncio
    async def test_raises_404_when_config_missing(
        self, mock_request, db, org, admin_user
    ):
        # Org exists but no OrganizationConfig row was created.
        with patch(
            "src.services.orgs.orgs.rbac_check",
            new_callable=AsyncMock,
            return_value=True,
        ):
            with pytest.raises(HTTPException) as exc:
                await update_org_default_language_config(
                    mock_request, "en", org.id, admin_user, db
                )
        assert exc.value.status_code == 404
        assert exc.value.detail == "Organization config not found"


class TestGetOrgDefaultLanguage:
    @pytest.mark.asyncio
    async def test_returns_v2_value_when_present(self, db, org):
        row = await _make_org_config(
            db,
            org,
            {
                "config_version": "2.0",
                "customization": {"general": {"default_language": "ja"}},
            },
        )
        assert get_org_default_language(row) == "ja"

    @pytest.mark.asyncio
    async def test_falls_back_to_v1_general_branch(self, db, org):
        row = await _make_org_config(
            db,
            org,
            {
                "config_version": "1.4",
                "general": {"default_language": "es"},
            },
        )
        assert get_org_default_language(row) == "es"

    @pytest.mark.asyncio
    async def test_returns_en_when_key_absent(self, db, org):
        row = await _make_org_config(
            db,
            org,
            {"config_version": "2.0", "customization": {"general": {}}},
        )
        assert get_org_default_language(row) == "en"

    def test_returns_en_when_org_config_is_none(self):
        assert get_org_default_language(None) == "en"

    @pytest.mark.asyncio
    async def test_returns_en_when_config_is_empty(self, db, org):
        row = await _make_org_config(db, org, {})
        assert get_org_default_language(row) == "en"

    # --- Hardening: this is read out of a plain JSON column, so it can hold
    # anything a restore, an import or a hand-edited row put there. Seven
    # emails call it, one of them the magic login link, which is a user's only
    # way in — so it must always return a str and never raise. See
    # test_auth_magic_link.py for the end-to-end proof that the link still goes.

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "stored", [42, ["fr"], {"code": "fr"}, True, 3.5, None, ""],
        ids=["int", "list", "dict", "bool", "float", "null", "empty"],
    )
    async def test_returns_en_when_the_stored_value_is_not_a_language_string(
        self, db, org, stored
    ):
        row = await _make_org_config(
            db,
            org,
            {
                "config_version": "2.0",
                "customization": {"general": {"default_language": stored}},
            },
        )
        assert get_org_default_language(row) == "en"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "config",
        [
            {"customization": None},
            {"customization": {"general": None}},
            {"general": None},
            "not a dict at all",
            ["not", "a", "dict"],
            42,
        ],
        ids=["customization_null", "general_null", "v1_general_null",
             "string", "list", "int"],
    )
    async def test_returns_en_when_a_section_is_null_or_mistyped(
        self, db, org, config
    ):
        """Previously raised AttributeError: ``{}.get(k, {})`` hands back an
        explicit ``null`` rather than the default. Mirrors the guard
        ``resolve_org_sender_name`` already had."""
        row = await _make_org_config(db, org, config)
        assert get_org_default_language(row) == "en"

    @pytest.mark.asyncio
    async def test_still_reads_a_v1_string_under_a_null_v2_section(self, db, org):
        row = await _make_org_config(
            db,
            org,
            {"customization": None, "general": {"default_language": "es"}},
        )
        assert get_org_default_language(row) == "es"


class TestApiUpdateOrgDefaultLanguageRouterWrapper:
    """Cover the thin router handler that just delegates to the service."""

    @pytest.mark.asyncio
    async def test_delegates_to_service(self, mock_request, db, org, admin_user):
        with patch(
            "src.routers.orgs.orgs.update_org_default_language_config",
            new_callable=AsyncMock,
            return_value={"detail": "Default language updated"},
        ) as mocked:
            result = await api_update_org_default_language_config(
                mock_request,
                org.id,
                "fr",
                admin_user,
                db,
            )

        mocked.assert_awaited_once_with(
            mock_request, "fr", org.id, admin_user, db
        )
        assert result == {"detail": "Default language updated"}
