"""HotSot Search Service — Database Models."""
from sqlalchemy import Column, String, Integer, Float, Boolean, DateTime, Text, Index
from sqlalchemy.dialects.postgresql import UUID as PG_UUID, JSONB
from shared.utils.database import BaseModel


class SearchIndexModel(BaseModel):
    __tablename__ = "search_index"
    entity_type = Column(String(20), nullable=False)  # VENDOR/MENU_ITEM/KITCHEN
    entity_id = Column(PG_UUID(as_uuid=True), nullable=False, index=True)
    name = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    tags = Column(JSONB, default=list)
    cuisine = Column(String(50), nullable=True)
    city = Column(String(100), nullable=True)
    is_available = Column(Boolean, default=True)
    search_vector = Column(Text, nullable=True)  # Full-text search vector
    popularity_score = Column(Float, default=0.0)
