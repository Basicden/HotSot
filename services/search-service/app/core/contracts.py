"""
HotSot Search Service — Service Contracts.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from decimal import Decimal
from typing import Any, Dict, List, Optional


class SearchServiceContract(ABC):
    """Contract that the Search Service must fulfill."""

    @abstractmethod
    async def search(
        self, query: Optional[str], tenant_id: str,
        entity_type: Optional[str] = None,
        cuisine: Optional[str] = None,
        city: Optional[str] = None,
        price_min: Optional[Decimal] = None,
        price_max: Optional[Decimal] = None,
        dietary: Optional[str] = None,
        limit: int = 20, offset: int = 0,
    ) -> Dict[str, Any]:
        """Full-text search across vendors, menu items, kitchens."""
        raise NotImplementedError("search not implemented")

    @abstractmethod
    async def index_entity(
        self, entity_type: str, entity_id: str, name: str,
        tenant_id: str, **kwargs,
    ) -> Dict[str, Any]:
        """Add or update entity in search index."""
        raise NotImplementedError("index_entity not implemented")

    @abstractmethod
    async def suggest(
        self, query: str, tenant_id: str,
        entity_type: Optional[str] = None, limit: int = 10,
    ) -> Dict[str, Any]:
        """Autocomplete suggestions."""
        raise NotImplementedError("suggest not implemented")

    @abstractmethod
    async def remove_entity(self, entity_id: str, tenant_id: str) -> Dict[str, Any]:
        """Remove entity from search index."""
        raise NotImplementedError("remove_entity not implemented")
