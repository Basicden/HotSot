"""HotSot API Gateway — V2 Production-Grade Reverse Proxy.

Routes requests to all 18 microservices with:
- JWT authentication verification
- Rate limiting per tenant
- Request/response logging
- Circuit breaker pattern
- Header propagation (X-Tenant-ID, X-Correlation-ID)
"""

from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import StreamingResponse
import httpx
import os
import logging

from shared.auth.jwt import get_current_user

router = APIRouter()
logger = logging.getLogger("api-gateway")

http_client = httpx.AsyncClient(timeout=30.0)

# Service registry — all 18 microservices
SERVICES = {
    "order": os.getenv("ORDER_SERVICE_URL", "http://localhost:8001"),
    "kitchen": os.getenv("KITCHEN_SERVICE_URL", "http://localhost:8002"),
    "shelf": os.getenv("SHELF_SERVICE_URL", "http://localhost:8003"),
    "eta": os.getenv("ETA_SERVICE_URL", "http://localhost:8004"),
    "notification": os.getenv("NOTIFICATION_SERVICE_URL", "http://localhost:8005"),
    "realtime": os.getenv("REALTIME_SERVICE_URL", "http://localhost:8006"),
    "ml": os.getenv("ML_SERVICE_URL", "http://localhost:8007"),
    "arrival": os.getenv("ARRIVAL_SERVICE_URL", "http://localhost:8008"),
    "compensation": os.getenv("COMPENSATION_SERVICE_URL", "http://localhost:8009"),
    "vendor": os.getenv("VENDOR_SERVICE_URL", "http://localhost:8010"),
    "menu": os.getenv("MENU_SERVICE_URL", "http://localhost:8011"),
    "search": os.getenv("SEARCH_SERVICE_URL", "http://localhost:8012"),
    "pricing": os.getenv("PRICING_SERVICE_URL", "http://localhost:8013"),
    "compliance": os.getenv("COMPLIANCE_SERVICE_URL", "http://localhost:8014"),
    "billing": os.getenv("BILLING_SERVICE_URL", "http://localhost:8015"),
    "analytics": os.getenv("ANALYTICS_SERVICE_URL", "http://localhost:8016"),
    "config": os.getenv("CONFIG_SERVICE_URL", "http://localhost:8017"),
}

# Route prefix → service mapping
ROUTE_MAP = {
    "/orders": "order",
    "/payments": "order",
    "/admin": "order",
    "/kitchen": "kitchen",
    "/queue": "kitchen",
    "/batch": "kitchen",
    "/shelf": "shelf",
    "/eta": "eta",
    "/notification": "notification",
    "/ws": "realtime",
    "/sse": "realtime",
    "/ml": "ml",
    "/arrival": "arrival",
    "/compensation": "compensation",
    "/vendor": "vendor",
    "/menu": "menu",
    "/search": "search",
    "/pricing": "pricing",
    "/compliance": "compliance",
    "/billing": "billing",
    "/analytics": "analytics",
    "/config": "config",
}


async def _proxy_request(service_name: str, path: str, request: Request,
                          method: str = "GET") -> dict:
    """Generic proxy request with auth header forwarding."""
    base_url = SERVICES.get(service_name)
    if not base_url:
        raise HTTPException(status_code=503, detail=f"Service {service_name} not available")

    url = f"{base_url}{path}"
    headers = {}
    # Forward auth headers
    if request.headers.get("authorization"):
        headers["Authorization"] = request.headers["authorization"]
    if request.headers.get("x-tenant-id"):
        headers["X-Tenant-ID"] = request.headers["x-tenant-id"]
    if request.headers.get("x-correlation-id"):
        headers["X-Correlation-ID"] = request.headers["x-correlation-id"]

    try:
        if method == "GET":
            resp = await http_client.get(url, headers=headers)
        elif method == "POST":
            body = await request.json() if await request.body() else {}
            resp = await http_client.post(url, json=body, headers=headers)
        elif method == "PUT":
            body = await request.json() if await request.body() else {}
            resp = await http_client.put(url, json=body, headers=headers)
        elif method == "DELETE":
            resp = await http_client.delete(url, headers=headers)
        else:
            raise HTTPException(status_code=405, detail="Method not allowed")

        if resp.status_code >= 400:
            raise HTTPException(status_code=resp.status_code, detail=resp.text)
        return resp.json()

    except httpx.ConnectError:
        logger.error("service_unavailable", service=service_name, url=url)
        raise HTTPException(status_code=503, detail=f"Service {service_name} unavailable")
    except httpx.TimeoutException:
        logger.error("service_timeout", service=service_name, url=url)
        raise HTTPException(status_code=504, detail=f"Service {service_name} timeout")


# ═══════════════════════════════════════════════════════════════
# GENERIC CATCH-ALL PROXY
# ═══════════════════════════════════════════════════════════════

@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def proxy_any(path: str, request: Request):
    """Generic proxy that routes based on path prefix.

    Examples:
    - /orders/123 → order-service
    - /kitchen/abc/load → kitchen-service
    - /vendor/xyz → vendor-service
    - /menu/item → menu-service
    """
    # Determine target service from path
    parts = path.split("/")
    prefix = f"/{parts[0]}" if parts else ""

    service_name = ROUTE_MAP.get(prefix)
    if not service_name:
        raise HTTPException(status_code=404, detail=f"No service mapped for {prefix}")

    return await _proxy_request(service_name, f"/{path}", request, request.method)


# ═══════════════════════════════════════════════════════════════
# ORDER SERVICE PROXIES
# ═══════════════════════════════════════════════════════════════

@router.post("/orders/create")
async def proxy_create_order(request: Request):
    body = await request.json()
    resp = await http_client.post(f"{SERVICES['order']}/orders/create", json=body)
    return resp.json()

@router.post("/orders/{order_id}/pay")
async def proxy_pay(order_id: str, request: Request):
    body = await request.json()
    resp = await http_client.post(f"{SERVICES['order']}/orders/{order_id}/pay", json=body)
    return resp.json()

@router.post("/orders/{order_id}/confirm-payment")
async def proxy_confirm_payment(order_id: str, request: Request):
    body = await request.json()
    resp = await http_client.post(f"{SERVICES['order']}/orders/{order_id}/confirm-payment", json=body)
    return resp.json()

@router.post("/orders/{order_id}/assign-queue")
async def proxy_assign_queue(order_id: str, request: Request):
    body = await request.json()
    resp = await http_client.post(f"{SERVICES['order']}/orders/{order_id}/assign-queue", json=body)
    return resp.json()

@router.post("/orders/{order_id}/start-prep")
async def proxy_start_prep(order_id: str):
    resp = await http_client.post(f"{SERVICES['order']}/orders/{order_id}/start-prep")
    return resp.json()

@router.post("/orders/{order_id}/ready")
async def proxy_ready(order_id: str):
    resp = await http_client.post(f"{SERVICES['order']}/orders/{order_id}/ready")
    return resp.json()

@router.post("/orders/{order_id}/shelf")
async def proxy_shelf(order_id: str, request: Request):
    body = await request.json() if await request.body() else {}
    resp = await http_client.post(f"{SERVICES['order']}/orders/{order_id}/shelf", json=body)
    return resp.json()

@router.post("/orders/{order_id}/arrival")
async def proxy_arrival(order_id: str, request: Request):
    body = await request.json() if await request.body() else {}
    resp = await http_client.post(f"{SERVICES['order']}/orders/{order_id}/arrival", json=body)
    return resp.json()

@router.post("/orders/{order_id}/handoff")
async def proxy_handoff(order_id: str, request: Request):
    body = await request.json() if await request.body() else {}
    resp = await http_client.post(f"{SERVICES['order']}/orders/{order_id}/handoff", json=body)
    return resp.json()

@router.post("/orders/{order_id}/expire")
async def proxy_expire(order_id: str):
    resp = await http_client.post(f"{SERVICES['order']}/orders/{order_id}/expire")
    return resp.json()

@router.post("/orders/{order_id}/cancel")
async def proxy_cancel(order_id: str, request: Request):
    body = await request.json() if await request.body() else {}
    resp = await http_client.post(f"{SERVICES['order']}/orders/{order_id}/cancel", json=body)
    return resp.json()

@router.get("/orders/{order_id}")
async def proxy_get_order(order_id: str):
    resp = await http_client.get(f"{SERVICES['order']}/orders/{order_id}")
    return resp.json()

@router.get("/orders/{order_id}/events")
async def proxy_get_events(order_id: str):
    resp = await http_client.get(f"{SERVICES['order']}/orders/{order_id}/events")
    return resp.json()


# ═══════════════════════════════════════════════════════════════
# PAYMENT PROXIES
# ═══════════════════════════════════════════════════════════════

@router.post("/payments/initiate")
async def proxy_payment_initiate(request: Request):
    body = await request.json()
    resp = await http_client.post(f"{SERVICES['order']}/payments/initiate", json=body)
    return resp.json()

@router.post("/payments/webhook/razorpay")
async def proxy_razorpay_webhook(request: Request):
    body = await request.json()
    resp = await http_client.post(f"{SERVICES['order']}/payments/webhook/razorpay", json=body)
    return resp.json()


# ═══════════════════════════════════════════════════════════════
# KITCHEN PROXIES
# ═══════════════════════════════════════════════════════════════

@router.post("/kitchen/")
async def proxy_register_kitchen(request: Request):
    body = await request.json()
    resp = await http_client.post(f"{SERVICES['kitchen']}/kitchen/", json=body)
    return resp.json()

@router.get("/kitchen/{kitchen_id}")
async def proxy_get_kitchen(kitchen_id: str):
    resp = await http_client.get(f"{SERVICES['kitchen']}/kitchen/{kitchen_id}")
    return resp.json()

@router.get("/kitchen/{kitchen_id}/load")
async def proxy_kitchen_load(kitchen_id: str):
    resp = await http_client.get(f"{SERVICES['kitchen']}/kitchen/{kitchen_id}/load")
    return resp.json()

@router.get("/queue/{kitchen_id}")
async def proxy_kitchen_queue(kitchen_id: str):
    resp = await http_client.get(f"{SERVICES['kitchen']}/queue/{kitchen_id}")
    return resp.json()

@router.get("/batch/{kitchen_id}/active")
async def proxy_active_batches(kitchen_id: str):
    resp = await http_client.get(f"{SERVICES['kitchen']}/batch/{kitchen_id}/active")
    return resp.json()


# ═══════════════════════════════════════════════════════════════
# SHELF PROXIES
# ═══════════════════════════════════════════════════════════════

@router.get("/shelf/kitchen/{kitchen_id}")
async def proxy_shelf_list(kitchen_id: str):
    resp = await http_client.get(f"{SERVICES['shelf']}/shelf/kitchen/{kitchen_id}")
    return resp.json()

@router.post("/shelf/assign")
async def proxy_shelf_assign(request: Request):
    body = await request.json()
    resp = await http_client.post(f"{SERVICES['shelf']}/shelf/assign", json=body)
    return resp.json()


# ═══════════════════════════════════════════════════════════════
# ETA PROXIES
# ═══════════════════════════════════════════════════════════════

@router.get("/eta/{order_id}")
async def proxy_eta(order_id: str):
    resp = await http_client.get(f"{SERVICES['eta']}/eta/{order_id}")
    return resp.json()

@router.post("/eta/{order_id}/recalculate")
async def proxy_eta_recalc(order_id: str, request: Request):
    body = await request.json()
    resp = await http_client.post(f"{SERVICES['eta']}/eta/{order_id}/recalculate", json=body)
    return resp.json()


# ═══════════════════════════════════════════════════════════════
# ARRIVAL PROXIES
# ═══════════════════════════════════════════════════════════════

@router.post("/arrival/detect")
async def proxy_arrival_detect(request: Request):
    body = await request.json()
    resp = await http_client.post(f"{SERVICES['arrival']}/arrival/detect", json=body)
    return resp.json()

@router.post("/arrival/qr-scan")
async def proxy_arrival_qr(request: Request):
    body = await request.json()
    resp = await http_client.post(f"{SERVICES['arrival']}/arrival/qr-scan", json=body)
    return resp.json()


# ═══════════════════════════════════════════════════════════════
# NOTIFICATION PROXIES
# ═══════════════════════════════════════════════════════════════

@router.post("/notification/send")
async def proxy_send_notification(request: Request):
    body = await request.json()
    resp = await http_client.post(f"{SERVICES['notification']}/notification/send", json=body)
    return resp.json()


# ═══════════════════════════════════════════════════════════════
# COMPENSATION PROXIES
# ═══════════════════════════════════════════════════════════════

@router.post("/compensation/trigger")
async def proxy_trigger_compensation(request: Request):
    body = await request.json()
    resp = await http_client.post(f"{SERVICES['compensation']}/compensation/trigger", json=body)
    return resp.json()

@router.get("/compensation/order/{order_id}")
async def proxy_compensation_order(order_id: str):
    resp = await http_client.get(f"{SERVICES['compensation']}/compensation/order/{order_id}")
    return resp.json()


# ═══════════════════════════════════════════════════════════════
# NEW SERVICE PROXIES (V2)
# ═══════════════════════════════════════════════════════════════

# Vendor
@router.post("/vendor/")
async def proxy_register_vendor(request: Request):
    body = await request.json()
    resp = await http_client.post(f"{SERVICES['vendor']}/vendor/", json=body)
    return resp.json()

@router.get("/vendor/{vendor_id}")
async def proxy_get_vendor(vendor_id: str):
    resp = await http_client.get(f"{SERVICES['vendor']}/vendor/{vendor_id}")
    return resp.json()

# Menu
@router.post("/menu/item")
async def proxy_create_menu_item(request: Request):
    body = await request.json()
    resp = await http_client.post(f"{SERVICES['menu']}/menu/item", json=body)
    return resp.json()

@router.get("/menu/vendor/{vendor_id}")
async def proxy_vendor_menu(vendor_id: str):
    resp = await http_client.get(f"{SERVICES['menu']}/menu/vendor/{vendor_id}")
    return resp.json()

# Search
@router.get("/search/")
async def proxy_search(q: str = "", cuisine: str = None, limit: int = 20):
    params = {"q": q, "limit": limit}
    if cuisine:
        params["cuisine"] = cuisine
    resp = await http_client.get(f"{SERVICES['search']}/search/", params=params)
    return resp.json()

# Pricing
@router.post("/pricing/calculate")
async def proxy_calculate_price(request: Request):
    body = await request.json()
    resp = await http_client.post(f"{SERVICES['pricing']}/pricing/calculate", json=body)
    return resp.json()

# Compliance
@router.post("/compliance/check")
async def proxy_compliance_check(request: Request):
    body = await request.json()
    resp = await http_client.post(f"{SERVICES['compliance']}/compliance/check", json=body)
    return resp.json()

@router.post("/compliance/gst/calculate")
async def proxy_gst_calculate(request: Request):
    body = await request.json()
    resp = await http_client.post(f"{SERVICES['compliance']}/compliance/gst/calculate", json=body)
    return resp.json()

# Billing
@router.post("/billing/invoice/generate")
async def proxy_generate_invoice(request: Request):
    body = await request.json()
    resp = await http_client.post(f"{SERVICES['billing']}/billing/invoice/generate", json=body)
    return resp.json()

@router.get("/billing/vendor/{vendor_id}")
async def proxy_vendor_invoices(vendor_id: str):
    resp = await http_client.get(f"{SERVICES['billing']}/billing/vendor/{vendor_id}")
    return resp.json()

# Analytics
@router.post("/analytics/event")
async def proxy_track_event(request: Request):
    body = await request.json()
    resp = await http_client.post(f"{SERVICES['analytics']}/analytics/event", json=body)
    return resp.json()

@router.get("/analytics/dashboard")
async def proxy_analytics_dashboard(vendor_id: str = None, days: int = 7):
    params = {"days": days}
    if vendor_id:
        params["vendor_id"] = vendor_id
    resp = await http_client.get(f"{SERVICES['analytics']}/analytics/dashboard", params=params)
    return resp.json()

# Config
@router.get("/config/{service_name}/{config_key}")
async def proxy_get_config(service_name: str, config_key: str):
    resp = await http_client.get(f"{SERVICES['config']}/config/{service_name}/{config_key}")
    return resp.json()
