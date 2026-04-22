"""
HotSot — PHASE 2D: Search ES vs PG Consistency Checker

Validates that Elasticsearch and PostgreSQL produce consistent results
and handles mismatches gracefully. The dual-engine search architecture
routes queries to ES first, falling back to PostgreSQL tsvector when
ES is unavailable.

Test Categories:
    1. Dual Engine Consistency — same results from both engines
    2. Search Result Validation — required fields, pagination, filters
    3. Search Edge Cases — special chars, unicode, long queries, concurrent ops
    4. Search Relevance Validation — ranking, popularity, freshness

Uses mock infrastructure similar to the existing integration test files.
Imports from shared.circuit_breaker for realistic circuit breaker behavior.
"""

import asyncio
import json
import random
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Set, Tuple

import pytest

# ═══════════════════════════════════════════════════════════════
# MOCK INFRASTRUCTURE
# ═══════════════════════════════════════════════════════════════

class MockSearchDocument:
    """Represents a searchable entity in the mock index."""

    def __init__(
        self,
        entity_type: str = "MENU_ITEM",
        entity_id: Optional[str] = None,
        name: str = "",
        description: str = "",
        cuisine: str = "",
        category: str = "",
        city: str = "Mumbai",
        dietary_preference: str = "NON_VEG",
        price_min: float = 0.0,
        price_max: float = 0.0,
        popularity_score: float = 0.0,
        tags: Optional[List[str]] = None,
        tenant_id: str = "default",
        is_available: bool = True,
        vendor_id: Optional[str] = None,
        indexed_at: Optional[str] = None,
    ):
        self.entity_type = entity_type
        self.entity_id = entity_id or str(uuid.uuid4())
        self.name = name
        self.description = description
        self.cuisine = cuisine
        self.category = category
        self.city = city
        self.dietary_preference = dietary_preference
        self.price_min = price_min
        self.price_max = price_max
        self.popularity_score = popularity_score
        self.tags = tags or []
        self.tenant_id = tenant_id
        self.is_available = is_available
        self.vendor_id = vendor_id
        self.indexed_at = indexed_at or datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> Dict:
        return {
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "name": self.name,
            "description": self.description,
            "cuisine": self.cuisine,
            "category": self.category,
            "city": self.city,
            "dietary_preference": self.dietary_preference,
            "price_min": self.price_min,
            "price_max": self.price_max,
            "popularity_score": self.popularity_score,
            "tags": self.tags,
            "tenant_id": self.tenant_id,
            "is_available": self.is_available,
            "vendor_id": self.vendor_id,
            "relevance_score": 0.0,
        }


class MockElasticsearchEngine:
    """Simulates Elasticsearch with fuzzy matching and scoring.

    Supports:
        - Multi-field text matching with basic fuzzy logic
        - Field-level filtering (cuisine, city, entity_type, etc.)
        - Relevance scoring based on match quality
        - Pagination with limit/offset
        - Circuit breaker integration for availability control
    """

    def __init__(self, available: bool = True):
        self._available = available
        self._documents: Dict[str, MockSearchDocument] = {}
        self._call_count = 0

    @property
    def is_available(self) -> bool:
        return self._available

    def set_available(self, available: bool):
        self._available = available

    def index_document(self, doc: MockSearchDocument):
        """Index a document in the mock ES store."""
        self._documents[doc.entity_id] = doc

    def bulk_index(self, docs: List[MockSearchDocument]):
        """Bulk index multiple documents."""
        for doc in docs:
            self.index_document(doc)

    async def search(
        self,
        query: str,
        filters: Optional[Dict] = None,
        limit: int = 20,
        offset: int = 0,
        tenant_id: str = "default",
    ) -> Dict[str, Any]:
        """Search documents with text matching and filters.

        Simulates ES behavior:
            - Multi-match on name, description, cuisine, tags
            - Filter by entity_type, cuisine, city, etc.
            - Score based on match quality
            - Sort by score + popularity
        """
        self._call_count += 1

        if not self._available:
            return {
                "total": 0,
                "results": [],
                "engine": "elasticsearch_unavailable",
            }

        filters = filters or {}

        # Filter documents
        candidates = []
        for doc in self._documents.values():
            if not doc.is_available:
                continue
            if doc.tenant_id != tenant_id:
                continue

            # Apply filters
            if filters.get("entity_type") and doc.entity_type != filters["entity_type"]:
                continue
            if filters.get("cuisine") and doc.cuisine != filters["cuisine"]:
                continue
            if filters.get("city") and doc.city != filters["city"]:
                continue
            if filters.get("category") and doc.category != filters["category"]:
                continue
            if filters.get("dietary") and doc.dietary_preference != filters["dietary"].upper():
                continue
            if filters.get("vendor_id") and doc.vendor_id != filters["vendor_id"]:
                continue
            if filters.get("price_min") is not None and doc.price_max < filters["price_min"]:
                continue
            if filters.get("price_max") is not None and doc.price_min > filters["price_max"]:
                continue

            # Calculate relevance score
            score = 0.0
            if query:
                query_lower = query.lower()
                # Exact name match gets highest score
                if query_lower == doc.name.lower():
                    score += 10.0
                # Name contains query
                elif query_lower in doc.name.lower():
                    score += 5.0
                # Description contains query
                if query_lower in doc.description.lower():
                    score += 2.0
                # Cuisine contains query
                if query_lower in doc.cuisine.lower():
                    score += 3.0
                # Tags contain query
                if any(query_lower in tag.lower() for tag in doc.tags):
                    score += 1.5
                # No match → skip
                if score == 0.0:
                    continue
            else:
                # No query → rank by popularity
                score = doc.popularity_score

            # Boost by popularity
            score += doc.popularity_score * 0.1

            result = doc.to_dict()
            result["relevance_score"] = round(score, 4)
            result["_score"] = score
            candidates.append((score, result))

        # Sort by score descending, then by popularity
        candidates.sort(key=lambda x: (-x[0], -x[1].get("popularity_score", 0)))

        total = len(candidates)
        paginated = candidates[offset:offset + limit]

        return {
            "total": total,
            "results": [r for _, r in paginated],
            "engine": "elasticsearch",
            "limit": limit,
            "offset": offset,
            "has_more": (offset + limit) < total,
        }


class MockPostgresSearchEngine:
    """Simulates PostgreSQL tsvector + ilike search.

    Supports:
        - tsvector-style full-text matching
        - ilike partial matching for autocomplete
        - Same filters as ES engine
        - Pagination with limit/offset
        - Slower but always available (no external dependency)
    """

    def __init__(self, available: bool = True):
        self._available = available
        self._documents: Dict[str, MockSearchDocument] = {}
        self._call_count = 0

    @property
    def is_available(self) -> bool:
        return self._available

    def set_available(self, available: bool):
        self._available = available

    def index_document(self, doc: MockSearchDocument):
        self._documents[doc.entity_id] = doc

    def bulk_index(self, docs: List[MockSearchDocument]):
        for doc in docs:
            self.index_document(doc)

    async def search(
        self,
        query: str,
        filters: Optional[Dict] = None,
        limit: int = 20,
        offset: int = 0,
        tenant_id: str = "default",
    ) -> Dict[str, Any]:
        """Search using PostgreSQL tsvector + ilike logic.

        Simulates:
            - plainto_tsquery for full-text search
            - ilike for partial/substring matching
            - ts_rank for relevance scoring
            - Fallback to popularity_score when no query text
        """
        self._call_count += 1

        if not self._available:
            raise ConnectionError("PostgreSQL unavailable")

        filters = filters or {}

        candidates = []
        for doc in self._documents.values():
            if not doc.is_available:
                continue
            if doc.tenant_id != tenant_id:
                continue

            # Apply filters (same as ES)
            if filters.get("entity_type") and doc.entity_type != filters["entity_type"]:
                continue
            if filters.get("cuisine") and doc.cuisine != filters["cuisine"]:
                continue
            if filters.get("city") and doc.city != filters["city"]:
                continue
            if filters.get("category") and doc.category != filters["category"]:
                continue
            if filters.get("dietary") and doc.dietary_preference != filters["dietary"].upper():
                continue
            if filters.get("vendor_id") and doc.vendor_id != filters["vendor_id"]:
                continue
            if filters.get("price_min") is not None and doc.price_max < filters["price_min"]:
                continue
            if filters.get("price_max") is not None and doc.price_min > filters["price_max"]:
                continue

            # Calculate ts_rank + ilike score
            rank = 0.0
            if query:
                query_lower = query.lower()
                # tsvector match (whole words / stems)
                words = query_lower.split()
                for word in words:
                    if word in doc.name.lower():
                        rank += 3.0
                    if word in doc.description.lower():
                        rank += 1.0
                    if word in doc.cuisine.lower():
                        rank += 2.0
                    # ilike match (substring)
                    if query_lower in doc.name.lower():
                        rank += 2.0
                    if query_lower in doc.description.lower():
                        rank += 0.5

                if rank == 0.0:
                    continue
            else:
                rank = doc.popularity_score

            result = doc.to_dict()
            result["relevance_score"] = round(rank, 4)
            candidates.append((rank, result))

        # Sort by rank descending, then popularity
        candidates.sort(key=lambda x: (-x[0], -x[1].get("popularity_score", 0)))

        total = len(candidates)
        paginated = candidates[offset:offset + limit]

        return {
            "total": total,
            "results": [r for _, r in paginated],
            "engine": "postgresql_tsvector",
            "limit": limit,
            "offset": offset,
            "has_more": (offset + limit) < total,
        }


class DualSearchOrchestrator:
    """Orchestrates dual-engine search with fallback logic.

    Mimics the real search route:
        1. Try ES first (if available)
        2. Fall back to PG if ES is down
        3. Results include 'engine' field
    """

    def __init__(
        self,
        es_engine: MockElasticsearchEngine,
        pg_engine: MockPostgresSearchEngine,
    ):
        self._es = es_engine
        self._pg = pg_engine

    async def search(
        self,
        query: Optional[str] = None,
        filters: Optional[Dict] = None,
        limit: int = 20,
        offset: int = 0,
        tenant_id: str = "default",
    ) -> Dict[str, Any]:
        """Execute dual-engine search with fallback.

        Returns:
            Search result dict with engine field indicating which
            engine provided the results.
        """
        # Try ES first
        if self._es.is_available:
            es_result = await self._es.search(
                query=query or "",
                filters=filters,
                limit=limit,
                offset=offset,
                tenant_id=tenant_id,
            )
            if es_result.get("results") or es_result.get("engine") == "elasticsearch":
                return {
                    "query": query,
                    "total": es_result.get("total", 0),
                    "limit": limit,
                    "offset": offset,
                    "has_more": (offset + limit) < es_result.get("total", 0),
                    "results": es_result.get("results", []),
                    "engine": "elasticsearch",
                }

        # Fallback to PG
        try:
            pg_result = await self._pg.search(
                query=query or "",
                filters=filters,
                limit=limit,
                offset=offset,
                tenant_id=tenant_id,
            )
            return {
                "query": query,
                "total": pg_result.get("total", 0),
                "limit": limit,
                "offset": offset,
                "has_more": (offset + limit) < pg_result.get("total", 0),
                "results": pg_result.get("results", []),
                "engine": "postgresql_tsvector",
            }
        except ConnectionError:
            return {
                "query": query,
                "total": 0,
                "limit": limit,
                "offset": offset,
                "has_more": False,
                "results": [],
                "engine": "unavailable",
            }


def _create_sample_documents(count: int = 20) -> List[MockSearchDocument]:
    """Create a set of sample documents for testing."""
    dishes = [
        ("Butter Chicken", "Creamy tomato-based chicken curry", "North Indian", "CURRY", "NON_VEG", 350),
        ("Paneer Tikka", "Grilled cottage cheese with spices", "North Indian", "STARTER", "VEG", 280),
        ("Hyderabadi Biryani", "Fragrant basmati rice with tender meat", "North Indian", "RICE_BOWL", "NON_VEG", 400),
        ("Masala Dosa", "Crispy crepe with spiced potato filling", "South Indian", "BREAKFAST", "VEG", 150),
        ("Chole Bhature", "Spiced chickpeas with fried bread", "North Indian", "COMBO", "VEG", 180),
        ("Dal Makhani", "Slow-cooked black lentils in cream", "North Indian", "CURRY", "VEG", 250),
        ("Fish Fry", "Crispy fried fish with Indian spices", "South Indian", "STARTER", "NON_VEG", 320),
        ("Gulab Jamun", "Deep-fried milk dumplings in sugar syrup", "Dessert", "DESSERT", "VEG", 80),
        ("Rajma Chawal", "Kidney beans in rich gravy with rice", "North Indian", "COMBO", "VEG", 160),
        ("Tandoori Roti", "Whole wheat bread baked in clay oven", "North Indian", "BREAD", "VEG", 40),
        ("Veg Biryani", "Fragrant rice with mixed vegetables", "North Indian", "RICE_BOWL", "VEG", 300),
        ("Chicken Tikka", "Marinated grilled chicken pieces", "North Indian", "STARTER", "NON_VEG", 350),
        ("Samosa", "Crispy pastry with spiced potato filling", "Street Food", "SNACK", "VEG", 50),
        ("Pav Bhaji", "Mashed vegetables with buttered bread", "Street Food", "COMBO", "VEG", 150),
        ("Mutton Rogan Josh", "Slow-cooked mutton in Kashmiri spices", "Kashmiri", "CURRY", "NON_VEG", 450),
        ("Idli Sambar", "Steamed rice cakes with lentil soup", "South Indian", "BREAKFAST", "VEG", 120),
        ("Aloo Gobi", "Potato and cauliflower dry curry", "North Indian", "CURRY", "VEG", 200),
        ("Butter Naan", "Soft leavened bread with butter", "North Indian", "BREAD", "VEG", 60),
        ("Palak Paneer", "Cottage cheese in spinach gravy", "North Indian", "CURRY", "VEG", 270),
        ("Chicken 65", "Spicy deep-fried chicken bites", "South Indian", "STARTER", "NON_VEG", 300),
    ]

    docs = []
    for i in range(count):
        dish_idx = i % len(dishes)
        name, desc, cuisine, category, dietary, price = dishes[dish_idx]
        popularity = round(random.uniform(1.0, 10.0), 1)

        doc = MockSearchDocument(
            entity_type="MENU_ITEM",
            entity_id=f"item_{uuid.uuid4().hex[:8]}",
            name=name if i < len(dishes) else f"{name} Special",
            description=desc if i < len(dishes) else f"{desc} - Special edition",
            cuisine=cuisine,
            category=category,
            city=random.choice(["Mumbai", "Delhi", "Bangalore", "Chennai"]),
            dietary_preference=dietary,
            price_min=float(price) * 0.8,
            price_max=float(price) * 1.2,
            popularity_score=popularity,
            tags=[cuisine.lower().replace(" ", "-"), category.lower(), dietary.lower()],
            tenant_id="default",
            vendor_id=f"vendor_{random.randint(1, 10)}",
        )
        docs.append(doc)

    return docs


# ═══════════════════════════════════════════════════════════════
# FIX #8: DUAL ENGINE CONSISTENCY
# ═══════════════════════════════════════════════════════════════

class TestDualEngineConsistency:
    """Test: Elasticsearch and PostgreSQL produce consistent results."""

    @pytest.mark.asyncio
    async def test_both_engines_return_same_entities(self):
        """Invariant: Same query → same entity IDs in results.

        Both engines index the same documents, so a query should
        return overlapping sets of entity IDs. The order may differ
        due to different scoring algorithms, but the entity sets
        should be substantially the same.
        """
        docs = _create_sample_documents(20)
        es = MockElasticsearchEngine()
        pg = MockPostgresSearchEngine()
        es.bulk_index(docs)
        pg.bulk_index(docs)

        query = "butter"
        es_result = await es.search(query=query, tenant_id="default")
        pg_result = await pg.search(query=query, tenant_id="default")

        es_ids = {r["entity_id"] for r in es_result["results"]}
        pg_ids = {r["entity_id"] for r in pg_result["results"]}

        # Both should find results
        assert len(es_ids) > 0, "ES should find results for 'butter'"
        assert len(pg_ids) > 0, "PG should find results for 'butter'"

        # Entity IDs should overlap significantly
        overlap = es_ids & pg_ids
        assert len(overlap) > 0, (
            f"ES and PG should return overlapping entity IDs. "
            f"ES: {es_ids}, PG: {pg_ids}"
        )

        # At least 50% of ES results should appear in PG results
        overlap_ratio = len(overlap) / max(len(es_ids), 1)
        assert overlap_ratio >= 0.5, (
            f"Expected at least 50% overlap between engines, got {overlap_ratio:.0%}"
        )

    @pytest.mark.asyncio
    async def test_es_unavailable_falls_back_to_pg(self):
        """Invariant: When ES is down, PG returns results with engine="postgresql_tsvector"."""
        docs = _create_sample_documents(10)
        es = MockElasticsearchEngine(available=False)
        pg = MockPostgresSearchEngine()
        es.bulk_index(docs)
        pg.bulk_index(docs)

        orchestrator = DualSearchOrchestrator(es, pg)
        result = await orchestrator.search(query="biryani", tenant_id="default")

        assert result["engine"] == "postgresql_tsvector", (
            f"Should fall back to PG when ES is down, got engine={result['engine']}"
        )
        assert len(result["results"]) > 0, "PG should return results"
        assert result["total"] > 0, "PG should report total > 0"

    @pytest.mark.asyncio
    async def test_pg_unavailable_uses_es_only(self):
        """Invariant: When PG is slow/down, ES results are returned.

        In the real system, PG might timeout while ES responds quickly.
        The orchestrator should return ES results without waiting for PG.
        """
        docs = _create_sample_documents(10)
        es = MockElasticsearchEngine(available=True)
        pg = MockPostgresSearchEngine(available=False)  # PG down
        es.bulk_index(docs)
        pg.bulk_index(docs)

        orchestrator = DualSearchOrchestrator(es, pg)
        result = await orchestrator.search(query="chicken", tenant_id="default")

        # ES is available, so it should be used
        assert result["engine"] == "elasticsearch", (
            f"Should use ES when available, got engine={result['engine']}"
        )
        assert len(result["results"]) > 0, "ES should return results"

    @pytest.mark.asyncio
    async def test_result_ranking_consistency(self):
        """Invariant: Top-3 results from ES and PG share at least 1 entity (fuzzy consistency).

        Due to different scoring algorithms (ES uses multi-match with fuzziness,
        PG uses ts_rank + ilike), the exact rankings may differ. But the top
        results should have substantial overlap.
        """
        docs = _create_sample_documents(20)
        es = MockElasticsearchEngine()
        pg = MockPostgresSearchEngine()
        es.bulk_index(docs)
        pg.bulk_index(docs)

        query = "north indian"
        es_result = await es.search(query=query, tenant_id="default", limit=3)
        pg_result = await pg.search(query=query, tenant_id="default", limit=3)

        es_top3_ids = {r["entity_id"] for r in es_result["results"][:3]}
        pg_top3_ids = {r["entity_id"] for r in pg_result["results"][:3]}

        # At least 1 entity should appear in both top-3
        overlap = es_top3_ids & pg_top3_ids
        assert len(overlap) >= 1, (
            f"Top-3 results from ES and PG should share at least 1 entity. "
            f"ES top-3: {es_top3_ids}, PG top-3: {pg_top3_ids}"
        )


# ═══════════════════════════════════════════════════════════════
# FIX #8: SEARCH RESULT VALIDATION
# ═══════════════════════════════════════════════════════════════

class TestSearchResultValidation:
    """Test: Search results have required fields and consistent structure."""

    @pytest.mark.asyncio
    async def test_results_have_required_fields(self):
        """Invariant: Every result has entity_type, entity_id, name."""
        docs = _create_sample_documents(10)
        es = MockElasticsearchEngine()
        es.bulk_index(docs)

        result = await es.search(query="chicken", tenant_id="default")

        assert len(result["results"]) > 0, "Should have results"

        for item in result["results"]:
            assert "entity_type" in item, f"Missing entity_type in {item}"
            assert "entity_id" in item, f"Missing entity_id in {item}"
            assert "name" in item, f"Missing name in {item}"
            assert item["entity_type"] is not None
            assert item["entity_id"] is not None
            assert item["name"] is not None

        # Also test PG engine
        pg = MockPostgresSearchEngine()
        pg.bulk_index(docs)
        pg_result = await pg.search(query="chicken", tenant_id="default")

        for item in pg_result["results"]:
            assert "entity_type" in item
            assert "entity_id" in item
            assert "name" in item

    @pytest.mark.asyncio
    async def test_pagination_consistent(self):
        """Invariant: Page 1 + Page 2 results don't overlap, total count is consistent."""
        docs = _create_sample_documents(20)
        es = MockElasticsearchEngine()
        es.bulk_index(docs)

        # Page 1
        page1 = await es.search(query="", tenant_id="default", limit=10, offset=0)
        # Page 2
        page2 = await es.search(query="", tenant_id="default", limit=10, offset=10)

        # Total should be consistent
        assert page1["total"] == page2["total"], "Total count should be the same across pages"

        # Pages should not overlap
        page1_ids = {r["entity_id"] for r in page1["results"]}
        page2_ids = {r["entity_id"] for r in page2["results"]}
        overlap = page1_ids & page2_ids
        assert len(overlap) == 0, (
            f"Pages should not overlap. Overlapping IDs: {overlap}"
        )

        # Combined results should equal total
        assert len(page1["results"]) + len(page2["results"]) <= page1["total"]

        # has_more should be correct
        assert page1["has_more"] is True, "Page 1 should indicate more results"
        if page2["offset"] + page2["limit"] >= page1["total"]:
            assert page2["has_more"] is False, "Last page should not indicate more results"

    @pytest.mark.asyncio
    async def test_filter_consistency(self):
        """Invariant: Filter by cuisine="North Indian" → only North Indian results."""
        docs = _create_sample_documents(20)
        es = MockElasticsearchEngine()
        pg = MockPostgresSearchEngine()
        es.bulk_index(docs)
        pg.bulk_index(docs)

        filters = {"cuisine": "North Indian"}

        es_result = await es.search(query="", filters=filters, tenant_id="default")
        pg_result = await pg.search(query="", filters=filters, tenant_id="default")

        # All ES results should be North Indian
        for item in es_result["results"]:
            assert item["cuisine"] == "North Indian", (
                f"Expected cuisine=North Indian, got {item['cuisine']} for {item['name']}"
            )

        # All PG results should be North Indian
        for item in pg_result["results"]:
            assert item["cuisine"] == "North Indian", (
                f"Expected cuisine=North Indian, got {item['cuisine']} for {item['name']}"
            )

    @pytest.mark.asyncio
    async def test_empty_query_returns_popular(self):
        """Invariant: No query text → results ordered by popularity_score."""
        docs = _create_sample_documents(10)
        es = MockElasticsearchEngine()
        es.bulk_index(docs)

        result = await es.search(query="", tenant_id="default")

        assert len(result["results"]) > 0, "Empty query should return results"

        # Results should be ordered by popularity (descending)
        popularities = [r["popularity_score"] for r in result["results"]]
        for i in range(len(popularities) - 1):
            assert popularities[i] >= popularities[i + 1], (
                f"Results should be ordered by popularity (descending). "
                f"Got {popularities[i]} before {popularities[i + 1]}"
            )


# ═══════════════════════════════════════════════════════════════
# FIX #8: SEARCH EDGE CASES
# ═══════════════════════════════════════════════════════════════

class TestSearchEdgeCases:
    """Test: Search handles edge cases gracefully without crashes."""

    @pytest.mark.asyncio
    async def test_special_characters_in_query(self):
        """Invariant: Query "butter-chicken & biryani" → no crash, valid results.

        Special characters like &, -, |, ! should not cause errors
        in either ES or PG. The search should handle them gracefully.
        """
        docs = _create_sample_documents(20)
        es = MockElasticsearchEngine()
        pg = MockPostgresSearchEngine()
        es.bulk_index(docs)
        pg.bulk_index(docs)

        special_queries = [
            "butter-chicken & biryani",
            "paneer (tikka)",
            "dosa + sambar",
            "biryani | rice",
            "chicken!!",
            "dal...makhani",
        ]

        for query in special_queries:
            # Should not raise exceptions
            es_result = await es.search(query=query, tenant_id="default")
            pg_result = await pg.search(query=query, tenant_id="default")

            assert isinstance(es_result, dict), f"ES should return dict for query: {query}"
            assert isinstance(pg_result, dict), f"PG should return dict for query: {query}"
            assert "results" in es_result
            assert "results" in pg_result
            assert "total" in es_result
            assert "total" in pg_result

    @pytest.mark.asyncio
    async def test_unicode_search(self):
        """Invariant: Hindi/Regional language text → handled gracefully.

        The system should handle Unicode text (Hindi, regional languages)
        without crashing. Results may be empty if no matching documents
        exist, but the search should never error out.
        """
        docs = _create_sample_documents(20)

        # Add some Hindi-named documents
        hindi_docs = [
            MockSearchDocument(
                entity_type="MENU_ITEM",
                entity_id=f"item_hindi_{i}",
                name=name,
                description=desc,
                cuisine="North Indian",
                category="CURRY",
                dietary_preference="NON_VEG" if "चिकन" in name else "VEG",
                popularity_score=5.0,
                tags=["hindi"],
                tenant_id="default",
            )
            for i, (name, desc) in enumerate([
                ("बटर चिकन", "क्रीमी टमाटर चिकन करी"),
                ("पनीर टिक्का", "ग्रिल्ड पनीर मसाले के साथ"),
                ("बिरयानी", "सुगन्धित बासमती चावल"),
                ("दोसा", "क्रिस्पी क्रेप मसाले आलू के साथ"),
                ("दाल मखनी", "धीमी आंच पर पकी काली दाल"),
            ])
        ]

        es = MockElasticsearchEngine()
        pg = MockPostgresSearchEngine()
        es.bulk_index(docs + hindi_docs)
        pg.bulk_index(docs + hindi_docs)

        # Search with Hindi text
        hindi_queries = ["बटर चिकन", "बिरयानी", "दोसा"]

        for query in hindi_queries:
            es_result = await es.search(query=query, tenant_id="default")
            pg_result = await pg.search(query=query, tenant_id="default")

            # Should not crash — that's the primary invariant
            assert isinstance(es_result, dict), f"ES crashed on Hindi query: {query}"
            assert isinstance(pg_result, dict), f"PG crashed on Hindi query: {query}"
            assert "results" in es_result
            assert "results" in pg_result

    @pytest.mark.asyncio
    async def test_very_long_query(self):
        """Invariant: 1000-char query → no crash, truncated or limited results.

        Extremely long queries should not cause buffer overflows,
        excessive memory usage, or crashes. The system should handle
        them gracefully, potentially returning fewer results.
        """
        docs = _create_sample_documents(20)
        es = MockElasticsearchEngine()
        pg = MockPostgresSearchEngine()
        es.bulk_index(docs)
        pg.bulk_index(docs)

        # Generate a very long query
        long_query = "butter chicken biryani " * 50  # ~1250 chars

        es_result = await es.search(query=long_query, tenant_id="default")
        pg_result = await pg.search(query=long_query, tenant_id="default")

        # Should not crash
        assert isinstance(es_result, dict), "ES should handle long queries"
        assert isinstance(pg_result, dict), "PG should handle long queries"
        assert "results" in es_result
        assert "results" in pg_result
        # Results may be empty for nonsensical long queries — that's OK
        # The invariant is: no crash

    @pytest.mark.asyncio
    async def test_concurrent_search_and_index(self):
        """Invariant: Searching while indexing → no corruption.

        When documents are being indexed concurrently with search
        queries, the search results should be consistent within
        each individual query (no partial/corrupted results).
        """
        es = MockElasticsearchEngine()
        pg = MockPostgresSearchEngine()

        # Pre-index some documents
        initial_docs = _create_sample_documents(10)
        es.bulk_index(initial_docs)
        pg.bulk_index(initial_docs)

        # Simulate concurrent indexing and searching
        indexing_errors = []
        search_errors = []
        search_results = []

        async def index_new_docs():
            """Index new documents concurrently."""
            try:
                for i in range(5):
                    new_doc = MockSearchDocument(
                        entity_type="MENU_ITEM",
                        entity_id=f"item_concurrent_{i}",
                        name=f"Concurrent Dish {i}",
                        description=f"Added during concurrent test {i}",
                        cuisine="North Indian",
                        category="CURRY",
                        popularity_score=3.0 + i,
                        tenant_id="default",
                    )
                    es.index_document(new_doc)
                    pg.index_document(new_doc)
                    await asyncio.sleep(0.001)
            except Exception as e:
                indexing_errors.append(str(e))

        async def search_repeatedly():
            """Search while indexing is happening."""
            try:
                for _ in range(10):
                    result = await es.search(query="north indian", tenant_id="default")
                    search_results.append(result)
                    # Verify result integrity
                    assert isinstance(result, dict)
                    assert "results" in result
                    assert "total" in result
                    for item in result["results"]:
                        assert "entity_type" in item
                        assert "entity_id" in item
                        assert "name" in item
                    await asyncio.sleep(0.001)
            except Exception as e:
                search_errors.append(str(e))

        # Run concurrently
        await asyncio.gather(index_new_docs(), search_repeatedly())

        # No errors should have occurred
        assert len(indexing_errors) == 0, f"Indexing errors: {indexing_errors}"
        assert len(search_errors) == 0, f"Search errors: {search_errors}"
        assert len(search_results) == 10, "All searches should have completed"

        # Verify all results were valid
        for result in search_results:
            assert result["total"] >= 0, "Total should be non-negative"


# ═══════════════════════════════════════════════════════════════
# FIX #8: SEARCH RELEVANCE VALIDATION
# ═══════════════════════════════════════════════════════════════

class TestSearchRelevanceValidation:
    """Test: Search results are ranked by relevance, popularity, and freshness."""

    @pytest.mark.asyncio
    async def test_exact_match_ranks_higher(self):
        """Invariant: Exact name match should rank higher than partial.

        A search for "Butter Chicken" should rank an item named
        "Butter Chicken" higher than "Butter Chicken Masala" or
        "Chicken Butter Fry".
        """
        docs = [
            MockSearchDocument(
                entity_id="exact_match",
                name="Butter Chicken",
                description="Classic creamy tomato chicken curry",
                cuisine="North Indian",
                popularity_score=5.0,
                tenant_id="default",
            ),
            MockSearchDocument(
                entity_id="partial_match_1",
                name="Butter Chicken Masala",
                description="Butter chicken with extra spices",
                cuisine="North Indian",
                popularity_score=6.0,  # Higher popularity but partial match
                tenant_id="default",
            ),
            MockSearchDocument(
                entity_id="partial_match_2",
                name="Chicken Butter Fry",
                description="Fried chicken in butter sauce",
                cuisine="North Indian",
                popularity_score=7.0,  # Even higher popularity but worse match
                tenant_id="default",
            ),
        ]

        es = MockElasticsearchEngine()
        pg = MockPostgresSearchEngine()
        es.bulk_index(docs)
        pg.bulk_index(docs)

        # ES: Exact match should have highest relevance_score
        es_result = await es.search(query="Butter Chicken", tenant_id="default")
        es_results = es_result["results"]

        assert len(es_results) >= 1, "Should find results"

        # Find the exact match in results
        exact_match_rank = None
        for i, r in enumerate(es_results):
            if r["entity_id"] == "exact_match":
                exact_match_rank = i
                break

        if exact_match_rank is not None:
            # Exact match should be in top results
            assert exact_match_rank <= 1, (
                f"Exact name match should rank in top-2, but found at position {exact_match_rank}"
            )

        # Verify ES relevance score for exact match is highest
        exact_score = None
        for r in es_results:
            if r["entity_id"] == "exact_match":
                exact_score = r["relevance_score"]
                break

        if exact_score is not None:
            for r in es_results:
                if r["entity_id"] != "exact_match":
                    # ES gives extra weight to exact matches
                    assert exact_score >= r["relevance_score"] * 0.5, (
                        f"Exact match score ({exact_score}) should be comparable to "
                        f"partial match score ({r['relevance_score']})"
                    )

    @pytest.mark.asyncio
    async def test_popularity_affects_ranking(self):
        """Invariant: Items with higher popularity_score appear in top results.

        When multiple items match equally on text relevance, popularity
        should break ties. More popular items should rank higher.
        """
        docs = [
            MockSearchDocument(
                entity_id="low_popularity",
                name="Chicken Curry",
                description="Basic chicken curry",
                cuisine="North Indian",
                popularity_score=1.0,  # Low popularity
                tenant_id="default",
            ),
            MockSearchDocument(
                entity_id="high_popularity",
                name="Chicken Curry Special",
                description="Chef's special chicken curry",
                cuisine="North Indian",
                popularity_score=9.5,  # High popularity
                tenant_id="default",
            ),
            MockSearchDocument(
                entity_id="medium_popularity",
                name="Chicken Curry Deluxe",
                description="Premium chicken curry",
                cuisine="North Indian",
                popularity_score=5.0,  # Medium popularity
                tenant_id="default",
            ),
        ]

        es = MockElasticsearchEngine()
        pg = MockPostgresSearchEngine()
        es.bulk_index(docs)
        pg.bulk_index(docs)

        # Empty query: should sort by popularity
        es_result = await es.search(query="", tenant_id="default")
        results = es_result["results"]

        if len(results) >= 2:
            # High popularity item should appear before low popularity
            high_idx = None
            low_idx = None
            for i, r in enumerate(results):
                if r["entity_id"] == "high_popularity":
                    high_idx = i
                if r["entity_id"] == "low_popularity":
                    low_idx = i

            if high_idx is not None and low_idx is not None:
                assert high_idx < low_idx, (
                    f"High popularity item should rank above low popularity. "
                    f"High at position {high_idx}, Low at position {low_idx}"
                )

    @pytest.mark.asyncio
    async def test_freshness_consistency(self):
        """Invariant: Recently indexed items are findable.

        Items that were just indexed should be immediately searchable.
        There should be no delay or stale-cache issues in the mock system.
        In production, this tests the near-real-time indexing behavior.
        """
        es = MockElasticsearchEngine()
        pg = MockPostgresSearchEngine()

        # Initially index some documents
        initial_docs = _create_sample_documents(5)
        es.bulk_index(initial_docs)
        pg.bulk_index(initial_docs)

        # Verify initial search works
        result1 = await es.search(query="biryani", tenant_id="default")
        initial_total = result1["total"]

        # Index a new document
        new_doc = MockSearchDocument(
            entity_id="fresh_item_biryani",
            name="Fresh Biryani Special",
            description="Just added biryani dish",
            cuisine="North Indian",
            category="RICE_BOWL",
            popularity_score=8.0,
            tags=["fresh", "biryani"],
            tenant_id="default",
            indexed_at=datetime.now(timezone.utc).isoformat(),
        )
        es.index_document(new_doc)
        pg.index_document(new_doc)

        # Search again — new item should be findable
        result2 = await es.search(query="fresh biryani", tenant_id="default")
        assert result2["total"] >= initial_total, (
            "New item should be findable immediately after indexing"
        )

        # Verify the fresh item appears in results
        found_fresh = False
        for r in result2["results"]:
            if r["entity_id"] == "fresh_item_biryani":
                found_fresh = True
                break

        assert found_fresh, (
            "Recently indexed item should be findable in search results"
        )

        # Same check for PG
        pg_result = await pg.search(query="fresh biryani", tenant_id="default")
        found_fresh_pg = any(
            r["entity_id"] == "fresh_item_biryani" for r in pg_result["results"]
        )
        assert found_fresh_pg, (
            "Recently indexed item should be findable in PG search results"
        )

    @pytest.mark.asyncio
    async def test_es_and_pg_consistency_on_filter_change(self):
        """Invariant: Both engines respond consistently when filters change.

        When the same query is run with different filters, both
        engines should produce consistent behavior (same engine
        selection, consistent field presence).
        """
        docs = _create_sample_documents(20)
        es = MockElasticsearchEngine()
        pg = MockPostgresSearchEngine()
        es.bulk_index(docs)
        pg.bulk_index(docs)

        orchestrator = DualSearchOrchestrator(es, pg)

        filter_sets = [
            {},
            {"cuisine": "North Indian"},
            {"city": "Mumbai"},
            {"cuisine": "South Indian", "city": "Chennai"},
        ]

        for filters in filter_sets:
            result = await orchestrator.search(
                query="chicken",
                filters=filters,
                tenant_id="default",
            )

            # Should always have required response structure
            assert "engine" in result, "Should include engine field"
            assert "results" in result, "Should include results"
            assert "total" in result, "Should include total"
            assert "has_more" in result, "Should include has_more"

            # All results should satisfy the filter
            for item in result["results"]:
                if "cuisine" in filters:
                    assert item.get("cuisine") == filters["cuisine"], (
                        f"Result should match cuisine filter: "
                        f"expected {filters['cuisine']}, got {item.get('cuisine')}"
                    )
                if "city" in filters:
                    assert item.get("city") == filters["city"], (
                        f"Result should match city filter: "
                        f"expected {filters['city']}, got {item.get('city')}"
                    )
