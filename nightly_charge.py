#!/usr/bin/env python3
"""
Nightly charging orchestrator.

Fetches today/tomorrow spot prices, picks the cheapest contiguous charging
window ending by --latest-end on the target morning, and schedules
`start_charge.py` to run at that time via `at`.

Designed to run from cron once or twice per evening (e.g. 17:00 and 23:00).
The 23:00 run cancels any job queued by the 17:00 run before scheduling a
fresh one, so a late price update can reshuffle the plan.

Logs to charging.log:
  PLAN       — every run, what the cheapest window looks like
  SCHEDULED  — when an at-job has been queued
  START      — when the at-job fires (avg price + total cost baked in)
  SKIP/*FAIL — error / no-op paths

Required env (read at at-job fire time, inherited from the cron environment
that schedules the job):
  TESSIE_ACCESS_TOKEN
  TESSIE_VIN
"""

from __future__ import annotations

import argparse
import os
import shlex
import shutil
import subprocess
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from fetch_current_spot_prices import fetch_spot_prices, load_from_json
from min_cost import build_night_window, find_cheapest_window

HELSINKI = ZoneInfo("Europe/Helsinki")
PROJECT_DIR = Path(__file__).resolve().parent
LOG_FILE = PROJECT_DIR / "charging.log"
STDOUT_LOG = PROJECT_DIR / "charging.stdout.log"
AT_QUEUE = "n"


def log(line: str) -> None:
    ts = datetime.now(HELSINKI).strftime("%Y-%m-%d %H:%M:%S")
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(f"[{ts}] {line}\n")


def parse_hhmm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def cancel_existing_jobs() -> int:
    out = subprocess.run(
        ["atq", "-q", AT_QUEUE], capture_output=True, text=True, check=False
    )
    job_ids = [ln.split()[0] for ln in out.stdout.splitlines() if ln.strip()]
    for jid in job_ids:
        subprocess.run(["atrm", jid], check=False)
    return len(job_ids)


def schedule_at(when: datetime, script_body: str) -> str:
    """Queue script_body via `at` at the given local time. Returns job id."""
    res = subprocess.run(
        ["at", "-q", AT_QUEUE, "-t", when.strftime("%Y%m%d%H%M")],
        input=script_body,
        text=True,
        capture_output=True,
        check=True,
    )
    # `at` reports e.g. "job 7 at Fri May 30 03:30:00 2026" on stderr.
    for tok in res.stderr.split():
        if tok.isdigit():
            return tok
    return ""


def build_at_job(plan: dict, hours: float) -> str:
    """The shell script that the at-job will execute when it fires."""
    start_dt = plan["start"]
    end_dt = plan["end"]
    avg = plan["avg_price_snt_per_kwh"]
    energy = plan["energy_kwh"]
    cost_eur = plan["total_cost_eur"]

    start_log = (
        f"START start={start_dt:%Y-%m-%d %H:%M} end={end_dt:%Y-%m-%d %H:%M} "
        f"hours={hours} avg={avg:.3f}snt/kWh energy={energy:.2f}kWh "
        f"cost={cost_eur:.3f}EUR"
    )
    fail_log = "START_FAIL exit=$rc"

    py = shlex.quote(sys.executable)
    project = shlex.quote(str(PROJECT_DIR))
    start_script = shlex.quote(str(PROJECT_DIR / "start_charge.py"))
    log_file = shlex.quote(str(LOG_FILE))
    stdout_log = shlex.quote(str(STDOUT_LOG))

    # Note: env vars (TESSIE_ACCESS_TOKEN, TESSIE_VIN) are inherited from the
    # process that ran `at`. Cron entries must therefore set them before
    # invoking this orchestrator.
    return (
        f"cd {project}\n"
        f"ts=$(date '+%Y-%m-%d %H:%M:%S')\n"
        f"echo \"[$ts] {start_log}\" >> {log_file}\n"
        f"{py} {start_script} >> {stdout_log} 2>&1\n"
        f"rc=$?\n"
        f"if [ $rc -ne 0 ]; then\n"
        f"  ts=$(date '+%Y-%m-%d %H:%M:%S')\n"
        f"  echo \"[$ts] {fail_log}\" >> {log_file}\n"
        f"fi\n"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--hours", type=float, default=5.0,
                    help="Charging duration (h). Default 5.")
    ap.add_argument("--latest-end", type=parse_hhmm, default=time(6, 0),
                    help="Latest allowed end time on target date. Default 06:00.")
    ap.add_argument("--charging-power-kw", type=float, default=11.0,
                    help="Effective charging power kW. Default 11.")
    ap.add_argument("--date", type=str, default=None,
                    help="Target morning date (ISO). Default: tomorrow (Helsinki).")
    ap.add_argument("--prices", type=str, default=None,
                    help="Cached prices JSON (skips API call). For testing.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Compute and log the plan but do not schedule the at-job.")
    args = ap.parse_args()

    target_date = (
        date.fromisoformat(args.date) if args.date
        else datetime.now(HELSINKI).date() + timedelta(days=1)
    )
    win_start, win_end = build_night_window(target_date, args.latest_end)

    try:
        prices = (
            load_from_json(args.prices) if args.prices
            else fetch_spot_prices(include_vat=True)
        )
    except Exception as e:
        log(f"FETCH_FAIL target={target_date} error={e!r}")
        print(f"ERROR fetching prices: {e}", file=sys.stderr)
        return 1

    try:
        plan = find_cheapest_window(
            prices=prices,
            charge_hours=args.hours,
            window_start=win_start,
            window_end=win_end,
            charging_power_kw=args.charging_power_kw,
        )
    except ValueError as e:
        log(f"PLAN_FAIL target={target_date} error={e}")
        print(f"ERROR planning: {e}", file=sys.stderr)
        return 1

    start_dt = plan["start"]
    end_dt = plan["end"]
    avg = plan["avg_price_snt_per_kwh"]
    energy = plan["energy_kwh"]
    cost_eur = plan["total_cost_eur"]
    now = datetime.now(HELSINKI)

    log(
        f"PLAN target={target_date} start={start_dt:%Y-%m-%d %H:%M} "
        f"end={end_dt:%Y-%m-%d %H:%M} hours={args.hours} "
        f"avg={avg:.3f}snt/kWh energy={energy:.2f}kWh cost={cost_eur:.3f}EUR"
    )
    print(
        f"Plan: start {start_dt:%Y-%m-%d %H:%M}, "
        f"end {end_dt:%Y-%m-%d %H:%M}, "
        f"avg {avg:.3f} snt/kWh, total {cost_eur:.3f} EUR"
    )

    if start_dt <= now:
        log(f"SKIP reason=window_in_past start={start_dt:%Y-%m-%d %H:%M}")
        print(f"Cheapest window already started ({start_dt:%H:%M}); not scheduling.")
        return 0

    if args.dry_run:
        print("Dry run: not scheduling at-job.")
        return 0

    if not shutil.which("at"):
        log("SCHEDULE_FAIL reason=at_missing")
        print("ERROR: `at` not installed. Install with: sudo apt-get install at",
              file=sys.stderr)
        return 2

    if not (os.environ.get("TESSIE_ACCESS_TOKEN") and os.environ.get("TESSIE_VIN")):
        # Warn — at-job inherits this process's env, so missing vars now means
        # the job will fail when it fires.
        log("WARN reason=tessie_env_missing")
        print("WARN: TESSIE_ACCESS_TOKEN / TESSIE_VIN not set; the at-job "
              "will fail to start charging.", file=sys.stderr)

    cancelled = cancel_existing_jobs()
    if cancelled:
        log(f"CANCELLED prior_jobs={cancelled}")

    body = build_at_job(plan, args.hours)
    try:
        job_id = schedule_at(start_dt, body)
    except subprocess.CalledProcessError as e:
        log(f"SCHEDULE_FAIL error={(e.stderr or '').strip()}")
        print(f"ERROR scheduling at-job: {e.stderr}", file=sys.stderr)
        return 1

    log(f"SCHEDULED at={start_dt:%Y-%m-%d %H:%M} job={job_id or '?'}")
    print(f"Scheduled at-job {job_id or '?'} for {start_dt:%Y-%m-%d %H:%M}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
