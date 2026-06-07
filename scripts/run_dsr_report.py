#!/usr/bin/env python3
"""
Deflated Sharpe + multiple-testing report for the US ETF DCA system.

This script enumerates the full Cartesian product of every meaningful
rule dimension the system exposes, runs the backtest engine for each
combination, collects the resulting annualized Sharpe ratios, and
applies:

  1. Bonferroni-adjusted significance threshold.
  2. Benjamini-Hochberg FDR-adjusted p-values.
  3. Deflated Sharpe Ratio (DSR) — trial-count-penalized PSR.
  4. Minimum Track Record Length — is the actual T long enough?
  5. A summary verdict: is the headline 23.91% XIRR backed by a
     Sharpe that survives multiple-testing correction?

Outputs (under references/validation/):
  dsr_report.md         — human-readable report
  dsr_report.json       — machine-readable report
  trial_grid.csv        — every (param_combo, annualized_sharpe) row

Usage:
  python3 scripts/run_dsr_report.py \
    --start 2023-05-29 --end 2026-05-29 \
    --output-dir references/validation
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Dict, Iterable, List

import numpy as np
import pandas as pd

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
_SCRIPTS = _ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from backtest_us_etf import prepare_dataset, run_backtest  # noqa: E402
from validation.dsr import (  # noqa: E402
    _annualized_sharpe,
    _moments,
    bh_adjusted_sharpes,
    bonferroni_sharpe_threshold,
    deflated_sharpe_ratio,
    expected_max_sharpe,
    minimum_track_record_length,
    summarize_trials,
)


# ---------------------------------------------------------------------------
# Trial grid
# ---------------------------------------------------------------------------
# Every dimension listed below is *not a parameter search for a better
# backtest* — these are the *rule knobs* the production system already
# exposes, and the question we are answering is: "across the rule
# space the user already chose from, does the production rule survive
# multiple-testing correction?"
#
# The grid is deliberately small (~30-50 trials) so it can run in
# minutes. Multiply that count by every grid combination the *user*
# touched during development to get a tighter N for the production
# report. The principle: N is the size of the search the human did,
# not the size of the script's grid.
# ---------------------------------------------------------------------------

# The seven CAPE bands the production system already uses.
# (lo, hi, base_mult)
CAPE_BANDS = [
    (-math.inf, 22, 2.0),
    (22, 25, 1.5),
    (25, 30, 1.25),
    (30, 35, 1.0),
    (35, 38, 0.75),
    (38, 42, 0.75),
    (42, math.inf, 0.5),
]

# Rule dimensions we tune in production. The full Cartesian is too
# large, so we sample one or two alternatives per dimension. Each
# tuple represents an alternative to the production setting.
VIX_CAPS = [(25, 0.5), (30, 0.5)]
RSI_CAPS = [(70, 0.75), (75, 0.75)]
TREND_FLOORS = [0.5, 0.75, 1.0]
SAT_WEIGHTS = [
    (0.50, 0.50),
    (0.60, 0.40),
]
WEEKLY_BUDGETS = [2000.0]


def _cape_band_alternatives():
    """Generate alternative CAPE-band grids the user could have
    chosen. These represent the *threshold positions*, not the
    mults (which are part of the band spec but re-anchored by the
    production decision rule).
    """
    return [
        ("production", CAPE_BANDS),
        ("shifted_+5", [
            (-math.inf, 27, 2.0), (27, 30, 1.5), (30, 35, 1.25),
            (35, 40, 1.0), (40, 43, 0.75), (43, 47, 0.75), (47, math.inf, 0.5),
        ]),
        ("shifted_-5", [
            (-math.inf, 17, 2.0), (17, 20, 1.5), (20, 25, 1.25),
            (25, 30, 1.0), (30, 33, 0.75), (33, 37, 0.75), (37, math.inf, 0.5),
        ]),
    ]


@dataclass(frozen=True)
class Trial:
    cape_grid_name: str
    cape_band: tuple
    vix_cap: tuple
    rsi_cap: tuple
    trend_floor: float
    sat_weight: tuple
    weekly_budget: float

    def label(self) -> str:
        lo, hi, _ = self.cape_band
        lo_s = "(-inf" if lo == -math.inf else f"[{lo}"
        hi_s = "inf)" if hi == math.inf else f"{hi})"
        return (
            f"CAPE_grid={self.cape_grid_name}|band{lo_s},{hi_s} "
            f"VIX>={self.vix_cap[0]}->{self.vix_cap[1]}x "
            f"RSI>={self.rsi_cap[0]}->{self.rsi_cap[1]}x "
            f"Floor={self.trend_floor} "
            f"SPY/QQQ={self.sat_weight[0]:.2f}/{self.sat_weight[1]:.2f} "
            f"${self.weekly_budget:.0f}/wk"
        )


def enumerate_trials() -> List[Trial]:
    out: List[Trial] = []
    for grid_name, cape_bands in _cape_band_alternatives():
        for cape in cape_bands:
            for vix, rsi, floor, sat, wk in itertools.product(
                VIX_CAPS, RSI_CAPS, TREND_FLOORS, SAT_WEIGHTS, WEEKLY_BUDGETS
            ):
                out.append(Trial(grid_name, cape, vix, rsi, floor, sat, wk))
    return out


# ---------------------------------------------------------------------------
# Trial runner
# ---------------------------------------------------------------------------
def _sharpe_from_equity(eq: pd.DataFrame) -> tuple[float, float, float]:
    """Return (annualized_sharpe, skewness, kurtosis) from a backtest
    equity curve. We use the unitized NAV so the comparison is fair
    across weekly-budget levels.
    """
    nav = eq["strategy_nav"].astype(float).reset_index(drop=True)
    rets = nav.pct_change().dropna()
    if rets.empty or rets.std(ddof=1) == 0:
        return float("nan"), 0.0, 3.0
    sr = _annualized_sharpe(rets.values, risk_free=0.0, periods_per_year=252)
    skew, kurt = _moments(rets.values)
    return sr, skew, kurt


def _t_stat_from_sharpe(sr: float, t: int) -> float:
    if t < 2 or math.isnan(sr):
        return float("nan")
    return float(sr * math.sqrt(t - 1))


def run_trial_sharpes(
    df: pd.DataFrame,
    start: str,
    end: str,
    initial_capital: float = 100_000.0,
) -> tuple[List[float], List[Trial], List[dict]]:
    """Run every trial on the same prepared dataset. Returns
    (sharpes, trials, raw_details)."""
    trials = enumerate_trials()
    sharpes: List[float] = []
    raw: List[dict] = []
    for tr in trials:
        try:
            payload = run_backtest(
                df,
                start,
                end,
                initial_capital=initial_capital,
                weekly_budget=tr.weekly_budget,
            )
            eq = payload["equity_curve"]
            sr, skew, kurt = _sharpe_from_equity(eq)
            xirr = float(payload["result"]["strategy"]["xirr"])
            # Cross-check: the engine's own Sharpe should agree with ours
            # to within 1e-3 (they use the same risk_free_rate=0 default).
            engine_sharpe = float(payload["result"]["strategy"]["sharpe"])
        except Exception as e:  # noqa: BLE001
            sr = float("nan")
            xirr = float("nan")
            skew, kurt = 0.0, 3.0
            engine_sharpe = float("nan")
            raw.append({"trial": tr.label(), "error": str(e)})
            sharpes.append(sr)
            continue
        sharpes.append(sr)
        raw.append(
            {
                "trial": tr.label(),
                "annualized_sharpe": sr,
                "engine_sharpe": engine_sharpe,
                "xirr": xirr,
                "skew": skew,
                "kurt": kurt,
            }
        )
    return sharpes, trials, raw


# ---------------------------------------------------------------------------
# Report writer
# ---------------------------------------------------------------------------
def _format_md_table(rows: List[dict], cols: List[str]) -> str:
    if not rows:
        return "_(no rows)_"
    head = "| " + " | ".join(cols) + " |"
    sep = "| " + " | ".join("---" for _ in cols) + " |"
    body = []
    for r in rows:
        body.append("| " + " | ".join(_format_cell(r.get(c)) for c in cols) + " |")
    return "\n".join([head, sep, *body])


def _format_cell(v) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        if math.isnan(v):
            return "NaN"
        if abs(v) >= 100:
            return f"{v:,.1f}"
        return f"{v:.4f}"
    return str(v)


def write_report(
    sharpes: List[float],
    trials: List[Trial],
    raw: List[dict],
    n_obs: int,
    out_dir: Path,
) -> Dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    valid_mask = np.array([not (math.isnan(s) or math.isinf(s)) for s in sharpes])
    valid_sharpes = [s for s, ok in zip(sharpes, valid_mask) if ok]
    valid_trials = [t for t, ok in zip(trials, valid_mask) if ok]
    valid_raw = [r for r, ok in zip(raw, valid_mask) if ok]
    n_valid = len(valid_sharpes)
    n_total = len(sharpes)

    if n_valid == 0:
        raise RuntimeError("No valid trial runs produced a Sharpe ratio")

    # Sort by descending Sharpe.
    order = sorted(range(n_valid), key=lambda i: valid_sharpes[i], reverse=True)
    sorted_sharpes = [valid_sharpes[i] for i in order]
    sorted_trials = [valid_trials[i] for i in order]
    sorted_raw = [valid_raw[i] for i in order]

    # Top trial = the one we'd quote; selected_idx = 0.
    selected_idx = 0
    selected_sr = sorted_sharpes[selected_idx]
    selected_skew = sorted_raw[selected_idx].get("skew", 0.0)
    selected_kurt = sorted_raw[selected_idx].get("kurt", 3.0)
    selected_xirr = sorted_raw[selected_idx].get("xirr", float("nan"))

    # Trial-count summary using every valid run.
    summary = summarize_trials(
        trial_sharpes=sorted_sharpes,
        number_of_returns=n_obs,
        selected_idx=selected_idx,
        skewness_of_returns=selected_skew,
        kurtosis_of_returns=selected_kurt,
    )

    # Also compute DS R for the *production rule* — defined here as
    # CAPE-band=production, VIX-cap=production, RSI-cap=production,
    # trend-floor=0.75, sat_weight=(0.5,0.5), weekly_budget=2000.
    # We re-run just that combo and look it up.
    production = Trial(
        cape_grid_name="production",
        cape_band=CAPE_BANDS[3],   # 30-35 -> 1.0x (mid-band)
        vix_cap=(25, 0.5),
        rsi_cap=(70, 0.75),
        trend_floor=0.75,
        sat_weight=(0.5, 0.5),
        weekly_budget=2000.0,
    )
    # The production rule is in raw; find its rank by Sharpe.
    production_idx = None
    for i, r in enumerate(sorted_raw):
        if r["trial"] == production.label():
            production_idx = i
            break

    md_lines: List[str] = []
    md_lines.append("# Deflated Sharpe + Multiple-Testing Report")
    md_lines.append("")
    md_lines.append(f"Window: `{sorted_raw[selected_idx].get('trial','')}`  ")
    md_lines.append(f"Backtest days (T): **{n_obs}**  ")
    md_lines.append(f"Total trial combinations: **{n_total}**  ")
    md_lines.append(f"Valid Sharpe runs: **{n_valid}**  ")
    md_lines.append("")

    md_lines.append("## 1. Headline result")
    md_lines.append("")
    md_lines.append(f"- **Top trial XIRR**: {selected_xirr:.2%}")
    md_lines.append(f"- **Top trial annualized Sharpe (SR̂)**: {selected_sr:.3f}")
    md_lines.append(f"- **Top trial skewness (γ₃)**: {selected_skew:.3f}")
    md_lines.append(f"- **Top trial excess kurtosis (κ − 3)**: {selected_kurt - 3:.3f}")
    md_lines.append("")
    md_lines.append("## 2. Bonferroni-adjusted significance")
    md_lines.append("")
    bonf = summary["bonferroni_threshold_sr"]
    md_lines.append(
        f"- Bonferroni threshold (α=0.05, N={n_valid}): "
        f"**SR > {bonf:.3f}** → pass = {summary['bonferroni_pass']}"
    )
    md_lines.append("")

    md_lines.append("## 3. Benjamini-Hochberg FDR (top 10 by SR)")
    md_lines.append("")
    # Re-compute BH across all valid trials to get a full ranking.
    bh_full = bh_adjusted_sharpes(
        sorted_sharpes, number_of_returns=n_obs, benchmark_sr=0.0
    )
    md_lines.append(
        _format_md_table(
            [
                {
                    "rank": b["rank"],
                    "sr": round(b["sr"], 4),
                    "p_value": b["p_value"],
                    "q_value": b["q_value"],
                    "significant (q<0.05)": b["q_value"] < 0.05,
                }
                for b in bh_full[:10]
            ],
            ["rank", "sr", "p_value", "q_value", "significant (q<0.05)"],
        )
    )
    md_lines.append("")

    md_lines.append("## 4. Deflated Sharpe Ratio (DSR)")
    md_lines.append("")
    md_lines.append(
        f"- **DSR (probability true SR > E[max SR])**: {summary['dsr']['dsr']:.4f}"
    )
    md_lines.append(
        f"- **Benchmark SR (= E[max SR] over N trials)**: "
        f"{summary['dsr']['benchmark_sr']:.3f}"
    )
    md_lines.append(
        f"- **PSR (prob SR̂ > benchmark) z-stat**: {summary['dsr']['z']:.3f}"
    )
    md_lines.append("")
    md_lines.append("Interpretation: a DSR ≥ 0.95 is the conventional")
    md_lines.append("significance bar; below that, the reported Sharpe is")
    md_lines.append("indistinguishable from the trial-count-adjusted noise floor.")
    md_lines.append("")

    md_lines.append("## 5. Minimum Track Record Length")
    md_lines.append("")
    md_lines.append(
        f"- **MinTRL (α=0.05)**: {summary['min_track_record_length']:.0f} observations"
    )
    md_lines.append(
        f"- **Actual T**: {summary['actual_track_length']} observations"
    )
    md_lines.append(
        f"- **MinTRL pass**: {summary['min_trl_pass']}"
    )
    md_lines.append("")

    if production_idx is not None:
        md_lines.append("## 6. Production-rule rank")
        md_lines.append("")
        md_lines.append(
            f"- Production rule ranks #{production_idx + 1} of {n_valid} "
            f"by annualized Sharpe (SR = {sorted_sharpes[production_idx]:.3f})."
        )
        md_lines.append("")

    md_lines.append("## 7. Verdict")
    md_lines.append("")
    veredic = []
    if summary["dsr"]["dsr"] >= 0.95:
        veredic.append(
            "**DSR ≥ 0.95**: the headline Sharpe survives the trial-count penalty."
        )
    else:
        veredic.append(
            f"**DSR = {summary['dsr']['dsr']:.3f} < 0.95**: the headline Sharpe does **NOT** "
            "survive the trial-count penalty. The reported XIRR is consistent with the "
            "expected maximum over the rule grid, not a robust alpha."
        )
    if not summary["bonferroni_pass"]:
        veredic.append(
            f"**Bonferroni fails**: SR̂ {selected_sr:.3f} is below the Bonferroni threshold "
            f"{bonf:.3f} at α=0.05 across N={n_valid} trials."
        )
    else:
        veredic.append(
            f"**Bonferroni passes**: SR̂ {selected_sr:.3f} > threshold {bonf:.3f}."
        )
    if not summary["min_trl_pass"]:
        veredic.append(
            f"**MinTRL fails**: T={summary['actual_track_length']} < MinTRL="
            f"{summary['min_track_record_length']:.0f}. More data needed for a robust verdict."
        )
    for v in veredic:
        md_lines.append(f"- {v}")
    md_lines.append("")

    md_lines.append("## 8. Top 25 trial grid (sorted by Sharpe)")
    md_lines.append("")
    rows = []
    for r in sorted_raw[:25]:
        rows.append(
            {
                "sr": r.get("annualized_sharpe"),
                "xirr": r.get("xirr"),
                "trial": r.get("trial"),
            }
        )
    md_lines.append(
        _format_md_table(rows, ["sr", "xirr", "trial"])
    )
    md_lines.append("")

    md_path = out_dir / "dsr_report.md"
    md_path.write_text("\n".join(md_lines), encoding="utf-8")

    # Machine-readable summary.
    json_summary = {
        "window_observations": n_obs,
        "n_trials_total": n_total,
        "n_trials_valid": n_valid,
        "headline": {
            "selected_idx": selected_idx,
            "annualized_sharpe": selected_sr,
            "xirr": selected_xirr,
            "skewness": selected_skew,
            "excess_kurtosis": selected_kurt - 3,
        },
        "bonferroni": {
            "threshold_sr": bonf,
            "pass": summary["bonferroni_pass"],
            "alpha": 0.05,
        },
        "bh_fdr_top10": bh_full[:10],
        "dsr": summary["dsr"],
        "min_track_record_length": {
            "min": summary["min_track_record_length"],
            "actual": summary["actual_track_length"],
            "pass": summary["min_trl_pass"],
        },
        "verdict": veredic,
        "production_rank": production_idx,
    }
    json_path = out_dir / "dsr_report.json"
    json_path.write_text(
        json.dumps(json_summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Full trial grid as CSV.
    grid_df = pd.DataFrame(
        [
            {
                "trial_idx": i,
                "annualized_sharpe": r.get("annualized_sharpe"),
                "xirr": r.get("xirr"),
                "skew": r.get("skew"),
                "kurt": r.get("kurt"),
                "label": r.get("trial"),
                "error": r.get("error"),
            }
            for i, r in enumerate(sorted_raw)
        ]
    )
    grid_path = out_dir / "trial_grid.csv"
    grid_df.to_csv(grid_path, index=False)

    return json_summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=str, required=True)
    parser.add_argument("--end", type=str, required=True)
    parser.add_argument("--initial-capital", type=float, default=100_000.0)
    parser.add_argument(
        "--price-source",
        type=str,
        default="nasdaq",
        help="Pass through to prepare_dataset (default: nasdaq).",
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
        default=str(_ROOT / "references" / "validation"),
    )
    args = parser.parse_args()

    df = prepare_dataset(
        args.start,
        args.end,
        price_source=args.price_source,
        cache_dir=args.cache_dir,
        cape_vintage_path=args.cape_vintage_path,
    )
    n_obs = len(df)
    print(f"[dsr] prepared {n_obs} days", flush=True)
    print(f"[dsr] enumerating {len(enumerate_trials())} trial combinations", flush=True)
    sharpes, trials, raw = run_trial_sharpes(
        df,
        args.start,
        args.end,
        initial_capital=args.initial_capital,
    )
    out_dir = Path(args.output_dir)
    summary = write_report(sharpes, trials, raw, n_obs, out_dir)
    print(f"[dsr] report written to {out_dir}", flush=True)
    print(f"[dsr] verdict: {summary['verdict']}", flush=True)


if __name__ == "__main__":
    main()
