import React from 'react';
import { compensationReasonLabel, refundTypeLabel } from '../utils/colors';

/** Compensation Panel — refund, UPI, penalty tracking */
export default function CompensationPanel({ compensations, expanded = false }) {
  const statusBg = (s) => {
    if (s === 'COMPLETED') return '#22c55e22';
    if (s === 'PROCESSING') return '#f59e0b22';
    if (s === 'PENDING') return '#3b82f622';
    return '#64748b22';
  };
  const statusClr = (s) => {
    if (s === 'COMPLETED') return '#22c55e';
    if (s === 'PROCESSING') return '#f59e0b';
    if (s === 'PENDING') return '#3b82f6';
    return '#64748b';
  };

  return (
    <div style={{ background: '#1e293b', borderRadius: 12, padding: 20, border: '1px solid #334155' }}>
      <h3 style={{ margin: '0 0 16px 0', fontSize: 15, fontWeight: 700, color: '#f43f5e' }}>Compensation Engine</h3>

      <div style={{ display: 'grid', gridTemplateColumns: expanded ? '1fr' : 'repeat(auto-fit, minmax(320px, 1fr))', gap: 12 }}>
        {compensations.map(c => (
          <div key={c.compensation_id} style={{
            background: '#0f172a', borderRadius: 10, padding: 16, border: '1px solid #334155',
          }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 10 }}>
              <span style={{ fontSize: 12, fontWeight: 700, color: '#e2e8f0' }}>{c.compensation_id}</span>
              <span style={{
                background: statusBg(c.status), color: statusClr(c.status),
                padding: '2px 8px', borderRadius: 6, fontSize: 10, fontWeight: 700,
              }}>{c.status}</span>
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, fontSize: 11 }}>
              <div><span style={{ color: '#64748b' }}>Order:</span> <span style={{ color: '#94a3b8' }}>{c.order_id}</span></div>
              <div><span style={{ color: '#64748b' }}>Kitchen:</span> <span style={{ color: '#94a3b8' }}>{c.kitchen_id.replace('k_', '')}</span></div>
              <div><span style={{ color: '#64748b' }}>Reason:</span> <span style={{ color: '#f43f5e', fontWeight: 600 }}>{compensationReasonLabel(c.reason)}</span></div>
              <div><span style={{ color: '#64748b' }}>Type:</span> <span style={{ color: '#94a3b8' }}>{refundTypeLabel(c.refund_type)}</span></div>
              <div><span style={{ color: '#64748b' }}>Amount:</span> <span style={{ color: '#22c55e', fontWeight: 700 }}>\u20B9{c.amount.toFixed(2)}</span></div>
              <div><span style={{ color: '#64748b' }}>UPI Ref:</span> <span style={{ color: '#94a3b8' }}>{c.upi_refund_ref || 'Pending'}</span></div>
              {c.delay_minutes > 0 && (
                <div><span style={{ color: '#64748b' }}>Delay:</span> <span style={{ color: '#f59e0b' }}>{c.delay_minutes} min</span></div>
              )}
              {c.is_duplicate && (
                <div><span style={{ color: '#f59e0b', fontWeight: 600 }}>Duplicate Skipped</span></div>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
