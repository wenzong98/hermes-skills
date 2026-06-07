#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
REPORT = ROOT / "signals" / "factor_report.md"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-lookahead", action="store_true")
    args = parser.parse_args()

    if not REPORT.exists():
        subprocess.run([sys.executable, str(ROOT / "scripts" / "factor_signals.py")], check=True)

    errors = []
    text = REPORT.read_text(encoding="utf-8")
    if "IC_IR" not in text:
        errors.append("factor_report.md missing IC_IR")
    if args.check_lookahead and "Lookahead-safe factor shift: `1`" not in text:
        errors.append("factor shift is not documented as 1 trading day")

    if errors:
        print(f"{len(errors)} errors")
        for err in errors:
            print(f"- {err}")
        raise SystemExit(1)

    print("0 errors" if args.check_lookahead else "PASS")


if __name__ == "__main__":
    main()
