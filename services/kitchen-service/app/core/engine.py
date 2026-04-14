"""
Kitchen OS V2 Engine — HotSot
==============================

Production-grade kitchen orchestration engine optimized for Indian cloud kitchens.
Manages station assignment, priority scoring, queue routing, batch formation,
overload detection, and staff behaviour enforcement.

Architecture
------------
- **Stations**: TANDOOR, DOSA, CURRY, FRYER, GRILL, COLD, RICE_BOWL
- **Queues** : IMMEDIATE_PREP_QUEUE (>80), NORMAL_QUEUE (60-80), BATCH_QUEUE (<60)
- **Scoring**: India-optimised weighted formula with dynamic weight adjustment
- **Batching**: Same-station grouping with ETA proximity and size cap

Usage
-----
>>> from app.core.engine import KitchenEngine, KitchenOrder, StationType
>>> engine = KitchenEngine()
>>> order = KitchenOrder(order_id="O-1001", items=[...], station=StationType.CURRY, tier=OrderTier.PRO)
>>> engine.enqueue(order, arrival_detected=True)
>>> next_order = engine.get_next_order()
"""

from __future__ import annotations

import math
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum, unique
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ARRIVAL_BOOST: int = 20
"""Extra points added to arrival_proximity when the customer is detected nearby."""

BATCH_ETA_TOLERANCE_SEC: int = 180  # 3 minutes
"""Maximum ETA difference between orders eligible for the same batch."""

BATCH_MAX_SIZE: int = 4
"""Maximum number of orders per batch."""

STAFF_ACK_TIMEOUT_SEC: int = 60
"""Seconds before an unacknowledged START_PREP is escalated."""

OVERLOAD_THRESHOLD: int = 15
"""Number of active orders at which the kitchen enters OVERLOAD."""

CRITICAL_THRESHOLD: int = 25
"""Number of active orders at which the kitchen enters CRITICAL."""

DYNAMIC_LOAD_THRESHOLD: float = 0.80
"""Kitchen load fraction above which weight adjustment kicks in."""

# Default weights (India-optimised)
WEIGHT_ARRIVAL_PROXIMITY: float = 0.40
WEIGHT_DELAY_RISK: float = 0.30
WEIGHT_AGE_FACTOR: float = 0.20
WEIGHT_TIER_WEIGHT: float = 0.10

# Adjusted weight when kitchen is under high load
WEIGHT_ARRIVAL_PROXIMITY_HIGH_LOAD: float = 0.50
WEIGHT_DELAY_RISK_HIGH_LOAD: float = 0.25
WEIGHT_AGE_FACTOR_HIGH_LOAD: float = 0.15
WEIGHT_TIER_WEIGHT_HIGH_LOAD: float = 0.10

# Queue-type thresholds
IMMEDIATE_THRESHOLD: float = 80.0
NORMAL_LOWER_THRESHOLD: float = 60.0

# Station capacity defaults
DEFAULT_STATION_CAPACITY: int = 8
DEFAULT_STATION_THROUGHPUT: float = 2.0  # orders/min


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

@unique
class StationType(str, Enum):
    """Identifiers for every kitchen station."""
    TANDOOR = "TANDOOR"
    DOSA = "DOSA"
    CURRY = "CURRY"
    FRYER = "FRYER"
    GRILL = "GRILL"
    COLD = "COLD"
    RICE_BOWL = "RICE_BOWL"


@unique
class QueueType(str, Enum):
    """Priority queue classification."""
    IMMEDIATE_PREP_QUEUE = "IMMEDIATE_PREP_QUEUE"
    NORMAL_QUEUE = "NORMAL_QUEUE"
    BATCH_QUEUE = "BATCH_QUEUE"


@unique
class OrderTier(int, Enum):
    """Customer subscription tier — higher value = higher priority."""
    FREE = 1
    PLUS = 2
    PRO = 3
    VIP = 4


@unique
class OrderState(str, Enum):
    """Lifecycle states of a kitchen order."""
    QUEUED = "QUEUED"
    BATCH_WAIT = "BATCH_WAIT"
    ASSIGNED = "ASSIGNED"
    PREP_STARTED = "PREP_STARTED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    COMPLETED = "COMPLETED"
    ESCALATED = "ESCALATED"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class KitchenPriorityScore:
    """Immutable snapshot of the priority computation for an order.

    Attributes
    ----------
    total : float
        Final composite priority score (0-100 scale conceptually).
    arrival_proximity : float
        Raw arrival proximity component (0-100).
    delay_risk : float
        Raw delay risk component (0-100).
    age_factor : float
        Raw age factor component (0-100).
    tier_weight : float
        Raw tier weight component (0-100).
    weights_used : Dict[str, float]
        The weight vector applied during computation (for audit).
    """

    total: float
    arrival_proximity: float
    delay_risk: float
    age_factor: float
    tier_weight: float
    weights_used: Dict[str, float]

    def to_dict(self) -> Dict[str, float]:
        """Serialise to a plain dictionary."""
        return {
            "total": self.total,
            "arrival_proximity": self.arrival_proximity,
            "delay_risk": self.delay_risk,
            "age_factor": self.age_factor,
            "tier_weight": self.tier_weight,
            "weights_used": self.weights_used,
        }


@dataclass
class KitchenStation:
    """Represents a physical station in the kitchen.

    Attributes
    ----------
    station_id : str
        Unique identifier (auto-generated if omitted).
    station_type : StationType
        The kind of station.
    current_orders : List[str]
        Order IDs currently being prepared at this station.
    throughput_per_min : float
        Average orders completed per minute.
    capacity : int
        Maximum concurrent orders this station can handle.
    completed_count : int
        Lifetime count of orders completed at this station (for throughput tracking).
    """

    station_id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    station_type: StationType = StationType.CURRY
    current_orders: List[str] = field(default_factory=list)
    throughput_per_min: float = DEFAULT_STATION_THROUGHPUT
    capacity: int = DEFAULT_STATION_CAPACITY
    completed_count: int = 0

    @property
    def load(self) -> float:
        """Current utilisation as a fraction of capacity (0.0 – 1.0+)."""
        if self.capacity <= 0:
            return 1.0
        return len(self.current_orders) / self.capacity

    @property
    def is_available(self) -> bool:
        """True if the station still has capacity for another order."""
        return len(self.current_orders) < self.capacity

    def assign_order(self, order_id: str) -> None:
        """Add an order to this station's active list."""
        if not self.is_available:
            raise ValueError(
                f"Station {self.station_id} ({self.station_type.value}) is at capacity "
                f"({len(self.current_orders)}/{self.capacity})"
            )
        self.current_orders.append(order_id)

    def complete_order(self, order_id: str) -> None:
        """Remove an order from the active list and increment throughput counter."""
        if order_id in self.current_orders:
            self.current_orders.remove(order_id)
            self.completed_count += 1

    def to_dict(self) -> Dict:
        """Serialise station state."""
        return {
            "station_id": self.station_id,
            "station_type": self.station_type.value,
            "current_orders": list(self.current_orders),
            "throughput_per_min": self.throughput_per_min,
            "capacity": self.capacity,
            "load": round(self.load, 3),
            "completed_count": self.completed_count,
            "is_available": self.is_available,
        }


@dataclass
class KitchenOrder:
    """Represents an order flowing through the kitchen.

    Attributes
    ----------
    order_id : str
        Unique order identifier.
    items : List[str]
        Item names or SKUs in the order.
    station : StationType
        Target station for this order.
    tier : OrderTier
        Customer subscription tier (affects priority).
    priority_score : Optional[KitchenPriorityScore]
        Computed priority (set by engine after enqueue).
    queue_type : Optional[QueueType]
        Queue the order was routed to.
    state : OrderState
        Current lifecycle state.
    batch_id : Optional[str]
        Batch identifier if the order is part of a batch.
    age_seconds : float
        Seconds since the order was enqueued.
    arrival_proximity : float
        Raw arrival proximity value (0-100).
    eta_seconds : float
        Estimated time to completion (used for batch ETA matching).
    enqueued_at : float
        Epoch timestamp when the order was enqueued.
    prep_started_at : Optional[float]
        Epoch timestamp when prep started.
    ack_at : Optional[float]
        Epoch timestamp when staff acknowledged the order.
    staff_id : Optional[str]
        ID of the staff member who acknowledged.
    """

    order_id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    items: List[str] = field(default_factory=list)
    station: StationType = StationType.CURRY
    tier: OrderTier = OrderTier.FREE
    priority_score: Optional[KitchenPriorityScore] = None
    queue_type: Optional[QueueType] = None
    state: OrderState = OrderState.QUEUED
    batch_id: Optional[str] = None
    age_seconds: float = 0.0
    arrival_proximity: float = 0.0
    eta_seconds: float = 600.0  # default 10 min
    enqueued_at: float = field(default_factory=time.time)
    prep_started_at: Optional[float] = None
    ack_at: Optional[float] = None
    staff_id: Optional[str] = None

    # ---- live helpers -------------------------------------------------------

    def refresh_age(self) -> float:
        """Recalculate and store the age of this order in seconds."""
        self.age_seconds = time.time() - self.enqueued_at
        return self.age_seconds

    def to_dict(self) -> Dict:
        """Serialise order state."""
        return {
            "order_id": self.order_id,
            "items": list(self.items),
            "station": self.station.value,
            "tier": self.tier.name,
            "priority_score": self.priority_score.to_dict() if self.priority_score else None,
            "queue_type": self.queue_type.value if self.queue_type else None,
            "state": self.state.value,
            "batch_id": self.batch_id,
            "age_seconds": round(self.age_seconds, 2),
            "arrival_proximity": round(self.arrival_proximity, 2),
            "eta_seconds": round(self.eta_seconds, 2),
        }


@dataclass
class Batch:
    """A group of orders prepared together at the same station.

    Attributes
    ----------
    batch_id : str
        Unique batch identifier.
    station : StationType
        The station at which all orders in this batch will be prepared.
    order_ids : List[str]
        Order identifiers belonging to this batch.
    created_at : float
        Epoch timestamp of batch formation.
    """

    batch_id: str = field(default_factory=lambda: f"B-{uuid.uuid4().hex[:8]}")
    station: StationType = StationType.CURRY
    order_ids: List[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict:
        """Serialise batch."""
        return {
            "batch_id": self.batch_id,
            "station": self.station.value,
            "order_ids": list(self.order_ids),
            "created_at": self.created_at,
            "size": len(self.order_ids),
        }


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class KitchenEngine:
    """Core kitchen orchestration engine.

    Manages stations, computes priorities, routes orders into the correct
    queue, forms batches, detects overloads, and enforces staff ACK
    timeouts.

    Parameters
    ----------
    stations : Optional[List[KitchenStation]]
        Initial station roster. If *None*, one station per
        :class:`StationType` is created with default capacity and throughput.
    """

    def __init__(self, stations: Optional[List[KitchenStation]] = None) -> None:
        # ---- stations -------------------------------------------------------
        if stations is not None:
            self._stations: Dict[StationType, List[KitchenStation]] = {}
            for s in stations:
                self._stations.setdefault(s.station_type, []).append(s)
        else:
            self._stations = {
                st: [KitchenStation(station_type=st)]
                for st in StationType
            }

        # ---- queues ---------------------------------------------------------
        self._immediate_queue: List[KitchenOrder] = []
        self._normal_queue: List[KitchenOrder] = []
        self._batch_queue: List[KitchenOrder] = []

        # ---- order registry -------------------------------------------------
        self._orders: Dict[str, KitchenOrder] = {}
        self._batches: Dict[str, Batch] = {}

        # ---- tracking -------------------------------------------------------
        self._total_enqueued: int = 0
        self._total_completed: int = 0
        self._created_at: float = time.time()

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    @property
    def active_order_count(self) -> int:
        """Number of orders currently in the system (not COMPLETED)."""
        return sum(
            1
            for o in self._orders.values()
            if o.state != OrderState.COMPLETED
        )

    @property
    def kitchen_load_fraction(self) -> float:
        """Overall kitchen utilisation as a fraction (0.0 – 1.0+).

        Computed as total active orders / total station capacity.
        """
        total_capacity = sum(
            s.capacity for station_list in self._stations.values() for s in station_list
        )
        if total_capacity == 0:
            return 1.0
        return self.active_order_count / total_capacity

    @property
    def kitchen_load_percent(self) -> float:
        """Kitchen utilisation as a percentage (0 – 100+)."""
        return self.kitchen_load_fraction * 100.0

    # ------------------------------------------------------------------
    # Station access
    # ------------------------------------------------------------------

    def get_stations(self, station_type: Optional[StationType] = None) -> List[KitchenStation]:
        """Return all stations, optionally filtered by type.

        Parameters
        ----------
        station_type : Optional[StationType]
            If provided, only stations of this type are returned.

        Returns
        -------
        List[KitchenStation]
        """
        if station_type is None:
            return [s for sl in self._stations.values() for s in sl]
        return list(self._stations.get(station_type, []))

    def get_available_station(self, station_type: StationType) -> Optional[KitchenStation]:
        """Find the first station of *station_type* with available capacity.

        Returns
        -------
        Optional[KitchenStation]
            A station with spare capacity, or *None* if all are full.
        """
        for station in self._stations.get(station_type, []):
            if station.is_available:
                return station
        return None

    # ------------------------------------------------------------------
    # Priority computation
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_arrival_proximity(
        base_proximity: float,
        arrival_detected: bool,
    ) -> float:
        """Compute the arrival proximity score.

        Parameters
        ----------
        base_proximity : float
            A raw proximity estimate (0-100), e.g. based on GPS/delivery ETA.
        arrival_detected : bool
            Whether the customer has been detected as arrived/nearby.

        Returns
        -------
        float
            Arrival proximity score (clamped 0-100).
        """
        score = base_proximity
        if arrival_detected:
            score += ARRIVAL_BOOST
        return min(max(score, 0.0), 100.0)

    @staticmethod
    def _compute_delay_risk(kitchen_load: float, hour_of_day: int) -> float:
        """Compute the delay risk score (0-100).

        Considers both kitchen load and the time of day (peak hours in India
        increase delay risk).

        Parameters
        ----------
        kitchen_load : float
            Current kitchen load fraction (0.0-1.0+).
        hour_of_day : int
            Current hour (0-23) in local (IST) time.

        Returns
        -------
        float
            Delay risk score (0-100).
        """
        # Load component: 0-60 points based on load
        load_score = min(kitchen_load * 60.0, 60.0)

        # Peak-hour component: Indian peak lunch (12-14), dinner (19-22)
        peak_hours = {12, 13, 14, 19, 20, 21, 22}
        secondary_peak = {11, 15, 18}
        if hour_of_day in peak_hours:
            hour_score = 40.0
        elif hour_of_day in secondary_peak:
            hour_score = 25.0
        else:
            hour_score = 10.0

        return min(load_score + hour_score, 100.0)

    @staticmethod
    def _compute_age_factor(age_seconds: float) -> float:
        """Compute the age factor score (0-100).

        Orders that have been waiting longer get a higher age factor.

        Parameters
        ----------
        age_seconds : float
            Seconds since the order was enqueued.

        Returns
        -------
        float
            Age factor score (0-100).
        """
        # Linear ramp: 100 points at ~10 min wait, capped at 100
        factor = (age_seconds / 600.0) * 100.0
        return min(max(factor, 0.0), 100.0)

    @staticmethod
    def _compute_tier_weight(tier: OrderTier) -> float:
        """Compute the tier weight score (0-100).

        Parameters
        ----------
        tier : OrderTier
            Customer subscription tier.

        Returns
        -------
        float
            Tier weight score scaled to 0-100.
        """
        # Scale 1-4 → 25-100
        return float(tier.value) * 25.0

    def compute_priority(
        self,
        order: KitchenOrder,
        kitchen_load: Optional[float] = None,
        arrival_detected: bool = False,
    ) -> KitchenPriorityScore:
        """Compute the composite priority score for *order*.

        Priority = 0.40 * arrival_proximity
                 + 0.30 * delay_risk
                 + 0.20 * age_factor
                 + 0.10 * tier_weight

        When ``kitchen_load > 80 %``, weights shift to:
        ``0.50 / 0.25 / 0.15 / 0.10`` to further prioritise arriving
        customers.

        Parameters
        ----------
        order : KitchenOrder
            The order to score.
        kitchen_load : Optional[float]
            Kitchen load fraction. If *None*, the engine's current load is used.
        arrival_detected : bool
            Whether the customer has been detected nearby.

        Returns
        -------
        KitchenPriorityScore
            Composite score and component breakdown.
        """
        if kitchen_load is None:
            kitchen_load = self.kitchen_load_fraction

        # Select weight vector based on load
        if kitchen_load > DYNAMIC_LOAD_THRESHOLD:
            w_ap = WEIGHT_ARRIVAL_PROXIMITY_HIGH_LOAD
            w_dr = WEIGHT_DELAY_RISK_HIGH_LOAD
            w_af = WEIGHT_AGE_FACTOR_HIGH_LOAD
            w_tw = WEIGHT_TIER_WEIGHT_HIGH_LOAD
        else:
            w_ap = WEIGHT_ARRIVAL_PROXIMITY
            w_dr = WEIGHT_DELAY_RISK
            w_af = WEIGHT_AGE_FACTOR
            w_tw = WEIGHT_TIER_WEIGHT

        # Compute components
        ap = self._compute_arrival_proximity(order.arrival_proximity, arrival_detected)
        dr = self._compute_delay_risk(kitchen_load, time.localtime().tm_hour)
        af = self._compute_age_factor(order.age_seconds)
        tw = self._compute_tier_weight(order.tier)

        total = (w_ap * ap) + (w_dr * dr) + (w_af * af) + (w_tw * tw)
        total = min(max(total, 0.0), 100.0)

        return KitchenPriorityScore(
            total=total,
            arrival_proximity=ap,
            delay_risk=dr,
            age_factor=af,
            tier_weight=tw,
            weights_used={
                "arrival_proximity": w_ap,
                "delay_risk": w_dr,
                "age_factor": w_af,
                "tier_weight": w_tw,
            },
        )

    # ------------------------------------------------------------------
    # Queue assignment
    # ------------------------------------------------------------------

    def assign_queue(self, order: KitchenOrder) -> QueueType:
        """Determine which priority queue *order* should be placed in.

        - Priority > 80  → :attr:`QueueType.IMMEDIATE_PREP_QUEUE`
        - Priority 60-80 → :attr:`QueueType.NORMAL_QUEUE`
        - Priority < 60  → :attr:`QueueType.BATCH_QUEUE`

        Parameters
        ----------
        order : KitchenOrder
            Order with a computed ``priority_score``.

        Returns
        -------
        QueueType
            Target queue type.

        Raises
        ------
        ValueError
            If *order* has not yet been scored.
        """
        if order.priority_score is None:
            raise ValueError(
                f"Order {order.order_id} must be scored before queue assignment"
            )
        score = order.priority_score.total
        if score > IMMEDIATE_THRESHOLD:
            return QueueType.IMMEDIATE_PREP_QUEUE
        elif score >= NORMAL_LOWER_THRESHOLD:
            return QueueType.NORMAL_QUEUE
        else:
            return QueueType.BATCH_QUEUE

    # ------------------------------------------------------------------
    # Enqueue
    # ------------------------------------------------------------------

    def enqueue(
        self,
        order: KitchenOrder,
        arrival_detected: bool = False,
    ) -> KitchenOrder:
        """Score, assign queue, and insert *order* into the engine.

        Parameters
        ----------
        order : KitchenOrder
            The incoming order.
        arrival_detected : bool
            Whether the customer is already nearby.

        Returns
        -------
        KitchenOrder
            The same order, now scored and routed.
        """
        order.refresh_age()
        order.priority_score = self.compute_priority(
            order, arrival_detected=arrival_detected
        )
        order.queue_type = self.assign_queue(order)

        # Insert into the right queue (sorted descending by priority)
        queue = self._get_queue(order.queue_type)
        queue.append(order)
        queue.sort(key=lambda o: o.priority_score.total if o.priority_score else 0, reverse=True)

        # If it goes to the batch queue, set state to BATCH_WAIT
        if order.queue_type == QueueType.BATCH_QUEUE:
            order.state = OrderState.BATCH_WAIT
        else:
            order.state = OrderState.QUEUED

        self._orders[order.order_id] = order
        self._total_enqueued += 1
        return order

    # ------------------------------------------------------------------
    # Internal queue accessor
    # ------------------------------------------------------------------

    def _get_queue(self, queue_type: QueueType) -> List[KitchenOrder]:
        """Return the internal list for *queue_type*."""
        if queue_type == QueueType.IMMEDIATE_PREP_QUEUE:
            return self._immediate_queue
        elif queue_type == QueueType.NORMAL_QUEUE:
            return self._normal_queue
        else:
            return self._batch_queue

    # ------------------------------------------------------------------
    # Batch formation
    # ------------------------------------------------------------------

    def form_batches(self, queue_type: QueueType = QueueType.BATCH_QUEUE) -> List[Batch]:
        """Form batches from eligible orders in the given queue.

        Batching rules:
        1. Orders must target the **same** station type.
        2. ETA difference between any two orders ≤ 3 minutes (180 s).
        3. Maximum batch size is 4 orders.
        4. Orders must still be in ``BATCH_WAIT`` state.

        Parameters
        ----------
        queue_type : QueueType
            Queue to scan (default: BATCH_QUEUE).

        Returns
        -------
        List[Batch]
            Newly formed batches.
        """
        queue = self._get_queue(queue_type)
        eligible = [o for o in queue if o.state == OrderState.BATCH_WAIT]
        # Group by station type
        by_station: Dict[StationType, List[KitchenOrder]] = {}
        for order in eligible:
            by_station.setdefault(order.station, []).append(order)

        new_batches: List[Batch] = []
        for station_type, orders in by_station.items():
            # Sort by eta_seconds for proximity grouping
            orders.sort(key=lambda o: o.eta_seconds)
            used: set = set()

            for i, anchor in enumerate(orders):
                if anchor.order_id in used:
                    continue
                batch_order_ids: List[str] = [anchor.order_id]
                used.add(anchor.order_id)

                for j in range(i + 1, len(orders)):
                    candidate = orders[j]
                    if candidate.order_id in used:
                        continue
                    if len(batch_order_ids) >= BATCH_MAX_SIZE:
                        break
                    eta_diff = abs(candidate.eta_seconds - anchor.eta_seconds)
                    if eta_diff <= BATCH_ETA_TOLERANCE_SEC:
                        batch_order_ids.append(candidate.order_id)
                        used.add(candidate.order_id)

                # Only form a batch if there are ≥2 orders
                if len(batch_order_ids) >= 2:
                    batch = Batch(station=station_type, order_ids=batch_order_ids)
                    self._batches[batch.batch_id] = batch

                    # Update order metadata
                    for oid in batch_order_ids:
                        o = self._orders[oid]
                        o.batch_id = batch.batch_id
                        o.state = OrderState.QUEUED  # ready to be picked up

                    new_batches.append(batch)

        return new_batches

    # ------------------------------------------------------------------
    # Order retrieval
    # ------------------------------------------------------------------

    def get_next_order(self) -> Optional[KitchenOrder]:
        """Pop the highest-priority order across all queues.

        Search order: IMMEDIATE → NORMAL → BATCH.

        Returns
        -------
        Optional[KitchenOrder]
            The next order to prepare, or *None* if all queues are empty.
        """
        # Refresh ages and re-score across all queues
        self._refresh_all_priorities()

        for qt in (
            QueueType.IMMEDIATE_PREP_QUEUE,
            QueueType.NORMAL_QUEUE,
            QueueType.BATCH_QUEUE,
        ):
            queue = self._get_queue(qt)
            while queue:
                order = queue.pop(0)
                if order.state == OrderState.COMPLETED:
                    continue
                # Try to assign to a station
                station = self.get_available_station(order.station)
                if station is not None:
                    station.assign_order(order.order_id)
                    order.state = OrderState.ASSIGNED
                    order.prep_started_at = time.time()
                    return order
                else:
                    # No station available — put it back and stop trying this queue
                    queue.insert(0, order)
                    break  # station full; no point checking further in this queue

        return None

    # ------------------------------------------------------------------
    # Kitchen status
    # ------------------------------------------------------------------

    def get_kitchen_status(self) -> Dict:
        """Return a comprehensive snapshot of the kitchen state.

        Returns
        -------
        dict
            Keys: ``stations``, ``queues``, ``overload``, ``active_orders``,
            ``total_enqueued``, ``total_completed``, ``kitchen_load_pct``,
            ``throughput_by_station``, ``batches``.
        """
        throughput: Dict[str, Dict] = {}
        for st, station_list in self._stations.items():
            total_completed = sum(s.completed_count for s in station_list)
            total_capacity = sum(s.capacity for s in station_list)
            total_current = sum(len(s.current_orders) for s in station_list)
            throughput[st.value] = {
                "completed": total_completed,
                "capacity": total_capacity,
                "current": total_current,
                "utilization_pct": round(
                    (total_current / total_capacity * 100) if total_capacity else 0, 1
                ),
            }

        return {
            "stations": {
                st.value: [s.to_dict() for s in sl]
                for st, sl in self._stations.items()
            },
            "queues": {
                QueueType.IMMEDIATE_PREP_QUEUE.value: len(self._immediate_queue),
                QueueType.NORMAL_QUEUE.value: len(self._normal_queue),
                QueueType.BATCH_QUEUE.value: len(self._batch_queue),
            },
            "overload": self.check_overload(),
            "active_orders": self.active_order_count,
            "total_enqueued": self._total_enqueued,
            "total_completed": self._total_completed,
            "kitchen_load_pct": round(self.kitchen_load_percent, 2),
            "throughput_by_station": throughput,
            "batches": {
                bid: b.to_dict() for bid, b in self._batches.items()
            },
        }

    # ------------------------------------------------------------------
    # Overload detection
    # ------------------------------------------------------------------

    def check_overload(self) -> Optional[str]:
        """Check whether the kitchen is in an overload state.

        - Active orders > 25 → ``"CRITICAL"``
        - Active orders > 15 → ``"OVERLOAD"``
        - Otherwise → ``None``

        Returns
        -------
        Optional[str]
            ``"CRITICAL"``, ``"OVERLOAD"``, or ``None``.
        """
        count = self.active_order_count
        if count > CRITICAL_THRESHOLD:
            return "CRITICAL"
        if count > OVERLOAD_THRESHOLD:
            return "OVERLOAD"
        return None

    # ------------------------------------------------------------------
    # Staff ACK enforcement
    # ------------------------------------------------------------------

    def record_staff_ack(self, order_id: str, staff_id: str) -> bool:
        """Record a staff member's acknowledgment of a prep start.

        Parameters
        ----------
        order_id : str
            The order being acknowledged.
        staff_id : str
            Identifier of the acknowledging staff member.

        Returns
        -------
        bool
            ``True`` if the ACK was accepted, ``False`` if the order was not
            found or not in a valid state for acknowledgment.
        """
        order = self._orders.get(order_id)
        if order is None:
            return False
        if order.state not in (OrderState.PREP_STARTED, OrderState.ASSIGNED, OrderState.ESCALATED):
            return False
        order.ack_at = time.time()
        order.staff_id = staff_id
        order.state = OrderState.ACKNOWLEDGED
        return True

    def check_staff_ack_timeout(self) -> List[str]:
        """Return order IDs that have exceeded the ACK timeout and need escalation.

        An order is flagged when it has been in ``PREP_STARTED`` or ``ASSIGNED``
        state for more than :data:`STAFF_ACK_TIMEOUT_SEC` seconds without
        acknowledgment.  Flagged orders are moved to ``ESCALATED`` state.

        Returns
        -------
        List[str]
            Order IDs that were just escalated.
        """
        now = time.time()
        escalated: List[str] = []

        for order in self._orders.values():
            if order.state in (OrderState.PREP_STARTED, OrderState.ASSIGNED):
                start_time = order.prep_started_at or order.enqueued_at
                if (now - start_time) > STAFF_ACK_TIMEOUT_SEC:
                    order.state = OrderState.ESCALATED
                    escalated.append(order.order_id)

        return escalated

    # ------------------------------------------------------------------
    # Order completion
    # ------------------------------------------------------------------

    def complete_order(self, order_id: str) -> bool:
        """Mark an order as completed and free station capacity.

        Parameters
        ----------
        order_id : str
            The order to complete.

        Returns
        -------
        bool
            ``True`` if successfully completed, ``False`` if not found.
        """
        order = self._orders.get(order_id)
        if order is None:
            return False

        # Remove from any queue it might still be in
        for qt in QueueType:
            queue = self._get_queue(qt)
            self._remove_from_queue(queue, order_id)

        # Remove from station
        for station_list in self._stations.values():
            for station in station_list:
                if order_id in station.current_orders:
                    station.complete_order(order_id)

        order.state = OrderState.COMPLETED
        self._total_completed += 1
        return True

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _remove_from_queue(queue: List[KitchenOrder], order_id: str) -> None:
        """Remove an order from a queue list by order_id (in-place)."""
        queue[:] = [o for o in queue if o.order_id != order_id]

    def _refresh_all_priorities(self) -> None:
        """Refresh age and re-score all queued orders, then re-sort queues."""
        now = time.time()
        for order in self._orders.values():
            if order.state in (OrderState.QUEUED, OrderState.BATCH_WAIT):
                order.refresh_age()
                # Preserve arrival_detected context: if arrival_proximity was
                # boosted (>= 80 raw), treat as detected for re-score.
                was_detected = order.arrival_proximity >= 80.0
                order.priority_score = self.compute_priority(
                    order, arrival_detected=was_detected
                )
                order.queue_type = self.assign_queue(order)

        # Re-sort each queue by priority descending
        for qt in QueueType:
            queue = self._get_queue(qt)
            queue.sort(
                key=lambda o: o.priority_score.total if o.priority_score else 0,
                reverse=True,
            )

    # ------------------------------------------------------------------
    # Dunder helpers
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return (
            f"<KitchenEngine active={self.active_order_count} "
            f"load={self.kitchen_load_percent:.1f}% "
            f"overload={self.check_overload()}>"
        )
