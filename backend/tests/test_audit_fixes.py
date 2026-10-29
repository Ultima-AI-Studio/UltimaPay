import asyncio
import os
import tempfile
from pathlib import Path

from httpx import ASGITransport, AsyncClient


def _run(coro):
    return asyncio.run(coro)


def test_http_order_idempotency_uses_allocation_fields_and_user_namespace():
    with tempfile.TemporaryDirectory(prefix="ultimapay-audit-order-") as temp:
        path = Path(temp) / "orders.db"
        os.environ["ULTIMAPAY_DATABASE_URL"] = "sqlite:///" + path.as_posix()
        os.environ["ULTIMAPAY_USER_AUTH_SECRET"] = "audit-user-secret"
        os.environ["ULTIMAPAY_LIVE_PAYMENT"] = "0"

        from app import db as database
        from app.auth.policy import sign_user
        from app.main import app

        async def exercise():
            async with app.router.lifespan_context(app):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://testserver"
                ) as client:
                    def headers(user):
                        return {
                            "X-Pay-User": user,
                            "X-Pay-Signature": sign_user(user),
                        }

                    body = {
                        "planId": "SP-01",
                        "channel": "TRON",
                        "idempotencyKey": "same-browser-key",
                    }
                    first, second = await asyncio.gather(
                        client.post("/api/pay/order", headers=headers("alice"), json=body),
                        client.post("/api/pay/order", headers=headers("alice"), json=body),
                    )
                    assert first.status_code == 200, first.text
                    assert second.status_code == 200, second.text
                    assert first.json()["data"]["orderNo"] == second.json()["data"]["orderNo"]

                    other = await client.post(
                        "/api/pay/order", headers=headers("bob"), json=body
                    )
                    assert other.status_code == 200, other.text
                    assert other.json()["data"]["orderNo"] != first.json()["data"]["orderNo"]

        _run(exercise())
        connection = database.connection()
        try:
            rows = connection.execute(
                "SELECT username,idempotency_key,amount_units,asset_key,allocation_key"
                " FROM payment_orders ORDER BY id"
            ).fetchall()
            assert len(rows) == 2
            assert rows[0][1].startswith("user:alice:")
            assert rows[1][1].startswith("user:bob:")
            assert all(row[2] is not None and row[3] and row[4] for row in rows)
        finally:
            connection.close()


def test_merchant_idempotency_is_namespaced_and_sessions_enforce_owner():
    with tempfile.TemporaryDirectory(prefix="ultimapay-audit-merchant-") as temp:
        path = Path(temp) / "merchants.db"
        os.environ["ULTIMAPAY_DATABASE_URL"] = "sqlite:///" + path.as_posix()

        from app import db as database
        from app.payments import merchant_api

        database.migrate()
        connection = database.connection()
        try:
            now = "2026-10-29 22:30:00"
            for code in ("merchant-a", "merchant-b"):
                connection.execute(
                    "INSERT INTO payment_merchants"
                    "(code,name,api_key,status,enabled,created_at,tron_address)"
                    " VALUES(?,?,?,'active',1,?,?)",
                    (code, code, f"key-{code}", now, f"address-{code}"),
                )
            connection.commit()

            order_a, session_a, error = merchant_api.create_merchant_intent(
                connection, "merchant-a", "EXTERNAL-1", "user-a", 1_230_000,
                "TRON", now=now
            )
            assert error is None
            order_b, session_b, error = merchant_api.create_merchant_intent(
                connection, "merchant-b", "EXTERNAL-1", "user-b", 1_240_000,
                "TRON", now=now
            )
            assert error is None
            assert order_a["order_no"] != order_b["order_no"]
            assert order_a["merchant"] == "merchant-a"
            assert order_b["merchant"] == "merchant-b"

            duplicate, _, error = merchant_api.create_merchant_intent(
                connection, "merchant-a", "EXTERNAL-1", "different-user", 9_990_000,
                "TRON", now=now
            )
            assert duplicate is None
            assert "幂等" in error

            resolved_a, error = merchant_api.resolve_pay_session(
                connection, session_a, order_a["order_no"], now=now
            )
            assert error is None and resolved_a["merchant"] == "merchant-a"
            resolved_b, error = merchant_api.resolve_pay_session(
                connection, session_b, order_b["order_no"], now=now
            )
            assert error is None and resolved_b["merchant"] == "merchant-b"

            connection.execute(
                "UPDATE payment_pay_sessions SET merchant='merchant-b' WHERE session_token=?",
                (session_a,),
            )
            connection.commit()
            denied, error = merchant_api.resolve_pay_session(
                connection, session_a, order_a["order_no"], now=now
            )
            assert denied is None
            assert "商户" in error
        finally:
            connection.close()


def test_cold_database_has_no_publicly_usable_seed_merchant_keys():
    with tempfile.TemporaryDirectory(prefix="ultimapay-audit-seeds-") as temp:
        path = Path(temp) / "seeds.db"
        os.environ["ULTIMAPAY_DATABASE_URL"] = "sqlite:///" + path.as_posix()

        from app import db as database

        database.migrate()
        connection = database.connection()
        try:
            exposed = connection.execute(
                "SELECT code,api_key FROM payment_merchants"
                " WHERE enabled=1 AND COALESCE(api_key,'')!=''"
            ).fetchall()
            assert exposed == []
            seeds = connection.execute(
                "SELECT code,api_key,enabled FROM payment_merchants ORDER BY code"
            ).fetchall()
            assert seeds
            assert all(not row[1] and not row[2] for row in seeds)
        finally:
            connection.close()


def test_admin_can_provision_disabled_seed_once_without_public_credentials():
    with tempfile.TemporaryDirectory(prefix="ultimapay-audit-provision-") as temp:
        path = Path(temp) / "provision.db"
        os.environ["ULTIMAPAY_DATABASE_URL"] = "sqlite:///" + path.as_posix()
        os.environ["ULTIMAPAY_ADMIN_TOKEN"] = "audit-admin-token"
        os.environ["ULTIMAPAY_LIVE_PAYMENT"] = "0"

        from app import db as database
        from app.main import app

        async def exercise():
            async with app.router.lifespan_context(app):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://testserver"
                ) as client:
                    headers = {"Authorization": "Bearer audit-admin-token"}
                    configured = await client.post(
                        "/api/pay/merchants",
                        headers=headers,
                        json={
                            "code": "ultima-gold",
                            "name": "Ultima Gold",
                            "enabled": True,
                            "tronAddress": "TProvisionedAddress",
                        },
                    )
                    assert configured.status_code == 200, configured.text

                    changed = await client.post(
                        "/api/pay/merchants",
                        headers=headers,
                        json={
                            "code": "ultima-gold",
                            "name": "Ultima Gold",
                            "enabled": True,
                            "tronAddress": "TDifferentAddress",
                        },
                    )
                    assert changed.status_code == 400

        _run(exercise())
        connection = database.connection()
        try:
            row = connection.execute(
                "SELECT api_key,status,enabled,tron_address FROM payment_merchants"
                " WHERE code='ultima-gold'"
            ).fetchone()
            assert row[0].startswith("upay_live_")
            assert row[0] not in {
                "upay_live_ultimagold00000000000001",
                "upay_live_ultimaagent0000000000002",
                "upay_live_console0000000000000003",
            }
            assert tuple(row[1:]) == ("active", 1, "TProvisionedAddress")
        finally:
            connection.close()
