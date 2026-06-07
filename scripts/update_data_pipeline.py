#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import date
from pathlib import Path
from typing import Dict, List

import pandas as pd

import sys as _sys
_scripts_dir = str(Path(__file__).resolve().parent)
if _scripts_dir not in _sys.path:
    _sys.path.insert(0, _scripts_dir)

from backtest_us_etf import prepare_dataset, decide
from factor_signals import compute_factors, multifactor_signal, forward_return_target, information_stats, FACTOR_NAMES


SCHEMA_VERSION = 1


def _ensure_meta(conn: sqlite3.Connection) -> None:
    conn.execute("create table if not exists meta (key text primary key, value text not null)")
    row = conn.execute("select value from meta where key='schema_version'").fetchone()
    if row is None:
        conn.execute("insert into meta(key, value) values('schema_version', ?)", (str(SCHEMA_VERSION),))
    elif row[0] != str(SCHEMA_VERSION):
        raise RuntimeError(f"schema change requires manual confirmation: found {row[0]}, expected {SCHEMA_VERSION}")


def write_prices_db(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    prices = df.reset_index()[[
        "date", "spy_open", "spy_high", "spy_low", "spy_close", "spy_volume",
        "qqq_open", "qqq_high", "qqq_low", "qqq_close", "qqq_volume", "vix",
    ]]
    with sqlite3.connect(path) as conn:
        _ensure_meta(conn)
        prices.to_sql("daily_prices", conn, if_exists="replace", index=False)


def write_vix_csv(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.reset_index()[["date", "vix"]].to_csv(path, index=False)


def write_factors_db(factors: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    out = factors.reset_index()[["date", *FACTOR_NAMES]]
    with sqlite3.connect(path) as conn:
        _ensure_meta(conn)
        out.to_sql("daily_factors", conn, if_exists="replace", index=False)


def integrity_checks(df: pd.DataFrame) -> List[str]:
    errors: List[str] = []
    if df.empty:
        return ["dataset is empty"]
    gaps = df.index.to_series().diff().dt.days.dropna()
    if (gaps > 7).any():
        errors.append("detected date gap > 7 calendar days")
    for col in ["spy_close", "qqq_close", "vix"]:
        if df[col].isna().any():
            errors.append(f"{col} contains NaN")
        if (df[col].astype(float) <= 0).any():
            errors.append(f"{col} contains non-positive values")
    for col in ["spy_close", "qqq_close"]:
        if (df[col].pct_change().abs() > 0.25).any():
            errors.append(f"{col} has daily move > 25%")
    return errors


def write_today_signal(df: pd.DataFrame, factors: pd.DataFrame, path: Path) -> Dict:
    row = df.iloc[-1]
    dec = decide(row)
    payload = {
        "date": str(pd.Timestamp(df.index[-1]).date()),
        "regime": dec.regime,
        "multiplier": dec.multiplier,
        "target_spy_weight": dec.spy_weight,
        "target_qqq_weight": dec.qqq_weight,
        "vix": float(row["vix"]),
        "factor_signal": float(multifactor_signal(factors).dropna().iloc[-1]) if not factors.dropna().empty else 0.0,
        "reason": dec.reason,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Update local SPY/QQQ data and factor databases")
    parser.add_argument("--start", default="2018-01-01")
    parser.add_argument("--end", default=date.today().isoformat())
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--signals-dir", default="signals")
    parser.add_argument("--log-dir", default="daily_log")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    state_path = root / args.log_dir / ".pipeline_state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        df = prepare_dataset(
            args.start,
            args.end,
            price_source="yahoo_chart_adjusted",
            cache_dir=str(root / "references" / "data_cache"),
            cape_vintage_path=str(root / "references" / "cape_vintage.csv"),
        )
        factors = compute_factors(df)
        errors = integrity_checks(df)
        if errors:
            raise RuntimeError("; ".join(errors))
        write_prices_db(df, root / args.data_dir / "prices.db")
        write_vix_csv(df, root / args.data_dir / "vix_daily.csv")
        write_factors_db(factors, root / args.data_dir / "factors.db")
        signal = write_today_signal(df, factors, root / args.signals_dir / "today_signal.json")
        payload = {
            "status": "ok",
            "date": date.today().isoformat(),
            "last_market_date": str(pd.Timestamp(df.index[-1]).date()),
            "rows": int(len(df)),
            "signal": signal,
        }
        state_path.write_text(json.dumps({"consecutive_failures": 0}, indent=2), encoding="utf-8")
    except Exception as exc:
        state = {"consecutive_failures": 0}
        if state_path.exists():
            state.update(json.loads(state_path.read_text(encoding="utf-8")))
        state["consecutive_failures"] = int(state.get("consecutive_failures", 0)) + 1
        state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
        payload = {"status": "failed", "date": date.today().isoformat(), "error": str(exc), **state}
        if state["consecutive_failures"] >= 3:
            payload["exit_reason"] = "consecutive 3 data pull failures"
    log_path = root / args.log_dir / f"{date.today().isoformat()}.txt"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if payload["status"] != "ok":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
