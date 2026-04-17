"""HotSot Shelf Service — TTL Background Worker.

Runs as an asyncio task during the FastAPI lifespan.  Every 30 seconds it
scans all ``shelf_ttl:{tenant_id}:*`` keys in Redis, calculates the
percentage of TTL consumed for each, and emits Kafka events at the
defined warning thresholds:

    - 50 % TTL  →  shelf.expiry_warning  (level=INFO)
    - 75 % TTL  →  shelf.expiry_warning  (level=WARNING)
    - 90 % TTL  →  shelf.expiry_warning  (level=CRITICAL)
    - 100 % TTL →  shelf.expired          (triggers compensation)

Warning flags (``warning_50_sent``, ``warning_75_sent``,
``warning_90_sent``) are persisted back into the Redis key so that each
threshold fires at most once per shelf assignment.

Fail-safe: Redis or Kafka failures are logged but never crash the
worker — the next tick simply retries.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

from shared.utils.helpers import now_ts, now_iso
from shared.utils.redis_client import RedisClient
from shared.utils.kafka_client import KafkaProducer

logger = logging.getLogger("shelf-service.ttl-worker")

# ── Configuration ────────────────────────────────────────────────
SCAN_INTERVAL_SECONDS = 30
SHELF_TTL_KEY_PREFIX = "shelf_ttl:"
SHELF_EVENTS_TOPIC = "hotsot.shelf.events.v1"


class TTLWorker:
    """Background worker that polls Redis for expiring shelf TTLs and
    emits Kafka events at the 50/75/90/100 % thresholds."""

    def __init__(
        self,
        redis_client: RedisClient,
        kafka_producer: KafkaProducer,
        scan_interval: int = SCAN_INTERVAL_SECONDS,
    ) -> None:
        self._redis = redis_client
        self._kafka = kafka_producer
        self._scan_interval = scan_interval
        self._task: Optional[asyncio.Task] = None
        self._running = False

    # ── Lifecycle ────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the background polling loop as an asyncio Task."""
        if self._running:
            logger.warning("ttl_worker_already_running")
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="ttl-worker")
        logger.info("ttl_worker_started")

    async def stop(self) -> None:
        """Gracefully stop the background polling loop."""
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("ttl_worker_stopped")

    # ── Main Loop ────────────────────────────────────────────────

    async def _loop(self) -> None:
        """Infinite polling loop — runs until ``_running`` is cleared
        or the task is cancelled."""
        while self._running:
            try:
                await self._scan_all_keys()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("ttl_worker_scan_error")
            await asyncio.sleep(self._scan_interval)

    # ── Scanning ─────────────────────────────────────────────────

    async def _scan_all_keys(self) -> None:
        """Scan every ``shelf_ttl:*`` key and process thresholds."""
        try:
            client = self._redis.client
        except RuntimeError:
            logger.error("ttl_worker_redis_not_connected")
            return

        pattern = f"{SHELF_TTL_KEY_PREFIX}*"
        scanned = 0
        warnings = 0
        expired = 0

        try:
            async for key in client.scan_iter(match=pattern):
                scanned += 1
                try:
                    result = await self._process_key(client, key)
                    if result == "warning":
                        warnings += 1
                    elif result == "expired":
                        expired += 1
                except Exception:
                    logger.exception(f"ttl_worker_key_error key={key}")
        except Exception:
            logger.exception("ttl_worker_scan_iter_error")
            return

        if scanned > 0:
            logger.info(
                f"ttl_worker_scan_complete "
                f"scanned={scanned} warnings={warnings} expired={expired}"
            )

    async def _process_key(self, client, key: str) -> Optional[str]:
        """Process a single TTL key.  Returns ``"warning"``,
        ``"expired"``, or ``None``."""

        raw = await client.get(key)
        if raw is None:
            # Key vanished between scan and GET — nothing to do.
            return None

        try:
            info = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            logger.warning(f"ttl_worker_bad_json key={key}")
            return None

        # ── Compute percentage consumed ──────────────────────────
        ttl_seconds: float = info.get("ttl_seconds", 0)
        assigned_at: float = info.get("assigned_at", 0)

        if ttl_seconds <= 0:
            logger.warning(f"ttl_worker_zero_ttl key={key}")
            return None

        elapsed = now_ts() - assigned_at
        pct_used = (elapsed / ttl_seconds) * 100.0
        remaining = max(0, ttl_seconds - elapsed)

        # ── Threshold checks ─────────────────────────────────────
        result: Optional[str] = None

        if pct_used >= 100.0:
            # Shelf TTL expired — emit shelf.expired and delete key
            await self._emit_expired(info)
            try:
                await client.delete(key)
            except Exception:
                logger.exception(f"ttl_worker_delete_error key={key}")
            result = "expired"

        elif pct_used >= 90.0 and not info.get("warning_90_sent"):
            info["warning_90_sent"] = True
            await self._update_key(client, key, info, ttl_seconds)
            await self._emit_warning(info, "CRITICAL", pct_used, int(remaining))
            result = "warning"

        elif pct_used >= 75.0 and not info.get("warning_75_sent"):
            info["warning_75_sent"] = True
            await self._update_key(client, key, info, ttl_seconds)
            await self._emit_warning(info, "WARNING", pct_used, int(remaining))
            result = "warning"

        elif pct_used >= 50.0 and not info.get("warning_50_sent"):
            info["warning_50_sent"] = True
            await self._update_key(client, key, info, ttl_seconds)
            await self._emit_warning(info, "INFO", pct_used, int(remaining))
            result = "warning"

        return result

    # ── Redis helpers ────────────────────────────────────────────

    async def _update_key(
        self, client, key: str, info: dict, ttl_seconds: float
    ) -> None:
        """Write updated warning flags back to Redis, keeping the
        original TTL margin (+60 s buffer as used by TTLEngine)."""
        try:
            remaining = max(1, int(ttl_seconds - (now_ts() - info["assigned_at"]) + 60))
            await client.setex(key, remaining, json.dumps(info))
        except Exception:
            logger.exception(f"ttl_worker_update_error key={key}")

    # ── Kafka event emission ─────────────────────────────────────

    async def _emit_warning(
        self,
        info: dict,
        level: str,
        pct_used: float,
        ttl_remaining: int,
    ) -> None:
        """Emit a ``shelf.expiry_warning`` event to Kafka."""
        event = {
            "event_type": "shelf.expiry_warning",
            "shelf_id": info.get("shelf_id"),
            "order_id": info.get("order_id"),
            "kitchen_id": info.get("kitchen_id"),
            "zone": info.get("zone"),
            "warning_level": level,
            "pct_used": round(pct_used, 1),
            "ttl_remaining": ttl_remaining,
            "tenant_id": info.get("tenant_id"),
            "timestamp": now_iso(),
        }
        await self._publish(event, info.get("order_id"))
        logger.info(
            f"ttl_worker_warning_emitted shelf={info.get('shelf_id')} "
            f"level={level} pct={pct_used:.1f}% remaining={ttl_remaining}s"
        )

    async def _emit_expired(self, info: dict) -> None:
        """Emit a ``shelf.expired`` event to Kafka."""
        event = {
            "event_type": "shelf.expired",
            "shelf_id": info.get("shelf_id"),
            "order_id": info.get("order_id"),
            "kitchen_id": info.get("kitchen_id"),
            "zone": info.get("zone"),
            "tenant_id": info.get("tenant_id"),
            "timestamp": now_iso(),
        }
        await self._publish(event, info.get("order_id"))
        logger.info(
            f"ttl_worker_expired_emitted shelf={info.get('shelf_id')} "
            f"order={info.get('order_id')}"
        )

    async def _publish(self, event: dict, order_id: Optional[str]) -> None:
        """Publish an event dict to the shelf Kafka topic.

        Gracefully handles Kafka failures — logs the error and moves on
        so that one failed publish never blocks the worker loop.
        """
        try:
            await self._kafka.publish_raw(
                topic=SHELF_EVENTS_TOPIC,
                value=event,
                key=order_id,
            )
        except Exception:
            logger.exception(
                f"ttl_worker_kafka_error event_type={event.get('event_type')} "
                f"shelf_id={event.get('shelf_id')}"
            )
