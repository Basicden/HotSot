-- HotSot Database Schema V2 (PostgreSQL)
-- Source of Truth Layer — 16-state order lifecycle

-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ─── KITCHENS ───
CREATE TABLE kitchens (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name TEXT NOT NULL,
    location JSONB,
    capacity INT DEFAULT 20,
    current_load INT DEFAULT 0,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- ─── ORDERS (V2 — 16 states) ───
-- V2 Flow: CREATED → PAYMENT_PENDING → PAYMENT_CONFIRMED → SLOT_RESERVED →
--          QUEUE_ASSIGNED → IN_PREP → PACKING → READY → ON_SHELF →
--          ARRIVED → HANDOFF_IN_PROGRESS → PICKED
-- Terminal: PICKED, EXPIRED, REFUNDED, CANCELLED, FAILED
CREATE TABLE orders (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL,
    kitchen_id UUID NOT NULL REFERENCES kitchens(id),
    status TEXT NOT NULL DEFAULT 'CREATED',
    queue_type TEXT DEFAULT 'NORMAL',           -- IMMEDIATE, NORMAL, BATCH
    priority_score FLOAT DEFAULT 0.0,
    batch_id UUID,                              -- assigned batch for kitchen batching
    user_tier TEXT DEFAULT 'FREE',              -- FREE, PLUS, PRO, VIP
    eta_seconds INT,
    eta_confidence FLOAT,
    eta_risk TEXT DEFAULT 'LOW',
    items JSONB DEFAULT '[]',
    total_amount DECIMAL(10, 2),
    payment_method TEXT DEFAULT 'UPI',
    payment_ref TEXT,
    shelf_id TEXT,
    shelf_zone TEXT,                            -- HOT, COLD, AMBIENT
    shelf_ttl_remaining INT,                    -- seconds remaining on shelf
    slot_time TIMESTAMP WITH TIME ZONE,
    arrived_at TIMESTAMP WITH TIME ZONE,
    handoff_method TEXT,                        -- QR_SCAN, MANUAL_ACK
    picked_at TIMESTAMP WITH TIME ZONE,
    prep_started_at TIMESTAMP WITH TIME ZONE,
    ready_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_orders_kitchen ON orders(kitchen_id);
CREATE INDEX idx_orders_status ON orders(status);
CREATE INDEX idx_orders_user ON orders(user_id);
CREATE INDEX idx_orders_created ON orders(created_at);
CREATE INDEX idx_orders_queue_type ON orders(queue_type);
CREATE INDEX idx_orders_batch ON orders(batch_id);

-- ─── ORDER EVENTS (Event Sourcing) ───
CREATE TABLE order_events (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    order_id UUID NOT NULL REFERENCES orders(id),
    event_type TEXT NOT NULL,
    payload JSONB DEFAULT '{}',
    idempotency_key TEXT,
    source TEXT,
    schema_version INT DEFAULT 2,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_events_order ON order_events(order_id);
CREATE INDEX idx_events_type ON order_events(event_type);
CREATE INDEX idx_events_created ON order_events(created_at);
CREATE INDEX idx_events_idempotency ON order_events(idempotency_key);

-- ─── SHELF SLOTS ───
CREATE TABLE shelf_slots (
    id TEXT PRIMARY KEY,
    kitchen_id UUID NOT NULL REFERENCES kitchens(id),
    current_order_id UUID REFERENCES orders(id),
    status TEXT DEFAULT 'AVAILABLE',            -- AVAILABLE, OCCUPIED, EXPIRED
    zone TEXT DEFAULT 'HOT',                    -- HOT, COLD, AMBIENT
    ttl_seconds INT DEFAULT 600,
    assigned_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_shelf_kitchen ON shelf_slots(kitchen_id);
CREATE INDEX idx_shelf_status ON shelf_slots(status);

-- ─── KITCHEN SLOTS ───
CREATE TABLE kitchen_slots (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    kitchen_id UUID NOT NULL REFERENCES kitchens(id),
    slot_time TIMESTAMP WITH TIME ZONE NOT NULL,
    capacity INT DEFAULT 10,
    booked INT DEFAULT 0,
    is_available BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_slot_kitchen ON kitchen_slots(kitchen_id);
CREATE INDEX idx_slot_time ON kitchen_slots(slot_time);

-- ─── ETA PREDICTIONS (ML Audit) ───
CREATE TABLE eta_predictions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    order_id UUID NOT NULL REFERENCES orders(id),
    predicted_seconds INT NOT NULL,
    confidence_interval JSONB,
    risk_level TEXT DEFAULT 'LOW',
    delay_probability FLOAT DEFAULT 0.0,
    features JSONB DEFAULT '{}',
    model_version TEXT DEFAULT 'v2',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_eta_order ON eta_predictions(order_id);

-- ─── USERS ───
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    phone TEXT UNIQUE NOT NULL,
    name TEXT,
    is_premium BOOLEAN DEFAULT FALSE,
    user_tier TEXT DEFAULT 'FREE',              -- FREE, PLUS, PRO, VIP
    repeat_order_count INT DEFAULT 0,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- ─── ARRIVALS (V2) ───
CREATE TABLE arrivals (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    order_id UUID NOT NULL REFERENCES orders(id),
    user_id UUID NOT NULL,
    kitchen_id UUID NOT NULL REFERENCES kitchens(id),
    strength TEXT NOT NULL DEFAULT 'SOFT',      -- HARD (GPS <=150m or QR), SOFT (GPS 150-500m)
    arrival_type TEXT NOT NULL DEFAULT 'GPS',   -- GPS, QR_SCAN
    distance_m FLOAT,                           -- distance from kitchen in meters
    latitude FLOAT,
    longitude FLOAT,
    qr_token TEXT,
    priority_boosted BOOLEAN DEFAULT FALSE,
    boost_amount INT DEFAULT 0,
    dedup_hash TEXT,                            -- hash for 30s dedup window
    detected_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_arrivals_order ON arrivals(order_id);
CREATE INDEX idx_arrivals_kitchen ON arrivals(kitchen_id);
CREATE INDEX idx_arrivals_user ON arrivals(user_id);
CREATE INDEX idx_arrivals_dedup ON arrivals(dedup_hash);
CREATE INDEX idx_arrivals_detected ON arrivals(detected_at);

-- ─── COMPENSATIONS (V2) ───
CREATE TABLE compensations (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    compensation_id TEXT UNIQUE NOT NULL,       -- COMP_XXXXXXXXXX
    order_id UUID NOT NULL REFERENCES orders(id),
    kitchen_id UUID NOT NULL REFERENCES kitchens(id),
    reason TEXT NOT NULL,                       -- SHELF_EXPIRED, KITCHEN_FAILURE, PAYMENT_CONFLICT, DELAY_EXCEEDED, WRONG_ORDER_DELIVERED, CUSTOMER_COMPLAINT
    refund_type TEXT NOT NULL,                  -- FULL_REFUND, PARTIAL_REFUND, CREDIT_NOTE, RE_MAKE_ORDER
    amount DECIMAL(10, 2) NOT NULL DEFAULT 0.0,
    status TEXT NOT NULL DEFAULT 'PENDING',     -- PENDING, PROCESSING, COMPLETED, FAILED, DUPLICATE_SKIPPED
    payment_ref TEXT,
    upi_refund_ref TEXT,
    is_duplicate BOOLEAN DEFAULT FALSE,
    delay_minutes INT,                          -- for DELAY_EXCEEDED
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_compensations_order ON compensations(order_id);
CREATE INDEX idx_compensations_kitchen ON compensations(kitchen_id);
CREATE INDEX idx_compensations_reason ON compensations(reason);
CREATE INDEX idx_compensations_status ON compensations(status);
CREATE INDEX idx_compensations_created ON compensations(created_at);

-- ─── KITCHEN PENALTIES (V2) ───
CREATE TABLE kitchen_penalties (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    kitchen_id UUID NOT NULL REFERENCES kitchens(id),
    date DATE NOT NULL,
    shelf_expiration_count INT DEFAULT 0,
    threshold INT DEFAULT 3,
    flagged BOOLEAN DEFAULT FALSE,
    reviewed BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE(kitchen_id, date)
);

CREATE INDEX idx_penalties_kitchen ON kitchen_penalties(kitchen_id);
CREATE INDEX idx_penalties_date ON kitchen_penalties(date);

-- ─── VALID ORDER STATE TRANSITIONS (V2 — 16 states) ───
-- CREATED → PAYMENT_PENDING → PAYMENT_CONFIRMED → SLOT_RESERVED →
-- QUEUE_ASSIGNED → IN_PREP → PACKING → READY → ON_SHELF →
-- ARRIVED → HANDOFF_IN_PROGRESS → PICKED
-- Terminal: PICKED, EXPIRED → REFUNDED, CANCELLED, FAILED
-- This is enforced in application logic (state_machine.py)
