from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd


def _import_update():
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import update_cape_snapshot  # noqa: E402
    return update_cape_snapshot


def test_build_vintage_clamps_available_at_to_downloaded_at() -> None:
    """Case A: downloaded_at < obs + 10 BDay lag.

    Observation 2024-06-01 was downloaded on 2024-06-10 (before the 10-BDay
    publisher lag would have ended around 2024-06-14). The original code
    emits available_at = 2024-06-14, which is in the future relative to
    downloaded_at = 2024-06-10, i.e., a future-dated vintage row.

    The clamp fix must set available_at = min(obs + 10 BDay, downloaded_at)
    so the row is PIT-correct.
    """
    mod = _import_update()
    yale_df = pd.DataFrame([{
        "observation_month": pd.Timestamp("2024-06-01"),
        "cape": 35.0,
        "source": "yale_shiller_ie_data",
        "source_url": "https://www.econ.yale.edu/~shiller/data/ie_data.xls",
        "source_sha256": "abc",
        "downloaded_at": "2024-06-10T00:00:00",
    }])

    vintage = mod.build_vintage(yale_df, None)

    assert len(vintage) == 1
    row = vintage.iloc[0]
    assert pd.Timestamp(row["available_at"]) == pd.Timestamp("2024-06-10"), (
        f"available_at should be clamped to downloaded_at (2024-06-10), got {row['available_at']}"
    )
    assert pd.Timestamp(row["available_at"]) <= pd.Timestamp(row["downloaded_at"]), (
        f"available_at ({row['available_at']}) must be <= downloaded_at ({row['downloaded_at']})"
    )


def test_build_vintage_keeps_lag_when_lag_ends_after_download() -> None:
    """Case B: downloaded_at > obs + 10 BDay lag.

    Observation 2024-01-01 was downloaded on 2024-01-30 (well after the
    publisher lag would have ended around 2024-01-15). available_at should
    equal the lag-based date (~2024-01-15), not downloaded_at.
    """
    mod = _import_update()
    yale_df = pd.DataFrame([{
        "observation_month": pd.Timestamp("2024-01-01"),
        "cape": 32.0,
        "source": "yale_shiller_ie_data",
        "source_url": "https://www.econ.yale.edu/~shiller/data/ie_data.xls",
        "source_sha256": "abc",
        "downloaded_at": "2024-01-30T00:00:00",
    }])

    vintage = mod.build_vintage(yale_df, None)

    assert len(vintage) == 1
    row = vintage.iloc[0]
    expected = mod.compute_available_at(pd.Timestamp("2024-01-01"))
    assert pd.Timestamp(row["available_at"]) == expected, (
        f"available_at should equal lag-based date ({expected.date()}), got {row['available_at']}"
    )
    assert pd.Timestamp(row["available_at"]) <= pd.Timestamp(row["downloaded_at"]), (
        f"available_at ({row['available_at']}) must be <= downloaded_at ({row['downloaded_at']})"
    )


def test_build_vintage_no_row_has_available_at_after_downloaded_at() -> None:
    """Across a mixed batch, no output row may have available_at > downloaded_at.

    Combines Case A (clamps down) and Case B (lag-based date already < download)
    and asserts the invariant on the full output frame.
    """
    mod = _import_update()
    yale_df = pd.DataFrame([
        {
            "observation_month": pd.Timestamp("2024-06-01"),
            "cape": 35.0,
            "source": "yale_shiller_ie_data",
            "source_url": "https://www.econ.yale.edu/~shiller/data/ie_data.xls",
            "source_sha256": "a1",
            "downloaded_at": "2024-06-10T00:00:00",
        },
        {
            "observation_month": pd.Timestamp("2024-01-01"),
            "cape": 32.0,
            "source": "yale_shiller_ie_data",
            "source_url": "https://www.econ.yale.edu/~shiller/data/ie_data.xls",
            "source_sha256": "b2",
            "downloaded_at": "2024-01-30T00:00:00",
        },
        {
            "observation_month": pd.Timestamp("2023-12-01"),
            "cape": 30.0,
            "source": "yale_shiller_ie_data",
            "source_url": "https://www.econ.yale.edu/~shiller/data/ie_data.xls",
            "source_sha256": "c3",
            "downloaded_at": "2023-12-15T00:00:00",
        },
    ])

    vintage = mod.build_vintage(yale_df, None)
    available = pd.to_datetime(vintage["available_at"])
    downloaded = pd.to_datetime(vintage["downloaded_at"])
    bad = vintage[available > downloaded]
    assert bad.empty, f"Found future-dated vintage rows: {bad.to_dict(orient='records')}"
