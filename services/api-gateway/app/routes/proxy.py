"""HotSot API Gateway — Proxy Routes.

V2 proxy routes for all microservices including compensation and arrival.
"""

from fastapi import APIRouter, Request
import httpx
import os

router = APIRouter()

http_client = httpx.AsyncClient(timeout=30.0)

# Service URLs from environment
ORDER_SERVICE = os.getenv("ORDER_SERVICE_URL", "http://localhost:8001")
KITCHEN_SERVICE = os.getenv("KITCHEN_SERVICE_URL", "http://localhost:8002")
SHELF_SERVICE = os.getenv("SHELF_SERVICE_URL", "http://localhost:8003")
ETA_SERVICE = os.getenv("ETA_SERVICE_URL", "http://localhost:8004")
NOTIFICATION_SERVICE = os.getenv("NOTIFICATION_SERVICE_URL", "http://localhost:8005")
REALTIME_SERVICE = os.getenv("REALTIME_SERVICE_URL", "http://localhost:8006")
ML_SERVICE = os.getenv("ML_SERVICE_URL", "http://localhost:8007")
ARRIVAL_SERVICE = os.getenv("ARRIVAL_SERVICE_URL", "http://localhost:8008")
COMPENSATION_SERVICE = os.getenv("COMPENSATION_SERVICE_URL", "http://localhost:8009")


# ─── ORDER PROXY ───────────────────────────────────────────────

@router.post("/orders/create")
async def proxy_create_order(request: Request):
    body = await request.json()
    resp = await http_client.post(f"{ORDER_SERVICE}/orders/create", json=body)
    return resp.json()


@router.post("/orders/{order_id}/pay")
async def proxy_pay_order(order_id: str, request: Request):
    body = await request.json()
    resp = await http_client.post(f"{ORDER_SERVICE}/orders/{order_id}/pay", json=body)
    return resp.json()


@router.post("/orders/{order_id}/assign-queue")
async def proxy_assign_queue(order_id: str, request: Request):
    body = await request.json()
    resp = await http_client.post(f"{ORDER_SERVICE}/orders/{order_id}/assign-queue", json=body)
    return resp.json()


@router.post("/orders/{order_id}/start-prep")
async def proxy_start_prep(order_id: str):
    resp = await http_client.post(f"{ORDER_SERVICE}/orders/{order_id}/start-prep")
    return resp.json()


@router.post("/orders/{order_id}/ready")
async def proxy_ready_order(order_id: str):
    resp = await http_client.post(f"{ORDER_SERVICE}/orders/{order_id}/ready")
    return resp.json()


@router.post("/orders/{order_id}/shelf")
async def proxy_shelf_order(order_id: str, request: Request):
    body = await request.json() if await request.body() else {}
    resp = await http_client.post(f"{ORDER_SERVICE}/orders/{order_id}/shelf", json=body)
    return resp.json()


@router.post("/orders/{order_id}/arrival")
async def proxy_arrival(order_id: str, request: Request):
    body = await request.json() if await request.body() else {}
    resp = await http_client.post(f"{ORDER_SERVICE}/orders/{order_id}/arrival", json=body)
    return resp.json()


@router.post("/orders/{order_id}/handoff")
async def proxy_handoff(order_id: str, request: Request):
    body = await request.json() if await request.body() else {}
    resp = await http_client.post(f"{ORDER_SERVICE}/orders/{order_id}/handoff", json=body)
    return resp.json()


@router.post("/orders/{order_id}/pickup")
async def proxy_pickup_order(order_id: str):
    resp = await http_client.post(f"{ORDER_SERVICE}/orders/{order_id}/pickup")
    return resp.json()


@router.post("/orders/{order_id}/expire")
async def proxy_expire_order(order_id: str):
    resp = await http_client.post(f"{ORDER_SERVICE}/orders/{order_id}/expire")
    return resp.json()


@router.post("/orders/{order_id}/refund")
async def proxy_refund_order(order_id: str):
    resp = await http_client.post(f"{ORDER_SERVICE}/orders/{order_id}/refund")
    return resp.json()


@router.post("/orders/{order_id}/cancel")
async def proxy_cancel_order(order_id: str):
    resp = await http_client.post(f"{ORDER_SERVICE}/orders/{order_id}/cancel")
    return resp.json()


@router.get("/orders/{order_id}")
async def proxy_get_order(order_id: str):
    resp = await http_client.get(f"{ORDER_SERVICE}/orders/{order_id}")
    return resp.json()


@router.get("/orders/{order_id}/events")
async def proxy_get_events(order_id: str):
    resp = await http_client.get(f"{ORDER_SERVICE}/orders/{order_id}/events")
    return resp.json()


# ─── KITCHEN PROXY ─────────────────────────────────────────────

@router.get("/kitchen/{kitchen_id}/status")
async def proxy_kitchen_status(kitchen_id: str):
    resp = await http_client.get(f"{KITCHEN_SERVICE}/kitchen/{kitchen_id}/status")
    return resp.json()


@router.get("/kitchen/{kitchen_id}/batches")
async def proxy_kitchen_batches(kitchen_id: str):
    resp = await http_client.get(f"{KITCHEN_SERVICE}/kitchen/{kitchen_id}/batches")
    return resp.json()


# ─── ETA PROXY ─────────────────────────────────────────────────

@router.post("/eta/predict")
async def proxy_eta_predict(request: Request):
    body = await request.json()
    resp = await http_client.post(f"{ETA_SERVICE}/eta/predict", json=body)
    return resp.json()


# ─── SHELF PROXY ───────────────────────────────────────────────

@router.get("/shelf/{kitchen_id}/status")
async def proxy_shelf_status(kitchen_id: str):
    resp = await http_client.get(f"{SHELF_SERVICE}/shelf/{kitchen_id}/status")
    return resp.json()


# ─── NOTIFICATION PROXY ────────────────────────────────────────

@router.post("/notifications/send")
async def proxy_send_notification(request: Request):
    body = await request.json()
    resp = await http_client.post(f"{NOTIFICATION_SERVICE}/notifications/send", json=body)
    return resp.json()


# ─── ARRIVAL PROXY (V2) ───────────────────────────────────────

@router.post("/arrival/detect")
async def proxy_arrival_detect(request: Request):
    body = await request.json()
    resp = await http_client.post(f"{ARRIVAL_SERVICE}/arrival/detect", json=body)
    return resp.json()


@router.get("/arrival/{order_id}/status")
async def proxy_arrival_status(order_id: str):
    resp = await http_client.get(f"{ARRIVAL_SERVICE}/arrival/{order_id}/status")
    return resp.json()


@router.get("/arrival/{order_id}/qr-token")
async def proxy_arrival_qr(order_id: str):
    resp = await http_client.get(f"{ARRIVAL_SERVICE}/arrival/{order_id}/qr-token")
    return resp.json()


# ─── COMPENSATION PROXY (V2) ──────────────────────────────────

@router.post("/compensation/evaluate")
async def proxy_compensation_evaluate(request: Request):
    body = await request.json()
    resp = await http_client.post(f"{COMPENSATION_SERVICE}/compensation/evaluate", json=body)
    return resp.json()


@router.get("/compensation/{order_id}")
async def proxy_compensation_status(order_id: str):
    resp = await http_client.get(f"{COMPENSATION_SERVICE}/compensation/{order_id}")
    return resp.json()


@router.get("/compensation/kitchen/{kitchen_id}/penalty")
async def proxy_kitchen_penalty(kitchen_id: str):
    resp = await http_client.get(f"{COMPENSATION_SERVICE}/compensation/kitchen/{kitchen_id}/penalty")
    return resp.json()


# ─── ML PROXY (V2) ─────────────────────────────────────────────

@router.post("/ml/train")
async def proxy_ml_train(request: Request):
    body = await request.json()
    resp = await http_client.post(f"{ML_SERVICE}/ml/train", json=body)
    return resp.json()


@router.post("/ml/feedback")
async def proxy_ml_feedback(request: Request):
    body = await request.json()
    resp = await http_client.post(f"{ML_SERVICE}/ml/feedback", json=body)
    return resp.json()
