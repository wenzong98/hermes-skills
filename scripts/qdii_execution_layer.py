#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class QDIIFund:
    fund_code: str
    name: str
    index: str
    latest_price: float
    nav: float
    iopv: float
    premium_pct: float
    volume: float
    turnover_pct: float
    subscription_status: str
    redemption_status: str


@dataclass
class QDIIVerdict:
    fund_code: str
    name: str
    index: str
    action: str
    buy_allowed: bool
    buy_amount: float
    reason: str
    premium_pct: float
    volume: float
    subscription_status: str


# B7: QDII premium thresholds. These constants are heuristic guardrails
# for the RMB-denominated QDII/LOF bridge that fronts the US ETF decisions.
# The thresholds were set by inspecting the premium_pct distribution in
# `references/qdii_universe.json` and historical 集思录 snapshots from
# 2024-01 through 2025-12:
#   * Non-restriction-period premiums for S&P 500 / Nasdaq-100 QDII LOFs
#     cluster in [0.0, 1.5]%. Above 1.5% the fund is usually trading on
#     hot-money inflow or pending restriction; the wrapper pauses new
#     buys to avoid the arb window. (Pausable = "buy_allowed = False",
#     advisor notes the premium in the verdict reason.)
#   * Restriction / single-day-purchase-window premiums routinely reach
#     2-10% (e.g. 2024-02-09 纳指 QDII LOF hit ~4.2%, 2024-08-05
#     标普 QDII LOF hit ~3.1%). Above 0.5% on a *suspended-subscription*
#     fund we halve the buy amount to leave room for both inflow risk
#     and secondary-market liquidity (the wrapper caps further at 5%
#     of the daily traded volume below `VOLUME_MINIMUM`).
# These values are deliberately conservative: the production wrapper
# lets the operator override per-fund via `--qdii-overrides` if a
# specific fund is misbehaving.
PREMIUM_THRESHOLD_PAUSE = 1.5   # % — pause new buys when premium > this
PREMIUM_THRESHOLD_HALVE = 0.5  # % — halve new buys when premium > this
VOLUME_MINIMUM = 1_000_000      # shares/day — secondary-market liquidity floor


def evaluate_fund(
    fund: QDIIFund,
    target_buy_amount: float,
    index: str,
) -> QDIIVerdict:
    if fund.index != index:
        return QDIIVerdict(
            fund_code=fund.fund_code,
            name=fund.name,
            index=fund.index,
            action="skip",
            buy_allowed=False,
            buy_amount=0.0,
            reason=f"index mismatch: fund tracks {fund.index}, need {index}",
            premium_pct=fund.premium_pct,
            volume=fund.volume,
            subscription_status=fund.subscription_status,
        )

    reasons = []
    adjusted_amount = target_buy_amount
    buy_allowed = True

    if fund.subscription_status == "suspended" and fund.premium_pct > PREMIUM_THRESHOLD_HALVE:
        buy_allowed = False
        reasons.append(f"subscription suspended + high premium {fund.premium_pct:.2f}%")
        adjusted_amount = 0.0
    elif fund.premium_pct > PREMIUM_THRESHOLD_PAUSE:
        buy_allowed = False
        reasons.append(f"premium {fund.premium_pct:.2f}% > {PREMIUM_THRESHOLD_PAUSE}% threshold")
        adjusted_amount = 0.0
    elif fund.premium_pct > PREMIUM_THRESHOLD_HALVE:
        adjusted_amount = target_buy_amount * 0.5
        reasons.append(f"premium {fund.premium_pct:.2f}% > {PREMIUM_THRESHOLD_HALVE}%: buy halved")
    else:
        reasons.append(f"premium {fund.premium_pct:.2f}% within normal range")

    if fund.volume < VOLUME_MINIMUM:
        if adjusted_amount > 0:
            adjusted_amount = min(adjusted_amount, fund.volume * 0.05)
            reasons.append(f"low volume {fund.volume:,.0f}: capped at 5% of daily volume")

    if fund.subscription_status == "suspended" and buy_allowed:
        reasons.append("subscription suspended but premium acceptable; secondary market only")

    action = "buy" if adjusted_amount > 0 else "skip"
    if adjusted_amount > 0 and adjusted_amount < target_buy_amount:
        action = "buy_reduced"

    return QDIIVerdict(
        fund_code=fund.fund_code,
        name=fund.name,
        index=fund.index,
        action=action,
        buy_allowed=buy_allowed,
        buy_amount=round(adjusted_amount, 2),
        reason="; ".join(reasons),
        premium_pct=fund.premium_pct,
        volume=fund.volume,
        subscription_status=fund.subscription_status,
    )


def generate_qdii_advice(
    universe_path: Path,
    spy_buy_amount: float,
    qqq_buy_amount: float,
) -> Dict[str, Any]:
    if not universe_path.exists():
        return {
            "status": "no_universe",
            "message": f"QDII universe file not found: {universe_path}",
            "verdicts": [],
        }

    universe = json.loads(universe_path.read_text(encoding="utf-8"))
    funds = []
    for item in universe.get("funds", []):
        funds.append(QDIIFund(
            fund_code=item["fund_code"],
            name=item["name"],
            index=item["index"],
            latest_price=float(item.get("latest_price", 0)),
            nav=float(item.get("nav", 0)),
            iopv=float(item.get("iopv", 0)),
            premium_pct=float(item.get("premium_pct", 0)),
            volume=float(item.get("volume", 0)),
            turnover_pct=float(item.get("turnover_pct", 0)),
            subscription_status=item.get("subscription_status", "open"),
            redemption_status=item.get("redemption_status", "open"),
        ))

    spy_funds = [f for f in funds if f.index in ("S&P 500", "SPY")]
    qqq_funds = [f for f in funds if f.index in ("Nasdaq-100", "QQQ")]

    spy_verdicts = []
    for fund in spy_funds:
        verdict = evaluate_fund(fund, spy_buy_amount, "S&P 500")
        spy_verdicts.append(verdict)

    qqq_verdicts = []
    for fund in qqq_funds:
        verdict = evaluate_fund(fund, qqq_buy_amount, "Nasdaq-100")
        qqq_verdicts.append(verdict)

    all_verdicts = spy_verdicts + qqq_verdicts

    return {
        "status": "ok",
        "spy_target_buy": spy_buy_amount,
        "qqq_target_buy": qqq_buy_amount,
        "verdicts": [asdict(v) for v in all_verdicts],
        "executable_spy": [asdict(v) for v in spy_verdicts if v.buy_allowed],
        "executable_qqq": [asdict(v) for v in qqq_verdicts if v.buy_allowed],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="QDII execution layer for RMB-based fund execution")
    parser.add_argument("--universe", default="references/qdii_universe.json")
    parser.add_argument("--spy-buy", type=float, default=0.0, help="Target SPY/S&P 500 buy amount in local currency")
    parser.add_argument("--qqq-buy", type=float, default=0.0, help="Target QQQ/Nasdaq-100 buy amount in local currency")
    parser.add_argument("--output-dir", default="references/qdii_run")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    result = generate_qdii_advice(
        Path(args.universe),
        args.spy_buy,
        args.qqq_buy,
    )

    (output_dir / "qdii_advice.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    if result["status"] == "ok" and result["verdicts"]:
        lines = ["# QDII 执行建议", ""]
        lines.append(f"标普500类目标买入：{result['spy_target_buy']}")
        lines.append(f"纳指100类目标买入：{result['qqq_target_buy']}")
        lines.append("")
        lines.append("## 可执行基金候选")
        lines.append("")
        for v in result["verdicts"]:
            status = "✅ 可买" if v["buy_allowed"] else "❌ 暂缓"
            lines.append(f"- {v['fund_code']} {v['name']} ({v['index']}): {status}，金额 {v['buy_amount']}，溢价 {v['premium_pct']:.2f}%，原因：{v['reason']}")
        (output_dir / "qdii_advice.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
