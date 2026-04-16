"""HotSot Arrival Service — Arrival Detection Routes."""

import uuid
import hashlib
import time
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from shared.auth.jwt import get_current_user
from shared.utils.helpers import now_iso, generate_id
from shared.types.schemas import EventType

from app.core.database import ArrivalEventModel
from app.core.geo import haversine_distance, is_within_geofence, classify_proximity
from app.core.dedup import ArrivalDeduplicator

router = APIRouter()
_session_factory = None
_redis_client = None
_kafka_producer = None


def set_dependencies(session_factory, redis_client, kafka_producer):
    global _session_factory, _redis_client, _kafka_producer
    _session_factory = session_factory
    _redis_client = redis_client
    _kafka_producer = kafka_producer


async def get_session():
    if _session_factory is None:
        raise RuntimeError("Session factory not initialized")
    async with _session_factory() as session:
        yield session


@router.post("/detect")
async def detect_arrival(
    order_id: str,
    user_id: str,
    kitchen_id: str,
    kitchen_lat: float,
    kitchen_lng: float,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
    qr_scan: bool = False,
    idempotency_key: Optional[str] = None,
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Detect user arrival via GPS coordinates or QR scan.

    Validation:
    - GPS: user must be within 150m of kitchen
    - QR Scan: always valid (user physically scanned)
    """
    tenant_id = user.get("claims", {}).get("tenant_id", user.get("user_id"))

    # Deduplication (fail-closed: Redis unavailable → treated as duplicate)
    dedup = ArrivalDeduplicator(_redis_client)
    try:
        if await dedup.is_duplicate(user_id, order_id, time.time(), tenant_id):
            return {"order_id": order_id, "status": "duplicate", "message": "Already processed"}
    except RuntimeError:
        raise HTTPException(status_code=503, detail="Deduplication service unavailable")

    # Check idempotency key (fail-closed: Redis unavailable → 503)
    if idempotency_key:
        try:
            cached = await dedup.check_idempotency_key(idempotency_key, tenant_id)
            if cached:
                return {"order_id": order_id, "status": "cached", "cached_response": cached}
        except RuntimeError:
            raise HTTPException(status_code=503, detail="Idempotency service unavailable")

    # Calculate distance
    distance_meters = None
    is_valid = False

    if qr_scan:
        # QR scan is always valid — user is physically at the kitchen
        is_valid = True
        distance_meters = 0
        signal_type = "QR_SCAN"
    elif latitude is not None and longitude is not None:
        # GPS-based detection
        distance_meters = haversine_distance(latitude, longitude, kitchen_lat, kitchen_lng)
        is_valid = is_within_geofence(latitude, longitude, kitchen_lat, kitchen_lng, 150)
        signal_type = "GPS"
    else:
        raise HTTPException(status_code=400, detail="Provide GPS coordinates or qr_scan=true")

    if not is_valid:
        return {
            "order_id": order_id,
            "is_valid": False,
            "distance_meters": round(distance_meters, 1) if distance_meters else None,
            "message": "User not within geofence (150m radius)",
        }

    # Generate idempotency key if not provided
    if not idempotency_key:
        ts_bucket = int(time.time()) // 30
        raw = f"{user_id}:{order_id}:{ts_bucket}"
        idempotency_key = hashlib.sha256(raw.encode()).hexdigest()[:32]

    # Save to database
    event = ArrivalEventModel(
        tenant_id=uuid.UUID(tenant_id),
        user_id=uuid.UUID(user_id),
        order_id=uuid.UUID(order_id),
        kitchen_id=uuid.UUID(kitchen_id),
        signal_type=signal_type,
        latitude=latitude,
        longitude=longitude,
        distance_meters=distance_meters,
        is_valid=is_valid,
        idempotency_key=idempotency_key,
    )
    session.add(event)
    await session.commit()

    # Publish arrival event to Kafka
    if _kafka_producer:
        await _kafka_producer.publish(
            topic="hotsot.arrival.events.v1",
            key=order_id,
            value={
                "event_id": generate_id(),
                "event_type": EventType.ARRIVAL_DETECTED.value,
                "order_id": order_id,
                "kitchen_id": kitchen_id,
                "tenant_id": tenant_id,
                "source": "arrival-service",
                "timestamp": now_iso(),
                "schema_version": 2,
                "payload": {
                    "user_id": user_id,
                    "signal_type": signal_type,
                    "distance_meters": distance_meters,
                    "proximity": classify_proximity(distance_meters) if distance_meters else "IMMEDIATE",
                    "arrival_boost": 20.0,
                    "validated": is_valid,
                },
            },
        )

    # Store idempotency response (fail-closed: Redis unavailable → 503)
    response_data = {
        "order_id": order_id,
        "is_valid": is_valid,
        "distance_meters": round(distance_meters, 1) if distance_meters else None,
        "proximity": classify_proximity(distance_meters) if distance_meters else "IMMEDIATE",
        "signal_type": signal_type,
    }
    if idempotency_key:
        import json
        try:
            await dedup.store_idempotency_key(
                idempotency_key, json.dumps(response_data), tenant_id
            )
        except RuntimeError:
            raise HTTPException(status_code=503, detail="Idempotency storage unavailable")

    return response_data


@router.post("/qr-scan")
async def qr_scan_arrival(
    order_id: str,
    user_id: str,
    kitchen_id: str,
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """QR scan-based arrival (always valid — user is physically at kitchen)."""
    return await detect_arrival(
        order_id=order_id,
        user_id=user_id,
        kitchen_id=kitchen_id,
        kitchen_lat=0, kitchen_lng=0,
        qr_scan=True,
        user=user,
        session=session,
    )


@router.get("/{order_id}/status")
async def get_arrival_status(
    order_id: str,
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Get latest arrival status for an order."""
    tenant_id = user.get("claims", {}).get("tenant_id", user.get("user_id"))

    result = await session.execute(
        select(ArrivalEventModel).where(
            ArrivalEventModel.order_id == uuid.UUID(order_id),
            ArrivalEventModel.tenant_id == uuid.UUID(tenant_id),
        ).order_by(ArrivalEventModel.detected_at.desc()).limit(1)
    )
    event = result.scalar_one_or_none()

    if not event:
        return {"order_id": order_id, "arrived": False}

    return {
        "order_id": order_id,
        "arrived": event.is_valid,
        "signal_type": event.signal_type,
        "distance_meters": event.distance_meters,
        "detected_at": event.detected_at.isoformat() if event.detected_at else None,
    }


@router.get("/kitchen/{kitchen_id}/pending")
async def get_pending_arrivals(
    kitchen_id: str,
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Get all pending (valid) arrivals for a kitchen."""
    tenant_id = user.get("claims", {}).get("tenant_id", user.get("user_id"))

    result = await session.execute(
        select(ArrivalEventModel).where(
            ArrivalEventModel.kitchen_id == uuid.UUID(kitchen_id),
            ArrivalEventModel.tenant_id == uuid.UUID(tenant_id),
            ArrivalEventModel.is_valid == True,
        ).order_by(ArrivalEventModel.detected_at.desc()).limit(50)
    )
    events = result.scalars().all()

    return {
        "kitchen_id": kitchen_id,
        "pending_arrivals": [
            {
                "order_id": str(e.order_id),
                "user_id": str(e.user_id),
                "signal_type": e.signal_type,
                "distance_meters": e.distance_meters,
                "detected_at": e.detected_at.isoformat() if e.detected_at else None,
            }
            for e in events
        ],
    }
