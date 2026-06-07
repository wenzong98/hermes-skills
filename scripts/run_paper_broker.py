#!/usr/bin/env python3
"""
Drive the paper broker from an execution_plan.json.

The execution_plan.json is produced by current_market_advice.py or
hand-written. The paper broker:

1. Reads the plan
2. Looks up the latest market price (from the backtest dataset
   unless --manual-px is given)
3. Submits the implied orders
4. Writes a ledger + a one-line summary to stdout

This is the local-only verification path for the live-trading
half of the system. It is designed to run in CI without API keys.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
_SCRIPTS = _ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from paper_broker import (  # noqa: E402
    AccountState,
    LocalPaperBroker,
    Order,
    make_order_id,
)


def _q(amount: float, price: float) -> float:
    """Convert a USD amount to whole-share quantity."""
    if price <= 0:
        return 0.0
    return float(math.floor(amount / price))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--execution-plan",
        type=str,
        required=True,
        help="Path to execution_plan.json",
    )
    parser.add_argument(
        "--manual-spy-px",
        type=float,
        default=None,
        help="Override SPY market price (otherwise read from the plan or dataset)",
    )
    parser.add_argument(
        "--manual-qqq-px",
        type=float,
        default=None,
    )
    parser.add_argument(
        "--initial-cash",
        type=float,
        default=100_000.0,
    )
    parser.add_argument(
        "--ledger-dir",
        type=str,
        default=str(_ROOT / "references" / "validation" / "paper_broker"),
    )
    args = parser.parse_args()

    plan_path = Path(args.execution_plan)
    if not plan_path.exists():
        raise SystemExit(f"execution plan not found: {plan_path}")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))

    spy_px = args.manual_spy_px or float(plan.get("spy_market_price") or 0.0)
    qqq_px = args.manual_qqq_px or float(plan.get("qqq_market_price") or 0.0)
    if spy_px <= 0 or qqq_px <= 0:
        # Fall back to the latest dataset close so the broker can
        # still run end-to-end.
        from backtest_us_etf import prepare_dataset  # noqa: E402

        df = prepare_dataset(
            "2020-01-02",
            datetime.utcnow().strftime("%Y-%m-%d"),
            price_source="yahoo_chart_adjusted",
            cache_dir=str(_ROOT / "references" / "data_cache"),
            cape_vintage_path=str(_ROOT / "references" / "cape_vintage.csv"),
        )
        spy_px = spy_px or float(df["spy_close"].iloc[-1])
        qqq_px = qqq_px or float(df["qqq_close"].iloc[-1])

    broker = LocalPaperBroker(initial_cash=args.initial_cash, ledger_dir=Path(args.ledger_dir))
    broker.update_market_prices(spy=spy_px, qqq=qqq_px)

    submitted: list[Order] = []
    for symbol, side, amount in [
        ("SPY", plan.get("spy_action", "buy").lower(), float(plan.get("spy_amount_usd", 0.0))),
        ("QQQ", plan.get("qqq_action", "buy").lower(), float(plan.get("qqq_amount_usd", 0.0))),
    ]:
        if amount <= 0:
            continue
        px = spy_px if symbol == "SPY" else qqq_px
        qty = _q(amount, px)
        if qty <= 0:
            print(f"[paper] skipping {symbol}: amount {amount:.2f} < price {px:.2f}")
            continue
        order = Order(
            order_id=make_order_id(symbol.lower()),
            symbol=symbol,
            side=side,
            qty=qty,
            order_type=plan.get("execution_mode", "market"),
        )
        result = broker.submit_order(order)
        submitted.append(result)
        print(
            f"[paper] {result.status:>8} {result.side:>4} {result.qty:>5.0f} "
            f"{result.symbol} @ {px:.2f} = ${result.qty * px:,.2f}"
        )

    acct = broker.get_account()
    print()
    print(
        f"[paper] account: cash=${acct.cash_usd:,.2f}  "
        f"SPY={acct.spy_shares:.0f}  QQQ={acct.qqq_shares:.0f}  "
        f"total=${acct.total_value():,.2f}"
    )
    out_dir = Path(args.ledger_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    summary_path = out_dir / "summary.json"
    summary_path.write_text(
        json.dumps(
            {
                "as_of": datetime.utcnow().isoformat(),
                "plan": plan,
                "market_prices": {"spy": spy_px, "qqq": qqq_px},
                "orders": [
                    {
                        "order_id": o.order_id,
                        "symbol": o.symbol,
                        "side": o.side,
                        "qty": o.qty,
                        "status": o.status,
                        "filled_avg_price": o.filled_avg_price,
                        "notes": o.notes,
                    }
                    for o in submitted
                ],
                "account": {
                    "cash_usd": acct.cash_usd,
                    "spy_shares": acct.spy_shares,
                    "qqq_shares": acct.qqq_shares,
                    "total_value": acct.total_value(),
                },
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"[paper] summary written to {summary_path}")


if __name__ == "__main__":
    main()
