"""
HotSot Compliance Decorators — Enforcement-as-Architecture.

Provides decorators that ensure compliance checks are actually performed
before allowing business operations. This implements the "Compliance-as-Architecture"
principle from the HotSot architecture.

Usage:
    from shared.compliance_decorators import compliance_check, require_compliance

    @compliance_check("FSSAI")
    async def create_vendor_menu(...):
        # This will verify FSSAI compliance before allowing menu creation
        ...
"""

from __future__ import annotations

import functools
import logging
from typing import Any, Callable, List, Optional

logger = logging.getLogger("hotsot.compliance")


# Registry of compliance check functions per domain
_CHECK_REGISTRY: dict[str, Callable] = {}


def register_check(domain: str):
    """Decorator to register a compliance check function for a domain."""
    def decorator(func: Callable) -> Callable:
        _CHECK_REGISTRY[domain] = func
        return func
    return decorator


def compliance_check(*domains: str):
    """
    Decorator that performs compliance verification before the decorated function.

    Unlike simply checking a boolean flag, this decorator calls the registered
    compliance check function for each domain, which performs actual verification.

    Args:
        domains: One or more compliance domain names (e.g., "FSSAI", "GST", "DPDP", "RBI").

    Example:
        @compliance_check("FSSAI", "GST")
        async def create_vendor_menu(vendor_id, ...):
            # FSSAI and GST compliance will be verified before this runs
            ...
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs) -> Any:
            # Look for vendor_id or entity_id in kwargs
            entity_id = kwargs.get("vendor_id") or kwargs.get("entity_id")
            tenant_id = kwargs.get("tenant_id")
            session = kwargs.get("session")

            if not entity_id or not tenant_id or not session:
                logger.warning(
                    f"compliance_check: Cannot verify — missing entity_id, tenant_id, or session "
                    f"in {func.__name__}. Proceeding without compliance gate."
                )
                return await func(*args, **kwargs)

            for domain in domains:
                check_func = _CHECK_REGISTRY.get(domain)
                if check_func:
                    try:
                        result = await check_func(entity_id, tenant_id, session)
                        status = result.get("overall_status", result.get("status", "PENDING"))
                        if status == "FAILED":
                            from fastapi import HTTPException
                            raise HTTPException(
                                status_code=403,
                                detail=f"Compliance check FAILED for {domain}: {result.get('reason', 'Verification failed')}",
                            )
                        elif status == "PENDING":
                            logger.warning(
                                f"Compliance PENDING for {domain} on entity {entity_id} — allowing with audit trail"
                            )
                    except Exception as e:
                        if hasattr(e, 'status_code'):  # HTTPException
                            raise
                        logger.error(f"Compliance check error for {domain}: {e}")
                else:
                    logger.debug(f"No compliance check registered for domain: {domain}")

            return await func(*args, **kwargs)
        return wrapper
    return decorator


def require_compliance(*domains: str):
    """
    Strict version of compliance_check that blocks PENDING status too.

    Use for critical operations where PENDING compliance is not acceptable.
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs) -> Any:
            entity_id = kwargs.get("vendor_id") or kwargs.get("entity_id")
            tenant_id = kwargs.get("tenant_id")
            session = kwargs.get("session")

            if not entity_id or not tenant_id or not session:
                from fastapi import HTTPException
                raise HTTPException(
                    status_code=403,
                    detail=f"Compliance verification required but entity_id, tenant_id, or session not provided",
                )

            for domain in domains:
                check_func = _CHECK_REGISTRY.get(domain)
                if check_func:
                    result = await check_func(entity_id, tenant_id, session)
                    status = result.get("overall_status", result.get("status", "PENDING"))
                    if status != "PASSED":
                        from fastapi import HTTPException
                        raise HTTPException(
                            status_code=403,
                            detail=f"Compliance check {status} for {domain}: {result.get('reason', 'Not verified')}",
                        )
                else:
                    logger.warning(f"No compliance check registered for domain: {domain}")

            return await func(*args, **kwargs)
        return wrapper
    return decorator


def get_registered_domains() -> list[str]:
    """
    Return a list of all registered compliance domain names.

    Useful for diagnostics, health checks, and startup verification
    to confirm that compliance check functions have been properly
    registered.

    Returns:
        List of domain name strings (e.g., ["FSSAI", "GST", "DPDP", "RBI"]).
    """
    return list(_CHECK_REGISTRY.keys())


# Auto-register compliance checks on import.
# This ensures that when compliance_decorators is imported,
# the real check functions from the compliance service are
# registered in _CHECK_REGISTRY.
try:
    import shared.compliance_registry  # noqa: F401
except ImportError:
    logger.debug("compliance_registry not available — compliance checks will not be registered")
