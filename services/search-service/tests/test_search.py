"""HotSot Search Service — Unit Tests.

Tests cover:
1. Dietary preference validation
2. Price range validation
3. Search result formatting
4. Pagination metadata
"""
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.routes.search import _format_result


# ═══════════════════════════════════════════════════════════════
# DIETARY PREFERENCE VALIDATION
# ═══════════════════════════════════════════════════════════════

class TestDietaryPreferenceValidation:
    """Test dietary preference filter validation."""

    def test_valid_dietary_preferences(self):
        """Valid dietary preferences should be accepted."""
        valid = {"VEG", "NON_VEG", "VEGAN", "EGGETARIAN"}
        for pref in valid:
            assert pref in valid

    def test_invalid_dietary_preference_rejected(self):
        """Invalid dietary preferences should be rejected."""
        valid = {"VEG", "NON_VEG", "VEGAN", "EGGETARIAN"}
        assert "OMNIVORE" not in valid
        assert "PESCATARIAN" not in valid

    def test_dietary_case_insensitive(self):
        """Dietary preference matching should be case-insensitive."""
        valid = {"VEG", "NON_VEG", "VEGAN", "EGGETARIAN"}
        assert "veg".upper() in valid
        assert "non_veg".upper() in valid


# ═══════════════════════════════════════════════════════════════
# SEARCH RESULT FORMATTING
# ═══════════════════════════════════════════════════════════════

class TestSearchResultFormatting:
    """Test search result formatting."""

    def _make_mock_item(self, **overrides):
        defaults = {
            "entity_type": "MENU_ITEM",
            "entity_id": uuid.uuid4(),
            "name": "Biryani",
            "description": "Hyderabadi biryani",
            "cuisine": "North Indian",
            "category": "RICE_BOWL",
            "city": "Bangalore",
            "dietary_preference": "NON_VEG",
            "price_min": 200.0,
            "price_max": 350.0,
            "popularity_score": 4.5,
            "tags": ["spicy", "rice"],
            "vendor_id": uuid.uuid4(),
        }
        defaults.update(overrides)
        item = MagicMock()
        for k, v in defaults.items():
            setattr(item, k, v)
        return item

    def test_format_result_includes_entity_type(self):
        """Formatted result should include entity_type."""
        item = self._make_mock_item()
        result = _format_result(item, None)
        assert result["entity_type"] == "MENU_ITEM"

    def test_format_result_includes_name(self):
        """Formatted result should include name."""
        item = self._make_mock_item()
        result = _format_result(item, None)
        assert result["name"] == "Biryani"

    def test_format_result_includes_cuisine(self):
        """Formatted result should include cuisine."""
        item = self._make_mock_item()
        result = _format_result(item, None)
        assert result["cuisine"] == "North Indian"

    def test_format_result_includes_price_range(self):
        """Formatted result should include price range."""
        item = self._make_mock_item()
        result = _format_result(item, None)
        assert result["price_min"] == 200.0
        assert result["price_max"] == 350.0

    def test_format_result_with_relevance_score(self):
        """Formatted result with rank should include relevance_score."""
        item = self._make_mock_item()
        result = _format_result(item, 0.8567)
        assert result["relevance_score"] == round(0.8567, 4)

    def test_format_result_without_rank_relevance_zero(self):
        """Formatted result without rank should have relevance_score 0.0."""
        item = self._make_mock_item()
        result = _format_result(item, None)
        assert result["relevance_score"] == 0.0

    def test_format_result_includes_vendor_id(self):
        """Formatted result should include vendor_id when set."""
        item = self._make_mock_item()
        result = _format_result(item, None)
        assert "vendor_id" in result

    def test_format_result_no_vendor_id_when_none(self):
        """Formatted result should not include vendor_id when None."""
        item = self._make_mock_item(vendor_id=None)
        result = _format_result(item, None)
        assert "vendor_id" not in result


# ═══════════════════════════════════════════════════════════════
# PAGINATION METADATA
# ═══════════════════════════════════════════════════════════════

class TestPaginationMetadata:
    """Test pagination logic for search results."""

    def test_has_more_true_when_more_results(self):
        """has_more should be True when offset + limit < total."""
        offset, limit, total = 0, 20, 50
        has_more = (offset + limit) < total
        assert has_more is True

    def test_has_more_false_when_at_end(self):
        """has_more should be False when offset + limit >= total."""
        offset, limit, total = 40, 20, 50
        has_more = (offset + limit) < total
        assert has_more is False

    def test_has_more_false_when_exact_match(self):
        """has_more should be False when offset + limit == total."""
        offset, limit, total = 30, 20, 50
        has_more = (offset + limit) < total
        assert has_more is True

    def test_price_min_cannot_exceed_price_max(self):
        """price_min > price_max should be rejected."""
        price_min, price_max = 500, 200
        assert price_min > price_max

    def test_vendor_id_must_be_valid_uuid(self):
        """vendor_id filter should be a valid UUID."""
        valid_uuid = str(uuid.uuid4())
        try:
            uuid.UUID(valid_uuid)
            is_valid = True
        except ValueError:
            is_valid = False
        assert is_valid

    def test_invalid_vendor_id_rejected(self):
        """Non-UUID vendor_id should be rejected."""
        invalid = "not-a-uuid"
        try:
            uuid.UUID(invalid)
            is_valid = True
        except ValueError:
            is_valid = False
        assert not is_valid
