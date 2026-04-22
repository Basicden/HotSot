/**
 * HotSot — PHASE 4: Adversarial Load Test
 *
 * Simulates realistic Indian cloud kitchen traffic with adversarial patterns:
 *   - Festival rush (Diwali, Ramadan, Dussehra)
 *   - Flash sale spikes
 *   - Malicious bot traffic
 *   - Mixed normal + adversarial users
 *
 * Usage:
 *   k6 run -e BASE_URL=http://localhost:8000 chaos/k6/adversarial.js
 *
 * Metrics:
 *   - hotsot_adversarial_success_rate (% of valid responses)
 *   - hotsot_injection_blocked_rate (% of malicious inputs blocked)
 *   - Standard k6 metrics (latency, RPS, error rate)
 */

import http from 'k6/http';
import { check, sleep, group } from 'k6';
import { Rate, Counter, Trend } from 'k6/metrics';

// ─── Custom Metrics ─────────────────────────────────────────
const errorRate = new Rate('errors');
const injectionBlocked = new Counter('injection_blocked');
const validResponseRate = new Rate('valid_responses');
const duplicateHandled = new Counter('duplicate_handled');
const orderLatency = new Trend('order_latency', true);

// ─── Configuration ──────────────────────────────────────────
const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';

export const options = {
  stages: [
    { duration: '1m',  target: 50 },    // Warm-up: normal traffic
    { duration: '30s', target: 200 },    // Ramp up
    { duration: '2m',  target: 200 },    // Sustained mixed traffic
    { duration: '30s', target: 500 },    // DIWALI RUSH
    { duration: '1m',  target: 500 },    // Sustained rush
    { duration: '30s', target: 100 },    // Cool-down
    { duration: '30s', target: 0 },      // Stop
  ],
  thresholds: {
    http_req_duration: ['p(95)<3000'],
    errors: ['rate<0.20'],
    valid_responses: ['rate>0.80'],
  },
  tags: { test_type: 'adversarial', service: 'hotsot' },
};

// ─── Indian Food Items ──────────────────────────────────────
const ITEMS = [
  { item_id: 'butter-chicken', price: 350, complexity: 1.2 },
  { item_id: 'paneer-tikka', price: 280, complexity: 1.0 },
  { item_id: 'hyderabadi-biryani', price: 400, complexity: 1.5 },
  { item_id: 'masala-dosa', price: 150, complexity: 0.8 },
  { item_id: 'chole-bhature', price: 180, complexity: 0.9 },
  { item_id: 'dal-makhani', price: 250, complexity: 1.1 },
  { item_id: 'gulab-jamun', price: 80, complexity: 0.5 },
  { item_id: 'tandoori-roti', price: 40, complexity: 0.3 },
];

const TENANTS = ['mumbai', 'delhi', 'bangalore', 'chennai', 'hyderabad'];
const PAYMENT_METHODS = ['UPI', 'CARD', 'WALLET', 'COD'];

// ─── Payload Generators ─────────────────────────────────────

function generateNormalOrder() {
  const items = [];
  const count = Math.floor(Math.random() * 3) + 1;
  for (let i = 0; i < count; i++) {
    const item = ITEMS[Math.floor(Math.random() * ITEMS.length)];
    items.push({ item_id: item.item_id, qty: Math.floor(Math.random() * 3) + 1, price: item.price });
  }
  const total = items.reduce((sum, i) => sum + i.qty * i.price, 0);
  return {
    user_id: `user_${Math.floor(Math.random() * 50000)}`,
    kitchen_id: `kitchen_${Math.floor(Math.random() * 100) + 1}`,
    tenant_id: TENANTS[Math.floor(Math.random() * TENANTS.length)],
    items,
    total_amount: total,
    payment_method: PAYMENT_METHODS[Math.floor(Math.random() * PAYMENT_METHODS.length)],
    idempotency_key: `idem_${__VU}_${__ITER}`,
  };
}

function generateMaliciousPayload() {
  const attackType = Math.floor(Math.random() * 7);
  switch (attackType) {
    case 0: // SQL Injection
      return {
        user_id: "'; DROP TABLE orders;--",
        kitchen_id: "kitchen_1",
        tenant_id: "mumbai",
        items: [{ item_id: "1' OR '1'='1", qty: 1, price: 100 }],
        total_amount: 100,
      };
    case 1: // XSS
      return {
        user_id: "<script>alert('xss')</script>",
        kitchen_id: "kitchen_1",
        tenant_id: "mumbai",
        items: [{ item_id: "biryani", qty: 1, price: 400 }],
        total_amount: 400,
      };
    case 2: // Price manipulation
      return {
        user_id: `user_attack_${__VU}`,
        kitchen_id: "kitchen_1",
        tenant_id: "mumbai",
        items: [{ item_id: "biryani", qty: 1, price: 400 }],
        total_amount: 1, // Mismatch!
      };
    case 3: // Negative amount
      return {
        user_id: `user_attack_${__VU}`,
        kitchen_id: "kitchen_1",
        tenant_id: "mumbai",
        items: [{ item_id: "biryani", qty: -1, price: -400 }],
        total_amount: -400,
      };
    case 4: // Oversized
      return {
        user_id: `user_attack_${__VU}`,
        kitchen_id: "kitchen_1",
        tenant_id: "mumbai",
        items: [{ item_id: "item_" + "x".repeat(10000), qty: 999999, price: 999999 }],
        total_amount: 999999999,
      };
    case 5: // Empty
      return {
        user_id: "",
        kitchen_id: "",
        tenant_id: "",
        items: [],
        total_amount: 0,
      };
    case 6: // Type confusion
      return {
        user_id: { $gt: "" },
        kitchen_id: ["kitchen_1"],
        tenant_id: null,
        items: "not_an_array",
        total_amount: "free",
      };
  }
}

// ─── Main Test Function ─────────────────────────────────────
export default function () {
  // 70% normal, 20% malicious, 10% edge case
  const scenario = Math.random();
  let payload, isMalicious = false;

  if (scenario < 0.70) {
    // Normal user
    payload = generateNormalOrder();
  } else if (scenario < 0.90) {
    // Malicious user
    payload = generateMaliciousPayload();
    isMalicious = true;
  } else {
    // Edge case: double-click (same idempotency key)
    payload = generateNormalOrder();
    // First request
    const res1 = http.post(
      `${BASE_URL}/orders`,
      JSON.stringify(payload),
      { headers: { 'Content-Type': 'application/json' } }
    );
    // Immediate duplicate (within 50ms)
    const res2 = http.post(
      `${BASE_URL}/orders`,
      JSON.stringify(payload),
      { headers: { 'Content-Type': 'application/json' } }
    );

    if (res2.status === 200 || res2.status === 409) {
      duplicateHandled.add(1);
    }
    validResponseRate.add(res1.status < 500);
    sleep(Math.random() * 0.5);
    return;
  }

  const startTime = Date.now();

  if (isMalicious) {
    // Send malicious payload and verify it's blocked
    const res = http.post(
      `${BASE_URL}/orders`,
      JSON.stringify(payload),
      { headers: { 'Content-Type': 'application/json' } }
    );

    // Malicious payloads should be rejected (4xx, not 5xx)
    const blocked = res.status >= 400 && res.status < 500;
    if (blocked) {
      injectionBlocked.add(1);
    }
    validResponseRate.add(blocked);

  } else {
    // Normal order flow
    const res = http.post(
      `${BASE_URL}/orders`,
      JSON.stringify(payload),
      { headers: { 'Content-Type': 'application/json' } }
    );

    orderLatency.add(Date.now() - startTime);

    check(res, {
      'order created': (r) => r.status === 200 || r.status === 201,
      'no server error': (r) => r.status < 500,
    }) || errorRate.add(1);

    validResponseRate.add(res.status < 500);
  }

  sleep(Math.random() * 1);
}

// ─── Teardown ───────────────────────────────────────────────
export function teardown() {
  console.log('Adversarial load test complete — check injection_blocked counter');
}
