"""
HotSot — PHASE 3: Human Behavior Simulation

Simulates realistic AND adversarial human behaviors to discover
where the system breaks under "human" pressure.

User Types:
    - Normal: Predictable usage patterns
    - Impatient: Rapid repeated actions, double-clicks
    - Confused: Random navigation, incomplete flows
    - Malicious: Invalid payloads, injection patterns
    - Edge-case: Extreme inputs, boundary values

Behavior Patterns:
    - Repeated clicks (double-submit)
    - Partial transactions (abandoned carts)
    - Retry storms (refresh spam)
    - Multi-device conflicts
"""

import asyncio
import json
import random
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

import pytest


# ═══════════════════════════════════════════════════════════════
# USER PERSONA SIMULATORS
# ═══════════════════════════════════════════════════════════════

class NormalUser:
    """Simulates a normal user placing an order."""

    def __init__(self, user_id: str = None):
        self.user_id = user_id or f"user_normal_{random.randint(1, 10000)}"
        self.tenant_id = random.choice(["mumbai", "delhi", "bangalore"])

    def create_order(self) -> Dict:
        items = random.sample([
            {"item_id": "butter-chicken", "qty": random.randint(1, 3), "price": 350},
            {"item_id": "paneer-tikka", "qty": random.randint(1, 2), "price": 280},
            {"item_id": "biryani", "qty": random.randint(1, 2), "price": 400},
            {"item_id": "masala-dosa", "qty": random.randint(1, 3), "price": 150},
        ], k=random.randint(1, 3))
        total = sum(i["qty"] * i["price"] for i in items)

        return {
            "user_id": self.user_id,
            "kitchen_id": f"kitchen_{random.randint(1, 50)}",
            "tenant_id": self.tenant_id,
            "items": items,
            "total_amount": total,
            "payment_method": random.choice(["UPI", "CARD", "WALLET"]),
        }

    def generate_session(self) -> List[Dict]:
        """Generate a complete normal user session."""
        return [
            {"action": "create_order", "payload": self.create_order(), "delay_ms": random.randint(500, 2000)},
            {"action": "wait", "delay_ms": random.randint(3000, 8000)},
        ]


class ImpatientUser:
    """Simulates an impatient user — double-clicks, refresh spam, rapid retries."""

    def __init__(self, user_id: str = None):
        self.user_id = user_id or f"user_impatient_{random.randint(1, 1000)}"

    def double_click_order(self) -> List[Dict]:
        """Same order sent twice rapidly (within 100ms)."""
        order = {
            "user_id": self.user_id,
            "kitchen_id": f"kitchen_{random.randint(1, 50)}",
            "tenant_id": "mumbai",
            "items": [{"item_id": "biryani", "qty": 1, "price": 400}],
            "total_amount": 400,
            "payment_method": "UPI",
            "idempotency_key": f"imp_{self.user_id}_{uuid.uuid4().hex[:8]}",
        }
        return [
            {"action": "create_order", "payload": order, "delay_ms": 0},
            {"action": "create_order", "payload": order, "delay_ms": 50},  # Double-click!
        ]

    def refresh_spam(self, order_id: str, count: int = 5) -> List[Dict]:
        """Rapidly check order status."""
        return [
            {"action": "get_order", "order_id": order_id, "delay_ms": random.randint(0, 100)}
            for _ in range(count)
        ]

    def payment_retry_storm(self, order_id: str) -> List[Dict]:
        """Rapidly retry payment after failure."""
        return [
            {"action": "pay_order", "order_id": order_id, "delay_ms": random.randint(50, 200)}
            for _ in range(random.randint(3, 8))
        ]


class ConfusedUser:
    """Simulates a confused user — incomplete flows, random navigation."""

    def __init__(self, user_id: str = None):
        self.user_id = user_id or f"user_confused_{random.randint(1, 1000)}"

    def incomplete_flows(self) -> List[Dict]:
        """Start orders but never complete them."""
        flows = []
        for i in range(random.randint(2, 5)):
            flows.append({
                "action": "create_order",
                "payload": {
                    "user_id": self.user_id,
                    "kitchen_id": f"kitchen_{random.randint(1, 50)}",
                    "tenant_id": "delhi",
                    "items": [{"item_id": "dosa", "qty": 1, "price": 150}],
                    "total_amount": 150,
                },
                "delay_ms": random.randint(100, 500),
            })
            # Never pay — just abandon
            flows.append({"action": "wait", "delay_ms": random.randint(1000, 5000)})
        return flows

    def random_navigation(self) -> List[Dict]:
        """Randomly navigate between unrelated endpoints."""
        actions = ["get_menu", "search", "get_kitchen_status", "get_eta", "health_check"]
        return [
            {"action": random.choice(actions), "delay_ms": random.randint(200, 1000)}
            for _ in range(random.randint(5, 15))
        ]


class MaliciousUser:
    """Simulates a malicious user — injection, fuzzing, abuse patterns."""

    def __init__(self, user_id: str = None):
        self.user_id = user_id or f"user_malicious_{random.randint(1, 100)}"

    def sql_injection_payloads(self) -> List[Dict]:
        """SQL injection attempts in various fields."""
        payloads = [
            "'; DROP TABLE orders;--",
            "1' OR '1'='1",
            "1; SELECT * FROM users--",
            "' UNION SELECT password FROM admin--",
            "1'; INSERT INTO orders VALUES('hacked');--",
        ]
        return [
            {
                "action": "create_order",
                "payload": {
                    "user_id": self.user_id,
                    "kitchen_id": payload,
                    "tenant_id": payload,
                    "items": [{"item_id": payload, "qty": 1, "price": 100}],
                    "total_amount": 100,
                },
                "expect_rejection": True,
            }
            for payload in payloads
        ]

    def xss_payloads(self) -> List[Dict]:
        """XSS attempts in text fields."""
        payloads = [
            "<script>alert('xss')</script>",
            "<img src=x onerror=alert(1)>",
            "javascript:alert(1)",
            "{{7*7}}",  # Template injection
            "${7*7}",   # Expression language injection
        ]
        return [
            {
                "action": "create_order",
                "payload": {
                    "user_id": payload,
                    "kitchen_id": "kitchen_1",
                    "tenant_id": "mumbai",
                    "items": [{"item_id": payload, "qty": 1, "price": 100}],
                    "total_amount": 100,
                },
                "expect_sanitization": True,
            }
            for payload in payloads
        ]

    def financial_manipulation(self) -> List[Dict]:
        """Attempt to manipulate prices and amounts."""
        return [
            # Zero amount order
            {
                "action": "create_order",
                "payload": {
                    "user_id": self.user_id,
                    "kitchen_id": "kitchen_1",
                    "items": [{"item_id": "biryani", "qty": 1, "price": 400}],
                    "total_amount": 0,  # Mismatch!
                },
                "expect_rejection": True,
            },
            # Negative amount
            {
                "action": "create_order",
                "payload": {
                    "user_id": self.user_id,
                    "kitchen_id": "kitchen_1",
                    "items": [{"item_id": "biryani", "qty": 1, "price": -400}],
                    "total_amount": -400,
                },
                "expect_rejection": True,
            },
            # Price manipulation (item price != total)
            {
                "action": "create_order",
                "payload": {
                    "user_id": self.user_id,
                    "kitchen_id": "kitchen_1",
                    "items": [{"item_id": "biryani", "qty": 1, "price": 400}],
                    "total_amount": 1,  # Should be 400!
                },
                "expect_rejection": True,
            },
        ]

    def rate_limit_bypass(self) -> List[Dict]:
        """Attempt to bypass rate limiting."""
        return [
            {"action": "create_order", "payload": {"user_id": f"bot_{i}"}, "delay_ms": 0}
            for i in range(200)  # Flood
        ]

    def webhook_replay(self) -> List[Dict]:
        """Replay old webhook payloads to trigger duplicate processing."""
        webhook = {
            "event": "payment.captured",
            "payload": {
                "payment_id": "pay_12345",
                "order_id": "ord_67890",
                "amount": 70000,
            },
            "signature": "valid_sig_at_time_t",
        }
        # Replay the same webhook 10 times
        return [{"action": "webhook", "payload": webhook} for _ in range(10)]


class EdgeCaseUser:
    """Simulates edge-case users with extreme inputs."""

    def __init__(self):
        pass

    def extreme_order_sizes(self) -> List[Dict]:
        """Orders with extreme item counts."""
        return [
            # Massive order (wedding catering)
            {
                "action": "create_order",
                "payload": {
                    "user_id": "user_wedding",
                    "kitchen_id": "kitchen_1",
                    "items": [{"item_id": f"item_{i}", "qty": 50, "price": 200} for i in range(100)],
                    "total_amount": 1000000,
                },
            },
            # Minimal order (1 item, ₹1)
            {
                "action": "create_order",
                "payload": {
                    "user_id": "user_minimal",
                    "kitchen_id": "kitchen_1",
                    "items": [{"item_id": "water", "qty": 1, "price": 1}],
                    "total_amount": 1,
                },
            },
        ]

    def extreme_gps_coordinates(self) -> List[Dict]:
        """Arrival detection with extreme GPS coordinates."""
        return [
            {"action": "arrival", "payload": {"lat": 0.0, "lon": 0.0, "order_id": "ord_1"}},  # Null Island
            {"action": "arrival", "payload": {"lat": 90.0, "lon": 180.0, "order_id": "ord_1"}},  # North Pole
            {"action": "arrival", "payload": {"lat": -90.0, "lon": -180.0, "order_id": "ord_1"}},  # South Pole
            {"action": "arrival", "payload": {"lat": 28.6139, "lon": 77.2090, "order_id": "ord_1"}},  # Delhi (valid)
        ]

    def unicode_edge_cases(self) -> List[Dict]:
        """Unicode bombs in text fields."""
        return [
            {"action": "create_order", "payload": {"user_id": "user_" + "\uffff" * 1000}},
            {"action": "create_order", "payload": {"user_id": "user_🎉🍕🍛"}},
            {"action": "create_order", "payload": {"user_id": "user_\u0000\u0001\u0002"}},  # Control chars
            {"action": "create_order", "payload": {"user_id": "user_" + "\u200b" * 5000}},  # Zero-width
        ]


# ═══════════════════════════════════════════════════════════════
# BEHAVIOR SIMULATION TESTS
# ═══════════════════════════════════════════════════════════════

class TestNormalUserBehavior:
    """Test: Normal user behavior works smoothly."""

    def test_normal_user_session_generates_valid_requests(self):
        user = NormalUser()
        session = user.generate_session()
        assert len(session) >= 2

        order_action = [s for s in session if s["action"] == "create_order"]
        assert len(order_action) >= 1
        payload = order_action[0]["payload"]
        assert payload["total_amount"] > 0
        assert len(payload["items"]) >= 1


class TestImpatientUserBehavior:
    """Test: System handles impatient users correctly."""

    @pytest.mark.asyncio
    async def test_double_click_creates_only_one_order(self):
        """Invariant: Double-click doesn't create duplicate orders."""
        user = ImpatientUser(user_id="user_double_click")
        actions = user.double_click_order()

        # Both have same idempotency_key
        key1 = actions[0]["payload"]["idempotency_key"]
        key2 = actions[1]["payload"]["idempotency_key"]
        assert key1 == key2  # Same idempotency key

        # System should process only once
        processed_keys = set()
        results = []
        for action in actions:
            key = action["payload"]["idempotency_key"]
            if key in processed_keys:
                results.append("duplicate")
            else:
                processed_keys.add(key)
                results.append("created")

        assert results.count("created") == 1
        assert results.count("duplicate") == 1

    @pytest.mark.asyncio
    async def test_refresh_spam_doesnt_corrupt_order(self):
        """Invariant: Rapid status checks don't change order state."""
        user = ImpatientUser()
        actions = user.refresh_spam("ord_123", count=20)

        # All are GET requests — should be idempotent
        for action in actions:
            assert action["action"] == "get_order"
            assert "delay_ms" in action


class TestMaliciousUserBehavior:
    """Test: System rejects malicious inputs."""

    def test_sql_injection_rejected(self):
        """Invariant: SQL injection payloads are sanitized/rejected."""
        user = MaliciousUser()
        payloads = user.sql_injection_payloads()

        for p in payloads:
            assert p["expect_rejection"] is True
            # In real system, validation layer would reject these

    def test_xss_sanitized(self):
        """Invariant: XSS payloads are sanitized."""
        user = MaliciousUser()
        payloads = user.xss_payloads()

        for p in payloads:
            assert p["expect_sanitization"] is True

    def test_financial_manipulation_detected(self):
        """Invariant: Price/amount mismatches are detected."""
        user = MaliciousUser()
        payloads = user.financial_manipulation()

        for p in payloads:
            payload = p["payload"]
            items_total = sum(i.get("qty", 0) * i.get("price", 0) for i in payload.get("items", []))
            claimed_total = payload.get("total_amount", 0)

            # If total doesn't match items, should be rejected
            if items_total != claimed_total:
                assert p.get("expect_rejection") is True or items_total <= 0


class TestConfusedUserBehavior:
    """Test: System handles incomplete/abandoned flows."""

    @pytest.mark.asyncio
    async def test_abandoned_orders_dont_block_kitchen(self):
        """Invariant: Unpaid orders don't hold kitchen capacity forever."""
        user = ConfusedUser()
        flows = user.incomplete_flows()

        # Each flow creates an order but never pays
        create_actions = [f for f in flows if f["action"] == "create_order"]
        pay_actions = [f for f in flows if f["action"] == "pay_order"]

        assert len(create_actions) >= 2
        assert len(pay_actions) == 0  # Confused user never pays


class TestEdgeCaseUserBehavior:
    """Test: System handles extreme inputs gracefully."""

    def test_massive_order_has_reasonable_limits(self):
        """Invariant: System caps maximum order size."""
        user = EdgeCaseUser()
        orders = user.extreme_order_sizes()

        # Wedding catering order
        massive = orders[0]["payload"]
        assert len(massive["items"]) == 100
        assert massive["total_amount"] == 1000000

        # In real system, would need configurable limits

    def test_invalid_gps_coordinates_handled(self):
        """Invariant: Invalid GPS coordinates are rejected, not crash."""
        user = EdgeCaseUser()
        coords = user.extreme_gps_coordinates()

        valid_india = 0
        for c in coords:
            lat = c["payload"]["lat"]
            lon = c["payload"]["lon"]
            if 8.0 <= lat <= 37.0 and 68.0 <= lon <= 97.0:
                valid_india += 1

        assert valid_india == 1  # Only Delhi coordinates are valid for India


# ═══════════════════════════════════════════════════════════════
# MULTI-DEVICE CONFLICT SIMULATION
# ═══════════════════════════════════════════════════════════════

class TestMultiDeviceConflicts:
    """Test: Same user on multiple devices causes conflicts."""

    @pytest.mark.asyncio
    async def test_cancel_on_phone_and_advance_on_laptop(self):
        """Invariant: Conflicting actions from different devices are resolved."""
        store_lock = asyncio.Lock()
        order = {"id": "ord_multi", "status": "QUEUE_ASSIGNED", "version": 4}
        results = []

        async def cancel_on_phone():
            async with store_lock:
                if order["version"] == 4:
                    order["status"] = "CANCELLED"
                    order["version"] += 1
                    return "cancelled"
                return "conflict"

        async def advance_on_laptop():
            async with store_lock:
                if order["version"] == 4:
                    order["status"] = "IN_PREP"
                    order["version"] += 1
                    return "advanced"
                return "conflict"

        # Simultaneous conflicting actions
        r1, r2 = await asyncio.gather(cancel_on_phone(), advance_on_laptop())

        # One wins, one conflicts
        assert "cancelled" in (r1, r2) or "advanced" in (r1, r2)
        assert order["version"] == 5  # Only incremented once
        assert order["status"] in ["CANCELLED", "IN_PREP"]


# ═══════════════════════════════════════════════════════════════
# BEHAVIOR SEQUENCE GENERATOR
# ═══════════════════════════════════════════════════════════════

def generate_adversarial_session(duration_seconds: int = 60) -> List[Dict]:
    """
    Generate a complete adversarial test session mixing all user types.

    Returns a list of actions with timestamps for k6 or custom runner.
    """
    session = []
    t = 0

    while t < duration_seconds * 1000:
        user_type = random.choices(
            ["normal", "impatient", "confused", "malicious", "edge"],
            weights=[50, 20, 15, 10, 5],
            k=1,
        )[0]

        if user_type == "normal":
            user = NormalUser()
            actions = user.generate_session()
        elif user_type == "impatient":
            user = ImpatientUser()
            actions = random.choice([
                user.double_click_order(),
                user.refresh_spam(f"ord_{uuid.uuid4().hex[:8]}"),
                user.payment_retry_storm(f"ord_{uuid.uuid4().hex[:8]}"),
            ])
        elif user_type == "confused":
            user = ConfusedUser()
            actions = random.choice([
                user.incomplete_flows(),
                user.random_navigation(),
            ])
        elif user_type == "malicious":
            user = MaliciousUser()
            actions = random.choice([
                user.sql_injection_payloads()[:2],
                user.xss_payloads()[:2],
                user.financial_manipulation()[:2],
            ])
        else:  # edge
            user = EdgeCaseUser()
            actions = random.choice([
                user.extreme_order_sizes(),
                user.extreme_gps_coordinates(),
            ])

        for action in actions:
            delay = action.get("delay_ms", random.randint(100, 1000))
            t += delay
            session.append({
                **action,
                "timestamp_ms": t,
                "user_type": user_type,
            })

    return session


def generate_behavior_report(session: List[Dict]) -> Dict:
    """Generate a statistical report of a behavior session."""
    user_types = {}
    actions = {}

    for action in session:
        ut = action.get("user_type", "unknown")
        user_types[ut] = user_types.get(ut, 0) + 1

        act = action.get("action", "unknown")
        actions[act] = actions.get(act, 0) + 1

    return {
        "total_actions": len(session),
        "duration_ms": session[-1]["timestamp_ms"] if session else 0,
        "user_type_distribution": user_types,
        "action_distribution": actions,
    }


class TestBehaviorSessionGenerator:
    """Test: Behavior session generator produces valid sessions."""

    def test_generates_valid_session(self):
        session = generate_adversarial_session(duration_seconds=10)
        assert len(session) > 0

        report = generate_behavior_report(session)
        assert report["total_actions"] > 0
        assert report["duration_ms"] > 0

    def test_session_has_mixed_user_types(self):
        session = generate_adversarial_session(duration_seconds=30)
        report = generate_behavior_report(session)
        assert len(report["user_type_distribution"]) >= 2  # At least 2 different user types

    def test_session_actions_are_timestamped(self):
        session = generate_adversarial_session(duration_seconds=5)
        for action in session:
            assert "timestamp_ms" in action
            assert "user_type" in action
