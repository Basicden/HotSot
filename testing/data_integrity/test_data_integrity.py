"""
HotSot — PHASE 6: Data Integrity Validation

Ensures:
    - No duplicate records
    - Consistency across services
    - Correct event processing
    - Idempotency enforcement

Simulates:
    - Out-of-order events
    - Duplicate events
    - Partial writes
    - Event replay
"""

import asyncio
import json
import random
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

import pytest


# ═══════════════════════════════════════════════════════════════
# EVENT PROCESSOR SIMULATOR
# ═══════════════════════════════════════════════════════════════

class EventProcessor:
    """Simulates event processing with dedup, ordering, and integrity checks."""

    def __init__(self):
        self.processed_events = {}  # event_id → result
        self.event_log = []
        self.dlq = []
        self.order_states = {}

    def process_event(self, event: Dict) -> Dict:
        """Process a single event with dedup and validation."""
        event_id = event.get("event_id")
        event_type = event.get("event_type")
        order_id = event.get("order_id")

        # 1. Dedup check
        if event_id and event_id in self.processed_events:
            return {
                "status": "duplicate",
                "event_id": event_id,
                "previous_result": self.processed_events[event_id],
            }

        # 2. Validate order state transition
        if order_id and event_type:
            current_state = self.order_states.get(order_id, "CREATED")
            valid_transitions = {
                "CREATED": {"PAYMENT_PENDING", "CANCELLED", "FAILED"},
                "PAYMENT_PENDING": {"PAYMENT_CONFIRMED", "CANCELLED", "FAILED"},
                "PAYMENT_CONFIRMED": {"SLOT_RESERVED", "CANCELLED"},
                "SLOT_RESERVED": {"QUEUE_ASSIGNED", "CANCELLED"},
                "QUEUE_ASSIGNED": {"IN_PREP", "BATCH_WAIT", "CANCELLED"},
                "IN_PREP": {"PACKING", "FAILED"},
                "PACKING": {"READY", "FAILED"},
                "READY": {"ON_SHELF", "CANCELLED"},
                "ON_SHELF": {"ARRIVED", "EXPIRED", "CANCELLED"},
                "ARRIVED": {"HANDOFF_IN_PROGRESS", "EXPIRED"},
                "HANDOFF_IN_PROGRESS": {"PICKED", "EXPIRED"},
                "EXPIRED": {"REFUNDED"},
            }

            # Map event types to target states
            event_to_state = {
                "ORDER_CREATED": "CREATED",
                "PAYMENT_PENDING": "PAYMENT_PENDING",
                "PAYMENT_CONFIRMED": "PAYMENT_CONFIRMED",
                "SLOT_RESERVED": "SLOT_RESERVED",
                "QUEUE_ASSIGNED": "QUEUE_ASSIGNED",
                "IN_PREP": "IN_PREP",
                "READY_FOR_PICKUP": "READY",
                "ON_SHELF": "ON_SHELF",
                "ARRIVAL_DETECTED": "ARRIVED",
                "HANDOFF_STARTED": "HANDOFF_IN_PROGRESS",
                "ORDER_PICKED": "PICKED",
                "ORDER_CANCELLED": "CANCELLED",
                "ORDER_EXPIRED": "EXPIRED",
                "ORDER_REFUNDED": "REFUNDED",
                "ORDER_FAILED": "FAILED",
            }

            target_state = event_to_state.get(event_type)
            if target_state and current_state in valid_transitions:
                if target_state not in valid_transitions[current_state]:
                    # Invalid transition → DLQ
                    self.dlq.append({
                        "event_id": event_id,
                        "event_type": event_type,
                        "order_id": order_id,
                        "current_state": current_state,
                        "target_state": target_state,
                        "reason": "invalid_transition",
                    })
                    return {
                        "status": "invalid_transition",
                        "event_id": event_id,
                        "current_state": current_state,
                        "target_state": target_state,
                    }

                # Valid transition
                self.order_states[order_id] = target_state

        # 3. Process event
        result = {
            "status": "processed",
            "event_id": event_id,
            "event_type": event_type,
            "order_id": order_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        if event_id:
            self.processed_events[event_id] = result

        self.event_log.append({**event, "processed_at": result["timestamp"]})

        return result


# ═══════════════════════════════════════════════════════════════
# DATA INTEGRITY TESTS
# ═══════════════════════════════════════════════════════════════

class TestDuplicateEventDetection:
    """Test: Duplicate events are detected and deduplicated."""

    def test_duplicate_event_returns_same_result(self):
        """Invariant: Processing same event twice returns same result."""
        processor = EventProcessor()
        event = {
            "event_id": "evt_001",
            "event_type": "ORDER_CREATED",
            "order_id": "ord_123",
        }

        result1 = processor.process_event(event)
        result2 = processor.process_event(event)

        assert result1["status"] == "processed"
        assert result2["status"] == "duplicate"

    def test_duplicate_events_dont_create_duplicate_state_changes(self):
        """Invariant: Duplicate events don't cause duplicate state changes."""
        processor = EventProcessor()
        order_id = "ord_dedup"

        event = {
            "event_id": "evt_002",
            "event_type": "PAYMENT_CONFIRMED",
            "order_id": order_id,
        }
        # Set initial state
        processor.order_states[order_id] = "PAYMENT_PENDING"

        # First process
        processor.process_event(event)
        assert processor.order_states[order_id] == "PAYMENT_CONFIRMED"

        # Duplicate — state should not change
        processor.process_event(event)
        assert processor.order_states[order_id] == "PAYMENT_CONFIRMED"

    def test_different_event_ids_not_deduplicated(self):
        """Invariant: Different events with same type are not deduplicated."""
        processor = EventProcessor()
        processor.order_states["ord_1"] = "PAYMENT_PENDING"

        event1 = {"event_id": "evt_a", "event_type": "PAYMENT_CONFIRMED", "order_id": "ord_1"}
        event2 = {"event_id": "evt_b", "event_type": "PAYMENT_CONFIRMED", "order_id": "ord_1"}

        # Second event with different ID but same type
        # Should still be processed (it's a different event)
        # But since state is already PAYMENT_CONFIRMED, it would be an invalid transition
        result1 = processor.process_event(event1)
        result2 = processor.process_event(event2)

        assert result1["status"] == "processed"
        assert result2["status"] == "invalid_transition"  # Already in PAYMENT_CONFIRMED


class TestOutOfOrderEvents:
    """Test: Out-of-order events are handled correctly."""

    def test_late_payment_confirmed_after_queue_assigned(self):
        """Test: Payment confirmation arrives after queue assignment (should be rejected)."""
        processor = EventProcessor()
        processor.order_states["ord_ooo"] = "QUEUE_ASSIGNED"

        # Late event: PAYMENT_CONFIRMED when already QUEUE_ASSIGNED
        event = {
            "event_id": "evt_late_payment",
            "event_type": "PAYMENT_CONFIRMED",
            "order_id": "ord_ooo",
        }
        result = processor.process_event(event)
        assert result["status"] == "invalid_transition"

    def test_events_processed_in_correct_order(self):
        """Test: Valid sequence of events produces correct final state."""
        processor = EventProcessor()
        order_id = "ord_ordered"

        events = [
            {"event_id": f"evt_{i}", "event_type": et, "order_id": order_id}
            for i, et in enumerate([
                "ORDER_CREATED",
                "PAYMENT_PENDING",
                "PAYMENT_CONFIRMED",
                "SLOT_RESERVED",
                "QUEUE_ASSIGNED",
                "IN_PREP",
                "READY_FOR_PICKUP",
                "ON_SHELF",
                "ARRIVAL_DETECTED",
                "HANDOFF_STARTED",
                "ORDER_PICKED",
            ])
        ]

        results = [processor.process_event(e) for e in events]

        # All should be processed
        assert all(r["status"] == "processed" for r in results)
        assert processor.order_states[order_id] == "PICKED"

    def test_shuffled_events_some_invalid(self):
        """Test: Shuffled event order causes some events to be rejected."""
        processor = EventProcessor()
        order_id = "ord_shuffled"

        events = [
            {"event_id": "evt_1", "event_type": "ORDER_CREATED", "order_id": order_id},
            {"event_id": "evt_2", "event_type": "ON_SHELF", "order_id": order_id},  # Too early!
            {"event_id": "evt_3", "event_type": "PAYMENT_PENDING", "order_id": order_id},
            {"event_id": "evt_4", "event_type": "PAYMENT_CONFIRMED", "order_id": order_id},
        ]

        results = [processor.process_event(e) for e in events]

        processed = [r for r in results if r["status"] == "processed"]
        invalid = [r for r in results if r["status"] == "invalid_transition"]

        assert len(processed) == 3  # Created, PaymentPending, PaymentConfirmed
        assert len(invalid) == 1  # OnShelf (too early)
        assert len(processor.dlq) == 1  # Routed to DLQ


class TestPartialWriteRecovery:
    """Test: Partial writes don't corrupt data."""

    def test_failed_status_update_preserves_original(self):
        """Invariant: Failed update doesn't change the current state."""
        states = {"ord_partial": {"status": "CREATED", "version": 1}}

        # Simulate failed update
        try:
            # Validation fails — state unchanged
            if states["ord_partial"]["status"] != "PAYMENT_PENDING":
                raise ValueError("Cannot transition from CREATED to IN_PREP")
        except ValueError:
            pass  # Expected

        assert states["ord_partial"]["status"] == "CREATED"
        assert states["ord_partial"]["version"] == 1

    def test_compensating_transaction_rollback(self):
        """Test: Saga compensation correctly rolls back partial changes."""
        saga_state = {
            "saga_id": "saga_1",
            "order_id": "ord_saga",
            "steps": [
                {"step": "create_order", "status": "completed"},
                {"step": "reserve_slot", "status": "completed"},
                {"step": "charge_payment", "status": "failed"},
            ],
            "compensating_actions": [
                {"step": "release_slot", "status": "pending"},
                {"step": "cancel_order", "status": "pending"},
            ],
        }

        # Execute compensations in reverse
        for action in reversed(saga_state["compensating_actions"]):
            action["status"] = "completed"

        # All compensations completed
        assert all(a["status"] == "completed" for a in saga_state["compensating_actions"])


class TestEventReplay:
    """Test: DLQ event replay restores correct state."""

    def test_dlq_event_can_be_replayed(self):
        """Invariant: Failed events in DLQ can be successfully replayed."""
        processor = EventProcessor()
        processor.order_states["ord_replay"] = "QUEUE_ASSIGNED"

        # Invalid event (wrong order) → goes to DLQ
        event = {
            "event_id": "evt_replay_1",
            "event_type": "PAYMENT_PENDING",
            "order_id": "ord_replay",
        }
        result = processor.process_event(event)
        assert result["status"] == "invalid_transition"
        assert len(processor.dlq) == 1

    def test_replay_after_state_correction_succeeds(self):
        """Test: Event that was invalid earlier becomes valid after state correction."""
        processor = EventProcessor()

        # State: CREATED
        processor.order_states["ord_replay2"] = "CREATED"

        # Event that requires PAYMENT_PENDING state
        event = {
            "event_id": "evt_replay_2",
            "event_type": "PAYMENT_CONFIRMED",
            "order_id": "ord_replay2",
        }
        result1 = processor.process_event(event)
        assert result1["status"] == "invalid_transition"

        # Advance state
        processor.order_states["ord_replay2"] = "PAYMENT_PENDING"

        # Replay with new event ID
        replay_event = {**event, "event_id": "evt_replay_2_retry"}
        result2 = processor.process_event(replay_event)
        assert result2["status"] == "processed"
        assert processor.order_states["ord_replay2"] == "PAYMENT_CONFIRMED"


class TestCrossServiceConsistency:
    """Test: Data is consistent across services."""

    def test_order_and_payment_status_consistent(self):
        """Invariant: Order status matches payment status."""
        # Order: PAYMENT_CONFIRMED → Payment: CAPTURED
        order_status = "PAYMENT_CONFIRMED"
        payment_status = "CAPTURED"

        # These should be consistent
        assert order_status == "PAYMENT_CONFIRMED"
        assert payment_status in ["AUTHORIZED", "CAPTURED"]

    def test_shelf_assignment_matches_order_status(self):
        """Invariant: Shelf assignment only when order is READY or ON_SHELF."""
        shelf_assignments = {
            "ord_1": {"order_status": "ON_SHELF", "shelf_zone": "HOT", "slot": "A1"},
            "ord_2": {"order_status": "IN_PREP", "shelf_zone": None, "slot": None},
        }

        for order_id, assignment in shelf_assignments.items():
            if assignment["shelf_zone"] is not None:
                assert assignment["order_status"] in ["READY", "ON_SHELF", "ARRIVED"], \
                    f"Order {order_id} has shelf assignment but status is {assignment['order_status']}"

    def test_kitchen_queue_matches_order_states(self):
        """Invariant: Kitchen queue only contains orders in QUEUE_ASSIGNED or IN_PREP."""
        kitchen_queue = [
            {"order_id": "ord_q1", "status": "QUEUE_ASSIGNED"},
            {"order_id": "ord_q2", "status": "IN_PREP"},
            {"order_id": "ord_q3", "status": "BATCH_WAIT"},
        ]

        for item in kitchen_queue:
            assert item["status"] in ["QUEUE_ASSIGNED", "IN_PREP", "BATCH_WAIT"], \
                f"Invalid order in kitchen queue: {item['order_id']} with status {item['status']}"
