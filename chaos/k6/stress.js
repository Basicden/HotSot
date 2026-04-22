/**
 * HotSot — Stress Test (Break the System)
 *
 * Purpose: Find the absolute breaking point of the system.
 * Ramp load until failure, then measure recovery.
 *
 * Usage:
 *   k6 run -e BASE_URL=http://localhost:8000 chaos/k6/stress.js
 *
 * Success criteria:
 *   - System degrades gracefully (higher latency, not crashes)
 *   - Auto-recovery when load decreases
 *   - No data corruption
 */

import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend } from 'k6/metrics';

const errorRate = new Rate('errors');
const recoveryLatency = new Trend('recovery_latency', true);

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';

export const options = {
  stages: [
    { duration: '2m', target: 100 },    // Normal
    { duration: '2m', target: 500 },    // Elevated
    { duration: '2m', target: 1000 },   // High
    { duration: '2m', target: 2000 },   // Extreme
    { duration: '2m', target: 3000 },   // BREAK POINT
    { duration: '5m', target: 100 },    // Recovery phase
    { duration: '2m', target: 0 },      // Cool-down
  ],
  thresholds: {
    http_req_duration: ['p(99)<5000'],     // p99 < 5s even at break point
    errors: ['rate<0.30'],                 // Up to 30% at break point
  },
  tags: { test_type: 'stress', service: 'hotsot' },
};

export default function () {
  const startTime = Date.now();
  const res = http.get(`${BASE_URL}/health`);
  const latency = Date.now() - startTime;

  if (__VU < 200) {
    // Lower VU range = should remain healthy
    recoveryLatency.add(latency);
  }

  check(res, {
    'health check alive': (r) => r.status === 200,
    'degraded but alive': (r) => r.status === 200 || r.status === 503,
    'system crashed': (r) => {
      if (r.status === 0 || r.status >= 500 && r.status !== 503) {
        errorRate.add(1);
        return false;
      }
      return true;
    },
  });

  sleep(Math.random() * 0.3);
}
