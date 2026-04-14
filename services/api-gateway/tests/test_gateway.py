"""HotSot API Gateway — Unit Tests."""
from app.main import app
from httpx import AsyncClient, ASGITransport
import pytest

@pytest.mark.asyncio
async def test_health_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["gateway"] == "healthy"
        assert "services" in data

@pytest.mark.asyncio
async def test_rate_limit_headers():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/health")
        assert "x-response-time" in resp.headers
