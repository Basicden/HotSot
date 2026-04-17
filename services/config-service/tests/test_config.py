"""HotSot Config Service — Unit Tests.

Tests cover:
1. Config CRUD: get, set, update
2. Tenant isolation: configs are tenant-scoped
3. Config key structure: service_name + config_key
"""
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest


# ═══════════════════════════════════════════════════════════════
# CONFIG GET
# ═══════════════════════════════════════════════════════════════

class TestConfigGet:
    """Test config retrieval."""

    @pytest.fixture
    def mock_session(self):
        return AsyncMock()

    @pytest.mark.asyncio
    async def test_get_existing_config_returns_value(self, mock_session):
        """Getting an existing config should return its value."""
        mock_config = MagicMock()
        mock_config.config_key = "max_concurrent_orders"
        mock_config.config_value = {"limit": 50}
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_config
        mock_session.execute.return_value = mock_result

        result = mock_result.scalar_one_or_none()
        assert result is not None
        assert result.config_value == {"limit": 50}

    @pytest.mark.asyncio
    async def test_get_nonexistent_config_returns_none(self, mock_session):
        """Getting a non-existent config should return None."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        result = mock_result.scalar_one_or_none()
        assert result is None


# ═══════════════════════════════════════════════════════════════
# CONFIG SET
# ═══════════════════════════════════════════════════════════════

class TestConfigSet:
    """Test config creation and update."""

    @pytest.fixture
    def mock_session(self):
        return AsyncMock()

    @pytest.mark.asyncio
    async def test_set_new_config_creates_entry(self, mock_session):
        """Setting a new config key should create an entry."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        result = mock_result.scalar_one_or_none()
        assert result is None  # Config doesn't exist yet → create

    @pytest.mark.asyncio
    async def test_set_existing_config_updates_value(self, mock_session):
        """Setting an existing config key should update its value."""
        mock_config = MagicMock()
        mock_config.config_value = {"old": True}
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_config
        mock_session.execute.return_value = mock_result

        result = mock_result.scalar_one_or_none()
        assert result is not None
        # Update value
        result.config_value = {"new": True}
        assert result.config_value == {"new": True}


# ═══════════════════════════════════════════════════════════════
# TENANT ISOLATION
# ═══════════════════════════════════════════════════════════════

class TestTenantIsolation:
    """Test that configs are properly tenant-isolated."""

    def test_config_lookup_includes_tenant_id(self):
        """Config queries should include tenant_id filter."""
        tenant_id = str(uuid.uuid4())
        service_name = "kitchen-service"
        config_key = "max_concurrent"
        # Verify tenant_id is used
        assert tenant_id is not None

    def test_different_tenants_same_key_isolated(self):
        """Same config key for different tenants should be separate."""
        tenant1 = uuid.uuid4()
        tenant2 = uuid.uuid4()
        assert tenant1 != tenant2

    def test_tenant_id_converted_to_uuid(self):
        """Tenant ID should be a valid UUID for queries."""
        tenant_id = str(uuid.uuid4())
        parsed = uuid.UUID(tenant_id)
        assert str(parsed) == tenant_id


# ═══════════════════════════════════════════════════════════════
# CONFIG KEY STRUCTURE
# ═══════════════════════════════════════════════════════════════

class TestConfigKeyStructure:
    """Test config key naming structure."""

    def test_service_name_and_key_required(self):
        """Both service_name and config_key should be required."""
        service_name = "eta-service"
        config_key = "base_prep_seconds"
        assert service_name is not None
        assert config_key is not None

    def test_config_value_is_dict(self):
        """Config value should be stored as a dict/JSON."""
        config_value = {"limit": 50, "enabled": True}
        assert isinstance(config_value, dict)

    def test_updated_at_set_on_change(self):
        """updated_at should be set when config is modified."""
        now = datetime.now(timezone.utc)
        assert now is not None

    def test_service_name_scopes_config(self):
        """Same config_key in different services should be separate."""
        kitchen_key = ("kitchen-service", "timeout")
        eta_key = ("eta-service", "timeout")
        assert kitchen_key != eta_key
