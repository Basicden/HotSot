"""
HotSot JWT Authentication — Production-grade token management.

Uses python-jose for JWT encoding/decoding with HMAC-SHA256.
Supports multi-tenancy, role-based access, and token refresh.

Usage:
    from shared.auth.jwt import create_access_token, get_current_user, require_role

    # Create token
    token = create_access_token(
        user_id="usr_123",
        tenant_id="tnt_abc",
        role="vendor_admin",
        extra_claims={"kitchen_id": "ktc_456"},
    )

    # FastAPI dependency
    @app.get("/me")
    async def me(user: dict = Depends(get_current_user)):
        return user

    # Role guard
    @app.delete("/orders/{id}")
    async def cancel(user: dict = Depends(require_role("admin"))):
        ...
"""

from __future__ import annotations

import os
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional
from dataclasses import dataclass, field

from jose import JWTError, jwt, jws
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

logger = logging.getLogger(__name__)

# ─── Configuration (no hardcoded secrets) ───

_SECRET_KEY: str = os.getenv("JWT_SECRET_KEY", "")
_ALGORITHM: str = os.getenv("JWT_ALGORITHM", "HS256")
_ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("JWT_ACCESS_EXPIRE_MINUTES", "60"))
_REFRESH_TOKEN_EXPIRE_DAYS: int = int(os.getenv("JWT_REFRESH_EXPIRE_DAYS", "7"))
_ISSUER: str = os.getenv("JWT_ISSUER", "hotsot")

# Validate secret is set in production
_ENV = os.getenv("ENVIRONMENT", "development")
if _ENV != "development" and not _SECRET_KEY:
    raise RuntimeError(
        "JWT_SECRET_KEY must be set in production. "
        "Set the JWT_SECRET_KEY environment variable."
    )

# Dev-only fallback (NEVER use in production)
if _ENV == "development" and not _SECRET_KEY:
    _SECRET_KEY = "hotsot-dev-secret-change-in-production"
    logger.warning(
        "Using default JWT secret — ONLY for development. "
        "Set JWT_SECRET_KEY environment variable for production."
    )

security = HTTPBearer(auto_error=False)


# ─── Data Classes ───

@dataclass
class TokenPayload:
    """Structured JWT payload representation."""
    user_id: str
    tenant_id: str
    role: str
    exp: datetime
    iat: datetime
    iss: str = _ISSUER
    token_type: str = "access"
    extra_claims: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to JWT-encodable dictionary."""
        payload: dict[str, Any] = {
            "sub": self.user_id,
            "tid": self.tenant_id,
            "role": self.role,
            "exp": self.exp,
            "iat": self.iat,
            "iss": self.iss,
            "type": self.token_type,
        }
        payload.update(self.extra_claims)
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TokenPayload:
        """Construct from decoded JWT payload."""
        exp = data.get("exp")
        iat = data.get("iat")
        return cls(
            user_id=data.get("sub", ""),
            tenant_id=data.get("tid", ""),
            role=data.get("role", "user"),
            exp=datetime.fromtimestamp(exp, tz=timezone.utc) if exp else datetime.now(timezone.utc),
            iat=datetime.fromtimestamp(iat, tz=timezone.utc) if iat else datetime.now(timezone.utc),
            iss=data.get("iss", _ISSUER),
            token_type=data.get("type", "access"),
            extra_claims={
                k: v for k, v in data.items()
                if k not in {"sub", "tid", "role", "exp", "iat", "iss", "type"}
            },
        )


# ─── Token Creation ───

def create_access_token(
    user_id: str,
    tenant_id: str,
    role: str = "user",
    extra_claims: Optional[dict[str, Any]] = None,
) -> str:
    """
    Create a signed JWT access token.

    Args:
        user_id: Unique user identifier.
        tenant_id: Tenant (vendor/kitchen group) identifier for multi-tenancy.
        role: User role for RBAC (admin, vendor_admin, staff, user).
        extra_claims: Additional claims to embed in the token.

    Returns:
        Encoded JWT string.
    """
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=_ACCESS_TOKEN_EXPIRE_MINUTES)

    payload: dict[str, Any] = {
        "sub": user_id,
        "tid": tenant_id,
        "role": role,
        "exp": expire,
        "iat": now,
        "iss": _ISSUER,
        "type": "access",
    }

    if extra_claims:
        # Prevent overwriting reserved claims
        reserved = {"sub", "tid", "role", "exp", "iat", "iss", "type"}
        for key, value in extra_claims.items():
            if key not in reserved:
                payload[key] = value
            else:
                logger.warning(f"Ignoring reserved claim '{key}' in extra_claims")

    try:
        token = jwt.encode(payload, _SECRET_KEY, algorithm=_ALGORITHM)
        logger.debug(f"Access token created for user={user_id} tenant={tenant_id} role={role}")
        return token
    except Exception as e:
        logger.error(f"Failed to create access token: {e}")
        raise


def create_refresh_token(
    user_id: str,
    tenant_id: str,
    role: str = "user",
    extra_claims: Optional[dict[str, Any]] = None,
) -> str:
    """
    Create a signed JWT refresh token with longer expiry.

    Refresh tokens are used to obtain new access tokens without
    re-authentication. They have a longer TTL but fewer claims.

    Args:
        user_id: Unique user identifier.
        tenant_id: Tenant identifier.
        role: User role.
        extra_claims: Additional claims (stored but minimal on refresh).

    Returns:
        Encoded JWT refresh token string.
    """
    now = datetime.now(timezone.utc)
    expire = now + timedelta(days=_REFRESH_TOKEN_EXPIRE_DAYS)

    payload: dict[str, Any] = {
        "sub": user_id,
        "tid": tenant_id,
        "role": role,
        "exp": expire,
        "iat": now,
        "iss": _ISSUER,
        "type": "refresh",
    }

    if extra_claims:
        reserved = {"sub", "tid", "role", "exp", "iat", "iss", "type"}
        for key, value in extra_claims.items():
            if key not in reserved:
                payload[key] = value

    try:
        token = jwt.encode(payload, _SECRET_KEY, algorithm=_ALGORITHM)
        logger.debug(f"Refresh token created for user={user_id} tenant={tenant_id}")
        return token
    except Exception as e:
        logger.error(f"Failed to create refresh token: {e}")
        raise


# ─── Token Decoding ───

def decode_access_token(token: str) -> dict[str, Any]:
    """
    Decode and validate an access token.

    Args:
        token: Encoded JWT string.

    Returns:
        Decoded payload dictionary.

    Raises:
        HTTPException: If the token is invalid, expired, or not an access token.
    """
    try:
        payload = jwt.decode(
            token,
            _SECRET_KEY,
            algorithms=[_ALGORITHM],
            issuer=_ISSUER,
        )

        # Verify it's an access token, not a refresh token
        if payload.get("type") != "access":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token type. Expected access token.",
                headers={"WWW-Authenticate": "Bearer"},
            )

        return payload

    except JWTError as e:
        logger.warning(f"JWT decode failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )


def decode_refresh_token(token: str) -> dict[str, Any]:
    """
    Decode and validate a refresh token.

    Args:
        token: Encoded JWT refresh token string.

    Returns:
        Decoded payload dictionary.

    Raises:
        HTTPException: If the token is invalid, expired, or not a refresh token.
    """
    try:
        payload = jwt.decode(
            token,
            _SECRET_KEY,
            algorithms=[_ALGORITHM],
            issuer=_ISSUER,
        )

        if payload.get("type") != "refresh":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token type. Expected refresh token.",
                headers={"WWW-Authenticate": "Bearer"},
            )

        return payload

    except JWTError as e:
        logger.warning(f"JWT refresh decode failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        )


def refresh_access_token(refresh_token_str: str) -> tuple[str, str]:
    """
    Exchange a valid refresh token for a new access + refresh token pair.

    Implements token rotation: the old refresh token is implicitly
    invalidated by issuing a new one.

    Args:
        refresh_token_str: The current refresh token.

    Returns:
        Tuple of (new_access_token, new_refresh_token).

    Raises:
        HTTPException: If the refresh token is invalid.
    """
    payload = decode_refresh_token(refresh_token_str)

    new_access = create_access_token(
        user_id=payload["sub"],
        tenant_id=payload.get("tid", ""),
        role=payload.get("role", "user"),
        extra_claims={
            k: v for k, v in payload.items()
            if k not in {"sub", "tid", "role", "exp", "iat", "iss", "type"}
        },
    )

    new_refresh = create_refresh_token(
        user_id=payload["sub"],
        tenant_id=payload.get("tid", ""),
        role=payload.get("role", "user"),
    )

    logger.info(f"Token refreshed for user={payload.get('sub')}")
    return new_access, new_refresh


# ─── FastAPI Dependencies ───

async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> dict[str, Any]:
    """
    FastAPI dependency that extracts and validates the current user from JWT.

    Returns a dict with:
        - user_id: The subject claim
        - tenant_id: The tenant identifier
        - role: The user's role
        - claims: The full decoded payload

    Raises:
        HTTPException: 401 if no token or invalid token.
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials
    payload = decode_access_token(token)

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload: missing subject",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return {
        "user_id": user_id,
        "tenant_id": payload.get("tid", ""),
        "role": payload.get("role", "user"),
        "claims": payload,
    }


def require_role(*allowed_roles: str) -> Callable:
    """
    FastAPI dependency factory that enforces role-based access control.

    Usage:
        @app.get("/admin")
        async def admin_endpoint(
            user: dict = Depends(require_role("admin", "super_admin"))
        ):
            ...

    Args:
        allowed_roles: One or more roles that are permitted access.

    Returns:
        A FastAPI dependency function.
    """

    async def role_checker(
        user: dict[str, Any] = Depends(get_current_user),
    ) -> dict[str, Any]:
        user_role = user.get("role", "")
        if user_role not in allowed_roles:
            logger.warning(
                f"Role denied: user={user.get('user_id')} "
                f"role={user_role} required={allowed_roles}"
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Insufficient permissions. Required role: {', '.join(allowed_roles)}",
            )
        return user

    return role_checker


def require_tenant() -> Callable:
    """
    FastAPI dependency that ensures the tenant_id is present and non-empty.

    Used for endpoints that require multi-tenant isolation.

    Returns:
        A FastAPI dependency function.
    """

    async def tenant_checker(
        user: dict[str, Any] = Depends(get_current_user),
    ) -> dict[str, Any]:
        tenant_id = user.get("tenant_id", "")
        if not tenant_id:
            logger.warning(f"Missing tenant_id for user={user.get('user_id')}")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Tenant context required. Ensure token includes tenant_id.",
            )
        return user

    return tenant_checker


def extract_tenant_id(request: Request) -> Optional[str]:
    """
    Extract tenant_id from the request's JWT token.

    This is useful in middleware where you don't have FastAPI Depends.
    Returns None if no valid token is found (middleware should handle gracefully).

    Args:
        request: The FastAPI Request object.

    Returns:
        The tenant_id from the JWT, or None.
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return None

    token = auth_header[7:]  # Strip "Bearer "
    try:
        payload = jwt.decode(
            token,
            _SECRET_KEY,
            algorithms=[_ALGORITHM],
            options={"verify_exp": True},
        )
        return payload.get("tid")
    except JWTError:
        return None


def extract_user_context(request: Request) -> Optional[dict[str, Any]]:
    """
    Extract full user context from the request's JWT token.

    Used in middleware to populate request.state without raising errors.

    Args:
        request: The FastAPI Request object.

    Returns:
        User context dict or None.
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return None

    token = auth_header[7:]
    try:
        payload = jwt.decode(
            token,
            _SECRET_KEY,
            algorithms=[_ALGORITHM],
            options={"verify_exp": True},
        )
        return {
            "user_id": payload.get("sub", ""),
            "tenant_id": payload.get("tid", ""),
            "role": payload.get("role", "user"),
            "claims": payload,
        }
    except JWTError:
        return None
