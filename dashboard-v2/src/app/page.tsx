"use client";

import { useState, useEffect, useCallback } from "react";

// ─── TYPES ───
interface Order {
  id: string;
  status: "CREATED" | "SLOT_RESERVED" | "PAYMENT_CONFIRMED" | "IN_PREP" | "READY" | "ON_SHELF" | "PICKED";
  eta: number;
  priority: number;
  shelf?: string;
  userArrived: boolean;
  item: string;
  paymentMethod: string;
}

interface ShelfSlot {
  id: string;
  status: "AVAILABLE" | "OCCUPIED" | "EXPIRED";
  orderId?: string;
  remainingTtl: number;
  maxTtl: number;
  zone: "HOT" | "COLD" | "AMBIENT";
}

interface Incident {
  id: string;
  type: "DELAY_SPIKE" | "SHELF_EXPIRY" | "KITCHEN_OVERLOAD" | "PAYMENT_FAILURE";
  message: string;
  severity: "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";
  acknowledged: boolean;
  timestamp: number;
}

// ─── MOCK DATA GENERATORS ───
const ITEMS = ["Butter Chicken Thali", "Paneer Tikka Wrap", "Biryani Bowl", "Masala Dosa Set", "Chole Bhature", "Dal Makhani Meal", "Vada Pav Combo", "Samosa Platter", "Rajma Chawal", "Dhokla Plate"];
const KITCHEN_IDS = ["KITCHEN-01", "KITCHEN-02"];

function generateId() { return `ORD-${Math.random().toString(36).substring(2, 8).toUpperCase()}`; }

function generateMockOrders(count: number): Order[] {
  const statuses: Order["status"][] = ["CREATED", "SLOT_RESERVED", "PAYMENT_CONFIRMED", "IN_PREP", "IN_PREP", "READY", "ON_SHELF"];
  return Array.from({ length: count }, (_, i) => ({
    id: generateId(),
    status: statuses[Math.floor(Math.random() * statuses.length)],
    eta: Math.floor(Math.random() * 600) + 120,
    priority: Math.floor(Math.random() * 100),
    shelf: Math.random() > 0.6 ? `A${Math.floor(Math.random() * 10) + 1}` : undefined,
    userArrived: Math.random() > 0.7,
    item: ITEMS[Math.floor(Math.random() * ITEMS.length)],
    paymentMethod: Math.random() > 0.15 ? "UPI" : "WALLET",
  })).sort((a, b) => b.priority - a.priority);
}

function generateMockShelves(): ShelfSlot[] {
  const zones: ShelfSlot["zone"][] = ["HOT", "HOT", "HOT", "HOT", "HOT", "COLD", "COLD", "COLD", "AMBIENT", "AMBIENT"];
  return Array.from({ length: 10 }, (_, i) => {
    const occupied = Math.random() > 0.4;
    const maxTtl = zones[i] === "HOT" ? 600 : zones[i] === "COLD" ? 900 : 1200;
    return {
      id: `A${i + 1}`,
      status: occupied ? "OCCUPIED" : "AVAILABLE",
      orderId: occupied ? generateId() : undefined,
      remainingTtl: occupied ? Math.floor(Math.random() * maxTtl) : 0,
      maxTtl,
      zone: zones[i],
    };
  });
}

function generateMockIncidents(): Incident[] {
  const types: Incident[] = [
    { id: "INC-001", type: "DELAY_SPIKE", message: "ETA drift detected — kitchen load spike +35%", severity: "HIGH", acknowledged: false, timestamp: Date.now() - 120000 },
    { id: "INC-002", type: "SHELF_EXPIRY", message: "Shelf A3 TTL expired — order needs reheat", severity: "MEDIUM", acknowledged: false, timestamp: Date.now() - 60000 },
    { id: "INC-003", type: "KITCHEN_OVERLOAD", message: "Queue depth 18 — approaching critical threshold", severity: "HIGH", acknowledged: false, timestamp: Date.now() - 30000 },
    { id: "INC-004", type: "PAYMENT_FAILURE", message: "UPI timeout for ORD-X7K2M — retry initiated", severity: "LOW", acknowledged: true, timestamp: Date.now() - 180000 },
  ];
  return types;
}

// ─── STATUS COLORS ───
function statusColor(status: string) {
  const map: Record<string, string> = {
    CREATED: "bg-blue-500/20 text-blue-400 border-blue-500/30",
    SLOT_RESERVED: "bg-cyan-500/20 text-cyan-400 border-cyan-500/30",
    PAYMENT_CONFIRMED: "bg-green-500/20 text-green-400 border-green-500/30",
    IN_PREP: "bg-amber-500/20 text-amber-400 border-amber-500/30",
    READY: "bg-orange-500/20 text-orange-400 border-orange-500/30",
    ON_SHELF: "bg-purple-500/20 text-purple-400 border-purple-500/30",
    PICKED: "bg-emerald-500/20 text-emerald-400 border-emerald-500/30",
  };
  return map[status] || "bg-zinc-500/20 text-zinc-400 border-zinc-500/30";
}

function severityColor(severity: string) {
  const map: Record<string, string> = { LOW: "text-blue-400", MEDIUM: "text-amber-400", HIGH: "text-orange-400", CRITICAL: "text-red-500 animate-pulse" };
  return map[severity] || "text-zinc-400";
}

function shelfTtlColor(remaining: number, max: number) {
  const pct = remaining / max;
  if (pct > 0.7) return "bg-emerald-500";
  if (pct > 0.3) return "bg-amber-500";
  return "bg-red-500 animate-pulse";
}

function formatSeconds(s: number) {
  const m = Math.floor(s / 60);
  const sec = s % 60;
  return `${m}:${sec.toString().padStart(2, "0")}`;
}

// ─── COMPONENTS ───

function MetricsBar({ metrics }: { metrics: { onTime: number; etaAccuracy: number; shelfExpiry: number; throughput: number } }) {
  return (
    <div className="grid grid-cols-4 gap-3">
      {[
        { label: "On-Time Pickup", value: `${metrics.onTime}%`, color: metrics.onTime >= 92 ? "text-emerald-400" : "text-red-400" },
        { label: "ETA Accuracy", value: `±${metrics.etaAccuracy}s`, color: metrics.etaAccuracy <= 30 ? "text-emerald-400" : "text-amber-400" },
        { label: "Shelf Expiry Rate", value: `${metrics.shelfExpiry}%`, color: metrics.shelfExpiry <= 5 ? "text-emerald-400" : "text-red-400" },
        { label: "Kitchen Throughput", value: `${metrics.throughput}/hr`, color: "text-blue-400" },
      ].map((m, i) => (
        <div key={i} className="bg-zinc-900/80 border border-zinc-800 rounded-xl p-4 backdrop-blur-sm">
          <div className="text-xs text-zinc-500 uppercase tracking-wider mb-1">{m.label}</div>
          <div className={`text-2xl font-bold ${m.color}`}>{m.value}</div>
        </div>
      ))}
    </div>
  );
}

function LiveOrdersPanel({ orders }: { orders: Order[] }) {
  return (
    <div className="bg-zinc-900/80 border border-zinc-800 rounded-xl p-4 backdrop-blur-sm">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-lg font-bold text-zinc-100 flex items-center gap-2">
          <span className="w-2 h-2 bg-emerald-500 rounded-full animate-pulse" />
          Live Orders
        </h2>
        <span className="text-xs text-zinc-500 bg-zinc-800 px-2 py-1 rounded-full">{orders.length} active</span>
      </div>
      <div className="space-y-2 max-h-[400px] overflow-y-auto pr-1">
        {orders.map((order) => (
          <div key={order.id} className={`flex items-center gap-3 p-3 rounded-lg border ${order.userArrived ? "border-orange-500/40 bg-orange-500/5" : "border-zinc-800 bg-zinc-800/40"} transition-all`}>
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 mb-1">
                <span className="text-sm font-mono font-bold text-zinc-200">{order.id}</span>
                <span className={`text-[10px] px-2 py-0.5 rounded-full border ${statusColor(order.status)}`}>{order.status.replace("_", " ")}</span>
                {order.userArrived && <span className="text-[10px] px-2 py-0.5 rounded-full bg-orange-500/20 text-orange-400 border border-orange-500/30 animate-pulse">ARRIVED</span>}
              </div>
              <div className="text-xs text-zinc-500">{order.item}</div>
            </div>
            <div className="text-right">
              <div className="text-sm font-mono text-amber-400">{formatSeconds(order.eta)}</div>
              <div className="text-[10px] text-zinc-600">ETA</div>
            </div>
            <div className="text-right">
              <div className="text-sm font-bold text-zinc-300">{order.priority}</div>
              <div className="text-[10px] text-zinc-600">Priority</div>
            </div>
            <div className="text-right">
              <span className={`text-[10px] px-1.5 py-0.5 rounded ${order.paymentMethod === "UPI" ? "bg-blue-500/20 text-blue-400" : "bg-purple-500/20 text-purple-400"}`}>
                {order.paymentMethod}
              </span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function KitchenStatusPanel({ queueDepth, isOverloaded, throughput, batchCount, isFestival, isMonsoon }: {
  queueDepth: number; isOverloaded: boolean; throughput: number; batchCount: number; isFestival: boolean; isMonsoon: boolean;
}) {
  return (
    <div className="bg-zinc-900/80 border border-zinc-800 rounded-xl p-4 backdrop-blur-sm">
      <h2 className="text-lg font-bold text-zinc-100 mb-4">Kitchen Status</h2>
      <div className="space-y-3">
        <div className="flex justify-between items-center">
          <span className="text-sm text-zinc-400">Queue Depth</span>
          <span className={`text-lg font-bold ${queueDepth > 15 ? "text-red-400" : queueDepth > 10 ? "text-amber-400" : "text-emerald-400"}`}>{queueDepth}</span>
        </div>
        <div className="w-full bg-zinc-800 rounded-full h-2">
          <div className={`h-2 rounded-full transition-all ${queueDepth > 15 ? "bg-red-500" : queueDepth > 10 ? "bg-amber-500" : "bg-emerald-500"}`} style={{ width: `${Math.min(queueDepth * 4, 100)}%` }} />
        </div>
        <div className="flex justify-between items-center">
          <span className="text-sm text-zinc-400">Throughput</span>
          <span className="text-lg font-bold text-blue-400">{throughput}/hr</span>
        </div>
        <div className="flex justify-between items-center">
          <span className="text-sm text-zinc-400">Suggested Batches</span>
          <span className="text-lg font-bold text-purple-400">{batchCount}</span>
        </div>
        <div className="flex justify-between items-center">
          <span className="text-sm text-zinc-400">Overload Status</span>
          <span className={`text-sm font-bold ${isOverloaded ? "text-red-400 animate-pulse" : "text-emerald-400"}`}>
            {isOverloaded ? "OVERLOADED" : "NORMAL"}
          </span>
        </div>
        <div className="border-t border-zinc-800 pt-3 mt-3 space-y-2">
          <div className="flex justify-between items-center">
            <span className="text-xs text-zinc-500">Festival Mode</span>
            <span className={`text-xs font-bold ${isFestival ? "text-orange-400 animate-pulse" : "text-zinc-600"}`}>{isFestival ? "ACTIVE" : "OFF"}</span>
          </div>
          <div className="flex justify-between items-center">
            <span className="text-xs text-zinc-500">Monsoon Mode</span>
            <span className={`text-xs font-bold ${isMonsoon ? "text-cyan-400" : "text-zinc-600"}`}>{isMonsoon ? "ACTIVE" : "OFF"}</span>
          </div>
        </div>
      </div>
    </div>
  );
}

function ShelfViewPanel({ shelves }: { shelves: ShelfSlot[] }) {
  return (
    <div className="bg-zinc-900/80 border border-zinc-800 rounded-xl p-4 backdrop-blur-sm">
      <h2 className="text-lg font-bold text-zinc-100 mb-4">Shelf View</h2>
      <div className="grid grid-cols-5 gap-2">
        {shelves.map((shelf) => (
          <div key={shelf.id} className={`relative rounded-lg border p-3 text-center transition-all ${
            shelf.status === "AVAILABLE" ? "border-zinc-700 bg-zinc-800/50" :
            shelf.status === "EXPIRED" ? "border-red-500/50 bg-red-500/10" :
            "border-zinc-600 bg-zinc-800/80"
          }`}>
            <div className="text-xs text-zinc-500 mb-1">{shelf.id}</div>
            <div className={`text-[10px] px-1 py-0.5 rounded mb-1 ${
              shelf.zone === "HOT" ? "bg-red-500/20 text-red-400" :
              shelf.zone === "COLD" ? "bg-blue-500/20 text-blue-400" :
              "bg-zinc-500/20 text-zinc-400"
            }`}>{shelf.zone}</div>
            {shelf.status === "OCCUPIED" ? (
              <>
                <div className="text-[10px] text-zinc-400 font-mono truncate">{shelf.orderId}</div>
                <div className="mt-1">
                  <div className="w-full bg-zinc-700 rounded-full h-1.5">
                    <div className={`h-1.5 rounded-full transition-all ${shelfTtlColor(shelf.remainingTtl, shelf.maxTtl)}`} style={{ width: `${(shelf.remainingTtl / shelf.maxTtl) * 100}%` }} />
                  </div>
                  <div className="text-[10px] text-zinc-500 mt-0.5">{formatSeconds(shelf.remainingTtl)}</div>
                </div>
              </>
            ) : shelf.status === "EXPIRED" ? (
              <div className="text-xs text-red-400 animate-pulse">EXPIRED</div>
            ) : (
              <div className="text-xs text-zinc-600">Empty</div>
            )}
          </div>
        ))}
      </div>
      <div className="mt-3 flex justify-between text-[10px] text-zinc-600">
        <span>HOT: 600s TTL</span>
        <span>COLD: 900s TTL</span>
        <span>AMBIENT: 1200s TTL</span>
      </div>
    </div>
  );
}

function ArrivalPanel({ orders }: { orders: Order[] }) {
  const arrived = orders.filter(o => o.userArrived);
  return (
    <div className="bg-zinc-900/80 border border-zinc-800 rounded-xl p-4 backdrop-blur-sm">
      <h2 className="text-lg font-bold text-zinc-100 mb-4 flex items-center gap-2">
        <span className="w-2 h-2 bg-orange-500 rounded-full animate-pulse" />
        Arrivals
      </h2>
      {arrived.length === 0 ? (
        <div className="text-sm text-zinc-600 text-center py-4">No users waiting</div>
      ) : (
        <div className="space-y-2">
          {arrived.map((order) => (
            <div key={order.id} className="flex items-center gap-3 p-2 rounded-lg bg-orange-500/10 border border-orange-500/30">
              <span className="w-3 h-3 bg-orange-500 rounded-full animate-pulse" />
              <div className="flex-1">
                <div className="text-sm font-mono text-zinc-200">{order.id}</div>
                <div className="text-xs text-zinc-500">{order.item}</div>
              </div>
              <div className="text-right">
                <div className="text-sm font-bold text-amber-400">{order.shelf || "—"}</div>
                <div className="text-[10px] text-zinc-600">Shelf</div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function IncidentPanel({ incidents, onAck }: { incidents: Incident[]; onAck: (id: string) => void }) {
  return (
    <div className="bg-zinc-900/80 border border-zinc-800 rounded-xl p-4 backdrop-blur-sm">
      <h2 className="text-lg font-bold text-zinc-100 mb-4">Incidents</h2>
      <div className="space-y-2 max-h-[250px] overflow-y-auto">
        {incidents.map((inc) => (
          <div key={inc.id} className={`p-3 rounded-lg border ${inc.acknowledged ? "border-zinc-800 bg-zinc-800/30 opacity-60" : "border-zinc-700 bg-zinc-800/50"}`}>
            <div className="flex items-start justify-between">
              <div className="flex-1">
                <div className="flex items-center gap-2 mb-1">
                  <span className={`text-[10px] px-1.5 py-0.5 rounded font-bold ${severityColor(inc.severity)}`}>{inc.severity}</span>
                  <span className="text-[10px] text-zinc-500">{inc.type.replace("_", " ")}</span>
                </div>
                <div className="text-xs text-zinc-300">{inc.message}</div>
              </div>
              {!inc.acknowledged && (
                <button onClick={() => onAck(inc.id)} className="ml-2 text-[10px] bg-zinc-700 hover:bg-zinc-600 text-zinc-300 px-2 py-1 rounded transition-colors">
                  ACK
                </button>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ─── MAIN PAGE ───
export default function HotSotDashboard() {
  const [currentTime, setCurrentTime] = useState(new Date());
  const [orders, setOrders] = useState<Order[]>(() => generateMockOrders(8));
  const [shelves, setShelves] = useState<ShelfSlot[]>(() => generateMockShelves());
  const [incidents, setIncidents] = useState<Incident[]>(() => generateMockIncidents());
  const [selectedKitchen, setSelectedKitchen] = useState("KITCHEN-01");
  const [isFestival, setIsFestival] = useState(false);
  const [isMonsoon, setIsMonsoon] = useState(false);
  const [metrics, setMetrics] = useState({ onTime: 94, etaAccuracy: 22, shelfExpiry: 3, throughput: 47 });

  // Live clock
  useEffect(() => {
    const timer = setInterval(() => setCurrentTime(new Date()), 1000);
    return () => clearInterval(timer);
  }, []);

  // Simulate real-time updates
  useEffect(() => {
    const timer = setInterval(() => {
      setOrders(prev => {
        const updated = prev.map(o => ({
          ...o,
          eta: Math.max(0, o.eta - Math.floor(Math.random() * 15)),
          priority: o.status === "IN_PREP" ? o.priority + Math.floor(Math.random() * 3) : o.priority,
        }));
        // Occasionally add new order
        if (Math.random() > 0.7 && updated.length < 15) {
          return [...updated, ...generateMockOrders(1)];
        }
        // Remove completed orders
        return updated.filter(o => o.eta > 0).slice(0, 15);
      });

      setShelves(prev => prev.map(s => s.status === "OCCUPIED" ? { ...s, remainingTtl: Math.max(0, s.remainingTtl - 5) } : s));

      setMetrics(prev => ({
        onTime: Math.min(99, Math.max(85, prev.onTime + (Math.random() > 0.5 ? 1 : -1))),
        etaAccuracy: Math.max(10, Math.min(45, prev.etaAccuracy + (Math.random() > 0.5 ? 1 : -1))),
        shelfExpiry: Math.max(0, Math.min(15, prev.shelfExpiry + (Math.random() > 0.7 ? 1 : 0))),
        throughput: Math.max(20, Math.min(80, prev.throughput + (Math.random() > 0.5 ? 2 : -1))),
      }));
    }, 3000);
    return () => clearInterval(timer);
  }, []);

  const handleAck = useCallback((id: string) => {
    setIncidents(prev => prev.map(i => i.id === id ? { ...i, acknowledged: true } : i));
  }, []);

  const queueDepth = orders.filter(o => o.status === "IN_PREP").length;
  const isOverloaded = queueDepth > 12;

  return (
    <div className="min-h-screen bg-zinc-950 text-zinc-100">
      {/* Header */}
      <header className="border-b border-zinc-800 bg-zinc-900/90 backdrop-blur-md sticky top-0 z-50">
        <div className="max-w-[1600px] mx-auto px-4 py-3 flex items-center justify-between">
          <div className="flex items-center gap-4">
            <div className="flex items-center gap-2">
              <div className="w-8 h-8 bg-gradient-to-br from-orange-500 to-red-600 rounded-lg flex items-center justify-center text-white font-black text-sm">H</div>
              <div>
                <h1 className="text-lg font-bold tracking-tight">HotSot <span className="text-orange-400">Kitchen OS</span></h1>
                <p className="text-[10px] text-zinc-500 -mt-0.5">Real-time Pickup Commerce</p>
              </div>
            </div>
            <div className="flex items-center gap-2 ml-4">
              <select value={selectedKitchen} onChange={e => setSelectedKitchen(e.target.value)}
                className="bg-zinc-800 border border-zinc-700 text-sm text-zinc-300 rounded-lg px-3 py-1.5 focus:outline-none focus:ring-1 focus:ring-orange-500">
                {KITCHEN_IDS.map(k => <option key={k} value={k}>{k}</option>)}
              </select>
            </div>
          </div>
          <div className="flex items-center gap-4">
            <div className="flex items-center gap-3">
              <button onClick={() => setIsFestival(!isFestival)}
                className={`text-xs px-3 py-1.5 rounded-lg border transition-all ${isFestival ? "border-orange-500/50 bg-orange-500/10 text-orange-400" : "border-zinc-700 text-zinc-600"}`}>
                Festival Mode
              </button>
              <button onClick={() => setIsMonsoon(!isMonsoon)}
                className={`text-xs px-3 py-1.5 rounded-lg border transition-all ${isMonsoon ? "border-cyan-500/50 bg-cyan-500/10 text-cyan-400" : "border-zinc-700 text-zinc-600"}`}>
                Monsoon Mode
              </button>
            </div>
            <div className="text-right">
              <div className="text-sm font-mono text-zinc-300">{currentTime.toLocaleTimeString("en-IN", { hour12: true })}</div>
              <div className="text-[10px] text-zinc-600">{currentTime.toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" })}</div>
            </div>
          </div>
        </div>
      </header>

      <main className="max-w-[1600px] mx-auto p-4 space-y-4">
        {/* Metrics Bar */}
        <MetricsBar metrics={metrics} />

        {/* Main Grid */}
        <div className="grid grid-cols-12 gap-4">
          {/* Live Orders - 5 cols */}
          <div className="col-span-5">
            <LiveOrdersPanel orders={orders} />
          </div>

          {/* Right Side - 7 cols */}
          <div className="col-span-7 space-y-4">
            {/* Kitchen + Shelf Row */}
            <div className="grid grid-cols-2 gap-4">
              <KitchenStatusPanel queueDepth={queueDepth} isOverloaded={isOverloaded} throughput={metrics.throughput} batchCount={Math.floor(queueDepth / 3)} isFestival={isFestival} isMonsoon={isMonsoon} />
              <ShelfViewPanel shelves={shelves} />
            </div>

            {/* Arrivals + Incidents Row */}
            <div className="grid grid-cols-2 gap-4">
              <ArrivalPanel orders={orders} />
              <IncidentPanel incidents={incidents} onAck={handleAck} />
            </div>
          </div>
        </div>

        {/* Order Flow Diagram */}
        <div className="bg-zinc-900/80 border border-zinc-800 rounded-xl p-4 backdrop-blur-sm">
          <h2 className="text-sm font-bold text-zinc-400 mb-3">Order Flow</h2>
          <div className="flex items-center justify-center gap-1 text-xs">
            {["CREATED", "SLOT_RESERVED", "PAYMENT_CONFIRMED", "IN_PREP", "READY", "ON_SHELF", "PICKED"].map((step, i) => (
              <div key={step} className="flex items-center gap-1">
                <div className={`px-3 py-1.5 rounded-lg border ${statusColor(step)} text-[10px] font-bold`}>{step.replace("_", " ")}</div>
                {i < 6 && <span className="text-zinc-700">→</span>}
              </div>
            ))}
          </div>
        </div>

        {/* Footer */}
        <footer className="text-center text-[10px] text-zinc-700 pb-4">
          HotSot Kitchen OS v1.0 — India-First Pickup Commerce Operating System — {selectedKitchen}
        </footer>
      </main>
    </div>
  );
}
