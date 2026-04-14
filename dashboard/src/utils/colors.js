/** HotSot Dashboard V2 — V2 State Color Mapping (16 states) */
export const statusColor = (status) => {
  const map = {
    CREATED: '#64748b',
    PAYMENT_PENDING: '#f59e0b',
    PAYMENT_CONFIRMED: '#3b82f6',
    SLOT_RESERVED: '#6366f1',
    QUEUE_ASSIGNED: '#8b5cf6',
    IN_PREP: '#f97316',
    PACKING: '#ec4899',
    READY: '#22c55e',
    ON_SHELF: '#10b981',
    ARRIVED: '#06b6d4',
    HANDOFF_IN_PROGRESS: '#14b8a6',
    PICKED: '#22c55e',
    EXPIRED: '#ef4444',
    REFUNDED: '#f43f5e',
    CANCELLED: '#94a3b8',
    FAILED: '#dc2626',
  };
  return map[status] || '#64748b';
};

export const statusLabel = (status) => {
  const map = {
    CREATED: 'Created',
    PAYMENT_PENDING: 'Payment Pending',
    PAYMENT_CONFIRMED: 'Payment Confirmed',
    SLOT_RESERVED: 'Slot Reserved',
    QUEUE_ASSIGNED: 'Queue Assigned',
    IN_PREP: 'In Preparation',
    PACKING: 'Packing',
    READY: 'Ready',
    ON_SHELF: 'On Shelf',
    ARRIVED: 'Arrived',
    HANDOFF_IN_PROGRESS: 'Handoff',
    PICKED: 'Picked Up',
    EXPIRED: 'Expired',
    REFUNDED: 'Refunded',
    CANCELLED: 'Cancelled',
    FAILED: 'Failed',
  };
  return map[status] || status;
};

export const isTerminal = (status) => ['PICKED', 'EXPIRED', 'REFUNDED', 'CANCELLED', 'FAILED'].includes(status);

export const arrivalTypeIcon = (type) => {
  if (type === 'GPS') return '\uD83D\uDCCD';
  if (type === 'QR_SCAN') return '\uD83D\uDCF1';
  return '?';
};

export const compensationReasonLabel = (reason) => {
  const map = {
    SHELF_EXPIRED: 'Shelf Expired',
    KITCHEN_FAILURE: 'Kitchen Failure',
    PAYMENT_CONFLICT: 'Payment Conflict',
    DELAY_EXCEEDED: 'Delay Exceeded',
    WRONG_ORDER_DELIVERED: 'Wrong Order',
    CUSTOMER_COMPLAINT: 'Complaint',
  };
  return map[reason] || reason;
};

export const refundTypeLabel = (type) => {
  const map = {
    FULL_REFUND: 'Full Refund',
    PARTIAL_REFUND: 'Partial Refund',
    CREDIT_NOTE: 'Credit Note',
    RE_MAKE_ORDER: 'Re-Make Order',
  };
  return map[type] || type;
};
