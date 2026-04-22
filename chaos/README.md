# HotSot — Chaos Engineering Infrastructure

Production-grade chaos testing framework for HotSot's 18 microservices.

## Architecture

```
k6 (load) → API Gateway → 18 Services → PostgreSQL / Redis / Kafka / ES
                     ↓
            Chaos Mesh injects failures
                     ↓
Prometheus collects metrics → Grafana dashboards
                     ↓
Jaeger traces root cause
```

## Quick Start

### 1. Start the full stack
```bash
docker compose up -d
```

### 2. Run a chaos experiment
```bash
./chaos/scripts/run-experiment.sh pod-kill
```

### 3. View results
- **Grafana:** http://localhost:3000 (admin/admin)
- **Prometheus:** http://localhost:9090
- **Jaeger:** http://localhost:16686

### 4. Validate criteria
```bash
python3 chaos/scripts/validate-criteria.py --results-dir chaos/experiments/results/latest
```

## Available Experiments

| Experiment | What it Tests | File |
|------------|---------------|------|
| `pod-kill` | Service auto-recovery | `chaos-mesh/pod-kill.yaml` |
| `network-latency` | Timeout handling, circuit breaker | `chaos-mesh/network-latency.yaml` |
| `db-failure` | Database resilience | `chaos-mesh/db-failure.yaml` |
| `redis-failure` | Cache + lock fallback | `chaos-mesh/redis-failure.yaml` |
| `kafka-failure` | Event fallback logging | `chaos-mesh/kafka-failure.yaml` |
| `network-partition` | Service isolation | `chaos-mesh/network-partition.yaml` |
| `event-storm` | Kafka pipeline under 10x load | `k6/event-storm.js` |
| `spike` | Sudden traffic burst | `k6/spike.js` |
| `stress` | Find breaking point | `k6/stress.js` |
| `full-suite` | All experiments sequentially | — |

## k6 Load Tests

| Script | Purpose | VUs | Duration |
|--------|---------|-----|----------|
| `baseline.js` | Healthy metrics baseline | 50 | 3m |
| `spike.js` | 10x traffic burst | 500 | 3m |
| `stress.js` | Find breaking point | 3000 | 15m |
| `event-storm.js` | Kafka pipeline stress | 1000 | 4m |

Run individually:
```bash
k6 run -e BASE_URL=http://localhost:8000 chaos/k6/baseline.js
```

## Success Criteria

| Metric | Normal | During Chaos |
|--------|--------|--------------|
| Error rate | < 1% | < 15% |
| p95 latency | < 500ms | < 3s |
| Recovery time | — | < 30s |
| DLQ growth | 0 | Expected |
| Data corruption | None | None |

## Experiment Loop (Scientific Method)

Every chaos experiment follows:

1. **Hypothesis** → "If X fails, system should Y"
2. **Baseline** → Record healthy metrics
3. **Inject** → Apply chaos
4. **Measure** → Collect metrics during chaos
5. **Analyze** → Compare before vs after
6. **Improve** → Fix and re-run

## Directory Structure

```
chaos/
├── k6/                        # k6 load test scripts
│   ├── baseline.js            # Normal traffic baseline
│   ├── spike.js               # Sudden traffic burst
│   ├── stress.js              # Find breaking point
│   └── event-storm.js         # Kafka event flood
├── chaos-mesh/                # Chaos Mesh YAML configs
│   ├── pod-kill.yaml          # Service crash simulation
│   ├── network-latency.yaml   # Network delay injection
│   ├── db-failure.yaml        # Database outage
│   ├── redis-failure.yaml     # Cache outage
│   ├── kafka-failure.yaml     # Message broker outage
│   └── network-partition.yaml # Split brain
├── prometheus/                # Prometheus configuration
│   ├── prometheus.yml         # Scrape targets
│   └── alerts/                # Alert rules
│       └── hotsot.yml         # HotSot-specific alerts
├── grafana/                   # Grafana dashboards
│   └── dashboards/
│       └── hotsot-chaos.json  # 5-panel chaos dashboard
├── scripts/                   # Automation scripts
│   ├── run-experiment.sh      # Experiment runner
│   └── validate-criteria.py   # Success criteria validator
└── experiments/               # Experiment results (gitignored)
    └── results/
```

## Docker Compose Additions

The `docker-compose.yml` includes:
- **Prometheus** (port 9090) — Metrics collection
- **Grafana** (port 3000) — Visualization dashboard
- **Jaeger** (port 16686) — Distributed tracing

## Prerequisites

- Docker + Docker Compose
- k6 (`brew install k6` or `https://k6.io`)
- kubectl + Chaos Mesh (for K8s chaos injection)
