"""HotSot API Gateway — V2 Request Routing + Auth + Rate Limiting."""

import time
from typing import Dict, Any
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import httpx
import os

from app.routes.proxy import router as proxy_router, http_client

RATE_LIMIT_WINDOW = 60
RATE_LIMIT_MAX = 100
request_counts: Dict[str, Dict[str, Any]] = {}

app = FastAPI(title="HotSot API Gateway", version="2.0.0")

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
    """V2 Health check — includes all 10 services."""
    services_urls = {
        "order": os.getenv("ORDER_SERVICE_URL", "http://localhost:8001"),
        "kitchen": os.getenv("KITCHEN_SERVICE_URL", "http://localhost:8002"),
        "shelf": os.getenv("SHELF_SERVICE_URL", "http://localhost:8003"),
        "eta": os.getenv("ETA_SERVICE_URL", "http://localhost:8004"),
        "notification": os.getenv("NOTIFICATION_SERVICE_URL", "http://localhost:8005"),
        "realtime": os.getenv("REALTIME_SERVICE_URL", "http://localhost:8006"),
        "ml": os.getenv("ML_SERVICE_URL", "http://localhost:8007"),
        "arrival": os.getenv("ARRIVAL_SERVICE_URL", "http://localhost:8008"),
        "compensation": os.getenv("COMPENSATION_SERVICE_URL", "http://localhost:8009"),
    }
    services = {}
    for name, url in services_urls.items():
        try:
            resp = await http_client.get(f"{url}/health", timeout=3.0)
            services[name] = {"status": "healthy", "code": resp.status_code}
        except Exception:
            services[name] = {"status": "unhealthy"}
    return {"gateway": "healthy", "version": "2.0.0", "services": services}
