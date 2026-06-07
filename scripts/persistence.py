#!/usr/bin/env python3
"""SQLite persistence for daily decision snapshots and real executions."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List


def init_decisions_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            create table if not exists decision_snapshots (
                market_date text primary key,
                generated_at text not null,
                action text not null,
                ticker text not null,
                payload_json text not null,
                created_at text not null default current_timestamp
            )
            """
        )


def archive_decision(path: Path, payload: Dict[str, Any]) -> str:
    init_decisions_db(path)
    market_date = str((payload.get("market") or {}).get("latest_market_date") or "")
    if not market_date:
        raise ValueError("advice payload has no market.latest_market_date")
    generated_at = str((payload.get("meta") or {}).get("generated_at") or "")
    decision = payload.get("decision") or {}
    action = str(decision.get("action_label") or "HOLD")
    ticker = "QQQ" if float(decision.get("new_buy_qqq_weight_pct") or 0) >= float(decision.get("new_buy_spy_weight_pct") or 0) else "SPY"
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            insert into decision_snapshots(market_date, generated_at, action, ticker, payload_json)
            values(?, ?, ?, ?, ?)
            on conflict(market_date) do update set
                generated_at=excluded.generated_at,
                action=excluded.action,
                ticker=excluded.ticker,
                payload_json=excluded.payload_json
            """,
            (market_date, generated_at, action, ticker, json.dumps(payload, ensure_ascii=False)),
        )
    return market_date


def load_decisions(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    with sqlite3.connect(path) as conn:
        rows = conn.execute(
            "select payload_json from decision_snapshots order by market_date"
        ).fetchall()
    return [json.loads(row[0]) for row in rows]


def init_trades_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            create table if not exists executions (
                id integer primary key autoincrement,
                executed_at text not null,
                account text not null,
                ticker text not null,
                side text not null check(side in ('BUY', 'SELL')),
                quantity real not null check(quantity > 0),
                price real not null check(price > 0),
                fee real not null default 0 check(fee >= 0),
                currency text not null default 'USD',
                order_id text,
                note text,
                created_at text not null default current_timestamp,
                unique(account, order_id)
            )
            """
        )
        conn.execute("create index if not exists idx_executions_time on executions(executed_at)")
        conn.execute("create index if not exists idx_executions_ticker on executions(ticker)")


def record_trade(path: Path, trade: Dict[str, Any]) -> int:
    init_trades_db(path)
    with sqlite3.connect(path) as conn:
        cur = conn.execute(
            """
            insert into executions(
                executed_at, account, ticker, side, quantity, price, fee,
                currency, order_id, note
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trade["executed_at"],
                trade["account"],
                str(trade["ticker"]).upper(),
                str(trade["side"]).upper(),
                float(trade["quantity"]),
                float(trade["price"]),
                float(trade.get("fee") or 0),
                str(trade.get("currency") or "USD").upper(),
                trade.get("order_id"),
                trade.get("note"),
            ),
        )
        return int(cur.lastrowid)


def list_trades(path: Path, limit: int = 100) -> List[Dict[str, Any]]:
    init_trades_db(path)
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "select * from executions order by executed_at desc, id desc limit ?",
            (int(limit),),
        ).fetchall()
    return [dict(row) for row in rows]
