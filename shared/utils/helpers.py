"""
HotSot Shared Utilities — Helper functions used across all services.

Includes:
    - ID generation
    - Timestamp helpers
    - Type conversion utilities
    - Idempotency key generation
    - Dict utilities
    - Timer context manager
    - String utilities

Usage:
    from shared.utils.helpers import generate_id, now_iso, idempotency_key, Timer

    # ID generation
    order_id = generate_id()

    # Timestamps
    ts = now_ts()       # Unix float
    iso = now_iso()     # ISO 8601 string

    # Idempotency
    key = idempotency_key(user_id="u1", kitchen_id="k1", items_hash="abc123")

    # Type conversion
    val = safe_float("3.14", default=0.0)
    clamped = clamp(1.5, 0.0, 1.0)  # 1.0

    # Timing
    with Timer("process_order") as t:
        await process_order(order_id)
    print(f"Took {t.elapsed:.2f}s")
"""

from __future__ import annotations

import hashlib
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Generator, Optional


# ═══════════════════════════════════════════════════════════════
# ID GENERATION
# ═══════════════════════════════════════════════════════════════

def generate_id() -> str:
    """
    Generate a UUID4 string.

    Returns:
        Random UUID as string (e.g., "a1b2c3d4-e5f6-4789-a0b1-c2d3e4f5a6b7").
    """
    return str(uuid.uuid4())


def generate_short_id(length: int = 12) -> str:
    """
    Generate a short, URL-safe random ID.

    Suitable for public-facing IDs (order IDs, compensation IDs, etc.).

    Args:
        length: Desired length of the ID.

    Returns:
        URL-safe base64-encoded random string.
    """
    import secrets
    import base64
    raw = secrets.token_bytes(length)
    return base64.urlsafe_b64encode(raw).decode("ascii")[:length]


def generate_compensation_id() -> str:
    """
    Generate a compensation ID in format: COMP_XXXXXXXXXX.

    Returns:
        Compensation ID string.
    """
    import secrets
    suffix = secrets.token_hex(5).upper()  # 10 hex chars
    return f"COMP_{suffix}"


# ═══════════════════════════════════════════════════════════════
# TIMESTAMP HELPERS
# ═══════════════════════════════════════════════════════════════

def now_ts() -> float:
    """
    Current Unix timestamp as float.

    Returns:
        Current time as seconds since epoch (float).
    """
    return time.time()


def now_iso() -> str:
    """
    Current ISO 8601 timestamp in UTC.

    Returns:
        ISO 8601 formatted string (e.g., "2024-01-15T10:30:00+00:00").
    """
    return datetime.now(timezone.utc).isoformat()


def now_iso_compact() -> str:
    """
    Current timestamp in compact format for logging/filenames.

    Returns:
        Compact timestamp (e.g., "20240115T103000Z").
    """
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def parse_iso_timestamp(ts: str) -> Optional[datetime]:
    """
    Parse an ISO 8601 timestamp string.

    Args:
        ts: ISO 8601 timestamp string.

    Returns:
        datetime object or None if parsing fails.
    """
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


# ═══════════════════════════════════════════════════════════════
# IDEMPOTENCY KEY GENERATION
# ═══════════════════════════════════════════════════════════════

def idempotency_key(
    user_id: str,
    kitchen_id: str,
    items_hash: str,
    window_hours: int = 1,
) -> str:
    """
    Generate an idempotency key for safe retries on unstable networks.

    The key is time-bucketed to the current hour (configurable) to
    prevent replay attacks while allowing legitimate retries within
    the same window.

    Args:
        user_id: User identifier.
        kitchen_id: Kitchen identifier.
        items_hash: Hash of the order items (for content-based dedup).
        window_hours: Time window in hours for the bucket.

    Returns:
        SHA-256 hex digest (first 32 chars) as the idempotency key.
    """
    bucket = datetime.now(timezone.utc).strftime(f"%Y%m%d%H" if window_hours == 1 else f"%Y%m%d")
    raw = f"{user_id}:{kitchen_id}:{items_hash}:{bucket}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def arrival_dedup_key(
    user_id: str,
    order_id: str,
    window_seconds: int = 30,
) -> str:
    """
    Generate a dedup key for arrival events.

    Time-bucketed to floor(timestamp / window_seconds) for
    dedup within the specified window.

    Args:
        user_id: User identifier.
        order_id: Order identifier.
        window_seconds: Dedup window in seconds.

    Returns:
        SHA-256 hex digest (first 32 chars) as the dedup key.
    """
    bucket = int(time.time() / window_seconds)
    raw = f"arrival:{user_id}:{order_id}:{bucket}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def items_hash(items: list[dict]) -> str:
    """
    Generate a content hash for a list of order items.

    Used for idempotency key generation — same items produce
    the same hash regardless of list ordering.

    Args:
        items: List of item dicts.

    Returns:
        SHA-256 hex digest (first 16 chars).
    """
    import json
    # Sort items by string representation for consistent hashing
    sorted_items = sorted(json.dumps(item, sort_keys=True) for item in items)
    raw = json.dumps(sorted_items)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# ═══════════════════════════════════════════════════════════════
# TYPE CONVERSION UTILITIES
# ═══════════════════════════════════════════════════════════════

def safe_float(value: Any, default: float = 0.0) -> float:
    """
    Safely convert a value to float.

    Args:
        value: Value to convert.
        default: Default value on conversion failure.

    Returns:
        Float value or default.
    """
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_int(value: Any, default: int = 0) -> int:
    """
    Safely convert a value to int.

    Args:
        value: Value to convert.
        default: Default value on conversion failure.

    Returns:
        Integer value or default.
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def safe_str(value: Any, default: str = "") -> str:
    """
    Safely convert a value to string.

    Args:
        value: Value to convert.
        default: Default value on conversion failure.

    Returns:
        String value or default.
    """
    if value is None:
        return default
    try:
        return str(value)
    except Exception:
        return default


def clamp(value: float, min_val: float, max_val: float) -> float:
    """
    Clamp a value within a range.

    Args:
        value: Value to clamp.
        min_val: Minimum allowed value.
        max_val: Maximum allowed value.

    Returns:
        Clamped value.
    """
    return max(min_val, min(max_val, value))


def safe_divide(numerator: float, denominator: float, default: float = 0.0) -> float:
    """
    Safely divide two numbers, returning default on zero division.

    Args:
        numerator: Numerator.
        denominator: Denominator.
        default: Default value on zero division.

    Returns:
        Division result or default.
    """
    try:
        return numerator / denominator if denominator != 0 else default
    except (TypeError, ZeroDivisionError):
        return default


# ═══════════════════════════════════════════════════════════════
# DICT UTILITIES
# ═══════════════════════════════════════════════════════════════

def truncate_dict(d: Dict, max_depth: int = 5, max_str_len: int = 200) -> Dict:
    """
    Truncate nested dict for safe logging.

    Prevents excessively deep or long values from bloating logs.

    Args:
        d: Dictionary to truncate.
        max_depth: Maximum nesting depth.
        max_str_len: Maximum string value length.

    Returns:
        Truncated dictionary.
    """
    if max_depth <= 0:
        return {"...": "truncated"}

    result = {}
    for k, v in d.items():
        if isinstance(v, dict):
            result[k] = truncate_dict(v, max_depth - 1, max_str_len)
        elif isinstance(v, str) and len(v) > max_str_len:
            result[k] = v[:max_str_len] + f"... ({len(v)} chars)"
        elif isinstance(v, (list, tuple)) and len(v) > 10:
            result[k] = v[:10] + [f"... ({len(v)} items)"]
        else:
            result[k] = v
    return result


def flatten_dict(
    d: Dict,
    parent_key: str = "",
    sep: str = ".",
) -> Dict[str, Any]:
    """
    Flatten a nested dictionary.

    Args:
        d: Dictionary to flatten.
        parent_key: Prefix for keys.
        sep: Separator between nested keys.

    Returns:
        Flattened dictionary with dot-separated keys.
    """
    items: list[tuple[str, Any]] = []
    for k, v in d.items():
        new_key = f"{parent_key}{sep}{k}" if parent_key else k
        if isinstance(v, dict):
            items.extend(flatten_dict(v, new_key, sep=sep).items())
        else:
            items.append((new_key, v))
    return dict(items)


# ═══════════════════════════════════════════════════════════════
# TIMER CONTEXT MANAGER
# ═══════════════════════════════════════════════════════════════

class Timer:
    """
    Context manager for timing operations.

    Usage:
        with Timer("process_order") as t:
            await process_order(order_id)
        print(f"Took {t.elapsed:.2f}s")

    Or as a decorator-like pattern:
        t = Timer("api_call")
        t.start()
        ... do work ...
        t.stop()
        print(f"Duration: {t.elapsed}")
    """

    def __init__(self, label: str = "operation"):
        self.label = label
        self.elapsed: Optional[float] = None
        self._start: Optional[float] = None

    def __enter__(self) -> Timer:
        self._start = time.monotonic()
        return self

    def __exit__(self, *args: Any) -> None:
        if self._start is not None:
            self.elapsed = time.monotonic() - self._start

    def start(self) -> None:
        """Start the timer."""
        self._start = time.monotonic()

    def stop(self) -> float:
        """
        Stop the timer and return elapsed time.

        Returns:
            Elapsed time in seconds.
        """
        if self._start is None:
            return 0.0
        self.elapsed = time.monotonic() - self._start
        return self.elapsed

    @property
    def elapsed_ms(self) -> Optional[float]:
        """Elapsed time in milliseconds (or None if not stopped)."""
        if self.elapsed is not None:
            return self.elapsed * 1000
        return None

    def __repr__(self) -> str:
        if self.elapsed is not None:
            return f"Timer(label={self.label!r}, elapsed={self.elapsed:.4f}s)"
        return f"Timer(label={self.label!r}, running={self._start is not None})"


@contextmanager
def timed(label: str = "operation") -> Generator[Timer, None, None]:
    """
    Context manager that logs the duration of a block.

    Usage:
        with timed("expensive_operation") as t:
            result = do_something()
        # Logs: "expensive_operation completed in 1.234s"

    Args:
        label: Description for logging.
    """
    import logging
    _logger = logging.getLogger(__name__)
    t = Timer(label)
    with t:
        yield t
    if t.elapsed is not None:
        _logger.info(f"{label} completed in {t.elapsed:.3f}s")


# ═══════════════════════════════════════════════════════════════
# STRING UTILITIES
# ═══════════════════════════════════════════════════════════════

def mask_sensitive(value: str, visible_chars: int = 4) -> str:
    """
    Mask a sensitive string, showing only the last N characters.

    Args:
        value: String to mask.
        visible_chars: Number of visible characters at the end.

    Returns:
        Masked string (e.g., "****1234").
    """
    if not value or len(value) <= visible_chars:
        return "*" * len(value) if value else ""
    return "*" * (len(value) - visible_chars) + value[-visible_chars:]


def truncate_string(s: str, max_len: int = 100, suffix: str = "...") -> str:
    """
    Truncate a string to a maximum length with a suffix.

    Args:
        s: String to truncate.
        max_len: Maximum length including suffix.
        suffix: Suffix to append when truncated.

    Returns:
        Truncated string.
    """
    if not s or len(s) <= max_len:
        return s
    return s[: max_len - len(suffix)] + suffix


# ═══════════════════════════════════════════════════════════════
# MATH UTILITIES
# ═══════════════════════════════════════════════════════════════

def percentage(part: float, whole: float, default: float = 0.0) -> float:
    """
    Calculate percentage safely.

    Args:
        part: The part value.
        whole: The whole value.
        default: Default if whole is zero.

    Returns:
        Percentage value (0-100).
    """
    if whole == 0:
        return default
    return clamp((part / whole) * 100, 0.0, 100.0)


def moving_average(values: list[float], window: int = 5) -> list[float]:
    """
    Calculate simple moving average.

    Args:
        values: List of numeric values.
        window: Window size for averaging.

    Returns:
        List of moving averages.
    """
    if not values or window <= 0:
        return []

    result = []
    for i in range(len(values)):
        start = max(0, i - window + 1)
        window_vals = values[start:i + 1]
        result.append(sum(window_vals) / len(window_vals))

    return result
