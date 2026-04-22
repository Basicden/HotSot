/**
 * HotSot — Event Storm Test
 *
 * Purpose: Test Kafka event pipeline under massive event volume.
 * Simulates: Festival rush where 10x normal events flood the system.
 *
 * Usage:
 *   k6 run -e BASE_URL=http://localhost:8000 chaos/k6/event-storm.js
 *
 * What it tests:
 *   - Kafka consumer lag handling
 *   - DLQ routing under pressure
 *   - Event idempotency under duplicate flood
 *   - Circuit breaker on event pipeline
 */

import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Counter, Trend } from 'k6/metrics';

const errorRate = new Rate('errors');
const eventsPublished = new Counter('events_published');
const eventsFailed = new Counter('events_failed');
const eventLatency = new Trend('event_publish_latency', true);

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';

export const options = {
  stages: [
    { duration: '30s', target: 100 },    // Ramp up
    { duration: '2m',  target: 1000 },   // EVENT STORM: 1000 VUs flooding events
    { duration: '1m',  target: 1000 },   // Sustained storm
    { duration: '30s', target: 0 },      // Cool-down
  ],
  thresholds: {
    event_publish_latency: ['p(95)<2000'],  // Event publish < 2s
    errors: ['rate<0.20'],                  // Up to 20% errors acceptable
  },
  tags: { test_type: 'event-storm', service: 'hotsot' },
};

const EVENT_TYPES = [
  'ORDER_CREATED', 'PAYMENT_CONFIRMED', 'SLOT_RESERVED',
  'QUEUE_ASSIGNED', 'PREP_STARTED', 'PREP_COMPLETED',
  'READY_FOR_PICKUP', 'SHELF_ASSIGNED', 'ARRIVAL_DETECTED',
  'HANDOFF_STARTED', 'HANDOFF_COMPLETED', 'ORDER_PICKED',
  'ORDER_CANCELLED', 'KITCHEN_OVERLOAD', 'ETA_UPDATED',
];

export default function () {
  const eventType = EVENT_TYPES[Math.floor(Math.random() * EVENT_TYPES.length)];
  const orderId = `storm_order_${__VU}_${__ITER}`;
  const kitchenId = `kitchen_${Math.floor(Math.random() * 50) + 1}`;
  const userId = `user_${Math.floor(Math.random() * 100000)}`;

  const startTime = Date.now();

  // Publish event via order service
  const res = http.post(
    `${BASE_URL}/orders`,
    JSON.stringify({
      user_id: userId,
      kitchen_id: kitchenId,
      tenant_id: 'default',
      items: [{ item_id: 'storm-item', qty: 1, price: 100 }],
      total_amount: 100,
      payment_method: 'UPI',
      idempotency_key: `storm_${__VU}_${__ITER}`,
    }),
    { headers: { 'Content-Type': 'application/json' } }
  );

  eventLatency.add(Date.now() - startTime);

  if (res.status >= 200 && res.status < 300) {
    eventsPublished.add(1);
  } else {
    eventsFailed.add(1);
    errorRate.add(1);
  }

  check(res, {
    'event accepted': (r) => r.status >= 200 && r.status < 300,
    'event rejected 429': (r) => r.status === 429,  // Rate limited — expected
    'circuit breaker 503': (r) => r.status === 503,  // Open CB — expected
  });

  // Also test duplicate event idempotency every 5th request
  if (__ITER % 5 === 0) {
    const dupRes = http.post(
      `${BASE_URL}/orders`,
      JSON.stringify({
        user_id: userId,
        kitchen_id: kitchenId,
        tenant_id: 'default',
        items: [{ item_id: 'storm-item', qty: 1, price: 100 }],
        total_amount: 100,
        payment_method: 'UPI',
        idempotency_key: `storm_${__VU}_${__ITER}`,  // SAME key = duplicate
      }),
      { headers: { 'Content-Type': 'application/json' } }
    );

    check(dupRes, {
      'duplicate handled': (r) => r.status === 200 || r.status === 409,
    });
  }

  sleep(Math.random() * 0.2); // Very fast — storm mode
}
