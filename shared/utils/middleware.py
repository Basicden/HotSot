"""
HotSot Middleware — FastAPI middleware stack for all services.

Features:
    - TenantMiddleware: Extract and enforce tenant_id from JWT
    - RequestLoggingMiddleware: Log all requests with timing
    - CorrelationIDMiddleware: Propagate or generate X-Correlation-ID
    - RateLimitMiddleware: Per-tenant rate limiting via Redis
    - ErrorHandlingMiddleware: Catch-all with structured error responses

Usage:
    from shared.utils.middleware import setup_middleware

    # In your main.py:
    setup_middleware(app, service_name="order")
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Callable, Optional

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.types import ASGIApp

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# TENANT MIDDLEWARE
# ═══════════════════════════════════════════════════════════════

class TenantMiddleware(BaseHTTPMiddleware):
    """
    Extract tenant_id from JWT and set it in request state.

    This middleware:
    1. Extracts the Bearer token from Authorization header
    2. Decodes the JWT to get tenant_id
    3. Sets request.state.tenant_id for downstream use
    4. Optionally sets tenant_id in database session for RLS

    Skip paths: /health, /ready, /live, /docs, /openapi.json
    """

    SKIP_PATHS = {"/health", "/ready", "/live", "/docs", "/openapi.json", "/redoc", "/metrics"}

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        # Skip health, docs, and metrics endpoints
        if request.url.path in self.SKIP_PATHS:
            return await call_next(request)

        # Extract tenant_id from JWT
        tenant_id = None
        auth_header = request.headers.get("Authorization", "")

        if auth_header.startswith("Bearer "):
            try:
                from shared.auth.jwt import extract_tenant_id
                tenant_id = extract_tenant_id(request)
            except Exception:
                pass

        # Fallback: check X-Tenant-ID header (for internal service calls)
        if not tenant_id:
            tenant_id = request.headers.get("X-Tenant-ID")

        # Set in request state
        request.state.tenant_id = tenant_id

        response = await call_next(request)
        return response


# ═══════════════════════════════════════════════════════════════
# REQUEST LOGGING MIDDLEWARE
# ═══════════════════════════════════════════════════════════════

class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """
    Log all HTTP requests with method, path, status, and duration.

    Logs in structured format:
        method=POST path=/orders status=201 duration_ms=45.2
    """

    SKIP_PATHS = {"/health", "/ready", "/live", "/metrics"}

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        if request.url.path in self.SKIP_PATHS:
            return await call_next(request)

        start_time = time.monotonic()

        try:
            response = await call_next(request)
            duration_ms = (time.monotonic() - start_time) * 1000

            logger.info(
                f"method={request.method} path={request.url.path} "
                f"status={response.status_code} duration_ms={duration_ms:.1f}"
            )

            # Add timing header
            response.headers["X-Response-Time-Ms"] = f"{duration_ms:.1f}"

            return response

        except Exception as e:
            duration_ms = (time.monotonic() - start_time) * 1000
            logger.error(
                f"method={request.method} path={request.url.path} "
                f"status=500 duration_ms={duration_ms:.1f} error={e}"
            )
            raise


# ═══════════════════════════════════════════════════════════════
# CORRELATION ID MIDDLEWARE
# ═══════════════════════════════════════════════════════════════

class CorrelationIDMiddleware(BaseHTTPMiddleware):
    """
    Propagate or generate X-Correlation-ID for distributed tracing.

    If the client sends X-Correlation-ID, it's propagated.
    Otherwise, a new one is generated and added to the response.
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        # Check for existing correlation ID
        correlation_id = request.headers.get("X-Correlation-ID")

        if not correlation_id:
            correlation_id = str(uuid.uuid4())

        # Store in request state for downstream use
        request.state.correlation_id = correlation_id

        response = await call_next(request)

        # Always include in response
        response.headers["X-Correlation-ID"] = correlation_id

        return response


# ═══════════════════════════════════════════════════════════════
# RATE LIMIT MIDDLEWARE
# ═══════════════════════════════════════════════════════════════

class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Per-tenant rate limiting using Redis sliding window.

    Limits are configured via environment variables:
        - RATE_LIMIT_PER_MINUTE: Max requests per minute (default: 60)
        - RATE_LIMIT_BURST: Max burst allowance (default: 10)

    When Redis is unavailable, requests are DENIED (fail-closed).
    """

    SKIP_PATHS = {"/health", "/ready", "/live", "/docs", "/openapi.json", "/metrics"}

    def __init__(self, app: ASGIApp, service_name: str = "hotsot"):
        super().__init__(app)
        self._service_name = service_name

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        if request.url.path in self.SKIP_PATHS:
            return await call_next(request)

        # Get identifier (tenant_id or IP)
        tenant_id = getattr(request.state, "tenant_id", None) if hasattr(request, "state") else None
        identifier = tenant_id or request.client.host if request.client else "unknown"

        try:
            from shared.utils.redis_client import get_redis_client
            from shared.utils.config import get_settings

            redis = get_redis_client(self._service_name)
            settings = get_settings(self._service_name)

            is_allowed, remaining = await redis.check_rate_limit(
                identifier=identifier,
                limit=settings.RATE_LIMIT_PER_MINUTE,
                window_seconds=60,
            )

            if not is_allowed:
                logger.warning(f"Rate limit exceeded: identifier={identifier}")
                return JSONResponse(
                    status_code=429,
                    content={
                        "detail": "Rate limit exceeded. Please retry later.",
                        "retry_after_seconds": 60,
                    },
                    headers={"Retry-After": "60"},
                )

        except Exception as e:
            # Fail-CLOSED: When Redis is down, DENY the request rather than
            # silently allowing it.  Bypassing rate limits during an outage
            # could allow abuse floods that overwhelm downstream services.
            logger.error(f"Rate limit check failed (Redis unavailable): {e} — DENYING request (fail-CLOSED)")
            return JSONResponse(
                status_code=503,
                content={
                    "detail": "Rate limiting service unavailable. Please retry later.",
                    "retry_after_seconds": 30,
                },
                headers={"Retry-After": "30"},
            )

        return await call_next(request)


# ═══════════════════════════════════════════════════════════════
# ERROR HANDLING MIDDLEWARE
# ═══════════════════════════════════════════════════════════════

class ErrorHandlingMiddleware(BaseHTTPMiddleware):
    """
    Catch-all error handler with structured error responses.

    Catches unhandled exceptions and returns:
        - 500 Internal Server Error
        - JSON body with error details (dev) or generic message (prod)
        - Correlation ID for debugging
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        try:
            return await call_next(request)
        except Exception as e:
            correlation_id = getattr(request.state, "correlation_id", str(uuid.uuid4())) if hasattr(request, "state") else str(uuid.uuid4())

            logger.error(
                f"Unhandled exception: method={request.method} path={request.url.path} "
                f"error={e} correlation_id={correlation_id}",
                exc_info=True,
            )

            import os
            environment = os.getenv("ENVIRONMENT", "development")

            error_detail = str(e) if environment == "development" else "Internal server error"

            return JSONResponse(
                status_code=500,
                content={
                    "detail": error_detail,
                    "correlation_id": correlation_id,
                    "type": type(e).__name__,
                },
            )


# ═══════════════════════════════════════════════════════════════
# SETUP HELPER
# ═══════════════════════════════════════════════════════════════

def setup_middleware(app: FastAPI, service_name: str = "hotsot") -> None:
    """
    Configure all middleware for a FastAPI app.

    Adds middleware in the correct order (Starlette processes in reverse):
        1. ErrorHandlingMiddleware (outermost — catches all errors)
        2. CorrelationIDMiddleware
        3. RequestLoggingMiddleware
        4. RateLimitMiddleware
        5. TenantMiddleware (innermost — sets tenant_id first)

    Args:
        app: The FastAPI application instance.
        service_name: The microservice identifier.
    """
    # Add in reverse order (Starlette processes last-added first)
    app.add_middleware(ErrorHandlingMiddleware)
    app.add_middleware(CorrelationIDMiddleware)
    app.add_middleware(RequestLoggingMiddleware)
    app.add_middleware(RateLimitMiddleware, service_name=service_name)
    app.add_middleware(TenantMiddleware)

    logger.info(f"Middleware configured for service={service_name}")
