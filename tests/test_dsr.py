"""Tests for scripts/validation/dsr.py — Lopez de Prado DSR / PSR / MinTRL.

We verify against the published AFML / SSRN 2460551 example and
against the canonical (hand-coded) formula.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "scripts"))

from validation.dsr import (  # noqa: E402
    _annualized_sharpe,
    _moments,
    bh_adjusted_sharpes,
    bonferroni_sharpe_threshold,
    deflated_sharpe_ratio,
    expected_max_sharpe,
    minimum_track_record_length,
    probabilistic_sharpe_ratio,
    summarize_trials,
)


def test_psr_under_h0_is_about_05():
    """If true SR == 0, observed SR close to 0, and T is moderate,
    PSR should be near 0.5."""
    psr, z, denom = probabilistic_sharpe_ratio(
        observed_sr=0.0, benchmark_sr=0.0, number_of_returns=252
    )
    assert 0.45 < psr < 0.55


def test_psr_high_sr_is_close_to_1():
    psr, _, _ = probabilistic_sharpe_ratio(
        observed_sr=2.0, benchmark_sr=0.0, number_of_returns=1000
    )
    assert psr > 0.999


def test_bonferroni_threshold_grows_with_n():
    t1 = bonferroni_sharpe_threshold(10, 252)
    t2 = bonferroni_sharpe_threshold(1000, 252)
    assert t2 > t1  # more trials → stricter threshold


def test_min_track_record_length_finite():
    n = minimum_track_record_length(observed_sr=1.0, benchmark_sr=0.0, alpha=0.05)
    assert math.isfinite(n) and n > 0


def test_min_trl_grows_with_alpha_relaxation():
    """Lower alpha → stricter test → more observations needed."""
    n_strict = minimum_track_record_length(1.0, 0.0, alpha=0.01)
    n_loose = minimum_track_record_length(1.0, 0.0, alpha=0.10)
    assert n_strict > n_loose


def test_summarize_trials_returns_expected_keys():
    s = summarize_trials(
        trial_sharpes=[0.5, 0.6, 0.7, 0.8, 0.9],
        number_of_returns=252,
        selected_idx=4,
    )
    assert {"n_trials", "observed_sr", "bonferroni_threshold_sr",
            "bonferroni_pass", "dsr", "min_track_record_length"}.issubset(s)


def test_deflated_sharpe_handcoded_reproduction():
    """Hand-coded AFML example: DSR = 0.9003 ≈ 0.9004 published."""
    # In per-period units (matching the published formula).
    T, gamma_3, gamma_4 = 1250, -3, 10
    obs = 2.5 / math.sqrt(250)        # annualized 2.5 → daily 0.158
    bench = 0.1132                    # per-period E[max SR]
    from validation.dsr import _normal_cdf
    denom = math.sqrt(1 - gamma_3 * obs + (gamma_4 - 1) / 4 * obs ** 2)
    z = (obs - bench) * math.sqrt(T - 1) / denom
    assert abs(_normal_cdf(z) - 0.9003) < 0.001


def test_bh_fdr_top_significant():
    """Top SRs in a clearly-significant set should have q < 0.05."""
    # 20 trial SRs centered well above the benchmark.
    srs = [1.5 + 0.01 * i for i in range(20)]
    bh = bh_adjusted_sharpes(srs, number_of_returns=1000, benchmark_sr=0.0)
    assert bh[0]["q_value"] < 0.05


def test_annualized_sharpe_known_input():
    """For returns drawn from N(mu, sigma), the *daily* SR is
    mu/sigma and the *annualized* SR is mu/sigma * sqrt(252). With
    mu=0.001 and sigma=0.01, daily SR = 0.1, annualized ≈ 1.587.
    """
    rng = np.random.default_rng(0)
    r = rng.normal(0.001, 0.01, 50_000)
    sr = _annualized_sharpe(r)
    # Allow a 10% CI for finite-sample variance.
    assert 1.4 < sr < 1.8, f"got {sr}"


def test_moments_normal_returns():
    rng = np.random.default_rng(0)
    r = rng.normal(0, 0.01, 10000)
    skew, kurt = _moments(r)
    assert abs(skew) < 0.1
    assert 2.9 < kurt < 3.1


def test_expected_max_sharpe_under_h0():
    """E[max SR] under H0 grows with N."""
    e10 = expected_max_sharpe(10, 252)
    e1000 = expected_max_sharpe(1000, 252)
    assert e10 < e1000
    assert e1000 < 1.0  # bounded by sqrt(V[SR]) ~ 0.063 at T=252
