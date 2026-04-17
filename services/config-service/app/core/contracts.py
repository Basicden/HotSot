"""
HotSot Config Service — API Contracts.

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

class CreateConfigRequest(BaseModel):
    """Request schema for creating a Config entity."""
    tenant_id: str = Field(..., description="Tenant ID for multi-tenancy")
    name: str = Field(..., min_length=1, max_length=200, description="Entity name")
    description: Optional[str] = Field(None, max_length=2000, description="Description")


class UpdateConfigRequest(BaseModel):
    """Request schema for updating a Config entity."""
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=2000)


class ListConfigRequest(BaseModel):
    """Request schema for listing Config entities with pagination."""
    tenant_id: str = Field(..., description="Tenant ID")
    limit: int = Field(20, ge=1, le=100, description="Items per page")
    offset: int = Field(0, ge=0, description="Offset for pagination")
    filters: Optional[Dict[str, Any]] = Field(None, description="Optional filters")


# ═══════════════════════════════════════════════════════════════
# RESPONSE CONTRACTS
# ═══════════════════════════════════════════════════════════════

class ConfigResponse(BaseModel):
    """Standard response schema for a Config entity."""
    id: str
    tenant_id: str
    name: str
    description: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class ConfigListResponse(BaseModel):
    """Paginated list response for Config entities."""
    items: List[ConfigResponse]
    total: int
    limit: int
    offset: int
    has_more: bool


class ConfigEvent(BaseModel):
    """Kafka event schema for Config events."""
    event_type: str = Field(..., description="Event type: created, updated, deleted")
    entity_id: str = Field(..., description="Entity UUID")
    tenant_id: str = Field(..., description="Tenant ID")
    data: Dict[str, Any] = Field(default_factory=dict, description="Event payload")
    timestamp: Optional[str] = None
