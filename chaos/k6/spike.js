/**
 * HotSot — Spike Load Test
 *
 * Purpose: Test system behavior under sudden traffic burst.
 * Simulates: Flash sale, festival rush, viral food trend.
 *
 * Usage:
 *   k6 run -e BASE_URL=http://localhost:8000 chaos/k6/spike.js
 *
 * Expected behavior:
 *   - Circuit breakers should trip and recover
 *   - No data corruption under load
 *   - Graceful degradation, not crash
 */

import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Counter } from 'k6/metrics';

const errorRate = new Rate('errors');
const circuitBreakerOpens = new Counter('circuit_breaker_opens');

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';

export const options = {
  stages: [
    { duration: '30s',  target: 50 },     // Normal traffic
    { duration: '10s',  target: 500 },    // SPIKE! 10x traffic
    { duration: '1m',   target: 500 },    // Sustained spike
    { duration: '30s',  target: 50 },     // Recovery
    { duration: '30s',  target: 0 },      // Cool-down
  ],
  thresholds: {
    http_req_duration: ['p(99)<3000'],     // p99 < 3s during spike
    errors: ['rate<0.15'],                 // Error rate < 15% (some degradation OK)
    http_req_failed: ['rate<0.15'],
  },
  tags: { test_type: 'spike', service: 'hotsot' },
};

export default function () {
  const scenario = Math.floor(Math.random() * 5);

  switch (scenario) {
    case 0:
    case 1:
      // 40% — Order creation (most impacted during spike)
      {
        const res = http.post(
          `${BASE_URL}/orders`,
          JSON.stringify({
            user_id: `user_${Math.floor(Math.random() * 50000)}`,
            kitchen_id: `kitchen_${Math.floor(Math.random() * 100) + 1}`,
            tenant_id: 'default',
            items: [{ item_id: 'butter-chicken', qty: 1, price: 350 }],
            total_amount: 350,
            payment_method: 'UPI',
          }),
          { headers: { 'Content-Type': 'application/json' } }
        );

        check(res, {
          'order status 2xx': (r) => r.status >= 200 && r.status < 300,
          'order status 5xx': (r) => {
            if (r.status >= 500) { errorRate.add(1); return false; }
            return true;
          },
          'circuit breaker 503': (r) => {
            if (r.status === 503) { circuitBreakerOpens.add(1); return true; }
            return true; // Not a failure — expected under load
          },
        });
      }
      break;

    case 2:
      // 20% — Kitchen status
      {
        const res = http.get(`${BASE_URL}/kitchen/kitchen_${Math.floor(Math.random() * 100) + 1}/status`);
        check(res, { 'kitchen ok': (r) => r.status < 500 }) || errorRate.add(1);
      }
      break;

    case 3:
      // 20% — Search (heavy DB/ES load)
      {
        const queries = ['biryani', 'paneer', 'dosa', 'chaat', 'samosa'];
        const res = http.get(`${BASE_URL}/search?q=${queries[Math.floor(Math.random() * queries.length)]}`);
        check(res, { 'search ok': (r) => r.status < 500 }) || errorRate.add(1);
      }
      break;

    case 4:
      // 20% — ETA
      {
        const res = http.get(`${BASE_URL}/eta/order_${Math.floor(Math.random() * 10000)}`);
        check(res, { 'eta ok': (r) => r.status < 500 }) || errorRate.add(1);
      }
      break;
  }

  sleep(Math.random() * 0.5); // Aggressive pacing during spike
}
