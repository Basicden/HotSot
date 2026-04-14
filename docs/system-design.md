# HotSot — System Design Document V2

> **Version**: 2.0
> **Last Updated**: 2026-03-04
> **Status**: Active — Production Blueprint

---

## 1. Overview

HotSot is a **pickup-first real-time execution system** engineered for the demands of Indian cloud-kitchen operations. At its core lies a **16-state order lifecycle** that provides granular visibility and control over every order from creation to handoff — far beyond the coarse 7-state model of V1. The system orchestrates kitchen-aware scheduling that accounts for station availability, prep-time variance, and dynamic priority reordering. Shelf locking via Redis SETNX ensures that physical items on shelves are never double-picked or lost in the handoff chaos of a busy pickup counter.

Arrival detection is a first-class subsystem in V2: it combines GPS haversine distance validation with QR token rotation to deterministically confirm when a customer has reached the kitchen, triggering priority boosts and handoff workflows automatically. The **compensation engine** handles the full spectrum of failure modes — shelf expiry, kitchen failure, payment conflicts, delivery delays, wrong orders, and customer complaints — with idempotent UPI refund processing and kitchen penalty tracking to close the feedback loop.

ML-based ETA intelligence, powered by a LightGBM inference pipeline with India-specific multipliers (festival, monsoon, peak-hour), delivers ±2-minute accuracy guarantees. Every state transition emits a Kafka event, every order decision is guard-ruled, and every financial action is idempotent. HotSot is not just an order tracker — it is a real-time execution engine that treats the kitchen as a controlled, measurable, optimizable system.

---

## 2. Architecture Principles

The architecture of HotSot V2 is governed by six foundational principles that ensure correctness under load, operational simplicity, and evolutionary flexibility.

1. **Event-Driven**: Every state change in the 16-state machine emits a Kafka event *and* writes to Postgres in a single transactional boundary. Downstream services consume events for side effects — notifications, ETA recalculation, shelf assignment — without coupling to the Order Service. This enables zero-downtime schema evolution: new consumers can replay the event log without modifying the producer.

2. **Strong Consistency for Orders**: PostgreSQL is the authoritative source of truth for order state. A state transition is only valid if the Postgres UPDATE succeeds and the Kafka produce is acknowledged. If either fails, the entire operation rolls back. This prevents split-brain scenarios where Redis says "ON_SHELF" but Postgres says "IN_PREP." Orders are financial instruments — they demand ACID guarantees.

3. **Eventually Consistent for ETA**: ML predictions are inherently probabilistic and time-lagged. The ETA Service computes a new prediction asynchronously upon receiving a `prep.started` or `queue.assigned` event. The client may briefly see a stale ETA (at most 120s, the Redis TTL) before the updated value propagates. This trade-off is intentional: we never block an order transition on an ETA recalculation.

4. **Redis for Hot State**: Shelf locks (`SETNX` with 300s TTL), kitchen load counters, arrival dedup windows, and ETA caches all live in Redis. Redis is *not* the source of truth — it is a low-latency read-optimized projection. If Redis crashes, the system rebuilds from Postgres + Kafka. Every Redis key is namespaced by `kitchen_id` to support multi-tenant isolation and shard-level eviction.

5. **Sharded by Kitchen**: `kitchen_id` is the universal partition key. Kafka topics are partitioned by `kitchen_id`, Postgres tables are range-partitioned by `kitchen_id`, and Redis clusters map `kitchen_id` to specific nodes. This ensures that a single kitchen's operations never contend with another's — a hot kitchen in Koramangala does not slow down a quiet kitchen in Whitefield.

6. **Idempotency Everywhere**: Every mutation endpoint accepts an `idempotency_key`. The Order Service and Compensation Service both check Redis for duplicate keys before processing. This is critical for India's unreliable network conditions: a customer on a flaky 4G connection may retry a payment three times, but the system must process it exactly once.

---

## 3. V2 Order State Machine

The V2 state machine expands from 7 states to 16, providing the granularity needed for kitchen-internal orchestration, shelf management, arrival detection, and compensation flows.

### State Diagram

```
CREATED ──→ PAYMENT_PENDING ──→ PAYMENT_CONFIRMED ──→ SLOT_RESERVED ──→
QUEUE_ASSIGNED ──→ IN_PREP ──→ PACKING ──→ READY ──→ ON_SHELF ──→
ARRIVED ──→ HANDOFF_IN_PROGRESS ──→ PICKED

Terminal States:
  EXPIRED ──→ REFUNDED
  CANCELLED
  FAILED
```

### State Descriptions

| State | Description | Responsible Service |
|-------|-------------|-------------------|
| `CREATED` | Order object instantiated, items validated, no payment attempted | Order Service |
| `PAYMENT_PENDING` | UPI payment initiated, waiting for gateway callback | Order Service |
| `PAYMENT_CONFIRMED` | Payment verified via UPI callback or polling, order is financially committed | Order Service |
| `SLOT_RESERVED` | Kitchen has confirmed it can fulfill the order within the requested time window | Kitchen Service |
| `QUEUE_ASSIGNED` | Priority score computed; order placed in one of 3 priority queues (URGENT/NORMAL/BATCH) | Kitchen Service |
| `IN_PREP` | Active cooking has started at a specific station | Kitchen Service |
| `PACKING` | Cooking complete; item is being packaged, labeled, and sealed | Kitchen Service |
| `READY` | Packing complete; item is physically ready but not yet placed on shelf | Kitchen Service |
| `ON_SHELF` | Item placed on a specific shelf (HOT/COLD/AMBIENT zone) with TTL enforcement | Shelf Service |
| `ARRIVED` | Customer arrival confirmed via GPS ≤150m or valid QR scan | Arrival Service |
| `HANDOFF_IN_PROGRESS` | Staff has picked the item from shelf; physical handoff to customer is underway | Order Service |
| `PICKED` | Customer has confirmed receipt; order lifecycle complete | Order Service |
| `EXPIRED` | Shelf TTL exceeded and order was not PICKED; transitions to REFUNDED | Shelf Service → Compensation Service |
| `REFUNDED` | Compensation processed; UPI refund initiated or credit issued | Compensation Service |
| `CANCELLED` | Customer or system cancelled before IN_PREP | Order Service |
| `FAILED` | Unrecoverable error (payment conflict, kitchen system failure) | Order Service → Compensation Service |

### Guard Rules

State transitions are not free-form. Each transition has a **guard rule** — a boolean condition that must be true before the transition is permitted. If the guard fails, the transition is rejected with a `GuardViolationError` and the order remains in its current state.

| Transition | Guard Rule | Evaluation |
|-----------|-----------|------------|
| `→ SLOT_RESERVED` | `slot_available == true` | Kitchen Service checks that the requested time slot has remaining capacity for the given station type. Capacity is a function of station count × prep slots per station, minus current bookings. |
| `→ QUEUE_ASSIGNED` | `priority_score IS NOT NULL` | Priority score is computed from: `(wait_minutes × 2) + (vip_multiplier × 10) + (retry_penalty × 5) - (kitchen_load_factor × 3)`. The score determines queue placement: score ≥ 70 → URGENT, 30–69 → NORMAL, < 30 → BATCH. |
| `→ ON_SHELF` | `shelf_id IS NOT NULL AND zone_capacity < max_capacity` | Shelf Service must identify an available shelf slot in the correct zone (HOT for hot food, COLD for beverages, AMBIENT for dry items) and acquire a Redis SETNX lock on `shelf:{kitchen_id}:{shelf_id}:{slot}` before the transition is permitted. |
| `→ ARRIVED` | `gps_distance_meters ≤ 150 OR qr_token_valid == true` | Arrival Service validates either: (a) haversine distance between customer GPS and kitchen GPS ≤ 150m with accuracy ≤ 50m, or (b) QR token matches the currently valid rotation (30s window). Both conditions are debounced within a 60s dedup window. |
| `→ EXPIRED` | `current_time > shelf_placed_at + TTL AND state != PICKED` | Shelf TTL is zone-dependent: HOT = 15min, COLD = 30min, AMBIENT = 60min. The Shelf Service runs a periodic sweeper (every 30s) that evaluates all ON_SHELF orders against their TTL. Expired orders are transitioned to EXPIRED, which automatically triggers REFUNDED via the Compensation Service. |

### Transition Constraints

- **Forward-only**: No state may transition backwards. `CANCELLED` and `FAILED` are terminal — once entered, no further transitions are possible.
- **Cancellation window**: `CANCELLED` is only valid from states before `IN_PREP`. Once cooking starts, cancellation requires escalation to the Compensation Service.
- **Terminal isolation**: `EXPIRED → REFUNDED` is the only valid transition out of `EXPIRED`. `REFUNDED`, `CANCELLED`, and `FAILED` accept no further transitions.
- **Atomicity**: Each transition is a single Postgres transaction that: (1) validates the guard, (2) updates `order_state`, (3) inserts into `order_events`, (4) produces to Kafka. If any step fails, the entire transaction rolls back.

---

## 4. Service Architecture

HotSot V2 is decomposed into 10 microservices, each with a single bounded context, its own data store where applicable, and explicit API contracts. Services communicate synchronously via gRPC for critical paths (order state transitions) and asynchronously via Kafka for eventual-consistency paths (notifications, ETA updates, ML feedback).

### 4.1 API Gateway (Port 8000)

The API Gateway is the sole entry point for all external traffic. It handles TLS termination, JWT validation, rate limiting (token bucket: 100 req/s per API key), CORS policy enforcement, and request routing to downstream services. It also performs request-level observability injection — every inbound request receives a `trace_id` that propagates through the entire call chain via OpenTelemetry headers. The gateway maintains a route table that maps URL prefixes to service endpoints:

| Route Prefix | Target Service | Protocol |
|-------------|---------------|----------|
| `/api/v2/orders/*` | Order Service (8001) | HTTP |
| `/api/v2/kitchens/*` | Kitchen Service (8002) | HTTP |
| `/api/v2/shelves/*` | Shelf Service (8003) | HTTP |
| `/api/v2/eta/*` | ETA Service (8004) | HTTP |
| `/ws/v2/track/*` | Realtime Service (8006) | WebSocket |

Rate limiting is enforced per-route: order creation is capped at 50 req/s per kitchen (to prevent flash-flood abuse), while read endpoints (ETA, tracking) are capped at 200 req/s per client. The gateway also implements circuit breakers per downstream service — if a service returns 5xx at >30% rate over a 60s window, the circuit opens and the gateway returns `503 Service Unavailable` with a `Retry-After` header.

### 4.2 Order Service (Port 8001)

The Order Service is the brain of the 16-state machine. It owns the `orders` and `order_events` tables in Postgres and is the only service permitted to execute state transitions. Every transition goes through a three-step pipeline: (1) guard evaluation, (2) transactional state update + event insert, (3) Kafka produce. This pipeline is wrapped in an outbox pattern — the Kafka produce is not a separate network call but a logical decode from the Postgres WAL via Debezium, guaranteeing at-least-once delivery even if the Order Service crashes mid-transaction.

Event sourcing is a first-class feature. The `order_events` table records every state transition with: `event_id`, `order_id`, `from_state`, `to_state`, `guard_result`, `triggered_by`, `timestamp`, and `metadata` (JSONB). This enables full audit trail reconstruction — given an `order_id`, the Order Service can replay every decision point. The event log also feeds the ML Service's feedback loop for ETA model retraining.

Idempotency is enforced via a Redis-backed key store. Every mutation request must include an `Idempotency-Key` header. The Order Service checks `idempotency:{key}` in Redis before processing. If the key exists, the service returns the cached response. If not, it processes the request and stores the response with a 24-hour TTL. This handles retry storms from mobile clients on unstable Indian networks.

### 4.3 Kitchen Service (Port 8002)

The Kitchen Service manages the physical reality of food preparation. It models each kitchen as having up to **7 stations** (GRILL, FRY, SAUTE, PASTA, BEVERAGE, DESSERT, PACKING), each with its own concurrency limit and average prep time. When an order enters `QUEUE_ASSIGNED`, the Kitchen Service computes a **priority score** using the formula:

```
priority_score = (wait_minutes × 2) + (vip_multiplier × 10) + (retry_penalty × 5) - (kitchen_load_factor × 3)
```

Orders are then placed into one of **3 priority queues**: URGENT (score ≥ 70), NORMAL (30–69), or BATCH (< 30). The Kitchen Service dequeues from URGENT first, then NORMAL, then BATCH. Within each queue, FIFO ordering is maintained. This prevents a large batch order from starving a single urgent VIP order.

**Dynamic weights** adjust station priorities in real-time. If the GRILL station has 8 orders queued but the PASTA station is idle, the Kitchen Service temporarily increases the `kitchen_load_factor` for GRILL, which reduces priority scores for new GRILL orders and makes it more likely that non-GRILL orders get assigned first. This load-balancing prevents station-level bottlenecks.

**Batching** is applied to BATCH-queue orders. If two or more BATCH orders share the same station and items, the Kitchen Service merges them into a single prep run, reducing station time by up to 35%. The batch is split back into individual orders at the PACKING stage.

### 4.4 Shelf Service (Port 8003)

The Shelf Service manages the physical staging area where prepared orders await pickup. Each kitchen has multiple shelves organized into **3 temperature zones**: HOT (kept at 60°C, TTL 15 min — for gravies, rotis, fried items), COLD (kept at 4°C, TTL 30 min — for beverages, desserts), and AMBIENT (room temperature, TTL 60 min — for packed dry items, breads).

Shelf slots are protected by **Redis SETNX locks** with the key pattern `shelf:{kitchen_id}:{zone}:{slot_id}`. When an order transitions to `ON_SHELF`, the Shelf Service: (1) identifies the correct zone based on item metadata, (2) scans for an unlocked slot in that zone, (3) acquires the SETNX lock (300s TTL, auto-renewed every 60s via a background worker), (4) confirms the transition with the Order Service. If no slot is available, the transition is rejected and the order remains in `READY` — the Kitchen Service is notified to pause further READY transitions until a shelf slot frees up.

**TTL enforcement** is handled by a periodic sweeper that runs every 30 seconds. The sweeper evaluates all `ON_SHELF` orders against their zone-specific TTL. Expired orders are transitioned to `EXPIRED`, which triggers the Compensation Service for refund processing. The sweeper also emits `shelf.expired` events to Kafka, which the ML Service consumes to adjust future ETA predictions for items that frequently expire (indicating systematic overestimation of pickup timing).

### 4.5 ETA Service (Port 8004)

The ETA Service provides real-time estimated time-to-pickup for every active order. It operates a **two-tier prediction engine**: a primary LightGBM model for orders with sufficient feature history, and a rule-based fallback for cold-start scenarios (new kitchens, new items, insufficient data).

The **LightGBM inference pipeline** loads a pre-trained model artifact from S3 (versioned by model ID). Feature vectors are assembled from: item prep times (historical mean ± std), station load factor, kitchen current queue depth, time of day, day of week, festival flag, monsoon flag, and order priority score. Inference latency is targeted at 30–50ms per prediction, achieved by keeping the model in memory and pre-computing static features (kitchen profiles, item metadata) at service startup.

**India multipliers** are applied as post-inference adjustments:

| Factor | Multiplier | Condition |
|--------|-----------|-----------|
| Festival | 1.8x | `is_festival == true` (Holi, Diwali, Eid, Christmas, Pongal) |
| Monsoon | 1.3x | `month IN (6,7,8,9) AND city_monsoon_affected == true` |
| Peak hours | 1.5x | `hour IN (12,13,20,21) AND is_weekday == true` |

These multipliers are cumulative: a Diwali peak-hour order in Mumbai during monsoon would receive a `1.8 × 1.5 × 1.3 = 3.51x` multiplier. While this seems extreme, field data shows that these conditions genuinely produce 3–4x prep time increases due to order volume surges and staff constraints.

The **rule-based fallback** activates when: (a) the kitchen has fewer than 50 historical orders, (b) the item is new and has no prep time history, or (c) the LightGBM model returns a confidence score below 0.6. The fallback uses a simple formula: `eta = base_prep_time × kitchen_load_factor × india_multiplier + buffer_minutes(5)`. It is intentionally conservative — it is better to overestimate and delight than underestimate and frustrate.

Predicted ETAs are cached in Redis with a 120-second TTL. Clients receive updates via the Realtime Service (WebSocket) whenever a new prediction is computed.

### 4.6 Notification Service (Port 8005)

The Notification Service orchestrates **multi-channel dispatch** across four delivery mechanisms: WebSocket (instant, for connected clients), Push Notification (FCM/APNs, for backgrounded mobile apps), SMS (Twilio/Gupshup, for Tier-2/3 users with low bandwidth), and In-App (polling endpoint for clients that cannot maintain WebSocket connections).

Each notification is defined by a **template ID** and a **channel priority list**. For example, the `order.ready` notification uses the priority: WebSocket → Push → In-App, skipping SMS (since the customer likely needs to physically move to the kitchen). The `payment.confirmed` notification uses: WebSocket → Push → SMS (since payment confirmation is critical and must reach the customer regardless of app state).

The service implements **delivery guarantees**: at-least-once for WebSocket and Push (with 3 retries, exponential backoff), at-least-once for SMS (with 2 retries), and best-effort for In-App. Delivery status is tracked per-channel: `PENDING → SENT → DELIVERED → READ` (where READ is only available for In-App). Failed notifications after all retries are logged to a dead-letter queue for manual inspection.

Rate limiting per customer prevents notification spam: max 10 notifications per order, max 3 per 5-minute window per channel. This prevents a rapid state-machine progression from flooding a customer's phone with 8 push notifications in 30 seconds.

### 4.7 Realtime Service (Port 8006)

The Realtime Service is a **WebSocket layer** that provides live order tracking to connected clients. Each client opens a WebSocket connection to `/ws/v2/track/{order_id}` after authentication. The service subscribes to Kafka topics filtered by `order_id` and pushes state change events, ETA updates, and kitchen status updates to the client in real-time.

Connection management is critical at scale. The service maintains a **connection registry** in Redis: `ws:connections:{order_id}` → `{session_id, connected_at, last_ping}`. Heartbeat pings are sent every 30 seconds; if a client misses 3 consecutive pings, the connection is terminated and the client must reconnect. On reconnection, the service replays all missed events from the last acknowledged event ID.

For horizontal scaling, the Realtime Service uses a **sticky-session load balancer** (based on `order_id` hash) to ensure that all WebSocket frames for a given order route to the same service instance. If an instance crashes, the load balancer redistributes connections, and the new instance rebuilds its subscription state from Kafka consumer offsets.

The service also supports **kitchen dashboard mode** — a WebSocket connection authenticated with a kitchen staff token that receives a live feed of all orders for that kitchen (queue positions, station assignments, shelf occupancy). This enables kitchen staff to see their workload in real-time without refreshing a page.

### 4.8 ML Service (Port 8007)

The ML Service manages the **training pipeline, feature store, and feedback loop** for ETA prediction. It is the only service that writes to the model artifact store (S3) and the feature store (Redis + Postgres).

The **training pipeline** runs as a daily batch job (02:00 IST) that: (1) extracts the last 30 days of completed orders from Postgres, (2) computes feature vectors (prep time, station load, time-of-day, kitchen profile, item metadata, India multipliers), (3) trains a new LightGBM model with 5-fold cross-validation, (4) evaluates the model against the previous production model on a held-out test set, (5) promotes the new model only if MAE improves by ≥2% — otherwise, the previous model is retained.

The **feature store** provides low-latency feature retrieval for the ETA Service's inference pipeline. Features are pre-computed and stored in Redis with a 1-hour TTL: `features:kitchen:{kitchen_id}` (station loads, queue depths, historical prep times), `features:item:{item_id}` (base prep time, station affinity, variance). The ML Service refreshes these features every 15 minutes by querying the Kitchen Service and Order Service.

The **feedback loop** consumes `order.picked` and `shelf.expired` events from Kafka. For `order.picked`, it records the **actual prep time** (from `IN_PREP` timestamp to `PICKED` timestamp) and compares it against the predicted ETA. The prediction error is stored in a `model_feedback` table and used as a training signal. For `shelf.expired`, it records the **overestimation error** — the order was ready but not picked within the TTL, suggesting that the ETA was too long or the customer was not notified effectively. This negative signal is weighted 3x in training to aggressively correct overestimation patterns.

### 4.9 Arrival Service (Port 8008)

The Arrival Service is responsible for **deterministically confirming that a customer has physically arrived at the kitchen**. This is a critical prerequisite for the `ARRIVED` state transition and triggers downstream effects: the customer's order gets a +20 priority boost, kitchen staff receive a handoff alert, and the order is marked as "arrival-confirmed" on the shelf display.

The service supports **two detection mechanisms**: GPS-based and QR-based. GPS detection uses the **haversine formula** to compute the great-circle distance between the customer's reported GPS coordinates and the kitchen's registered GPS coordinates. Two thresholds are defined: **hard arrival** (≤150m with GPS accuracy ≤50m) and **soft arrival** (≤500m with any accuracy). Hard arrival immediately triggers the `ARRIVED` transition. Soft arrival triggers a "nearby" notification to kitchen staff but does not change order state.

QR-based detection uses **rotating tokens**. When an order reaches `ON_SHELF`, the Arrival Service generates a QR token (`SHA256(order_id + kitchen_id + timestamp + secret)`) and sends it to the customer's app. The token rotates every 30 seconds; the Arrival Service maintains the last 3 valid tokens in Redis (`qr:tokens:{order_id}`) to account for clock skew. When the customer scans the QR code at the kitchen entrance, the scanned token is validated against the stored tokens. A match confirms arrival with 100% confidence (no GPS drift, no spoofing).

A **dedup engine** prevents duplicate arrival events. Within a 60-second window, only one arrival event per `order_id` is processed. Subsequent events within the window are silently dropped. This handles scenarios where a customer's app sends multiple GPS pings or scans the QR code twice.

Upon confirmed arrival, the Arrival Service: (1) transitions the order to `ARRIVED`, (2) applies a +20 priority boost to the order's queue position (moving it ahead of non-arrived orders), (3) emits an `order.arrived` event to Kafka, (4) notifies kitchen staff via the Realtime Service dashboard.

### 4.10 Compensation Service (Port 8010)

The Compensation Service handles all financial remediation — refunds, credits, and kitchen penalties. It is the most financially sensitive service in the system and is designed with **idempotency, auditability, and regulatory compliance** as top priorities.

The service processes compensation requests triggered by 6 defined reasons (detailed in Section 8). Each request goes through a **decision engine** that evaluates: (a) is the reason valid for the current order state? (b) has a compensation already been issued for this order + reason combination? (c) what is the appropriate refund type? (d) does the kitchen bear financial responsibility?

**UPI refund processing** is simulated in the current deployment (production UPI integration requires NPCI certification). The simulation records the refund intent in the `compensations` table with status `PROCESSING`, waits 2 seconds (simulating UPI round-trip), then updates to `COMPLETED`. In production, the service would call the UPI refund API with idempotency keys and handle 5 possible gateway responses: SUCCESS, PENDING, FAILED, TIMEOUT, and REJECTED. Only SUCCESS transitions to COMPLETED; PENDING triggers a polling loop (max 10 retries, 30s interval); FAILED and REJECTED escalate to manual review; TIMEOUT triggers a reconciliation batch job.

**Kitchen penalty tracking** records financial penalties against kitchens that cause compensation events. For example, if an order expires on the shelf due to kitchen delay (not customer no-show), the kitchen is penalized the order value + a 10% service fee. Penalties are tracked in the `kitchen_penalties` table and aggregated weekly for invoicing. The penalty decision is based on root-cause analysis: `KITCHEN_FAILURE` always triggers a penalty; `SHELF_EXPIRED` triggers a penalty only if the kitchen's `IN_PREP → ON_SHELF` time exceeded the SLA by >20%.

---

## 5. Event Flow (End-to-End V2)

The following describes the complete lifecycle of an order from creation to pickup, including all service interactions and event emissions.

### Step-by-Step Flow

1. **Order Creation** — Customer submits order via mobile app. API Gateway routes to Order Service. Order Service validates items, computes total, creates order in `CREATED` state, emits `order.created` event. Response includes `order_id` and `idempotency_key`.

2. **Payment Initiation** — Order Service transitions to `PAYMENT_PENDING`, emits `order.payment_pending`. UPI payment link is generated and sent to customer via Notification Service (WebSocket → Push → SMS).

3. **Payment Confirmation** — UPI gateway calls the webhook on Order Service. Order Service verifies payment signature, transitions to `PAYMENT_CONFIRMED`, emits `order.payment_confirmed`. Notification Service sends confirmation to customer.

4. **Slot Reservation** — Order Service calls Kitchen Service to reserve a prep slot. Kitchen Service checks station availability for the order's items. If a slot is available (guard: `slot_available == true`), it returns a `slot_id` and `estimated_prep_start`. Order Service transitions to `SLOT_RESERVED`, emits `order.slot_reserved`.

5. **Queue Assignment** — Kitchen Service computes `priority_score` based on wait time, VIP status, and kitchen load. Order is placed into the appropriate priority queue (URGENT/NORMAL/BATCH). Order Service transitions to `QUEUE_ASSIGNED`, emits `order.queue_assigned` with `priority_score` and `queue_name`.

6. **Prep Start** — When the order reaches the front of its queue, Kitchen Service assigns it to a specific station. Order Service transitions to `IN_PREP`, emits `order.prep_started` with `station_id`. ETA Service consumes this event and computes the initial ETA prediction.

7. **Packing** — Station reports cooking complete. Kitchen Service transitions order through `PACKING`, emits `order.packing`. Item is packaged, labeled with order ID and shelf zone.

8. **Ready** — Packing complete. Kitchen Service transitions to `READY`, emits `order.ready`. Shelf Service is notified to prepare a shelf slot.

9. **Shelf Placement** — Shelf Service identifies an available slot in the correct zone, acquires Redis SETNX lock, and confirms placement. Order Service transitions to `ON_SHELF`, emits `order.on_shelf` with `shelf_id`, `zone`, and `ttl_expires_at`. Notification Service sends "Your order is ready for pickup!" to customer via all channels. TTL countdown begins.

10. **Arrival Detection** — Customer approaches kitchen. Arrival Service receives GPS pings from customer's app. When haversine distance ≤150m (or customer scans QR code), arrival is confirmed. Order Service transitions to `ARRIVED`, emits `order.arrived`. Priority boost +20 is applied. Kitchen staff receive handoff alert via Realtime Service dashboard.

11. **Handoff** — Kitchen staff picks the item from the shelf. Order Service transitions to `HANDOFF_IN_PROGRESS`, emits `order.handoff_started`. Shelf Service releases the Redis SETNX lock, freeing the shelf slot.

12. **Pickup Complete** — Customer confirms receipt (app tap or QR scan). Order Service transitions to `PICKED`, emits `order.picked`. ML Service consumes this event for feedback loop (actual vs. predicted ETA). Notification Service sends "Enjoy your meal!" notification.

### Failure Paths

- **Payment timeout**: If `PAYMENT_PENDING` persists for >10 minutes without confirmation, Order Service transitions to `FAILED`, emits `order.failed`. Compensation Service evaluates for refund if payment was partially captured.
- **No shelf available**: If Shelf Service cannot find a slot, the order remains in `READY`. Kitchen Service pauses new READY transitions until a slot frees up. If the wait exceeds 5 minutes, an alert is escalated to kitchen operations.
- **Shelf expiry**: Shelf sweeper detects TTL exceeded. Order transitions to `EXPIRED`, then to `REFUNDED`. Compensation Service processes full refund and evaluates kitchen penalty.
- **Customer no-show**: If order is `ON_SHELF` with no arrival detected within 80% of TTL, Notification Service sends a "Your order is waiting!" reminder. If no arrival by TTL, the order expires.
- **Kitchen failure**: If kitchen reports a system failure (equipment breakdown, ingredient shortage) while order is `IN_PREP`, Kitchen Service emits `kitchen.failure`. Order Service transitions to `FAILED`. Compensation Service processes refund with kitchen penalty.

---

## 6. Data Layer Strategy

The data layer is designed for **separation of concerns**: Postgres for transactional truth, Redis for low-latency projections, Kafka for event distribution, and S3 for ML artifacts. All tables are partitioned by `kitchen_id` to support sharding and efficient queries per kitchen.

### Core Tables (PostgreSQL)

| Table | Purpose | Key Columns | Partition |
|-------|---------|-------------|-----------|
| `orders` | Source of truth for order state | `order_id`, `kitchen_id`, `state`, `customer_id`, `total_amount`, `created_at` | Range by `kitchen_id` |
| `order_events` | Event sourcing log | `event_id`, `order_id`, `from_state`, `to_state`, `guard_result`, `metadata` (JSONB) | Range by `kitchen_id` |
| `kitchens` | Kitchen profile and configuration | `kitchen_id`, `name`, `gps_lat`, `gps_lng`, `station_config` (JSONB), `zone_config` (JSONB) | — |
| `kitchen_stations` | Station definitions per kitchen | `station_id`, `kitchen_id`, `station_type`, `concurrency_limit`, `avg_prep_seconds` | Range by `kitchen_id` |
| `shelf_slots` | Physical shelf inventory | `slot_id`, `kitchen_id`, `zone`, `is_occupied`, `current_order_id`, `ttl_expires_at` | Range by `kitchen_id` |

### V2 Addition Tables (PostgreSQL)

| Table | Purpose | Key Columns |
|-------|---------|-------------|
| `compensations` | Refund and credit tracking | `compensation_id`, `order_id`, `reason`, `refund_type`, `amount`, `status`, `idempotency_key`, `created_at` |
| `arrivals` | Customer arrival events | `arrival_id`, `order_id`, `detection_method` (GPS/QR), `gps_distance_meters`, `qr_token_used`, `detected_at` |
| `kitchen_penalties` | Financial penalties against kitchens | `penalty_id`, `kitchen_id`, `order_id`, `reason`, `penalty_amount`, `invoice_status`, `created_at` |
| `priority_queue` | Queue assignments with scores | `queue_id`, `order_id`, `kitchen_id`, `queue_name`, `priority_score`, `assigned_at` |
| `model_feedback` | ML prediction accuracy tracking | `feedback_id`, `order_id`, `predicted_eta_minutes`, `actual_eta_minutes`, `error_minutes`, `model_version` |

### Redis Key Schema

| Key Pattern | Type | TTL | Purpose |
|-------------|------|-----|---------|
| `shelf:{kitchen_id}:{zone}:{slot_id}` | STRING (SETNX) | 300s | Shelf slot distributed lock |
| `kitchen:load:{kitchen_id}` | HASH | 60s | Real-time station load counters |
| `eta:cache:{order_id}` | STRING (JSON) | 120s | Cached ETA prediction |
| `idempotency:{key}` | STRING (JSON) | 86400s | Idempotent response cache |
| `qr:tokens:{order_id}` | LIST | 300s | Last 3 valid QR tokens |
| `arrival:dedup:{order_id}` | STRING | 60s | Arrival event dedup window |
| `features:kitchen:{kitchen_id}` | HASH | 3600s | ML feature store (kitchen) |
| `features:item:{item_id}` | HASH | 3600s | ML feature store (item) |
| `ws:connections:{order_id}` | HASH | — | WebSocket connection registry |

---

## 7. Arrival Detection Architecture

Arrival detection is a mission-critical subsystem that bridges the digital and physical worlds. An order cannot transition to `ARRIVED` — and therefore cannot proceed to handoff — until the Arrival Service confirms the customer's physical presence. This prevents phantom arrivals, reduces handoff errors, and enables intelligent priority reordering.

### GPS-Based Detection

The GPS detection pipeline operates as follows:

1. **Ping Ingestion**: The customer's app sends GPS coordinates every 5 seconds when within 1km of the kitchen (geofence trigger). Each ping includes: `latitude`, `longitude`, `accuracy_meters`, `timestamp`.

2. **Haversine Computation**: The Arrival Service computes the haversine distance between the customer's coordinates and the kitchen's registered coordinates:
   ```
   d = 2R × arcsin(√(sin²((lat2-lat1)/2) + cos(lat1)×cos(lat2)×sin²((lon2-lon1)/2)))
   ```
   Where R = 6,371,000 meters (Earth's radius).

3. **Threshold Evaluation**:
   - **Hard arrival**: `distance ≤ 150m AND accuracy ≤ 50m` → Immediate `ARRIVED` transition
   - **Soft arrival**: `distance ≤ 500m` → "Customer nearby" notification to kitchen staff, no state change
   - **No arrival**: `distance > 500m` → Ping logged, no action

4. **Accuracy Filter**: GPS accuracy > 50m is rejected for hard arrival to prevent false positives from indoor GPS drift (common in Indian malls and tech parks where kitchen pickup counters are located). The accuracy filter can be configured per kitchen — outdoor kitchens may use a looser threshold (100m).

5. **Spoofing Prevention**: The Arrival Service maintains a velocity check. If consecutive pings imply movement > 200 km/h (impossible on foot), the session is flagged for review. GPS coordinates that jump between distant locations within seconds are rejected.

### QR-Based Detection

QR detection provides a **deterministic, spoof-resistant** alternative to GPS:

1. **Token Generation**: When an order reaches `ON_SHELF`, the Arrival Service generates a QR token: `SHA256(order_id || kitchen_id || epoch_minute || HMAC_SECRET)`. The token is encoded as a Base64 string and rendered as a QR code in the customer's app.

2. **Token Rotation**: Tokens rotate every 30 seconds. The Arrival Service stores the last 3 valid tokens in Redis (`qr:tokens:{order_id}`) to handle clock skew between the server and the customer's device. Each token has a 90-second validity window (current + 2 previous).

3. **Scan Validation**: When the customer scans the QR code at the kitchen entrance kiosk, the kiosk sends the scanned token to the Arrival Service. The service checks the token against `qr:tokens:{order_id}`. A match confirms arrival with 100% confidence.

4. **Single-Use Enforcement**: Once a QR token is used for arrival confirmation, it is immediately invalidated in Redis. This prevents token sharing or screenshot-based spoofing.

### Dedup Engine

The dedup engine prevents a single arrival from generating multiple events:

- **Window**: 60 seconds per `order_id`. After the first confirmed arrival, any subsequent GPS pings or QR scans within the window are silently dropped.
- **Storage**: `arrival:dedup:{order_id}` in Redis with 60s TTL. The value is the `arrival_id` of the first confirmed event.
- **Cross-method dedup**: If a customer triggers GPS hard arrival and then scans QR within 60 seconds, the QR scan is treated as a duplicate and dropped. The original GPS-based arrival event is preserved.

### Priority Boost

Upon confirmed arrival, the customer's order receives a **+20 priority boost**. This means:

- In the kitchen queue, the order is moved ahead of non-arrived orders with lower priority scores.
- On the shelf display, the order is highlighted with an "ARRIVED" badge, signaling to kitchen staff that the customer is physically waiting.
- If multiple orders for the same kitchen have arrived customers, they are served in arrival-timestamp order (first-come, first-served among arrivals).

The priority boost is computed by: `new_priority_score = current_priority_score + 20`. This is applied to the `priority_queue` table and reflected in the Kitchen Service's dequeue logic within 5 seconds.

---

## 8. Compensation Engine Architecture

The Compensation Engine is the financial safety net of HotSot V2. It handles every scenario where a customer's experience deviates from the expected outcome, ensuring fair remediation while holding kitchens accountable for preventable failures.

### Compensation Reasons

| Reason | Description | Trigger Condition | Refund Type | Kitchen Penalty |
|--------|-------------|-------------------|-------------|-----------------|
| `SHELF_EXPIRED` | Order expired on shelf without pickup | `ON_SHELF` TTL exceeded, customer did not arrive | Full refund | Yes, if kitchen `IN_PREP → ON_SHELF` exceeded SLA by >20% |
| `KITCHEN_FAILURE` | Kitchen could not fulfill the order | Equipment failure, ingredient shortage, staff absence | Full refund + 10% credit | Yes, order value + 10% service fee |
| `PAYMENT_CONFLICT` | Payment captured but order not created | UPI webhook confirmed payment but Order Service did not record it | Full refund | No |
| `DELAY_EXCEEDED` | Actual wait time exceeded promised ETA by >15 minutes | `actual_pickup_time - promised_eta > 15 minutes` | Partial refund (50%) | Yes, if delay was kitchen-caused |
| `WRONG_ORDER_DELIVERED` | Customer received incorrect items | Customer report + kitchen verification within 2 hours | Full refund + reorder | Yes, 150% of order value |
| `CUSTOMER_COMPLAINT` | General quality/taste complaint | Customer submission via app, verified by ops team within 24 hours | Partial credit (30%) | Case-by-case |

### Refund Types

1. **Full Refund**: Entire order amount returned to the original UPI payment method. Processing time: instant (HotSot wallet) or T+1 (UPI bank). Used for `SHELF_EXPIRED`, `KITCHEN_FAILURE`, `PAYMENT_CONFLICT`, and `WRONG_ORDER_DELIVERED`.

2. **Partial Refund**: 50% of order amount returned to UPI. Used for `DELAY_EXCEEDED`. The remaining 50% is absorbed by HotSot (not charged to kitchen) as a shared responsibility model.

3. **Credit Issued**: HotSot wallet credit (no UPI refund). Used for `CUSTOMER_COMPLAINT` and as a supplementary gesture for `DELAY_EXCEEDED`. Credits expire after 90 days and are applied automatically to the next order.

4. **Reorder**: A new order is automatically created with the same items at no charge, with URGENT priority. Used exclusively for `WRONG_ORDER_DELIVERED`. The original order is marked `REFUNDED`, the new order enters at `PAYMENT_CONFIRMED` (skipping payment), and the Kitchen Service is instructed to prioritize it.

### Idempotency

Every compensation request is guarded by an `idempotency_key` (format: `comp:{order_id}:{reason}:{timestamp_bucket}`):

1. The Compensation Service checks `idempotency:{key}` in Redis before processing.
2. If the key exists, the service returns the cached compensation result without re-processing.
3. If the key does not exist, the service processes the compensation, stores the result in Redis with a 72-hour TTL, and returns it.

This prevents double-refunds in scenarios where: (a) the client retries the compensation request due to network timeout, (b) the Kafka consumer replays a `shelf.expired` event, or (c) an ops team member manually triggers a compensation that was already auto-processed.

### UPI Refund Simulation

In the current deployment, UPI refunds are simulated:

```
1. Compensation Service creates record in `compensations` table with status = PROCESSING
2. Async worker picks up the record after 2-second delay (simulating UPI round-trip)
3. Worker updates status = COMPLETED, records `refund_reference_id` (UUID)
4. Notification Service sends "Refund of ₹{amount} processed" to customer
```

In production, the service would integrate with a UPI payment gateway (Razorpay/PayU) using their refund API with idempotency headers. The gateway response codes would be handled as follows:

| Response | Action |
|----------|--------|
| `SUCCESS` | Mark COMPLETED, notify customer |
| `PENDING` | Start polling loop (10 retries, 30s interval), mark COMPLETED on success |
| `FAILED` | Mark FAILED, escalate to manual ops review queue |
| `TIMEOUT` | Mark RETRYING, schedule reconciliation batch job |
| `REJECTED` | Mark REJECTED, escalate to compliance team |

### Kitchen Penalty Tracking

Kitchen penalties serve as a financial accountability mechanism:

- **Penalty recording**: When a compensation is issued with a kitchen penalty, the Compensation Service creates a record in `kitchen_penalties` with `invoice_status = PENDING`.
- **Weekly aggregation**: Every Monday at 06:00 IST, a batch job aggregates all PENDING penalties by `kitchen_id` and generates an invoice record.
- **Deduction mechanism**: Penalties are deducted from the kitchen's weekly payout (the revenue share that HotSot owes the kitchen). If penalties exceed payout, the kitchen receives a debit notice.
- **Dispute window**: Kitchens have 48 hours to dispute a penalty. Disputed penalties are moved to `DISPUTED` status and reviewed by the operations team. Undisputed penalties auto-transition to `CONFIRMED` after 48 hours.
- **Escalation**: Kitchens with penalty rates exceeding 5% of total orders in a rolling 7-day window are flagged for an operational review. Persistent offenders (>10% for 14 consecutive days) face temporary suspension.

---

## 9. India-Specific Design

HotSot is built from the ground up for Indian market realities. Every subsystem accounts for the unique constraints of Indian infrastructure, consumer behavior, and operational patterns.

### UPI-First Payments

India's payment landscape is UPI-dominated, and HotSot embraces this fully:

- **No COD**: Cash-on-delivery is not supported for pickup orders. This eliminates cash-handling overhead at kitchens and removes reconciliation complexity. All payments are digital: UPI (primary), debit/credit cards (secondary), HotSot wallet (tertiary).
- **Idempotent UPI APIs**: Every payment request includes a `transaction_ref` that is unique per order + attempt. The payment gateway is queried for duplicate `transaction_ref` before initiating a new payment. This prevents double-charges when a customer retries a stuck payment.
- **Payment polling**: UPI payment confirmation is not always delivered via webhook (bank-side issues are common). The Order Service implements a polling fallback: after initiating payment, it polls the gateway every 10 seconds for up to 5 minutes. This handles the common scenario where the UPI push notification is delayed but the payment has already succeeded.
- **Partial payment handling**: For orders with multiple items from different kitchens, payment is captured as a single UPI transaction but split internally. If one kitchen fails to fulfill, only that portion is refunded (partial refund), and the customer is not forced to re-pay for the successful portion.

### Offline-Safe Design

Indian mobile networks are notoriously unreliable, especially in Tier-2/3 cities and during peak hours:

- **Optimistic order creation**: The mobile app creates an order locally and syncs when connectivity returns. The `idempotency_key` (UUID generated client-side) ensures that the server processes the order exactly once, even if the client sends it 3 times.
- **Queue-and-forward**: The app maintains a local action queue (state transitions, GPS pings) that is flushed to the server in batch when connectivity is restored. Actions are timestamped client-side for causal ordering.
- **Graceful degradation**: If the Realtime Service WebSocket connection drops, the app falls back to HTTP polling (every 15 seconds) for order updates. If even HTTP fails, the app displays the last-known state with a "Reconnecting..." indicator and continues accepting user actions locally.

### Festival Load Handling

Indian festivals create order volume spikes that dwarf normal operations:

- **Festival calendar**: The system maintains a festival calendar (configurable per city) with expected multiplier ranges. Known high-volume festivals include Diwali (1.8x), Holi (1.5x), Eid (1.6x), Christmas (1.3x), and Pongal (1.4x). The calendar is updated quarterly by the operations team.
- **Pre-provisioning**: When a festival is detected (3 days before), the Kitchen Service increases station concurrency limits by the festival multiplier, the Shelf Service adds temporary shelf slots, and the ETA Service applies the multiplier to all predictions.
- **Queue depth limits**: During festivals, the URGENT queue depth is capped at 50 orders per kitchen (vs. normal cap of 20). Orders exceeding the cap are rejected with a "Kitchen at capacity" message and offered alternative time slots.

### Monsoon Factor

Monsoon season (June–September) affects both kitchen operations and customer behavior:

- **Delay multiplier**: 1.3x applied to all ETA predictions during monsoon-affected months. This accounts for: slower prep times (humidity affects cooking), delayed customer arrival (rain), and increased order volume (people prefer pickup over dining out in rain).
- **Ingredient supply alerts**: The Kitchen Service monitors ingredient availability and triggers `KITCHEN_FAILURE` if a key ingredient is unavailable due to supply chain disruption (common during monsoon flooding in Mumbai, Chennai, and Kolkata).

### Peak Hours

Peak hours follow predictable patterns in Indian urban markets:

- **Lunch peak**: 12:00–14:00 IST (1.5x multiplier). Driven by office workers ordering pickup lunches.
- **Dinner peak**: 20:00–22:00 IST (1.5x multiplier). Driven by families ordering dinner pickup.
- **Weekend brunch**: 10:00–12:00 IST on Saturdays and Sundays (1.2x multiplier). Emerging pattern in metro cities.
- **Late night**: 22:00–00:00 IST on Fridays and Saturdays (1.1x multiplier). Limited to kitchens with late-night licenses.

The peak-hour multiplier is applied cumulatively with festival and monsoon multipliers. During a Diwali dinner peak, the effective multiplier is `1.8 × 1.5 = 2.7x`.

### SMS Fallback

For customers in low-bandwidth areas (Tier-2/3 cities, rural-adjacent urban areas):

- **Critical notifications via SMS**: Payment confirmation, order ready, and arrival confirmation are always sent via SMS in addition to push notifications. SMS delivery rate in India is >98% within 30 seconds, making it the most reliable notification channel.
- **SMS-based order tracking**: Customers can text "STATUS {order_id}" to the HotSot SMS number to receive a text reply with current order state and ETA. This serves customers whose smartphones lack reliable data connectivity.
- **Cost optimization**: SMS is expensive at scale (₹0.15–0.50 per message). The Notification Service uses SMS only for critical notifications and only when the customer's app has not confirmed receipt via WebSocket or push within 60 seconds. This reduces SMS volume by ~70% compared to always-SMS strategies.

---

## 10. Scale Targets

HotSot V2 is engineered to meet the following scale targets, validated through load testing and production monitoring:

| Metric | Target | Measurement Method |
|--------|--------|--------------------|
| Peak order throughput | **10,000 orders/sec** | Kafka partition throughput, Order Service TPS |
| Kitchen coverage | **200+ kitchens per city** | Kitchen Service registration count |
| ETA inference latency | **30–50ms** | P99 latency of ETA Service inference endpoint |
| Pickup accuracy | **95%+** | Ratio of orders picked within ±2 minutes of predicted ETA |
| ETA guarantee | **±2 minutes** | P95 of `abs(predicted_eta - actual_pickup_time)` |
| Shelf lock acquisition | **<10ms** | P99 latency of Redis SETNX operation |
| Arrival detection latency | **<5 seconds** | Time from GPS ping/QR scan to ARRIVED state transition |
| Compensation processing | **<30 seconds** | Time from trigger to refund status = PROCESSING |
| WebSocket message delivery | **<500ms** | P99 latency from Kafka event to client WebSocket frame |
| Notification delivery | **<10 seconds** | P99 latency from event trigger to notification SENT status |
| System availability | **99.9%** | Rolling 30-day uptime across all services |
| Idempotency hit rate | **>99%** | Ratio of idempotency key lookups served from Redis vs. recomputed |

### Scaling Strategy

- **Horizontal scaling**: All services are stateless (state lives in Postgres/Redis/Kafka) and can be scaled horizontally by adding instances behind the load balancer. The Realtime Service uses sticky sessions for WebSocket connections but can redistribute on instance failure.
- **Database scaling**: Postgres is partitioned by `kitchen_id` and read-replicated for dashboard queries. Write operations go to the primary; read operations (order tracking, kitchen dashboard) go to replicas. Connection pooling via PgBouncer limits connection count per service instance to 20.
- **Redis scaling**: Redis Cluster mode with 6 nodes (3 primary + 3 replica), sharded by `kitchen_id` hash slot. Hot keys (popular kitchens) are monitored and split across slots if they exceed 100K QPS.
- **Kafka scaling**: Topics are partitioned by `kitchen_id` with a target of 10K partitions across the cluster. Consumer groups are sized to match partition count, with one consumer thread per partition. Lag alerts trigger auto-scaling of consumer instances.

### Performance Budgets

Each service operates within a defined performance budget to prevent cascading failures:

| Service | Max P99 Latency | Max CPU | Max Memory |
|---------|----------------|---------|------------|
| API Gateway | 20ms | 60% | 512MB |
| Order Service | 100ms | 70% | 1GB |
| Kitchen Service | 80ms | 60% | 1GB |
| Shelf Service | 50ms | 50% | 512MB |
| ETA Service | 50ms | 80% | 2GB (model in memory) |
| Notification Service | 200ms | 50% | 512MB |
| Realtime Service | 30ms | 60% | 1GB |
| ML Service | 5min (batch) | 90% | 8GB (training) |
| Arrival Service | 50ms | 50% | 512MB |
| Compensation Service | 100ms | 50% | 512MB |

Any service exceeding its budget triggers an auto-scaling event (add 2 instances) and a PagerDuty alert for the on-call engineer. If auto-scaling does not resolve the budget violation within 10 minutes, the incident is escalated to L2.

---

*This document is the authoritative reference for HotSot V2 system design. All implementation decisions should trace back to the principles, state machine, and architecture defined herein. For API contracts, refer to `api-spec.md`. For deployment topology, refer to `architecture.md`.*
