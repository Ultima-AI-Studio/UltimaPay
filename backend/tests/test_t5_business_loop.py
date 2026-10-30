from __future__ import annotations

import asyncio
import hashlib
import hmac
import sqlite3
import json
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app import payments
from app.payments import callbacks, outbox
from app.payments import reconciliation
from app.services import payment_loop
from app.storage import ensure_payment_base_schema


TRON_CONTRACT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
RECEIVE_ADDRESS = "TFa8L4TXqVPzdEQ5LgNCeEGDVJAbX22dA6"
CREATED_AT = "2026-10-30 10:00:00"
AMOUNT_UNITS = 198_100_000


def _db():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    ensure_payment_base_schema(db)
    db.execute(
        "UPDATE payment_merchants SET callback_url=?, secret=?, enabled=1"
        " WHERE code='ultima-gold'",
        ("https://merchant.invalid/callback", "merchant-secret"),
    )
    db.commit()
    return db


def _seed_order(db, order_no="PAY-T0-001", created_at=CREATED_AT):
    db.execute(
        "INSERT INTO payment_orders(order_no,username,plan_id,plan_name,price_usdt,"
        "discount,amount_usdt,tail,status,created_at,expired_at,channel,merchant,"
        "receive_address,amount_units,asset_key,source)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            order_no,
            "buyer",
            "gold_monthly",
            "Gold monthly",
            198.0,
            1.0,
            198.1,
            0.1,
            "pending",
            created_at,
            "2026-10-30 11:00:00",
            "TRON",
            "ultima-gold",
            RECEIVE_ADDRESS,
            AMOUNT_UNITS,
            f"tron@mainnet:{TRON_CONTRACT}",
            "test",
        ),
    )
    db.commit()


def _tron_event(**changes):
    created_ts = int(datetime.strptime(CREATED_AT, "%Y-%m-%d %H:%M:%S").timestamp())
    event = {
        "transaction_id": "tx-t0-verified",
        "token_info": {"address": TRON_CONTRACT, "symbol": "USDT"},
        "from": "TSender1111111111111111111111111111",
        "to": RECEIVE_ADDRESS,
        "value": str(AMOUNT_UNITS),
        "block_number": 100,
        "block_timestamp": created_ts + 120,
        "confirmed": True,
        "depth": 20,
        "type": "Transfer",
    }
    event.update(changes)
    return event


def _enable_tron_fixture(monkeypatch, event):
    monkeypatch.setattr(payment_loop, "live_payment_enabled", lambda: True)
    monkeypatch.setattr(payments, "fetch_trc20_incoming", lambda db, address: [event])
    payment_loop._VERIFICATION_TRACKERS.clear()


def test_polling_verifies_records_and_enqueues_after_four_observations(monkeypatch):
    db = _db()
    _seed_order(db)
    _enable_tron_fixture(monkeypatch, _tron_event())

    for _ in range(3):
        stats = payment_loop._poll_and_settle(db)
        assert stats["recorded"] == 0
        assert db.execute("SELECT status FROM payment_orders").fetchone()[0] == "pending"

    stats = payment_loop._poll_and_settle(db)

    order = db.execute(
        "SELECT status,source,tx_hash FROM payment_orders WHERE order_no='PAY-T0-001'"
    ).fetchone()
    assert tuple(order) == ("confirmed", "live", "tx-t0-verified")
    assert stats == {"verified": 1, "recorded": 1, "duplicate": 0}
    assert db.execute("SELECT COUNT(*) FROM payment_ledger").fetchone()[0] == 1
    event = db.execute(
        "SELECT event_type,status,payload FROM payment_events WHERE order_no='PAY-T0-001'"
    ).fetchone()
    assert event[0:2] == ("payment.verified", "pending")
    assert "https://merchant.invalid/callback" in event[2]


@pytest.mark.parametrize(
    "event_change,created_at",
    [
        ({"value": str(AMOUNT_UNITS - 1)}, CREATED_AT),
        ({"to": "TWrong11111111111111111111111111111"}, CREATED_AT),
        ({"depth": 18}, CREATED_AT),
        (
            {},
            (datetime.strptime(CREATED_AT, "%Y-%m-%d %H:%M:%S") + timedelta(hours=1)).strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
        ),
    ],
    ids=["amount", "recipient", "depth", "before-order"],
)
def test_polling_rejections_never_confirm(monkeypatch, event_change, created_at):
    db = _db()
    _seed_order(db, created_at=created_at)
    _enable_tron_fixture(monkeypatch, _tron_event(**event_change))

    for _ in range(4):
        payment_loop._poll_and_settle(db)

    assert db.execute("SELECT status FROM payment_orders").fetchone()[0] == "pending"
    assert db.execute("SELECT COUNT(*) FROM payment_ledger").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM payment_events").fetchone()[0] == 0


def test_duplicate_tx_hash_does_not_repeat_ledger_or_event(monkeypatch):
    db = _db()
    _seed_order(db)
    db.execute(
        "INSERT INTO payment_ledger(order_no,tx_hash,chain,network,asset_key,amount_units,created_at)"
        " VALUES(?,?,?,?,?,?,?)",
        (
            "historical-order",
            "tx-t0-verified",
            "tron",
            "mainnet",
            f"tron@mainnet:{TRON_CONTRACT}",
            AMOUNT_UNITS,
            CREATED_AT,
        ),
    )
    db.commit()
    _enable_tron_fixture(monkeypatch, _tron_event())

    for _ in range(3):
        payment_loop._poll_and_settle(db)
    stats = payment_loop._poll_and_settle(db)

    assert stats["duplicate"] == 1
    assert db.execute("SELECT status FROM payment_orders").fetchone()[0] == "pending"
    assert db.execute("SELECT COUNT(*) FROM payment_ledger").fetchone()[0] == 1
    assert db.execute("SELECT COUNT(*) FROM payment_events").fetchone()[0] == 0


def test_legacy_poll_receipt_still_only_marks_submitted():
    db = _db()
    _seed_order(db)

    row, error = payments.confirm_payment_order(
        db, "PAY-T0-001", "unverified-hash", 198.1, source="poll"
    )

    assert error is None
    assert row["status"] == "submitted"
    assert db.execute("SELECT source FROM payment_orders").fetchone()[0] == "test"
    assert db.execute("SELECT COUNT(*) FROM payment_ledger").fetchone()[0] == 0


def test_tron_fetch_preserves_raw_event_units_and_adds_depth(monkeypatch):
    raw = _tron_event(block_number=100, block_timestamp=1_761_799_320_000)

    class Response:
        def __init__(self, payload):
            self.payload = payload

        def read(self):
            return json.dumps(self.payload).encode()

    def fake_open(request, timeout=10):
        if request.full_url.endswith("/wallet/getnowblock"):
            return Response({"block_header": {"raw_data": {"number": 120}}})
        return Response({"data": [raw]})

    monkeypatch.setattr(payments.urllib.request, "urlopen", fake_open)
    db = _db()

    events = payments.fetch_trc20_incoming(db, RECEIVE_ADDRESS)

    assert events[0]["transaction_id"] == "tx-t0-verified"
    assert events[0]["value"] == str(AMOUNT_UNITS)
    assert events[0]["depth"] == 21
    assert events[0]["block_timestamp"] == 1_761_799_320


def test_evm_fetch_returns_parseable_log_with_depth_and_block_time(monkeypatch):
    contract = payments.EVM_USDT["ETH"][0]
    to_topic = "0x" + "0" * 24 + "421565a03a43857bdcab534fb8c8fd1c3158e06a"
    log = {
        "address": contract,
        "topics": [payments.TRANSFER_TOPIC, "0x" + "1" * 64, to_topic],
        "data": hex(AMOUNT_UNITS),
        "transactionHash": "0xevm",
        "blockNumber": "0xf0",
        "logIndex": "0x0",
    }

    def fake_rpc(db, chain, method, params):
        if method == "eth_blockNumber":
            return "0x100"
        if method == "eth_getLogs":
            return [log]
        if method == "eth_getBlockByNumber":
            return {"timestamp": "0x69036d98"}
        raise AssertionError(method)

    monkeypatch.setattr(payments, "evm_rpc_call", fake_rpc)
    db = _db()

    events = payments.fetch_evm_incoming(
        db, "0x421565A03A43857BdcAB534FB8C8fD1C3158E06a", "ETH"
    )

    assert events[0]["transactionHash"] == "0xevm"
    assert events[0]["data"] == hex(AMOUNT_UNITS)
    assert events[0]["depth"] == 17
    assert events[0]["block_time"] == int("69036d98", 16)


def test_solana_fetch_returns_raw_transaction_for_real_parser(monkeypatch):
    raw_tx = {
        "slot": 42,
        "blockTime": 1_761_799_320,
        "meta": {
            "status": {"Ok": None},
            "innerInstructions": [
                {
                    "instructions": [
                        {
                            "parsed": {
                                "type": "transferChecked",
                                "info": {
                                    "mint": payments.USDT_SPL_MINT,
                                    "amount": str(AMOUNT_UNITS),
                                    "source": "source-ata",
                                    "destination": "destination-ata",
                                    "ataFor": "merchant-wallet",
                                    "authority": "sender-wallet",
                                },
                            }
                        }
                    ]
                }
            ],
        },
        "transaction": {"message": {"instructions": []}},
    }

    def fake_rpc(db, method, params):
        if method == "getSignaturesForAddress":
            return [
                {
                    "signature": "sol-signature",
                    "err": None,
                    "confirmationStatus": "finalized",
                }
            ]
        if method == "getTransaction":
            return raw_tx
        raise AssertionError(method)

    monkeypatch.setattr(payments, "solana_rpc", fake_rpc)
    db = _db()

    events = payments.fetch_solana_incoming(db, "merchant-wallet")

    assert events == [
        {
            **raw_tx,
            "signature": "sol-signature",
            "confirmationStatus": "finalized",
        }
    ]


def _seed_callback_event(
    db, *, order_no="PAY-CB-001", merchant="external-shop", callback_url="https://shop.invalid/pay"
):
    if merchant != "ultima-gold":
        db.execute(
            "INSERT INTO payment_merchants(code,name,status,enabled,secret,callback_url,created_at)"
            " VALUES(?,?,?,?,?,?,?)",
            (
                merchant,
                "External shop",
                "active",
                1,
                "external-secret",
                callback_url or "",
                CREATED_AT,
            ),
        )
    db.execute(
        "INSERT INTO payment_orders(order_no,status,merchant,created_at,source) VALUES(?,?,?,?,?)",
        (order_no, "confirmed", merchant, CREATED_AT, "live"),
    )
    event = outbox.enqueue_event(
        db,
        order_no,
        outbox.EVENT_VERIFIED,
        {
            "order_no": order_no,
            "tx_hash": "callback-tx",
            "callback_url": callback_url,
        },
        now=datetime(2026, 10, 30, 10, 0, 0),
    )
    db.commit()
    return event["event_id"]


def test_outbox_callback_uses_merchant_secret_and_delivers_once():
    db = _db()
    db.execute("UPDATE payment_config SET callback_secret='global-secret' WHERE id=1")
    event_id = _seed_callback_event(db)
    posted = []

    def http_post(url, body):
        posted.append((url, json.loads(body)))
        return 204

    stats = callbacks.deliver_callbacks(db, http_post=http_post)

    assert stats == {"delivered": 1, "failed": 0, "dead": 0}
    assert posted[0][0] == "https://shop.invalid/pay"
    body = posted[0][1]
    signature = body.pop("signature")
    assert signature == callbacks.sign_payload(
        "external-secret",
        event_id,
        "PAY-CB-001",
        json.dumps(body, ensure_ascii=False, sort_keys=True),
    )
    assert db.execute("SELECT status FROM payment_events").fetchone()[0] == "delivered"
    assert db.execute("SELECT status,callback_status FROM payment_orders").fetchone()[:] == (
        "confirmed",
        "success",
    )

    assert callbacks.deliver_callbacks(db, http_post=http_post) == {
        "delivered": 0,
        "failed": 0,
        "dead": 0,
    }
    assert len(posted) == 1


def test_outbox_callback_failure_retries_without_rolling_back_order():
    db = _db()
    event_id = _seed_callback_event(db)
    attempts = []

    def fail_post(url, body):
        attempts.append(url)
        raise OSError("temporary network failure")

    first = callbacks.deliver_callbacks(
        db, http_post=fail_post, now=datetime(2026, 10, 30, 10, 0, 0)
    )

    event = db.execute(
        "SELECT status,attempts,next_retry_at,last_error FROM payment_events WHERE event_id=?",
        (event_id,),
    ).fetchone()
    assert first == {"delivered": 0, "failed": 1, "dead": 0}
    assert event[0:2] == ("failed", 1)
    assert event[2] == "2026-10-30 10:00:10"
    assert "temporary network failure" in event[3]
    assert db.execute("SELECT status,callback_status FROM payment_orders").fetchone()[:] == (
        "confirmed",
        "failed",
    )

    second = callbacks.deliver_callbacks(
        db, http_post=lambda url, body: 200, now=datetime(2026, 10, 30, 10, 0, 11)
    )
    assert second == {"delivered": 1, "failed": 0, "dead": 0}
    assert db.execute("SELECT status FROM payment_events").fetchone()[0] == "delivered"


def test_outbox_callback_missing_url_and_retry_cap_are_auditable():
    missing_db = _db()
    _seed_callback_event(missing_db, callback_url="")

    missing = callbacks.deliver_callbacks(
        missing_db, http_post=lambda url, body: 200, now=datetime(2026, 10, 30, 10, 0, 0)
    )

    missing_event = missing_db.execute(
        "SELECT status,last_error FROM payment_events"
    ).fetchone()
    assert missing == {"delivered": 0, "failed": 1, "dead": 0}
    assert missing_event[:] == ("failed", "缺少 callback_url")

    dead_db = _db()
    _seed_callback_event(dead_db)
    dead_db.execute("UPDATE payment_events SET max_attempts=1")
    dead_db.commit()

    dead = callbacks.deliver_callbacks(
        dead_db,
        http_post=lambda url, body: 500,
        now=datetime(2026, 10, 30, 10, 0, 0),
    )

    assert dead == {"delivered": 0, "failed": 0, "dead": 1}
    assert dead_db.execute("SELECT status,attempts FROM payment_events").fetchone()[:] == (
        "dead",
        1,
    )
    assert dead_db.execute("SELECT status FROM payment_orders").fetchone()[0] == "confirmed"


def test_payment_iteration_runs_the_single_outbox_delivery_path(monkeypatch):
    db = _db()
    calls = []
    monkeypatch.setattr(payment_loop, "open_connection", lambda: db)
    monkeypatch.setattr(payment_loop, "live_payment_enabled", lambda: False)

    def deliver(active_db):
        calls.append(active_db)
        return {"delivered": 2, "failed": 1, "dead": 1}

    monkeypatch.setattr(payment_loop.callbacks, "deliver_callbacks", deliver)

    result = payment_loop.run_payment_iteration()

    assert calls == [db]
    assert result["callbacks"] == {"delivered": 2, "failed": 1, "dead": 1}


def test_apply_verify_and_admin_secret_rotation_close_credential_loop():
    with tempfile.TemporaryDirectory(prefix="ultimapay-t2-credentials-") as temp:
        db_path = Path(temp) / "credentials.db"
        os.environ["ULTIMAPAY_DATABASE_URL"] = "sqlite:///" + db_path.as_posix()
        os.environ["ULTIMAPAY_ADMIN_TOKEN"] = "credential-admin-token"

        from app.main import app
        from app.storage import open_connection

        async def exercise():
            async with app.router.lifespan_context(app):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://testserver"
                ) as client:
                    applied = await client.post(
                        "/api/pay/apply",
                        json={
                            "code": "credential-shop",
                            "name": "Credential Shop",
                            "email": "owner@example.invalid",
                            "callbackUrl": "https://shop.invalid/ultimapay",
                            "tronAddress": RECEIVE_ADDRESS,
                        },
                    )
                    assert applied.status_code == 200, applied.text
                    applied_data = applied.json()["data"]
                    api_key = applied_data["apiKey"]
                    assert api_key.startswith("upay_live_")
                    assert applied_data["credentialDelivery"] == "one-time-response"

                    repeated = await client.post(
                        "/api/pay/apply",
                        json={
                            "code": "credential-shop",
                            "name": "Credential Shop",
                            "email": "owner@example.invalid",
                            "callbackUrl": "https://shop.invalid/ultimapay",
                            "tronAddress": RECEIVE_ADDRESS,
                        },
                    )
                    assert repeated.status_code == 400
                    assert api_key not in repeated.text

                    verified = await client.post(
                        "/api/pay/verify", headers={"X-Pay-Key": api_key}, json={}
                    )
                    assert verified.status_code == 200, verified.text
                    assert verified.json()["data"]["status"] == "active"

                    denied = await client.post(
                        "/api/pay/merchants/credential-shop/rotate-secret"
                    )
                    assert denied.status_code in (401, 403)

                    headers = {"Authorization": "Bearer credential-admin-token"}
                    first_rotate = await client.post(
                        "/api/pay/merchants/credential-shop/rotate-secret", headers=headers
                    )
                    assert first_rotate.status_code == 200, first_rotate.text
                    old_secret = first_rotate.json()["data"]["secret"]

                    second_rotate = await client.post(
                        "/api/pay/merchants/credential-shop/rotate-secret", headers=headers
                    )
                    assert second_rotate.status_code == 200
                    new_secret = second_rotate.json()["data"]["secret"]
                    assert new_secret != old_secret

                    listed = await client.get("/api/pay/merchants", headers=headers)
                    item = next(
                        row
                        for row in listed.json()["data"]["merchants"]
                        if row["code"] == "credential-shop"
                    )
                    assert item["callbackUrl"] == "https://shop.invalid/ultimapay"
                    assert item["apiKeyProvisioned"] is True
                    assert item["secretConfigured"] is True
                    assert item["canRotateSecret"] is True
                    assert "apiKey" not in item
                    assert "secret" not in item

                    db = open_connection()
                    try:
                        assert payments.merchant_secret_ok(
                            db, "credential-shop", old_secret
                        ) is False
                        assert payments.merchant_secret_ok(
                            db, "credential-shop", new_secret
                        ) is True
                        audit = "\n".join(
                            row[0]
                            for row in db.execute(
                                "SELECT detail FROM audit_logs ORDER BY id"
                            ).fetchall()
                        )
                        assert api_key not in audit
                        assert old_secret not in audit
                        assert new_secret not in audit
                    finally:
                        db.close()

        asyncio.run(exercise())


def _inbound_signature(secret, body):
    canonical = json.dumps(
        body, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hmac.new(secret.encode(), canonical.encode(), hashlib.sha256).hexdigest()


def test_inbound_callback_selects_secret_by_order_merchant():
    with tempfile.TemporaryDirectory(prefix="ultimapay-t3-secrets-") as temp:
        db_path = Path(temp) / "callback-secrets.db"
        os.environ["ULTIMAPAY_DATABASE_URL"] = "sqlite:///" + db_path.as_posix()

        from app.main import app
        from app.storage import open_connection

        async def exercise():
            async with app.router.lifespan_context(app):
                db = open_connection()
                try:
                    db.execute(
                        "UPDATE payment_config SET callback_secret='platform-global' WHERE id=1"
                    )
                    db.execute(
                        "INSERT INTO payment_merchants(code,name,status,enabled,secret,created_at)"
                        " VALUES(?,?,?,?,?,?)",
                        (
                            "external-callback",
                            "External Callback",
                            "active",
                            1,
                            "merchant-level",
                            CREATED_AT,
                        ),
                    )
                    for order_no, merchant in (
                        ("EXT-VALID", "external-callback"),
                        ("EXT-WRONG", "external-callback"),
                        ("PLATFORM-VALID", "ultima-gold"),
                        ("PLATFORM-WRONG", "ultima-gold"),
                        ("NO-SIGNATURE", "external-callback"),
                    ):
                        db.execute(
                            "INSERT INTO payment_orders(order_no,status,merchant,created_at)"
                            " VALUES(?,?,?,?)",
                            (order_no, "pending", merchant, CREATED_AT),
                        )
                    db.commit()
                finally:
                    db.close()

                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://testserver"
                ) as client:
                    external = {"orderNo": "EXT-VALID", "txHash": "ext-tx"}
                    accepted_external = await client.post(
                        "/api/pay/callback",
                        json=external,
                        headers={
                            "X-Pay-Signature": _inbound_signature(
                                "merchant-level", external
                            )
                        },
                    )
                    assert accepted_external.status_code == 200
                    assert accepted_external.json()["data"]["status"] == "submitted"

                    external_wrong = {"orderNo": "EXT-WRONG", "txHash": "ext-wrong"}
                    rejected_global_for_external = await client.post(
                        "/api/pay/callback",
                        json=external_wrong,
                        headers={
                            "X-Pay-Signature": _inbound_signature(
                                "platform-global", external_wrong
                            )
                        },
                    )
                    assert rejected_global_for_external.status_code == 401

                    platform = {"orderNo": "PLATFORM-VALID", "txHash": "platform-tx"}
                    accepted_platform = await client.post(
                        "/api/pay/callback",
                        json=platform,
                        headers={
                            "X-Pay-Signature": _inbound_signature(
                                "platform-global", platform
                            )
                        },
                    )
                    assert accepted_platform.status_code == 200
                    assert accepted_platform.json()["data"]["status"] == "submitted"

                    platform_wrong = {
                        "orderNo": "PLATFORM-WRONG",
                        "txHash": "platform-wrong",
                    }
                    rejected_merchant_for_platform = await client.post(
                        "/api/pay/callback",
                        json=platform_wrong,
                        headers={
                            "X-Pay-Signature": _inbound_signature(
                                "merchant-level", platform_wrong
                            )
                        },
                    )
                    assert rejected_merchant_for_platform.status_code == 401

                    missing = await client.post(
                        "/api/pay/callback",
                        json={"orderNo": "NO-SIGNATURE", "txHash": "missing"},
                    )
                    assert missing.status_code == 401

        asyncio.run(exercise())


def test_end_to_end_onboarding_chain_verification_callback_and_reconciliation(monkeypatch):
    with tempfile.TemporaryDirectory(prefix="ultimapay-t4-e2e-") as temp:
        db_path = Path(temp) / "business-loop.db"
        os.environ["ULTIMAPAY_DATABASE_URL"] = "sqlite:///" + db_path.as_posix()
        os.environ["ULTIMAPAY_ADMIN_TOKEN"] = "e2e-admin-token"
        os.environ["ULTIMAPAY_LIVE_PAYMENT"] = "0"

        from app.main import app
        from app.storage import open_connection

        async def onboard_and_order():
            async with app.router.lifespan_context(app):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://testserver"
                ) as client:
                    applied = await client.post(
                        "/api/pay/apply",
                        json={
                            "code": "e2e-shop",
                            "name": "E2E Shop",
                            "email": "e2e@example.invalid",
                            "callbackUrl": "https://e2e.invalid/payment-callback",
                            "tronAddress": RECEIVE_ADDRESS,
                        },
                    )
                    api_key = applied.json()["data"]["apiKey"]
                    verified = await client.post(
                        "/api/pay/verify", headers={"X-Pay-Key": api_key}, json={}
                    )
                    assert verified.status_code == 200

                    rotated = await client.post(
                        "/api/pay/merchants/e2e-shop/rotate-secret",
                        headers={"Authorization": "Bearer e2e-admin-token"},
                    )
                    merchant_secret = rotated.json()["data"]["secret"]

                    created = await client.post(
                        "/api/pay/merchant/intents",
                        headers={"X-Pay-Key": api_key},
                        json={
                            "merchant": "e2e-shop",
                            "externalOrderNo": "SHOP-ORDER-1",
                            "externalUserRef": "customer-1",
                            "amountUnits": 25_000_000,
                            "chain": "TRON",
                            "idempotencyKey": "shop-order-1",
                        },
                    )
                    assert created.status_code == 200, created.text
                    return created.json()["data"], merchant_secret

        order_data, merchant_secret = asyncio.run(onboard_and_order())
        db = open_connection()
        try:
            order = db.execute(
                "SELECT * FROM payment_orders WHERE order_no=?",
                (order_data["orderNo"],),
            ).fetchone()
            created_ts = int(
                datetime.strptime(order["created_at"], "%Y-%m-%d %H:%M:%S").timestamp()
            )
            provider_event = {
                "transaction_id": "e2e-chain-tx",
                "token_info": {"address": TRON_CONTRACT, "symbol": "USDT"},
                "from": "TE2ESender111111111111111111111111",
                "to": order["receive_address"],
                "value": str(order["amount_units"]),
                "block_number": 500,
                "block_timestamp": created_ts + 120,
                "confirmed": True,
                "depth": 25,
                "type": "Transfer",
            }
            monkeypatch.setattr(payment_loop, "live_payment_enabled", lambda: True)
            monkeypatch.setattr(
                payments, "fetch_trc20_incoming", lambda active_db, address: [provider_event]
            )
            payment_loop._VERIFICATION_TRACKERS.clear()

            for _ in range(4):
                payment_loop._poll_and_settle(db)

            confirmed = db.execute(
                "SELECT status,source FROM payment_orders WHERE order_no=?",
                (order_data["orderNo"],),
            ).fetchone()
            assert confirmed[:] == ("confirmed", "live")

            posted = []
            delivered = callbacks.deliver_callbacks(
                db, http_post=lambda url, body: posted.append((url, json.loads(body))) or 200
            )
            assert delivered == {"delivered": 1, "failed": 0, "dead": 0}
            assert posted[0][0] == "https://e2e.invalid/payment-callback"
            callback_body = posted[0][1]
            callback_signature = callback_body.pop("signature")
            assert callback_signature == callbacks.sign_payload(
                merchant_secret,
                callback_body["event_id"],
                order_data["orderNo"],
                json.dumps(callback_body, ensure_ascii=False, sort_keys=True),
            )

            event_id = callback_body["event_id"]
            db.execute(
                "INSERT INTO fulfillment_log(event_id,order_no,status,created_at,updated_at)"
                " VALUES(?,?,?,?,?)",
                (event_id, order_data["orderNo"], "applied", CREATED_AT, CREATED_AT),
            )
            db.commit()

            report = reconciliation.run_reconciliation(db)
            assert report["categories"] == {"matched": 1}
            assert report["scope"]["matched_live"] == 1
            assert report["scope"]["abnormal_live"] == 0
        finally:
            db.close()
