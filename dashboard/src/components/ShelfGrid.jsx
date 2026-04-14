import React from 'react';

/** Shelf Grid — visual shelf status with zones and TTL */
export default function ShelfGrid({ shelves, expanded = false }) {
  const zones = ['HOT', 'COLD', 'AMBIENT'];
  const zoneColor = { HOT: '#ef4444', COLD: '#3b82f6', AMBIENT: '#f59e0b' };
  const statusIcon = { AVAILABLE: '\u2B1C', OCCUPIED: '\uD83D\uDFE2', EXPIRED: '\uD83D\uDD34' };

  const grouped = {};
  zones.forEach(z => { grouped[z] = shelves.filter(s => s.zone === z); });

  return (
    <div style={{ background: '#1e293b', borderRadius: 12, padding: 20, border: '1px solid #334155' }}>
      <h3 style={{ margin: '0 0 16px 0', fontSize: 15, fontWeight: 700, color: '#10b981' }}>Shelf Status</h3>

      <div style={{ display: 'grid', gap: 16 }}>
        {zones.map(zone => (
          <div key={zone}>
            <div style={{ fontSize: 11, fontWeight: 700, color: zoneColor[zone], marginBottom: 8, textTransform: 'uppercase' }}>
              {zone} Zone {zone === 'HOT' ? '(15min)' : zone === 'COLD' ? '(30min)' : '(60min)'}
            </div>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              {grouped[zone]?.map(s => {
                const isExpired = s.status === 'EXPIRED';
                const isOccupied = s.status === 'OCCUPIED';
                return (
                  <div key={s.shelf_id} style={{
                    background: isExpired ? '#ef444422' : isOccupied ? '#10b98122' : '#334155',
                    border: `1.5px solid ${isExpired ? '#ef4444' : isOccupied ? '#10b981' : '#475569'}`,
                    borderRadius: 8,
                    padding: '10px 14px',
                    minWidth: 80,
                  }}>
                    <div style={{ fontSize: 12, fontWeight: 700, color: '#e2e8f0' }}>{s.shelf_id}</div>
                    <div style={{ fontSize: 10, color: isExpired ? '#ef4444' : isOccupied ? '#10b981' : '#64748b', fontWeight: 600 }}>
                      {s.status} {isOccupied && s.remaining_ttl ? `(${Math.round(s.remaining_ttl / 60)}m)` : ''}
                    </div>
                    {isOccupied && s.order_id && (
                      <div style={{ fontSize: 9, color: '#94a3b8', marginTop: 2 }}>{s.order_id}</div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        ))}
      </div>

      <div style={{ marginTop: 14, display: 'flex', gap: 12, fontSize: 10, color: '#64748b' }}>
        <span>\u2B1C Available</span>
        <span>\uD83D\uDFE2 Occupied</span>
        <span>\uD83D\uDD34 Expired</span>
      </div>
    </div>
  );
}
