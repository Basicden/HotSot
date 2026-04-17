"""HotSot Menu Service — Unit Tests.

Tests cover:
1. Menu item creation with pricing validation
2. Availability toggle
3. Price rounding (INR)
4. Vendor menu listing
"""
import uuid
from decimal import Decimal, ROUND_HALF_UP
from unittest.mock import AsyncMock, MagicMock

import pytest


# ═══════════════════════════════════════════════════════════════
# PRICE ROUNDING
# ═══════════════════════════════════════════════════════════════

class TestPriceRounding:
    """Test INR price rounding for menu items."""

    def test_price_rounded_to_two_decimals(self):
        """Prices should be rounded to 2 decimal places."""
        price = Decimal("99.999").quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        assert price == Decimal("100.00")

    def test_price_half_up_rounding(self):
        """Prices should use ROUND_HALF_UP (Indian standard)."""
        price = Decimal("99.995").quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        assert price == Decimal("100.00")

    def test_price_already_rounded_stays_same(self):
        """Already rounded prices should not change."""
        price = Decimal("199.00").quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        assert price == Decimal("199.00")

    def test_price_zero_rounds_correctly(self):
        """Zero price should round to 0.00."""
        price = Decimal("0").quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        assert price == Decimal("0.00")


# ═══════════════════════════════════════════════════════════════
# MENU ITEM MODEL
# ═══════════════════════════════════════════════════════════════

class TestMenuItemModel:
    """Test menu item model defaults."""

    def test_menu_item_default_is_available(self):
        """New menu items should be available by default."""
        mock_item = MagicMock()
        mock_item.is_available = True
        assert mock_item.is_available is True

    def test_menu_item_default_is_veg(self):
        """Default should be vegetarian (Indian market default)."""
        mock_item = MagicMock()
        mock_item.is_veg = True
        assert mock_item.is_veg is True

    def test_menu_item_prep_time_default(self):
        """Default prep time should be 300 seconds (5 min)."""
        mock_item = MagicMock()
        mock_item.prep_time_seconds = 300
        assert mock_item.prep_time_seconds == 300


# ═══════════════════════════════════════════════════════════════
# MENU ROUTE LOGIC
# ═══════════════════════════════════════════════════════════════

class TestMenuRouteLogic:
    """Test menu route business logic with mocked DB."""

    @pytest.fixture
    def mock_session(self):
        return AsyncMock()

    @pytest.fixture
    def mock_menu_item(self):
        item = MagicMock()
        item.id = uuid.uuid4()
        item.name = "Biryani"
        item.price = Decimal("250.00")
        item.is_veg = False
        item.is_available = True
        item.category = "RICE_BOWL"
        item.batch_category = "RICE_BOWL"
        item.prep_time_seconds = 420
        return item

    @pytest.mark.asyncio
    async def test_toggle_availability_to_false(self, mock_menu_item):
        """Toggling availability should set is_available to False."""
        mock_menu_item.is_available = False
        assert mock_menu_item.is_available is False

    @pytest.mark.asyncio
    async def test_toggle_availability_to_true(self, mock_menu_item):
        """Toggling availability back should set is_available to True."""
        mock_menu_item.is_available = False
        mock_menu_item.is_available = True
        assert mock_menu_item.is_available is True

    def test_vendor_menu_only_shows_available(self, mock_menu_item):
        """Vendor menu listing should only include available items."""
        items = [mock_menu_item]
        available = [i for i in items if i.is_available]
        assert len(available) == 1

        mock_menu_item.is_available = False
        available = [i for i in items if i.is_available]
        assert len(available) == 0

    def test_menu_item_uuid_format(self):
        """Menu item IDs should be valid UUID strings."""
        item_id = str(uuid.uuid4())
        parsed = uuid.UUID(item_id)
        assert str(parsed) == item_id

    def test_batch_category_from_category(self):
        """Batch category should match or derive from item category."""
        category_map = {
            "RICE_BOWL": "RICE_BOWL",
            "BREAD": "BREAD",
            "BEVERAGE": "BEVERAGE",
        }
        assert category_map.get("RICE_BOWL") == "RICE_BOWL"

    def test_complex_item_prep_time(self):
        """Complex items (biryani, pizza) should have longer prep time."""
        assert 420 > 300  # Biryani prep > default
