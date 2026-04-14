import React, { useState } from 'react';
import { statusColor, statusLabel, isTerminal } from '../utils/colors';

/** Order Table — all orders with V2 states, arrival type, priority */
export default function OrderTable({ orders }) {
  const [filter, setFilter] = useState('ALL');

  const filtered = filter === 'ALL' ? orders : filter === 'ACTIVE'
    ? orders.filter(o => !isTerminal(o.status))
    : orders.filter(o => isTerminal(o.status));

  return (
    <div style={{ background: '#1e293b', borderRadius: 12, padding: 20, border: '1px solid #334155' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <h3 style={{ margin: 0, fontSize: 15, fontWeight: 700, color: '#f97316' }}>Orders (V2)</h3>
        <div style={{ display: 'flex', gap: 6 }}>
          {['ALL', 'ACTIVE', 'TERMINAL'].map(f => (
            <button key={f} onClick={() => setFilter(f)} style={{
              padding: '4px 12px', fontSize: 11, fontWeight: 600, borderRadius: 6, border: 'none', cursor: 'pointer',
              background: filter === f ? '#f97316' : '#334155', color: filter === f ? '#fff' : '#94a3b8',
            }}>{f}</button>
          ))}
        </div>
      </div>

      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
          <thead>
            <tr style={{ borderBottom: '2px solid #334155' }}>
              {['Order', 'Kitchen', 'Status', 'Queue', 'Priority', 'Tier', 'ETA', 'Shelf', 'Arrival', 'Amount'].map(h => (
                <th key={h} style={{ padding: '8px 10px', textAlign: 'left', color: '#64748b', fontWeight: 700, fontSize: 11 }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {filtered.map(o => (
              <tr key={o.order_id} style={{ borderBottom: '1px solid #1e293b' }}>
                <td style={{ padding: '8px 10px', color: '#e2e8f0', fontWeight: 600 }}>{o.order_id}</td>
                <td style={{ padding: '8px 10px', color: '#94a3b8' }}>{o.kitchen_id.replace('k_', '')}</td>
                <td style={{ padding: '8px 10px' }}>
                  <span style={{
                    background: statusColor(o.status) + '22', color: statusColor(o.status),
                    padding: '2px 8px', borderRadius: 6, fontSize: 11, fontWeight: 700,
                  }}>{statusLabel(o.status)}</span>
                </td>
                <td style={{ padding: '8px 10px', color: '#94a3b8' }}>{o.queue_type}</td>
                <td style={{ padding: '8px 10px', color: o.priority_score >= 70 ? '#f97316' : o.priority_score >= 40 ? '#f59e0b' : '#64748b', fontWeight: 600 }}>{o.priority_score?.toFixed(0)}</td>
                <td style={{ padding: '8px 10px' }}>
                  <span style={{ fontSize: 10, fontWeight: 700, color: o.user_tier === 'VIP' ? '#f97316' : o.user_tier === 'PRO' ? '#3b82f6' : o.user_tier === 'PLUS' ? '#8b5cf6' : '#64748b' }}>{o.user_tier}</span>
                </td>
                <td style={{ padding: '8px 10px', color: '#94a3b8' }}>{o.eta_seconds ? `${Math.round(o.eta_seconds / 60)}m` : '-'}</td>
                <td style={{ padding: '8px 10px', color: '#94a3b8' }}>{o.shelf_id || '-'} {o.shelf_zone ? `(${o.shelf_zone})` : ''}</td>
                <td style={{ padding: '8px 10px', color: '#06b6d4' }}>{o.handoff_method || (o.arrived_at ? 'GPS' : '-')}</td>
                <td style={{ padding: '8px 10px', color: '#22c55e', fontWeight: 600 }}>\u20B9{o.total_amount}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
