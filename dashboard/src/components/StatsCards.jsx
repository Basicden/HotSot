import React from 'react';

/** Stats Cards — key metrics overview */
export default function StatsCards({ stats }) {
  const cards = [
    { label: 'Active Orders', value: stats.active_orders, color: '#f97316', icon: '\uD83D\uDD25' },
    { label: 'Picked Today', value: stats.picked_today, color: '#22c55e', icon: '\u2705' },
    { label: 'Expired Today', value: stats.expired_today, color: '#ef4444', icon: '\u23F0' },
    { label: 'Avg ETA', value: `${Math.round(stats.avg_eta_seconds / 60)}m`, color: '#3b82f6', icon: '\u23F1' },
    { label: 'Kitchen Load', value: `${Math.round(stats.kitchen_utilization * 100)}%`, color: '#8b5cf6', icon: '\uD83C\uDF73' },
    { label: 'Compensations', value: `\u20B9${stats.compensation_total_amount}`, color: '#f43f5e', icon: '\uD83D\uDCB8' },
  ];

  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 16, marginBottom: 20 }}>
      {cards.map(c => (
        <div key={c.label} style={{
          background: '#1e293b',
          borderRadius: 12,
          padding: '18px 20px',
          border: `1px solid ${c.color}33`,
          borderLeft: `4px solid ${c.color}`,
        }}>
          <div style={{ fontSize: 12, color: '#94a3b8', fontWeight: 600, marginBottom: 6 }}>{c.icon} {c.label}</div>
          <div style={{ fontSize: 28, fontWeight: 800, color: c.color }}>{c.value}</div>
        </div>
      ))}
    </div>
  );
}
