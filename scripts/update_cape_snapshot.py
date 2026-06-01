#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import io
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

NASDAQ_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
}

PUBLICATION_LAG_BDAYS = 10


def parse_number(value) -> float:
    if value is None:
        return float("nan")
    if isinstance(value, (int, float)):
        return float(value)
    import re
    text = str(value).replace(",", "")
    m = re.search(r"-?\d+(?:\.\d+)?", text)
    return float(m.group(0)) if m else float("nan")


def fetch_yale_shiller_raw() -> pd.DataFrame:
    url = "https://www.econ.yale.edu/~shiller/data/ie_data.xls"
    r = requests.get(url, headers=NASDAQ_HEADERS, timeout=60)
    r.raise_for_status()
    raw = pd.read_excel(io.BytesIO(r.content), sheet_name="Data", skiprows=7)
    raw = raw.rename(columns=lambda c: str(c).strip())
    date_col = raw.columns[0]
    cape_col = next((c for c in raw.columns if str(c).strip().lower() == "cape"), None)
    if cape_col is None:
        raise RuntimeError("Could not find CAPE column in Yale Shiller spreadsheet")
    raw = raw[[date_col, cape_col]].copy()
    raw.columns = ["year_month", "cape"]
    raw["year_month"] = pd.to_numeric(raw["year_month"], errors="coerce")
    raw["cape"] = pd.to_numeric(raw["cape"], errors="coerce")
    raw = raw.dropna(subset=["year_month", "cape"])
    years = raw["year_month"].astype(int)
    months = ((raw["year_month"] - years) * 100).round().astype(int).clip(lower=1, upper=12)
    raw["observation_month"] = pd.to_datetime({"year": years, "month": months, "day": 1})
    raw["source"] = "yale_shiller_ie_data"
    raw["source_url"] = url
    raw["source_sha256"] = hashlib.sha256(r.content).hexdigest()
    raw["downloaded_at"] = datetime.now().isoformat(timespec="seconds")
    return raw


def fetch_multpl_raw() -> pd.DataFrame:
    url = "https://www.multpl.com/shiller-pe/table/by-month"
    tables = pd.read_html(url)
    raw = tables[0].copy()
    raw.columns = ["observation_month_str", "cape"]
    raw["observation_month"] = pd.to_datetime(raw["observation_month_str"], format="mixed")
    raw["cape"] = raw["cape"].map(parse_number)
    raw = raw.dropna(subset=["observation_month", "cape"])
    raw["source"] = "multpl"
    raw["source_url"] = url
    raw["source_sha256"] = ""
    raw["downloaded_at"] = datetime.now().isoformat(timespec="seconds")
    return raw


def compute_available_at(observation_month: pd.Timestamp, lag_bdays: int = PUBLICATION_LAG_BDAYS) -> pd.Timestamp:
    return observation_month + pd.offsets.BDay(lag_bdays)

def _resolve_available_at(observation_month: pd.Timestamp, downloaded_at: str, lag_bdays: int = PUBLICATION_LAG_BDAYS) -> Optional[str]:
    """Clamp the PIT available_at to min(lag-based date, downloaded_at).

    The 10-BDay publication lag is a "potentially known" bound from the publisher's
    perspective; downloaded_at is the bound from our perspective. The earliest
    date at which the data could have been known is min(lag_date, downloaded_at).
    This guarantees the resulting row never claims available_at > downloaded_at,
    which would be a future-dated vintage row.

    Returns None if downloaded_at is unparseable or the clamped value would still
    be in the future (defensive — should be impossible after min()).
    """
    try:
        downloaded_dt = pd.Timestamp(downloaded_at)
    except Exception:
        return None
    lag_dt = compute_available_at(observation_month, lag_bdays)
    available_dt = min(lag_dt, downloaded_dt)
    if available_dt > downloaded_dt:
        return None
    return available_dt.strftime("%Y-%m-%d")


def build_vintage(yale_df: Optional[pd.DataFrame], multpl_df: Optional[pd.DataFrame]) -> pd.DataFrame:
    records = []
    if yale_df is not None and not yale_df.empty:
        for _, row in yale_df.iterrows():
            obs = pd.Timestamp(row["observation_month"])
            available_at = _resolve_available_at(obs, row["downloaded_at"])
            if available_at is None:
                continue
            records.append({
                "observation_month": obs.strftime("%Y-%m-%d"),
                "published_at": row["downloaded_at"],
                "available_at": available_at,
                "cape": float(row["cape"]),
                "source": row["source"],
                "source_url": row["source_url"],
                "source_sha256": row.get("source_sha256", ""),
                "downloaded_at": row["downloaded_at"],
            })

    if multpl_df is not None and not multpl_df.empty:
        yale_max = None
        if records:
            yale_dates = [pd.Timestamp(r["observation_month"]) for r in records]
            yale_max = max(yale_dates)
        for _, row in multpl_df.iterrows():
            obs = pd.Timestamp(row["observation_month"])
            if yale_max is not None and obs <= yale_max:
                continue
            available_at = _resolve_available_at(obs, row["downloaded_at"])
            if available_at is None:
                continue
            records.append({
                "observation_month": obs.strftime("%Y-%m-%d"),
                "published_at": row["downloaded_at"],
                "available_at": available_at,
                "cape": float(row["cape"]),
                "source": row["source"],
                "source_url": row["source_url"],
                "source_sha256": row.get("source_sha256", ""),
                "downloaded_at": row["downloaded_at"],
            })

    if not records:
        raise RuntimeError("No CAPE data available from any source")

    df = pd.DataFrame.from_records(records).sort_values("observation_month").reset_index(drop=True)
    return df


def load_vintage(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, parse_dates=["observation_month", "available_at"])


def merge_vintage(existing: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame:
    if existing.empty:
        return new
    if new.empty:
        return existing
    combined = pd.concat([existing, new]).drop_duplicates(
        subset=["observation_month", "source"], keep="last"
    ).sort_values("observation_month").reset_index(drop=True)
    return combined


def cape_available_at_signal(vintage_df: pd.DataFrame, signal_date: pd.Timestamp) -> Optional[float]:
    if vintage_df.empty:
        return None
    available = vintage_df[pd.to_datetime(vintage_df["available_at"]) <= signal_date]
    if available.empty:
        return None
    return float(available.iloc[-1]["cape"])


def main() -> None:
    parser = argparse.ArgumentParser(description="Update CAPE vintage snapshot with available_at constraints")
    parser.add_argument("--output", default="references/data_cache/cape_vintage.csv")
    parser.add_argument("--lag-bdays", type=int, default=PUBLICATION_LAG_BDAYS)
    parser.add_argument("--skip-yale", action="store_true")
    parser.add_argument("--skip-multpl", action="store_true")
    args = parser.parse_args()

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    yale_df = None
    multpl_df = None

    if not args.skip_yale:
        try:
            yale_df = fetch_yale_shiller_raw()
            print(f"Yale Shiller: {len(yale_df)} rows, latest {yale_df['observation_month'].max().strftime('%Y-%m')}")
        except Exception as e:
            print(f"Yale Shiller fetch failed: {e}")

    if not args.skip_multpl:
        try:
            multpl_df = fetch_multpl_raw()
            print(f"Multpl: {len(multpl_df)} rows, latest {multpl_df['observation_month'].max().strftime('%Y-%m')}")
        except Exception as e:
            print(f"Multpl fetch failed: {e}")

    new_vintage = build_vintage(yale_df, multpl_df)
    existing = load_vintage(output_path)
    merged = merge_vintage(existing, new_vintage)
    merged.to_csv(output_path, index=False)

    delivery_path = Path(__file__).resolve().parents[1] / "references" / "cape_vintage.csv"
    import shutil
    shutil.copy2(output_path, delivery_path)
    print(f"CAPE vintage written to {output_path}: {len(merged)} rows")
    print(f"Latest observation: {merged['observation_month'].iloc[-1]}")
    print(f"Latest available_at: {merged['available_at'].iloc[-1]}")


if __name__ == "__main__":
    main()
