-- ═══════════════════════════════════════════════════════════════
-- HotSot V2 — Database-per-Service Initialization
-- Creates separate databases for each of 18 microservices
-- Enables Row-Level Security (RLS) for multi-tenancy
-- ═══════════════════════════════════════════════════════════════

-- Core service databases
CREATE DATABASE hotsot_order;
CREATE DATABASE hotsot_kitchen;
CREATE DATABASE hotsot_shelf;
CREATE DATABASE hotsot_eta;
CREATE DATABASE hotsot_notification;
CREATE DATABASE hotsot_arrival;
CREATE DATABASE hotsot_compensation;

-- New V2 service databases
CREATE DATABASE hotsot_vendor;
CREATE DATABASE hotsot_menu;
CREATE DATABASE hotsot_search;
CREATE DATABASE hotsot_pricing;
CREATE DATABASE hotsot_compliance;
CREATE DATABASE hotsot_billing;
CREATE DATABASE hotsot_analytics;
CREATE DATABASE hotsot_config;

-- Grant permissions
GRANT ALL PRIVILEGES ON DATABASE hotsot_order TO hotsot;
GRANT ALL PRIVILEGES ON DATABASE hotsot_kitchen TO hotsot;
GRANT ALL PRIVILEGES ON DATABASE hotsot_shelf TO hotsot;
GRANT ALL PRIVILEGES ON DATABASE hotsot_eta TO hotsot;
GRANT ALL PRIVILEGES ON DATABASE hotsot_notification TO hotsot;
GRANT ALL PRIVILEGES ON DATABASE hotsot_arrival TO hotsot;
GRANT ALL PRIVILEGES ON DATABASE hotsot_compensation TO hotsot;
GRANT ALL PRIVILEGES ON DATABASE hotsot_vendor TO hotsot;
GRANT ALL PRIVILEGES ON DATABASE hotsot_menu TO hotsot;
GRANT ALL PRIVILEGES ON DATABASE hotsot_search TO hotsot;
GRANT ALL PRIVILEGES ON DATABASE hotsot_pricing TO hotsot;
GRANT ALL PRIVILEGES ON DATABASE hotsot_compliance TO hotsot;
GRANT ALL PRIVILEGES ON DATABASE hotsot_billing TO hotsot;
GRANT ALL PRIVILEGES ON DATABASE hotsot_analytics TO hotsot;
GRANT ALL PRIVILEGES ON DATABASE hotsot_config TO hotsot;

-- Enable RLS extension on each database (run as superuser)
-- Note: RLS policies will be created by each service on startup
-- via shared.utils.database init_service_db()

-- ═══════════════════════════════════════════════════════════════
-- Kafka Topics (created by Kafka on first publish)
-- ═══════════════════════════════════════════════════════════════
-- hotsot.order.events.v1      (12 partitions, key: order_id)
-- hotsot.kitchen.events.v1    (12 partitions, key: kitchen_id)
-- hotsot.slot.events.v1       (6 partitions, key: order_id)
-- hotsot.priority.events.v1   (6 partitions, key: order_id)
-- hotsot.shelf.events.v1      (6 partitions, key: kitchen_id)
-- hotsot.arrival.events.v1    (6 partitions, key: order_id)
-- hotsot.notification.events.v1 (6 partitions, key: user_id)
-- hotsot.compensation.events.v1 (6 partitions, key: order_id)
-- hotsot.incident.events.v1   (6 partitions, key: kitchen_id)
-- hotsot.payment.events.v1    (6 partitions, key: order_id)
-- hotsot.dlq.order.events.v1  (3 partitions)
-- hotsot.dlq.kitchen.events.v1 (3 partitions)
