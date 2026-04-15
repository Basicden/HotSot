"""HotSot ETA Service — ETA Routes."""

import uuid
from typing import Optional, List

from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from shared.auth.jwt import get_current_user
from shared.utils.helpers import now_iso
from shared.types.schemas import RiskLevel

from app.core.database import ETAPredictionModel, KitchenETABaselineModel
from app.core.predictor import ETAPredictor

router = APIRouter()
_session_factory = None
_redis_client = None


def set_dependencies(session_factory, redis_client):
    global _session_factory, _redis_client
    _session_factory = session_factory
    _redis_client = redis_client


async def get_session():
    if _session_factory is None:
        raise RuntimeError("Session factory not initialized")
    async with _session_factory() as session:
        yield session


@router.get("/{order_id}")
async def get_eta(
    order_id: str,
    kitchen_id: str = None,
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Get current ETA for an order."""
    tenant_id = user.get("claims", {}).get("tenant_id", user.get("user_id"))

    # Try Redis cache first
    predictor = ETAPredictor(_redis_client)
    if kitchen_id:
        cached = await predictor.get_cached_prediction(kitchen_id, order_id)
        if cached:
            return {"order_id": order_id, **cached, "source": "cache"}

    # Fallback to database
    result = await session.execute(
        select(ETAPredictionModel)
        .where(
            ETAPredictionModel.order_id == uuid.UUID(order_id),
            ETAPredictionModel.tenant_id == uuid.UUID(tenant_id),
        )
        .order_by(ETAPredictionModel.created_at.desc())
        .limit(1)
    )
    prediction = result.scalar_one_or_none()

    if not prediction:
        raise HTTPException(status_code=404, detail="ETA prediction not found")

    return {
        "order_id": str(prediction.order_id),
        "kitchen_id": str(prediction.kitchen_id),
        "eta_seconds": prediction.eta_seconds,
        "confidence_interval": [prediction.confidence_interval_low, prediction.confidence_interval_high],
        "confidence_score": prediction.confidence_score,
        "risk_level": prediction.risk_level,
        "delay_probability": prediction.delay_probability,
        "created_at": prediction.created_at.isoformat() if prediction.created_at else None,
        "source": "database",
    }


@router.post("/{order_id}/recalculate")
async def recalculate_eta(
    order_id: str,
    kitchen_id: str,
    queue_length: int = 0,
    kitchen_load_pct: float = 0.0,
    items: Optional[List[dict]] = None,
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Force recalculate ETA for an order."""
    tenant_id = user.get("claims", {}).get("tenant_id", user.get("user_id"))

    # Get kitchen baseline
    baseline_result = await session.execute(
        select(KitchenETABaselineModel).where(
            KitchenETABaselineModel.kitchen_id == uuid.UUID(kitchen_id)
        )
    )
    baseline = baseline_result.scalar_one_or_none()

    base_prep = baseline.base_prep_seconds if baseline else 300

    predictor = ETAPredictor(_redis_client)
    prediction = await predictor.predict(
        order_id=order_id,
        kitchen_id=kitchen_id,
        items=items or [],
        kitchen_load_pct=kitchen_load_pct,
        queue_length=queue_length,
        base_prep_seconds=base_prep,
    )

    # Save to database
    record = ETAPredictionModel(
        tenant_id=uuid.UUID(tenant_id),
        order_id=uuid.UUID(order_id),
        kitchen_id=uuid.UUID(kitchen_id),
        eta_seconds=prediction.eta_seconds,
        confidence_interval_low=prediction.confidence_interval_low,
        confidence_interval_high=prediction.confidence_interval_high,
        confidence_score=prediction.confidence_score,
        risk_level=prediction.risk_level,
        delay_probability=prediction.delay_probability,
        features_used=prediction.features_used,
    )
    session.add(record)
    await session.commit()

    return {
        "order_id": order_id,
        "eta_seconds": prediction.eta_seconds,
        "confidence_interval": [prediction.confidence_interval_low, prediction.confidence_interval_high],
        "confidence_score": prediction.confidence_score,
        "risk_level": prediction.risk_level,
        "delay_probability": prediction.delay_probability,
    }


@router.get("/kitchen/{kitchen_id}/baseline")
async def get_kitchen_baseline(
    kitchen_id: str,
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Get kitchen ETA baseline."""
    result = await session.execute(
        select(KitchenETABaselineModel).where(
            KitchenETABaselineModel.kitchen_id == uuid.UUID(kitchen_id)
        )
    )
    baseline = result.scalar_one_or_none()

    if not baseline:
        raise HTTPException(status_code=404, detail="Kitchen baseline not found")

    return {
        "kitchen_id": kitchen_id,
        "base_prep_seconds": baseline.base_prep_seconds,
        "peak_prep_seconds": baseline.peak_prep_seconds,
        "average_delay_seconds": baseline.average_delay_seconds,
    }


@router.put("/kitchen/{kitchen_id}/baseline")
async def update_kitchen_baseline(
    kitchen_id: str,
    base_prep_seconds: int = 300,
    peak_prep_seconds: int = 420,
    average_delay_seconds: int = 60,
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Update kitchen ETA baseline."""
    tenant_id = user.get("claims", {}).get("tenant_id", user.get("user_id"))

    result = await session.execute(
        select(KitchenETABaselineModel).where(
            KitchenETABaselineModel.kitchen_id == uuid.UUID(kitchen_id)
        )
    )
    baseline = result.scalar_one_or_none()

    if baseline:
        baseline.base_prep_seconds = base_prep_seconds
        baseline.peak_prep_seconds = peak_prep_seconds
        baseline.average_delay_seconds = average_delay_seconds
    else:
        baseline = KitchenETABaselineModel(
            tenant_id=uuid.UUID(tenant_id),
            kitchen_id=uuid.UUID(kitchen_id),
            base_prep_seconds=base_prep_seconds,
            peak_prep_seconds=peak_prep_seconds,
            average_delay_seconds=average_delay_seconds,
        )
        session.add(baseline)

    await session.commit()
    return {"kitchen_id": kitchen_id, "updated": True}


@router.get("/{order_id}/history")
async def get_eta_history(
    order_id: str,
    limit: int = 10,
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Get ETA prediction history for an order."""
    tenant_id = user.get("claims", {}).get("tenant_id", user.get("user_id"))

    result = await session.execute(
        select(ETAPredictionModel)
        .where(
            ETAPredictionModel.order_id == uuid.UUID(order_id),
            ETAPredictionModel.tenant_id == uuid.UUID(tenant_id),
        )
        .order_by(ETAPredictionModel.created_at.desc())
        .limit(limit)
    )
    predictions = result.scalars().all()

    return {
        "order_id": order_id,
        "history": [
            {
                "eta_seconds": p.eta_seconds,
                "risk_level": p.risk_level,
                "confidence_score": p.confidence_score,
                "model_version": p.model_version,
                "created_at": p.created_at.isoformat() if p.created_at else None,
            }
            for p in predictions
        ],
    }
