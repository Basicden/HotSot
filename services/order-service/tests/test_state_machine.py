"""HotSot Order Service — Unit Tests."""
import pytest
from app.core.state_machine import OrderStateMachine, GuardViolationError

sm = OrderStateMachine()

# ─── State Machine Tests ───────────────────────────────────
def test_valid_happy_path():
    """Full happy path: CREATED → PICKED"""
    transitions = [
        ("CREATED", "PAYMENT_PENDING"),
        ("PAYMENT_PENDING", "PAYMENT_CONFIRMED"),
        ("PAYMENT_CONFIRMED", "SLOT_RESERVED"),
        ("SLOT_RESERVED", "QUEUE_ASSIGNED"),
        ("QUEUE_ASSIGNED", "IN_PREP"),
        ("IN_PREP", "PACKING"),
        ("PACKING", "READY"),
        ("READY", "ON_SHELF"),
        ("ON_SHELF", "ARRIVED"),
        ("ARRIVED", "HANDOFF_IN_PROGRESS"),
        ("HANDOFF_IN_PROGRESS", "PICKED"),
    ]
    for from_s, to_s in transitions:
        assert sm.can_transition(from_s, to_s), f"Should allow {from_s} → {to_s}"


def test_invalid_transition():
    """Cannot skip states"""
    assert not sm.can_transition("CREATED", "IN_PREP")
    assert not sm.can_transition("PAYMENT_PENDING", "READY")


def test_terminal_states_no_exit():
    """Terminal states accept no further transitions"""
    for terminal in ["PICKED", "CANCELLED", "FAILED"]:
        assert not sm.can_transition(terminal, "CREATED")
        assert not sm.can_transition(terminal, "IN_PREP")


def test_cancel_from_pre_prep():
    """CANCELLED allowed from any pre-PICKED state"""
    for state in ["CREATED", "PAYMENT_PENDING", "QUEUE_ASSIGNED", "IN_PREP", "READY", "ON_SHELF"]:
        assert sm.can_transition(state, "CANCELLED"), f"Should allow {state} → CANCELLED"


def test_refund_after_payment():
    """REFUNDED allowed from PAYMENT_CONFIRMED onwards"""
    assert sm.can_transition("PAYMENT_CONFIRMED", "REFUNDED")
    assert sm.can_transition("ON_SHELF", "REFUNDED")


def test_no_refund_before_payment():
    """REFUNDED NOT allowed before payment"""
    assert not sm.can_transition("CREATED", "REFUNDED")


def test_expired_from_on_shelf():
    """EXPIRED only from ON_SHELF"""
    assert sm.can_transition("ON_SHELF", "EXPIRED")
    assert not sm.can_transition("IN_PREP", "EXPIRED")


def test_expired_to_refunded():
    """EXPIRED → REFUNDED is valid"""
    assert sm.can_transition("EXPIRED", "REFUNDED")


def test_failed_from_any_prepick():
    """FAILED from any pre-PICKED state"""
    assert sm.can_transition("IN_PREP", "FAILED")
    assert sm.can_transition("QUEUE_ASSIGNED", "FAILED")


def test_total_states():
    """V2 has exactly 16 states"""
    assert len(sm.STATES) == 16
