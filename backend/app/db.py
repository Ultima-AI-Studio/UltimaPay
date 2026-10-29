"""Authoritative standalone SQLite connection and migration boundary."""

from __future__ import annotations

import importlib
import sqlite3
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

from . import config
from .storage import ensure_payment_base_schema


SCHEMA_VERSION = 9
MIGRATION_MODULES = ("migrations.0009_payment_pay_sessions",)


class MigrationError(Exception):
    def __init__(self, version: int, name: str, cause: Exception):
        super().__init__(f"migration {version} ({name}) failed: {cause}")
        self.version = version
        self.name = name
        self.cause = cause


def database_path() -> Path:
    parsed = urlparse(config.database_url())
    if parsed.scheme != "sqlite":
        raise ValueError("UltimaPay currently supports only sqlite database URLs")
    raw = unquote(parsed.path)
    if parsed.netloc:
        raw = f"//{parsed.netloc}{raw}"
    if raw.startswith("/") and len(raw) > 2 and raw[2] == ":":
        raw = raw[1:]
    return Path(raw).resolve()


def connection() -> sqlite3.Connection:
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    result = sqlite3.connect(path, timeout=10, check_same_thread=False)
    result.row_factory = sqlite3.Row
    return result


def _ensure_version_ledger(db: sqlite3.Connection) -> None:
    db.execute(
        """CREATE TABLE IF NOT EXISTS schema_version(
        version INTEGER PRIMARY KEY, name TEXT, applied_at TEXT)"""
    )


def migrate(db: sqlite3.Connection | None = None) -> list[int]:
    owns_connection = db is None
    active = connection() if db is None else db
    applied: list[int] = []
    try:
        ensure_payment_base_schema(active)
        _ensure_version_ledger(active)
        done = {
            row[0] for row in active.execute("SELECT version FROM schema_version").fetchall()
        }
        for module_name in MIGRATION_MODULES:
            migration = importlib.import_module(module_name)
            if migration.VERSION > SCHEMA_VERSION or migration.VERSION in done:
                continue
            try:
                active.execute("BEGIN")
                migration.upgrade(active)
                active.execute(
                    "INSERT INTO schema_version(version,name,applied_at) VALUES(?,?,?)",
                    (
                        migration.VERSION,
                        migration.NAME,
                        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    ),
                )
                active.commit()
                applied.append(migration.VERSION)
            except Exception as exc:
                active.rollback()
                raise MigrationError(migration.VERSION, migration.NAME, exc) from exc
        return applied
    finally:
        if owns_connection:
            active.close()
