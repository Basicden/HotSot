import React from 'react';

/** Footer V2 — HotSot branding + tech stack info */
export default function Footer() {
  return (
    <footer style={{
      padding: '20px 32px',
      borderTop: '1px solid #1e293b',
      marginTop: 40,
      display: 'flex',
      justifyContent: 'space-between',
      alignItems: 'center',
      flexWrap: 'wrap',
      gap: 12,
    }}>
      <div>
        <span style={{ fontSize: 14, fontWeight: 800, color: '#f97316' }}>HotSot</span>
        <span style={{ fontSize: 11, color: '#64748b', marginLeft: 8 }}>V2.0.0</span>
        <span style={{ fontSize: 10, color: '#475569', marginLeft: 8 }}>Pickup-first Real-time Execution Engine</span>
      </div>
      <div style={{ display: 'flex', gap: 16, fontSize: 10, color: '#475569', flexWrap: 'wrap' }}>
        <span>FastAPI</span><span>PostgreSQL</span><span>Redis</span><span>Kafka</span><span>LightGBM</span>
        <span>Docker</span><span>Kubernetes</span><span>Terraform</span><span>React</span>
      </div>
      <div style={{ fontSize: 10, color: '#475569' }}>
        10 Microservices | 16 States | 6 Compensation Reasons | 3 Shelf Zones
      </div>
    </footer>
  );
}
