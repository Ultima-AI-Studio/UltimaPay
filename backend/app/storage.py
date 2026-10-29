"""Provisional SQLite bootstrap used until versioned migrations take ownership."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from .catalog import SUBSCRIPTION_PLANS


def database_path() -> Path:
    from .db import database_path as authoritative_database_path

    return authoritative_database_path()


def open_connection() -> sqlite3.Connection:
    from .db import connection

    return connection()


def ensure_payment_base_schema(db: sqlite3.Connection) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS users(
            user_id TEXT PRIMARY KEY, username TEXT UNIQUE, email TEXT,
            status TEXT DEFAULT 'active', created_at TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS subscription_plans(
            id TEXT PRIMARY KEY, name TEXT NOT NULL, product TEXT,
            price_usdt REAL, price_cny REAL, duration_days INTEGER DEFAULT 30,
            gift_days INTEGER DEFAULT 0, discount REAL DEFAULT 1.0,
            enabled INTEGER DEFAULT 1, remark TEXT DEFAULT '',
            created_at TEXT DEFAULT '', updated_at TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS payment_orders(
            id INTEGER PRIMARY KEY AUTOINCREMENT, order_no TEXT UNIQUE,
            username TEXT, plan_id TEXT, plan_name TEXT,
            price_usdt REAL, discount REAL, amount_usdt REAL, tail REAL,
            status TEXT DEFAULT 'pending', tx_hash TEXT DEFAULT '',
            created_at TEXT, paid_at TEXT, expired_at TEXT,
            channel TEXT DEFAULT 'TRON', merchant TEXT DEFAULT '',
            receive_address TEXT DEFAULT '', callback_status TEXT DEFAULT '',
            callback_at TEXT DEFAULT '', callback_attempts INTEGER DEFAULT 0,
            source TEXT DEFAULT 'test', idempotency_key TEXT,
            amount_units INTEGER, asset_key TEXT, allocation_key TEXT,
            confirmed_at TEXT);
        CREATE TABLE IF NOT EXISTS payment_config(
            id INTEGER PRIMARY KEY CHECK(id=1), enabled INTEGER DEFAULT 1,
            address TEXT, ttl_minutes INTEGER DEFAULT 30,
            callback_secret TEXT DEFAULT '', updated_at TEXT);
        CREATE TABLE IF NOT EXISTS payment_channel_config(
            channel TEXT PRIMARY KEY, address TEXT DEFAULT '', enabled INTEGER DEFAULT 1,
            rpc_url TEXT DEFAULT '', rpc_backup TEXT DEFAULT '', updated_at TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS payment_merchants(
            id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL, email TEXT DEFAULT '', api_key TEXT DEFAULT '',
            status TEXT DEFAULT 'applied', apply_at TEXT DEFAULT '', last_seen_at TEXT DEFAULT '',
            callback_url TEXT DEFAULT '', secret TEXT DEFAULT '', enabled INTEGER DEFAULT 1,
            remark TEXT DEFAULT '', created_at TEXT DEFAULT '', hidden INTEGER DEFAULT 0,
            tron_address TEXT DEFAULT '', eth_address TEXT DEFAULT '',
            bsc_address TEXT DEFAULT '', sol_address TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS payment_tickets(
            id INTEGER PRIMARY KEY AUTOINCREMENT, ticket_no TEXT DEFAULT '',
            merchant_code TEXT DEFAULT '', merchant_name TEXT DEFAULT '',
            contact_email TEXT DEFAULT '', type TEXT DEFAULT 'change_address',
            chain TEXT DEFAULT '', old_address TEXT DEFAULT '', new_address TEXT DEFAULT '',
            reason TEXT DEFAULT '', status TEXT DEFAULT 'open', remark TEXT DEFAULT '',
            created_at TEXT DEFAULT '', updated_at TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS payment_order_exceptions(
            id INTEGER PRIMARY KEY AUTOINCREMENT, order_no TEXT, reason TEXT,
            tx_hash TEXT, created_at TEXT);
        CREATE TABLE IF NOT EXISTS payment_events(
            id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE,
            order_no TEXT NOT NULL, event_type TEXT NOT NULL, payload TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
            max_attempts INTEGER NOT NULL DEFAULT 8, next_retry_at TEXT, last_error TEXT,
            created_at TEXT NOT NULL, delivered_at TEXT, UNIQUE(order_no, event_type));
        CREATE TABLE IF NOT EXISTS payment_ledger(
            id INTEGER PRIMARY KEY AUTOINCREMENT, order_no TEXT NOT NULL,
            tx_hash TEXT NOT NULL UNIQUE, chain TEXT NOT NULL, network TEXT NOT NULL,
            asset_key TEXT NOT NULL, amount_units INTEGER NOT NULL, block INTEGER,
            block_time INTEGER, evidence_id TEXT, event_id TEXT, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS fulfillment_log(
            id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE,
            order_no TEXT NOT NULL, user_id TEXT, product TEXT, plan_id TEXT,
            total_days INTEGER, expires_at TEXT, status TEXT NOT NULL DEFAULT 'applied',
            error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS audit_logs(
            id INTEGER PRIMARY KEY AUTOINCREMENT, actor TEXT, role TEXT,
            action TEXT, resource TEXT, detail TEXT, created_at TEXT);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_pay_idem ON payment_orders(idempotency_key)
            WHERE idempotency_key IS NOT NULL AND idempotency_key != '';
        CREATE UNIQUE INDEX IF NOT EXISTS idx_pay_alloc
            ON payment_orders(asset_key, receive_address, amount_units)
            WHERE status IN ('pending','submitted','confirming');
        CREATE INDEX IF NOT EXISTS idx_payment_status ON payment_orders(status);
        CREATE INDEX IF NOT EXISTS idx_payment_merchant ON payment_orders(merchant);
        CREATE INDEX IF NOT EXISTS idx_events_status ON payment_events(status, next_retry_at);
        CREATE INDEX IF NOT EXISTS idx_ledger_order ON payment_ledger(order_no);
        CREATE INDEX IF NOT EXISTS idx_fulfillment_order ON fulfillment_log(order_no);
        """
    )
    db.execute(
        "INSERT OR IGNORE INTO payment_config(id,enabled,address,ttl_minutes,callback_secret,updated_at)"
        " VALUES(1,1,?,30,'',?)",
        ("TFa8L4TXqVPzdEQ5LgNCeEGDVJAbX22dA6", now),
    )
    channels = (
        ("TRON", "TFa8L4TXqVPzdEQ5LgNCeEGDVJAbX22dA6", 1, "https://api.trongrid.io", ""),
        ("ETH", "0x421565A03A43857BdcAB534FB8C8fD1C3158E06a", 1,
         "https://ethereum.publicnode.com",
         "https://ethereum-rpc.publicnode.com,https://rpc.ankr.com/eth,https://eth.llamarpc.com"),
        ("BSC", "0x421565A03A43857BdcAB534FB8C8fD1C3158E06a", 1,
         "https://bsc-dataseed.bnbchain.org",
         "https://bsc-dataseed.binance.org,https://rpc.ankr.com/bsc"),
        ("SOL", "", 0, "https://solana-rpc.publicnode.com",
         "https://api.mainnet-beta.solana.com,https://rpc.ankr.com/solana"),
    )
    db.executemany(
        "INSERT OR IGNORE INTO payment_channel_config"
        "(channel,address,enabled,rpc_url,rpc_backup,updated_at) VALUES(?,?,?,?,?,?)",
        [(*row, now) for row in channels],
    )
    for plan in SUBSCRIPTION_PLANS:
        db.execute(
            "INSERT OR IGNORE INTO subscription_plans"
            "(id,name,product,price_usdt,price_cny,duration_days,gift_days,discount,enabled,remark,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (plan["id"], plan["name"], plan["product"], plan["priceUsdt"], plan["priceCny"],
             plan["durationDays"], plan["giftDays"], plan["discount"],
             1 if plan["enabled"] else 0, plan["remark"], plan["createdAt"], ""),
        )
    db.execute(
        "INSERT OR IGNORE INTO users(user_id,username,email,status,created_at) VALUES(?,?,?,?,?)",
        ("PAY-SMOKE-001", "smoke_user", "smoke@example.invalid", "active", now),
    )
    merchants = (
        ("ultima-gold", "Ultima Gold 官方站点", "ops@ultima.ai", "upay_live_ultimagold00000000000001"),
        ("ultima-agent", "Ultima Agent 官方站点", "ops@ultima.ai", "upay_live_ultimaagent0000000000002"),
        ("console", "Ultima AI Console 后台", "ops@ultima.ai", "upay_live_console0000000000000003"),
    )
    db.executemany(
        "INSERT OR IGNORE INTO payment_merchants"
        "(code,name,email,api_key,status,enabled,created_at) VALUES(?,?,?,?,'active',1,?)",
        [(*row, now) for row in merchants],
    )
    db.commit()

