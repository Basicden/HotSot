/** HotSot Dashboard V2 — Mock Data for 16-state V2 system */

export const V2_STATES = [
  'CREATED','PAYMENT_PENDING','PAYMENT_CONFIRMED','SLOT_RESERVED','QUEUE_ASSIGNED',
  'IN_PREP','PACKING','READY','ON_SHELF','ARRIVED','HANDOFF_IN_PROGRESS','PICKED',
  'EXPIRED','REFUNDED','CANCELLED','FAILED',
];

export const V2_FLOW = [
  'CREATED','PAYMENT_PENDING','PAYMENT_CONFIRMED','SLOT_RESERVED','QUEUE_ASSIGNED',
  'IN_PREP','PACKING','READY','ON_SHELF','ARRIVED','HANDOFF_IN_PROGRESS','PICKED',
];

export const TERMINAL_STATES = ['EXPIRED','REFUNDED','CANCELLED','FAILED'];

export const mockOrders = [
  { order_id: 'o_001', user_id: 'u_101', kitchen_id: 'k_koramangala', status: 'ON_SHELF', queue_type: 'NORMAL', priority_score: 45.2, batch_id: null, user_tier: 'PLUS', eta_seconds: 480, eta_confidence: 0.88, eta_risk: 'LOW', items: [{name:'Paneer Tikka Wrap',qty:2,price:149},{name:'Masala Chai',qty:1,price:49}], total_amount: 347.00, payment_method: 'UPI', shelf_id: 'A3', shelf_zone: 'HOT', shelf_ttl_remaining: 420, arrived_at: null, handoff_method: null, created_at: '2026-04-14T11:30:00Z', updated_at: '2026-04-14T11:38:00Z' },
  { order_id: 'o_002', user_id: 'u_102', kitchen_id: 'k_koramangala', status: 'IN_PREP', queue_type: 'URGENT', priority_score: 78.5, batch_id: 'b_012', user_tier: 'VIP', eta_seconds: 360, eta_confidence: 0.92, eta_risk: 'LOW', items: [{name:'Butter Chicken',qty:1,price:249},{name:'Garlic Naan',qty:3,price:59}], total_amount: 426.00, payment_method: 'UPI', shelf_id: null, shelf_zone: null, shelf_ttl_remaining: null, arrived_at: null, handoff_method: null, created_at: '2026-04-14T11:32:00Z', updated_at: '2026-04-14T11:36:00Z' },
  { order_id: 'o_003', user_id: 'u_103', kitchen_id: 'k_indiranagar', status: 'ARRIVED', queue_type: 'NORMAL', priority_score: 65.2, batch_id: null, user_tier: 'FREE', eta_seconds: 120, eta_confidence: 0.95, eta_risk: 'LOW', items: [{name:'Filter Coffee',qty:2,price:69},{name:'Rava Dosa',qty:1,price:129}], total_amount: 267.00, payment_method: 'CARD', shelf_id: 'B1', shelf_zone: 'COLD', shelf_ttl_remaining: 1800, arrived_at: '2026-04-14T11:40:00Z', handoff_method: 'QR_SCAN', created_at: '2026-04-14T11:10:00Z', updated_at: '2026-04-14T11:40:00Z' },
  { order_id: 'o_004', user_id: 'u_104', kitchen_id: 'k_koramangala', status: 'HANDOFF_IN_PROGRESS', queue_type: 'IMMEDIATE', priority_score: 92.0, batch_id: null, user_tier: 'PRO', eta_seconds: 30, eta_confidence: 0.99, eta_risk: 'LOW', items: [{name:'Biryani Special',qty:1,price:329}], total_amount: 329.00, payment_method: 'WALLET', shelf_id: 'A1', shelf_zone: 'HOT', shelf_ttl_remaining: 600, arrived_at: '2026-04-14T11:42:00Z', handoff_method: 'QR_SCAN', created_at: '2026-04-14T11:15:00Z', updated_at: '2026-04-14T11:43:00Z' },
  { order_id: 'o_005', user_id: 'u_105', kitchen_id: 'k_indiranagar', status: 'PAYMENT_PENDING', queue_type: 'NORMAL', priority_score: 25.0, batch_id: null, user_tier: 'FREE', eta_seconds: null, eta_confidence: null, eta_risk: 'LOW', items: [{name:'Samosa Platter',qty:2,price:99},{name:'Lassi',qty:1,price:79}], total_amount: 277.00, payment_method: 'UPI', shelf_id: null, shelf_zone: null, shelf_ttl_remaining: null, arrived_at: null, handoff_method: null, created_at: '2026-04-14T11:45:00Z', updated_at: '2026-04-14T11:45:00Z' },
  { order_id: 'o_006', user_id: 'u_106', kitchen_id: 'k_koramangala', status: 'EXPIRED', queue_type: 'BATCH', priority_score: 18.0, batch_id: 'b_011', user_tier: 'FREE', eta_seconds: 0, eta_confidence: null, eta_risk: 'HIGH', items: [{name:'Medu Vada',qty:3,price:69}], total_amount: 207.00, payment_method: 'UPI', shelf_id: 'A5', shelf_zone: 'HOT', shelf_ttl_remaining: 0, arrived_at: null, handoff_method: null, created_at: '2026-04-14T10:30:00Z', updated_at: '2026-04-14T10:45:00Z' },
  { order_id: 'o_007', user_id: 'u_107', kitchen_id: 'k_indiranagar', status: 'PACKING', queue_type: 'NORMAL', priority_score: 52.0, batch_id: null, user_tier: 'PLUS', eta_seconds: 300, eta_confidence: 0.82, eta_risk: 'MEDIUM', items: [{name:'Chicken 65',qty:1,price:199},{name:'Parotta',qty:2,price:39}], total_amount: 277.00, payment_method: 'CARD', shelf_id: null, shelf_zone: null, shelf_ttl_remaining: null, arrived_at: null, handoff_method: null, created_at: '2026-04-14T11:35:00Z', updated_at: '2026-04-14T11:39:00Z' },
  { order_id: 'o_008', user_id: 'u_108', kitchen_id: 'k_koramangala', status: 'PICKED', queue_type: 'IMMEDIATE', priority_score: 88.0, batch_id: null, user_tier: 'VIP', eta_seconds: 0, eta_confidence: 0.99, eta_risk: 'LOW', items: [{name:'Chole Bhature',qty:1,price:179}], total_amount: 179.00, payment_method: 'UPI', shelf_id: null, shelf_zone: null, shelf_ttl_remaining: null, arrived_at: '2026-04-14T11:20:00Z', handoff_method: 'QR_SCAN', created_at: '2026-04-14T11:00:00Z', updated_at: '2026-04-14T11:25:00Z' },
];

export const mockArrivals = [
  { arrival_id: 'ar_001', order_id: 'o_003', user_id: 'u_103', kitchen_id: 'k_indiranagar', strength: 'HARD', arrival_type: 'QR_SCAN', distance_m: 5.2, latitude: 12.9784, longitude: 77.6408, qr_token: '***masked***', priority_boosted: true, boost_amount: 20, detected_at: '2026-04-14T11:40:00Z' },
  { arrival_id: 'ar_002', order_id: 'o_004', user_id: 'u_104', kitchen_id: 'k_koramangala', strength: 'HARD', arrival_type: 'GPS', distance_m: 42.8, latitude: 12.9352, longitude: 77.6245, qr_token: null, priority_boosted: true, boost_amount: 20, detected_at: '2026-04-14T11:42:00Z' },
];

export const mockCompensations = [
  { compensation_id: 'COMP_20260414101', order_id: 'o_006', kitchen_id: 'k_koramangala', reason: 'SHELF_EXPIRED', refund_type: 'FULL_REFUND', amount: 207.00, status: 'COMPLETED', payment_ref: 'pay_ref_001', upi_refund_ref: 'upi_ref_001', is_duplicate: false, delay_minutes: 0, metadata: { expired_at: '2026-04-14T10:45:00Z', shelf_zone: 'HOT', ttl_exceeded_by_s: 300 }, created_at: '2026-04-14T10:45:30Z', updated_at: '2026-04-14T10:47:00Z' },
  { compensation_id: 'COMP_20260414102', order_id: 'o_009', kitchen_id: 'k_indiranagar', reason: 'DELAY_EXCEEDED', refund_type: 'PARTIAL_REFUND', amount: 138.50, status: 'PENDING', payment_ref: null, upi_refund_ref: null, is_duplicate: false, delay_minutes: 22, metadata: { promised_eta: '2026-04-14T11:30:00Z', actual_pickup: null, delay_minutes: 22 }, created_at: '2026-04-14T11:52:00Z', updated_at: '2026-04-14T11:52:00Z' },
  { compensation_id: 'COMP_20260414103', order_id: 'o_010', kitchen_id: 'k_koramangala', reason: 'KITCHEN_FAILURE', refund_type: 'FULL_REFUND', amount: 349.00, status: 'PROCESSING', payment_ref: 'pay_ref_003', upi_refund_ref: null, is_duplicate: false, delay_minutes: 0, metadata: { failure_reason: 'equipment_malfunction', station: 'GRILL' }, created_at: '2026-04-14T11:55:00Z', updated_at: '2026-04-14T11:55:00Z' },
];

export const mockKitchens = [
  { id: 'k_koramangala', name: 'Koramangala Hub', location: { lat: 12.9352, lng: 77.6245 }, capacity: 20, current_load: 14, is_active: true },
  { id: 'k_indiranagar', name: 'Indiranagar Central', location: { lat: 12.9784, lng: 77.6408 }, capacity: 15, current_load: 8, is_active: true },
];

export const mockShelves = [
  { shelf_id: 'A1', kitchen_id: 'k_koramangala', zone: 'HOT', status: 'OCCUPIED', order_id: 'o_004', remaining_ttl: 600 },
  { shelf_id: 'A2', kitchen_id: 'k_koramangala', zone: 'HOT', status: 'AVAILABLE', order_id: null, remaining_ttl: 0 },
  { shelf_id: 'A3', kitchen_id: 'k_koramangala', zone: 'HOT', status: 'OCCUPIED', order_id: 'o_001', remaining_ttl: 420 },
  { shelf_id: 'A4', kitchen_id: 'k_koramangala', zone: 'HOT', status: 'AVAILABLE', order_id: null, remaining_ttl: 0 },
  { shelf_id: 'A5', kitchen_id: 'k_koramangala', zone: 'HOT', status: 'EXPIRED', order_id: 'o_006', remaining_ttl: 0 },
  { shelf_id: 'B1', kitchen_id: 'k_indiranagar', zone: 'COLD', status: 'OCCUPIED', order_id: 'o_003', remaining_ttl: 1800 },
  { shelf_id: 'B2', kitchen_id: 'k_indiranagar', zone: 'COLD', status: 'AVAILABLE', order_id: null, remaining_ttl: 0 },
  { shelf_id: 'C1', kitchen_id: 'k_koramangala', zone: 'AMBIENT', status: 'AVAILABLE', order_id: null, remaining_ttl: 0 },
];

export const mockStats = {
  total_orders: 8,
  active_orders: 6,
  picked_today: 24,
  expired_today: 2,
  avg_eta_seconds: 380,
  avg_pickup_time_seconds: 1420,
  compensation_pending: 1,
  compensation_completed: 1,
  compensation_total_amount: 694.50,
  kitchen_utilization: 0.68,
};
