"""HotSot Realtime Service — SSE for Customers."""
import asyncio
import json
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from app.core.connection_manager import manager

router = APIRouter()

@router.get("/customer/{user_id}")
async def customer_sse(user_id: str, request: Request):
    """SSE endpoint for customer order updates.

    Unidirectional: server pushes order status changes to customer.
    More reliable than WebSocket for mobile clients (auto-reconnects).
    """
    queue = asyncio.Queue()
    await manager.register_customer(user_id, queue)

    async def event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=30)
                    yield f"data: {json.dumps(message)}\n\n"
                except asyncio.TimeoutError:
                    yield f": keepalive\n\n"
        finally:
            manager.unregister_customer(user_id, queue)

    return StreamingResponse(event_generator(), media_type="text/event-stream")
