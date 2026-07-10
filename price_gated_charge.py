#!/usr/bin/env python3
"""
Price-gated charging loop.

Runs continuously and, for every 15-minute spot price slot (matched by
exact start/end time), starts charging via Tessie if that slot's price is
below --max-price-snt-per-kwh and stops charging if it is not. Once the
battery reaches --max-battery-percent, charging is stopped and the script
exits.

Every action (and each period's decision) is logged to STDOUT and to
log/<script-start-time>.log, e.g. log/2026-07-10T13.50.log.

Required env:
  TESSIE_ACCESS_TOKEN
  TESSIE_VIN
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fetch_current_spot_prices import fetch_spot_prices

HELSINKI = ZoneInfo("Europe/Helsinki")
PROJECT_DIR = Path(__file__).resolve().parent
LOG_DIR = PROJECT_DIR / "log"
TESSIE_BASE_URL = "https://api.tessie.com"
RETRY_SECONDS = 60
CHARGING_STATE_ACTIVE = "Charging"

_log_file: Path | None = None


def log(line: str) -> None:
    ts = datetime.now(HELSINKI).strftime("%Y-%m-%d %H:%M:%S")
    msg = f"[{ts}] {line}"
    print(msg)
    if _log_file is not None:
        with _log_file.open("a", encoding="utf-8") as f:
            f.write(msg + "\n")


def call(path: str, token: str) -> dict:
    req = urllib.request.Request(
        f"{TESSIE_BASE_URL}{path}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def get_battery_status(token: str, vin: str) -> tuple[float | None, str | None]:
    data = call(f"/{vin}/state", token)
    charge = data.get("charge_state", {})
    return charge.get("battery_level"), charge.get("charging_state")


def start_charging(token: str, vin: str) -> None:
    call(f"/{vin}/wake", token)
    call(f"/{vin}/command/start_charging", token)


def stop_charging(token: str, vin: str) -> None:
    call(f"/{vin}/command/stop_charging", token)


def find_current_slot(prices: list[dict], now: datetime) -> dict | None:
    for p in prices:
        if p["start"] <= now < p["end"]:
            return p
    return None


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--max-price-snt-per-kwh", type=float, default=5.0,
        help="Charge only while the current 15-min spot price is below this "
             "(snt/kWh, incl. VAT). Default 5.0.",
    )
    ap.add_argument(
        "--max-battery-percent", type=float, default=80.0,
        help="Stop charging and exit once the battery reaches this level "
             "(0-100). Default 80.",
    )
    return ap.parse_args()


def main() -> int:
    global _log_file

    args = parse_args()

    token = os.environ.get("TESSIE_ACCESS_TOKEN")
    vin = os.environ.get("TESSIE_VIN")
    if not token or not vin:
        print("ERROR: set TESSIE_ACCESS_TOKEN and TESSIE_VIN", file=sys.stderr)
        return 2

    start_time = datetime.now(HELSINKI)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    _log_file = LOG_DIR / f"{start_time:%Y-%m-%dT%H.%M}.log"

    log(
        f"START max_price={args.max_price_snt_per_kwh:.3f}snt/kWh "
        f"max_battery={args.max_battery_percent:.1f}%"
    )

    while True:
        try:
            prices = fetch_spot_prices(include_vat=True)
        except Exception as e:
            log(f"FETCH_FAIL error={e!r}")
            time.sleep(RETRY_SECONDS)
            continue

        now = datetime.now(HELSINKI)
        slot = find_current_slot(prices, now)
        if slot is None:
            log(f"NO_PRICE_DATA time={now:%Y-%m-%d %H:%M}")
            time.sleep(RETRY_SECONDS)
            continue

        try:
            battery_level, charging_state = get_battery_status(token, vin)
        except Exception as e:
            log(f"STATUS_FAIL error={e!r}")
            time.sleep(RETRY_SECONDS)
            continue

        log(
            f"CHECK slot={slot['start']:%H:%M}-{slot['end']:%H:%M} "
            f"price={slot['price']:.3f}snt/kWh battery={battery_level}% "
            f"charging_state={charging_state}"
        )

        if battery_level is not None and battery_level >= args.max_battery_percent:
            if charging_state == CHARGING_STATE_ACTIVE:
                try:
                    stop_charging(token, vin)
                    log(f"STOP_CHARGE reason=battery_full battery={battery_level}%")
                except Exception as e:
                    log(f"STOP_FAIL error={e!r}")
            log(
                f"DONE reason=battery_full battery={battery_level}% "
                f"target={args.max_battery_percent}%"
            )
            return 0

        should_charge = slot["price"] < args.max_price_snt_per_kwh

        if should_charge and charging_state != CHARGING_STATE_ACTIVE:
            try:
                start_charging(token, vin)
                log(
                    f"START_CHARGE slot={slot['start']:%H:%M}-{slot['end']:%H:%M} "
                    f"price={slot['price']:.3f}snt/kWh battery={battery_level}%"
                )
            except Exception as e:
                log(f"START_FAIL error={e!r}")
        elif not should_charge and charging_state == CHARGING_STATE_ACTIVE:
            try:
                stop_charging(token, vin)
                log(
                    f"STOP_CHARGE reason=price_too_high "
                    f"price={slot['price']:.3f}snt/kWh "
                    f"limit={args.max_price_snt_per_kwh:.3f}snt/kWh"
                )
            except Exception as e:
                log(f"STOP_FAIL error={e!r}")
        else:
            log(f"NO_CHANGE should_charge={should_charge} charging_state={charging_state}")

        sleep_seconds = max(1.0, (slot["end"] - datetime.now(HELSINKI)).total_seconds())
        time.sleep(sleep_seconds)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log("INTERRUPTED")
        sys.exit(130)
