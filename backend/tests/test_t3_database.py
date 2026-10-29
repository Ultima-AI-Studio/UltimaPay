import asyncio
import os
import tempfile
from pathlib import Path

from httpx import ASGITransport, AsyncClient


EXPECTED_SESSION_COLUMNS = [
    ("id", "INTEGER", 0, 1),
    ("session_token", "TEXT", 1, 0),
    ("order_no", "TEXT", 1, 0),
    ("merchant", "TEXT", 1, 0),
    ("expires_at", "TEXT", 1, 0),
    ("created_at", "TEXT", 1, 0),
]

GOLD_DB_SOURCE = Path(r"H:\UltimaAI\UltimaGold\backend\ultima_backend\db.py")


def _run(coro):
    return asyncio.run(coro)


def test_cold_database_applies_exact_m009_once():
    with tempfile.TemporaryDirectory(prefix="ultimapay-t3-") as temp:
        path = Path(temp) / "standalone.db"
        os.environ["ULTIMAPAY_DATABASE_URL"] = "sqlite:///" + path.as_posix()

        from app import db as database

        assert database.database_path() == path.resolve()
        assert "UltimaGold" not in str(database.database_path())
        assert database.migrate() == [9]
        assert database.migrate() == []

        connection = database.connection()
        try:
            columns = [
                (row[1], row[2], row[3], row[5])
                for row in connection.execute("PRAGMA table_info(payment_pay_sessions)")
            ]
            assert columns == EXPECTED_SESSION_COLUMNS
            index = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_pay_session_order'"
            ).fetchone()
            assert index is not None
            version = connection.execute(
                "SELECT version,name FROM schema_version WHERE version=9"
            ).fetchone()
            assert tuple(version) == (9, "payment_pay_sessions")
            required = {
                "payment_orders",
                "payment_config",
                "payment_channel_config",
                "payment_merchants",
                "payment_pay_sessions",
            }
            tables = {
                row[0]
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            assert required <= tables
        finally:
            connection.close()


def test_merchant_intent_and_order_scoped_session_routes_on_cold_database():
    with tempfile.TemporaryDirectory(prefix="ultimapay-t3-api-") as temp:
        path = Path(temp) / "api.db"
        os.environ["ULTIMAPAY_DATABASE_URL"] = "sqlite:///" + path.as_posix()
        os.environ["ULTIMAPAY_LIVE_PAYMENT"] = "0"

        from app import db as database
        from app.main import app

        database.migrate()
        connection = database.connection()
        connection.execute(
            "UPDATE payment_merchants SET tron_address=? WHERE code='ultima-gold'",
            ("TStandaloneMerchantAddress",),
        )
        connection.commit()
        connection.close()

        async def exercise():
            async with app.router.lifespan_context(app):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://testserver"
                ) as client:
                    denied = await client.post(
                        "/api/pay/merchant/intents",
                        json={
                            "merchant": "ultima-gold",
                            "externalOrderNo": "EXT-001",
                            "externalUserRef": "external-user",
                            "amountUnits": 1230000,
                            "chain": "TRON",
                        },
                    )
                    assert denied.status_code == 401

                    headers = {"X-Pay-Key": "upay_live_ultimagold00000000000001"}
                    first = await client.post(
                        "/api/pay/merchant/intents",
                        headers=headers,
                        json={
                            "merchant": "ultima-gold",
                            "externalOrderNo": "EXT-001",
                            "externalUserRef": "external-user",
                            "amountUnits": 1230000,
                            "chain": "TRON",
                        },
                    )
                    assert first.status_code == 200, first.text
                    intent = first.json()["data"]
                    assert intent["sessionToken"].startswith("ps_")

                    second = await client.post(
                        "/api/pay/merchant/intents",
                        headers=headers,
                        json={
                            "merchant": "ultima-gold",
                            "externalOrderNo": "EXT-002",
                            "externalUserRef": "external-user",
                            "amountUnits": 1240000,
                            "chain": "TRON",
                        },
                    )
                    assert second.status_code == 200, second.text
                    other_order = second.json()["data"]["orderNo"]

                    resolved = await client.get(
                        "/api/pay/session/order",
                        params={
                            "sessionToken": intent["sessionToken"],
                            "orderNo": intent["orderNo"],
                        },
                    )
                    assert resolved.status_code == 200, resolved.text
                    assert resolved.json()["data"]["orderNo"] == intent["orderNo"]

                    cross_order = await client.get(
                        "/api/pay/session/order",
                        params={
                            "sessionToken": intent["sessionToken"],
                            "orderNo": other_order,
                        },
                    )
                    assert cross_order.status_code == 403

        _run(exercise())


def test_gold_no_longer_owns_m009():
    source = GOLD_DB_SOURCE.read_text(encoding="utf-8")
    assert "_m009_payment_pay_sessions" not in source
    assert "'payment_pay_sessions'" not in source
    assert "SCHEMA_VERSION = 8" in source
