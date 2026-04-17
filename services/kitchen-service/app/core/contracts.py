"""
HotSot Kitchen Service — Service Contracts.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from decimal import Decimal
from typing import Any, Dict, List, Optional


class KitchenServiceContract(ABC):
    """Contract that the Kitchen Service must fulfill."""

    @abstractmethod
    async def accept_order(self, order_id: str, kitchen_id: str, tenant_id: str) -> Dict[str, Any]:
        """Accept an incoming order for preparation."""
        raise NotImplementedError("accept_order not implemented")

    @abstractmethod
    async def start_preparation(self, order_id: str, kitchen_id: str, tenant_id: str) -> Dict[str, Any]:
        """Mark order as being prepared."""
        raise NotImplementedError("start_preparation not implemented")

    @abstractmethod
    async def mark_ready(self, order_id: str, kitchen_id: str, tenant_id: str) -> Dict[str, Any]:
        """Mark order as ready for pickup."""
        raise NotImplementedError("mark_ready not implemented")

    @abstractmethod
    async def update_eta(self, order_id: str, estimated_minutes: int, tenant_id: str) -> Dict[str, Any]:
        """Update estimated preparation time."""
        raise NotImplementedError("update_eta not implemented")

    @abstractmethod
    async def get_kitchen_status(self, kitchen_id: str, tenant_id: str) -> Dict[str, Any]:
        """Get current kitchen load and capacity."""
        raise NotImplementedError("get_kitchen_status not implemented")

    @abstractmethod
    async def get_kitchen_orders(self, kitchen_id: str, tenant_id: str, status: Optional[str] = None) -> Dict[str, Any]:
        """List orders for a kitchen with optional status filter."""
        raise NotImplementedError("get_kitchen_orders not implemented")
