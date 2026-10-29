import asyncio

from httpx import ASGITransport, AsyncClient


def test_app_exposes_standalone_health_endpoint():
    from app.main import app

    assert app.title == "UltimaPay API"

    async def request_health():
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            return await client.get("/health")

    response = asyncio.run(request_health())
    assert response.status_code == 200
    assert response.json()["status"] == "ok"

