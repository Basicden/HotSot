"""HotSot Shared Utilities."""

import time
import uuid
import hashlib
from datetime import datetime, timezone
from typing import Any, Dict, Optional


def generate_id() -> str:
    """Generate a UUID4 string."""
    return str(uuid.uuid4())


def now_ts() -> float:
    """Current Unix timestamp."""
    return time.time()


def now_iso() -> str:
    """Current ISO 8601 timestamp."""
    return datetime.now(timezone.utc).isoformat()


def idempotency_key(user_id: str, kitchen_id: str, items_hash: str) -> str:
    """Generate idempotency key for safe retries on unstable networks."""
    raw = f"{user_id}:{kitchen_id}:{items_hash}:{datetime.now(timezone.utc).strftime('%Y%m%d%H')}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def safe_float(value: Any, default: float = 0.0) -> float:
    """Safely convert to float."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_int(value: Any, default: int = 0) -> int:
    """Safely convert to int."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def clamp(value: float, min_val: float, max_val: float) -> float:
    """Clamp value within bounds."""
    return max(min_val, min(max_val, value))


def truncate_dict(d: Dict, max_depth: int = 5) -> Dict:
    """Truncate nested dict for safe logging."""
    if max_depth <= 0:
        return {...: "truncated"}
    result = {}
    for k, v in d.items():
        if isinstance(v, dict):
            result[k] = truncate_dict(v, max_depth - 1)
        else:
            result[k] = v
    return result


class Timer:
    """Simple context manager for timing operations."""

    def __init__(self, label: str = "operation"):
        self.label = label
        self.elapsed: Optional[float] = None

    def __enter__(self):
        self.start = time.monotonic()
        return self

    def __exit__(self, *args):
        self.elapsed = time.monotonic() - self.start
