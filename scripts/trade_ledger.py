#!/usr/bin/env python3
"""Manage the independent real-execution ledger."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from persistence import init_trades_db, list_trades, record_trade


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Real trade execution ledger")
    parser.add_argument("--db", default=str(root / "data" / "trades.db"))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    add = sub.add_parser("record")
    add.add_argument("--executed-at", required=True, help="ISO timestamp")
    add.add_argument("--account", required=True)
    add.add_argument("--ticker", required=True)
    add.add_argument("--side", choices=["BUY", "SELL"], required=True)
    add.add_argument("--quantity", type=float, required=True)
    add.add_argument("--price", type=float, required=True)
    add.add_argument("--fee", type=float, default=0)
    add.add_argument("--currency", default="USD")
    add.add_argument("--order-id")
    add.add_argument("--note")
    ls = sub.add_parser("list")
    ls.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()
    path = Path(args.db).expanduser()
    if args.command == "init":
        init_trades_db(path)
        print(json.dumps({"status": "ok", "db": str(path)}))
    elif args.command == "record":
        trade_id = record_trade(path, vars(args))
        print(json.dumps({"status": "ok", "id": trade_id, "db": str(path)}))
    else:
        print(json.dumps(list_trades(path, args.limit), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
