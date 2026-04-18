"""
HotSot Order Service — Service Contracts.

Defines the expected interface for the Order Service.
All public methods must be implemented — NotImplementedError is raised
for any method that lacks a concrete implementation.

This contract-first approach ensures:
    - All service methods are explicitly documented
    - Missing implementations are caught at startup, not runtime
    - Service boundaries are clear for cross-service communication
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from decimal import Decimal
from typing import Any, Dict, List, Optional


class OrderServiceContract(ABC):
    """Contract that the Order Service must fulfill."""

    @abstractmethod
    async def create_order(
        self,
        tenant_id: str,
        user_id: str,
        vendor_id: str,
        items: List[Dict[str, Any]],
        total_amount: Decimal,
        delivery_type: str = "PICKUP",
    ) -> Dict[str, Any]:
        """Create a new order.

        Args:
            tenant_id: Tenant identifier for multi-tenancy.
            user_id: User placing the order.
            vendor_id: Vendor/kitchen preparing the order.
            items: List of menu items with quantities.
            total_amount: Total order amount in INR.
            delivery_type: Type of fulfillment (PICKUP, DELIVERY).

        Returns:
            Dict with order_id, status, and estimated preparation time.

        Raises:
            NotImplementedError: If not implemented by the service.
        """
        raise NotImplementedError("create_order not implemented")

    @abstractmethod
    async def get_order(self, order_id: str, tenant_id: str) -> Dict[str, Any]:
        """Retrieve order details by ID."""
        raise NotImplementedError("get_order not implemented")

    @abstractmethod
    async def update_order_status(
        self, order_id: str, status: str, tenant_id: str, version: int = None
    ) -> Dict[str, Any]:
        """Update order status with state machine validation.

        Valid transitions:
            PENDING -> CONFIRMED -> PREPARING -> READY -> PICKED_UP -> COMPLETED
            Any state -> CANCELLED (with compensation)

        Args:
            order_id: Order UUID.
            status: New status to transition to.
            tenant_id: Tenant identifier.
            version: Optional optimistic locking version.
        """
        raise NotImplementedError("update_order_status not implemented")

    @abstractmethod
    async def cancel_order(
        self, order_id: str, reason: str, tenant_id: str
    ) -> Dict[str, Any]:
        """Cancel an order and trigger compensation flow."""
        raise NotImplementedError("cancel_order not implemented")

    @abstractmethod
    async def list_orders(
        self,
        tenant_id: str,
        user_id: Optional[str] = None,
        vendor_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """List orders with filtering and pagination."""
        raise NotImplementedError("list_orders not implemented")
