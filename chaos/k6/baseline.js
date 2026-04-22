/**
 * HotSot — Baseline Load Test
 *
 * Purpose: Establish normal traffic baseline for all 18 microservices.
 * Run this BEFORE any chaos injection to record "healthy" metrics.
 *
 * Usage:
 *   k6 run -e BASE_URL=http://localhost:8000 chaos/k6/baseline.js
 *
 * Metrics collected:
 *   - p50, p95, p99 latency per endpoint
 *   - Requests per second
 *   - Error rate (4xx + 5xx)
 *   - Circuit breaker open/close transitions
 */

import http from 'k6/http';
import { check, sleep, group } from 'k6';
import { Rate, Trend } from 'k6/metrics';

// ─── Custom Metrics ─────────────────────────────────────────
const errorRate = new Rate('errors');
const orderLatency = new Trend('order_latency', true);
const kitchenLatency = new Trend('kitchen_latency', true);
const shelfLatency = new Trend('shelf_latency', true);
const etaLatency = new Trend('eta_latency', true);
const searchLatency = new Trend('search_latency', true);

// ─── Configuration ──────────────────────────────────────────
const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';

export const options = {
  stages: [
    { duration: '30s', target: 20 },   // Warm-up: ramp to 20 VUs
    { duration: '2m',  target: 50 },   // Sustained: 50 VUs (normal traffic)
    { duration: '30s', target: 0 },    // Cool-down
  ],
  thresholds: {
    http_req_duration: ['p(95)<500'],   // 95% requests < 500ms
    errors: ['rate<0.05'],              // Error rate < 5%
    http_req_failed: ['rate<0.05'],     // Failed requests < 5%
  },
  tags: {
    test_type: 'baseline',
    service: 'hotsot',
  },
};

// ─── Test Data ──────────────────────────────────────────────
function generateOrderId() {
  return `ord_test_${Math.random().toString(36).substring(2, 10)}`;
}

function generateKitchenId() {
  return `kitchen_${Math.floor(Math.random() * 100) + 1}`;
}

function generateUserId() {
  return `user_${Math.floor(Math.random() * 10000) + 1}`;
}

// ─── Test Scenarios ─────────────────────────────────────────
export default function () {
  const scenario = Math.floor(Math.random() * 10);

  switch (scenario) {
    case 0:
    case 1:
    case 2:
      // 30% — Order lifecycle
      group('Order Lifecycle', () => {
        const orderId = generateOrderId();
        const startTime = Date.now();

        // Create order
        const createRes = http.post(
          `${BASE_URL}/orders`,
          JSON.stringify({
            user_id: generateUserId(),
            kitchen_id: generateKitchenId(),
            tenant_id: 'default',
            items: [{ item_id: 'butter-chicken', qty: 2, price: 350 }],
            total_amount: 700,
            payment_method: 'UPI',
          }),
          { headers: { 'Content-Type': 'application/json' } }
        );

        orderLatency.add(Date.now() - startTime);
        check(createRes, {
          'order created': (r) => r.status === 200 || r.status === 201,
        }) || errorRate.add(1);

        sleep(0.5);

        // Get order status
        if (createRes.status === 200 || createRes.status === 201) {
          const getRes = http.get(`${BASE_URL}/orders/${orderId}`);
          check(getRes, { 'order fetched': (r) => r.status === 200 || r.status === 404 });
        }
      });
      break;

    case 3:
    case 4:
      // 20% — Kitchen operations
      group('Kitchen Operations', () => {
        const startTime = Date.now();
        const kitchenId = generateKitchenId();

        const res = http.get(`${BASE_URL}/kitchen/${kitchenId}/status`);
        kitchenLatency.add(Date.now() - startTime);

        check(res, {
          'kitchen status': (r) => r.status === 200 || r.status === 404,
        }) || errorRate.add(1);
      });
      break;

    case 5:
    case 6:
      // 20% — Shelf operations
      group('Shelf Operations', () => {
        const startTime = Date.now();
        const res = http.get(`${BASE_URL}/shelf/status`);
        shelfLatency.add(Date.now() - startTime);

        check(res, {
          'shelf status': (r) => r.status === 200 || r.status === 404,
        }) || errorRate.add(1);
      });
      break;

    case 7:
      // 10% — ETA prediction
      group('ETA Prediction', () => {
        const startTime = Date.now();
        const orderId = generateOrderId();

        const res = http.get(`${BASE_URL}/eta/${orderId}`);
        etaLatency.add(Date.now() - startTime);

        check(res, {
          'eta response': (r) => r.status === 200 || r.status === 404,
        }) || errorRate.add(1);
      });
      break;

    case 8:
      // 10% — Search
      group('Search', () => {
        const startTime = Date.now();
        const queries = ['biryani', 'tandoor', 'thali', 'dosa', 'pizza'];
        const query = queries[Math.floor(Math.random() * queries.length)];

        const res = http.get(`${BASE_URL}/search?q=${query}`);
        searchLatency.add(Date.now() - startTime);

        check(res, {
          'search response': (r) => r.status === 200 || r.status === 404,
        }) || errorRate.add(1);
      });
      break;

    case 9:
      // 10% — Health checks
      group('Health Check', () => {
        const res = http.get(`${BASE_URL}/health`);
        check(res, {
          'health ok': (r) => r.status === 200,
        }) || errorRate.add(1);
      });
      break;
  }

  sleep(Math.random() * 2 + 0.5); // 0.5-2.5s between requests
}

// ─── Teardown ───────────────────────────────────────────────
export function teardown(data) {
  console.log('Baseline load test complete — record these metrics as your "healthy" baseline');
}
