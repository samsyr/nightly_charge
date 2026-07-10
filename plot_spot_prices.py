"""
plot_spot_prices.py

Visualize 15-minute spot electricity prices from a JSON file produced by
fetch_15min_spot_costs.py (e.g. spot_prices.json, spot_prices_2_days.json).

Usage:
    python plot_spot_prices.py [--prices spot_prices.json] [--out plot.png]
                               [--show] [--mark-cheapest 5]

If matplotlib is not installed:
    pip install matplotlib
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import matplotlib.dates as mdates
import matplotlib.pyplot as plt

HELSINKI = ZoneInfo("Europe/Helsinki")


def load_prices(path: str) -> list[dict]:
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    for row in raw:
        row["start"] = datetime.fromisoformat(row["start"]).astimezone(HELSINKI)
        row["end"] = datetime.fromisoformat(row["end"]).astimezone(HELSINKI)
    raw.sort(key=lambda r: r["start"])
    return raw


def find_cheapest_contiguous(prices: list[dict], hours: float) -> tuple[datetime, datetime, float] | None:
    slot_minutes = 15
    needed = int(round(hours * 60 / slot_minutes))
    if len(prices) < needed:
        return None
    best = None
    for i in range(len(prices) - needed + 1):
        block = prices[i : i + needed]
        if not all(block[j]["end"] == block[j + 1]["start"] for j in range(needed - 1)):
            continue
        avg = sum(b["price"] for b in block) / needed
        if best is None or avg < best[2]:
            best = (block[0]["start"], block[-1]["end"], avg)
    return best


def plot(prices: list[dict], out: str | None, show: bool, mark_cheapest: float | None) -> None:
    starts = [p["start"] for p in prices]
    ends = [p["end"] for p in prices]
    values = [p["price"] for p in prices]

    # Step-style: extend last value to its end timestamp
    xs = starts + [ends[-1]]
    ys = values + [values[-1]]

    fig, ax = plt.subplots(figsize=(12, 5))
    ax.step(xs, ys, where="post", linewidth=1.5, color="#1f77b4")
    ax.fill_between(xs, ys, step="post", alpha=0.15, color="#1f77b4")

    avg = sum(values) / len(values)
    ax.axhline(avg, linestyle="--", linewidth=0.8, color="gray",
               label=f"Average: {avg:.2f} snt/kWh")

    # Highlight overall min/max slots
    imin = min(range(len(values)), key=lambda i: values[i])
    imax = max(range(len(values)), key=lambda i: values[i])
    ax.scatter([starts[imin]], [values[imin]], color="green", zorder=5,
               label=f"Min: {values[imin]:.2f} @ {starts[imin]:%d.%m %H:%M}")
    ax.scatter([starts[imax]], [values[imax]], color="red", zorder=5,
               label=f"Max: {values[imax]:.2f} @ {starts[imax]:%d.%m %H:%M}")

    if mark_cheapest:
        cheap = find_cheapest_contiguous(prices, mark_cheapest)
        if cheap:
            cs, ce, cavg = cheap
            ax.axvspan(cs, ce, color="green", alpha=0.15,
                       label=f"Cheapest {mark_cheapest}h: avg {cavg:.2f} ({cs:%d.%m %H:%M}–{ce:%H:%M})")

    # Day separators at midnight
    day = starts[0].replace(hour=0, minute=0, second=0, microsecond=0)
    last = ends[-1]
    while day <= last:
        if day > starts[0]:
            ax.axvline(day, color="black", linewidth=0.5, alpha=0.3)
        day += timedelta(days=1)

    ax.set_xlabel("Time (Europe/Helsinki)")
    ax.set_ylabel("Price (snt/kWh, incl. VAT)")
    span_days = (ends[-1] - starts[0]).days + 1
    ax.set_title(f"Spot electricity price — {starts[0]:%a %d.%m.%Y} "
                 f"{'..' + ends[-1].strftime('%a %d.%m.%Y') if span_days > 1 else ''}")
    ax.grid(True, alpha=0.3)
    ax.legend(loc="best", fontsize=9)

    locator = mdates.AutoDateLocator(minticks=6, maxticks=12)
    ax.xaxis.set_major_locator(locator)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M\n%d.%m", tz=HELSINKI))

    fig.tight_layout()

    if out:
        fig.savefig(out, dpi=130)
        print(f"Saved: {out}")
    if show or not out:
        plt.show()


def main() -> None:
    ap = argparse.ArgumentParser(description="Visualize spot electricity prices.")
    ap.add_argument("--prices", default="spot_prices.json",
                    help="Path to prices JSON (default: spot_prices.json).")
    ap.add_argument("--out", default=None,
                    help="Save plot to file (e.g. plot.png). If omitted, opens a window.")
    ap.add_argument("--show", action="store_true",
                    help="Show interactive window even when --out is given.")
    ap.add_argument("--mark-cheapest", type=float, default=None,
                    help="Highlight the cheapest contiguous N-hour window (e.g. 5).")
    args = ap.parse_args()

    prices = load_prices(args.prices)
    if not prices:
        raise SystemExit(f"No price entries in {args.prices}")
    plot(prices, args.out, args.show, args.mark_cheapest)


if __name__ == "__main__":
    main()
