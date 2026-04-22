"""
HotSot — PHASE 2C: Concurrency Testing

Tests for race conditions, duplicate requests, and transaction conflicts.
These are the bugs that only appear under concurrent load.

Scenarios:
    1. Duplicate order creation (same idempotency key)
    2. Concurrent status updates (optimistic locking)
    3. Duplicate payment confirmations
    4. Race condition: cancel + advance simultaneously
    5. Duplicate arrival detection
    6. Concurrent shelf assignments
    7. Queue reorder while items being dequeued
"""

import asyncio
import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List

import pytest


# ═══════════════════════════════════════════════════════════════
# CONCURRENT ORDER OPERATIONS
# ═══════════════════════════════════════════════════════════════

class ConcurrentOrderStore:
    """Thread-safe order store for concurrency testing."""

    def __init__(self):
        self.orders = {}
        self.lock = asyncio.Lock()
        self.conflict_count = 0

    async def create_order(self, order_id: str, data: dict) -> dict:
        """Create order with idempotency check."""
        async with self.lock:
            if order_id in self.orders:
                return {"status": "duplicate", "order": self.orders[order_id]}
            self.orders[order_id] = {**data, "version": 1, "status": "CREATED"}
            return {"status": "created", "order": self.orders[order_id]}

    async def update_status(
        self, order_id: str, new_status: str, expected_version: int
    ) -> dict:
        """Update order status with optimistic locking."""
        async with self.lock:
            order = self.orders.get(order_id)
            if not order:
                return {"status": "not_found"}

            if order["version"] != expected_version:
                self.conflict_count += 1
                return {
                    "status": "conflict",
                    "current_version": order["version"],
                    "expected_version": expected_version,
                }

            order["status"] = new_status
            order["version"] += 1
            return {"status": "updated", "order": order}

    async def get_order(self, order_id: str) -> dict:
        return self.orders.get(order_id)


class TestDuplicateOrderCreation:
    """Test: Same idempotency key doesn't create duplicate orders."""

    @pytest.mark.asyncio
    async def test_duplicate_create_returns_same_order(self):
        """Invariant: Duplicate order with same idempotency_key returns original."""
        store = ConcurrentOrderStore()
        order_id = f"ord_{uuid.uuid4().hex[:12]}"
        order_data = {
            "id": order_id,
            "user_id": "user_123",
            "kitchen_id": "kitchen_1",
            "total_amount": 700,
            "idempotency_key": "idem_user123_kitchen1_abc",
        }

        # First create
        result1 = await store.create_order(order_id, order_data)
        assert result1["status"] == "created"

        # Duplicate create
        result2 = await store.create_order(order_id, order_data)
        assert result2["status"] == "duplicate"

        # Same order returned
        assert result1["order"]["id"] == result2["order"]["id"]

    @pytest.mark.asyncio
    async def test_concurrent_duplicate_creates_only_one(self):
        """Invariant: 100 concurrent creates with same ID → only 1 order."""
        store = ConcurrentOrderStore()
        order_id = "ord_concurrent_test"

        # Launch 100 concurrent creates
        tasks = [
            store.create_order(order_id, {"id": order_id, "user_id": f"user_{i}"})
            for i in range(100)
        ]
        results = await asyncio.gather(*tasks)

        created = [r for r in results if r["status"] == "created"]
        duplicates = [r for r in results if r["status"] == "duplicate"]

        assert len(created) == 1, f"Expected 1 creation, got {len(created)}"
        assert len(duplicates) == 99, f"Expected 99 duplicates, got {len(duplicates)}"


class TestOptimisticLocking:
    """Test: Concurrent updates cause version conflicts, not data corruption."""

    @pytest.mark.asyncio
    async def test_concurrent_status_updates_cause_conflict(self):
        """Test: Two concurrent updates → one succeeds, one gets conflict."""
        store = ConcurrentOrderStore()
        order_id = f"ord_{uuid.uuid4().hex[:12]}"

        await store.create_order(order_id, {"id": order_id, "user_id": "user_1"})

        # Both try to update from version 1
        result1 = await store.update_status(order_id, "PAYMENT_PENDING", expected_version=1)
        result2 = await store.update_status(order_id, "CANCELLED", expected_version=1)

        # One succeeds, one conflicts
        statuses = {result1["status"], result2["status"]}
        assert "updated" in statuses
        assert "conflict" in statuses

        # Final order is consistent
        order = await store.get_order(order_id)
        assert order["version"] == 2
        assert order["status"] in ["PAYMENT_PENDING", "CANCELLED"]

    @pytest.mark.asyncio
    async def test_high_concurrency_stress(self):
        """Test: 50 concurrent updates → exactly 1 succeeds, 49 conflict."""
        store = ConcurrentOrderStore()
        order_id = f"ord_stress_{uuid.uuid4().hex[:8]}"

        await store.create_order(order_id, {"id": order_id, "user_id": "user_1"})

        # 50 concurrent updates from version 1
        tasks = [
            store.update_status(order_id, f"STATUS_{i}", expected_version=1)
            for i in range(50)
        ]
        results = await asyncio.gather(*tasks)

        updated = [r for r in results if r["status"] == "updated"]
        conflicts = [r for r in results if r["status"] == "conflict"]

        assert len(updated) == 1
        assert len(conflicts) == 49

        # Final state is consistent
        order = await store.get_order(order_id)
        assert order["version"] == 2

    @pytest.mark.asyncio
    async def test_retry_after_conflict_succeeds(self):
        """Test: After conflict, retry with correct version succeeds."""
        store = ConcurrentOrderStore()
        order_id = f"ord_retry_{uuid.uuid4().hex[:8]}"

        await store.create_order(order_id, {"id": order_id, "user_id": "user_1"})

        # First update succeeds
        result1 = await store.update_status(order_id, "PAYMENT_PENDING", expected_version=1)
        assert result1["status"] == "updated"

        # Second update with stale version fails
        result2 = await store.update_status(order_id, "PAYMENT_CONFIRMED", expected_version=1)
        assert result2["status"] == "conflict"

        # Retry with correct version
        current_version = result1["order"]["version"]
        result3 = await store.update_status(order_id, "PAYMENT_CONFIRMED", expected_version=current_version)
        assert result3["status"] == "updated"


class TestRaceConditionCancelVsAdvance:
    """Test: Cancel and advance status simultaneously."""

    @pytest.mark.asyncio
    async def test_cancel_and_advance_race(self):
        """Invariant: Either cancel OR advance wins, not both."""
        store = ConcurrentOrderStore()
        order_id = f"ord_race_{uuid.uuid4().hex[:8]}"

        await store.create_order(order_id, {"id": order_id, "user_id": "user_1"})

        # Simultaneous: cancel + advance to PAYMENT_PENDING
        cancel_task = store.update_status(order_id, "CANCELLED", expected_version=1)
        advance_task = store.update_status(order_id, "PAYMENT_PENDING", expected_version=1)

        results = await asyncio.gather(cancel_task, advance_task)
        statuses = {r["status"] for r in results}

        # One wins, one conflicts
        assert "updated" in statuses
        assert "conflict" in statuses

        # Final state is one of the two
        order = await store.get_order(order_id)
        assert order["status"] in ["CANCELLED", "PAYMENT_PENDING"]


class TestDuplicatePaymentConfirmation:
    """Test: Duplicate payment webhook doesn't double-charge."""

    @pytest.mark.asyncio
    async def test_duplicate_webhook_ignored(self):
        """Invariant: Same payment_id processed only once."""
        processed_payments = set()
        payment_id = "pay_razorpay_12345"

        async def process_webhook(pid, amount, signature):
            """Idempotent webhook handler."""
            if pid in processed_payments:
                return {"status": "already_processed", "payment_id": pid}
            processed_payments.add(pid)
            return {"status": "captured", "payment_id": pid, "amount": amount}

        # First webhook
        result1 = await process_webhook(payment_id, 70000, "sig1")
        assert result1["status"] == "captured"

        # Duplicate webhook (network retry)
        result2 = await process_webhook(payment_id, 70000, "sig1")
        assert result2["status"] == "already_processed"

    @pytest.mark.asyncio
    async def test_concurrent_duplicate_webhooks(self):
        """Invariant: 10 concurrent webhooks → payment captured exactly once."""
        processed = set()
        lock = asyncio.Lock()
        capture_count = 0

        async def process_webhook(pid):
            nonlocal capture_count
            async with lock:
                if pid in processed:
                    return {"status": "duplicate"}
                processed.add(pid)
                capture_count += 1
                return {"status": "captured"}

        payment_id = "pay_concurrent_123"
        tasks = [process_webhook(payment_id) for _ in range(10)]
        results = await asyncio.gather(*tasks)

        captured = [r for r in results if r["status"] == "captured"]
        duplicates = [r for r in results if r["status"] == "duplicate"]

        assert capture_count == 1
        assert len(captured) == 1
        assert len(duplicates) == 9


class TestDuplicateArrivalDetection:
    """Test: Duplicate arrival events are deduplicated."""

    @pytest.mark.asyncio
    async def test_same_arrival_deduplicated(self):
        """Invariant: Same user+order arrival within 30s is deduplicated."""
        arrival_log = []
        dedup_keys = set()
        lock = asyncio.Lock()

        async def detect_arrival(user_id, order_id, lat, lon):
            from shared.utils.helpers import arrival_dedup_key
            key = arrival_dedup_key(user_id, order_id)

            async with lock:
                if key in dedup_keys:
                    arrival_log.append({"type": "duplicate", "user": user_id, "order": order_id})
                    return {"status": "duplicate"}
                dedup_keys.add(key)
                arrival_log.append({"type": "new", "user": user_id, "order": order_id})
                return {"status": "detected"}

        # First arrival
        result1 = await detect_arrival("user_1", "ord_1", 28.6, 77.2)
        assert result1["status"] == "detected"

        # Duplicate (same user, same order)
        result2 = await detect_arrival("user_1", "ord_1", 28.6, 77.2)
        assert result2["status"] == "duplicate"

        # Different user
        result3 = await detect_arrival("user_2", "ord_1", 28.6, 77.2)
        assert result3["status"] == "detected"

        assert len([a for a in arrival_log if a["type"] == "new"]) == 2
        assert len([a for a in arrival_log if a["type"] == "duplicate"]) == 1


class TestConcurrentShelfAssignment:
    """Test: Same order isn't assigned to two shelf slots."""

    @pytest.mark.asyncio
    async def test_double_shelf_assignment_prevented(self):
        """Invariant: Order can only be assigned to one shelf slot."""
        shelf_assignments = {}
        lock = asyncio.Lock()

        async def assign_shelf(order_id, zone, slot):
            async with lock:
                if order_id in shelf_assignments:
                    return {"status": "already_assigned", "slot": shelf_assignments[order_id]}
                shelf_assignments[order_id] = {"zone": zone, "slot": slot}
                return {"status": "assigned", "zone": zone, "slot": slot}

        # Two concurrent assignments for same order
        results = await asyncio.gather(
            assign_shelf("ord_1", "HOT", "A1"),
            assign_shelf("ord_1", "COLD", "B3"),
        )

        assigned = [r for r in results if r["status"] == "assigned"]
        already = [r for r in results if r["status"] == "already_assigned"]

        assert len(assigned) == 1
        assert len(already) == 1
        assert "ord_1" in shelf_assignments
        assert len(shelf_assignments) == 1  # Only one assignment
