"""HotSot Order Service — V2 Production-Grade State Machine Engine.

V2 adds 8 new states: PAYMENT_PENDING, QUEUE_ASSIGNED, BATCH_WAIT, PACKING,
ARRIVED, HANDOFF_IN_PROGRESS, EXPIRED, REFUNDED, FAILED

V2 corrects the order flow: CREATED → PAYMENT_PENDING → PAYMENT_CONFIRMED →
SLOT_RESERVED → QUEUE_ASSIGNED → IN_PREP → PACKING → READY → ON_SHELF →
ARRIVED → HANDOFF_IN_PROGRESS → PICKED

Guard rules enforce business invariants at critical transitions.
"""

from typing import Dict, List, Optional, Callable
from shared.types.schemas import (
    VALID_TRANSITIONS, TERMINAL_STATES, CANCELLABLE_STATES, GUARD_RULES,
)


class InvalidTransitionError(Exception):
    """Raised when an invalid state transition is attempted."""
    def __init__(self, current: str, target: str, reason: str = ""):
        self.current = current
        self.target = target
        self.reason = reason
        msg = f"Invalid transition: {current} → {target}"
        if reason:
            msg += f" — {reason}"
        super().__init__(msg)


class GuardViolationError(Exception):
    """Raised when a guard condition prevents a transition."""
    def __init__(self, current: str, target: str, guard: str):
        self.current = current
        self.target = target
        self.guard = guard
        super().__init__(f"Guard violation for {current} → {target}: {guard}")


class StateMachine:
    """
    V2 Order state machine that enforces valid transitions + guard rules.

    Features:
    - 16 states with full lifecycle coverage
    - Guard conditions at critical transitions
    - Terminal state detection
    - Cancellation eligibility checking
    - Explicit failure paths (EXPIRED, FAILED, REFUNDED)
    """

    def __init__(
        self,
        transitions: Optional[Dict[str, List[str]]] = None,
        guards: Optional[Dict[str, Callable]] = None,
    ):
        self.transitions = transitions or VALID_TRANSITIONS
        self.guards = guards or {}

    def can_transition(self, current: str, target: str) -> bool:
        """Check if a transition is valid without considering guards."""
        allowed = self.transitions.get(current, [])
        return target in allowed

    def transition(
        self,
        current: str,
        target: str,
        context: Optional[Dict] = None,
    ) -> str:
        """
        Execute a state transition with guard validation.

        Args:
            current: Current state
            target: Target state
            context: Optional context dict for guard evaluation
                    (e.g., {"shelf_id": "A3", "gps_distance": 120})

        Returns:
            New state string

        Raises:
            InvalidTransitionError: If transition is not in valid transitions
            GuardViolationError: If guard condition is not met
        """
        if not self.can_transition(current, target):
            raise InvalidTransitionError(
                current, target,
                f"Allowed transitions from {current}: {self.transitions.get(current, [])}"
            )

        # Evaluate guard rule if exists for target state
        guard_rule = GUARD_RULES.get(target)
        if guard_rule and context:
            if not self._evaluate_guard(target, context):
                raise GuardViolationError(
                    current, target, guard_rule["condition"]
                )

        return target

    def _evaluate_guard(self, target_state: str, context: Dict) -> bool:
        """Evaluate guard condition for a transition using context data."""
        if target_state == "SLOT_RESERVED":
            slot_available = context.get("slot_available", True)
            waitlist_allowed = context.get("waitlist_allowed", False)
            return slot_available or waitlist_allowed

        if target_state == "QUEUE_ASSIGNED":
            priority_computed = context.get("priority_score") is not None
            return priority_computed

        if target_state == "ON_SHELF":
            shelf_id = context.get("shelf_id")
            shelf_capacity = context.get("shelf_capacity", 0)
            return shelf_id is not None and shelf_capacity > 0

        if target_state == "ARRIVED":
            gps_distance = context.get("gps_distance", 999)
            qr_scan_valid = context.get("qr_scan_valid", False)
            return gps_distance <= 150 or qr_scan_valid

        if target_state == "EXPIRED":
            state = context.get("current_state", "")
            ttl_exceeded = context.get("shelf_ttl_exceeded", False)
            return ttl_exceeded and state != "PICKED"

        return True  # No guard — allow by default

    def get_valid_next_states(self, current: str) -> List[str]:
        """Get all valid next states from current state."""
        return self.transitions.get(current, [])

    def is_terminal(self, state: str) -> bool:
        """Check if state is terminal (no further transitions possible)."""
        return state in TERMINAL_STATES

    def is_cancellable(self, state: str) -> bool:
        """Check if order can be cancelled from this state."""
        return state in CANCELLABLE_STATES

    def get_state_info(self, state: str) -> Dict:
        """Get detailed info about a state."""
        return {
            "state": state,
            "is_terminal": self.is_terminal(state),
            "is_cancellable": self.is_cancellable(state),
            "valid_next_states": self.get_valid_next_states(state),
            "guard_rule": GUARD_RULES.get(state),
        }

    def validate_sequence(self, transitions: List[Dict]) -> List[str]:
        """
        Validate a sequence of transitions for correctness.

        Args:
            transitions: List of {"from": state, "to": state} dicts

        Returns:
            List of validation errors (empty if valid)
        """
        errors = []
        current = "CREATED"
        for i, t in enumerate(transitions):
            if t["from"] != current:
                errors.append(
                    f"Step {i}: expected from={current}, got from={t['from']}"
                )
            if not self.can_transition(t["from"], t["to"]):
                errors.append(
                    f"Step {i}: invalid transition {t['from']} → {t['to']}"
                )
            current = t["to"]
        return errors


# Singleton instance
state_machine = StateMachine()
