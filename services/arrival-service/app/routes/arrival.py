"""
HotSot Arrival Service — Arrival API Routes
============================================
All HTTP endpoints for user arrival detection, status, QR tokens,
and kitchen waiting lists.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.core.dedup import ActiveArrival, ArrivalDedupEngine
from app.core.geo import (
    ArrivalStrength,
    ArrivalType,
    GPSCoordinate,
    validate_gps_arrival,
    validate_qr_arrival,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/arrival", tags=["arrival"])


# ── Pydantic Schemas ───────────────────────────────────────────────────

class ArrivalDetectRequest(BaseModel):
    """Request body for POST /arrival/detect."""
    user_id: str = Field(..., min_length=1, description="User ID")
    order_id: str = Field(..., min_length=1, description="Order ID")
    kitchen_id: str = Field(..., min_length=1, description="Kitchen / pickup location ID")
    idempotency_key: str = Field(..., min_length=1, description="Client-supplied idempotency key")
    # GPS fields (required when method=gps)
    user_lat: Optional[float] = Field(None, ge=-90, le=90, description="User latitude")
    user_lon: Optional[float] = Field(None, ge=-180, le=180, description="User longitude")
    kitchen_lat: Optional[float] = Field(None, ge=-90, le=90, description="Kitchen latitude")
    kitchen_lon: Optional[float] = Field(None, ge=-180, le=180, description="Kitchen longitude")
    # QR fields
    qr_token: Optional[str] = Field(None, description="Scanned QR token")
    # Detection method
    method: str = Field(..., pattern="^(gps|qr)$", description="Detection method: gps or qr")


class ArrivalDetectResponse(BaseModel):
    """Response for POST /arrival/detect."""
    arrival_id: str
    order_id: str
    user_id: str
    kitchen_id: str
    is_arrived: bool
    strength: str          # "hard" | "soft"
    arrival_type: str      # "gps" | "qr"
    distance_m: Optional[float] = None
    threshold_m: Optional[float] = None
    priority_boosted: bool
    message: str
    detected_at: str


class ArrivalStatusResponse(BaseModel):
    """Response for GET /arrival/{order_id}/status."""
    order_id: str
    has_active_arrival: bool
    arrival: Optional[dict] = None


class QRTokenResponse(BaseModel):
    """Response for POST /arrival/{order_id}/qr-token."""
    order_id: str
    token: str
    expires_at: str
    rotation_s: int


class WaitingUserResponse(BaseModel):
    """Single waiting user entry."""
    order_id: str
    user_id: str
    kitchen_id: str
    detected_at: str
    strength: str
    arrival_type: str


class WaitingListResponse(BaseModel):
    """Response for GET /arrival/kitchen/{kitchen_id}/waiting."""
    kitchen_id: str
    count: int
    waiting: list[WaitingUserResponse]


# ── Dependencies ───────────────────────────────────────────────────────

def get_dedup_engine() -> ArrivalDedupEngine:
    return ArrivalDedupEngine()


# ── Kafka helpers (lightweight producer) ───────────────────────────────

async def _emit_kafka_event(topic: str, event: dict) -> None:
    """
    Fire-and-forget Kafka event emission.

    In production this would use a pooled producer; here we keep it
    simple with a per-call producer that is closed after sending.
    """
    try:
        from kafka import KafkaProducer
        import json

        settings = get_settings()
        producer = KafkaProducer(
            bootstrap_servers=settings.kafka_bootstrap_servers,
            value_serializer=lambda v: json.dumps(v).encode("utf-8"),
            linger_ms=0,
        )
        producer.send(topic, event)
        producer.flush(timeout=5)
        producer.close(timeout=5)
        logger.info("Kafka event sent to %s: %s", topic, event.get("event_type"))
    except Exception:
        # Kafka failures must NOT block the arrival flow
        logger.exception("Failed to emit Kafka event to %s", topic)


# ── QR token generation ────────────────────────────────────────────────

def _generate_qr_token(order_id: str, kitchen_id: str) -> tuple[str, str]:
    """
    Generate a time-bounded QR token using HMAC-SHA256.

    The token is valid for the current rotation window.  A fresh token
    is produced each call; the same inputs within the same window will
    produce the same token (idempotent at the window level).

    Returns
    -------
    (token, expires_at_iso)
    """
    settings = get_settings()
    rotation = settings.qr_token_rotation_s
    now = time.time()
    bucket = int(now // rotation)
    expires_at = (bucket + 1) * rotation

    payload = f"{order_id}:{kitchen_id}:{bucket}"
    sig = hmac.new(
        settings.qr_token_secret.encode(),
        payload.encode(),
        hashlib.sha256,
    ).hexdigest()

    token = f"{order_id}.{bucket}.{sig}"
    expires_at_iso = datetime.fromtimestamp(expires_at, tz=timezone.utc).isoformat()
    return token, expires_at_iso


def _verify_qr_token(token: str, order_id: str, kitchen_id: str) -> bool:
    """Verify a QR token against the current (and previous) window."""
    settings = get_settings()
    rotation = settings.qr_token_rotation_s
    now = time.time()

    for offset in (0, -1):  # current and previous window
        bucket = int(now // rotation) + offset
        payload = f"{order_id}:{kitchen_id}:{bucket}"
        sig = hmac.new(
            settings.qr_token_secret.encode(),
            payload.encode(),
            hashlib.sha256,
        ).hexdigest()
        expected = f"{order_id}.{bucket}.{sig}"
        if hmac.compare_digest(token, expected):
            return True
    return False


# ── Idempotency ────────────────────────────────────────────────────────

async def _check_idempotency(key: str) -> Optional[dict]:
    """Check if we've already processed a request with this idempotency key."""
    from app.core.dedup import ArrivalDedupEngine
    engine = ArrivalDedupEngine()
    r = await engine._get_redis()
    cached = await r.get(f"hotsot:arrival:idempotency:{key}")
    if cached:
        import json
        return json.loads(cached)
    return None


async def _store_idempotency(key: str, response: dict) -> None:
    """Cache a successful response under the idempotency key."""
    from app.core.dedup import ArrivalDedupEngine
    engine = ArrivalDedupEngine()
    r = await engine._get_redis()
    import json
    await r.set(
        f"hotsot:arrival:idempotency:{key}",
        json.dumps(response),
        ex=300,  # 5 minute TTL
    )


# ── Endpoints ──────────────────────────────────────────────────────────

@router.post(
    "/detect",
    response_model=ArrivalDetectResponse,
    status_code=status.HTTP_200_OK,
    summary="Detect user arrival",
    description=(
        "Detect whether a user has arrived at the pickup location "
        "via GPS coordinates or QR scan. Hard arrivals (within radius "
        "or QR-verified) trigger a priority boost; soft arrivals are "
        "logged only."
    ),
)
async def detect_arrival(
    body: ArrivalDetectRequest,
    dedup: ArrivalDedupEngine = Depends(get_dedup_engine),
) -> ArrivalDetectResponse:
    settings = get_settings()

    # ── 1. Idempotency check ───────────────────────────────────────
    cached = await _check_idempotency(body.idempotency_key)
    if cached:
        logger.info("Idempotent replay for key=%s", body.idempotency_key)
        return ArrivalDetectResponse(**cached)

    # ── 2. Dedup check ─────────────────────────────────────────────
    dedup_result = await dedup.check_and_record(
        user_id=body.user_id,
        order_id=body.order_id,
    )
    if dedup_result.is_duplicate:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Duplicate arrival event within dedup window",
        )

    # ── 3. Validate arrival ────────────────────────────────────────
    priority_boosted = False
    message = ""

    if body.method == "gps":
        if body.user_lat is None or body.user_lon is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="GPS method requires user_lat and user_lon",
            )
        if body.kitchen_lat is None or body.kitchen_lon is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="GPS method requires kitchen_lat and kitchen_lon",
            )

        user_coord = GPSCoordinate(latitude=body.user_lat, longitude=body.user_lon)
        kitchen_coord = GPSCoordinate(latitude=body.kitchen_lat, longitude=body.kitchen_lon)
        verdict = validate_gps_arrival(user_coord, kitchen_coord)

        if verdict.is_arrived:
            priority_boosted = True
            message = f"Hard GPS arrival — {verdict.distance_m}m away (≤{verdict.threshold_m}m)"
        else:
            message = f"Soft GPS arrival — {verdict.distance_m}m away (>{verdict.threshold_m}m threshold)"

    elif body.method == "qr":
        if body.qr_token is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="QR method requires qr_token",
            )
        if body.kitchen_lat is None or body.kitchen_lon is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="QR method requires kitchen_lat and kitchen_lon for tracking",
            )

        kitchen_coord = GPSCoordinate(latitude=body.kitchen_lat, longitude=body.kitchen_lon)

        if not _verify_qr_token(body.qr_token, body.order_id, body.kitchen_id):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired QR token",
            )

        verdict = validate_qr_arrival(kitchen_coord)
        priority_boosted = True
        message = "Hard QR arrival — token verified"
    else:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unsupported method: {body.method}",
        )

    # ── 4. Build response ──────────────────────────────────────────
    arrival_id = str(uuid.uuid4())
    now_iso = datetime.now(tz=timezone.utc).isoformat()

    response_data = {
        "arrival_id": arrival_id,
        "order_id": body.order_id,
        "user_id": body.user_id,
        "kitchen_id": body.kitchen_id,
        "is_arrived": verdict.is_arrived,
        "strength": verdict.strength.value,
        "arrival_type": verdict.arrival_type.value,
        "distance_m": verdict.distance_m if verdict.distance_m else None,
        "threshold_m": verdict.threshold_m if verdict.threshold_m else None,
        "priority_boosted": priority_boosted,
        "message": message,
        "detected_at": now_iso,
    }

    # ── 5. Store active arrival ────────────────────────────────────
    active = ActiveArrival(
        order_id=body.order_id,
        user_id=body.user_id,
        kitchen_id=body.kitchen_id,
        detected_at=time.time(),
        strength=verdict.strength.value,
        arrival_type=verdict.arrival_type.value,
    )
    await dedup.set_active_arrival(active)

    # ── 6. Emit Kafka events (fire-and-forget) ─────────────────────
    arrival_event = {
        "event_type": "arrival.detected",
        "arrival_id": arrival_id,
        "order_id": body.order_id,
        "user_id": body.user_id,
        "kitchen_id": body.kitchen_id,
        "strength": verdict.strength.value,
        "arrival_type": verdict.arrival_type.value,
        "distance_m": verdict.distance_m,
        "priority_boosted": priority_boosted,
        "detected_at": now_iso,
    }
    await _emit_kafka_event(settings.kafka_arrival_topic, arrival_event)

    # Priority boost event — only for HARD arrivals
    if priority_boosted:
        boost_event = {
            "event_type": "priority.boost",
            "order_id": body.order_id,
            "user_id": body.user_id,
            "kitchen_id": body.kitchen_id,
            "boost_amount": settings.priority_boost_amount,
            "reason": "arrival_detected",
            "detected_at": now_iso,
        }
        await _emit_kafka_event(settings.kafka_priority_topic, boost_event)

    # Staff notification — on any arrival (hard or soft)
    staff_event = {
        "event_type": "staff.arrival_notification",
        "order_id": body.order_id,
        "user_id": body.user_id,
        "kitchen_id": body.kitchen_id,
        "strength": verdict.strength.value,
        "arrival_type": verdict.arrival_type.value,
        "action": "flash_and_ping",
        "detected_at": now_iso,
    }
    await _emit_kafka_event(settings.kafka_staff_notification_topic, staff_event)

    # ── 7. Store idempotency ───────────────────────────────────────
    await _store_idempotency(body.idempotency_key, response_data)

    logger.info(
        "Arrival detected: order=%s user=%s strength=%s type=%s boosted=%s",
        body.order_id, body.user_id, verdict.strength.value,
        verdict.arrival_type.value, priority_boosted,
    )

    return ArrivalDetectResponse(**response_data)


@router.get(
    "/{order_id}/status",
    response_model=ArrivalStatusResponse,
    summary="Get arrival status",
    description="Retrieve the current arrival status for an order.",
)
async def get_arrival_status(
    order_id: str,
    dedup: ArrivalDedupEngine = Depends(get_dedup_engine),
) -> ArrivalStatusResponse:
    arrival = await dedup.get_active_arrival(order_id)
    if arrival:
        # Convert detected_at back to ISO format if it's a timestamp string
        detected_at_raw = arrival.get("detected_at", "")
        try:
            ts = float(detected_at_raw)
            detected_at_iso = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
        except (ValueError, TypeError):
            detected_at_iso = detected_at_raw

        arrival_data = {
            "order_id": arrival.get("order_id", order_id),
            "user_id": arrival.get("user_id", ""),
            "kitchen_id": arrival.get("kitchen_id", ""),
            "detected_at": detected_at_iso,
            "strength": arrival.get("strength", ""),
            "arrival_type": arrival.get("arrival_type", ""),
        }
        return ArrivalStatusResponse(
            order_id=order_id,
            has_active_arrival=True,
            arrival=arrival_data,
        )

    return ArrivalStatusResponse(
        order_id=order_id,
        has_active_arrival=False,
        arrival=None,
    )


@router.post(
    "/{order_id}/qr-token",
    response_model=QRTokenResponse,
    summary="Generate QR token for handoff",
    description=(
        "Generate a time-bounded QR token that the kitchen staff can "
        "display for the customer to scan.  Tokens rotate every 30 s."
    ),
)
async def generate_qr_token(
    order_id: str,
    kitchen_id: str = Field(..., description="Kitchen ID for the token binding"),
) -> QRTokenResponse:
    settings = get_settings()
    token, expires_at = _generate_qr_token(order_id, kitchen_id)

    return QRTokenResponse(
        order_id=order_id,
        token=token,
        expires_at=expires_at,
        rotation_s=settings.qr_token_rotation_s,
    )


@router.get(
    "/kitchen/{kitchen_id}/waiting",
    response_model=WaitingListResponse,
    summary="Get waiting users for a kitchen",
    description="List all users with active arrivals at a kitchen.",
)
async def get_waiting_users(
    kitchen_id: str,
    dedup: ArrivalDedupEngine = Depends(get_dedup_engine),
) -> WaitingListResponse:
    raw = await dedup.get_waiting_for_kitchen(kitchen_id)

    waiting: list[WaitingUserResponse] = []
    for entry in raw:
        detected_at_raw = entry.get("detected_at", "")
        try:
            ts = float(detected_at_raw)
            detected_at_iso = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
        except (ValueError, TypeError):
            detected_at_iso = detected_at_raw

        waiting.append(WaitingUserResponse(
            order_id=entry.get("order_id", ""),
            user_id=entry.get("user_id", ""),
            kitchen_id=entry.get("kitchen_id", ""),
            detected_at=detected_at_iso,
            strength=entry.get("strength", ""),
            arrival_type=entry.get("arrival_type", ""),
        ))

    return WaitingListResponse(
        kitchen_id=kitchen_id,
        count=len(waiting),
        waiting=waiting,
    )
