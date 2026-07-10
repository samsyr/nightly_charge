"""
define_min_nightly_charge_cost.py

Etsii edullisimman aloitusajan yölataukselle annetulle latauskestolle
(default 5 h). Hakee ikkunan, joka mahtuu yöjaksoon ja päättyy
viimeistään klo 06:00 aamulla.

Käyttö (komentoriviltä):
    python define_min_nightly_charge_cost.py [--hours 5] [--latest-end 06:00]
                                             [--charging-power-kw 11]
                                             [--prices spot_prices.json]
                                             [--date 2025-11-20]

Skripti lataa hinnat tiedostosta, jos --prices on annettu. Muuten
hakee hinnat suoraan API:sta fetch_15min_spot_costs.py:n avulla.
"""

from __future__ import annotations

import argparse
from datetime import datetime, date, time, timedelta
from zoneinfo import ZoneInfo

from fetch_current_spot_prices import fetch_spot_prices, load_from_json

HELSINKI = ZoneInfo("Europe/Helsinki")
SLOT_MINUTES = 15  # hintaikkunan pituus


def build_night_window(target_date: date, latest_end: time) -> tuple[datetime, datetime]:
    """
    Yöikkuna alkaa edellisen päivän klo 18:00 ja päättyy target_date latest_end.
    Tämä antaa joustoa myös "ennen kello kuutta aamulla" -tapaukseen.
    """
    end_dt = datetime.combine(target_date, latest_end, tzinfo=HELSINKI)
    start_dt = datetime.combine(target_date - timedelta(days=1), time(18, 0), tzinfo=HELSINKI)
    return start_dt, end_dt


def find_cheapest_window(
    prices: list[dict],
    charge_hours: float,
    window_start: datetime,
    window_end: datetime,
    charging_power_kw: float = 1.0,
) -> dict:
    """
    Etsii latausikkunan, jonka kustannus on minimaalinen.

    - charge_hours: latauksen kesto tunteina (esim. 5.0)
    - window_start..window_end: aikaikkuna, jossa lataus on sallittu
    - charging_power_kw: tehollinen latausteho. Jos 1.0, tulos on
      keskihinta snt/kWh. Jos annat oikean tehon, tulos on euroja.
    """
    # Suodata vain ikkunaan kuuluvat 15 min jaksot ja varmista jatkuvuus
    window_slots = [p for p in prices if p["start"] >= window_start and p["end"] <= window_end]
    window_slots.sort(key=lambda x: x["start"])

    needed = int(round(charge_hours * 60 / SLOT_MINUTES))
    if len(window_slots) < needed:
        raise ValueError(
            f"Ei riittävästi hintadataa: tarvitaan {needed} jaksoa, saatavilla {len(window_slots)}."
        )

    # Liukuva summa peräkkäisille jaksoille (varmistaen, että ne ovat aidosti vierekkäin)
    best = None
    energy_per_slot_kwh = charging_power_kw * (SLOT_MINUTES / 60.0)

    for i in range(len(window_slots) - needed + 1):
        block = window_slots[i : i + needed]
        # Vaadi yhtenäinen jakso (jokaisen alkupiste = edellisen loppupiste)
        contiguous = all(block[j]["end"] == block[j + 1]["start"] for j in range(needed - 1))
        if not contiguous:
            continue

        # Keskihinta snt/kWh
        avg_price = sum(b["price"] for b in block) / needed
        # Kokonaiskustannus snt jos charging_power_kw annettu
        total_cost_snt = sum(b["price"] * energy_per_slot_kwh for b in block)

        if best is None or total_cost_snt < best["total_cost_snt"]:
            best = {
                "start": block[0]["start"],
                "end": block[-1]["end"],
                "avg_price_snt_per_kwh": avg_price,
                "total_cost_snt": total_cost_snt,
                "total_cost_eur": total_cost_snt / 100.0,
                "energy_kwh": charging_power_kw * charge_hours,
                "slots": block,
            }

    if best is None:
        raise ValueError("Yhtenäistä latausikkunaa ei löytynyt annetuista hinnoista.")
    return best


def parse_time(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def main() -> None:
    ap = argparse.ArgumentParser(description="Yölatauksen edullisin aloitusaika.")
    ap.add_argument("--hours", type=float, default=5.0, help="Latauksen kesto tunteina (default 5).")
    ap.add_argument("--latest-end", type=parse_time, default=time(6, 0),
                    help="Viimeisin sallittu päättymisaika, oletus 06:00.")
    ap.add_argument("--charging-power-kw", type=float, default=11.0,
                    help="Latausteho kW (default 11 kW, kotilatauri).")
    ap.add_argument("--prices", type=str, default=None,
                    help="Polku JSON-tiedostoon, jossa hinnat. Jos ei annettu, haetaan API:sta.")
    ap.add_argument("--date", type=str, default=None,
                    help="Kohdepäivä ISO-muodossa (oletus: huominen, Europe/Helsinki).")
    args = ap.parse_args()

    if args.prices:
        prices = load_from_json(args.prices)
    else:
        prices = fetch_spot_prices(include_vat=True)

    if args.date:
        target_date = date.fromisoformat(args.date)
    else:
        # Oletus: huominen (yö, joka päättyy huomenna klo 06)
        target_date = datetime.now(HELSINKI).date() + timedelta(days=1)

    win_start, win_end = build_night_window(target_date, args.latest_end)

    result = find_cheapest_window(
        prices=prices,
        charge_hours=args.hours,
        window_start=win_start,
        window_end=win_end,
        charging_power_kw=args.charging_power_kw,
    )

    print("=" * 60)
    print(f"Yöikkuna: {win_start:%Y-%m-%d %H:%M} .. {win_end:%Y-%m-%d %H:%M}")
    print(f"Latauskesto: {args.hours} h, teho {args.charging_power_kw} kW")
    print("-" * 60)
    print(f"  Aloita lataus:  {result['start']:%Y-%m-%d %H:%M}  ({result['start']:%a})")
    print(f"  Päätä lataus:   {result['end']:%Y-%m-%d %H:%M}")
    print(f"  Keskihinta:     {result['avg_price_snt_per_kwh']:.3f} snt/kWh (sis. ALV)")
    print(f"  Energia:        {result['energy_kwh']:.2f} kWh")
    print(f"  Kokonaiskulu:   {result['total_cost_eur']:.3f} EUR")
    print("=" * 60)
    print("Hintajaksot (15 min):")
    for s in result["slots"]:
        print(f"  {s['start']:%H:%M}-{s['end']:%H:%M}  {s['price']:7.3f} snt/kWh")


if __name__ == "__main__":
    main()
