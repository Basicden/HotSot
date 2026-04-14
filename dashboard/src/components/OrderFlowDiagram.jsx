import React from 'react';
import { statusColor, statusLabel } from '../utils/colors';
import { V2_FLOW, TERMINAL_STATES } from '../data/mockData';

/** V2 Order Flow Diagram — 16-state lifecycle visualization */
export default function OrderFlowDiagram() {
  const flowStates = V2_FLOW;
  const termStates = TERMINAL_STATES;

  return (
    <div style={{ background: '#1e293b', borderRadius: 12, padding: 20, border: '1px solid #334155' }}>
      <h3 style={{ margin: '0 0 16px 0', fontSize: 15, fontWeight: 700, color: '#f97316' }}>V2 Order Flow (16 States)</h3>

      {/* Main happy path */}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, alignItems: 'center' }}>
        {flowStates.map((s, i) => (
          <React.Fragment key={s}>
            <div style={{
              background: statusColor(s) + '22',
              border: `1.5px solid ${statusColor(s)}`,
              borderRadius: 8,
              padding: '6px 12px',
              fontSize: 11,
              fontWeight: 600,
              color: statusColor(s),
              whiteSpace: 'nowrap',
            }}>
              {statusLabel(s)}
            </div>
            {i < flowStates.length - 1 && (
              <span style={{ color: '#475569', fontSize: 14 }}>&#8594;</span>
            )}
          </React.Fragment>
        ))}
      </div>

      {/* Terminal states */}
      <div style={{ marginTop: 14, display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
        <span style={{ fontSize: 11, color: '#64748b', fontWeight: 600 }}>Terminal:</span>
        {termStates.map(s => (
          <div key={s} style={{
            background: statusColor(s) + '22',
            border: `1.5px solid ${statusColor(s)}`,
            borderRadius: 8,
            padding: '4px 10px',
            fontSize: 10,
            fontWeight: 600,
            color: statusColor(s),
          }}>
            {statusLabel(s)}
          </div>
        ))}
      </div>

      {/* Branch arrows */}
      <div style={{ marginTop: 10, fontSize: 10, color: '#64748b', lineHeight: 1.8 }}>
        <div>ON_SHELF &#8594; EXPIRED (TTL exceeded)</div>
        <div>EXPIRED &#8594; REFUNDED (auto-compensation)</div>
        <div>Any pre-PICKED &#8594; CANCELLED / FAILED</div>
        <div>PAYMENT_CONFIRMED+ &#8594; REFUNDED</div>
      </div>
    </div>
  );
}
