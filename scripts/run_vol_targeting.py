#!/usr/bin/env python3
"""
Compare a backtest with and without the vol-targeting smoothing
layer. Produces a small report and a CSV of effective multipliers
so the user can eyeball the smoothing effect.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
_SCRIPTS = _ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from backtest_us_etf import prepare_dataset, run_backtest  # noqa: E402
from vol_targeting import VolTargetConfig, apply_vol_targeting  # noqa: E402


def _metrics(payload: dict) -> dict:
    s = payload["result"]["strategy"]
    return {
        "xirr": s["xirr"],
        "sharpe": s["sharpe"],
        "sortino": s["sortino"],
        "max_dd": s["unitized_max_drawdown"],
        "final_value": s["final_value"],
        "trade_count": s["trade_count"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=str, default="2020-01-02")
    parser.add_argument("--end", type=str, default="2026-05-29")
    parser.add_argument("--initial-capital", type=float, default=100_000.0)
    parser.add_argument("--weekly-budget", type=float, default=2_000.0)
    parser.add_argument(
        "--price-source",
        type=str,
        default="yahoo_chart_adjusted",
    )
    parser.add_argument(
        "--cache-dir",
        type=str,
        default=str(_ROOT / "references" / "data_cache"),
    )
    parser.add_argument(
        "--cape-vintage-path",
        type=str,
        default=str(_ROOT / "references" / "cape_vintage.csv"),
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=str(_ROOT / "references" / "validation" / "vol_targeting"),
    )
    parser.add_argument("--target-vol", type=float, default=0.12)
    parser.add_argument("--lookback", type=int, default=63)
    parser.add_argument("--vol-floor", type=float, default=0.5)
    parser.add_argument("--vol-ceiling", type=float, default=1.5)
    args = parser.parse_args()

    df = prepare_dataset(
        args.start, args.end,
        price_source=args.price_source,
        cache_dir=args.cache_dir,
        cape_vintage_path=args.cape_vintage_path,
    )
    # 1) Baseline: no vol-targeting.
    payload_base = run_backtest(
        df, args.start, args.end,
        initial_capital=args.initial_capital,
        weekly_budget=args.weekly_budget,
    )
    base = _metrics(payload_base)

    # 2) Vol-targeted: derive effective multiplier and run a second
    # backtest. We do this by post-processing the equity curve from
    # the baseline — the engine itself is unaware of the smooth
    # layer, but the smoother only changes the *multiplier*, not
    # the rule. For a research comparison we re-derive a smoothed
    # equity curve from the daily decisions.
    eq = payload_base["equity_curve"].copy()
    eq["date"] = pd.to_datetime(eq["date"])
    eq = eq.set_index("date")
    rule_mult = eq["multiplier"].astype(float)

    cfg = VolTargetConfig(
        target_vol=args.target_vol,
        lookback=args.lookback,
        vol_floor=args.vol_floor,
        vol_ceiling=args.vol_ceiling,
    )
    # Use SPY for realized vol as the conservative proxy.
    df_aligned = df.reindex(eq.index)
    eff_mult, scale, rv = apply_vol_targeting(
        df_aligned, rule_mult, config=cfg, return_col="spy_close"
    )

    # Build a smoothed equity curve. We re-derive cash and share
    # counts using the effective multiplier. Contributions are the
    # same; only the invested amount changes.
    smoothed_rows = []
    cash = 0.0
    spy_shares = 0.0
    qqq_shares = 0.0
    weekly_budget = args.weekly_budget
    initial_capital = args.initial_capital
    last_contrib_iso = None
    initialized = False
    transaction_cost = 0.0015
    contribution_weekday = 3

    for i, (dt, row) in enumerate(eq.iterrows()):
        spy_px = float(df_aligned.loc[dt, "spy_close"])
        qqq_px = float(df_aligned.loc[dt, "qqq_close"])
        spy_open = float(df_aligned.loc[dt, "spy_open"]) if "spy_open" in df_aligned.columns else spy_px
        qqq_open = float(df_aligned.loc[dt, "qqq_open"]) if "qqq_open" in df_aligned.columns else qqq_px
        if not initialized:
            spy_shares = initial_capital * float(row.get("spy_weight", 0.5)) * (1 - transaction_cost) / spy_open
            qqq_shares = initial_capital * float(row.get("qqq_weight", 0.5)) * (1 - transaction_cost) / qqq_open
            initialized = True
        iso_week = int(pd.Timestamp(dt).isocalendar().week)
        iso_year = int(pd.Timestamp(dt).isocalendar().year)
        contrib_key = (iso_year, iso_week)
        if pd.Timestamp(dt).weekday() >= contribution_weekday and contrib_key != last_contrib_iso:
            last_contrib_iso = contrib_key
            cash += weekly_budget
            mult_smooth = float(eff_mult.loc[dt])
            mult_smooth = max(mult_smooth, 0.0)
            invest_amt = min(cash, weekly_budget * mult_smooth)
            if invest_amt > 0:
                spy_amt = invest_amt * float(row.get("spy_weight", 0.5))
                qqq_amt = invest_amt * float(row.get("qqq_weight", 0.5))
                spy_shares += spy_amt * (1 - transaction_cost) / spy_open
                qqq_shares += qqq_amt * (1 - transaction_cost) / qqq_open
                cash -= invest_amt
        nav = cash + spy_shares * spy_px + qqq_shares * qqq_px
        smoothed_rows.append(
            {
                "date": dt,
                "nav_smoothed": nav,
                "rule_multiplier": float(row["multiplier"]),
                "smooth_scale": float(scale.loc[dt]) if not pd.isna(scale.loc[dt]) else 1.0,
                "effective_multiplier": float(eff_mult.loc[dt]),
                "realized_vol": float(rv.loc[dt]) if not pd.isna(rv.loc[dt]) else float("nan"),
            }
        )
    smooth_eq = pd.DataFrame(smoothed_rows).set_index("date")
    smooth_eq["return"] = smooth_eq["nav_smoothed"].pct_change()
    smooth_eq.dropna(subset=["return"], inplace=True)
    sr_smooth = (
        float(smooth_eq["return"].mean() / smooth_eq["return"].std(ddof=1) * math.sqrt(252))
        if smooth_eq["return"].std(ddof=1) > 0
        else float("nan")
    )
    mdd_smooth = float((smooth_eq["nav_smoothed"] / smooth_eq["nav_smoothed"].cummax() - 1).min())

    base_sharpe = base["sharpe"]
    delta_sharpe = sr_smooth - base_sharpe
    delta_dd = mdd_smooth - base["max_dd"]

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # CSV: daily rule_multiplier, scale, effective_multiplier, realized_vol
    csv_path = out_dir / "effective_multipliers.csv"
    smooth_eq[
        ["rule_multiplier", "smooth_scale", "effective_multiplier", "realized_vol", "nav_smoothed"]
    ].to_csv(csv_path, index_label="date")

    # Markdown
    md = ["# Vol-targeting smoothing layer — comparison", ""]
    md.append(f"Window: {args.start} → {args.end}  ")
    md.append(f"Config: target_vol={cfg.target_vol}, lookback={cfg.lookback}d, "
              f"vol_floor={cfg.vol_floor}, vol_ceiling={cfg.vol_ceiling}  ")
    md.append("")
    md.append("## Comparison")
    md.append("")
    md.append("| Metric | Baseline (rule only) | Vol-targeted | Delta |")
    md.append("|---|---|---|---|")
    md.append(
        f"| Sharpe (annualized) | {base_sharpe:.3f} | {sr_smooth:.3f} | "
        f"{delta_sharpe:+.3f} |"
    )
    md.append(f"| Unitized max DD | {base['max_dd']:.2%} | {mdd_smooth:.2%} | {delta_dd:+.2%} |")
    md.append(f"| Final value | ${base['final_value']:,.0f} | ${float(smooth_eq['nav_smoothed'].iloc[-1]):,.0f} | — |")
    md.append("")
    md.append("## How the smooth layer works")
    md.append("")
    md.append("- `realized_vol` = rolling std of SPY daily returns × √252")
    md.append("- `scale` = clip(target_vol / realized_vol, vol_floor, vol_ceiling)")
    md.append("- `effective_multiplier` = `rule_multiplier` × `scale`")
    md.append("")
    md.append("In a high-vol window, scale drops below 1.0 → the rule is "
              "*attenuated*. In a low-vol window, scale rises above 1.0 → the "
              "rule is *amplified*, but only up to vol_ceiling.")
    md.append("")
    md.append("## Limitations of this comparison")
    md.append("")
    md.append("The smoothed backtest re-derives cash + share counts from the "
              "baseline daily decisions. A more faithful comparison would "
              "re-engineer the buy/sell logic to consume the effective "
              "multiplier natively; that's a P1-3 (slippage-aware) follow-up.")
    md_path = out_dir / "vol_targeting_report.md"
    md_path.write_text("\n".join(md), encoding="utf-8")

    # JSON
    json_path = out_dir / "vol_targeting_report.json"
    json_path.write_text(
        json.dumps(
            {
                "config": {
                    "target_vol": cfg.target_vol,
                    "lookback": cfg.lookback,
                    "vol_floor": cfg.vol_floor,
                    "vol_ceiling": cfg.vol_ceiling,
                },
                "baseline": base,
                "vol_targeted": {
                    "sharpe": sr_smooth,
                    "unitized_max_dd": mdd_smooth,
                    "final_value": float(smooth_eq["nav_smoothed"].iloc[-1]),
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"[vol-targeting] report written to {out_dir}")
    print(f"  baseline  Sharpe={base_sharpe:.3f}  DD={base['max_dd']:.2%}  final=${base['final_value']:,.0f}")
    print(f"  smoothed  Sharpe={sr_smooth:.3f}  DD={mdd_smooth:.2%}  final=${float(smooth_eq['nav_smoothed'].iloc[-1]):,.0f}")


if __name__ == "__main__":
    main()
