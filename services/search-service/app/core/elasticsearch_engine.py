"""
HotSot Search Service — Elasticsearch Engine.

Dual-engine search: PostgreSQL tsvector (primary) + Elasticsearch (secondary).
When Elasticsearch is available, search queries are routed to ES for
better relevance, fuzzy matching, and autocomplete. Falls back to
PostgreSQL tsvector when ES is unavailable.

Features:
    - Elasticsearch 8.x async client
    - Index management (create, update, delete)
    - Full-text search with fuzzy matching
    - Autocomplete with edge_ngram analyzer
    - Multi-field search (name^3, description, cuisine, tags)
    - Faceted filtering (cuisine, city, category, dietary, price range)
    - Automatic fallback to PostgreSQL when ES is down
    - Circuit breaker protection for ES calls

Usage:
    from app.core.elasticsearch_engine import ElasticSearchEngine

    engine = ElasticSearchEngine()
    await engine.start()
    results = await engine.search("biryani", filters={"city": "Mumbai"})
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from shared.circuit_breaker import CircuitBreaker, CircuitOpenError

logger = logging.getLogger(__name__)

# Circuit breaker for Elasticsearch calls
_es_circuit_breaker = CircuitBreaker(
    service_name="elasticsearch",
    failure_threshold=3,
    recovery_timeout=30,
    success_threshold=2,
)

INDEX_NAME = "hotsot-search"
INDEX_SETTINGS = {
    "settings": {
        "number_of_shards": 3,
        "number_of_replicas": 1,
        "analysis": {
            "filter": {
                "autocomplete_filter": {
                    "type": "edge_ngram",
                    "min_gram": 2,
                    "max_gram": 20,
                }
            },
            "analyzer": {
                "autocomplete_analyzer": {
                    "type": "custom",
                    "tokenizer": "standard",
                    "filter": ["lowercase", "autocomplete_filter"],
                },
                "search_analyzer": {
                    "type": "custom",
                    "tokenizer": "standard",
                    "filter": ["lowercase"],
                },
            },
        },
    },
    "mappings": {
        "properties": {
            "entity_type": {"type": "keyword"},
            "entity_id": {"type": "keyword"},
            "vendor_id": {"type": "keyword"},
            "name": {
                "type": "text",
                "analyzer": "autocomplete_analyzer",
                "search_analyzer": "search_analyzer",
                "fields": {"keyword": {"type": "keyword"}},
            },
            "description": {"type": "text", "analyzer": "standard"},
            "cuisine": {"type": "keyword"},
            "city": {"type": "keyword"},
            "category": {"type": "keyword"},
            "tags": {"type": "keyword"},
            "dietary_preference": {"type": "keyword"},
            "price_min": {"type": "float"},
            "price_max": {"type": "float"},
            "popularity_score": {"type": "float"},
            "is_available": {"type": "boolean"},
            "tenant_id": {"type": "keyword"},
        }
    },
}


class ElasticSearchEngine:
    """
    Elasticsearch search engine with circuit breaker protection.

    When ES is unavailable, all calls fail-fast through the circuit breaker,
    and the search service falls back to PostgreSQL tsvector search.
    """

    def __init__(self, es_url: str = "http://localhost:9200"):
        self._es_url = es_url
        self._client = None
        self._started = False

    async def start(self) -> None:
        """Initialize Elasticsearch client and create index if needed."""
        try:
            from elasticsearch import AsyncElasticsearch

            self._client = AsyncElasticsearch(
                [self._es_url],
                request_timeout=10,
                max_retries=2,
                retry_on_timeout=True,
            )

            # Check connection
            if await self._client.ping():
                # Create index with mapping if not exists
                if not await self._client.indices.exists(index=INDEX_NAME):
                    await self._client.indices.create(
                        index=INDEX_NAME, body=INDEX_SETTINGS
                    )
                    logger.info(f"Elasticsearch index created: {INDEX_NAME}")

                self._started = True
                logger.info(f"Elasticsearch connected: {self._es_url}")
            else:
                logger.warning("Elasticsearch ping failed — running in fallback mode")
                self._client = None

        except ImportError:
            logger.warning(
                "elasticsearch package not installed — "
                "run: pip install elasticsearch[async] — "
                "falling back to PostgreSQL search"
            )
            self._client = None
        except Exception as e:
            logger.warning(f"Elasticsearch start failed: {e} — falling back to PostgreSQL")
            self._client = None

    async def stop(self) -> None:
        """Close Elasticsearch client."""
        if self._client:
            try:
                await self._client.close()
            except Exception as e:
                logger.warning(f"Error closing ES client: {e}")
        self._started = False

    @property
    def is_available(self) -> bool:
        """Check if ES is available for queries."""
        return self._started and self._client is not None

    async def index_document(self, doc: Dict[str, Any]) -> bool:
        """
        Index a document in Elasticsearch.

        Args:
            doc: Document dict with entity_type, entity_id, name, etc.

        Returns:
            True if indexed, False on error or circuit open.
        """
        if not await _es_circuit_breaker.allow_request():
            logger.warning("Circuit breaker OPEN for ES — index skipped")
            return False

        if not self.is_available:
            return False

        try:
            doc_id = f"{doc.get('entity_type', 'unknown')}_{doc.get('entity_id', '')}"
            await self._client.index(
                index=INDEX_NAME,
                id=doc_id,
                body=doc,
            )
            await _es_circuit_breaker.record_success()
            return True
        except Exception as e:
            await _es_circuit_breaker.record_failure(e)
            logger.error(f"ES index failed: {e}")
            return False

    async def search(
        self,
        query: str,
        filters: Optional[Dict[str, Any]] = None,
        limit: int = 20,
        offset: int = 0,
        tenant_id: str = "default",
    ) -> Dict[str, Any]:
        """
        Full-text search with fuzzy matching and faceted filters.

        Args:
            query: Search query string.
            filters: Optional dict with cuisine, city, category, dietary,
                     price_min, price_max, entity_type, vendor_id.
            limit: Results per page.
            offset: Pagination offset.
            tenant_id: Tenant for multi-tenancy.

        Returns:
            Dict with total, results, or empty results on error.
        """
        if not await _es_circuit_breaker.allow_request():
            logger.warning("Circuit breaker OPEN for ES — returning empty results")
            return {"total": 0, "results": [], "engine": "elasticsearch_unavailable"}

        if not self.is_available:
            return {"total": 0, "results": [], "engine": "unavailable"}

        filters = filters or {}

        # Build ES query
        must = []
        filter_clauses = [{"term": {"tenant_id": tenant_id}}, {"term": {"is_available": True}}]

        # Full-text search with multi-match
        if query:
            must.append({
                "multi_match": {
                    "query": query,
                    "fields": ["name^3", "description", "cuisine^2", "tags"],
                    "type": "best_fields",
                    "fuzziness": "AUTO",
                }
            })

        # Apply filters
        if filters.get("entity_type"):
            filter_clauses.append({"term": {"entity_type": filters["entity_type"]}})
        if filters.get("cuisine"):
            filter_clauses.append({"term": {"cuisine": filters["cuisine"]}})
        if filters.get("city"):
            filter_clauses.append({"term": {"city": filters["city"]}})
        if filters.get("category"):
            filter_clauses.append({"term": {"category": filters["category"]}})
        if filters.get("dietary"):
            filter_clauses.append({"term": {"dietary_preference": filters["dietary"].upper()}})
        if filters.get("vendor_id"):
            filter_clauses.append({"term": {"vendor_id": filters["vendor_id"]}})
        if filters.get("price_min") is not None:
            filter_clauses.append({"range": {"price_max": {"gte": filters["price_min"]}}})
        if filters.get("price_max") is not None:
            filter_clauses.append({"range": {"price_min": {"lte": filters["price_max"]}}})

        es_query = {
            "query": {"bool": {"must": must, "filter": filter_clauses}} if must else {"bool": {"filter": filter_clauses}},
            "sort": [
                "_score",
                {"popularity_score": {"order": "desc"}},
            ],
            "from": offset,
            "size": limit,
        }

        try:
            result = await self._client.search(index=INDEX_NAME, body=es_query)
            await _es_circuit_breaker.record_success()

            hits = result.get("hits", {})
            total = hits.get("total", {}).get("value", 0)

            items = []
            for hit in hits.get("hits", []):
                source = hit["_source"]
                source["_score"] = hit["_score"]
                source["relevance_score"] = round(hit["_score"], 4) if hit["_score"] else 0.0
                items.append(source)

            return {
                "total": total,
                "results": items,
                "engine": "elasticsearch",
                "limit": limit,
                "offset": offset,
            }

        except Exception as e:
            await _es_circuit_breaker.record_failure(e)
            logger.error(f"ES search failed: {e}")
            return {"total": 0, "results": [], "engine": "elasticsearch_error"}

    async def suggest(
        self,
        prefix: str,
        entity_type: Optional[str] = None,
        limit: int = 10,
        tenant_id: str = "default",
    ) -> List[Dict[str, str]]:
        """
        Autocomplete suggestions using edge_ngram analyzer.

        Args:
            prefix: Search prefix for type-ahead.
            entity_type: Optional entity type filter.
            limit: Max suggestions.
            tenant_id: Tenant for isolation.

        Returns:
            List of suggestion dicts with name, entity_type, cuisine.
        """
        if not await _es_circuit_breaker.allow_request():
            return []

        if not self.is_available:
            return []

        filter_clauses = [
            {"term": {"tenant_id": tenant_id}},
            {"term": {"is_available": True}},
        ]
        if entity_type:
            filter_clauses.append({"term": {"entity_type": entity_type}})

        es_query = {
            "query": {
                "bool": {
                    "must": [{"match": {"name": {"query": prefix, "analyzer": "search_analyzer"}}}],
                    "filter": filter_clauses,
                }
            },
            "sort": [{"popularity_score": {"order": "desc"}}],
            "size": limit,
            "_source": ["name", "entity_type", "cuisine"],
        }

        try:
            result = await self._client.search(index=INDEX_NAME, body=es_query)
            await _es_circuit_breaker.record_success()

            suggestions = []
            for hit in result.get("hits", {}).get("hits", []):
                source = hit["_source"]
                suggestions.append({
                    "name": source.get("name", ""),
                    "entity_type": source.get("entity_type", ""),
                    "cuisine": source.get("cuisine", ""),
                })
            return suggestions

        except Exception as e:
            await _es_circuit_breaker.record_failure(e)
            logger.error(f"ES suggest failed: {e}")
            return []

    async def delete_document(self, entity_type: str, entity_id: str) -> bool:
        """Delete a document from ES index."""
        if not await _es_circuit_breaker.allow_request():
            return False
        if not self.is_available:
            return False

        try:
            doc_id = f"{entity_type}_{entity_id}"
            await self._client.delete(index=INDEX_NAME, id=doc_id)
            await _es_circuit_breaker.record_success()
            return True
        except Exception as e:
            await _es_circuit_breaker.record_failure(e)
            logger.error(f"ES delete failed: {e}")
            return False

    async def bulk_index(self, documents: List[Dict[str, Any]]) -> int:
        """Bulk index multiple documents. Returns count of indexed docs."""
        if not await _es_circuit_breaker.allow_request():
            return 0
        if not self.is_available:
            return 0

        try:
            from elasticsearch.helpers import async_bulk

            actions = []
            for doc in documents:
                doc_id = f"{doc.get('entity_type', 'unknown')}_{doc.get('entity_id', '')}"
                actions.append({
                    "_index": INDEX_NAME,
                    "_id": doc_id,
                    "_source": doc,
                })

            success, errors = await async_bulk(self._client, actions, raise_on_error=False)
            await _es_circuit_breaker.record_success()

            if errors:
                logger.warning(f"Bulk index had {len(errors)} errors out of {len(actions)}")

            return success

        except Exception as e:
            await _es_circuit_breaker.record_failure(e)
            logger.error(f"ES bulk index failed: {e}")
            return 0
