"""Standalone payment polling loop migrated from the Gold feed process."""

from __future__ import annotations

import asyncio
import logging
import os

from .. import payments
from ..storage import ensure_payment_base_schema, open_connection


# Reuse Uvicorn's configured handler so background heartbeats remain visible in
# the standalone process instead of disappearing through an unhandled logger.
LOGGER = logging.getLogger("uvicorn.error")
LOGGER.setLevel(logging.INFO)
DEFAULT_INTERVAL_SECONDS = 30


def live_payment_enabled() -> bool:
    return os.getenv("ULTIMAPAY_LIVE_PAYMENT", "0") == "1"


def _poll_and_settle(db) -> int:
    if not live_payment_enabled():
        return 0
    cfg = payments.read_payment_config(db)
    if not cfg["enabled"]:
        return 0
    pending = db.execute(
        "SELECT order_no, receive_address, amount_usdt, channel"
        " FROM payment_orders WHERE status='pending'"
    ).fetchall()
    grouped = {}
    for order_no, address, amount, channel in pending:
        grouped.setdefault((address or cfg["address"], channel or "TRON"), []).append(
            (order_no, amount)
        )
    settled = 0
    for (address, channel), _items in grouped.items():
        if channel == "SOL":
            incoming = payments.fetch_solana_incoming(db, address)
        elif channel in ("ETH", "BSC"):
            incoming = payments.fetch_evm_incoming(db, address, channel)
        else:
            incoming = payments.fetch_trc20_incoming(db, address)
        for transaction in incoming or []:
            row = db.execute(
                "SELECT order_no FROM payment_orders WHERE status='pending'"
                " AND receive_address=? AND abs(amount_usdt-?)<0.005",
                (address, transaction["amount"]),
            ).fetchone()
            if not row:
                continue
            _confirmed, error = payments.confirm_payment_order(
                db, row[0], transaction["hash"], transaction["amount"], source="poll"
            )
            if not error:
                settled += 1
    return settled


def run_payment_iteration() -> dict[str, int | bool]:
    db = open_connection()
    try:
        ensure_payment_base_schema(db)
        expired = payments.expire_stale_orders(db)
        settled = _poll_and_settle(db)
        payments.retry_payment_callbacks(db)
        result = {
            "heartbeat": True,
            "livePolling": live_payment_enabled(),
            "expired": expired,
            "settled": settled,
        }
        LOGGER.info("payment loop heartbeat %s", result)
        return result
    finally:
        db.close()


async def payment_loop() -> None:
    interval = int(os.getenv("ULTIMAPAY_POLL_SECONDS", str(DEFAULT_INTERVAL_SECONDS)))
    while True:
        try:
            await asyncio.to_thread(run_payment_iteration)
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception("payment loop iteration failed")
        await asyncio.sleep(interval)

