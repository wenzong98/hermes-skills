#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import io
import json
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

import pandas as pd
import requests

NASDAQ_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://www.nasdaq.com",
    "Referer": "https://www.nasdaq.com/",
}

PRICE_SOURCES = {
    "nasdaq_price_return",
    "yahoo_chart_adjusted",
    "alpha_vantage_adjusted",
    "tiingo_adjusted",
}


def parse_number(value) -> float:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return float("nan")
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace(",", "")
    m = re.search(r"-?\d+(?:\.\d+)?", text)
    return float(m.group(0)) if m else float("nan")


def _file_sha256(path: Path) -> Optional[str]:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _cache_key(symbol: str, source: str, start: str, end: str) -> str:
    return f"{symbol}_{source}_{start}_{end}"


def _cache_paths(cache_dir: Path, symbol: str, source: str, start: str, end: str):
    stem = _cache_key(symbol, source, start, end)
    return cache_dir / f"{stem}.csv", cache_dir / f"{stem}_manifest.json", cache_dir / "raw" / f"{stem}.raw"


def _write_cache(
    cache_dir: Path,
    symbol: str,
    source: str,
    start: str,
    end: str,
    df: pd.DataFrame,
    raw_bytes: Optional[bytes] = None,
) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    csv_path, manifest_path, raw_path = _cache_paths(cache_dir, symbol, source, start, end)
    df.to_csv(csv_path)
    if raw_bytes is not None:
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_bytes(raw_bytes)
        raw_sha256 = hashlib.sha256(raw_bytes).hexdigest()
    else:
        raw_sha256 = None
    normalized_sha256 = _file_sha256(csv_path)
    manifest = {
        "symbol": symbol,
        "provider": source,
        "start": start,
        "end": end,
        "adjusted_for_dividends": bool(df.attrs.get("adjusted_for_dividends")),
        "price_return_only": bool(df.attrs.get("price_return_only")),
        "raw_sha256": raw_sha256,
        "normalized_sha256": normalized_sha256,
        "downloaded_at": datetime.now().isoformat(timespec="seconds"),
        "row_count": int(len(df)),
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_cache(
    cache_dir: Path,
    symbol: str,
    source: str,
    start: str,
    end: str,
) -> Optional[pd.DataFrame]:
    csv_path, manifest_path, _raw_path = _cache_paths(cache_dir, symbol, source, start, end)
    if not csv_path.exists():
        return None
    current_hash = _file_sha256(csv_path)
    if not manifest_path.exists():
        return None
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("normalized_sha256") != current_hash:
        return None
    df = pd.read_csv(csv_path, index_col=0, parse_dates=True)
    for k, v in manifest.items():
        df.attrs[k] = v
    if "provider" in manifest and "price_source" not in df.attrs:
        df.attrs["price_source"] = manifest["provider"]
    return df


def fetch_nasdaq_ohlcv(symbol: str, start: str, end: str, assetclass: str = "etf") -> Tuple[pd.DataFrame, bytes]:
    url = (
        f"https://api.nasdaq.com/api/quote/{symbol}/historical"
        f"?assetclass={assetclass}&fromdate={start}&todate={end}&limit=9999"
    )
    r = requests.get(url, headers=NASDAQ_HEADERS, timeout=40)
    r.raise_for_status()
    raw = r.content
    payload = r.json()
    if not payload.get("data") or not payload["data"].get("tradesTable"):
        raise RuntimeError(f"Nasdaq API returned no tradesTable for {symbol}: {payload!r}")
    rows = payload["data"]["tradesTable"].get("rows") or []
    if not rows:
        raise RuntimeError(f"Nasdaq API returned zero rows for {symbol}")
    records = []
    for row in rows:
        records.append({
            "date": pd.to_datetime(row["date"], format="%m/%d/%Y"),
            "open": parse_number(row.get("open")),
            "high": parse_number(row.get("high")),
            "low": parse_number(row.get("low")),
            "close": parse_number(row.get("close")),
            "volume": parse_number(row.get("volume")),
        })
    df = pd.DataFrame.from_records(records).dropna(subset=["date", "close"]).sort_values("date")
    df = df.set_index("date")
    df.index = pd.to_datetime(df.index)
    out = df[["open", "high", "low", "close", "volume"]]
    out.attrs["price_source"] = "nasdaq_price_return"
    out.attrs["price_return_only"] = True
    out.attrs["adjusted_for_dividends"] = False
    return out, raw


def fetch_yahoo_chart_adjusted_ohlcv(symbol: str, start: str, end: str) -> Tuple[pd.DataFrame, bytes]:
    """Fetch dividend- and split-adjusted OHLCV from the Yahoo Finance chart API.

    Approximation caveat (B6): Yahoo returns the *raw* (un-adjusted) open/
    high/low/volume alongside a fully-adjusted close. We back out the
    adjustment factor as `adj_close / close_raw` and apply it to the raw
    open/high/low so they line up with the adjusted close. The high/low
    values can therefore be slightly off from a true vendor-native adjusted
    high/low — particularly on large-dividend ex-dates where the per-day
    adjustment factor compresses the daily range. For DCA weekly budgeting
    and 21/63/252-day returns this is well within tolerance; downstream
    features that depend on intraday range (e.g. "true range" or
    "intraday volatility") should use Tiingo or Alpha Vantage adjusted
    series instead.
    """
    start_ts = int(pd.Timestamp(start, tz="UTC").timestamp())
    end_ts = int((pd.Timestamp(end, tz="UTC") + pd.Timedelta(days=1)).timestamp())
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        f"?period1={start_ts}&period2={end_ts}&interval=1d"
        "&events=history%7Cdiv%7Csplit&includeAdjustedClose=true"
    )
    r = requests.get(url, headers={"User-Agent": NASDAQ_HEADERS["User-Agent"]}, timeout=40)
    r.raise_for_status()
    raw = r.content
    payload = r.json()
    result = (payload.get("chart", {}).get("result") or [None])[0]
    if not result or not result.get("timestamp"):
        raise RuntimeError(f"Yahoo chart returned no rows for {symbol}: {payload!r}")

    quote = result["indicators"]["quote"][0]
    adjclose = result["indicators"].get("adjclose", [{}])[0].get("adjclose")
    if adjclose is None:
        raise RuntimeError(f"Yahoo chart returned no adjusted close for {symbol}")

    df = pd.DataFrame({
        "date": pd.to_datetime(result["timestamp"], unit="s").tz_localize("UTC").tz_convert(None).normalize(),
        "open_raw": quote.get("open"),
        "high_raw": quote.get("high"),
        "low_raw": quote.get("low"),
        "close_raw": quote.get("close"),
        "volume": quote.get("volume"),
        "adj_close": adjclose,
    }).dropna(subset=["date", "close_raw", "adj_close"]).sort_values("date")
    factor = df["adj_close"] / df["close_raw"]
    df["open"] = df["open_raw"] * factor
    df["high"] = df["high_raw"] * factor
    df["low"] = df["low_raw"] * factor
    df["close"] = df["adj_close"]
    out = df.set_index("date")[["open", "high", "low", "close", "volume"]]
    out.attrs["price_source"] = "yahoo_chart_adjusted"
    out.attrs["price_return_only"] = False
    out.attrs["adjusted_for_dividends"] = True
    return out, raw


def fetch_alpha_vantage_adjusted_ohlcv(symbol: str, start: str, end: str, api_key: str) -> Tuple[pd.DataFrame, bytes]:
    """Fetch Alpha Vantage TIME_SERIES_DAILY_ADJUSTED for a US ETF/equity.

    Approximation caveat (B6): the AV adjusted close is the ground truth,
    but the adjusted open/high/low are derived by multiplying the raw
    values by the same `adj_close / close` factor used elsewhere. This is
    the standard adjustment idiom for AV's response shape. As with Yahoo,
    on large-dividend ex-dates the synthesized adjusted high/low can
    deviate from a true adjusted high/low by a few basis points of the
    daily range. Acceptable for DCA / weekly return work; not suitable for
    high-frequency intraday features.
    """
    if not api_key:
        raise RuntimeError("alpha_vantage_adjusted requires ALPHAVANTAGE_API_KEY or --alpha-vantage-api-key")
    url = (
        "https://www.alphavantage.co/query"
        f"?function=TIME_SERIES_DAILY_ADJUSTED&symbol={symbol}&outputsize=full&apikey={api_key}"
    )
    r = requests.get(url, headers={"User-Agent": NASDAQ_HEADERS["User-Agent"]}, timeout=60)
    r.raise_for_status()
    raw = r.content
    payload = r.json()
    series = payload.get("Time Series (Daily)")
    if not series:
        raise RuntimeError(f"Alpha Vantage returned no daily adjusted data for {symbol}: {payload!r}")

    records = []
    for day, row in series.items():
        close_raw = parse_number(row.get("4. close"))
        adj_close = parse_number(row.get("5. adjusted close"))
        factor = adj_close / close_raw if close_raw else float("nan")
        records.append({
            "date": pd.to_datetime(day),
            "open": parse_number(row.get("1. open")) * factor,
            "high": parse_number(row.get("2. high")) * factor,
            "low": parse_number(row.get("3. low")) * factor,
            "close": adj_close,
            "volume": parse_number(row.get("6. volume")),
        })
    df = pd.DataFrame.from_records(records).dropna(subset=["date", "close"]).sort_values("date").set_index("date")
    df = df.loc[(df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))]
    out = df[["open", "high", "low", "close", "volume"]]
    out.attrs["price_source"] = "alpha_vantage_adjusted"
    out.attrs["price_return_only"] = False
    out.attrs["adjusted_for_dividends"] = True
    return out, raw


def fetch_tiingo_adjusted_ohlcv(symbol: str, start: str, end: str, api_key: str) -> Tuple[pd.DataFrame, bytes]:
    if not api_key:
        raise RuntimeError("tiingo_adjusted requires TIINGO_API_KEY or --tiingo-api-key")
    url = (
        f"https://api.tiingo.com/tiingo/daily/{symbol}/prices"
        f"?startDate={start}&endDate={end}&format=json"
    )
    r = requests.get(url, headers={"Authorization": f"Token {api_key}"}, timeout=60)
    r.raise_for_status()
    raw = r.content
    payload = r.json()
    if not payload:
        raise RuntimeError(f"Tiingo returned no data for {symbol}")

    records = []
    for row in payload:
        records.append({
            "date": pd.to_datetime(row.get("date")),
            "open": parse_number(row.get("adjOpen")),
            "high": parse_number(row.get("adjHigh")),
            "low": parse_number(row.get("adjLow")),
            "close": parse_number(row.get("adjClose")),
            "volume": parse_number(row.get("adjVolume", row.get("volume"))),
        })
    df = pd.DataFrame.from_records(records).dropna(subset=["date", "close"]).sort_values("date").set_index("date")
    out = df[["open", "high", "low", "close", "volume"]]
    out.attrs["price_source"] = "tiingo_adjusted"
    out.attrs["price_return_only"] = False
    out.attrs["adjusted_for_dividends"] = True
    return out, raw


def fetch_cboe_vix(start: str, end: str) -> pd.DataFrame:
    url = "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"
    r = requests.get(url, headers={"User-Agent": NASDAQ_HEADERS["User-Agent"]}, timeout=40)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text))
    df.columns = [c.strip().lower() for c in df.columns]
    df["date"] = pd.to_datetime(df["date"])
    df = df.rename(columns={"close": "vix"}).set_index("date").sort_index()
    return df.loc[(df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end)), ["vix"]]


def fetch_yale_shiller_cape(start: str, end: str) -> pd.DataFrame:
    url = "https://www.econ.yale.edu/~shiller/data/ie_data.xls"
    r = requests.get(url, headers={"User-Agent": NASDAQ_HEADERS["User-Agent"]}, timeout=60)
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
    months = ((raw["year_month"] - years) * 100).round().astype(int)
    months = months.clip(lower=1, upper=12)
    raw["date"] = pd.to_datetime({"year": years, "month": months, "day": 1})
    out = raw[["date", "cape"]].set_index("date").sort_index()
    out.attrs["cape_source"] = "yale_shiller_ie_data"
    return out.loc[(out.index >= pd.Timestamp(start) - pd.DateOffset(months=2)) & (out.index <= pd.Timestamp(end))]


def fetch_multpl_cape(start: str, end: str) -> pd.DataFrame:
    url = "https://www.multpl.com/shiller-pe/table/by-month"
    tables = pd.read_html(url)
    raw = tables[0].copy()
    raw.columns = ["date", "cape"]
    raw["date"] = pd.to_datetime(raw["date"], format="mixed")
    raw["cape"] = raw["cape"].map(parse_number)
    raw = raw.dropna(subset=["date", "cape"]).set_index("date").sort_index()
    raw.attrs["cape_source"] = "multpl"
    return raw.loc[(raw.index >= pd.Timestamp(start) - pd.DateOffset(months=2)) & (raw.index <= pd.Timestamp(end))]


def fetch_etf_ohlcv(
    symbol: str,
    start: str,
    end: str,
    price_source: str = "nasdaq_price_return",
    allow_price_return_fallback: bool = False,
    alpha_vantage_api_key: Optional[str] = None,
    tiingo_api_key: Optional[str] = None,
    cache_dir: Optional[str] = None,
) -> pd.DataFrame:
    if cache_dir:
        cached = _read_cache(Path(cache_dir), symbol, price_source, start, end)
        if cached is not None:
            return cached

    df, raw_bytes = _fetch_etf_ohlcv_live(
        symbol, start, end, price_source,
        allow_price_return_fallback=allow_price_return_fallback,
        alpha_vantage_api_key=alpha_vantage_api_key,
        tiingo_api_key=tiingo_api_key,
    )

    if cache_dir:
        _write_cache(Path(cache_dir), symbol, price_source, start, end, df, raw_bytes=raw_bytes)

    return df


def _fetch_etf_ohlcv_live(
    symbol: str,
    start: str,
    end: str,
    price_source: str,
    allow_price_return_fallback: bool = False,
    alpha_vantage_api_key: Optional[str] = None,
    tiingo_api_key: Optional[str] = None,
) -> Tuple[pd.DataFrame, bytes]:
    if price_source == "nasdaq_price_return":
        return fetch_nasdaq_ohlcv(symbol, start, end)
    try:
        if price_source == "yahoo_chart_adjusted":
            return fetch_yahoo_chart_adjusted_ohlcv(symbol, start, end)
        if price_source == "alpha_vantage_adjusted":
            return fetch_alpha_vantage_adjusted_ohlcv(symbol, start, end, alpha_vantage_api_key or "")
        if price_source == "tiingo_adjusted":
            return fetch_tiingo_adjusted_ohlcv(symbol, start, end, tiingo_api_key or "")
    except Exception:
        if not allow_price_return_fallback:
            raise
        fallback_df, fallback_raw = fetch_nasdaq_ohlcv(symbol, start, end)
        fallback_df.attrs["requested_price_source"] = price_source
        fallback_df.attrs["fallback_reason"] = "adjusted provider unavailable; fell back to Nasdaq price-return"
        return fallback_df, fallback_raw
    choices = ", ".join(sorted(PRICE_SOURCES))
    raise ValueError(f"Invalid price source {price_source!r}; choose one of: {choices}")


def fetch_shiller_cape(start: str, end: str, cape_source: str = "yale_shiller") -> pd.DataFrame:
    if cape_source == "yale_shiller":
        yale = fetch_yale_shiller_cape(start, end)
        stale_cutoff = pd.Timestamp(end) - pd.DateOffset(days=45)
        if not yale.empty and yale.index.max() >= stale_cutoff:
            return yale
        multpl = fetch_multpl_cape(start, end)
        if yale.empty:
            multpl.attrs["cape_source"] = "multpl_fallback_yale_unavailable"
            return multpl
        combined = pd.concat([yale, multpl.loc[multpl.index > yale.index.max()]]).sort_index()
        combined.attrs["cape_source"] = "yale_shiller_primary_multpl_recent_fallback"
        combined.attrs["yale_last_observation"] = str(yale.index.max().date())
        return combined
    if cape_source == "multpl":
        return fetch_multpl_cape(start, end)
    raise ValueError("Invalid CAPE source; choose 'yale_shiller' or 'multpl'")


def load_cape_vintage(vintage_path: str) -> pd.DataFrame:
    path = Path(vintage_path)
    if not path.exists():
        raise FileNotFoundError(f"CAPE vintage file not found: {vintage_path}")
    df = pd.read_csv(path, parse_dates=["observation_month", "available_at"])
    df = df.dropna(subset=["observation_month", "cape", "available_at"])
    df = df.sort_values("available_at")
    out = df[["available_at", "observation_month", "cape"]].copy()
    out = out.rename(columns={"available_at": "date"})
    out = out.set_index("date").sort_index()
    out.attrs["cape_source"] = f"vintage_file:{path.name}"
    out.attrs["cape_vintage_path"] = str(path.resolve())
    out.attrs["cape_uses_available_at_constraint"] = True
    return out
