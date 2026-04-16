"""
HotSot API Key Authentication — For vendor integrations and service-to-service auth.

Features:
    - Secure API key generation (cryptographic random)
    - Key hashing with salt (never store plaintext keys)
    - Constant-time comparison (timing attack prevention)
    - Key rotation support
    - Verification result with metadata

Usage:
    from shared.auth.api_key import generate_api_key, verify_api_key, hash_api_key

    # Generate a new API key
    raw_key, key_hash = generate_api_key()
    # Store key_hash in DB, give raw_key to vendor

    # Verify a key
    result = verify_api_key(provided_key, stored_hash)
    if result.valid:
        ...
"""

from __future__ import annotations

import hashlib
import hmac
import os
import logging
import secrets
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# Salt from environment (critical for security)
_API_KEY_SALT: str = os.getenv("API_KEY_SALT", "hotsot-default-salt-change-in-prod")

# Validate in production
_ENV = os.getenv("ENVIRONMENT", "development")
if _ENV != "development" and _API_KEY_SALT == "hotsot-default-salt-change-in-prod":
    logger.warning(
        "API_KEY_SALT is using default value in production. "
        "Set the API_KEY_SALT environment variable for security."
    )


# ═══════════════════════════════════════════════════════════════
# API KEY GENERATION
# ═══════════════════════════════════════════════════════════════

def generate_api_key(prefix: str = "hsot") -> tuple[str, str]:
    """
    Generate a new API key and its hash.

    The raw key is shown only once — it must be given to the vendor
    immediately and never stored in plaintext.

    Args:
        prefix: Key prefix for identification (e.g., "hsot", "hsot_dev").

    Returns:
        Tuple of (raw_key, key_hash). Store key_hash in the database.
    """
    # Generate 32 bytes of cryptographic randomness
    raw_bytes = secrets.token_bytes(32)
    raw_key = f"{prefix}_{secrets.token_urlsafe(32)}"

    # Hash the key for storage
    key_hash = hash_api_key(raw_key)

    logger.info(f"API key generated: prefix={prefix} hash={key_hash[:16]}...")
    return raw_key, key_hash


def hash_api_key(raw_key: str) -> str:
    """
    Hash an API key using HMAC-SHA256 with the configured salt.

    Args:
        raw_key: The plaintext API key.

    Returns:
        Hex-encoded hash string.
    """
    return hmac.new(
        _API_KEY_SALT.encode("utf-8"),
        raw_key.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


# ═══════════════════════════════════════════════════════════════
# API KEY VERIFICATION
# ═══════════════════════════════════════════════════════════════

@dataclass
class APIKeyVerificationResult:
    """Result of an API key verification attempt."""
    valid: bool
    key_prefix: Optional[str] = None
    error: Optional[str] = None

    def __bool__(self) -> bool:
        return self.valid


def verify_api_key(provided_key: str, stored_hash: str) -> APIKeyVerificationResult:
    """
    Verify an API key against its stored hash.

    Uses constant-time comparison to prevent timing attacks.

    Args:
        provided_key: The key provided in the request.
        stored_hash: The hash stored in the database.

    Returns:
        APIKeyVerificationResult with validity status.
    """
    if not provided_key or not stored_hash:
        return APIKeyVerificationResult(
            valid=False,
            error="Missing key or hash",
        )

    # Compute hash of provided key
    computed_hash = hash_api_key(provided_key)

    # Constant-time comparison
    is_valid = hmac.compare_digest(computed_hash, stored_hash)

    if is_valid:
        # Extract prefix (e.g., "hsot" from "hsot_abc123...")
        prefix = provided_key.split("_")[0] if "_" in provided_key else None
        return APIKeyVerificationResult(
            valid=True,
            key_prefix=prefix,
        )
    else:
        logger.warning("API key verification failed: hash mismatch")
        return APIKeyVerificationResult(
            valid=False,
            error="Invalid API key",
        )


# ═══════════════════════════════════════════════════════════════
# KEY ROTATION
# ═══════════════════════════════════════════════════════════════

def rotate_api_key(old_key: str, old_hash: str, prefix: str = "hsot") -> tuple[str, str, bool]:
    """
    Rotate an API key: verify the old key, then generate a new one.

    Args:
        old_key: The current (old) API key.
        old_hash: The stored hash of the old key.
        prefix: Prefix for the new key.

    Returns:
        Tuple of (new_raw_key, new_hash, rotation_successful).
    """
    # Verify the old key first
    result = verify_api_key(old_key, old_hash)

    if not result.valid:
        logger.warning("API key rotation failed: old key verification failed")
        return "", "", False

    # Generate new key
    new_raw_key, new_hash = generate_api_key(prefix=prefix)

    logger.info(
        f"API key rotated: prefix={prefix} old_prefix={result.key_prefix}"
    )

    return new_raw_key, new_hash, True
