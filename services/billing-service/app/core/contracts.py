"""
HotSot Billing Service — Service Contracts.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from decimal import Decimal
from typing import Any, Dict, Optional


class BillingServiceContract(ABC):
    """Contract that the Billing Service must fulfill."""

    @abstractmethod
    async def generate_invoice(
        self, vendor_id: str, period_start: str, period_end: str,
        tenant_id: str, commission_rate: Optional[Decimal] = None,
    ) -> Dict[str, Any]:
        """Generate an invoice for a vendor billing period."""
        raise NotImplementedError("generate_invoice not implemented")

    @abstractmethod
    async def finalize_invoice(
        self, invoice_id: str, total_orders: int, total_revenue: Decimal,
        tenant_id: str,
    ) -> Dict[str, Any]:
        """Finalize a DRAFT invoice with actual order data."""
        raise NotImplementedError("finalize_invoice not implemented")

    @abstractmethod
    async def process_payout(
        self, vendor_id: str, amount: Decimal, tenant_id: str,
        method: str = "BANK_TRANSFER", invoice_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Process a payout to a vendor."""
        raise NotImplementedError("process_payout not implemented")

    @abstractmethod
    async def get_vendor_invoices(
        self, vendor_id: str, tenant_id: str, limit: int = 10,
    ) -> Dict[str, Any]:
        """Get invoices for a vendor."""
        raise NotImplementedError("get_vendor_invoices not implemented")
