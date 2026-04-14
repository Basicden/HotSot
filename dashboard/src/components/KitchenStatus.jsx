import React from 'react';

/** Kitchen Status — load, capacity, utilization */
export default function KitchenStatus({ kitchens, expanded = false }) {
  return (
    <div style={{ background: '#1e293b', borderRadius: 12, padding: 20, border: '1px solid #334155' }}>
      <h3 style={{ margin: '0 0 16px 0', fontSize: 15, fontWeight: 700, color: '#8b5cf6' }}>Kitchen Status</h3>

      <div style={{ display: 'grid', gap: 12 }}>
        {kitchens.map(k => {
          const util = k.capacity > 0 ? k.current_load / k.capacity : 0;
          const utilColor = util > 0.85 ? '#ef4444' : util > 0.6 ? '#f59e0b' : '#22c55e';
          return (
            <div key={k.id} style={{
              background: '#0f172a', borderRadius: 10, padding: 16, border: '1px solid #334155',
            }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
                <div>
                  <span style={{ fontSize: 14, fontWeight: 700, color: '#e2e8f0' }}>{k.name}</span>
                  <span style={{
                    marginLeft: 8, fontSize: 10, background: k.is_active ? '#22c55e22' : '#ef444422',
                    color: k.is_active ? '#22c55e' : '#ef4444', padding: '2px 8px', borderRadius: 6, fontWeight: 700,
                  }}>{k.is_active ? 'ACTIVE' : 'OFFLINE'}</span>
                </div>
                <span style={{ fontSize: 24, fontWeight: 800, color: utilColor }}>{Math.round(util * 100)}%</span>
              </div>

              {/* Utilization bar */}
              <div style={{ background: '#334155', borderRadius: 4, height: 8, overflow: 'hidden', marginBottom: 8 }}>
                <div style={{ background: utilColor, height: '100%', width: `${util * 100}%`, borderRadius: 4, transition: 'width 0.5s' }} />
              </div>

              <div style={{ display: 'flex', gap: 16, fontSize: 11 }}>
                <span style={{ color: '#64748b' }}>Load: <span style={{ color: '#e2e8f0', fontWeight: 600 }}>{k.current_load}/{k.capacity}</span></span>
                <span style={{ color: '#64748b' }}>Location: <span style={{ color: '#94a3b8' }}>{k.location.lat.toFixed(4)}, {k.location.lng.toFixed(4)}</span></span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
