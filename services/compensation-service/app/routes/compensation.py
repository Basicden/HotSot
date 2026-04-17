"""HotSot Compensation Service — Routes.

Uses the shared Money class for all monetary calculations to ensure
Decimal-based precision and INR rounding consistency.
"""
import uuid
from decimal import Decimal
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from shared.auth.jwt import get_current_user, require_role
from shared.compliance_decorators import compliance_check
from shared.money import Money
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
@compliance_check("RBI")
async def trigger_compensation(
    order_id: str, user_id: str, kitchen_id: str,
    reason: str, order_amount: Decimal,
    auto_triggered: bool = True,
    vendor_id: str = None,
    tenant_id: str = None,
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Trigger compensation for an order issue.

    Uses Money class for order_amount handling to ensure
    Decimal-based precision. Compensation amounts are stored
    via Money.to_db() for database serialization.

    Compliance: @compliance_check("RBI") — soft gate verifies RBI compliance
    status before processing refunds. Logs warning if PENDING, blocks if FAILED.
    """
    if tenant_id is None:
        tenant_id = user.get("claims", {}).get("tenant_id", user.get("user_id"))

    # Convert order_amount to Money for precise handling
    order_money = Money(order_amount)

    engine = CompensationEngine(_redis_client, _kafka_producer)
    comp = engine.calculate_compensation(reason, order_money.amount, tenant_id)

    # Parse compensation amount as Money and use to_db() for storage
    comp_money = Money(comp["compensation_amount"])

    case = CompensationCaseModel(
        tenant_id=uuid.UUID(tenant_id),
        order_id=uuid.UUID(order_id),
        user_id=uuid.UUID(user_id),
        kitchen_id=uuid.UUID(kitchen_id),
        reason=reason,
        amount=comp_money.to_db(),
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
        "compensation_amount": comp_money.to_db(),
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
