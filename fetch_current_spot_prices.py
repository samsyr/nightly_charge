"""
fetch_15min_spot_costs.py

Hakee Suomen pörssisähkön spot-hinnat 15 minuutin tarkkuudella.
Käyttää spot-hinta.fi API:a (ilmainen, ei vaadi API-avainta).

Hinnat palautetaan muodossa: lista dict-objekteja, joissa
    - 'start': datetime (alkuhetki, Europe/Helsinki)
    - 'end':   datetime (loppuhetki, Europe/Helsinki)
    - 'price': float (snt/kWh, ALV 25,5 %)
"""

from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

HELSINKI = ZoneInfo("Europe/Helsinki")
VAT = 1.255  # Suomen sähkön ALV 25,5 %

# Palauttaa kuluvan ja seuraavan vuorokauden tuntihinnat.
API_URL = "https://api.spot-hinta.fi/TodayAndDayForward"


def fetch_spot_prices(include_vat: bool = True) -> list[dict]:
    """
    Hae spot-hinnat tälle ja seuraavalle päivälle.
    Palauttaa listan, jossa kukin alkio kuvaa yhtä hintajaksoa.

    API palauttaa natiivit 15 min jaksot; jokainen alkio on
    yhden 15 min ikkunan alkuhetki omalla hinnallaan.
    """
    req = urllib.request.Request(
        API_URL,
        headers={"Accept": "application/json", "User-Agent": "spot-optimizer/1.0"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = json.loads(resp.read().decode("utf-8"))

    intervals: list[dict] = []
    for item in raw:
        start_utc = datetime.fromisoformat(item["DateTime"].replace("Z", "+00:00"))
        start_local = start_utc.astimezone(HELSINKI)

        price_eur_per_kwh = item["PriceWithTax"] if include_vat else item["PriceNoTax"]
        price_snt_per_kwh = price_eur_per_kwh * 100.0

        intervals.append(
            {
                "start": start_local,
                "end": start_local + timedelta(minutes=15),
                "price": price_snt_per_kwh,
            }
        )

    intervals.sort(key=lambda x: x["start"])
    return intervals


def save_to_json(intervals: list[dict], path: str = "spot_prices.json") -> None:
    """Tallenna hinnat JSON-tiedostoon (datetime -> ISO-merkkijono)."""
    serializable = [
        {
            "start": iv["start"].isoformat(),
            "end": iv["end"].isoformat(),
            "price": iv["price"],
        }
        for iv in intervals
    ]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(serializable, f, ensure_ascii=False, indent=2)


def load_from_json(path: str = "spot_prices.json") -> list[dict]:
    """Lataa hinnat tiedostosta takaisin samaan rakenteeseen."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return [
        {
            "start": datetime.fromisoformat(d["start"]),
            "end": datetime.fromisoformat(d["end"]),
            "price": d["price"],
        }
        for d in data
    ]


if __name__ == "__main__":
    prices = fetch_spot_prices(include_vat=True)
    print(f"Haettu {len(prices)} kpl 15 min hintajaksoa.")
    print(f"Ensimmäinen: {prices[0]['start']}  -> {prices[0]['price']:.3f} snt/kWh")
    print(f"Viimeinen:   {prices[-1]['start']} -> {prices[-1]['price']:.3f} snt/kWh")
    save_to_json(prices)
    print("Tallennettu: spot_prices.json")
