#!/usr/bin/env python3
"""Print current Tesla charging status via Tessie."""

from __future__ import annotations

import json
import os
import sys
import urllib.request


TESSIE_BASE_URL = "https://api.tessie.com"


def main() -> int:
    token = os.environ.get("TESSIE_ACCESS_TOKEN")
    vin = os.environ.get("TESSIE_VIN")
    if not token or not vin:
        print("ERROR: set TESSIE_ACCESS_TOKEN and TESSIE_VIN", file=sys.stderr)
        return 2

    req = urllib.request.Request(
        f"{TESSIE_BASE_URL}/{vin}/state",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode())

    charge = data.get("charge_state", {})
    summary = {
        "charging_state": charge.get("charging_state"),
        "battery_level": charge.get("battery_level"),
        "charge_limit_soc": charge.get("charge_limit_soc"),
        "charger_power_kw": charge.get("charger_power"),
        "charger_voltage_v": charge.get("charger_voltage"),
        "charger_actual_current_a": charge.get("charger_actual_current"),
        "time_to_full_charge_h": charge.get("time_to_full_charge"),
        "plugged_in": charge.get("charge_port_latch") == "Engaged",
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
