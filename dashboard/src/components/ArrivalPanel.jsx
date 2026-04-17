import React from 'react';
import { arrivalTypeIcon } from '../utils/colors';

/** Arrival Panel — GPS/QR detection, priority boosts, dedup */
export default function ArrivalPanel({ arrivals, expanded = false }) {
  return (
    <div style={{ background: '#1e293b', borderRadius: 12, padding: 20, border: '1px solid #334155' }}>
      <h3 style={{ margin: '0 0 16px 0', fontSize: 15, fontWeight: 700, color: '#06b6d4' }}>Arrival Detection</h3>

      {arrivals.length === 0 ? (
        <div style={{ color: '#64748b', fontSize: 13, textAlign: 'center', padding: 20 }}>No arrivals detected</div>
      ) : (
        <div style={{ display: 'grid', gap: 10 }}>
          {arrivals.map(a => (
            <div key={a.arrival_id} style={{
              background: '#0f172a', borderRadius: 10, padding: 14, border: '1px solid #334155',
            }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <span style={{ fontSize: 18 }}>{arrivalTypeIcon(a.arrival_type)}</span>
                  <span style={{ fontSize: 13, fontWeight: 700, color: '#e2e8f0' }}>{a.order_id}</span>
                </div>
                <div style={{ display: 'flex', gap: 6 }}>
                  <span style={{
                    background: a.strength === 'HARD' ? '#22c55e22' : '#f59e0b22',
                    color: a.strength === 'HARD' ? '#22c55e' : '#f59e0b',
                    padding: '2px 8px', borderRadius: 6, fontSize: 10, fontWeight: 700,
                  }}>{a.strength}</span>
                  <span style={{
                    background: a.priority_boosted ? '#f9731622' : '#64748b22',
                    color: a.priority_boosted ? '#f97316' : '#64748b',
                    padding: '2px 8px', borderRadius: 6, fontSize: 10, fontWeight: 700,
                  }}>{a.priority_boosted ? `+${a.boost_amount} Boost` : 'No Boost'}</span>
                </div>
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 6, fontSize: 11 }}>
                <div><span style={{ color: '#64748b' }}>Type:</span> <span style={{ color: '#94a3b8' }}>{a.arrival_type}</span></div>
                <div><span style={{ color: '#64748b' }}>Distance:</span> <span style={{ color: '#94a3b8' }}>{a.distance_m?.toFixed(1) || '-'}m</span></div>
                <div><span style={{ color: '#64748b' }}>Kitchen:</span> <span style={{ color: '#94a3b8' }}>{a.kitchen_id.replace('k_', '')}</span></div>
                <div><span style={{ color: '#64748b' }}>Lat:</span> <span style={{ color: '#94a3b8' }}>{a.latitude?.toFixed(4)}</span></div>
                <div><span style={{ color: '#64748b' }}>Lng:</span> <span style={{ color: '#94a3b8' }}>{a.longitude?.toFixed(4)}</span></div>
                <div><span style={{ color: '#64748b' }}>At:</span> <span style={{ color: '#94a3b8' }}>{new Date(a.detected_at).toLocaleTimeString('en-IN')}</span></div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
