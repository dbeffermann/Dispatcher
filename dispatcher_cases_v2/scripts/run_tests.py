"""
CI / pre-release test runner
============================
Executes every test suite under scripts/ and exits with code 0 only when all
suites pass.  Designed to be the single entry-point for automated testing in CI
or as a local pre-release gate.

Usage
-----
    # From the dispatcher_cases_v2/ project root:
    python scripts/run_tests.py               # headless
    python scripts/run_tests.py --headed      # show browser windows

Exit codes
----------
    0  all suites passed
    1  one or more suites reported a failure

Suites executed (in order)
---------------------------
    1. scripts/validate_story.py      — JSON schema + story-graph validation
    2. scripts/test_input_regression.py — input-parity regression (unit-style,
                                          deterministic helpers)
    3. scripts/test_e2e_player.py     — E2E player-perspective tests (no internal
                                          manipulation)
"""

import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
PYTHON      = sys.executable


def run(label: str, script: Path, extra_args: list[str] | None = None) -> int:
    """Run a single script and return its exit code, printing a bordered summary."""
    cmd = [PYTHON, str(script)] + (extra_args or [])
    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"  {' '.join(cmd)}")
    print(f"{'='*60}")
    result = subprocess.run(cmd, check=False)
    return result.returncode


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Dispatcher pre-release test runner")
    parser.add_argument("--headed", action="store_true",
                        help="Pass --headed to Playwright-based suites")
    args = parser.parse_args()

    playwright_args = ["--headed"] if args.headed else []

    suites = [
        ("validate_story      — story graph & schema",
         SCRIPTS_DIR / "validate_story.py",
         []),
        ("test_input_regression — input-parity (deterministic)",
         SCRIPTS_DIR / "test_input_regression.py",
         playwright_args),
        ("test_e2e_player     — E2E player-perspective",
         SCRIPTS_DIR / "test_e2e_player.py",
         playwright_args),
    ]

    results: list[tuple[str, int]] = []
    for label, script, extra in suites:
        if not script.exists():
            print(f"\n[SKIP] {label}  (script not found: {script})")
            continue
        code = run(label, script, extra)
        results.append((label, code))

    # ── Summary ──────────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print("  Pre-release check summary")
    print(f"{'='*60}")
    all_passed = True
    for label, code in results:
        status = "PASS" if code == 0 else "FAIL"
        if code != 0:
            all_passed = False
        print(f"  [{status}] {label}")
    print(f"{'='*60}\n")

    return 0 if all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
