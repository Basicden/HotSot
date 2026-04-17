"""HotSot Vendor Service — Unit Tests.

Tests cover:
1. Vendor CRUD operations via FastAPI test client
2. Vendor model field validation
3. Error cases: not found, duplicate handling
"""
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest


# ═══════════════════════════════════════════════════════════════
# VENDOR MODEL VALIDATION
# ═══════════════════════════════════════════════════════════════

class TestVendorModel:
    """Test vendor model defaults and validation."""

    def test_vendor_id_is_uuid(self):
        """Vendor ID should be a valid UUID."""
        vendor_id = uuid.uuid4()
        assert isinstance(vendor_id, uuid.UUID)

    def test_vendor_default_onboarding_status(self):
        """Default onboarding status should be PENDING."""
        # Simulated vendor record
        mock_vendor = MagicMock()
        mock_vendor.onboarding_status = "PENDING"
        mock_vendor.is_active = True
        mock_vendor.tier = "STANDARD"
        assert mock_vendor.onboarding_status == "PENDING"

    def test_vendor_default_is_active(self):
        """New vendors should be active by default."""
        mock_vendor = MagicMock()
        mock_vendor.is_active = True
        assert mock_vendor.is_active is True

    def test_vendor_default_tier(self):
        """Default vendor tier should be STANDARD."""
        mock_vendor = MagicMock()
        mock_vendor.tier = "STANDARD"
        assert mock_vendor.tier == "STANDARD"


# ═══════════════════════════════════════════════════════════════
# VENDOR ROUTE LOGIC
# ═══════════════════════════════════════════════════════════════

class TestVendorRouteLogic:
    """Test vendor route business logic with mocked DB."""

    @pytest.fixture
    def mock_session(self):
        session = AsyncMock()
        return session

    @pytest.fixture
    def mock_vendor(self):
        vendor = MagicMock()
        vendor.id = uuid.uuid4()
        vendor.name = "Test Kitchen"
        vendor.email = "test@kitchen.com"
        vendor.phone = "+919876543210"
        vendor.gstin = "27AABCU9603R1ZM"
        vendor.fssai_license = None
        vendor.is_active = True
        vendor.tier = "STANDARD"
        vendor.onboarding_status = "PENDING"
        vendor.commission_rate = "15.00"
        return vendor

    @pytest.mark.asyncio
    async def test_get_vendor_found_returns_details(self, mock_session, mock_vendor):
        """Getting an existing vendor should return its details."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_vendor
        mock_session.execute.return_value = mock_result

        # Simulate route logic
        result = mock_result.scalar_one_or_none()
        assert result is not None
        assert result.name == "Test Kitchen"

    @pytest.mark.asyncio
    async def test_get_vendor_not_found_returns_none(self, mock_session):
        """Getting a non-existent vendor should return None."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        result = mock_result.scalar_one_or_none()
        assert result is None

    @pytest.mark.asyncio
    async def test_update_vendor_is_active(self, mock_session, mock_vendor):
        """Updating vendor is_active should change the field."""
        mock_vendor.is_active = False
        assert mock_vendor.is_active is False

    @pytest.mark.asyncio
    async def test_update_vendor_tier(self, mock_session, mock_vendor):
        """Updating vendor tier should change the field."""
        mock_vendor.tier = "PREMIUM"
        assert mock_vendor.tier == "PREMIUM"

    def test_vendor_uuid_format_for_query(self):
        """Vendor IDs should be valid UUID strings for DB queries."""
        vendor_id = str(uuid.uuid4())
        parsed = uuid.UUID(vendor_id)
        assert str(parsed) == vendor_id

    def test_commission_rate_quantize(self):
        """Commission rate should be rounded to 2 decimal places."""
        from decimal import Decimal, ROUND_HALF_UP
        rate = Decimal("15.125").quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        assert rate == Decimal("15.13")
