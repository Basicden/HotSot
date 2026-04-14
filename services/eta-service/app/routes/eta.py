"""HotSot ETA Service — ETA Routes.

V2: Emits eta.updated Kafka events on every prediction and recalculation
so downstream services (realtime dashboard, notifications) can react.
"""

import time as time_mod
from typing import Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.predictor import predict_eta
from app.core.redis_client import cache_eta, get_cached_eta, get_kitchen_load
from app.main import emit_eta_event

router = APIRouter()


class ETAPredictRequest(BaseModel):
    order_id: str
    kitchen_id: str
    queue_length: int = 0
    item_complexity: int = 1
    time_of_day: int = 12
    is_peak_hour: bool = False
    is_festival: bool = False
    is_monsoon: bool = False
    staff_count: int = 3
    historical_delay: float = 0.0
    arrival_pressure: float = 0.0


@router.post("/predict")
async def predict(req: ETAPredictRequest):
    """Predict ETA for an order using ML or rule-based engine."""
    kitchen_load = await get_kitchen_load(req.kitchen_id)
    result = predict_eta(
        kitchen_load=kitchen_load, queue_length=req.queue_length,
        item_complexity=req.item_complexity, time_of_day=req.time_of_day,
        is_peak_hour=req.is_peak_hour, is_festival=req.is_festival,
        is_monsoon=req.is_monsoon, staff_count=req.staff_count,
        historical_delay=req.historical_delay, arrival_pressure=req.arrival_pressure,
    )
    await cache_eta(req.order_id, {"eta_seconds": result.eta_seconds, "confidence": result.confidence, "risk_level": result.risk_level})

    # Emit eta.updated Kafka event
    emit_eta_event({
        "order_id": req.order_id,
        "kitchen_id": req.kitchen_id,
        "event_type": "eta.updated",
        "eta_seconds": result.eta_seconds,
        "confidence": round(result.confidence, 2),
        "risk_level": result.risk_level,
        "delay_probability": round(result.delay_probability, 2),
        "model_version": result.model_version,
    })

    return {"order_id": req.order_id, "eta_seconds": result.eta_seconds, "confidence": round(result.confidence, 2),
            "confidence_interval": result.confidence_interval, "risk_level": result.risk_level,
            "delay_probability": round(result.delay_probability, 2), "model_version": result.model_version}


@router.get("/{order_id}")
async def get_eta(order_id: str):
    """Get cached ETA prediction for an order."""
    cached = await get_cached_eta(order_id)
    if not cached:
        raise HTTPException(status_code=404, detail="ETA prediction not found")
    return {"order_id": order_id, **cached}


@router.post("/recalculate")
async def recalculate_eta(req: ETAPredictRequest):
    """Force recalculate ETA when conditions change."""
    kitchen_load = await get_kitchen_load(req.kitchen_id)
    result = predict_eta(
        kitchen_load=kitchen_load, queue_length=req.queue_length,
        item_complexity=req.item_complexity, time_of_day=req.time_of_day,
        is_peak_hour=req.is_peak_hour, is_festival=req.is_festival,
        is_monsoon=req.is_monsoon, staff_count=req.staff_count,
        historical_delay=req.historical_delay, arrival_pressure=req.arrival_pressure,
    )
    await cache_eta(req.order_id, {"eta_seconds": result.eta_seconds, "confidence": result.confidence,
                                   "risk_level": result.risk_level, "delay_probability": result.delay_probability,
                                   "recalculated_at": time_mod.time()})

    # Emit eta.updated Kafka event (recalculated)
    emit_eta_event({
        "order_id": req.order_id,
        "kitchen_id": req.kitchen_id,
        "event_type": "eta.updated",
        "eta_seconds": result.eta_seconds,
        "confidence": round(result.confidence, 2),
        "risk_level": result.risk_level,
        "delay_probability": round(result.delay_probability, 2),
        "model_version": result.model_version,
        "recalculated": True,
    })

    return {"order_id": req.order_id, "eta_seconds": result.eta_seconds, "confidence": round(result.confidence, 2),
            "confidence_interval": result.confidence_interval, "risk_level": result.risk_level,
            "delay_probability": round(result.delay_probability, 2), "recalculated": True}
