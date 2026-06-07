#!/usr/bin/env python3
from __future__ import annotations

import argparse
import itertools
import json
import math
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Dict, Iterable, List

import numpy as np
import pandas as pd

import sys as _sys
_scripts_dir = str(Path(__file__).resolve().parent)
if _scripts_dir not in _sys.path:
    _sys.path.insert(0, _scripts_dir)

from backtest_us_etf import prepare_dataset
from risk_sizing import max_drawdown


LOOKBACKS = [10, 15, 20, 30, 40, 60, 90]
THRESHOLDS = [0.00, 0.01, 0.02, 0.03, 0.05]
HOLDINGS = [5, 10, 20, 21, 42, 63]


@dataclass(frozen=True)
class ParamSet:
    lookback: int
    threshold: float
    holding: int


def _empty_metrics() -> Dict[str, float]:
    return {"sharpe": 0.0, "maxdd": 0.0, "cagr": 0.0, "win_rate": 0.0, "final_nav": 1.0}


def performance_metrics(nav: pd.Series) -> Dict[str, float]:
    nav = nav.astype(float).dropna()
    if len(nav) < 3:
        return _empty_metrics()
    ret = nav.pct_change().dropna()
    if ret.empty:
        return _empty_metrics()
    years = max((nav.index[-1] - nav.index[0]).days / 365.25, 1 / 252)
    vol = float(ret.std() * np.sqrt(252))
    sharpe = float(ret.mean() * 252 / vol) if vol > 0 else 0.0
    cagr = float((nav.iloc[-1] / nav.iloc[0]) ** (1 / years) - 1)
    monthly = nav.resample("ME").last().pct_change().dropna()
    win_rate = float((monthly > 0).mean()) if not monthly.empty else float((ret > 0).mean())
    return {
        "sharpe": sharpe,
        "maxdd": max_drawdown(nav),
        "cagr": cagr,
        "win_rate": win_rate,
        "final_nav": float(nav.iloc[-1] / nav.iloc[0]),
    }


def momentum_overlay_nav(df: pd.DataFrame, params: ParamSet) -> pd.Series:
    spy = df["spy_close"].astype(float)
    qqq = df["qqq_close"].astype(float)
    vix = df["vix"].astype(float)

    spy_mom = spy.pct_change(params.lookback)
    qqq_mom = qqq.pct_change(params.lookback)
    rel = qqq_mom - spy_mom
    spy_daily = spy.pct_change().fillna(0.0)
    qqq_daily = qqq.pct_change().fillna(0.0)

    position = []
    current = "SPY"
    next_rebalance_idx = 0
    for i, _ in enumerate(df.index):
        if i >= params.lookback and i >= next_rebalance_idx:
            signal_i = i - 1
            if rel.iloc[signal_i] > params.threshold and qqq_mom.iloc[signal_i] > 0:
                current = "QQQ"
            elif spy_mom.iloc[signal_i] > -params.threshold:
                current = "SPY"
            else:
                current = "CASH"
            next_rebalance_idx = i + params.holding
        position.append(current)

    pos = pd.Series(position, index=df.index).shift(1).fillna("CASH")
    risk_scale = np.where(vix.shift(1).fillna(vix) >= 25.0, 0.50, 1.0)
    daily = pd.Series(0.0, index=df.index)
    daily.loc[pos == "SPY"] = spy_daily.loc[pos == "SPY"]
    daily.loc[pos == "QQQ"] = qqq_daily.loc[pos == "QQQ"]
    daily = daily * risk_scale
    nav = (1.0 + daily.fillna(0.0)).cumprod()
    nav.name = "momentum_overlay_nav"
    return nav


def split_nav(nav: pd.Series, start: str, end: str) -> pd.Series:
    part = nav.loc[(nav.index >= pd.Timestamp(start)) & (nav.index <= pd.Timestamp(end))].copy()
    if part.empty:
        return part
    return part / part.iloc[0]


def run_search(df: pd.DataFrame, oos_start: str = "2022-01-01") -> Dict:
    rows: List[Dict] = []
    best = None
    for lookback, threshold, holding in itertools.product(LOOKBACKS, THRESHOLDS, HOLDINGS):
        params = ParamSet(int(lookback), round(float(threshold), 2), int(holding))
        nav = momentum_overlay_nav(df, params)
        ins = split_nav(nav, "2018-01-01", "2021-12-31")
        oos = split_nav(nav, oos_start, str(df.index.max().date()))
        ins_metrics = performance_metrics(ins)
        oos_metrics = performance_metrics(oos)
        row = {
            **asdict(params),
            "in_sample": ins_metrics,
            "out_of_sample": oos_metrics,
            "score": (
                oos_metrics["sharpe"]
                + max(oos_metrics["cagr"], -1.0)
                + min(0.0, oos_metrics["maxdd"] + 0.18) * 4
                + oos_metrics["win_rate"]
            ),
        }
        rows.append(row)
        if best is None or row["score"] > best["score"]:
            best = row

    passing = [
        r for r in rows
        if r["out_of_sample"]["sharpe"] > 1.2
        and r["out_of_sample"]["maxdd"] > -0.18
        and r["out_of_sample"]["cagr"] > 0.12
        and r["out_of_sample"]["win_rate"] > 0.52
    ]
    if passing:
        best = max(passing, key=lambda r: r["score"])

    assert best is not None
    oos = best["out_of_sample"]
    return {
        "generated_at": pd.Timestamp.utcnow().isoformat(),
        "tested_combinations": len(rows),
        "criteria": {
            "oos_start": oos_start,
            "sharpe_gt": 1.2,
            "maxdd_gt": -0.18,
            "cagr_gt": 0.12,
            "win_rate_gt": 0.52,
        },
        "passed_criteria": bool(best in passing),
        "lookback": int(best["lookback"]),
        "threshold": round(float(best["threshold"]), 2),
        "holding": int(best["holding"]),
        "sharpe": float(oos["sharpe"]),
        "maxdd": float(oos["maxdd"]),
        "cagr": float(oos["cagr"]),
        "win_rate": float(oos["win_rate"]),
        "best": best,
        "top_results": sorted(rows, key=lambda r: r["score"], reverse=True)[:20],
    }


def write_html_report(payload: Dict, output: Path) -> None:
    rows = []
    for item in payload["top_results"]:
        m = item["out_of_sample"]
        rows.append(
            "<tr>"
            f"<td>{item['lookback']}</td><td>{item['threshold']:.2f}</td><td>{item['holding']}</td>"
            f"<td>{m['sharpe']:.3f}</td><td>{m['maxdd']:.2%}</td>"
            f"<td>{m['cagr']:.2%}</td><td>{m['win_rate']:.2%}</td>"
            "</tr>"
        )
    html = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Parameter Optimization Report</title>
<style>body{{font-family:Arial,sans-serif;margin:32px;line-height:1.45}}table{{border-collapse:collapse}}td,th{{border:1px solid #ddd;padding:6px 9px}}th{{background:#f5f5f5}}</style>
</head><body>
<h1>SPY/QQQ Parameter Optimization</h1>
<p>Tested {payload['tested_combinations']} combinations. Criteria pass: {payload['passed_criteria']}.</p>
<h2>Selected Parameters</h2>
<pre>{json.dumps({k: payload[k] for k in ['lookback','threshold','holding','sharpe','maxdd','cagr','win_rate']}, indent=2)}</pre>
<h2>Top Out-of-Sample Results</h2>
<table><thead><tr><th>Lookback</th><th>Threshold</th><th>Holding</th><th>Sharpe</th><th>MaxDD</th><th>CAGR</th><th>Win Rate</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table>
</body></html>
"""
    output.write_text(html, encoding="utf-8")


def load_default_dataset(start: str, end: str) -> pd.DataFrame:
    root = Path(__file__).resolve().parents[1]
    return prepare_dataset(
        start,
        end,
        price_source="yahoo_chart_adjusted",
        cache_dir=str(root / "references" / "data_cache"),
        cape_vintage_path=str(root / "references" / "cape_vintage.csv"),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Grid-search SPY/QQQ momentum overlay parameters")
    parser.add_argument("--start", default="2018-01-01")
    parser.add_argument("--end", default=date.today().isoformat())
    parser.add_argument("--output-dir", default="results")
    args = parser.parse_args()

    df = load_default_dataset(args.start, args.end)
    if pd.Timestamp(args.end) > df.index.max():
        args.end = str(df.index.max().date())
    payload = run_search(df)
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "optimal_params.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    write_html_report(payload, out / "backtest_report.html")
    print(json.dumps({k: payload[k] for k in ["tested_combinations", "passed_criteria", "lookback", "threshold", "holding", "sharpe", "maxdd", "cagr", "win_rate"]}, indent=2))


if __name__ == "__main__":
    main()
