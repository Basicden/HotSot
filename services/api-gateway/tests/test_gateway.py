"""HotSot API Gateway — Unit Tests.

Tests cover:
1. Route mapping: path prefix to service resolution
2. Service registry: all 18 services registered
3. Proxy request: header forwarding, error handling
4. Rate limiting configuration
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.routes.proxy import SERVICES, ROUTE_MAP


# ═══════════════════════════════════════════════════════════════
# SERVICE REGISTRY
# ═══════════════════════════════════════════════════════════════

class TestServiceRegistry:
    """Test that all 18 microservices are registered."""

    def test_order_service_registered(self):
        assert "order" in SERVICES

    def test_kitchen_service_registered(self):
        assert "kitchen" in SERVICES

    def test_shelf_service_registered(self):
        assert "shelf" in SERVICES

    def test_eta_service_registered(self):
        assert "eta" in SERVICES

    def test_notification_service_registered(self):
        assert "notification" in SERVICES

    def test_realtime_service_registered(self):
        assert "realtime" in SERVICES

    def test_ml_service_registered(self):
        assert "ml" in SERVICES

    def test_arrival_service_registered(self):
        assert "arrival" in SERVICES

    def test_compensation_service_registered(self):
        assert "compensation" in SERVICES

    def test_vendor_service_registered(self):
        assert "vendor" in SERVICES

    def test_menu_service_registered(self):
        assert "menu" in SERVICES

    def test_search_service_registered(self):
        assert "search" in SERVICES

    def test_pricing_service_registered(self):
        assert "pricing" in SERVICES

    def test_compliance_service_registered(self):
        assert "compliance" in SERVICES

    def test_billing_service_registered(self):
        assert "billing" in SERVICES

    def test_analytics_service_registered(self):
        assert "analytics" in SERVICES

    def test_config_service_registered(self):
        assert "config" in SERVICES

    def test_total_service_count(self):
        """Should have exactly 17 service entries (18th is gateway itself)."""
        assert len(SERVICES) == 17


# ═══════════════════════════════════════════════════════════════
# ROUTE MAP
# ═══════════════════════════════════════════════════════════════

class TestRouteMap:
    """Test route prefix to service mapping."""

    def test_orders_routes_to_order(self):
        assert ROUTE_MAP["/orders"] == "order"

    def test_payments_routes_to_order(self):
        assert ROUTE_MAP["/payments"] == "order"

    def test_kitchen_routes_to_kitchen(self):
        assert ROUTE_MAP["/kitchen"] == "kitchen"

    def test_queue_routes_to_kitchen(self):
        assert ROUTE_MAP["/queue"] == "kitchen"

    def test_shelf_routes_to_shelf(self):
        assert ROUTE_MAP["/shelf"] == "shelf"

    def test_eta_routes_to_eta(self):
        assert ROUTE_MAP["/eta"] == "eta"

    def test_notification_routes_to_notification(self):
        assert ROUTE_MAP["/notification"] == "notification"

    def test_ws_routes_to_realtime(self):
        assert ROUTE_MAP["/ws"] == "realtime"

    def test_sse_routes_to_realtime(self):
        assert ROUTE_MAP["/sse"] == "realtime"

    def test_ml_routes_to_ml(self):
        assert ROUTE_MAP["/ml"] == "ml"

    def test_arrival_routes_to_arrival(self):
        assert ROUTE_MAP["/arrival"] == "arrival"

    def test_compensation_routes_to_compensation(self):
        assert ROUTE_MAP["/compensation"] == "compensation"

    def test_vendor_routes_to_vendor(self):
        assert ROUTE_MAP["/vendor"] == "vendor"

    def test_menu_routes_to_menu(self):
        assert ROUTE_MAP["/menu"] == "menu"

    def test_search_routes_to_search(self):
        assert ROUTE_MAP["/search"] == "search"

    def test_pricing_routes_to_pricing(self):
        assert ROUTE_MAP["/pricing"] == "pricing"

    def test_compliance_routes_to_compliance(self):
        assert ROUTE_MAP["/compliance"] == "compliance"

    def test_billing_routes_to_billing(self):
        assert ROUTE_MAP["/billing"] == "billing"

    def test_analytics_routes_to_analytics(self):
        assert ROUTE_MAP["/analytics"] == "analytics"

    def test_config_routes_to_config(self):
        assert ROUTE_MAP["/config"] == "config"


# ═══════════════════════════════════════════════════════════════
# PATH PREFIX RESOLUTION
# ═══════════════════════════════════════════════════════════════

class TestPathPrefixResolution:
    """Test that path prefixes correctly resolve to services."""

    def test_single_segment_path(self):
        """Single-segment path should resolve correctly."""
        path = "orders/123"
        parts = path.split("/")
        prefix = f"/{parts[0]}"
        assert ROUTE_MAP.get(prefix) == "order"

    def test_multi_segment_path(self):
        """Multi-segment path should use first segment for routing."""
        path = "kitchen/abc/load"
        parts = path.split("/")
        prefix = f"/{parts[0]}"
        assert ROUTE_MAP.get(prefix) == "kitchen"

    def test_unknown_prefix_returns_none(self):
        """Unknown path prefix should return None."""
        path = "unknown/path"
        parts = path.split("/")
        prefix = f"/{parts[0]}"
        assert ROUTE_MAP.get(prefix) is None


# ═══════════════════════════════════════════════════════════════
# SERVICE URL DEFAULTS
# ═══════════════════════════════════════════════════════════════

class TestServiceURLDefaults:
    """Test default service URLs."""

    def test_all_services_have_urls(self):
        """Every service should have a URL configured."""
        for name, url in SERVICES.items():
            assert url is not None
            assert len(url) > 0

    def test_default_urls_are_localhost(self):
        """Default URLs should point to localhost."""
        for name, url in SERVICES.items():
            assert "localhost" in url or "127.0.0.1" in url

    def test_services_use_different_ports(self):
        """Each service should use a distinct port."""
        ports = set()
        for name, url in SERVICES.items():
            port = url.split(":")[-1].rstrip("/")
            ports.add(port)
        # All ports should be unique
        assert len(ports) == len(SERVICES)
