"""
HotSot Test Pyramid — Shared Fixtures & Configuration

Provides reusable test fixtures for all testing layers:
    - In-memory databases for unit tests
    - Mock Redis/Kafka for integration tests
    - Synthetic user generators for behavior tests
    - Adversarial input generators
"""

import asyncio
import random
import string
import uuid
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional

import pytest
import pytest_asyncio

# ─── Async Event Loop ────────────────────────────────────────
@pytest.fixture(scope="session")
def event_loop():
    """Shared async event loop for all tests."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# ─── Indian Food Test Data ────────────────────────────────────
INDIAN_DISHES = [
    {"item_id": "butter-chicken", "name": "Butter Chicken", "price": 350, "prep_time": 900, "is_veg": False, "complexity": 1.2},
    {"item_id": "paneer-tikka", "name": "Paneer Tikka", "price": 280, "prep_time": 720, "is_veg": True, "complexity": 1.0},
    {"item_id": "hyderabadi-biryani", "name": "Hyderabadi Biryani", "price": 400, "prep_time": 1200, "is_veg": False, "complexity": 1.5},
    {"item_id": "masala-dosa", "name": "Masala Dosa", "price": 150, "prep_time": 480, "is_veg": True, "complexity": 0.8},
    {"item_id": "chole-bhature", "name": "Chole Bhature", "price": 180, "prep_time": 600, "is_veg": True, "complexity": 0.9},
    {"item_id": "tandoori-roti", "name": "Tandoori Roti", "price": 40, "prep_time": 180, "is_veg": True, "complexity": 0.3},
    {"item_id": "dal-makhani", "name": "Dal Makhani", "price": 250, "prep_time": 900, "is_veg": True, "complexity": 1.1},
    {"item_id": "gulab-jamun", "name": "Gulab Jamun", "price": 80, "prep_time": 300, "is_veg": True, "complexity": 0.5},
    {"item_id": "rajma-chawal", "name": "Rajma Chawal", "price": 160, "prep_time": 540, "is_veg": True, "complexity": 0.7},
    {"item_id": "fish-fry", "name": "Fish Fry", "price": 320, "prep_time": 660, "is_veg": False, "complexity": 1.0},
]

PAYMENT_METHODS = ["UPI", "CARD", "WALLET", "COD"]
USER_TIERS = ["FREE", "PLUS", "PRO", "VIP"]
SHELF_ZONES = ["HOT", "COLD", "AMBIENT"]
QUEUE_TYPES = ["IMMEDIATE", "NORMAL", "BATCH"]


# ─── Synthetic Data Generators ───────────────────────────────

def generate_user_id() -> str:
    return f"user_{random.randint(1, 100000)}"


def generate_kitchen_id() -> str:
    return f"kitchen_{random.randint(1, 500)}"


def generate_order_id() -> str:
    return f"ord_{uuid.uuid4().hex[:12]}"


def generate_tenant_id() -> str:
    return f"tenant_{random.choice(['default', 'mumbai', 'delhi', 'bangalore', 'chennai'])}"


def generate_random_items(min_items: int = 1, max_items: int = 5) -> List[Dict]:
    """Generate a random order with Indian food items."""
    count = random.randint(min_items, max_items)
    items = random.sample(INDIAN_DISHES, min(count, len(INDIAN_DISHES)))
    return [
        {
            "item_id": item["item_id"],
            "qty": random.randint(1, 4),
            "price": item["price"],
        }
        for item in items
    ]


def generate_order_payload(**overrides) -> Dict[str, Any]:
    """Generate a realistic order creation payload."""
    items = generate_random_items()
    total = sum(item["qty"] * item["price"] for item in items)
    payload = {
        "user_id": generate_user_id(),
        "kitchen_id": generate_kitchen_id(),
        "tenant_id": generate_tenant_id(),
        "items": items,
        "total_amount": total,
        "payment_method": random.choice(PAYMENT_METHODS),
    }
    payload.update(overrides)
    return payload


def generate_malicious_payload() -> List[Dict[str, Any]]:
    """Generate adversarial payloads for security testing."""
    return [
        # SQL Injection
        {"items": [{"item_id": "'; DROP TABLE orders;--", "qty": 1, "price": 100}]},
        # Negative price
        {"items": [{"item_id": "biryani", "qty": -5, "price": -350}]},
        # Oversized quantity
        {"items": [{"item_id": "dosa", "qty": 99999999, "price": 150}]},
        # Zero amount
        {"total_amount": 0, "items": [{"item_id": "free-item", "qty": 0, "price": 0}]},
        # Extreme total
        {"total_amount": 999999999.99, "items": [{"item_id": "gold-biryani", "qty": 1, "price": 999999999}]},
        # Empty items
        {"items": [], "total_amount": 0},
        # XSS in strings
        {"user_id": "<script>alert('xss')</script>", "kitchen_id": "k1"},
        # Unicode bomb
        {"user_id": "user_" + "\uffff" * 1000, "kitchen_id": "k1"},
        # Very long string
        {"user_id": "user_" + "a" * 10000, "kitchen_id": "k1"},
        # Type confusion (string where int expected)
        {"total_amount": "free", "items": [{"item_id": "1", "qty": "many", "price": "cheap"}]},
        # NoSQL injection
        {"user_id": {"$gt": ""}, "kitchen_id": {"$ne": ""}},
        # CRLF injection
        {"user_id": "user\r\nX-Injected: true", "kitchen_id": "k1"},
        # Path traversal
        {"user_id": "../../../etc/passwd", "kitchen_id": "k1"},
    ]


def generate_edge_case_amounts() -> List[Decimal]:
    """Generate edge case monetary amounts for INR."""
    return [
        Decimal("0.00"),
        Decimal("0.01"),
        Decimal("0.99"),
        Decimal("1.00"),
        Decimal("999.99"),
        Decimal("1000.00"),
        Decimal("99999.99"),
        Decimal("5000.00"),      # Compensation cap
        Decimal("-1.00"),        # Negative
        Decimal("0.001"),        # Sub-paise
        Decimal("999999999.99"), # Very large
    ]


def generate_edge_case_coords() -> List[Dict[str, float]]:
    """Generate edge case GPS coordinates for India."""
    return [
        {"lat": 28.6139, "lon": 77.2090},     # New Delhi (normal)
        {"lat": 19.0760, "lon": 72.8777},     # Mumbai (normal)
        {"lat": 12.9716, "lon": 77.5946},     # Bangalore (normal)
        {"lat": 0.0, "lon": 0.0},             # Null Island (invalid)
        {"lat": 90.0, "lon": 180.0},          # North Pole (invalid for India)
        {"lat": -90.0, "lon": -180.0},        # South Pole (invalid)
        {"lat": 28.6139, "lon": 77.2090},     # Duplicate (same as Delhi)
        {"lat": 91.0, "lon": 181.0},          # Out of range
        {"lat": float("nan"), "lon": 77.0},   # NaN
        {"lat": float("inf"), "lon": 77.0},   # Infinity
    ]


def generate_edge_case_phone_numbers() -> List[str]:
    """Generate edge case Indian phone numbers."""
    return [
        "9876543210",           # Valid 10-digit
        "+919876543210",        # Valid with country code
        "1234567890",           # Invalid series
        "0000000000",           # All zeros
        "9999999999",           # All nines
        "987654321",            # 9 digits (too short)
        "98765432101",          # 11 digits (too long)
        "",                     # Empty
        "abcdefghij",           # Non-numeric
        "98765 43210",          # With space
        "9876-543210",          # With hyphen
        "+1-987-654-3210",      # US format (wrong country)
    ]


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def sample_order():
    return generate_order_payload()


@pytest.fixture
def malicious_payloads():
    return generate_malicious_payload()


@pytest.fixture
def edge_case_amounts():
    return generate_edge_case_amounts()


@pytest.fixture
def edge_case_coords():
    return generate_edge_case_coords()
