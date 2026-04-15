"""
HotSot Auth Module — JWT + API Key authentication.

Provides production-grade authentication for all HotSot microservices:
    - JWT tokens with tenant isolation and role-based access
    - API key authentication for vendor integrations
    - FastAPI dependencies for route protection
"""

from shared.auth.jwt import (
    create_access_token,
    create_refresh_token,
    decode_access_token,
    decode_refresh_token,
    get_current_user,
    require_role,
    require_tenant,
    TokenPayload,
)
from shared.auth.api_key import (
    generate_api_key,
    verify_api_key,
    hash_api_key,
    rotate_api_key,
    APIKeyVerificationResult,
)

__all__ = [
    # JWT
    "create_access_token",
    "create_refresh_token",
    "decode_access_token",
    "decode_refresh_token",
    "get_current_user",
    "require_role",
    "require_tenant",
    "TokenPayload",
    # API Key
    "generate_api_key",
    "verify_api_key",
    "hash_api_key",
    "rotate_api_key",
    "APIKeyVerificationResult",
]
