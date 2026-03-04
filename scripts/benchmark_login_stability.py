import argparse
import os
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPT_SELENIUM = os.path.join(REPO_ROOT, "scripts", "local_login_check.py")
SCRIPT_PLAYWRIGHT = os.path.join(REPO_ROOT, "scripts", "local_login_check_playwright.py")


@dataclass
class RunResult:
    ok: bool
    exit_code: int
    elapsed_seconds: float
    stdout_tail: str


def _run_once(script_path: str, common_args, timeout_seconds: int) -> RunResult:
    cmd = [sys.executable, script_path] + common_args
    start = time.perf_counter()
    try:
        cp = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            cwd=REPO_ROOT,
        )
        elapsed = time.perf_counter() - start
        out = (cp.stdout or "").strip().splitlines()
        tail = "\n".join(out[-8:]) if out else ""
        return RunResult(ok=cp.returncode == 0, exit_code=cp.returncode, elapsed_seconds=elapsed, stdout_tail=tail)
    except subprocess.TimeoutExpired:
        elapsed = time.perf_counter() - start
        return RunResult(ok=False, exit_code=124, elapsed_seconds=elapsed, stdout_tail="timeout")


def _summary(name: str, results):
    total = len(results)
    success = sum(1 for x in results if x.ok)
    failures = total - success
    success_rate = (success / total * 100.0) if total else 0.0
    elapsed = [x.elapsed_seconds for x in results]
    avg = statistics.mean(elapsed) if elapsed else 0.0
    p95 = statistics.quantiles(elapsed, n=20)[18] if len(elapsed) >= 2 else (elapsed[0] if elapsed else 0.0)

    print(f"\n=== {name} ===")
    print(f"runs={total} success={success} fail={failures} success_rate={success_rate:.1f}%")
    print(f"avg={avg:.2f}s p95~={p95:.2f}s")
    if failures:
        print("latest failure tail:")
        for r in reversed(results):
            if not r.ok:
                print(r.stdout_tail or "(no stdout)")
                break


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Benchmark login stability between Selenium and Playwright login-check scripts."
    )
    parser.add_argument("--runs", type=int, default=5, help="Number of runs per implementation.")
    parser.add_argument("--delay-seconds", type=float, default=1.0, help="Delay between runs.")
    parser.add_argument("--timeout-seconds", type=int, default=180, help="Per-run timeout.")
    parser.add_argument("--secrets-toml", default="", help="Optional secrets toml path forwarded to child scripts.")
    parser.add_argument("--headed", action="store_true", help="Forward headed mode to child scripts.")
    parser.add_argument("--selenium-only", action="store_true", help="Only benchmark Selenium script.")
    parser.add_argument("--playwright-only", action="store_true", help="Only benchmark Playwright script.")
    args = parser.parse_args(argv)

    if args.selenium_only and args.playwright_only:
        print("Cannot set both --selenium-only and --playwright-only.")
        return 2
    if args.runs <= 0:
        print("--runs must be > 0")
        return 2

    common_args = []
    if args.secrets_toml:
        common_args.extend(["--secrets-toml", args.secrets_toml])
    if args.headed:
        common_args.append("--headed")

    tasks = []
    if not args.playwright_only:
        tasks.append(("Selenium", SCRIPT_SELENIUM, common_args))
    if not args.selenium_only:
        tasks.append(("Playwright", SCRIPT_PLAYWRIGHT, common_args))

    final_code = 0
    for name, script, base_args in tasks:
        if not os.path.exists(script):
            print(f"Script not found: {script}")
            final_code = 1
            continue

        print(f"\nRunning {name} benchmark ({args.runs} runs)...")
        results = []
        for i in range(1, args.runs + 1):
            print(f"[{name}] run {i}/{args.runs}")
            r = _run_once(script, base_args, args.timeout_seconds)
            results.append(r)
            mark = "OK" if r.ok else "FAIL"
            print(f"[{name}] {mark} exit={r.exit_code} elapsed={r.elapsed_seconds:.2f}s")
            if i < args.runs and args.delay_seconds > 0:
                time.sleep(args.delay_seconds)

        _summary(name, results)
        if not all(x.ok for x in results):
            final_code = 1

    return final_code


if __name__ == "__main__":
    raise SystemExit(main())
