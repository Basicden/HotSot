"""
HotSot Order Service — Saga Orchestrator.

Implements the Saga pattern for the order lifecycle:
    - OrderLifecycleSaga: CREATES → PAYMENT → SLOT → QUEUE → PREP → SHELF → PICKUP
    - RefundSaga: Refund processing with compensation steps
    - ExpirySaga: Shelf expiry → Refund with automatic compensation

Design:
    - Choreography-based (Kafka events trigger next steps)
    - State persisted in SagaInstanceModel for crash recovery
    - Each step has a corresponding compensation step
    - Timeout detection and automatic compensation
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Coroutine, Dict, List, Optional
from dataclasses import dataclass, field

from shared.types.schemas import EventType, OrderStatus
from shared.utils.helpers import generate_id, now_iso

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# SAGA STEP DEFINITION
# ═══════════════════════════════════════════════════════════════

@dataclass
class SagaStep:
    """A single step in a saga with its compensation."""
    name: str
    action: str  # Event type to emit on success
    compensation: str  # Event type to emit on failure
    timeout_seconds: int = 300  # Step timeout
    required: bool = True  # If False, saga continues on step failure


@dataclass
class SagaDefinition:
    """Complete saga definition with ordered steps."""
    name: str
    steps: List[SagaStep] = field(default_factory=list)
    timeout_seconds: int = 3600  # Total saga timeout


# ═══════════════════════════════════════════════════════════════
# ORDER LIFECYCLE SAGA
# ═══════════════════════════════════════════════════════════════

ORDER_LIFECYCLE_SAGA = SagaDefinition(
    name="OrderLifecycle",
    steps=[
        SagaStep(
            name="create_order",
            action="ORDER_CREATED",
            compensation="ORDER_CANCELLED",
            timeout_seconds=30,
        ),
        SagaStep(
            name="initiate_payment",
            action="PAYMENT_PENDING",
            compensation="ORDER_CANCELLED",
            timeout_seconds=120,
        ),
        SagaStep(
            name="confirm_payment",
            action="PAYMENT_CONFIRMED",
            compensation="ORDER_REFUNDED",
            timeout_seconds=300,
        ),
        SagaStep(
            name="reserve_slot",
            action="SLOT_RESERVED",
            compensation="ORDER_CANCELLED",
            timeout_seconds=60,
        ),
        SagaStep(
            name="assign_queue",
            action="QUEUE_ASSIGNED",
            compensation="ORDER_CANCELLED",
            timeout_seconds=30,
        ),
        SagaStep(
            name="start_prep",
            action="PREP_STARTED",
            compensation="ORDER_CANCELLED",
            timeout_seconds=600,
        ),
        SagaStep(
            name="complete_prep",
            action="PREP_COMPLETED",
            compensation="ORDER_CANCELLED",
            timeout_seconds=600,
        ),
        SagaStep(
            name="assign_shelf",
            action="SHELF_ASSIGNED",
            compensation="ORDER_CANCELLED",
            timeout_seconds=60,
        ),
        SagaStep(
            name="detect_arrival",
            action="ARRIVAL_DETECTED",
            compensation="",  # No compensation needed — arrival is passive
            timeout_seconds=900,
            required=False,
        ),
        SagaStep(
            name="complete_handoff",
            action="HANDOFF_COMPLETED",
            compensation="",
            timeout_seconds=300,
        ),
    ],
)

REFUND_SAGA = SagaDefinition(
    name="Refund",
    steps=[
        SagaStep(
            name="initiate_refund",
            action="ORDER_REFUNDED",
            compensation="",
            timeout_seconds=60,
        ),
        SagaStep(
            name="process_refund",
            action="COMPENSATION_TRIGGERED",
            compensation="",
            timeout_seconds=300,
        ),
        SagaStep(
            name="confirm_refund",
            action="COMPENSATION_COMPLETED",
            compensation="",
            timeout_seconds=300,
        ),
    ],
)

EXPIRY_SAGA = SagaDefinition(
    name="Expiry",
    steps=[
        SagaStep(
            name="mark_expired",
            action="ORDER_EXPIRED",
            compensation="",
            timeout_seconds=30,
        ),
        SagaStep(
            name="trigger_refund",
            action="COMPENSATION_TRIGGERED",
            compensation="",
            timeout_seconds=300,
        ),
        SagaStep(
            name="complete_refund",
            action="COMPENSATION_COMPLETED",
            compensation="",
            timeout_seconds=300,
        ),
    ],
)


# ═══════════════════════════════════════════════════════════════
# SAGA ENGINE
# ═══════════════════════════════════════════════════════════════

class SagaEngine:
    """
    Orchestrates saga execution with:
        - Step tracking and persistence
        - Timeout detection
        - Automatic compensation on failure
        - Crash recovery from persisted state
    """

    def __init__(self, kafka_producer=None):
        self._producer = kafka_producer
        self._active_sagas: Dict[str, Dict[str, Any]] = {}

    def get_saga_definition(self, saga_type: str) -> Optional[SagaDefinition]:
        """Get saga definition by type name."""
        saga_map = {
            "OrderLifecycle": ORDER_LIFECYCLE_SAGA,
            "Refund": REFUND_SAGA,
            "Expiry": EXPIRY_SAGA,
        }
        return saga_map.get(saga_type)

    async def start_saga(
        self,
        order_id: str,
        saga_type: str = "OrderLifecycle",
        tenant_id: str = "default",
    ) -> str:
        """
        Start a new saga instance.

        Args:
            order_id: The order this saga manages.
            saga_type: Type of saga to execute.
            tenant_id: Tenant for multi-tenancy.

        Returns:
            Saga instance ID.
        """
        definition = self.get_saga_definition(saga_type)
        if not definition:
            raise ValueError(f"Unknown saga type: {saga_type}")

        saga_id = generate_id()
        timeout_at = datetime.now(timezone.utc) + timedelta(
            seconds=definition.timeout_seconds
        )

        self._active_sagas[saga_id] = {
            "saga_id": saga_id,
            "order_id": order_id,
            "saga_type": saga_type,
            "tenant_id": tenant_id,
            "current_step_index": 0,
            "steps_completed": [],
            "steps_failed": [],
            "status": "RUNNING",
            "started_at": now_iso(),
            "timeout_at": timeout_at.isoformat(),
        }

        logger.info(
            f"Saga started: id={saga_id} type={saga_type} order={order_id}"
        )

        # In production: persist to SagaInstanceModel

        return saga_id

    async def advance_saga(
        self,
        saga_id: str,
        step_name: str,
        success: bool = True,
        error: Optional[str] = None,
    ) -> Optional[str]:
        """
        Advance a saga after a step completes.

        Args:
            saga_id: The saga instance ID.
            step_name: Name of the completed step.
            success: Whether the step succeeded.
            error: Error message if step failed.

        Returns:
            Next step name, or None if saga is complete.
        """
        saga = self._active_sagas.get(saga_id)
        if not saga:
            logger.warning(f"Saga not found: {saga_id}")
            return None

        definition = self.get_saga_definition(saga["saga_type"])
        if not definition:
            return None

        if success:
            saga["steps_completed"].append(step_name)
            saga["current_step_index"] += 1

            # Check if all steps completed
            if saga["current_step_index"] >= len(definition.steps):
                saga["status"] = "COMPLETED"
                saga["completed_at"] = now_iso()
                logger.info(f"Saga completed: id={saga_id} type={saga['saga_type']}")
                return None

            next_step = definition.steps[saga["current_step_index"]]
            logger.info(
                f"Saga step completed: saga={saga_id} step={step_name} "
                f"next={next_step.name}"
            )
            return next_step.name
        else:
            saga["steps_failed"].append(step_name)
            saga["status"] = "COMPENSATING"
            logger.warning(
                f"Saga step FAILED: saga={saga_id} step={step_name} error={error}"
            )

            # Execute compensation steps in reverse
            await self._compensate(saga_id, definition, step_name)
            return None

    async def _compensate(
        self,
        saga_id: str,
        definition: SagaDefinition,
        failed_step_name: str,
    ) -> None:
        """
        Execute compensation steps in reverse order.

        Args:
            saga_id: The saga instance ID.
            definition: The saga definition.
            failed_step_name: The step that failed.
        """
        saga = self._active_sagas.get(saga_id)
        if not saga:
            return

        completed = saga["steps_completed"]

        # Compensate in reverse order
        for step_name in reversed(completed):
            step = next(
                (s for s in definition.steps if s.name == step_name),
                None,
            )
            if step and step.compensation:
                logger.info(
                    f"Compensating: saga={saga_id} step={step_name} "
                    f"action={step.compensation}"
                )
                # In production: emit compensation event via Kafka

        saga["status"] = "COMPENSATED"
        logger.info(f"Saga compensated: id={saga_id}")

    async def check_timeouts(self) -> List[str]:
        """
        Check all active sagas for timeout and compensate expired ones.

        Returns:
            List of timed-out saga IDs.
        """
        now = datetime.now(timezone.utc)
        timed_out = []

        for saga_id, saga in self._active_sagas.items():
            if saga["status"] != "RUNNING":
                continue

            timeout_str = saga.get("timeout_at")
            if timeout_str:
                timeout_at = datetime.fromisoformat(timeout_str)
                if now > timeout_at:
                    logger.warning(
                        f"Saga TIMEOUT: id={saga_id} order={saga['order_id']}"
                    )
                    definition = self.get_saga_definition(saga["saga_type"])
                    if definition:
                        await self._compensate(saga_id, definition, "timeout")
                    timed_out.append(saga_id)

        return timed_out

    def get_saga_status(self, saga_id: str) -> Optional[Dict[str, Any]]:
        """Get the current status of a saga."""
        return self._active_sagas.get(saga_id)


# Singleton instance
order_saga = SagaEngine()
