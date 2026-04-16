"""HotSot Compensation Service — Routes."""
import uuid
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from shared.auth.jwt import get_current_user, require_role
from app.core.database import CompensationCaseModel, CompensationRuleModel
from app.core.engine import CompensationEngine

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

@router.post("/trigger")
async def trigger_compensation(
    order_id: str, user_id: str, kitchen_id: str,
    reason: str, order_amount: Decimal,
    auto_triggered: bool = True,
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Trigger compensation for an order issue."""
    tenant_id = user.get("claims", {}).get("tenant_id", user.get("user_id"))
    order_amount = order_amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    engine = CompensationEngine(_redis_client, _kafka_producer)
    comp = engine.calculate_compensation(reason, str(order_amount), tenant_id)

    case = CompensationCaseModel(
        tenant_id=uuid.UUID(tenant_id),
        order_id=uuid.UUID(order_id),
        user_id=uuid.UUID(user_id),
        kitchen_id=uuid.UUID(kitchen_id),
        reason=reason,
        amount=comp["compensation_amount"],
        currency=comp["currency"],
        status="APPROVED" if comp["auto_approve"] else "PENDING",
        auto_triggered=auto_triggered,
    )
    session.add(case)
    await session.commit()
    await session.refresh(case)

    if comp["auto_approve"]:
        refund = await engine.trigger_refund(
            str(case.id), order_id, "payment_ref",
            comp["compensation_amount"], reason, tenant_id
        )
        case.status = "PROCESSING"
        case.payment_ref = refund.get("refund_ref")
        await session.commit()

    return {
        "case_id": str(case.id),
        "compensation_amount": comp["compensation_amount"],
        "status": case.status,
        "auto_approved": comp["auto_approve"],
    }

@router.get("/case/{case_id}")
async def get_case(case_id: str, user: dict = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(CompensationCaseModel).where(CompensationCaseModel.id == uuid.UUID(case_id)))
    case = result.scalar_one_or_none()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    return {"case_id": str(case.id), "order_id": str(case.order_id), "reason": case.reason, "amount": case.amount, "status": case.status}

@router.get("/order/{order_id}")
async def get_order_compensations(order_id: str, user: dict = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    tenant_id = user.get("claims", {}).get("tenant_id", user.get("user_id"))
    result = await session.execute(
        select(CompensationCaseModel).where(
            CompensationCaseModel.order_id == uuid.UUID(order_id),
            CompensationCaseModel.tenant_id == uuid.UUID(tenant_id),
        )
    )
    cases = result.scalars().all()
    return {"order_id": order_id, "cases": [
        {"case_id": str(c.id), "reason": c.reason, "amount": c.amount, "status": c.status} for c in cases
    ]}
