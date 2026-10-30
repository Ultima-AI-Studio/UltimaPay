"""Standalone payment polling loop migrated from the Gold feed process."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime

from .. import payments
from ..payments import callbacks, orders as payment_orders, settlement, verification
from ..storage import ensure_payment_base_schema, open_connection


# Reuse Uvicorn's configured handler so background heartbeats remain visible in
# the standalone process instead of disappearing through an unhandled logger.
LOGGER = logging.getLogger("uvicorn.error")
LOGGER.setLevel(logging.INFO)
DEFAULT_INTERVAL_SECONDS = 30
_VERIFICATION_TRACKERS = {}


class _PollingConfirmationTracker:
    """Count one observation per polling iteration for one order/transaction.

    Provider transaction block/time describe the transfer itself and therefore do
    not advance between polls.  The loop is the observation clock; rejected or
    reorged evidence removes this tracker before a later candidate can restart.
    """

    def __init__(self, rounds=verification.CONFIRMATION_ROUNDS):
        self._tracker = verification.ConfirmationTracker(rounds=rounds)
        self._observation = None

    def prepare(self, block_ref):
        self._observation = (block_ref, time.monotonic_ns())

    def observe(self, block_ref, timestamp=None):
        observation = self._observation or (block_ref, timestamp)
        return self._tracker.observe(*observation)

    @property
    def count(self):
        return self._tracker.count

    def confirmed(self):
        return self._tracker.confirmed()

    def reset(self):
        self._tracker.reset()


def live_payment_enabled() -> bool:
    return os.getenv("ULTIMAPAY_LIVE_PAYMENT", "0") == "1"


def _verification_intent(row):
    asset_key = row["asset_key"] or ""
    asset_parts = asset_key.split(":", 1)
    chain_network = asset_parts[0].split("@", 1)
    return {
        "order_no": row["order_no"],
        "chain": chain_network[0] if chain_network else row["channel"].lower(),
        "network": chain_network[1] if len(chain_network) > 1 else "mainnet",
        "asset_key": asset_key,
        "amount_units": row["amount_units"],
        "receive_address": row["receive_address"],
        "created_at": row["created_at"],
        "expired_at": row["expired_at"],
    }


def _poll_and_settle(db) -> dict[str, int]:
    stats = {"verified": 0, "recorded": 0, "duplicate": 0}
    if not live_payment_enabled():
        return stats
    cfg = payments.read_payment_config(db)
    if not cfg["enabled"]:
        return stats
    pending = db.execute(
        "SELECT order_no, receive_address, channel, merchant, amount_units,"
        " asset_key, created_at, expired_at FROM payment_orders"
        " WHERE status IN ('pending','submitted','confirming')"
        " AND amount_units IS NOT NULL AND asset_key IS NOT NULL"
    ).fetchall()
    grouped = {}
    for row in pending:
        address = row["receive_address"] or cfg["address"]
        grouped.setdefault((address, row["channel"] or "TRON"), []).append(row)
    seen = set()
    active_orders = {row["order_no"] for row in pending}
    for (address, channel), orders in grouped.items():
        if channel == "SOL":
            incoming = payments.fetch_solana_incoming(db, address)
        elif channel in ("ETH", "BSC"):
            incoming = payments.fetch_evm_incoming(db, address, channel)
        else:
            incoming = payments.fetch_trc20_incoming(db, address)
        for transaction in incoming or []:
            tx_hash = transaction.get("transaction_id") or transaction.get(
                "transactionHash"
            ) or transaction.get("signature")
            for row in orders:
                candidate = (row["order_no"], tx_hash)
                if candidate in seen:
                    continue
                seen.add(candidate)
                tracker = _VERIFICATION_TRACKERS.setdefault(
                    candidate, _PollingConfirmationTracker()
                )
                observation_block = transaction.get("_observation_block")
                if observation_block is None:
                    observation_block = transaction.get("block_number")
                if observation_block is None:
                    observation_block = transaction.get("slot")
                tracker.prepare(observation_block)
                evidence, status, reason = verification.verify_receipt(
                    _verification_intent(row),
                    (row["channel"] or "TRON").lower(),
                    transaction,
                    db=db,
                    tracker=tracker,
                )
                if status == "rejected":
                    _VERIFICATION_TRACKERS.pop(candidate, None)
                    continue
                if status == "pending" and not reason.startswith("连续确认不足"):
                    _VERIFICATION_TRACKERS.pop(candidate, None)
                    continue
                if status != "confirmed" or evidence is None:
                    continue
                if evidence.block_time is None:
                    _VERIFICATION_TRACKERS.pop(candidate, None)
                    continue
                expires_at = row["expired_at"]
                if expires_at:
                    expiry_ts = datetime.strptime(
                        expires_at[:19], "%Y-%m-%d %H:%M:%S"
                    ).timestamp()
                    if evidence.block_time > expiry_ts:
                        payment_orders._audit_exception(
                            db,
                            row["order_no"],
                            "链上交易超出订单时间窗",
                            evidence.tx_hash,
                        )
                        _VERIFICATION_TRACKERS.pop(candidate, None)
                        continue
                stats["verified"] += 1
                merchant = db.execute(
                    "SELECT callback_url FROM payment_merchants WHERE code=?",
                    (row["merchant"],),
                ).fetchone()
                callback_url = merchant[0] if merchant else None
                result, _detail = settlement.record_verified_payment(
                    db, row["order_no"], evidence, callback_url=callback_url
                )
                if result in ("recorded", "duplicate"):
                    stats[result] += 1
                    _VERIFICATION_TRACKERS.pop(candidate, None)
    for candidate in list(_VERIFICATION_TRACKERS):
        if candidate[0] in active_orders and candidate not in seen:
            _VERIFICATION_TRACKERS.pop(candidate, None)
    return stats


def run_payment_iteration() -> dict[str, int | bool]:
    db = open_connection()
    try:
        ensure_payment_base_schema(db)
        expired = payments.expire_stale_orders(db)
        settlement_stats = _poll_and_settle(db)
        callback_stats = callbacks.deliver_callbacks(db)
        result = {
            "heartbeat": True,
            "livePolling": live_payment_enabled(),
            "expired": expired,
            **settlement_stats,
            "callbacks": callback_stats,
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

