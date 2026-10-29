"""Read-only reconciliation CLI owned by the standalone payment service."""

from __future__ import annotations

import argparse
from pathlib import Path

from app import db as database
from app.payments.reconciliation import render_report, run_reconciliation


def main() -> None:
    parser = argparse.ArgumentParser(description="UltimaPay read-only reconciliation")
    parser.add_argument("--out", default=None, help="optional report output path")
    args = parser.parse_args()

    connection = database.connection()
    connection.execute("PRAGMA query_only=ON")
    try:
        report = run_reconciliation(connection)
    finally:
        connection.close()

    text = render_report(report)
    if args.out:
        output = Path(args.out).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")
        print(f"report written to {output}")
    else:
        print(text)


if __name__ == "__main__":
    main()
