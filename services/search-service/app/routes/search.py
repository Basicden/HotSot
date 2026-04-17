"""HotSot Search Service — Routes.

V3: Dual-engine search with Elasticsearch + PostgreSQL fallback.
    - Primary: Elasticsearch (fuzzy matching, autocomplete, better relevance)
    - Fallback: PostgreSQL tsvector + ilike (when ES is unavailable)

Search Strategy:
    1. Try Elasticsearch first (if available and circuit breaker allows)
    2. Fallback to PostgreSQL tsvector + ilike if ES is down
    3. Results include 'engine' field to indicate which engine was used
"""
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional, List
from fastapi import APIRouter, HTTPException, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, or_, desc, literal_column
from shared.auth.jwt import get_current_user, require_role
from app.core.database import SearchIndexModel

router = APIRouter()
_session_factory = None
_redis_client = None
_kafka_producer = None
_es_engine = None

def set_dependencies(session_factory, redis_client=None, kafka_producer=None, es_engine=None):
    global _session_factory, _redis_client, _kafka_producer, _es_engine
    _session_factory = session_factory
    _redis_client = redis_client
    _kafka_producer = kafka_producer
    _es_engine = es_engine

async def get_session():
    if _session_factory is None:
        raise RuntimeError("Session factory not initialized")
    async with _session_factory() as session:
        yield session


def _build_tsvector_expr():
    """Build a tsvector expression from searchable columns.

    Uses the GIN-indexed 'search_tsvector' column if available (post-migration),
    otherwise falls back to inline to_tsvector() computation.
    """
    # Inline tsvector: combines name, description, cuisine, and tags
    return func.to_tsvector(
        'english',
        func.coalesce(SearchIndexModel.name, '') + ' ' +
        func.coalesce(SearchIndexModel.description, '') + ' ' +
        func.coalesce(SearchIndexModel.cuisine, '') + ' ' +
        func.coalesce(
            func.array_to_string(SearchIndexModel.tags, ' '), ''
        )
    )


@router.get("/")
async def search(
    q: Optional[str] = Query(None, description="Search query text"),
    entity_type: Optional[str] = Query(None, description="Entity type: VENDOR, MENU_ITEM, KITCHEN"),
    cuisine: Optional[str] = Query(None, description="Cuisine filter (e.g. North Indian, Chinese)"),
    city: Optional[str] = Query(None, description="City filter"),
    vendor_id: Optional[str] = Query(None, description="Filter by vendor UUID"),
    category: Optional[str] = Query(None, description="Category filter (e.g. Biryani, Burger)"),
    price_min: Optional[Decimal] = Query(None, description="Minimum price in INR", ge=0),
    price_max: Optional[Decimal] = Query(None, description="Maximum price in INR", ge=0),
    dietary: Optional[str] = Query(None, description="Dietary preference: VEG, NON_VEG, VEGAN, EGGETARIAN"),
    limit: int = Query(20, ge=1, le=100, description="Results per page"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Full-text search across vendors, menu items, kitchens.

    Dual-engine: Tries Elasticsearch first (fuzzy, better relevance),
    falls back to PostgreSQL tsvector + ilike if ES is unavailable.

    Pagination: Use limit/offset. Response includes total count.
    """
    tenant_id = user.get("claims", {}).get("tenant_id", user.get("user_id"))

    # ── Try Elasticsearch first ──────────────────────────────
    if _es_engine and _es_engine.is_available:
        es_filters = {
            "entity_type": entity_type,
            "cuisine": cuisine,
            "city": city,
            "vendor_id": vendor_id,
            "category": category,
            "dietary": dietary.upper() if dietary else None,
            "price_min": float(price_min) if price_min else None,
            "price_max": float(price_max) if price_max else None,
        }
        # Remove None values
        es_filters = {k: v for k, v in es_filters.items() if v is not None}

        es_result = await _es_engine.search(
            query=q or "",
            filters=es_filters,
            limit=limit,
            offset=offset,
            tenant_id=tenant_id,
        )

        if es_result.get("results") or es_result.get("engine") == "elasticsearch":
            # ES responded — use its results
            return {
                "query": q,
                "total": es_result.get("total", 0),
                "limit": limit,
                "offset": offset,
                "has_more": (offset + limit) < es_result.get("total", 0),
                "results": es_result.get("results", []),
                "engine": "elasticsearch",
            }

    # ── Fallback: PostgreSQL tsvector + ilike ────────────────

    # ─── Build base conditions ────────────────────────────────
    conditions = [
        SearchIndexModel.tenant_id == tenant_id,
        SearchIndexModel.is_available == True,
    ]

    # ─── Apply filters ────────────────────────────────────────
    if entity_type:
        conditions.append(SearchIndexModel.entity_type == entity_type)
    if cuisine:
        conditions.append(SearchIndexModel.cuisine == cuisine)
    if city:
        conditions.append(SearchIndexModel.city == city)
    if vendor_id:
        try:
            conditions.append(SearchIndexModel.vendor_id == uuid.UUID(vendor_id))
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid vendor_id format")
    if category:
        conditions.append(SearchIndexModel.category == category)
    if dietary:
        valid_dietary = {"VEG", "NON_VEG", "VEGAN", "EGGETARIAN"}
        dietary_upper = dietary.upper()
        if dietary_upper not in valid_dietary:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid dietary preference. Must be one of: {valid_dietary}",
            )
        conditions.append(SearchIndexModel.dietary_preference == dietary_upper)
    if price_min is not None:
        conditions.append(
            or_(
                SearchIndexModel.price_max >= price_min,
                SearchIndexModel.price_max.is_(None),
            )
        )
    if price_max is not None:
        conditions.append(
            or_(
                SearchIndexModel.price_min <= price_max,
                SearchIndexModel.price_min.is_(None),
            )
        )

    # ─── Full-text search with ilike fallback ─────────────────
    rank_expr = None
    if q:
        # tsvector expression: prefer the GIN-indexed column if populated
        tsvector_expr = func.coalesce(
            SearchIndexModel.search_tsvector,
            _build_tsvector_expr(),
        )

        # plainto_tsquery safely converts user input to tsquery
        # (handles special characters, no injection risk)
        tsquery_expr = func.plainto_tsquery('english', q)

        # Relevance ranking score
        rank_expr = func.ts_rank(tsvector_expr, tsquery_expr).label('rank')

        # Combine tsvector match with ilike fallback for partial matches
        # tsvector is fast but misses substring patterns like "bir" → "biryani"
        search_conditions = or_(
            tsvector_expr.op('@@')(tsquery_expr),
            SearchIndexModel.name.ilike(f"%{q}%"),
            SearchIndexModel.description.ilike(f"%{q}%"),
        )
        conditions.append(search_conditions)

    # ─── Build count query (for pagination metadata) ──────────
    count_query = select(func.count(SearchIndexModel.id)).where(and_(*conditions))
    count_result = await session.execute(count_query)
    total = count_result.scalar() or 0

    # ─── Build main query ─────────────────────────────────────
    if rank_expr is not None:
        query = select(SearchIndexModel, rank_expr).where(and_(*conditions))
    else:
        query = select(SearchIndexModel).where(and_(*conditions))

    # ─── Ordering: relevance first, then popularity ───────────
    if rank_expr is not None:
        query = query.order_by(
            desc(rank_expr),
            desc(SearchIndexModel.popularity_score),
        )
    else:
        query = query.order_by(desc(SearchIndexModel.popularity_score))

    # ─── Pagination ───────────────────────────────────────────
    query = query.limit(limit).offset(offset)

    result = await session.execute(query)

    # ─── Format results ───────────────────────────────────────
    items = []
    if rank_expr is not None:
        rows = result.all()
        for row in rows:
            i = row[0]  # SearchIndexModel instance
            rank = row[1]  # ts_rank score
            items.append(_format_result(i, rank))
    else:
        scalars = result.scalars().all()
        for i in scalars:
            items.append(_format_result(i, None))

    return {
        "query": q,
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": (offset + limit) < total,
        "results": items,
        "engine": "postgresql_tsvector",
    }


def _format_result(item: SearchIndexModel, rank: Optional[float]) -> dict:
    """Format a search result with scoring information."""
    result = {
        "entity_type": item.entity_type,
        "entity_id": str(item.entity_id),
        "name": item.name,
        "description": item.description,
        "cuisine": item.cuisine,
        "category": item.category,
        "city": item.city,
        "dietary_preference": item.dietary_preference,
        "price_min": item.price_min,
        "price_max": item.price_max,
        "popularity_score": item.popularity_score,
        "relevance_score": round(float(rank), 4) if rank else 0.0,
        "tags": item.tags or [],
    }
    # Only include vendor_id when it's set
    if item.vendor_id:
        result["vendor_id"] = str(item.vendor_id)
    return result


@router.post("/index")
async def index_entity(
    entity_type: str,
    entity_id: str,
    name: str,
    description: str = None,
    cuisine: str = None,
    tags: list = None,
    city: str = None,
    vendor_id: str = None,
    category: str = None,
    price_min: Optional[Decimal] = None,
    price_max: Optional[Decimal] = None,
    dietary: str = None,
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Add or update entity in search index.

    Supports full metadata including vendor, category, price range,
    and dietary preferences for filtered search.
    """
    tenant_id = user.get("claims", {}).get("tenant_id", user.get("user_id"))

    # Validate dietary preference
    if dietary:
        valid_dietary = {"VEG", "NON_VEG", "VEGAN", "EGGETARIAN"}
        dietary_upper = dietary.upper()
        if dietary_upper not in valid_dietary:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid dietary preference. Must be one of: {valid_dietary}",
            )
        dietary = dietary_upper

    # Validate price range
    if price_min is not None and price_max is not None and price_min > price_max:
        raise HTTPException(
            status_code=400,
            detail="price_min cannot be greater than price_max",
        )

    idx = SearchIndexModel(
        tenant_id=tenant_id,
        entity_type=entity_type,
        entity_id=uuid.UUID(entity_id),
        vendor_id=uuid.UUID(vendor_id) if vendor_id else None,
        name=name,
        description=description,
        cuisine=cuisine,
        tags=tags or [],
        city=city,
        category=category,
        price_min=price_min,
        price_max=price_max,
        dietary_preference=dietary,
    )
    session.add(idx)
    await session.commit()

    # Also index in Elasticsearch (if available)
    if _es_engine and _es_engine.is_available:
        doc = {
            "entity_type": entity_type,
            "entity_id": entity_id,
            "vendor_id": vendor_id,
            "name": name,
            "description": description,
            "cuisine": cuisine,
            "tags": tags or [],
            "city": city,
            "category": category,
            "price_min": float(price_min) if price_min else None,
            "price_max": float(price_max) if price_max else None,
            "dietary_preference": dietary,
            "tenant_id": tenant_id,
            "is_available": True,
            "popularity_score": 0.0,
        }
        await _es_engine.index_document(doc)

    return {"indexed": True, "entity_id": entity_id, "engine": "elasticsearch+postgresql"}


@router.get("/suggest")
async def suggest(
    q: str = Query(..., min_length=1, max_length=100, description="Prefix for autocomplete"),
    entity_type: Optional[str] = None,
    limit: int = Query(10, ge=1, le=50),
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Autocomplete suggestions for search input.

    Uses ilike prefix matching for fast autocomplete.
    Not full-text — designed for type-ahead UI.
    """
    tenant_id = user.get("claims", {}).get("tenant_id", user.get("user_id"))

    conditions = [
        SearchIndexModel.tenant_id == tenant_id,
        SearchIndexModel.is_available == True,
        SearchIndexModel.name.ilike(f"{q}%"),  # Prefix match for autocomplete
    ]
    if entity_type:
        conditions.append(SearchIndexModel.entity_type == entity_type)

    query = (
        select(SearchIndexModel.name, SearchIndexModel.entity_type, SearchIndexModel.cuisine)
        .where(and_(*conditions))
        .order_by(desc(SearchIndexModel.popularity_score))
        .limit(limit)
    )
    result = await session.execute(query)
    rows = result.all()

    return {
        "query": q,
        "suggestions": [
            {"name": r[0], "entity_type": r[1], "cuisine": r[2]}
            for r in rows
        ],
    }
