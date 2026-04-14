# HotSot — India-First Pickup Commerce Operating System

> Real-time distributed physical-world scheduling engine optimized for Indian kitchen + user behavior

## What is HotSot?

HotSot is a pickup-first commerce OS that guarantees **±2 minute pickup certainty** under real kitchen constraints. It is NOT a food delivery app — it is a **physical commerce operating system** with ML intelligence, real-time coordination, kitchen OS, shelf-based fulfillment, and economic optimization.

## Architecture

```
User App → API Gateway (K8s) → Order Service (sharded) → Kafka Event Bus
→ ML ETA Engine → Kitchen OS (human-centric) → Shelf System (physical lock)
→ Realtime WebSocket / SMS fallback → Pickup → Economics + Learning Loop
```

## Tech Stack

| Layer | Technology |
|-------|-----------|
| API | FastAPI (Python 3.11) |
| Database | PostgreSQL (source of truth) |
| Cache | Redis (hot state layer) |
| Event Bus | Apache Kafka |
| Realtime | WebSockets + SMS fallback |
| ML | LightGBM → Deep Temporal → RL |
| Infra | AWS EKS (ap-south-1) |
| CI/CD | GitHub Actions → ECR → Helm |
| Observability | CloudWatch + OpenTelemetry |

## Quick Start

```bash
# Clone
git clone https://github.com/your-org/hotsot.git
cd hotsot

# Start with Docker Compose (local dev)
docker-compose up --build

# Or run individual services
cd services/order-service
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8001
```

## Services

| Service | Port | Description |
|---------|------|-------------|
| api-gateway | 8000 | Request routing + auth |
| order-service | 8001 | Order lifecycle management |
| kitchen-service | 8002 | Kitchen OS + scheduling |
| shelf-service | 8003 | Physical shelf allocation |
| eta-service | 8004 | ML-based ETA prediction |
| notification-service | 8005 | Multi-channel notifications |
| realtime-service | 8006 | WebSocket layer |
| ml-service | 8007 | Model training + inference |

## India-Specific Design

- **UPI-first** payments (no COD in pickup model)
- **Offline-safe** order creation for Tier-2/3 networks
- **Festival load** multiplier in ETA engine
- **Human-first** kitchen OS for small staff kitchens
- **SMS fallback** for low-bandwidth scenarios
- **Single region** (ap-south-1) for latency-critical operations

## License

Proprietary — HotSot Systems Pvt. Ltd.
