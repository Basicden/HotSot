"""
HotSot Menu Service — API Contracts.

Contract-first development: defines the request/response schemas
that other services can depend on. Changes to these contracts
must be backward-compatible or versioned.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


# ═══════════════════════════════════════════════════════════════
# REQUEST CONTRACTS
# ═══════════════════════════════════════════════════════════════

class CreateMenuRequest(BaseModel):
    """Request schema for creating a Menu entity."""
    tenant_id: str = Field(..., description="Tenant ID for multi-tenancy")
    name: str = Field(..., min_length=1, max_length=200, description="Entity name")
    description: Optional[str] = Field(None, max_length=2000, description="Description")


class UpdateMenuRequest(BaseModel):
    """Request schema for updating a Menu entity."""
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=2000)


class ListMenuRequest(BaseModel):
    """Request schema for listing Menu entities with pagination."""
    tenant_id: str = Field(..., description="Tenant ID")
    limit: int = Field(20, ge=1, le=100, description="Items per page")
    offset: int = Field(0, ge=0, description="Offset for pagination")
    filters: Optional[Dict[str, Any]] = Field(None, description="Optional filters")


# ═══════════════════════════════════════════════════════════════
# RESPONSE CONTRACTS
# ═══════════════════════════════════════════════════════════════

class MenuResponse(BaseModel):
    """Standard response schema for a Menu entity."""
    id: str
    tenant_id: str
    name: str
    description: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class MenuListResponse(BaseModel):
    """Paginated list response for Menu entities."""
    items: List[MenuResponse]
    total: int
    limit: int
    offset: int
    has_more: bool


class MenuEvent(BaseModel):
    """Kafka event schema for Menu events."""
    event_type: str = Field(..., description="Event type: created, updated, deleted")
    entity_id: str = Field(..., description="Entity UUID")
    tenant_id: str = Field(..., description="Tenant ID")
    data: Dict[str, Any] = Field(default_factory=dict, description="Event payload")
    timestamp: Optional[str] = None
