"""
HotSot Optimistic Locking — Prevent Lost Updates in Concurrent Operations.

Uses a `version` column that is incremented on every update.
If the version in the UPDATE doesn't match the current DB version,
the update affects 0 rows — the caller knows the record was modified
by another transaction and must retry.

Usage:
    # In model definition:
    class OrderModel(BaseModel):
        version = Column(Integer, default=1, nullable=False)

    # In update route:
    from shared.optimistic_locking import optimistic_update, ConcurrentUpdateError

    try:
        updated = await optimistic_update(
            session, OrderModel, order_id, update_data, current_version=3
        )
    except ConcurrentUpdateError:
        raise HTTPException(409, "Record was modified by another request. Please refresh and retry.")
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, Optional, Type

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("hotsot.optimistic_locking")

# Maximum retry attempts for optimistic lock conflicts
MAX_RETRY_ATTEMPTS = 3


class ConcurrentUpdateError(Exception):
    """Raised when an optimistic lock update fails due to version mismatch."""

    def __init__(self, model_name: str, record_id: str, expected_version: int):
        self.model_name = model_name
        self.record_id = record_id
        self.expected_version = expected_version
        super().__init__(
            f"Concurrent update conflict on {model_name}({record_id}): "
            f"expected version {expected_version} but record was modified by another transaction"
        )


async def optimistic_update(
    session: AsyncSession,
    model_class: Type,
    record_id: str,
    update_data: Dict[str, Any],
    current_version: int,
    tenant_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Perform an optimistic-locked update on a database record.

    The record must have a `version` column (Integer). The UPDATE query
    includes a WHERE clause on version, so if another transaction has
    already modified the record, the UPDATE will affect 0 rows and
    ConcurrentUpdateError is raised.

    Args:
        session: SQLAlchemy async session.
        model_class: The SQLAlchemy model class.
        record_id: UUID of the record to update.
        update_data: Dict of field-value pairs to update.
        current_version: The version the client saw when reading the record.
        tenant_id: Optional tenant_id for multi-tenant isolation.

    Returns:
        Dict with the updated record data and new version.

    Raises:
        ConcurrentUpdateError: If the version doesn't match (concurrent update).
        HTTPException: If the record is not found.
    """
    # Build WHERE clause with version check
    conditions = [
        model_class.id == uuid.UUID(record_id),
        model_class.version == current_version,
    ]
    if tenant_id:
        conditions.append(model_class.tenant_id == uuid.UUID(tenant_id))

    # Add version increment to update data
    update_values = {
        **update_data,
        "version": current_version + 1,
    }

    # Execute versioned UPDATE
    stmt = update(model_class).where(*conditions).values(**update_values)
    result = await session.execute(stmt)

    if result.rowcount == 0:
        # Check if record exists at all (wrong version vs not found)
        check_conditions = [model_class.id == uuid.UUID(record_id)]
        if tenant_id:
            check_conditions.append(model_class.tenant_id == uuid.UUID(tenant_id))

        check = await session.execute(
            select(model_class).where(*check_conditions)
        )
        existing = check.scalar_one_or_none()

        if existing is None:
            raise HTTPException(status_code=404, detail=f"{model_class.__name__} not found")
        else:
            raise ConcurrentUpdateError(
                model_class.__name__, record_id, current_version
            )

    await session.commit()

    # Fetch the updated record for response
    check_conditions = [model_class.id == uuid.UUID(record_id)]
    if tenant_id:
        check_conditions.append(model_class.tenant_id == uuid.UUID(tenant_id))

    check = await session.execute(
        select(model_class).where(*check_conditions)
    )
    updated_record = check.scalar_one_or_none()

    return {
        "id": str(updated_record.id),
        "version": updated_record.version,
        "updated": True,
    }


async def optimistic_update_with_retry(
    session: AsyncSession,
    model_class: Type,
    record_id: str,
    update_fn,
    tenant_id: Optional[str] = None,
    max_retries: int = MAX_RETRY_ATTEMPTS,
) -> Dict[str, Any]:
    """
    Perform an optimistic-locked update with automatic retry on conflict.

    Instead of providing the version and update data, provide a function
    that reads the current record, computes the update, and returns
    (current_version, update_data). If the update conflicts, the function
    is called again with fresh data.

    Args:
        session: SQLAlchemy async session.
        model_class: The SQLAlchemy model class.
        record_id: UUID of the record to update.
        update_fn: Async function(record) -> (version, update_data).
        tenant_id: Optional tenant_id for isolation.
        max_retries: Maximum retry attempts.

    Returns:
        Dict with the updated record data.

    Raises:
        ConcurrentUpdateError: If all retries are exhausted.
    """
    for attempt in range(max_retries):
        # Read current record
        conditions = [model_class.id == uuid.UUID(record_id)]
        if tenant_id:
            conditions.append(model_class.tenant_id == uuid.UUID(tenant_id))

        result = await session.execute(
            select(model_class).where(*conditions)
        )
        record = result.scalar_one_or_none()

        if record is None:
            raise HTTPException(status_code=404, detail=f"{model_class.__name__} not found")

        # Let the caller compute the update
        current_version, update_data = await update_fn(record)

        try:
            return await optimistic_update(
                session, model_class, record_id, update_data,
                current_version=current_version,
                tenant_id=tenant_id,
            )
        except ConcurrentUpdateError:
            if attempt < max_retries - 1:
                logger.info(
                    f"Optimistic lock conflict on {model_class.__name__}({record_id}), "
                    f"retry {attempt + 1}/{max_retries}"
                )
                continue
            raise

    raise ConcurrentUpdateError(model_class.__name__, record_id, -1)
