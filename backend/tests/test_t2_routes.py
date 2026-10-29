import asyncio
import logging
import os
import tempfile
from pathlib import Path

from httpx import ASGITransport, AsyncClient


EXPECTED_PATHS = {
    "/api/pay/qrcode",
    "/api/pay/order",
    "/api/pay/channels",
    "/api/pay/merchant-orders",
    "/api/pay/tickets",
    "/api/pay/merchants",
    "/api/pay/address",
    "/api/pay/orders",
    "/api/pay/apply",
    "/api/pay/verify",
    "/api/pay/tickets/process",
    "/api/pay/ticket",
    "/api/pay/config",
    "/api/pay/create",
    "/api/pay/callback",
    "/api/pay/confirm",
    "/api/pay/expire",
}

GOLD_FEED_SERVER = Path(r"H:\UltimaAI\UltimaGold\news-feed\feed_server.py")


def _run(coro):
    return asyncio.run(coro)


def test_openapi_exposes_complete_new_payment_route_set():
    from app.main import app

    paths = set(app.openapi()["paths"])
    assert EXPECTED_PATHS <= paths
    assert not any("tupp" in path.lower() for path in paths)


def test_gold_feed_server_no_longer_owns_payment_routes_or_loop():
    source = GOLD_FEED_SERVER.read_text(encoding="utf-8")
    assert "def payment_loop" not in source
    assert "target=payment_loop" not in source
    assert "_PAYMENT_CHANNELS" not in source
    retired_brand = "tuppo" + "au"
    assert f"/api/{retired_brand}/payment" not in source
    assert "/api/ultimapay/payment" not in source
    assert "def entitlement_loop" in source
    assert "process_fulfillments()" in source
    assert "process_invite_rewards()" in source


def test_real_order_query_qrcode_and_receipt_flow():
    with tempfile.TemporaryDirectory(prefix="ultimapay-t2-") as temp:
        db_path = Path(temp) / "routes.db"
        os.environ["ULTIMAPAY_DATABASE_URL"] = "sqlite:///" + db_path.as_posix()
        os.environ["ULTIMAPAY_USER_AUTH_SECRET"] = "test-user-secret"
        os.environ["ULTIMAPAY_ADMIN_TOKEN"] = "test-admin-token"
        os.environ["ULTIMAPAY_LIVE_PAYMENT"] = "0"

        from app.auth.policy import sign_user
        from app.main import app

        async def exercise():
            async with app.router.lifespan_context(app):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://testserver"
                ) as client:
                    missing_auth = await client.post(
                        "/api/pay/order", json={"planId": "SP-01", "channel": "TRON"}
                    )
                    assert missing_auth.status_code == 401

                    admin_denied = await client.get("/api/pay/orders")
                    assert admin_denied.status_code == 401

                    admin_allowed = await client.get(
                        "/api/pay/orders",
                        headers={"Authorization": "Bearer test-admin-token"},
                    )
                    assert admin_allowed.status_code == 200

                    created = await client.post(
                        "/api/pay/order",
                        headers={
                            "X-Pay-User": "smoke_user",
                            "X-Pay-Signature": sign_user("smoke_user"),
                        },
                        json={"planId": "SP-01", "channel": "TRON"},
                    )
                    assert created.status_code == 200, created.text
                    order = created.json()["data"]
                    assert order["status"] == "pending"
                    assert 198.1 <= order["amountUsdt"] <= 198.9

                    invalid_merchant_key = await client.post(
                        "/api/pay/order",
                        headers={
                            "X-Pay-User": "smoke_user",
                            "X-Pay-Signature": sign_user("smoke_user"),
                            "X-Pay-Key": "invalid-key-must-not-fallback",
                        },
                        json={"planId": "SP-01", "channel": "TRON"},
                    )
                    assert invalid_merchant_key.status_code == 401

                    queried = await client.get(
                        "/api/pay/order", params={"orderNo": order["orderNo"]}
                    )
                    assert queried.status_code == 200
                    assert queried.json()["data"]["orderNo"] == order["orderNo"]

                    qrcode = await client.get(
                        "/api/pay/qrcode", params={"text": "tron:test?amount=198.1"}
                    )
                    assert qrcode.status_code == 200
                    assert qrcode.headers["content-type"] == "image/png"
                    assert qrcode.content.startswith(b"\x89PNG")

                    receipt = await client.post(
                        "/api/pay/confirm",
                        json={"orderNo": order["orderNo"], "txHash": "0xreceipt"},
                    )
                    assert receipt.status_code == 200
                    assert receipt.json()["data"]["status"] == "submitted"

                    callback = await client.post(
                        "/api/pay/callback",
                        json={"orderNo": order["orderNo"], "txHash": "0xcallback"},
                    )
                    assert callback.status_code == 403

        _run(exercise())
        assert db_path.exists()


def test_payment_iteration_reports_heartbeat_with_live_polling_disabled():
    with tempfile.TemporaryDirectory(prefix="ultimapay-loop-") as temp:
        db_path = Path(temp) / "loop.db"
        os.environ["ULTIMAPAY_DATABASE_URL"] = "sqlite:///" + db_path.as_posix()
        os.environ["ULTIMAPAY_LIVE_PAYMENT"] = "0"

        from app.services.payment_loop import run_payment_iteration
        from app.services.payment_loop import LOGGER
        from app.storage import ensure_payment_base_schema, open_connection

        db = open_connection()
        ensure_payment_base_schema(db)
        db.close()
        result = run_payment_iteration()
        assert LOGGER.isEnabledFor(logging.INFO)
        assert result["heartbeat"] is True
        assert result["livePolling"] is False
        assert result["expired"] == 0

