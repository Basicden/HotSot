"""HotSot API Gateway — V2 Request Routing + Auth + Rate Limiting + Circuit Breaker.

V2: Adds per-service circuit breaker that opens when a downstream service
returns 5xx at >30% rate over a 60s window. When open, the gateway
returns 503 Service Unavailable with a Retry-After header.
"""

import time
from typing import Dict, Any
from collections import defaultdict
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import httpx
import os

from app.routes.proxy import router as proxy_router, http_client

RATE_LIMIT_WINDOW = 60
RATE_LIMIT_MAX = 100
request_counts: Dict[str, Dict[str, Any]] = {}

# ─── Circuit Breaker ─────────────────────────────────────────
class CircuitBreaker:
    """Per-service circuit breaker.

    Tracks success/failure ratio over a rolling window. When the 5xx
    rate exceeds the threshold for a configurable observation window,
    the circuit opens and all requests to that service return 503.

    The circuit half-opens after a cooldown period, allowing a single
    probe request. If it succeeds, the circuit closes. If it fails,
    the circuit stays open.
    """

    def __init__(
        self,
        failure_threshold: float = 0.3,
        window_seconds: int = 60,
        cooldown_seconds: int = 30,
        min_requests: int = 5,
    ):
        self.failure_threshold = failure_threshold
        self.window_seconds = window_seconds
        self.cooldown_seconds = cooldown_seconds
        self.min_requests = min_requests
        # service → list of (timestamp, is_success)
        self._events: Dict[str, list] = defaultdict(list)
        # service → state: "closed", "open", "half_open"
        self._state: Dict[str, str] = defaultdict(lambda: "closed")
        # service → when the circuit was opened
        self._opened_at: Dict[str, float] = {}

    def record(self, service: str, success: bool):
        """Record a request outcome."""
        now = time.time()
        self._events[service].append((now, success))
        # Prune old events
        cutoff = now - self.window_seconds
        self._events[service] = [
            (t, s) for t, s in self._events[service] if t > cutoff
        ]

        # Check if circuit should open
        if self._state[service] == "closed":
            events = self._events[service]
            if len(events) >= self.min_requests:
                failures = sum(1 for _, s in events if not s)
                failure_rate = failures / len(events)
                if failure_rate >= self.failure_threshold:
                    self._state[service] = "open"
                    self._opened_at[service] = now

        # If half_open and request succeeded, close circuit
        elif self._state[service] == "half_open":
            if success:
                self._state[service] = "closed"
                self._events[service] = []
            else:
                self._state[service] = "open"
                self._opened_at[service] = now

    def is_open(self, service: str) -> bool:
        """Check if the circuit is open for a service."""
        state = self._state[service]

        if state == "closed":
            return False

        if state == "open":
            # Check cooldown
            elapsed = time.time() - self._opened_at.get(service, 0)
            if elapsed >= self.cooldown_seconds:
                # Transition to half_open
                self._state[service] = "half_open"
                return False  # Allow one probe request
            return True

        if state == "half_open":
            return False  # Allow the probe request

        return False

    def get_state(self, service: str) -> str:
        return self._state[service]

    def get_all_states(self) -> Dict[str, str]:
        return dict(self._state)


circuit_breaker = CircuitBreaker(
    failure_threshold=0.3,   # Open at 30% 5xx rate
    window_seconds=60,       # Over 60 second window
    cooldown_seconds=30,     # Half-open after 30 seconds
    min_requests=5,          # Need at least 5 requests to evaluate
)


app = FastAPI(title="HotSot API Gateway", version="2.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    client_ip = request.client.host
    now = time.time()
    if client_ip not in request_counts:
        request_counts[client_ip] = {"count": 0, "window_start": now}
    record = request_counts[client_ip]
    if now - record["window_start"] > RATE_LIMIT_WINDOW:
        record["count"] = 0
        record["window_start"] = now
    record["count"] += 1
    if record["count"] > RATE_LIMIT_MAX:
        return JSONResponse(status_code=429, content={"detail": "Rate limit exceeded."})
    start = time.time()
    response = await call_next(request)
    response.headers["X-Response-Time"] = f"{time.time() - start:.3f}s"
    return response


# Include proxy routes
app.include_router(proxy_router)


@app.get("/health")
async def gateway_health():
    """V2 Health check — includes all 10 services + circuit breaker state."""
    services_urls = {
        "order": os.getenv("ORDER_SERVICE_URL", "http://localhost:8001"),
        "kitchen": os.getenv("KITCHEN_SERVICE_URL", "http://localhost:8002"),
        "shelf": os.getenv("SHELF_SERVICE_URL", "http://localhost:8003"),
        "eta": os.getenv("ETA_SERVICE_URL", "http://localhost:8004"),
        "notification": os.getenv("NOTIFICATION_SERVICE_URL", "http://localhost:8005"),
        "realtime": os.getenv("REALTIME_SERVICE_URL", "http://localhost:8006"),
        "ml": os.getenv("ML_SERVICE_URL", "http://localhost:8007"),
        "arrival": os.getenv("ARRIVAL_SERVICE_URL", "http://localhost:8008"),
        "compensation": os.getenv("COMPENSATION_SERVICE_URL", "http://localhost:8010"),
    }
    services = {}
    for name, url in services_urls.items():
        # Check circuit breaker first
        if circuit_breaker.is_open(name):
            services[name] = {
                "status": "circuit_open",
                "circuit_state": circuit_breaker.get_state(name),
            }
            continue
        try:
            resp = await http_client.get(f"{url}/health", timeout=3.0)
            success = resp.status_code < 500
            circuit_breaker.record(name, success)
            services[name] = {
                "status": "healthy",
                "code": resp.status_code,
                "circuit_state": circuit_breaker.get_state(name),
            }
        except Exception:
            circuit_breaker.record(name, False)
            services[name] = {
                "status": "unhealthy",
                "circuit_state": circuit_breaker.get_state(name),
            }

    return {
        "gateway": "healthy",
        "version": "2.1.0",
        "services": services,
        "circuit_breakers": circuit_breaker.get_all_states(),
    }
