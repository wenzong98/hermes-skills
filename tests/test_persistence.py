from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from persistence import archive_decision, list_trades, load_decisions, record_trade  # noqa: E402


def test_decision_archive_upserts_by_market_date(tmp_path: Path) -> None:
    db = tmp_path / "decisions.db"
    payload = {
        "meta": {"generated_at": "2026-06-05T10:00:00+08:00"},
        "market": {"latest_market_date": "2026-06-05"},
        "decision": {"action_label": "买入", "new_buy_spy_weight_pct": 40, "new_buy_qqq_weight_pct": 60},
    }
    archive_decision(db, payload)
    payload["meta"]["generated_at"] = "2026-06-05T11:00:00+08:00"
    archive_decision(db, payload)
    rows = load_decisions(db)
    assert len(rows) == 1
    assert rows[0]["meta"]["generated_at"] == "2026-06-05T11:00:00+08:00"


def test_real_trade_ledger_records_explicit_execution(tmp_path: Path) -> None:
    db = tmp_path / "trades.db"
    trade_id = record_trade(db, {
        "executed_at": "2026-06-05T09:35:00-04:00",
        "account": "brokerage",
        "ticker": "qqq",
        "side": "BUY",
        "quantity": 2,
        "price": 715.25,
        "fee": 1.0,
        "order_id": "order-1",
    })
    rows = list_trades(db)
    assert trade_id == 1
    assert rows[0]["ticker"] == "QQQ"
    assert rows[0]["quantity"] == 2
    assert rows[0]["price"] == 715.25
