from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch, MagicMock

import numpy as np
import pandas as pd
import pytest


def test_cape_vintage_available_at_constraint() -> None:
    import sys as _sys
    _scripts = str(Path(__file__).resolve().parent.parent / "scripts")
    if _scripts not in _sys.path:
        _sys.path.insert(0, _scripts)
    from data_sources import load_cape_vintage

    tmp = Path(__file__).resolve().parent / "_tmp_cape_test.csv"
    tmp.write_text(
        "observation_month,published_at,available_at,cape,source,source_url,source_sha256,downloaded_at\n"
        "2024-01-01,2024-02-10T00:00:00,2024-02-15,33.5,yale_shiller,http://test,abc123,2024-02-10\n"
        "2024-02-01,2024-03-10T00:00:00,2024-03-15,34.0,yale_shiller,http://test,def456,2024-03-10\n",
        encoding="utf-8",
    )
    try:
        df = load_cape_vintage(str(tmp))
        assert df.attrs.get("cape_uses_available_at_constraint") is True
        assert df.index.name == "date"
        assert len(df) == 2
        first_avail = pd.Timestamp(df.index[0])
        second_avail = pd.Timestamp(df.index[1])
        assert first_avail == pd.Timestamp("2024-02-15")
        assert second_avail == pd.Timestamp("2024-03-15")
    finally:
        tmp.unlink(missing_ok=True)


def test_cape_vintage_forward_fill_respects_available_at() -> None:
    import sys as _sys
    _scripts = str(Path(__file__).resolve().parent.parent / "scripts")
    if _scripts not in _sys.path:
        _sys.path.insert(0, _scripts)
    from backtest_us_etf import prepare_dataset, assert_vintage_constraints

    tmp = Path(__file__).resolve().parent / "_tmp_cape_vint2.csv"
    tmp.write_text(
        "observation_month,published_at,available_at,cape,source,source_url,source_sha256,downloaded_at\n"
        "2023-12-01,2024-01-10T00:00:00,2024-01-12,32.0,yale_shiller,http://test,a1,2024-01-10\n"
        "2024-01-01,2024-02-10T00:00:00,2024-02-14,32.5,yale_shiller,http://test,b2,2024-02-10\n"
        "2024-02-01,2024-03-10T00:00:00,2024-03-13,33.0,yale_shiller,http://test,c3,2024-03-10\n",
        encoding="utf-8",
    )
    try:
        with patch("backtest_us_etf.fetch_etf_ohlcv") as mock_etf, \
             patch("backtest_us_etf.fetch_cboe_vix") as mock_vix:
            dates = pd.bdate_range("2022-01-03", "2024-04-30")
            n = len(dates)
            spy_df = pd.DataFrame({
                "open": np.random.uniform(400, 480, n),
                "high": np.random.uniform(420, 500, n),
                "low": np.random.uniform(390, 450, n),
                "close": np.random.uniform(405, 490, n),
                "volume": np.random.uniform(50e6, 120e6, n).astype(float),
            }, index=dates)
            spy_df.attrs["price_source"] = "yahoo_chart_adjusted"
            spy_df.attrs["price_return_only"] = False
            spy_df.attrs["adjusted_for_dividends"] = True

            qqq_df = pd.DataFrame({
                "open": np.random.uniform(180, 220, n),
                "high": np.random.uniform(190, 230, n),
                "low": np.random.uniform(175, 210, n),
                "close": np.random.uniform(185, 225, n),
                "volume": np.random.uniform(30e6, 80e6, n).astype(float),
            }, index=dates)
            qqq_df.attrs["price_source"] = "yahoo_chart_adjusted"
            qqq_df.attrs["price_return_only"] = False
            qqq_df.attrs["adjusted_for_dividends"] = True

            vix_df = pd.DataFrame({
                "vix": np.random.uniform(12, 35, n).astype(float),
            }, index=dates)

            def side_effect_etf(symbol, *args, **kwargs):
                return spy_df if symbol == "SPY" else qqq_df

            mock_etf.side_effect = side_effect_etf
            mock_vix.return_value = vix_df

            df = prepare_dataset(
                "2023-12-01", "2024-04-30",
                warmup_days=420,
                cape_lag_bdays=10,
                price_source="yahoo_chart_adjusted",
                cape_vintage_path=str(tmp),
            )

            assert df.attrs.get("cape_vintage_path") is not None
            assert "latest_vintage_file_observation_month" in df.attrs
            assert "latest_vintage_file_available_at" in df.attrs

            assert_vintage_constraints(df)

            cape_before_feb14 = df.loc[df.index < pd.Timestamp("2024-02-14"), "cape"]
            cape_after_feb14 = df.loc[df.index >= pd.Timestamp("2024-02-14"), "cape"]

            assert not cape_before_feb14.isna().all(), "CAPE should be available before Feb 14 from Dec observation"
            assert (cape_after_feb14 > 32.0).any(), "CAPE after Feb 14 should include the Jan/Feb observations"
    finally:
        tmp.unlink(missing_ok=True)


def test_cache_manifest_missing_returns_none() -> None:
    import sys as _sys
    _scripts = str(Path(__file__).resolve().parent.parent / "scripts")
    if _scripts not in _sys.path:
        _sys.path.insert(0, _scripts)
    from data_sources import _read_cache

    tmp_dir = Path(__file__).resolve().parent / "_tmp_cache_test"
    tmp_dir.mkdir(exist_ok=True)
    csv_file = tmp_dir / "SPY_nasdaq_price_return_2023-01-01_2023-06-01.csv"
    csv_file.write_text("date,open,high,low,close,volume\n", encoding="utf-8")

    result = _read_cache(tmp_dir, "SPY", "nasdaq_price_return", "2023-01-01", "2023-06-01")
    assert result is None, "Should return None when CSV exists but manifest is missing"

    csv_file.unlink()
    tmp_dir.rmdir()


def test_require_adjusted_blocks_price_return_only() -> None:
    import sys as _sys
    _scripts = str(Path(__file__).resolve().parent.parent / "scripts")
    if _scripts not in _sys.path:
        _sys.path.insert(0, _scripts)

    from backtest_us_etf import prepare_dataset

    with patch("backtest_us_etf.fetch_etf_ohlcv") as mock_etf, \
         patch("backtest_us_etf.fetch_cboe_vix") as mock_vix, \
         patch("backtest_us_etf.fetch_shiller_cape") as mock_cape:
        dates = pd.bdate_range("2023-05-29", "2026-05-29")
        n = len(dates)
        spy_df = pd.DataFrame({
            "open": np.random.uniform(400, 480, n),
            "high": np.random.uniform(420, 500, n),
            "low": np.random.uniform(390, 450, n),
            "close": np.random.uniform(405, 490, n),
            "volume": np.random.uniform(50e6, 120e6, n).astype(float),
        }, index=dates)
        spy_df.attrs["price_source"] = "nasdaq_price_return"
        spy_df.attrs["price_return_only"] = True
        spy_df.attrs["adjusted_for_dividends"] = False

        qqq_df = pd.DataFrame({
            "open": np.random.uniform(180, 220, n),
            "high": np.random.uniform(190, 230, n),
            "low": np.random.uniform(175, 210, n),
            "close": np.random.uniform(185, 225, n),
            "volume": np.random.uniform(30e6, 80e6, n).astype(float),
        }, index=dates)
        qqq_df.attrs["price_source"] = "nasdaq_price_return"
        qqq_df.attrs["price_return_only"] = True
        qqq_df.attrs["adjusted_for_dividends"] = False

        vix_df = pd.DataFrame({"vix": np.random.uniform(12, 35, n).astype(float)}, index=dates)
        cape_df = pd.DataFrame({"cape": np.full(n, 32.0).astype(float)}, index=dates)
        cape_df.attrs["cape_source"] = "yale_shiller"

        def side_effect_etf(symbol, *args, **kwargs):
            return spy_df if symbol == "SPY" else qqq_df

        mock_etf.side_effect = side_effect_etf
        mock_vix.return_value = vix_df
        mock_cape.return_value = cape_df

        with pytest.raises(RuntimeError, match="--require-adjusted is set"):
            prepare_dataset(
                "2023-05-29", "2026-05-29",
                warmup_days=60,
                price_source="nasdaq_price_return",
                require_adjusted=True,
            )


def test_data_sources_has_tiingo_provider() -> None:
    import sys as _sys
    _scripts = str(Path(__file__).resolve().parent.parent / "scripts")
    if _scripts not in _sys.path:
        _sys.path.insert(0, _scripts)
    from data_sources import PRICE_SOURCES
    assert "tiingo_adjusted" in PRICE_SOURCES


def test_data_sources_raw_bytes_saved_in_write_cache() -> None:
    import sys as _sys
    _scripts = str(Path(__file__).resolve().parent.parent / "scripts")
    if _scripts not in _sys.path:
        _sys.path.insert(0, _scripts)
    from data_sources import _write_cache, _cache_paths
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        cache_dir = Path(td)
        df = pd.DataFrame({
            "open": [100.0], "high": [105.0], "low": [98.0], "close": [102.0], "volume": [1e6]
        })
        df.attrs["adjusted_for_dividends"] = True
        df.attrs["price_return_only"] = False
        raw = b'{"raw":"data"}'

        _write_cache(cache_dir, "TEST", "test_source", "2023-01-01", "2023-06-01", df, raw_bytes=raw)

        csv_path, manifest_path, raw_path = _cache_paths(cache_dir, "TEST", "test_source", "2023-01-01", "2023-06-01")

        assert csv_path.exists()
        assert manifest_path.exists()
        assert raw_path.exists()

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["raw_sha256"] is not None
        assert manifest["normalized_sha256"] is not None
        assert manifest["provider"] == "test_source"
        assert manifest["adjusted_for_dividends"] is True
        assert manifest["price_return_only"] is False
