# HotSot — Architecture Document

## High-Level Architecture

```
User App → API Gateway (FastAPI) → Order Service (Postgres + Redis)
  → Kafka Event Bus → ML ETA Service → Kitchen OS Engine
  → Shelf Allocation Service → WebSocket Realtime Layer
  → Staff Dashboard → Pickup Completion → Economics Engine
```

## Microservice Map

| Service | Port | Responsibility |
|---------|------|----------------|
| api-gateway | 8000 | Request routing, auth, rate limiting |
| order-service | 8001 | Order lifecycle, state machine, event sourcing |
| kitchen-service | 8002 | Priority scheduling, batching, load management |
| shelf-service | 8003 | Physical shelf allocation, TTL, distributed locking |
| eta-service | 8004 | ML ETA prediction with LightGBM + fallback |
| notification-service | 8005 | Multi-channel notifications (WS/Push/SMS) |
| realtime-service | 8006 | WebSocket layer for live updates |
| ml-service | 8007 | Model training, feature store, feedback loop |

## Data Flow

### Happy Path
1. User creates order → Order Service → PostgreSQL + Redis + Kafka
2. Payment confirmed → Order Service → Kitchen queue update
3. Kitchen processes → Kitchen Engine (priority scoring) → ETA computed
4. Order ready → Shelf Service allocates slot (Redis SETNX lock)
5. User picks up → Order Service closes → Kitchen load decremented

### Failure Path
- Kafka lag → Redis fallback cache (eventual consistency mode)
- DB slow → Read from Redis projection layer
- Kitchen failure → Throttle new orders, reroute if possible
- Payment delay → Idempotent API retry with dedup key
- Shelf full → Redirect user to "wait zone"

## Infrastructure (AWS ap-south-1)

- EKS (Kubernetes) → All microservices
- RDS PostgreSQL → Orders + Events (source of truth)
- ElastiCache Redis → Hot state + distributed locks
- MSK Kafka → Event backbone
- S3 → ML datasets + logs + backups
- CloudWatch → Observability + alerting
- ALB + Route53 → Traffic routing

## Observability

- Metrics: p95 latency, kitchen throughput, ETA error rate, shelf expiry %
- Tracing: Distributed trace per order across services
- Logging: Structured JSON logs aligned with Kafka events
- Alerting: PagerDuty for ETA error > 20% or kitchen failure > 5%
