import sqlite3


def _database():
    from app.storage import ensure_payment_base_schema

    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    ensure_payment_base_schema(db)
    return db


def test_channels_match_frozen_configuration():
    from app.payments import _PAYMENT_CHANNELS

    assert _PAYMENT_CHANNELS == {
        "TRON": {
            "name": "TRON",
            "network": "TRON TRC-20",
            "currency": "USDT",
            "address": "TFa8L4TXqVPzdEQ5LgNCeEGDVJAbX22dA6",
            "enabled": True,
            "rpcUrl": "https://api.trongrid.io",
            "rpcBackup": "",
        },
        "ETH": {
            "name": "Ethereum",
            "network": "Ethereum ERC-20",
            "currency": "USDT",
            "address": "0x421565A03A43857BdcAB534FB8C8fD1C3158E06a",
            "enabled": True,
            "rpcUrl": "https://ethereum.publicnode.com",
            "rpcBackup": "https://ethereum-rpc.publicnode.com,https://rpc.ankr.com/eth,https://eth.llamarpc.com",
        },
        "BSC": {
            "name": "BSC",
            "network": "BSC BEP-20",
            "currency": "USDT",
            "address": "0x421565A03A43857BdcAB534FB8C8fD1C3158E06a",
            "enabled": True,
            "rpcUrl": "https://bsc-dataseed.bnbchain.org",
            "rpcBackup": "https://bsc-dataseed.binance.org,https://rpc.ankr.com/bsc",
        },
        "SOL": {
            "name": "Solana",
            "network": "Solana SPL · USDT",
            "currency": "USDT",
            "address": "",
            "enabled": False,
            "rpcUrl": "https://solana-rpc.publicnode.com",
            "rpcBackup": "https://api.mainnet-beta.solana.com,https://rpc.ankr.com/solana",
        },
    }


def test_amount_lock_and_tamper_rejection_preserve_business_rules():
    from app.compat import LEGACY_PRODUCT_REF
    from app.payments import lock_payment_intent, verify_payment_intent

    db = _database()
    plan = {
        "id": "SP-01",
        "name": "月卡",
        "product": LEGACY_PRODUCT_REF,
        "priceUsdt": 198,
        "discount": 1.0,
        "enabled": True,
        "durationDays": 30,
        "giftDays": 0,
    }
    intent, error = lock_payment_intent(db, plan, channel="TRON")
    assert error is None
    assert 198_100_000 <= intent["amount_units"] <= 198_900_000
    assert intent["amount_units"] == intent["price_units"] + intent["tail_units"]
    assert verify_payment_intent(intent, plan)[0] is True

    intent["amount_units"] += 1
    ok, reason = verify_payment_intent(intent, plan)
    assert ok is False
    assert "金额被篡改" in reason


def test_idempotency_and_receipt_never_directly_confirm():
    from app.compat import LEGACY_PRODUCT_REF
    from app.payments.orders import create_payment_intent, submit_receipt

    db = _database()
    principal = {"role": "user", "username": "smoke_user"}
    first, error = create_payment_intent(
        db,
        principal,
        LEGACY_PRODUCT_REF,
        "ETH",
        "idem-smoke-1",
        plan_id="SP-01",
    )
    assert error is None
    again, error = create_payment_intent(
        db,
        principal,
        LEGACY_PRODUCT_REF,
        "ETH",
        "idem-smoke-1",
        plan_id="SP-01",
    )
    assert error is None
    assert again["order_no"] == first["order_no"]
    assert db.execute("SELECT COUNT(*) FROM payment_orders").fetchone()[0] == 1

    submitted, error = submit_receipt(
        db,
        first["order_no"],
        "0xreceipt",
        amount_units=first["amount_units"],
    )
    assert error is None
    assert submitted["status"] == "submitted"
    assert submitted["source"] == "test"

