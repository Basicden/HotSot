#!/usr/bin/env bash
#
# HotSot — Chaos Experiment Runner
#
# Scientific method for chaos engineering:
#   1. Hypothesis  → "If X fails, system should Y"
#   2. Baseline    → Record healthy metrics
#   3. Inject      → Apply chaos
#   4. Measure     → Collect metrics during chaos
#   5. Analyze     → Compare before vs after
#   6. Improve     → Fix and re-run
#
# Usage:
#   ./chaos/scripts/run-experiment.sh <experiment-name>
#
# Examples:
#   ./chaos/scripts/run-experiment.sh pod-kill
#   ./chaos/scripts/run-experiment.sh network-latency
#   ./chaos/scripts/run-experiment.sh db-failure
#   ./chaos/scripts/run-experiment.sh event-storm
#
# Prerequisites:
#   - k6 installed (https://k6.io)
#   - kubectl configured with Chaos Mesh
#   - Prometheus + Grafana running
#   - docker-compose up (all HotSot services)

set -euo pipefail

# ─── Colors ──────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# ─── Configuration ───────────────────────────────────────────
EXPERIMENT="${1:-}"
BASE_URL="${BASE_URL:-http://localhost:8000}"
PROMETHEUS_URL="${PROMETHEUS_URL:-http://localhost:9090}"
GRAFANA_URL="${GRAFANA_URL:-http://localhost:3000}"
RESULTS_DIR="chaos/experiments/results/$(date +%Y%m%d_%H%M%S)"
CHAOS_DIR="chaos/chaos-mesh"
K6_DIR="chaos/k6"

# ─── Success Criteria ────────────────────────────────────────
MAX_ERROR_RATE=0.05        # 5%
MAX_P95_LATENCY=1.0        # 1 second
MAX_RECOVERY_TIME=30       # 30 seconds
MAX_DLQ_GROWTH=0           # No DLQ growth under normal conditions

# ─── Validate ────────────────────────────────────────────────
if [ -z "$EXPERIMENT" ]; then
    echo -e "${RED}Usage: ./chaos/scripts/run-experiment.sh <experiment-name>${NC}"
    echo ""
    echo "Available experiments:"
    echo "  pod-kill          — Kill a random service pod"
    echo "  network-latency   — Inject 200ms network delay"
    echo "  db-failure        — Kill PostgreSQL pod"
    echo "  redis-failure     — Kill Redis pod"
    echo "  kafka-failure     — Kill Kafka broker"
    echo "  network-partition — Split network between service groups"
    echo "  event-storm       — Flood system with 10x events"
    echo "  spike             — Sudden traffic burst"
    echo "  stress            — Find breaking point"
    echo "  full-suite        — Run all experiments sequentially"
    exit 1
fi

# ─── Helper Functions ────────────────────────────────────────
log_header() {
    echo ""
    echo -e "${CYAN}═══════════════════════════════════════════════════════════${NC}"
    echo -e "${CYAN}  🔬 $1${NC}"
    echo -e "${CYAN}═══════════════════════════════════════════════════════════${NC}"
}

log_step() {
    echo -e "${BLUE}  ▶ $1${NC}"
}

log_success() {
    echo -e "${GREEN}  ✅ $1${NC}"
}

log_warning() {
    echo -e "${YELLOW}  ⚠ $1${NC}"
}

log_fail() {
    echo -e "${RED}  ❌ $1${NC}"
}

# ─── Metric Collection ───────────────────────────────────────
collect_metrics() {
    local label="$1"
    local output_file="$RESULTS_DIR/metrics_${label}.json"

    log_step "Collecting metrics (${label})..."

    # Query Prometheus for key metrics
    curl -s "${PROMETHEUS_URL}/api/v1/query?query=rate(http_requests_total[1m])" \
        | python3 -m json.tool > "${output_file}.rps" 2>/dev/null || echo "{}" > "${output_file}.rps"

    curl -s "${PROMETHEUS_URL}/api/v1/query?query=histogram_quantile(0.95,sum(rate(http_request_duration_seconds_bucket[5m]))by(le))" \
        | python3 -m json.tool > "${output_file}.p95" 2>/dev/null || echo "{}" > "${output_file}.p95"

    curl -s "${PROMETHEUS_URL}/api/v1/query?query=sum(rate(http_requests_total{status=~\"5..\"}[1m]))/sum(rate(http_requests_total[1m]))" \
        | python3 -m json.tool > "${output_file}.error_rate" 2>/dev/null || echo "{}" > "${output_file}.error_rate"

    curl -s "${PROMETHEUS_URL}/api/v1/query?query=hotsot_circuit_breaker_state" \
        | python3 -m json.tool > "${output_file}.circuit_breaker" 2>/dev/null || echo "{}" > "${output_file}.circuit_breaker"

    log_success "Metrics saved to ${output_file}.*"
}

# ─── Baseline Test ───────────────────────────────────────────
run_baseline() {
    log_header "STEP 1: BASELINE — Recording healthy metrics"

    mkdir -p "$RESULTS_DIR"

    # Run baseline load test
    log_step "Running k6 baseline load test..."
    k6 run \
        --out json="${RESULTS_DIR}/baseline.json" \
        --env BASE_URL="${BASE_URL}" \
        "${K6_DIR}/baseline.js" || true

    # Collect baseline metrics from Prometheus
    collect_metrics "baseline"

    log_success "Baseline recorded"
}

# ─── Chaos Injection ─────────────────────────────────────────
inject_chaos() {
    local experiment="$1"
    local chaos_file="${CHAOS_DIR}/${experiment}.yaml"

    log_header "STEP 2: INJECT — Applying ${experiment} chaos"

    if [ ! -f "$chaos_file" ]; then
        log_warning "Chaos file not found: ${chaos_file}"
        log_step "Using Docker Compose chaos injection instead..."

        # Docker-based chaos injection
        case "$experiment" in
            pod-kill)
                local service=$(docker ps --format '{{.Names}}' | grep -E 'hotsot.*service' | shuf -n 1)
                if [ -n "$service" ]; then
                    log_step "Killing container: ${service}"
                    docker kill "$service" || true
                    log_step "Waiting 5s for container to restart..."
                    sleep 5
                fi
                ;;
            db-failure)
                log_step "Stopping PostgreSQL container..."
                docker compose -f docker-compose.yml stop postgres || true
                sleep 30
                log_step "Starting PostgreSQL container..."
                docker compose -f docker-compose.yml start postgres || true
                sleep 10
                ;;
            redis-failure)
                log_step "Stopping Redis container..."
                docker compose -f docker-compose.yml stop redis || true
                sleep 30
                log_step "Starting Redis container..."
                docker compose -f docker-compose.yml start redis || true
                sleep 10
                ;;
            kafka-failure)
                log_step "Stopping Kafka container..."
                docker compose -f docker-compose.yml stop kafka || true
                sleep 30
                log_step "Starting Kafka container..."
                docker compose -f docker-compose.yml start kafka || true
                sleep 10
                ;;
            network-latency)
                log_warning "Network latency requires Chaos Mesh on Kubernetes"
                log_step "Simulating with elevated load instead..."
                k6 run --env BASE_URL="${BASE_URL}" "${K6_DIR}/spike.js" || true
                ;;
            event-storm)
                log_step "Running event storm load test..."
                k6 run \
                    --out json="${RESULTS_DIR}/event-storm.json" \
                    --env BASE_URL="${BASE_URL}" \
                    "${K6_DIR}/event-storm.js" || true
                ;;
            spike)
                log_step "Running spike load test..."
                k6 run \
                    --out json="${RESULTS_DIR}/spike.json" \
                    --env BASE_URL="${BASE_URL}" \
                    "${K6_DIR}/spike.js" || true
                ;;
            stress)
                log_step "Running stress load test..."
                k6 run \
                    --out json="${RESULTS_DIR}/stress.json" \
                    --env BASE_URL="${BASE_URL}" \
                    "${K6_DIR}/stress.js" || true
                ;;
            *)
                log_fail "Unknown experiment: ${experiment}"
                exit 1
                ;;
        esac
    else
        # Kubernetes Chaos Mesh injection
        log_step "Applying Chaos Mesh: ${chaos_file}"
        kubectl apply -f "$chaos_file"
    fi

    log_success "Chaos injection complete"
}

# ─── Measurement ─────────────────────────────────────────────
measure_impact() {
    local experiment="$1"

    log_header "STEP 3: MEASURE — Collecting chaos metrics"

    # Wait for chaos to propagate
    log_step "Waiting 30s for chaos impact to propagate..."
    sleep 30

    # Collect chaos metrics
    collect_metrics "during_${experiment}"

    # Run load test during chaos
    log_step "Running k6 baseline during chaos..."
    k6 run \
        --out json="${RESULTS_DIR}/during_${experiment}.json" \
        --env BASE_URL="${BASE_URL}" \
        "${K6_DIR}/baseline.js" || true

    log_success "Chaos metrics collected"
}

# ─── Analysis ────────────────────────────────────────────────
analyze_results() {
    local experiment="$1"

    log_header "STEP 4: ANALYZE — Before vs After comparison"

    # Collect post-chaos metrics
    collect_metrics "after_${experiment}"

    # Compare baseline vs chaos metrics
    log_step "Comparing baseline vs chaos metrics..."

    # Extract key metrics from Prometheus responses
    local baseline_p95=$(cat "${RESULTS_DIR}/metrics_baseline.p95" 2>/dev/null | python3 -c "
import json,sys
try:
    data=json.load(sys.stdin)
    val=data.get('data',{}).get('result',[{}])[0].get('value',[0,'0'])[1]
    print(float(val))
except: print(0.0)
" 2>/dev/null || echo "0.0")

    local chaos_p95=$(cat "${RESULTS_DIR}/metrics_during_${experiment}.p95" 2>/dev/null | python3 -c "
import json,sys
try:
    data=json.load(sys.stdin)
    val=data.get('data',{}).get('result',[{}])[0].get('value',[0,'0'])[1]
    print(float(val))
except: print(0.0)
" 2>/dev/null || echo "0.0")

    local baseline_error=$(cat "${RESULTS_DIR}/metrics_baseline.error_rate" 2>/dev/null | python3 -c "
import json,sys
try:
    data=json.load(sys.stdin)
    val=data.get('data',{}).get('result',[{}])[0].get('value',[0,'0'])[1]
    print(float(val))
except: print(0.0)
" 2>/dev/null || echo "0.0")

    local chaos_error=$(cat "${RESULTS_DIR}/metrics_during_${experiment}.error_rate" 2>/dev/null | python3 -c "
import json,sys
try:
    data=json.load(sys.stdin)
    val=data.get('data',{}).get('result',[{}])[0].get('value',[0,'0'])[1]
    print(float(val))
except: print(0.0)
" 2>/dev/null || echo "0.0")

    echo ""
    echo -e "${CYAN}┌──────────────────────────────────────────────────┐${NC}"
    echo -e "${CYAN}│         EXPERIMENT RESULTS: ${experiment}          │${NC}"
    echo -e "${CYAN}├──────────────┬──────────────┬────────────────────┤${NC}"
    echo -e "${CYAN}│ Metric       │ Baseline     │ During Chaos       │${NC}"
    echo -e "${CYAN}├──────────────┼──────────────┼────────────────────┤${NC}"
    printf "│ p95 Latency  │ %-12s │ %-18s │\n" "${baseline_p95}s" "${chaos_p95}s"
    printf "│ Error Rate   │ %-12s │ %-18s │\n" "${baseline_error}" "${chaos_error}"
    echo -e "${CYAN}└──────────────┴──────────────┴────────────────────┘${NC}"

    # Check success criteria
    echo ""
    log_step "Checking success criteria..."

    local pass=true

    if (( $(echo "${chaos_error} > ${MAX_ERROR_RATE}" | bc -l 2>/dev/null || echo 0) )); then
        log_fail "Error rate ${chaos_error} exceeds threshold ${MAX_ERROR_RATE}"
        pass=false
    else
        log_success "Error rate within threshold"
    fi

    if (( $(echo "${chaos_p95} > ${MAX_P95_LATENCY}" | bc -l 2>/dev/null || echo 0) )); then
        log_fail "p95 latency ${chaos_p95}s exceeds threshold ${MAX_P95_LATENCY}s"
        pass=false
    else
        log_success "p95 latency within threshold"
    fi

    echo ""
    if [ "$pass" = true ]; then
        log_success "EXPERIMENT PASSED — System is resilient to ${experiment}"
    else
        log_fail "EXPERIMENT FAILED — System needs improvement for ${experiment}"
        log_warning "Check Jaeger traces: http://localhost:16686"
        log_warning "Check Grafana dashboard: http://localhost:3000"
    fi

    # Save experiment report
    cat > "${RESULTS_DIR}/report.md" <<EOF
# Chaos Experiment Report: ${experiment}

**Date:** $(date)
**Environment:** development

## Metrics

| Metric | Baseline | During Chaos | Threshold | Status |
|--------|----------|--------------|-----------|--------|
| p95 Latency | ${baseline_p95}s | ${chaos_p95}s | ${MAX_P95_LATENCY}s | $([ "$pass" = true ] && echo "✅" || echo "❌") |
| Error Rate | ${baseline_error} | ${chaos_error} | ${MAX_ERROR_RATE} | $([ "$pass" = true ] && echo "✅" || echo "❌") |

## Result

$([ "$pass" = true ] && echo "**PASSED** — System is resilient to ${experiment}" || echo "**FAILED** — System needs improvement for ${experiment}")

## Next Steps

- Review Jaeger traces for root cause
- Check DLQ for lost events
- Verify circuit breaker transitions in Grafana
EOF

    log_success "Report saved to ${RESULTS_DIR}/report.md"
}

# ─── Cleanup ─────────────────────────────────────────────────
cleanup() {
    log_header "STEP 5: CLEANUP — Removing chaos injection"

    # Delete Chaos Mesh experiments
    kubectl delete podchaos,networkchaos --all -n hotsot 2>/dev/null || true

    # Ensure all Docker containers are running
    docker compose -f docker-compose.yml up -d 2>/dev/null || true

    log_success "Cleanup complete"
}

# ─── Main ────────────────────────────────────────────────────
main() {
    log_header "🧪 HotSot Chaos Experiment: ${EXPERIMENT}"

    echo -e "${YELLOW}Hypothesis: \"If ${EXPERIMENT} occurs, system should degrade gracefully, not crash\"${NC}"
    echo ""

    # Step 1: Baseline
    run_baseline

    # Step 2: Inject Chaos
    inject_chaos "$EXPERIMENT"

    # Step 3: Measure
    measure_impact "$EXPERIMENT"

    # Step 4: Analyze
    analyze_results "$EXPERIMENT"

    # Step 5: Cleanup
    cleanup

    log_header "🎉 Experiment Complete"
    echo -e "Results: ${RESULTS_DIR}/"
    echo -e "Dashboard: ${GRAFANA_URL}/d/hotsot-chaos"
    echo -e "Traces: http://localhost:16686"
}

# Run full suite if requested
if [ "$EXPERIMENT" = "full-suite" ]; then
    log_header "🧪 Running FULL Chaos Suite"

    for exp in pod-kill network-latency db-failure redis-failure kafka-failure event-storm spike stress; do
        echo ""
        echo -e "${CYAN}═══════════════════════════════════════════════════════════${NC}"
        echo -e "${CYAN}  Running: ${exp}${NC}"
        echo -e "${CYAN}═══════════════════════════════════════════════════════════${NC}"

        EXPERIMENT="$exp"
        RESULTS_DIR="chaos/experiments/results/$(date +%Y%m%d_%H%M%S)_${exp}"
        mkdir -p "$RESULTS_DIR"

        run_baseline
        inject_chaos "$exp"
        measure_impact "$exp"
        analyze_results "$exp"
        cleanup

        sleep 30  # Cool-down between experiments
    done
else
    main
fi
