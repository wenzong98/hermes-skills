#!/usr/bin/env python3
"""
Deflated / Probabilistic Sharpe Ratio utilities.

Implements three things from López de Prado, *Advances in Financial
Machine Learning* (Wiley 2018), Ch. 5 and Ch. 14, plus the underlying
SSRN 2460551 paper. The functions are written in plain numpy / scipy
so the verifier and offline runs do not need a fresh dependency.

Functions
---------
probabilistic_sharpe_ratio
    P(true SR > benchmark SR) under non-Normal returns.
deflated_sharpe_ratio
    As PSR but the benchmark is the *expected maximum* SR over N
    trials, with the *selection-bias-corrected* threshold.
minimum_track_record_length
    The smallest T such that the observed SR is distinguishable from
    benchmark SR with the given skew / kurtosis / alpha.
expected_max_sharpe
    Helper implementing the E[max{SR_n}] formula from SSRN 2460551.
bonferroni_sharpe_threshold
    Helper for the Bonferroni-adjusted SR threshold (1 - alpha/N).
bh_adjusted_sharpes
    Benjamini-Hochberg adjusted p-values for an array of trial SRs.
"""
from __future__ import annotations

import math
from typing import Iterable, List, Sequence

import numpy as np
from scipy import stats


def _normal_cdf(x: float) -> float:
    return float(stats.norm.cdf(x))


def _normal_ppf(p: float) -> float:
    return float(stats.norm.ppf(p))


def _normal_pdf(x: float) -> float:
    return float(stats.norm.pdf(x))


def _annualized_sharpe(daily_returns: np.ndarray, risk_free: float = 0.0,
                      periods_per_year: int = 252) -> float:
    """Annualized Sharpe from a flat array of period returns."""
    r = np.asarray(daily_returns, dtype=float) - risk_free
    if r.size < 2 or r.std(ddof=1) == 0.0:
        return float("nan")
    return float(r.mean() / r.std(ddof=1) * math.sqrt(periods_per_year))


def _moments(daily_returns: np.ndarray) -> tuple[float, float]:
    r = np.asarray(daily_returns, dtype=float)
    if r.size < 4:
        return 0.0, 3.0  # kurtosis 3 = normal reference
    skew = float(stats.skew(r, bias=False))
    kurt = float(stats.kurtosis(r, fisher=True, bias=False)) + 3.0
    return skew, kurt


def _sr_to_per_period(sr_annualized: float, periods_per_year: int = 252) -> float:
    """Convert an annualized Sharpe to per-period Sharpe.

    The canonical DSR / PSR formulas in AFML and SSRN 2460551 use
    per-period SR (the natural unit for the t-statistic). Public
    convention reports annualized SR, so this helper bridges them.
    """
    return float(sr_annualized) / math.sqrt(periods_per_year)


def probabilistic_sharpe_ratio(
    observed_sr: float,
    benchmark_sr: float = 0.0,
    number_of_returns: int | None = None,
    skewness_of_returns: float = 0.0,
    kurtosis_of_returns: float = 3.0,
    periods_per_year: int = 252,
) -> tuple[float, float, float]:
    """
    Probabilistic Sharpe Ratio (PSR).

    Both `observed_sr` and `benchmark_sr` are interpreted as
    *annualized* Sharpe ratios; the function converts to per-period
    internally to match the AFML / SSRN 2460551 form. Returns
    (psr, z_stat, denominator) where psr is the probability that the
    true Sharpe exceeds `benchmark_sr`, given non-Normal moments.
    Mirrors AFML Snippet 5.1 / Equation 5.4.
    """
    if number_of_returns is None or number_of_returns < 2:
        raise ValueError("number_of_returns must be >= 2")
    obs_pp = _sr_to_per_period(observed_sr, periods_per_year)
    bench_pp = _sr_to_per_period(benchmark_sr, periods_per_year)
    denom = math.sqrt(
        1.0
        - skewness_of_returns * obs_pp
        + ((kurtosis_of_returns - 1.0) / 4.0) * obs_pp ** 2
    )
    if denom <= 0:
        return float("nan"), float("nan"), denom
    z = (obs_pp - bench_pp) * math.sqrt(number_of_returns - 1) / denom
    return _normal_cdf(z), z, denom


def expected_max_sharpe(
    number_of_trials: int,
    sample_length: int,
    skewness: float = 0.0,
    kurtosis: float = 3.0,
    periods_per_year: int = 252,
) -> float:
    """
    Approximate E[max{SR_n}] under H0: all trial SRs are i.i.d. with
    mean 0. Returns the *per-period* expected maximum (matching the
    units used in SSRN 2460551, Equation 2.1, and AFML Equation 5.3):

        E[max SR] ≈ E[SR] + sqrt(V[SR]) * [(1-γ) Φ⁻¹(1-1/N)
                                            + γ Φ⁻¹(1-1/(Ne))]

    where γ ≈ 0.5772156649 (Euler-Mascheroni) and
    V[SR] = (1 - γ₃·SR + ((κ-1)/4)·SR²) / (T-1) evaluated at SR=0,
    which collapses to 1/(T-1).
    """
    if number_of_trials < 1:
        raise ValueError("number_of_trials must be >= 1")
    if sample_length < 2:
        raise ValueError("sample_length must be >= 2")

    var_sr = 1.0 / (sample_length - 1)
    sd_sr = math.sqrt(var_sr)
    euler = 0.5772156649015329
    z1 = _normal_ppf(1.0 - 1.0 / number_of_trials)
    z2 = _normal_ppf(1.0 - 1.0 / (number_of_trials * math.e))
    e_max = sd_sr * ((1.0 - euler) * z1 + euler * z2)
    return float(e_max)


def deflated_sharpe_ratio(
    observed_sr: float,
    sr_estimates: Sequence[float] | None,
    number_of_returns: int,
    skewness_of_returns: float = 0.0,
    kurtosis_of_returns: float = 3.0,
    benchmark_out: bool = False,
    periods_per_year: int = 252,
    sr_variance: float | None = None,
) -> dict:
    """
    Deflated Sharpe Ratio (DSR).

    Parameters
    ----------
    observed_sr
        The selected (best-in-search) **annualized** SR.
    sr_estimates
        Optional array of N trial **annualized** SRs. If provided and
        `sr_variance` is None, the empirical sample variance of the
        trials is used. Pass `sr_variance` directly to match the
        AFML/SSRN 2460551 published example, which fixes V[SR_n] under
        H0.
    number_of_returns
        T — sample length of the selected strategy.
    skewness_of_returns, kurtosis_of_returns
        Skew (γ₃) and kurtosis (κ) of the selected strategy's returns.
    benchmark_out
        If True, return benchmark SR alongside the DSR.
    sr_variance
        Optional explicit per-period trial-SR variance. Use this to
        reproduce the published AFML / SSRN 2460551 numerical example
        (where V[SR_n] is fixed at 0.5 under H0). If None and
        `sr_estimates` is provided, V is the empirical sample variance.
        If both None, the analytic H0 V[SR_n] = 1/(T-1) is used.

    Returns
    -------
    dict with keys: dsr, psr, benchmark_sr, z, denom, n_trials
    """
    if sr_estimates is not None and len(sr_estimates) > 0:
        n_trials = len(sr_estimates)
        # Per-period units for trials.
        pp_estimates = [_sr_to_per_period(s, periods_per_year) for s in sr_estimates]
        emp_mean = float(np.mean(pp_estimates))
        if sr_variance is not None:
            emp_var = float(sr_variance)
        else:
            emp_var = float(np.var(pp_estimates, ddof=1)) if n_trials > 1 else 0.0
        if emp_var > 0:
            emp_sd = math.sqrt(emp_var)
            euler = 0.5772156649015329
            z1 = _normal_ppf(1.0 - 1.0 / n_trials)
            z2 = _normal_ppf(1.0 - 1.0 / (n_trials * math.e))
            benchmark_pp = emp_mean + emp_sd * ((1.0 - euler) * z1 + euler * z2)
        else:
            benchmark_pp = emp_mean
        benchmark_sr = benchmark_pp * math.sqrt(periods_per_year)
    else:
        n_trials = 1
        benchmark_sr_pp = expected_max_sharpe(
            n_trials, number_of_returns, skewness_of_returns, kurtosis_of_returns
        )
        benchmark_sr = benchmark_sr_pp * math.sqrt(periods_per_year)

    psr, z, denom = probabilistic_sharpe_ratio(
        observed_sr=observed_sr,
        benchmark_sr=benchmark_sr,
        number_of_returns=number_of_returns,
        skewness_of_returns=skewness_of_returns,
        kurtosis_of_returns=kurtosis_of_returns,
        periods_per_year=periods_per_year,
    )
    return {
        "dsr": float(psr),
        "psr": float(psr),
        "benchmark_sr": float(benchmark_sr),
        "z": float(z),
        "denom": float(denom),
        "n_trials": int(n_trials),
    }


def minimum_track_record_length(
    observed_sr: float,
    benchmark_sr: float = 0.0,
    skewness_of_returns: float = 0.0,
    kurtosis_of_returns: float = 3.0,
    alpha: float = 0.05,
    periods_per_year: int = 252,
) -> float:
    """
    Smallest sample length T such that the observed **annualized** SR
    is statistically distinguishable from `benchmark_sr` at the
    (1 - alpha) confidence level, accounting for non-Normal moments.
    Mirrors AFML Eq. 5.5.
    """
    if alpha <= 0.0 or alpha >= 1.0:
        raise ValueError("alpha must be in (0, 1)")
    obs_pp = _sr_to_per_period(observed_sr, periods_per_year)
    bench_pp = _sr_to_per_period(benchmark_sr, periods_per_year)
    z_alpha = _normal_ppf(1.0 - alpha)
    delta = obs_pp - bench_pp
    if delta == 0:
        return float("inf")
    denom_sq = (
        1.0
        - skewness_of_returns * obs_pp
        + ((kurtosis_of_returns - 1.0) / 4.0) * obs_pp ** 2
    )
    if denom_sq <= 0:
        return float("inf")
    n_min = 1.0 + denom_sq * (z_alpha / delta) ** 2
    return float(n_min)


def bonferroni_sharpe_threshold(
    number_of_trials: int,
    number_of_returns: int,
    alpha: float = 0.05,
) -> float:
    """
    Bonferroni-adjusted Sharpe significance threshold.

    Threshold = Φ⁻¹(1 - α/N) / sqrt(T - 1). If the observed SR is
    below this, the strategy fails the Bonferroni-corrected
    significance test.
    """
    if number_of_trials < 1:
        raise ValueError("number_of_trials must be >= 1")
    z_crit = _normal_ppf(1.0 - alpha / number_of_trials)
    return float(z_crit / math.sqrt(number_of_returns - 1))


def bh_adjusted_sharpes(
    trial_sharpes: Sequence[float],
    number_of_returns: int,
    benchmark_sr: float = 0.0,
) -> List[dict]:
    """
    Benjamini-Hochberg FDR-adjusted one-sided p-values for an array
    of trial annualized SRs against `benchmark_sr`.

    Returns a list of dicts: {sr, p_value, q_value, significant}
    sorted by descending SR.
    """
    n = len(trial_sharpes)
    if n == 0:
        return []
    pairs = [(float(s), i) for i, s in enumerate(trial_sharpes)]
    pairs.sort(key=lambda t: t[0], reverse=True)
    results = [None] * n
    prev_q = 1.0
    for rank_idx, (sr, _) in enumerate(pairs):
        order = rank_idx + 1
        z = (sr - benchmark_sr) * math.sqrt(max(number_of_returns - 1, 1))
        p = 1.0 - _normal_cdf(z)
        raw_q = p * n / order
        q = min(raw_q, prev_q)
        prev_q = q
        results[rank_idx] = {"sr": sr, "p_value": p, "q_value": q, "rank": order}
    # Reorder by original index
    ordered = [None] * n
    for r in results:
        ordered[r["rank"] - 1] = r
    return ordered


def summarize_trials(
    trial_sharpes: Sequence[float],
    number_of_returns: int,
    selected_idx: int = 0,
    skewness_of_returns: float = 0.0,
    kurtosis_of_returns: float = 3.0,
    benchmark_sr: float = 0.0,
    alpha: float = 0.05,
) -> dict:
    """
    One-shot summary suitable for writing to a JSON report.

    Combines Bonferroni, BH-FDR, DSR, PSR, MinTRL into a single dict
    so the calling script can dump it to a report.
    """
    n = len(trial_sharpes)
    if n == 0:
        raise ValueError("trial_sharpes must be non-empty")
    if selected_idx < 0 or selected_idx >= n:
        raise ValueError("selected_idx out of range")
    obs_sr = float(trial_sharpes[selected_idx])

    bonf = bonferroni_sharpe_threshold(n, number_of_returns, alpha=alpha)
    bh = bh_adjusted_sharpes(trial_sharpes, number_of_returns, benchmark_sr=benchmark_sr)
    dsr = deflated_sharpe_ratio(
        observed_sr=obs_sr,
        sr_estimates=trial_sharpes,
        number_of_returns=number_of_returns,
        skewness_of_returns=skewness_of_returns,
        kurtosis_of_returns=kurtosis_of_returns,
    )
    min_trl = minimum_track_record_length(
        observed_sr=obs_sr,
        benchmark_sr=benchmark_sr,
        skewness_of_returns=skewness_of_returns,
        kurtosis_of_returns=kurtosis_of_returns,
        alpha=alpha,
    )
    return {
        "n_trials": n,
        "selected_idx": selected_idx,
        "observed_sr": obs_sr,
        "benchmark_sr": benchmark_sr,
        "bonferroni_threshold_sr": bonf,
        "bonferroni_pass": obs_sr > bonf,
        "bh_top": bh[:5],  # top 5 by SR, with q-values
        "bh_selected": next((b for b in bh if b["rank"] == selected_idx + 1), None),
        "dsr": dsr,
        "min_track_record_length": min_trl,
        "actual_track_length": number_of_returns,
        "min_trl_pass": number_of_returns >= min_trl,
    }


__all__ = [
    "probabilistic_sharpe_ratio",
    "deflated_sharpe_ratio",
    "expected_max_sharpe",
    "minimum_track_record_length",
    "bonferroni_sharpe_threshold",
    "bh_adjusted_sharpes",
    "summarize_trials",
    "_annualized_sharpe",
    "_moments",
]
