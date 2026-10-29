"""M009: short-lived, order-scoped browser payment sessions."""

VERSION = 9
NAME = "payment_pay_sessions"


def upgrade(db) -> None:
    db.execute(
        """CREATE TABLE IF NOT EXISTS payment_pay_sessions(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_token TEXT NOT NULL UNIQUE,
        order_no TEXT NOT NULL,
        merchant TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        created_at TEXT NOT NULL)"""
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_pay_session_order"
        " ON payment_pay_sessions(order_no)"
    )
