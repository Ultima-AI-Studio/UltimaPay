import asyncio
import importlib
import os
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from httpx import ASGITransport, AsyncClient


GOLD_ROOT = Path(r"H:\UltimaAI\UltimaGold")
GOLD_BACKEND = GOLD_ROOT / "backend"
GOLD_FEED = GOLD_ROOT / "news-feed" / "feed_server.py"
GOLD_SITE = GOLD_ROOT / "ultima-gold-site" / "UltimaGold官网.html"


def _run(coro):
    return asyncio.run(coro)


def test_gold_has_no_payment_package_routes_schema_or_live_caller():
    assert not (GOLD_BACKEND / "ultima_backend" / "payments").exists()

    feed = GOLD_FEED.read_text(encoding="utf-8")
    retired_brand = "tuppo" + "au"
    for forbidden in (
        "def payment_loop",
        "target=payment_loop",
        "CREATE TABLE IF NOT EXISTS payment_orders",
        "CREATE TABLE IF NOT EXISTS payment_config",
        "CREATE TABLE IF NOT EXISTS payment_channel_config",
        "CREATE TABLE IF NOT EXISTS payment_tickets",
        "CREATE TABLE IF NOT EXISTS payment_merchants",
        "PAYMENT_ADDRESS_DEFAULT",
        "PAYMENT_TTL_DEFAULT",
        f"/api/{retired_brand}/payment",
        "/api/ultimapay/payment",
        f"/api/{retired_brand}/my-orders",
    ):
        assert forbidden not in feed

    api_registry = (GOLD_BACKEND / "ultima_backend" / "api" / "__init__.py").read_text(
        encoding="utf-8"
    )
    assert "/api/pay/" not in api_registry
    assert "/payment/" not in api_registry

    policy = (GOLD_BACKEND / "ultima_backend" / "auth" / "policy.py").read_text(
        encoding="utf-8"
    )
    assert "def merchant_principal" not in policy
    assert "/payment/merchant-orders" not in policy

    site = GOLD_SITE.read_text(encoding="utf-8")
    assert f"{retired_brand}/payment" not in site
    assert "window.__ULTIMAPAY_API__" in site
    assert site.count("/api/pay/") >= 6

    assert not (GOLD_ROOT / "scripts" / "reconcile.py").exists()
    assert (Path(__file__).parents[1] / "scripts" / "reconcile.py").exists()


def test_membership_event_helper_preserves_atomic_idempotent_enqueue():
    sys.path.insert(0, str(GOLD_BACKEND))
    try:
        module = importlib.import_module("ultima_backend.membership.payment_events")
    finally:
        sys.path.pop(0)

    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute(
        """CREATE TABLE payment_events(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id TEXT NOT NULL UNIQUE,
        order_no TEXT NOT NULL,
        event_type TEXT NOT NULL,
        payload TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        attempts INTEGER NOT NULL DEFAULT 0,
        max_attempts INTEGER NOT NULL DEFAULT 8,
        next_retry_at TEXT,
        last_error TEXT,
        created_at TEXT NOT NULL,
        delivered_at TEXT,
        UNIQUE(order_no,event_type))"""
    )
    now = datetime(2026, 10, 29, 20, 0, 0)
    first = module.enqueue_event(db, "ORDER-1", module.EVENT_VERIFIED, {"b": 2, "a": 1}, now)
    second = module.enqueue_event(db, "ORDER-1", module.EVENT_VERIFIED, {"ignored": True}, now)
    assert first["duplicate"] is False
    assert second["duplicate"] is True
    assert first["event_id"] == second["event_id"]
    assert db.in_transaction is True
    assert db.execute("SELECT payload FROM payment_events").fetchone()[0] == '{"a": 1, "b": 2}'
    db.rollback()
    assert db.execute("SELECT COUNT(*) FROM payment_events").fetchone()[0] == 0


def test_gold_cold_init_no_longer_requires_payment_tables():
    with tempfile.TemporaryDirectory(prefix="ultimagold-t4-") as temp:
        path = Path(temp) / "gold.db"
        env = os.environ.copy()
        env.update(
            {
                "UG_DB_PATH": str(path),
                "UGP_TEST_MODE": "1",
                "ULTIMAPAY_USER_AUTH_SECRET": "test-shared-secret",
            }
        )
        code = (
            "import sys; "
            f"sys.path.insert(0, {str(GOLD_BACKEND)!r}); "
            f"sys.path.insert(0, {str(GOLD_ROOT / 'news-feed')!r}); "
            "import feed_server; feed_server.init_db()"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            env=env,
            text=True,
            capture_output=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        db = sqlite3.connect(path)
        tables = {
            row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        db.close()
        assert "users" in tables
        assert "payment_orders" not in tables
        assert "payment_config" not in tables
        assert "payment_channel_config" not in tables
        assert "payment_merchants" not in tables


def test_signed_external_gold_user_can_use_pay_and_cors():
    with tempfile.TemporaryDirectory(prefix="ultimapay-t4-") as temp:
        os.environ["ULTIMAPAY_DATABASE_URL"] = "sqlite:///" + (
            Path(temp) / "detached.db"
        ).as_posix()
        os.environ["ULTIMAPAY_USER_AUTH_SECRET"] = "shared-detached-secret"
        os.environ["ULTIMAPAY_CORS_ORIGINS"] = "http://127.0.0.1:8912"
        os.environ["ULTIMAPAY_LIVE_PAYMENT"] = "0"

        from app.auth.policy import sign_user
        from app.main import app

        async def exercise():
            async with app.router.lifespan_context(app):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://testserver"
                ) as client:
                    preflight = await client.options(
                        "/api/pay/order",
                        headers={
                            "Origin": "http://127.0.0.1:8912",
                            "Access-Control-Request-Method": "POST",
                            "Access-Control-Request-Headers": "x-pay-user,x-pay-signature,content-type",
                        },
                    )
                    assert preflight.status_code == 200
                    assert (
                        preflight.headers["access-control-allow-origin"]
                        == "http://127.0.0.1:8912"
                    )

                    username = "gold-user-not-preseeded"
                    headers = {
                        "X-Pay-User": username,
                        "X-Pay-Signature": sign_user(username),
                    }
                    created = await client.post(
                        "/api/pay/order",
                        headers=headers,
                        json={"planId": "SP-01", "channel": "TRON"},
                    )
                    assert created.status_code == 200, created.text

                    orders = await client.get("/api/pay/user/orders", headers=headers)
                    assert orders.status_code == 200, orders.text
                    assert [item["username"] for item in orders.json()["data"]["list"]] == [
                        username
                    ]

        _run(exercise())
