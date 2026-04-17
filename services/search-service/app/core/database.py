"""HotSot Search Service — Database Models.

Migration Note (GIN Index):
    After deploying, run the following SQL to enable fast full-text search:

    ALTER TABLE search_index ADD COLUMN IF NOT EXISTS search_tsvector TSVECTOR
        GENERATED ALWAYS AS (
            to_tsvector('english',
                coalesce(name, '') || ' ' ||
                coalesce(description, '') || ' ' ||
                coalesce(cuisine, '') || ' ' ||
                coalesce(array_to_string(tags, ' '), '')
            )
        ) STORED;

    CREATE INDEX IF NOT EXISTS idx_search_tsvector_gin
        ON search_index USING GIN (search_tsvector);

    This adds a GIN-indexed TSVECTOR column that is automatically maintained
    by PostgreSQL. Until the migration runs, the search service uses
    to_tsvector() inline in queries (slower but functional).
"""
from sqlalchemy import Column, String, Integer, Float, Boolean, Text, Index
from sqlalchemy.dialects.postgresql import UUID as PG_UUID, JSONB, TSVECTOR, Numeric
from shared.utils.database import BaseModel


class SearchIndexModel(BaseModel):
    __tablename__ = "search_index"
    entity_type = Column(String(20), nullable=False)  # VENDOR/MENU_ITEM/KITCHEN
    entity_id = Column(PG_UUID(as_uuid=True), nullable=False, index=True)
    vendor_id = Column(PG_UUID(as_uuid=True), nullable=True, index=True)  # Filter by vendor
    name = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    tags = Column(JSONB, default=list)
    category = Column(String(100), nullable=True, index=True)  # e.g. "Biryani", "Burger", "Thali"
    cuisine = Column(String(50), nullable=True)
    city = Column(String(100), nullable=True)
    is_available = Column(Boolean, default=True)
    price_min = Column(Numeric(10, 2), nullable=True)  # Minimum price in INR
    price_max = Column(Numeric(10, 2), nullable=True)  # Maximum price in INR
    dietary_preference = Column(String(20), nullable=True, index=True)  # VEG / NON_VEG / VEGAN / EGGETARIAN
    search_vector = Column(Text, nullable=True)  # Legacy — kept for backward compat
    search_tsvector = Column(TSVECTOR, nullable=True)  # GIN-indexed full-text vector (set by migration)
    popularity_score = Column(Float, default=0.0)

    __table_args__ = (
        Index('ix_search_index_dietary', 'dietary_preference'),
        Index('ix_search_index_category', 'category'),
    )
