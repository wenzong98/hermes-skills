#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

import pandas as pd


def export_market_inputs(
    main_repo: Path,
    output_path: Path,
    price_source: str = "yahoo_chart_adjusted",
    require_adjusted: bool = True,
    cache_dir: Optional[str] = None,
    cape_vintage_path: Optional[str] = None,
    start: str = "2023-05-29",
    end: str = "2026-05-29",
) -> None:
    scripts_dir = main_repo / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))

    import data_sources as ds

    start_ts = pd.Timestamp(start)
    fetch_start = (start_ts - pd.Timedelta(days=420)).strftime("%Y-%m-%d")

    spy_raw = ds.fetch_etf_ohlcv(
        "SPY", fetch_start, end, price_source,
        cache_dir=cache_dir,
    )
    qqq_raw = ds.fetch_etf_ohlcv(
        "QQQ", fetch_start, end, price_source,
        cache_dir=cache_dir,
    )

    if require_adjusted:
        if spy_raw.attrs.get("price_return_only") or qqq_raw.attrs.get("price_return_only"):
            raise RuntimeError(
                f"--require-adjusted is set but actual data source is price-return-only. "
                f"SPY source: {spy_raw.attrs.get('price_source')}, "
                f"QQQ source: {qqq_raw.attrs.get('price_source')}."
            )

    spy = spy_raw.add_prefix("spy_")
    qqq = qqq_raw.add_prefix("qqq_")
    vix = ds.fetch_cboe_vix(fetch_start, end)

    if cape_vintage_path:
        cape = ds.load_cape_vintage(cape_vintage_path)
    else:
        cape = ds.fetch_shiller_cape(fetch_start, end)

    df = spy.join(qqq, how="inner").join(vix, how="left")
    df["vix"] = df["vix"].ffill()
    df["cape"] = cape["cape"].reindex(df.index, method="ffill")

    out = df.reset_index()
    out = out.rename(columns={"index": "date"})

    input_cols = [
        "date", "spy_open", "spy_high", "spy_low", "spy_close", "spy_volume",
        "qqq_open", "qqq_high", "qqq_low", "qqq_close", "qqq_volume",
        "vix", "cape",
    ]
    available = [c for c in input_cols if c in out.columns]
    out = out[available].copy()

    if cape_vintage_path:
        try:
            vintage_df = pd.read_csv(cape_vintage_path, parse_dates=["observation_month", "available_at"])
            vintage_df = vintage_df.sort_values("available_at")
            obs_months = []
            avail_ats = []
            for dt_val in df.index:
                matched = None
                for avail_dt in reversed(vintage_df["available_at"]):
                    if avail_dt <= dt_val:
                        matched = avail_dt
                        break
                if matched is not None:
                    row = vintage_df[vintage_df["available_at"] == matched].iloc[0]
                    obs_months.append(str(pd.Timestamp(row["observation_month"]).date()))
                    avail_ats.append(str(matched.date()))
                else:
                    obs_months.append("")
                    avail_ats.append("")
            out["cape_observation_month"] = obs_months
            out["cape_available_at"] = avail_ats
        except Exception:
            pass

    meta = {
        "price_source": price_source,
        "adjusted_for_dividends": bool(spy_raw.attrs.get("adjusted_for_dividends")),
        "price_return_only": bool(spy_raw.attrs.get("price_return_only")),
        "cape_source": f"vintage_file:{Path(cape_vintage_path).name}" if cape_vintage_path else "",
        "export_row_count": len(out),
        "export_date_range": f"{out.iloc[0]['date']} ~ {out.iloc[-1]['date']}" if len(out) > 0 else "",
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(output_path, index=False)
    print(f"Exported {len(out)} rows x {len(out.columns)} columns to {output_path}")
    print(f"Date range: {meta['export_date_range']}")
    print(f"Meta: {json.dumps(meta)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Export raw market inputs for independent verifier (with warmup)")
    parser.add_argument("--main-repo", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--output", default="references/market_inputs_3y.csv")
    parser.add_argument("--price-source", default="yahoo_chart_adjusted")
    parser.add_argument("--require-adjusted", action="store_true", default=True)
    parser.add_argument("--cache-dir", default=None)
    parser.add_argument("--cape-vintage-path", default=None)
    parser.add_argument("--start", default="2023-05-29")
    parser.add_argument("--end", default="2026-05-29")
    args = parser.parse_args()

    export_market_inputs(
        Path(args.main_repo).expanduser().resolve(),
        Path(args.output),
        price_source=args.price_source,
        require_adjusted=args.require_adjusted,
        cache_dir=args.cache_dir,
        cape_vintage_path=args.cape_vintage_path,
        start=args.start,
        end=args.end,
    )


if __name__ == "__main__":
    main()
