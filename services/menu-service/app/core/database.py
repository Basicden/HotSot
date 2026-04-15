"""HotSot Menu Service — Database Models."""
import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Integer, Float, Boolean, DateTime, Text, Index
from sqlalchemy.dialects.postgresql import UUID as PG_UUID, JSONB
from shared.utils.database import Base as SharedBase, BaseModelMixin

class Base(SharedBase):
    pass

class MenuItemModel(Base, BaseModelMixin):
    __tablename__ = "menu_items"
    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(PG_UUID(as_uuid=True), nullable=False, index=True)
    vendor_id = Column(PG_UUID(as_uuid=True), nullable=False, index=True)
    kitchen_id = Column(PG_UUID(as_uuid=True), nullable=True, index=True)
    name = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    category = Column(String(50), nullable=True)
    cuisine = Column(String(50), nullable=True)
    price = Column(Float, nullable=False)
    is_veg = Column(Boolean, default=True)
    is_available = Column(Boolean, default=True)
    prep_time_seconds = Column(Integer, default=300)
    batch_category = Column(String(20), nullable=True)  # GRILL/FRYER/COLD/RICE_BOWL
    spice_level = Column(Integer, default=2)
    allergens = Column(JSONB, default=list)
    image_url = Column(String(500), nullable=True)
    sort_order = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

class MenuCategoryModel(Base, BaseModelMixin):
    __tablename__ = "menu_categories"
    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(PG_UUID(as_uuid=True), nullable=False, index=True)
    vendor_id = Column(PG_UUID(as_uuid=True), nullable=False, index=True)
    name = Column(String(100), nullable=False)
    display_order = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)
