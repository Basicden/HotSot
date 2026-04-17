import React, { useState, useEffect, useCallback } from 'react';
import { mockOrders, mockStats, mockCompensations, mockArrivals, mockKitchens, mockShelves } from './data/mockData';
import { statusColor, statusLabel, isTerminal, arrivalTypeIcon, compensationReasonLabel, refundTypeLabel } from './utils/colors';
import { V2_FLOW, TERMINAL_STATES } from './data/mockData';
import OrderFlowDiagram from './components/OrderFlowDiagram';
import StatsCards from './components/StatsCards';
import OrderTable from './components/OrderTable';
import CompensationPanel from './components/CompensationPanel';
import ArrivalPanel from './components/ArrivalPanel';
import ShelfGrid from './components/ShelfGrid';
import KitchenStatus from './components/KitchenStatus';
import Footer from './components/Footer';

const styles = {
  app: { minHeight: '100vh', background: 'linear-gradient(135deg, #0f172a 0%, #1e293b 100%)' },
  header: { padding: '20px 32px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', borderBottom: '1px solid #1e293b', background: 'rgba(15,23,42,0.9)', backdropFilter: 'blur(10px)', position: 'sticky', top: 0, zIndex: 50 },
  logo: { fontSize: 24, fontWeight: 800, color: '#f97316', letterSpacing: -1 },
  logoSub: { fontSize: 12, color: '#64748b', marginLeft: 8, fontWeight: 500 },
  badge: { fontSize: 11, background: '#f97316', color: '#fff', padding: '2px 8px', borderRadius: 10, marginLeft: 8, fontWeight: 700 },
  tabs: { display: 'flex', gap: 4, padding: '0 32px', background: 'rgba(15,23,42,0.6)', borderBottom: '1px solid #1e293b' },
  tab: (active) => ({ padding: '10px 18px', cursor: 'pointer', fontSize: 13, fontWeight: 600, color: active ? '#f97316' : '#94a3b8', borderBottom: active ? '2px solid #f97316' : '2px solid transparent', transition: 'all 0.2s' }),
  main: { padding: '24px 32px', maxWidth: 1440, margin: '0 auto' },
  row: { display: 'grid', gap: 20, marginBottom: 20 },
  clock: { fontSize: 13, color: '#94a3b8', fontWeight: 500 },
};

const TABS = ['Dashboard', 'Orders', 'Shelves', 'Compensations', 'Arrivals', 'Kitchens'];

export default function App() {
  const [tab, setTab] = useState('Dashboard');
  const [clock, setClock] = useState(new Date());

  useEffect(() => {
    const t = setInterval(() => setClock(new Date()), 1000);
    return () => clearInterval(t);
  }, []);

  const formatClock = (d) => d.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: true, timeZone: 'Asia/Kolkata' });

  return (
    <div style={styles.app}>
      <header style={styles.header}>
        <div style={{ display: 'flex', alignItems: 'center' }}>
          <span style={styles.logo}>HotSot</span>
          <span style={styles.logoSub}>Dashboard</span>
          <span style={styles.badge}>V2</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
          <span style={{ fontSize: 12, color: '#22c55e' }}>● System Online</span>
          <span style={styles.clock}>{formatClock(clock)} IST</span>
        </div>
      </header>

      <nav style={styles.tabs}>
        {TABS.map(t => (
          <div key={t} style={styles.tab(tab === t)} onClick={() => setTab(t)}>{t}</div>
        ))}
      </nav>

      <main style={styles.main}>
        {tab === 'Dashboard' && (
          <>
            <StatsCards stats={mockStats} />
            <div style={{ ...styles.row, gridTemplateColumns: '1fr 1fr' }}>
              <OrderFlowDiagram />
              <KitchenStatus kitchens={mockKitchens} />
            </div>
            <div style={{ ...styles.row, gridTemplateColumns: '1fr 1fr' }}>
              <ShelfGrid shelves={mockShelves} />
              <ArrivalPanel arrivals={mockArrivals} />
            </div>
            <CompensationPanel compensations={mockCompensations} />
          </>
        )}
        {tab === 'Orders' && <OrderTable orders={mockOrders} />}
        {tab === 'Shelves' && <ShelfGrid shelves={mockShelves} expanded />}
        {tab === 'Compensations' && <CompensationPanel compensations={mockCompensations} expanded />}
        {tab === 'Arrivals' && <ArrivalPanel arrivals={mockArrivals} expanded />}
        {tab === 'Kitchens' && <KitchenStatus kitchens={mockKitchens} expanded />}
      </main>

      <Footer />
    </div>
  );
}
