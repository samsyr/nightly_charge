#!/usr/bin/env python3
"""Stop Tesla charging immediately via Tessie."""

from __future__ import annotations

import json
import os
import sys
import urllib.request


TESSIE_BASE_URL = "https://api.tessie.com"


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


def main() -> int:
    token = os.environ.get("TESSIE_ACCESS_TOKEN")
    vin = os.environ.get("TESSIE_VIN")
    if not token or not vin:
        print("ERROR: set TESSIE_ACCESS_TOKEN and TESSIE_VIN", file=sys.stderr)
        return 2

    print(f"wake: {call(f'/{vin}/wake', token)}")
    print(f"stop_charging: {call(f'/{vin}/command/stop_charging', token)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
