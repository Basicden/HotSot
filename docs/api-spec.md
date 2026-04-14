# HotSot — V2 API Specification

> **Version:** 2.0.0
> **Last Updated:** 2025-03-04

---

## Table of Contents

1. [Base URL](#base-url)
2. [Authentication](#authentication)
3. [Error Response Format](#error-response-format)
4. [V2 State Machine](#v2-state-machine)
5. [Order Service (Port 8001)](#order-service)
6. [Kitchen Service (Port 8002)](#kitchen-service)
7. [Shelf Service (Port 8003)](#shelf-service)
8. [ETA Service (Port 8004)](#eta-service)
9. [Notification Service (Port 8005)](#notification-service)
10. [Arrival Service (Port 8008)](#arrival-service)
11. [Compensation Service (Port 8010)](#compensation-service)
12. [ML Service (Port 8007)](#ml-service)
13. [WebSocket](#websocket)

---

## Base URL

```
Production:  https://api.hotsot.in
Staging:     https://staging-api.hotsot.in
Local:       http://localhost:<port>   (port varies by service — see below)
```

| Service              | Port |
|----------------------|------|
| Order Service        | 8001 |
| Kitchen Service      | 8002 |
| Shelf Service        | 8003 |
| ETA Service          | 8004 |
| Notification Service | 8005 |
| ML Service           | 8007 |
| Arrival Service      | 8008 |
| Compensation Service | 8010 |

---

## Authentication

All endpoints (except health checks and WebSocket handshake) require an `Authorization` header:

```
Authorization: Bearer <jwt_token>
```

JWT tokens are issued by the Auth Service and carry the following claims:

| Claim          | Description                                    |
|----------------|------------------------------------------------|
| `sub`          | User or service identity                       |
| `role`         | One of `customer`, `kitchen_staff`, `admin`    |
| `kitchen_id`   | Kitchen scope (for kitchen-scoped operations)  |
| `exp`          | Token expiry (UNIX timestamp)                  |

### Role-Based Access

| Endpoint Category       | `customer` | `kitchen_staff` | `admin` |
|-------------------------|:----------:|:---------------:|:-------:|
| Order create / status   | ✅         | —               | ✅      |
| Order state transitions | —          | ✅              | ✅      |
| Kitchen / Shelf ops     | —          | ✅              | ✅      |
| Compensation / ML       | —          | —               | ✅      |
| Notification send       | —          | ✅              | ✅      |

---

## Error Response Format

All errors follow a consistent JSON structure:

```json
{
  "error": {
    "code": "INVALID_STATE_TRANSITION",
    "message": "Order o_abc123 cannot transition from READY to PAYMENT_PENDING",
    "details": {
      "current_state": "READY",
      "requested_transition": "PAYMENT_PENDING",
      "allowed_transitions": ["ON_SHELF", "EXPIRED", "CANCELLED"]
    },
    "request_id": "req_x9f2k1",
    "timestamp": "2025-03-04T12:34:56.789Z"
  }
}
```

### Standard Error Codes

| HTTP Status | Code                        | Description                                          |
|-------------|-----------------------------|------------------------------------------------------|
| 400         | `BAD_REQUEST`               | Malformed request body or missing required fields    |
| 400         | `INVALID_STATE_TRANSITION`  | Order state transition not allowed                   |
| 401         | `UNAUTHORIZED`              | Missing or invalid authentication token              |
| 403         | `FORBIDDEN`                 | Insufficient role / scope for this operation         |
| 404         | `NOT_FOUND`                 | Resource does not exist                              |
| 409         | `CONFLICT`                  | Duplicate resource or concurrent modification        |
| 422         | `VALIDATION_ERROR`          | Field-level validation failures                      |
| 429         | `RATE_LIMITED`              | Too many requests — retry after `Retry-After` header|
| 500         | `INTERNAL_ERROR`            | Unexpected server error                              |
| 503         | `SERVICE_UNAVAILABLE`       | Downstream dependency unavailable                    |

---

## V2 State Machine

The V2 order lifecycle expands from 6 states (V1) to **16 states**, enabling granular tracking of payment, kitchen, shelf, arrival, and handoff phases.

```
CREATED → PAYMENT_PENDING → PAYMENT_CONFIRMED → SLOT_RESERVED → QUEUE_ASSIGNED
  → IN_PREP → PACKING → READY → ON_SHELF → ARRIVED → HANDOFF_IN_PROGRESS → PICKED

Terminal states:
  ├── EXPIRED     (shelf TTL exceeded, no pickup)
  ├── REFUNDED    (payment reversed)
  ├── CANCELLED   (user or system cancellation)
  └── FAILED      (unrecoverable error)
```

### State Diagram

```
                    ┌──────────────────────────────────────────────────────────────────┐
                    │                                                                  │
  [Customer]        │  CREATED ──▶ PAYMENT_PENDING ──▶ PAYMENT_CONFIRMED              │
    places order    │                                    │                              │
                    │                              SLOT_RESERVED                      │
                    │                                    │                              │
                    │                            QUEUE_ASSIGNED                        │
                    │                                    │                              │
  [Kitchen]         │                              IN_PREP ◀─── (batch start)          │
    starts prep     │                                    │                              │
                    │                               PACKING                             │
                    │                                    │                              │
                    │                                READY                               │
                    │                                    │                              │
  [Shelf]           │                              ON_SHELF                             │
    assigns slot    │                                    │                              │
                    │                               ARRIVED  ◀─── (GPS/QR detect)      │
                    │                                    │                              │
  [Handoff]         │                       HANDOFF_IN_PROGRESS                        │
    hands off       │                                    │                              │
                    │                               PICKED ✅                           │
                    │                                                                  │
                    └──────────────────────────────────────────────────────────────────┘

  Any pre-PICKED state ──▶ CANCELLED  (user / system)
  Any pre-PICKED state ──▶ FAILED     (unrecoverable error)
  ON_SHELF            ──▶ EXPIRED     (TTL exceeded)
  PAYMENT_CONFIRMED+  ──▶ REFUNDED    (payment reversal)
```

### Transition Rules

| From                  | To                     | Trigger                              |
|-----------------------|------------------------|--------------------------------------|
| `CREATED`             | `PAYMENT_PENDING`      | `POST /orders/create`                |
| `PAYMENT_PENDING`     | `PAYMENT_CONFIRMED`    | `POST /orders/{id}/pay`              |
| `PAYMENT_CONFIRMED`   | `SLOT_RESERVED`        | `POST /orders/{id}/confirm-payment`  |
| `SLOT_RESERVED`       | `QUEUE_ASSIGNED`       | `POST /orders/{id}/assign-queue`     |
| `QUEUE_ASSIGNED`      | `IN_PREP`              | `POST /orders/{id}/start-prep`       |
| `IN_PREP`             | `PACKING`              | `POST /orders/{id}/packing`          |
| `PACKING`             | `READY`                | `POST /orders/{id}/ready`            |
| `READY`               | `ON_SHELF`             | `POST /orders/{id}/shelf`            |
| `ON_SHELF`            | `ARRIVED`              | `POST /orders/{id}/arrival`          |
| `ON_SHELF`            | `EXPIRED`              | `POST /orders/{id}/expire` (auto)    |
| `ARRIVED`             | `HANDOFF_IN_PROGRESS`  | `POST /orders/{id}/handoff`          |
| `HANDOFF_IN_PROGRESS` | `PICKED`               | `POST /orders/{id}/pickup`           |
| *(any pre-PICKED)*    | `CANCELLED`            | `POST /orders/{id}/cancel`           |
| *(any pre-PICKED)*    | `FAILED`               | `POST /orders/{id}/fail`             |
| `PAYMENT_CONFIRMED+`  | `REFUNDED`             | `POST /orders/{id}/refund`           |

---

## Order Service

**Base:** `http://localhost:8001`

The Order Service is the central orchestrator for the V2 state machine. Every state transition is persisted as an event, enabling full audit trails and event-sourced replay.

---

### POST /orders/create

Create a new order. The order enters the `PAYMENT_PENDING` state immediately.

**Request:**

```json
{
  "user_id": "u_abc123",
  "kitchen_id": "k_xyz789",
  "items": [
    {
      "menu_item_id": "mi_001",
      "name": "Paneer Tikka Wrap",
      "quantity": 2,
      "unit_price": 149.00
    }
  ],
  "total_amount": 298.00,
  "payment_method": "UPI",
  "metadata": {
    "source": "app",
    "platform_version": "2.4.1"
  }
}
```

| Field             | Type     | Required | Description                            |
|-------------------|----------|----------|----------------------------------------|
| `user_id`         | string   | ✅       | Customer identifier                    |
| `kitchen_id`      | string   | ✅       | Target kitchen                         |
| `items`           | array    | ✅       | Order line items                       |
| `items[].menu_item_id` | string | ✅    | Menu item reference                    |
| `items[].name`    | string   | ✅       | Display name                           |
| `items[].quantity`| integer  | ✅       | Quantity (≥ 1)                         |
| `items[].unit_price` | number | ✅      | Price per unit in INR                  |
| `total_amount`    | number   | ✅       | Total order amount in INR              |
| `payment_method`  | string   | ✅       | One of `UPI`, `CARD`, `WALLET`, `COD`  |
| `metadata`        | object   | ❌       | Arbitrary key-value metadata           |

**Response `201 Created`:**

```json
{
  "order_id": "o_9f8e7d",
  "status": "PAYMENT_PENDING",
  "kitchen_id": "k_xyz789",
  "total_amount": 298.00,
  "payment_method": "UPI",
  "created_at": "2025-03-04T12:00:00.000Z",
  "updated_at": "2025-03-04T12:00:00.000Z"
}
```

---

### POST /orders/{order_id}/pay

Initiate payment for a `PAYMENT_PENDING` order. On success, the order transitions to `PAYMENT_CONFIRMED`.

**Path Parameters:**

| Parameter  | Type   | Description     |
|------------|--------|-----------------|
| `order_id` | string | Order identifier|

**Request:**

```json
{
  "payment_ref": "pay_abc123def456",
  "payment_method": "UPI",
  "payment_gateway": "razorpay"
}
```

| Field             | Type   | Required | Description                             |
|-------------------|--------|----------|-----------------------------------------|
| `payment_ref`     | string | ✅       | Payment gateway reference               |
| `payment_method`  | string | ✅       | One of `UPI`, `CARD`, `WALLET`, `COD`   |
| `payment_gateway` | string | ❌       | Gateway name (default: `razorpay`)      |

**Response `200 OK`:**

```json
{
  "order_id": "o_9f8e7d",
  "status": "PAYMENT_CONFIRMED",
  "payment_ref": "pay_abc123def456",
  "paid_at": "2025-03-04T12:01:00.000Z"
}
```

**Errors:**

| Code                        | Condition                            |
|-----------------------------|--------------------------------------|
| `INVALID_STATE_TRANSITION`  | Order not in `PAYMENT_PENDING`       |
| `NOT_FOUND`                 | Order does not exist                 |

---

### POST /orders/{order_id}/confirm-payment

Confirm a payment and reserve a preparation slot. Transitions from `PAYMENT_CONFIRMED` → `SLOT_RESERVED`.

**Path Parameters:**

| Parameter  | Type   | Description     |
|------------|--------|-----------------|
| `order_id` | string | Order identifier|

**Request:**

```json
{
  "slot_time": "2025-03-04T12:15:00.000Z",
  "estimated_prep_minutes": 12
}
```

| Field                     | Type   | Required | Description                     |
|---------------------------|--------|----------|---------------------------------|
| `slot_time`               | string | ❌       | ISO 8601 target slot time       |
| `estimated_prep_minutes`  | number | ❌       | Estimated preparation time       |

**Response `200 OK`:**

```json
{
  "order_id": "o_9f8e7d",
  "status": "SLOT_RESERVED",
  "slot_time": "2025-03-04T12:15:00.000Z",
  "estimated_prep_minutes": 12
}
```

---

### POST /orders/{order_id}/assign-queue

Assign the order to a kitchen preparation queue. Transitions `SLOT_RESERVED` → `QUEUE_ASSIGNED`.

**Path Parameters:**

| Parameter  | Type   | Description     |
|------------|--------|-----------------|
| `order_id` | string | Order identifier|

**Request:**

```json
{
  "kitchen_id": "k_xyz789",
  "priority": "normal",
  "batch_id": "b_001"
}
```

| Field         | Type   | Required | Description                           |
|---------------|--------|----------|---------------------------------------|
| `kitchen_id`  | string | ✅       | Kitchen to assign                     |
| `priority`    | string | ❌       | One of `normal`, `high`, `urgent`     |
| `batch_id`    | string | ❌       | Batch grouping identifier             |

**Response `200 OK`:**

```json
{
  "order_id": "o_9f8e7d",
  "status": "QUEUE_ASSIGNED",
  "kitchen_id": "k_xyz789",
  "queue_position": 3,
  "estimated_start": "2025-03-04T12:10:00.000Z"
}
```

---

### POST /orders/{order_id}/start-prep

Begin preparing the order. Transitions `QUEUE_ASSIGNED` → `IN_PREP`.

**Path Parameters:**

| Parameter  | Type   | Description     |
|------------|--------|-----------------|
| `order_id` | string | Order identifier|

**Request:**

```json
{
  "assigned_chef": "chef_001",
  "station": "grill"
}
```

| Field             | Type   | Required | Description                  |
|-------------------|--------|----------|------------------------------|
| `assigned_chef`   | string | ❌       | Chef identifier              |
| `station`         | string | ❌       | Kitchen station name         |

**Response `200 OK`:**

```json
{
  "order_id": "o_9f8e7d",
  "status": "IN_PREP",
  "assigned_chef": "chef_001",
  "started_at": "2025-03-04T12:10:00.000Z"
}
```

---

### POST /orders/{order_id}/packing

Mark the order as being packed. Transitions `IN_PREP` → `PACKING`.

**Path Parameters:**

| Parameter  | Type   | Description     |
|------------|--------|-----------------|
| `order_id` | string | Order identifier|

**Request:**

```json
{
  "packaging_type": "standard",
  "special_instructions": "extra napkins"
}
```

| Field                   | Type   | Required | Description               |
|-------------------------|--------|----------|---------------------------|
| `packaging_type`        | string | ❌       | `standard`, `premium`     |
| `special_instructions`  | string | ❌       | Packing instructions      |

**Response `200 OK`:**

```json
{
  "order_id": "o_9f8e7d",
  "status": "PACKING",
  "packing_started_at": "2025-03-04T12:20:00.000Z"
}
```

---

### POST /orders/{order_id}/ready

Mark the order as ready for shelf placement. Transitions `PACKING` → `READY`.

**Path Parameters:**

| Parameter  | Type   | Description     |
|------------|--------|-----------------|
| `order_id` | string | Order identifier|

**Request:**

```json
{}
```

**Response `200 OK`:**

```json
{
  "order_id": "o_9f8e7d",
  "status": "READY",
  "ready_at": "2025-03-04T12:22:00.000Z"
}
```

---

### POST /orders/{order_id}/shelf

Assign the order to a shelf slot. Transitions `READY` → `ON_SHELF`.

**Path Parameters:**

| Parameter  | Type   | Description     |
|------------|--------|-----------------|
| `order_id` | string | Order identifier|

**Query Parameters:**

| Parameter  | Type   | Required | Description          |
|------------|--------|----------|----------------------|
| `shelf_id` | string | ❌       | Specific shelf slot  |

**Request:**

```json
{
  "shelf_id": "A1",
  "ttl_seconds": 900
}
```

| Field          | Type    | Required | Description                          |
|----------------|---------|----------|--------------------------------------|
| `shelf_id`     | string  | ❌       | Shelf slot (auto-assigned if empty)  |
| `ttl_seconds`  | integer | ❌       | Shelf TTL (default: 900 = 15 min)    |

**Response `200 OK`:**

```json
{
  "order_id": "o_9f8e7d",
  "status": "ON_SHELF",
  "shelf_id": "A1",
  "placed_at": "2025-03-04T12:22:30.000Z",
  "expires_at": "2025-03-04T12:37:30.000Z"
}
```

---

### POST /orders/{order_id}/arrival

Mark the customer as having arrived at the kitchen. Transitions `ON_SHELF` → `ARRIVED`.

**Path Parameters:**

| Parameter  | Type   | Description     |
|------------|--------|-----------------|
| `order_id` | string | Order identifier|

**Request:**

```json
{
  "detection_method": "GPS",
  "confidence": 0.95
}
```

| Field               | Type   | Required | Description                       |
|---------------------|--------|----------|-----------------------------------|
| `detection_method`  | string | ✅       | One of `GPS`, `QR`, `MANUAL`      |
| `confidence`        | number | ❌       | Detection confidence (0.0 – 1.0)  |

**Response `200 OK`:**

```json
{
  "order_id": "o_9f8e7d",
  "status": "ARRIVED",
  "detected_at": "2025-03-04T12:30:00.000Z",
  "detection_method": "GPS"
}
```

---

### POST /orders/{order_id}/handoff

Initiate the handoff process. Transitions `ARRIVED` → `HANDOFF_IN_PROGRESS`.

**Path Parameters:**

| Parameter  | Type   | Description     |
|------------|--------|-----------------|
| `order_id` | string | Order identifier|

**Request:**

```json
{
  "handoff_by": "staff_001",
  "verification_code": "4829"
}
```

| Field               | Type   | Required | Description                       |
|---------------------|--------|----------|-----------------------------------|
| `handoff_by`        | string | ❌       | Staff member performing handoff   |
| `verification_code` | string | ❌       | Customer verification code        |

**Response `200 OK`:**

```json
{
  "order_id": "o_9f8e7d",
  "status": "HANDOFF_IN_PROGRESS",
  "handoff_by": "staff_001",
  "handoff_started_at": "2025-03-04T12:31:00.000Z"
}
```

---

### POST /orders/{order_id}/pickup

Complete the order pickup. Transitions `HANDOFF_IN_PROGRESS` → `PICKED`. This is the happy-path terminal state.

**Path Parameters:**

| Parameter  | Type   | Description     |
|------------|--------|-----------------|
| `order_id` | string | Order identifier|

**Request:**

```json
{
  "picked_by": "u_abc123",
  "verification_code": "4829"
}
```

| Field               | Type   | Required | Description                       |
|---------------------|--------|----------|-----------------------------------|
| `picked_by`         | string | ❌       | Person who picked up the order    |
| `verification_code` | string | ❌       | Verification code confirmation    |

**Response `200 OK`:**

```json
{
  "order_id": "o_9f8e7d",
  "status": "PICKED",
  "picked_at": "2025-03-04T12:32:00.000Z",
  "total_duration_seconds": 1920
}
```

---

### POST /orders/{order_id}/expire

Mark the order as expired. Can transition from `ON_SHELF` → `EXPIRED` when the shelf TTL is exceeded.

**Path Parameters:**

| Parameter  | Type   | Description     |
|------------|--------|-----------------|
| `order_id` | string | Order identifier|

**Request:**

```json
{
  "reason": "shelf_ttl_exceeded",
  "expired_at": "2025-03-04T12:37:30.000Z"
}
```

| Field         | Type   | Required | Description                       |
|---------------|--------|----------|-----------------------------------|
| `reason`      | string | ❌       | Expiry reason                     |
| `expired_at`  | string | ❌       | ISO 8601 timestamp of expiry      |

**Response `200 OK`:**

```json
{
  "order_id": "o_9f8e7d",
  "status": "EXPIRED",
  "expired_at": "2025-03-04T12:37:30.000Z",
  "reason": "shelf_ttl_exceeded"
}
```

---

### POST /orders/{order_id}/refund

Process a refund for the order. Transitions to `REFUNDED`. Available from any state after `PAYMENT_CONFIRMED`.

**Path Parameters:**

| Parameter  | Type   | Description     |
|------------|--------|-----------------|
| `order_id` | string | Order identifier|

**Request:**

```json
{
  "refund_amount": 298.00,
  "reason": "order_expired",
  "initiated_by": "system"
}
```

| Field            | Type   | Required | Description                          |
|------------------|--------|----------|--------------------------------------|
| `refund_amount`  | number | ✅       | Amount to refund in INR              |
| `reason`         | string | ✅       | Refund reason                        |
| `initiated_by`   | string | ❌       | One of `customer`, `system`, `admin` |

**Response `200 OK`:**

```json
{
  "order_id": "o_9f8e7d",
  "status": "REFUNDED",
  "refund_amount": 298.00,
  "refund_ref": "ref_abc123",
  "refunded_at": "2025-03-04T12:38:00.000Z"
}
```

---

### POST /orders/{order_id}/cancel

Cancel the order. Transitions to `CANCELLED`. Available from any pre-`PICKED` state.

**Path Parameters:**

| Parameter  | Type   | Description     |
|------------|--------|-----------------|
| `order_id` | string | Order identifier|

**Request:**

```json
{
  "reason": "customer_request",
  "cancelled_by": "u_abc123"
}
```

| Field           | Type   | Required | Description                                   |
|-----------------|--------|----------|-----------------------------------------------|
| `reason`        | string | ✅       | Cancellation reason                           |
| `cancelled_by`  | string | ❌       | Identity that triggered the cancellation      |

**Response `200 OK`:**

```json
{
  "order_id": "o_9f8e7d",
  "status": "CANCELLED",
  "cancelled_at": "2025-03-04T12:05:00.000Z",
  "reason": "customer_request"
}
```

---

### POST /orders/{order_id}/fail

Mark the order as failed. Transitions to `FAILED`. Available from any pre-`PICKED` state for unrecoverable errors.

**Path Parameters:**

| Parameter  | Type   | Description     |
|------------|--------|-----------------|
| `order_id` | string | Order identifier|

**Request:**

```json
{
  "reason": "kitchen_equipment_failure",
  "error_code": "KITCHEN_001",
  "details": "Grill malfunction — unable to complete order"
}
```

| Field         | Type   | Required | Description                       |
|---------------|--------|----------|-----------------------------------|
| `reason`      | string | ✅       | Failure reason                    |
| `error_code`  | string | ❌       | Internal error code               |
| `details`     | string | ❌       | Additional failure details        |

**Response `200 OK`:**

```json
{
  "order_id": "o_9f8e7d",
  "status": "FAILED",
  "failed_at": "2025-03-04T12:15:00.000Z",
  "reason": "kitchen_equipment_failure",
  "error_code": "KITCHEN_001"
}
```

---

### GET /orders/{order_id}

Retrieve full order details including current state, timestamps, and metadata.

**Path Parameters:**

| Parameter  | Type   | Description     |
|------------|--------|-----------------|
| `order_id` | string | Order identifier|

**Query Parameters:**

| Parameter | Type    | Required | Description                     |
|-----------|---------|----------|---------------------------------|
| `include` | string  | ❌       | Comma-separated: `events,eta,shelf` |

**Response `200 OK`:**

```json
{
  "order_id": "o_9f8e7d",
  "user_id": "u_abc123",
  "kitchen_id": "k_xyz789",
  "status": "ON_SHELF",
  "items": [
    {
      "menu_item_id": "mi_001",
      "name": "Paneer Tikka Wrap",
      "quantity": 2,
      "unit_price": 149.00
    }
  ],
  "total_amount": 298.00,
  "payment_method": "UPI",
  "payment_ref": "pay_abc123def456",
  "shelf_id": "A1",
  "eta_seconds": 480,
  "state_history": [
    { "state": "CREATED", "entered_at": "2025-03-04T12:00:00.000Z" },
    { "state": "PAYMENT_PENDING", "entered_at": "2025-03-04T12:00:00.000Z" },
    { "state": "PAYMENT_CONFIRMED", "entered_at": "2025-03-04T12:01:00.000Z" },
    { "state": "SLOT_RESERVED", "entered_at": "2025-03-04T12:01:30.000Z" },
    { "state": "QUEUE_ASSIGNED", "entered_at": "2025-03-04T12:02:00.000Z" },
    { "state": "IN_PREP", "entered_at": "2025-03-04T12:10:00.000Z" },
    { "state": "PACKING", "entered_at": "2025-03-04T12:20:00.000Z" },
    { "state": "READY", "entered_at": "2025-03-04T12:22:00.000Z" },
    { "state": "ON_SHELF", "entered_at": "2025-03-04T12:22:30.000Z" }
  ],
  "created_at": "2025-03-04T12:00:00.000Z",
  "updated_at": "2025-03-04T12:22:30.000Z"
}
```

---

### GET /orders/{order_id}/events

Retrieve the complete event log for an order. Events are emitted on every state transition and can include arbitrary payloads.

**Path Parameters:**

| Parameter  | Type   | Description     |
|------------|--------|-----------------|
| `order_id` | string | Order identifier|

**Query Parameters:**

| Parameter    | Type    | Required | Description                         |
|--------------|---------|----------|-------------------------------------|
| `from`       | string  | ❌       | ISO 8601 — start of range           |
| `to`         | string  | ❌       | ISO 8601 — end of range             |
| `event_type` | string  | ❌       | Filter by event type                |
| `limit`      | integer | ❌       | Max events (default 100, max 1000)  |

**Response `200 OK`:**

```json
{
  "order_id": "o_9f8e7d",
  "events": [
    {
      "event_id": "evt_001",
      "event_type": "ORDER_CREATED",
      "payload": { "kitchen_id": "k_xyz789", "total_amount": 298.00 },
      "created_at": "2025-03-04T12:00:00.000Z"
    },
    {
      "event_id": "evt_002",
      "event_type": "PAYMENT_CONFIRMED",
      "payload": { "payment_ref": "pay_abc123def456", "payment_method": "UPI" },
      "created_at": "2025-03-04T12:01:00.000Z"
    }
  ],
  "total_count": 9,
  "has_more": false
}
```

---

### GET /orders/kitchen-queue/{kitchen_id}

Retrieve the current kitchen queue for a specific kitchen, including all orders in queue-assigned or in-prep states.

**Path Parameters:**

| Parameter    | Type   | Description      |
|--------------|--------|------------------|
| `kitchen_id` | string | Kitchen identifier|

**Query Parameters:**

| Parameter   | Type    | Required | Description                          |
|-------------|---------|----------|--------------------------------------|
| `status`    | string  | ❌       | Filter by order status               |
| `priority`  | string  | ❌       | Filter by priority                   |
| `limit`     | integer | ❌       | Max results (default 50, max 200)    |
| `offset`    | integer | ❌       | Pagination offset                    |

**Response `200 OK`:**

```json
{
  "kitchen_id": "k_xyz789",
  "queue_depth": 5,
  "orders": [
    {
      "order_id": "o_9f8e7d",
      "status": "IN_PREP",
      "priority": "normal",
      "assigned_chef": "chef_001",
      "entered_queue_at": "2025-03-04T12:02:00.000Z",
      "estimated_ready": "2025-03-04T12:22:00.000Z"
    }
  ],
  "is_overloaded": false
}
```

---

## Kitchen Service

**Base:** `http://localhost:8002`

Manages kitchen entities, queue management, and batch optimization.

---

### POST /kitchen/

Create a new kitchen.

**Request:**

```json
{
  "name": "Koramangala Hub",
  "location": {
    "latitude": 12.9352,
    "longitude": 77.6245
  },
  "capacity": {
    "max_concurrent_orders": 20,
    "stations": ["grill", "tandoor", "prep", "packing"]
  },
  "operating_hours": {
    "opens": "08:00",
    "closes": "23:00"
  }
}
```

| Field                              | Type   | Required | Description                    |
|------------------------------------|--------|----------|--------------------------------|
| `name`                             | string | ✅       | Kitchen display name           |
| `location.latitude`                | number | ✅       | Latitude                       |
| `location.longitude`               | number | ✅       | Longitude                      |
| `capacity.max_concurrent_orders`   | integer| ✅       | Maximum concurrent orders      |
| `capacity.stations`                | array  | ❌       | Available kitchen stations     |
| `operating_hours.opens`            | string | ❌       | Opening time (HH:mm)           |
| `operating_hours.closes`           | string | ❌       | Closing time (HH:mm)           |

**Response `201 Created`:**

```json
{
  "kitchen_id": "k_xyz789",
  "name": "Koramangala Hub",
  "status": "ACTIVE",
  "created_at": "2025-03-04T10:00:00.000Z"
}
```

---

### POST /kitchen/{kitchen_id}/enqueue

Enqueue an order into the kitchen's preparation queue.

**Path Parameters:**

| Parameter    | Type   | Description      |
|--------------|--------|------------------|
| `kitchen_id` | string | Kitchen identifier|

**Request:**

```json
{
  "order_id": "o_9f8e7d",
  "priority": "normal",
  "items": [
    { "menu_item_id": "mi_001", "station": "grill", "prep_time_seconds": 480 }
  ],
  "deadline": "2025-03-04T12:30:00.000Z"
}
```

| Field                      | Type   | Required | Description                           |
|----------------------------|--------|----------|---------------------------------------|
| `order_id`                 | string | ✅       | Order to enqueue                      |
| `priority`                 | string | ❌       | `normal`, `high`, `urgent`            |
| `items`                    | array  | ❌       | Items with station and prep time      |
| `deadline`                 | string | ❌       | ISO 8601 — must be ready by this time |

**Response `200 OK`:**

```json
{
  "order_id": "o_9f8e7d",
  "kitchen_id": "k_xyz789",
  "queue_position": 4,
  "estimated_start": "2025-03-04T12:08:00.000Z",
  "estimated_ready": "2025-03-04T12:22:00.000Z"
}
```

---

### GET /kitchen/{kitchen_id}/next

Dequeue and return the next order to prepare based on priority and FIFO ordering.

**Path Parameters:**

| Parameter    | Type   | Description      |
|--------------|--------|------------------|
| `kitchen_id` | string | Kitchen identifier|

**Query Parameters:**

| Parameter  | Type   | Required | Description               |
|------------|--------|----------|---------------------------|
| `station`  | string | ❌       | Filter by station         |

**Response `200 OK`:**

```json
{
  "order_id": "o_9f8e7d",
  "priority": "normal",
  "items": [
    { "menu_item_id": "mi_001", "station": "grill", "prep_time_seconds": 480 }
  ],
  "assigned_station": "grill",
  "dequeued_at": "2025-03-04T12:10:00.000Z"
}
```

**Response `204 No Content`:** Queue is empty.

---

### GET /kitchen/{kitchen_id}/status

Get current kitchen status including queue depth, load, and station utilization.

**Path Parameters:**

| Parameter    | Type   | Description      |
|--------------|--------|------------------|
| `kitchen_id` | string | Kitchen identifier|

**Response `200 OK`:**

```json
{
  "kitchen_id": "k_xyz789",
  "status": "ACTIVE",
  "queue_depth": 5,
  "active_orders": 12,
  "is_overloaded": false,
  "load_percentage": 60.0,
  "stations": {
    "grill": { "active": 3, "capacity": 5, "utilization": 0.60 },
    "tandoor": { "active": 2, "capacity": 3, "utilization": 0.67 },
    "prep": { "active": 4, "capacity": 6, "utilization": 0.67 },
    "packing": { "active": 3, "capacity": 4, "utilization": 0.75 }
  },
  "suggested_batches": [
    {
      "batch_id": "b_001",
      "orders": ["o_9f8e7d", "o_4k2m1n"],
      "reason": "Same station (grill), similar prep time",
      "estimated_savings_seconds": 180
    }
  ]
}
```

---

### GET /kitchen/{kitchen_id}/batches

Get batch preparation suggestions to optimize kitchen throughput.

**Path Parameters:**

| Parameter    | Type   | Description      |
|--------------|--------|------------------|
| `kitchen_id` | string | Kitchen identifier|

**Query Parameters:**

| Parameter         | Type    | Required | Description                          |
|-------------------|---------|----------|--------------------------------------|
| `strategy`        | string  | ❌       | `station`, `item_type`, `deadline`   |
| `min_batch_size`  | integer | ❌       | Minimum orders per batch (default 2) |
| `max_batch_size`  | integer | ❌       | Maximum orders per batch (default 8) |

**Response `200 OK`:**

```json
{
  "kitchen_id": "k_xyz789",
  "batches": [
    {
      "batch_id": "b_001",
      "orders": ["o_9f8e7d", "o_4k2m1n", "o_7p3q8r"],
      "station": "grill",
      "strategy": "station",
      "estimated_savings_seconds": 240,
      "estimated_ready_at": "2025-03-04T12:22:00.000Z"
    }
  ],
  "total_batches": 1,
  "total_savings_seconds": 240
}
```

---

## Shelf Service

**Base:** `http://localhost:8003`

Manages shelf slot allocation, TTL tracking, and expiry warnings for orders awaiting pickup.

---

### POST /shelf/init

Initialize shelf slots for a kitchen. Must be called before any shelf assignments.

**Request:**

```json
{
  "kitchen_id": "k_xyz789",
  "zones": [
    {
      "zone_id": "A",
      "slots": ["A1", "A2", "A3", "A4", "A5"],
      "temperature": "ambient"
    },
    {
      "zone_id": "B",
      "slots": ["B1", "B2", "B3"],
      "temperature": "warm"
    }
  ],
  "default_ttl_seconds": 900
}
```

| Field                          | Type    | Required | Description                       |
|--------------------------------|---------|----------|-----------------------------------|
| `kitchen_id`                   | string  | ✅       | Kitchen to initialize shelves for |
| `zones`                        | array   | ✅       | Shelf zone definitions            |
| `zones[].zone_id`              | string  | ✅       | Zone identifier                   |
| `zones[].slots`                | array   | ✅       | Slot identifiers in this zone     |
| `zones[].temperature`          | string  | ❌       | `ambient`, `warm`, `cold`         |
| `default_ttl_seconds`          | integer | ❌       | Default TTL (default: 900)        |

**Response `201 Created`:**

```json
{
  "kitchen_id": "k_xyz789",
  "total_slots": 8,
  "zones": [
    { "zone_id": "A", "slot_count": 5, "temperature": "ambient" },
    { "zone_id": "B", "slot_count": 3, "temperature": "warm" }
  ],
  "initialized_at": "2025-03-04T08:00:00.000Z"
}
```

---

### POST /shelf/assign

Assign a shelf slot to an order. Automatically selects the best available slot if none specified.

**Request:**

```json
{
  "order_id": "o_9f8e7d",
  "kitchen_id": "k_xyz789",
  "preferred_zone": "A",
  "preferred_slot": "A1",
  "ttl_seconds": 900
}
```

| Field             | Type    | Required | Description                          |
|-------------------|---------|----------|--------------------------------------|
| `order_id`        | string  | ✅       | Order to place on shelf              |
| `kitchen_id`      | string  | ✅       | Kitchen identifier                   |
| `preferred_zone`  | string  | ❌       | Preferred zone (best-effort)         |
| `preferred_slot`  | string  | ❌       | Specific slot request                |
| `ttl_seconds`     | integer | ❌       | Shelf TTL (default: kitchen default) |

**Response `200 OK`:**

```json
{
  "order_id": "o_9f8e7d",
  "shelf_id": "A1",
  "zone_id": "A",
  "kitchen_id": "k_xyz789",
  "placed_at": "2025-03-04T12:22:30.000Z",
  "expires_at": "2025-03-04T12:37:30.000Z",
  "ttl_seconds": 900
}
```

---

### POST /shelf/{shelf_id}/release

Release a shelf slot, making it available for new orders.

**Path Parameters:**

| Parameter  | Type   | Description     |
|------------|--------|-----------------|
| `shelf_id` | string | Shelf slot ID   |

**Request:**

```json
{
  "reason": "picked_up",
  "order_id": "o_9f8e7d"
}
```

| Field      | Type   | Required | Description                       |
|------------|--------|----------|-----------------------------------|
| `reason`   | string | ❌       | Release reason                    |
| `order_id` | string | ❌       | Associated order                  |

**Response `200 OK`:**

```json
{
  "shelf_id": "A1",
  "status": "AVAILABLE",
  "released_at": "2025-03-04T12:32:00.000Z",
  "previous_order_id": "o_9f8e7d",
  "occupied_duration_seconds": 570
}
```

---

### GET /shelf/{kitchen_id}/status

Get full shelf status for a kitchen including all slots and their occupancy.

**Path Parameters:**

| Parameter    | Type   | Description      |
|--------------|--------|------------------|
| `kitchen_id` | string | Kitchen identifier|

**Query Parameters:**

| Parameter   | Type    | Required | Description                          |
|-------------|---------|----------|--------------------------------------|
| `zone`      | string  | ❌       | Filter by zone                       |
| `status`    | string  | ❌       | Filter: `available`, `occupied`, `expired` |

**Response `200 OK`:**

```json
{
  "kitchen_id": "k_xyz789",
  "total_slots": 8,
  "available_slots": 5,
  "occupied_slots": 3,
  "expired_slots": 0,
  "shelves": [
    {
      "shelf_id": "A1",
      "zone_id": "A",
      "status": "OCCUPIED",
      "order_id": "o_9f8e7d",
      "placed_at": "2025-03-04T12:22:30.000Z",
      "expires_at": "2025-03-04T12:37:30.000Z",
      "remaining_ttl": 480,
      "temperature": "ambient"
    },
    {
      "shelf_id": "A2",
      "zone_id": "A",
      "status": "AVAILABLE",
      "order_id": null,
      "temperature": "ambient"
    }
  ]
}
```

---

### GET /shelf/{kitchen_id}/warnings

Get expiry warnings for orders approaching or past their shelf TTL.

**Path Parameters:**

| Parameter    | Type   | Description      |
|--------------|--------|------------------|
| `kitchen_id` | string | Kitchen identifier|

**Query Parameters:**

| Parameter              | Type    | Required | Description                               |
|------------------------|---------|----------|-------------------------------------------|
| `threshold_seconds`    | integer | ❌       | Warn if TTL < threshold (default: 300)    |
| `include_expired`      | boolean | ❌       | Include already-expired orders (default: true) |

**Response `200 OK`:**

```json
{
  "kitchen_id": "k_xyz789",
  "warning_count": 2,
  "warnings": [
    {
      "order_id": "o_9f8e7d",
      "shelf_id": "A1",
      "remaining_ttl": 120,
      "expires_at": "2025-03-04T12:37:30.000Z",
      "severity": "critical",
      "customer_notified": true
    },
    {
      "order_id": "o_2m4n6p",
      "shelf_id": "B2",
      "remaining_ttl": -60,
      "expires_at": "2025-03-04T12:35:30.000Z",
      "severity": "expired",
      "customer_notified": true
    }
  ]
}
```

---

## ETA Service

**Base:** `http://localhost:8004`

Predicts and tracks estimated time of completion for orders using ML models and real-time kitchen data.

---

### POST /eta/predict

Generate an ETA prediction for an order.

**Request:**

```json
{
  "order_id": "o_9f8e7d",
  "kitchen_id": "k_xyz789",
  "queue_length": 5,
  "item_complexity": 0.7,
  "current_state": "QUEUE_ASSIGNED",
  "order_items": [
    { "menu_item_id": "mi_001", "quantity": 2, "prep_time_seconds": 480 }
  ],
  "historical_avg_prep_seconds": 720
}
```

| Field                          | Type   | Required | Description                           |
|--------------------------------|--------|----------|---------------------------------------|
| `order_id`                     | string | ✅       | Order identifier                      |
| `kitchen_id`                   | string | ✅       | Kitchen identifier                    |
| `queue_length`                 | integer| ❌       | Current queue depth                   |
| `item_complexity`              | number | ❌       | Complexity score (0.0 – 1.0)          |
| `current_state`                | string | ❌       | Current order state                   |
| `order_items`                  | array  | ❌       | Items with prep time estimates        |
| `historical_avg_prep_seconds`  | number | ❌       | Kitchen's average prep time           |

**Response `200 OK`:**

```json
{
  "order_id": "o_9f8e7d",
  "eta_seconds": 1320,
  "eta_timestamp": "2025-03-04T12:22:00.000Z",
  "confidence": 0.85,
  "confidence_interval": {
    "low": 1080,
    "high": 1620
  },
  "risk_level": "low",
  "model_version": "v2.3.1",
  "factors": {
    "queue_wait_seconds": 480,
    "prep_time_seconds": 600,
    "packing_time_seconds": 120,
    "shelf_time_seconds": 120
  }
}
```

---

### GET /eta/{order_id}

Retrieve the latest ETA prediction for an order.

**Path Parameters:**

| Parameter  | Type   | Description     |
|------------|--------|-----------------|
| `order_id` | string | Order identifier|

**Response `200 OK`:**

```json
{
  "order_id": "o_9f8e7d",
  "eta_seconds": 1320,
  "eta_timestamp": "2025-03-04T12:22:00.000Z",
  "confidence": 0.85,
  "confidence_interval": {
    "low": 1080,
    "high": 1620
  },
  "risk_level": "low",
  "last_calculated_at": "2025-03-04T12:05:00.000Z",
  "recalculation_count": 2
}
```

---

### POST /eta/recalculate

Force an ETA recalculation for an order. Typically triggered on state transitions or queue changes.

**Request:**

```json
{
  "order_id": "o_9f8e7d",
  "reason": "state_change",
  "new_state": "IN_PREP",
  "override_factors": {
    "queue_length": 3
  }
}
```

| Field                          | Type   | Required | Description                            |
|--------------------------------|--------|----------|----------------------------------------|
| `order_id`                     | string | ✅       | Order identifier                       |
| `reason`                       | string | ✅       | Trigger for recalculation              |
| `new_state`                    | string | ❌       | Updated order state                    |
| `override_factors`             | object | ❌       | Override specific prediction factors   |
| `override_factors.queue_length`| integer| ❌       | Override queue depth                   |

**Response `200 OK`:**

```json
{
  "order_id": "o_9f8e7d",
  "previous_eta_seconds": 1320,
  "new_eta_seconds": 840,
  "eta_timestamp": "2025-03-04T12:14:00.000Z",
  "confidence": 0.90,
  "confidence_interval": {
    "low": 720,
    "high": 1020
  },
  "risk_level": "low",
  "recalculated_at": "2025-03-04T12:10:00.000Z"
}
```

---

## Notification Service

**Base:** `http://localhost:8005`

Sends push notifications, SMS, and in-app messages to customers and kitchen staff.

---

### POST /notifications/send

Send a notification to a specific user.

**Request:**

```json
{
  "user_id": "u_abc123",
  "channel": "push",
  "template_id": "order_ready",
  "template_data": {
    "order_id": "o_9f8e7d",
    "eta_minutes": 2,
    "shelf_id": "A1"
  },
  "priority": "high"
}
```

| Field            | Type   | Required | Description                                   |
|------------------|--------|----------|-----------------------------------------------|
| `user_id`        | string | ✅       | Recipient user identifier                     |
| `channel`        | string | ✅       | One of `push`, `sms`, `in_app`, `email`       |
| `template_id`    | string | ✅       | Notification template identifier              |
| `template_data`  | object | ❌       | Template variable values                      |
| `priority`       | string | ❌       | One of `low`, `normal`, `high`                |

**Response `200 OK`:**

```json
{
  "notification_id": "notif_abc123",
  "user_id": "u_abc123",
  "channel": "push",
  "status": "sent",
  "sent_at": "2025-03-04T12:22:30.000Z"
}
```

---

### POST /notifications/order-update

Automatically generate and send a notification based on an order state transition. The service determines the right template, channel, and recipient.

**Request:**

```json
{
  "order_id": "o_9f8e7d",
  "previous_state": "PACKING",
  "new_state": "READY",
  "user_id": "u_abc123",
  "additional_data": {
    "shelf_id": "A1",
    "eta_minutes": 2
  }
}
```

| Field              | Type   | Required | Description                          |
|--------------------|--------|----------|--------------------------------------|
| `order_id`         | string | ✅       | Order identifier                     |
| `previous_state`   | string | ✅       | Previous order state                 |
| `new_state`        | string | ✅       | New order state                      |
| `user_id`          | string | ✅       | Customer to notify                   |
| `additional_data`  | object | ❌       | Extra template data                  |

**Response `200 OK`:**

```json
{
  "notification_id": "notif_def456",
  "order_id": "o_9f8e7d",
  "channel": "push",
  "template_id": "order_ready",
  "status": "sent",
  "sent_at": "2025-03-04T12:22:30.000Z"
}
```

---

### GET /notifications/log

Retrieve the notification log. Supports filtering by user, order, channel, and time range.

**Query Parameters:**

| Parameter    | Type    | Required | Description                          |
|--------------|---------|----------|--------------------------------------|
| `user_id`    | string  | ❌       | Filter by recipient                  |
| `order_id`   | string  | ❌       | Filter by associated order           |
| `channel`    | string  | ❌       | Filter by channel                    |
| `status`     | string  | ❌       | Filter: `sent`, `delivered`, `failed`|
| `from`       | string  | ❌       | ISO 8601 — start of range            |
| `to`         | string  | ❌       | ISO 8601 — end of range              |
| `limit`      | integer | ❌       | Max results (default 50, max 500)    |
| `offset`     | integer | ❌       | Pagination offset                    |

**Response `200 OK`:**

```json
{
  "notifications": [
    {
      "notification_id": "notif_abc123",
      "user_id": "u_abc123",
      "order_id": "o_9f8e7d",
      "channel": "push",
      "template_id": "order_ready",
      "status": "delivered",
      "sent_at": "2025-03-04T12:22:30.000Z",
      "delivered_at": "2025-03-04T12:22:31.000Z"
    }
  ],
  "total_count": 1,
  "has_more": false
}
```

---

## Arrival Service

**Base:** `http://localhost:8008`

Detects customer arrival at the kitchen using GPS geofencing or QR code scanning, enabling seamless handoff.

---

### POST /arrival/detect

Detect a user's arrival at a kitchen. Supports GPS-based geofence triggers and QR code scans.

**Request:**

```json
{
  "order_id": "o_9f8e7d",
  "user_id": "u_abc123",
  "kitchen_id": "k_xyz789",
  "detection_method": "GPS",
  "gps": {
    "latitude": 12.9353,
    "longitude": 77.6246,
    "accuracy_meters": 5.0
  },
  "qr_token": null
}
```

| Field                      | Type   | Required | Description                        |
|----------------------------|--------|----------|------------------------------------|
| `order_id`                 | string | ✅       | Order identifier                   |
| `user_id`                  | string | ✅       | Customer identifier                |
| `kitchen_id`               | string | ✅       | Kitchen identifier                 |
| `detection_method`         | string | ✅       | One of `GPS`, `QR`                |
| `gps.latitude`             | number | ❌*      | Required when method is `GPS`      |
| `gps.longitude`            | number | ❌*      | Required when method is `GPS`      |
| `gps.accuracy_meters`      | number | ❌       | GPS accuracy in meters             |
| `qr_token`                 | string | ❌*      | Required when method is `QR`       |

**Response `200 OK`:**

```json
{
  "arrival_id": "arr_abc123",
  "order_id": "o_9f8e7d",
  "user_id": "u_abc123",
  "kitchen_id": "k_xyz789",
  "detection_method": "GPS",
  "confidence": 0.95,
  "distance_meters": 8.5,
  "detected_at": "2025-03-04T12:30:00.000Z",
  "status": "CONFIRMED"
}
```

**Response `200 OK` (low confidence):**

```json
{
  "arrival_id": "arr_abc456",
  "order_id": "o_9f8e7d",
  "user_id": "u_abc123",
  "kitchen_id": "k_xyz789",
  "detection_method": "GPS",
  "confidence": 0.45,
  "distance_meters": 85.0,
  "detected_at": "2025-03-04T12:29:00.000Z",
  "status": "PENDING_CONFIRMATION"
}
```

---

### GET /arrival/{order_id}/status

Get the current arrival status for an order.

**Path Parameters:**

| Parameter  | Type   | Description     |
|------------|--------|-----------------|
| `order_id` | string | Order identifier|

**Response `200 OK`:**

```json
{
  "order_id": "o_9f8e7d",
  "arrival_status": "CONFIRMED",
  "detected_at": "2025-03-04T12:30:00.000Z",
  "detection_method": "GPS",
  "confidence": 0.95,
  "waiting_since": "2025-03-04T12:30:00.000Z",
  "estimated_handoff_seconds": 120
}
```

---

### GET /arrival/{order_id}/qr-token

Generate or retrieve a QR token for a specific order. Used for QR-based arrival detection.

**Path Parameters:**

| Parameter  | Type   | Description     |
|------------|--------|-----------------|
| `order_id` | string | Order identifier|

**Response `200 OK`:**

```json
{
  "order_id": "o_9f8e7d",
  "qr_token": "qr_x9f2k1m3n4",
  "expires_at": "2025-03-04T13:00:00.000Z",
  "scan_url": "https://hotsot.in/qr/qr_x9f2k1m3n4"
}
```

---

### GET /arrival/kitchen/{kitchen_id}/waiting

Get a list of users currently waiting at a kitchen for order handoff.

**Path Parameters:**

| Parameter    | Type   | Description      |
|--------------|--------|------------------|
| `kitchen_id` | string | Kitchen identifier|

**Query Parameters:**

| Parameter   | Type    | Required | Description                       |
|-------------|---------|----------|-----------------------------------|
| `status`    | string  | ❌       | Filter: `PENDING`, `CONFIRMED`    |
| `limit`     | integer | ❌       | Max results (default 20)          |

**Response `200 OK`:**

```json
{
  "kitchen_id": "k_xyz789",
  "waiting_count": 2,
  "waiting_users": [
    {
      "user_id": "u_abc123",
      "order_id": "o_9f8e7d",
      "shelf_id": "A1",
      "arrival_status": "CONFIRMED",
      "waiting_since": "2025-03-04T12:30:00.000Z",
      "wait_duration_seconds": 60,
      "order_status": "ARRIVED"
    },
    {
      "user_id": "u_def456",
      "order_id": "o_5h7j9k",
      "shelf_id": "B2",
      "arrival_status": "PENDING_CONFIRMATION",
      "waiting_since": "2025-03-04T12:28:00.000Z",
      "wait_duration_seconds": 180,
      "order_status": "ON_SHELF"
    }
  ]
}
```

---

## Compensation Service

**Base:** `http://localhost:8010`

Evaluates and processes compensation for delayed, expired, or failed orders. Calculates kitchen penalties for SLA violations.

---

### POST /compensation/evaluate

Evaluate whether compensation is warranted for an order and calculate the amount.

**Request:**

```json
{
  "order_id": "o_9f8e7d",
  "trigger": "delay",
  "delay_minutes": 15,
  "original_eta_seconds": 900,
  "actual_seconds": 1800,
  "customer_tier": "premium"
}
```

| Field                  | Type   | Required | Description                                  |
|------------------------|--------|----------|----------------------------------------------|
| `order_id`             | string | ✅       | Order identifier                             |
| `trigger`              | string | ✅       | One of `delay`, `expiry`, `failure`, `cancellation` |
| `delay_minutes`        | number | ❌       | Delay in minutes (for `delay` trigger)       |
| `original_eta_seconds` | number | ❌       | Original ETA in seconds                      |
| `actual_seconds`       | number | ❌       | Actual time taken in seconds                 |
| `customer_tier`        | string | ❌       | One of `standard`, `premium`, `vip`          |

**Response `200 OK`:**

```json
{
  "evaluation_id": "comp_abc123",
  "order_id": "o_9f8e7d",
  "eligible": true,
  "compensation_type": "partial_refund",
  "amount": 59.60,
  "currency": "INR",
  "percentage": 20.0,
  "reason": "Delay exceeded 10-minute threshold for premium customer",
  "auto_apply": true,
  "kitchen_penalty": {
    "penalty_amount": 50.00,
    "penalty_type": "sla_violation",
    "points": 3
  },
  "evaluated_at": "2025-03-04T12:45:00.000Z"
}
```

---

### GET /compensation/{order_id}

Get the compensation status for a specific order.

**Path Parameters:**

| Parameter  | Type   | Description     |
|------------|--------|-----------------|
| `order_id` | string | Order identifier|

**Response `200 OK`:**

```json
{
  "order_id": "o_9f8e7d",
  "compensations": [
    {
      "compensation_id": "comp_abc123",
      "type": "partial_refund",
      "amount": 59.60,
      "currency": "INR",
      "status": "APPLIED",
      "applied_at": "2025-03-04T12:46:00.000Z",
      "refund_ref": "ref_comp_001"
    }
  ],
  "total_compensation_amount": 59.60
}
```

---

### GET /compensation/kitchen/{kitchen_id}/penalty

Get accumulated penalty information for a kitchen over a time period.

**Path Parameters:**

| Parameter    | Type   | Description      |
|--------------|--------|------------------|
| `kitchen_id` | string | Kitchen identifier|

**Query Parameters:**

| Parameter | Type   | Required | Description                       |
|-----------|--------|----------|-----------------------------------|
| `period`  | string | ❌       | `day`, `week`, `month` (default: `day`) |
| `from`    | string | ❌       | ISO 8601 — start of range         |
| `to`      | string | ❌       | ISO 8601 — end of range           |

**Response `200 OK`:**

```json
{
  "kitchen_id": "k_xyz789",
  "period": "day",
  "penalty_summary": {
    "total_penalties": 5,
    "total_penalty_amount": 350.00,
    "total_penalty_points": 15,
    "sla_violations": 3,
    "expiry_incidents": 2
  },
  "penalties": [
    {
      "penalty_id": "pen_001",
      "order_id": "o_9f8e7d",
      "type": "sla_violation",
      "amount": 50.00,
      "points": 3,
      "reason": "Delay exceeded 10-minute SLA threshold",
      "created_at": "2025-03-04T12:45:00.000Z"
    }
  ]
}
```

---

### POST /compensation/bulk-evaluate

Bulk evaluate compensation for multiple orders. Useful for batch processing of SLA violations.

**Request:**

```json
{
  "order_ids": ["o_9f8e7d", "o_5h7j9k", "o_2m4n6p"],
  "trigger": "delay",
  "threshold_minutes": 10
}
```

| Field               | Type    | Required | Description                              |
|---------------------|---------|----------|------------------------------------------|
| `order_ids`         | array   | ✅       | List of order identifiers (max 100)      |
| `trigger`           | string  | ✅       | Compensation trigger type                |
| `threshold_minutes` | integer | ❌       | Minimum delay to trigger evaluation      |

**Response `200 OK`:**

```json
{
  "bulk_id": "bulk_abc123",
  "total_evaluated": 3,
  "eligible_count": 2,
  "total_compensation_amount": 109.60,
  "results": [
    {
      "order_id": "o_9f8e7d",
      "eligible": true,
      "compensation_amount": 59.60,
      "auto_applied": true
    },
    {
      "order_id": "o_5h7j9k",
      "eligible": true,
      "compensation_amount": 50.00,
      "auto_applied": true
    },
    {
      "order_id": "o_2m4n6p",
      "eligible": false,
      "compensation_amount": 0,
      "reason": "Delay below threshold"
    }
  ],
  "evaluated_at": "2025-03-04T13:00:00.000Z"
}
```

---

## ML Service

**Base:** `http://localhost:8007`

Manages ML model training, feature storage, and feedback loops for continuous ETA and demand prediction improvement.

---

### POST /ml/train

Trigger a model training run.

**Request:**

```json
{
  "model_type": "eta_prediction",
  "training_config": {
    "algorithm": "xgboost",
    "hyperparameters": {
      "max_depth": 8,
      "learning_rate": 0.1,
      "n_estimators": 200
    }
  },
  "data_range": {
    "from": "2025-02-01T00:00:00.000Z",
    "to": "2025-03-01T00:00:00.000Z"
  },
  "kitchen_id": "k_xyz789"
}
```

| Field                                | Type   | Required | Description                                |
|--------------------------------------|--------|----------|--------------------------------------------|
| `model_type`                         | string | ✅       | One of `eta_prediction`, `demand_forecast` |
| `training_config.algorithm`          | string | ❌       | ML algorithm (default: `xgboost`)          |
| `training_config.hyperparameters`    | object | ❌       | Algorithm-specific parameters              |
| `data_range.from`                    | string | ❌       | Training data start date                   |
| `data_range.to`                      | string | ❌       | Training data end date                     |
| `kitchen_id`                         | string | ❌       | Scope to specific kitchen (null = global)  |

**Response `202 Accepted`:**

```json
{
  "training_id": "train_abc123",
  "model_type": "eta_prediction",
  "status": "QUEUED",
  "queued_at": "2025-03-04T14:00:00.000Z",
  "estimated_completion": "2025-03-04T14:30:00.000Z"
}
```

---

### POST /ml/features/store

Store feature vectors for model training and inference.

**Request:**

```json
{
  "order_id": "o_9f8e7d",
  "features": {
    "kitchen_id": "k_xyz789",
    "queue_length": 5,
    "item_count": 2,
    "item_complexity": 0.7,
    "hour_of_day": 12,
    "day_of_week": 2,
    "is_weekend": false,
    "kitchen_load": 0.6,
    "historical_avg_prep": 720,
    "customer_tier": "premium"
  },
  "label": {
    "actual_prep_seconds": 680,
    "actual_total_seconds": 1920
  }
}
```

| Field       | Type   | Required | Description                        |
|-------------|--------|----------|------------------------------------|
| `order_id`  | string | ✅       | Order identifier                   |
| `features`  | object | ✅       | Feature key-value pairs            |
| `label`     | object | ❌       | Ground truth labels for training   |

**Response `201 Created`:**

```json
{
  "feature_id": "feat_abc123",
  "order_id": "o_9f8e7d",
  "stored_at": "2025-03-04T12:32:00.000Z"
}
```

---

### GET /ml/features/{order_id}

Retrieve stored feature vectors for an order.

**Path Parameters:**

| Parameter  | Type   | Description     |
|------------|--------|-----------------|
| `order_id` | string | Order identifier|

**Response `200 OK`:**

```json
{
  "order_id": "o_9f8e7d",
  "features": {
    "kitchen_id": "k_xyz789",
    "queue_length": 5,
    "item_count": 2,
    "item_complexity": 0.7,
    "hour_of_day": 12,
    "day_of_week": 2,
    "is_weekend": false,
    "kitchen_load": 0.6,
    "historical_avg_prep": 720,
    "customer_tier": "premium"
  },
  "label": {
    "actual_prep_seconds": 680,
    "actual_total_seconds": 1920
  },
  "stored_at": "2025-03-04T12:32:00.000Z"
}
```

---

### POST /ml/feedback

Submit feedback on prediction accuracy for model monitoring and improvement.

**Request:**

```json
{
  "order_id": "o_9f8e7d",
  "predicted_eta_seconds": 1320,
  "actual_eta_seconds": 1920,
  "error_seconds": 600,
  "model_version": "v2.3.1",
  "feedback_type": "prediction_error",
  "notes": "Kitchen was unexpectedly overloaded due to lunch rush"
}
```

| Field                   | Type   | Required | Description                           |
|-------------------------|--------|----------|---------------------------------------|
| `order_id`              | string | ✅       | Order identifier                      |
| `predicted_eta_seconds` | number | ✅       | Model's predicted ETA                 |
| `actual_eta_seconds`    | number | ✅       | Actual time taken                     |
| `error_seconds`         | number | ❌       | Prediction error (auto-calculated)    |
| `model_version`         | string | ❌       | Model version used for prediction     |
| `feedback_type`         | string | ❌       | One of `prediction_error`, `good`, `anomaly` |
| `notes`                 | string | ❌       | Free-text feedback                    |

**Response `201 Created`:**

```json
{
  "feedback_id": "fb_abc123",
  "order_id": "o_9f8e7d",
  "error_percentage": 45.5,
  "severity": "high",
  "stored_at": "2025-03-04T12:45:00.000Z"
}
```

---

### GET /ml/status

Get the current training status and model information.

**Query Parameters:**

| Parameter     | Type   | Required | Description                         |
|---------------|--------|----------|-------------------------------------|
| `training_id` | string | ❌       | Specific training run to check      |
| `model_type`  | string | ❌       | Filter by model type                |

**Response `200 OK`:**

```json
{
  "active_models": [
    {
      "model_type": "eta_prediction",
      "version": "v2.3.1",
      "status": "ACTIVE",
      "trained_at": "2025-03-03T02:00:00.000Z",
      "metrics": {
        "mae": 180.5,
        "rmse": 240.3,
        "mape": 12.4
      },
      "training_samples": 54200
    }
  ],
  "current_training": [
    {
      "training_id": "train_abc123",
      "model_type": "eta_prediction",
      "status": "RUNNING",
      "progress": 0.65,
      "started_at": "2025-03-04T14:05:00.000Z",
      "estimated_completion": "2025-03-04T14:30:00.000Z"
    }
  ]
}
```

---

## WebSocket

Real-time event streams for order tracking and kitchen dashboards.

### Connection

All WebSocket connections require the JWT token as a query parameter:

```
ws://localhost:8001/ws/order/{order_id}?token=<jwt_token>
```

### Message Format

All WebSocket messages follow a consistent envelope:

```json
{
  "type": "STATE_CHANGE",
  "payload": { ... },
  "timestamp": "2025-03-04T12:22:30.000Z",
  "sequence": 42
}
```

---

### WS /ws/order/{order_id}

Subscribe to real-time updates for a specific order.

**Path Parameters:**

| Parameter  | Type   | Description     |
|------------|--------|-----------------|
| `order_id` | string | Order identifier|

**Event Types:**

| Type                | Payload                                              | Description                     |
|---------------------|------------------------------------------------------|---------------------------------|
| `STATE_CHANGE`      | `{ order_id, previous_state, new_state }`            | Order state transition          |
| `ETA_UPDATE`        | `{ order_id, eta_seconds, confidence }`              | ETA recalculation               |
| `SHELF_ASSIGNED`    | `{ order_id, shelf_id, expires_at }`                 | Shelf slot assigned             |
| `ARRIVAL_DETECTED`  | `{ order_id, detection_method }`                     | Customer arrival detected       |
| `COMPENSATION`      | `{ order_id, amount, type }`                         | Compensation applied            |
| `NOTIFICATION`      | `{ order_id, channel, template_id }`                 | Notification sent               |

**Example:**

```json
{
  "type": "STATE_CHANGE",
  "payload": {
    "order_id": "o_9f8e7d",
    "previous_state": "PACKING",
    "new_state": "READY"
  },
  "timestamp": "2025-03-04T12:22:00.000Z",
  "sequence": 7
}
```

---

### WS /ws/kitchen/{kitchen_id}

Subscribe to real-time updates for a kitchen dashboard. Used by kitchen staff to monitor queue, orders, and arrivals.

**Path Parameters:**

| Parameter    | Type   | Description      |
|--------------|--------|------------------|
| `kitchen_id` | string | Kitchen identifier|

**Event Types:**

| Type                    | Payload                                                      | Description                    |
|-------------------------|--------------------------------------------------------------|--------------------------------|
| `ORDER_ENQUEUED`        | `{ order_id, queue_position, priority }`                     | New order added to queue       |
| `ORDER_DEQUEUED`        | `{ order_id, assigned_station }`                             | Order removed from queue       |
| `ORDER_STATE_CHANGE`    | `{ order_id, previous_state, new_state }`                    | Order state transition         |
| `ARRIVAL`               | `{ user_id, order_id, detection_method, confidence }`        | Customer arrival detected      |
| `SHELF_WARNING`         | `{ order_id, shelf_id, remaining_ttl, severity }`            | Shelf expiry warning           |
| `BATCH_SUGGESTION`      | `{ batch_id, orders, estimated_savings_seconds }`            | Batch preparation suggestion   |
| `LOAD_UPDATE`           | `{ queue_depth, load_percentage, is_overloaded }`            | Kitchen load update            |

**Example:**

```json
{
  "type": "SHELF_WARNING",
  "payload": {
    "order_id": "o_9f8e7d",
    "shelf_id": "A1",
    "remaining_ttl": 120,
    "severity": "critical"
  },
  "timestamp": "2025-03-04T12:35:30.000Z",
  "sequence": 15
}
```

---

## Appendix: V1 → V2 Migration Summary

| Aspect                   | V1                      | V2                                        |
|--------------------------|-------------------------|-------------------------------------------|
| Order states             | 6                       | 16                                        |
| Payment flow             | Single `/pay`           | `/pay` → `/confirm-payment` (2-step)      |
| Kitchen queue            | Implicit                | Explicit `/assign-queue` + `/start-prep`  |
| Packing                  | Not tracked             | Dedicated `PACKING` state                 |
| Arrival detection        | Not supported           | `ARRIVED` state + Arrival Service          |
| Handoff                  | Not tracked             | `HANDOFF_IN_PROGRESS` state               |
| Expiry                   | Not tracked             | `EXPIRED` state + Shelf TTL               |
| Refund                   | Not supported           | `REFUNDED` state + Compensation Service    |
| Cancellation             | Not supported           | `CANCELLED` state                         |
| Failure                  | Not supported           | `FAILED` state                            |
| Event sourcing           | Partial                 | Full event log on all transitions          |
| ETA                      | Basic prediction        | ML-powered with confidence intervals      |
| Notifications            | Not supported           | Template-based, multi-channel              |
| Compensation             | Not supported           | Evaluation + kitchen penalties             |
| ML integration           | Not supported           | Training, features, feedback loop          |
| Services                 | 4 (Order, ETA, Kitchen, Shelf) | 8 (all above + Arrival, Notification, Compensation, ML) |
