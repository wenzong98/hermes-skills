#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
RESULT = ROOT / "results" / "optimal_params.json"


def main() -> None:
    if not RESULT.exists():
        subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "optimize_params.py"), "--output-dir", str(ROOT / "results")],
            check=True,
        )

    payload = json.loads(RESULT.read_text(encoding="utf-8"))
    failures = []
    if int(payload.get("tested_combinations", 0)) < 200:
        failures.append("tested_combinations < 200")
    for field in ("sharpe", "maxdd", "cagr"):
        if field not in payload:
            failures.append(f"missing {field}")
    if payload.get("sharpe", 0.0) <= 1.2:
        failures.append("sharpe <= 1.2")
    if payload.get("maxdd", -1.0) <= -0.18:
        failures.append("maxdd <= -18%")
    if payload.get("cagr", 0.0) <= 0.12:
        failures.append("cagr <= 12%")
    if payload.get("win_rate", 0.0) <= 0.52:
        failures.append("win_rate <= 52%")

    if failures:
        print("FAIL")
        print(json.dumps({"failures": failures, "metrics": {k: payload.get(k) for k in ("sharpe", "maxdd", "cagr", "win_rate")}}, indent=2))
        raise SystemExit(1)

    print("PASS")


if __name__ == "__main__":
    main()
