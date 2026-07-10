# Night Charger

Automatically charges an EV (electric vehicle) at the cheapest time overnight,
based on Finnish Nord Pool spot prices (Pörssisähkö) at 15-minute resolution,
and issues the actual start/stop commands to a Tesla via the
[Tessie](https://tessie.com) API.

The night window starts at 18:00 the previous evening and ends at 06:00 the
target morning (both adjustable). The tool picks the contiguous sub-window of
the requested charging duration that minimises total cost, schedules a
one-off job to start charging at that time, and logs everything it does.

## Status

Working end-to-end: price fetching, cheapest-window calculation, scheduling
via `at`, and Tesla start/stop commands via Tessie are all implemented and
have run successfully (see `charging.log`). Intended to run unattended from
cron once or twice per evening. Not yet containerised end-to-end — the
`Dockerfile` only wraps the price/cost calculation, not the Tessie
orchestration (see [Docker](#running-with-docker) below).

## Project layout

- `fetch_current_spot_prices.py` – fetches 15-min spot prices from
  `api.spot-hinta.fi` and can save them to JSON.
- `min_cost.py` – computes the cheapest contiguous charging window for a
  given duration and night window.
- `nightly_charge.py` – orchestrator: fetches prices, computes the cheapest
  window, and schedules `start_charge.py` to run at that time via `at`.
  Meant to be run from cron. Logs to `charging.log`.
- `price_gated_charge.py` – long-running loop alternative to
  `nightly_charge.py`: for every 15-min spot price slot, starts charging if
  the price is below a threshold and stops it otherwise, until the battery
  reaches a target level. Logs to `log/<start-time>.log`.
- `start_charge.py` / `stop_charge.py` – wake the car and start/stop
  charging immediately via the Tessie API.
- `get_charging_status.py` – prints the current charging status via Tessie.
- `plot_spot_prices.py` – optional visualisation of prices (requires
  `matplotlib`).

## Tessie account (required for car control)

`nightly_charge.py`, `start_charge.py`, `stop_charge.py`, and
`get_charging_status.py` all talk to the car through
[Tessie](https://tessie.com), a third-party service that keeps a persistent
connection to your Tesla and exposes a simple REST API. This project does
**not** talk to Tesla's own API directly, so a Tessie account is required for
anything that actually starts/stops charging (price fetching and the
cheapest-window calculation work without it).

Setup:

1. Create a Tessie account and add your vehicle at
   [tessie.com](https://tessie.com) (paid subscription; a free trial is
   available). Tessie handles Tesla OAuth and keeps your car's session alive
   so commands work even when the car is asleep.
2. Get an API access token from the Tessie web app under
   **Settings → API / Developer** (or see the
   [Tessie API docs](https://developer.tessie.com/)).
3. Find your car's VIN in the Tessie app (or your Tesla account).
4. Export both as environment variables before running any script that
   controls the car:

   ```bash
   export TESSIE_ACCESS_TOKEN="your-token-here"
   export TESSIE_VIN="5YJ..."
   ```

   `nightly_charge.py` schedules `start_charge.py` via `at`, and `at`-jobs
   inherit the environment of the process that queued them. If you run
   `nightly_charge.py` from cron, set these variables in the crontab (or
   source a file that exports them) so the scheduled job has them at fire
   time — the orchestrator will warn (`WARN reason=tessie_env_missing`) if
   they're missing at scheduling time, but the failure only actually
   surfaces later when the `at`-job runs.

Without these variables, `min_cost.py` / `fetch_current_spot_prices.py`
still work standalone for price lookups, but `start_charge.py`,
`stop_charge.py`, `get_charging_status.py`, and the scheduled part of
`nightly_charge.py` will exit with an error.

## Running locally (without Docker)

The core scripts depend only on the Python standard library (Python 3.9+);
`plot_spot_prices.py` additionally needs `matplotlib`.

```bash
python min_cost.py --hours 5 --charging-power-kw 11
```

To use cached prices from a file instead of hitting the API:

```bash
python fetch_current_spot_prices.py     # writes spot_prices.json
python min_cost.py --prices spot_prices.json
```

### Running the full nightly orchestrator

Requires `at` installed (`sudo apt-get install at`) and the Tessie
environment variables from above:

```bash
python nightly_charge.py --hours 5 --charging-power-kw 11 --latest-end 06:00
```

Add `--dry-run` to compute and log the plan without scheduling anything.
Typical cron setup (run at 17:00 and again at 23:00 so a late price update
can reshuffle the plan):

```cron
0 17 * * * TESSIE_ACCESS_TOKEN=... TESSIE_VIN=... /usr/bin/python3 /path/to/nightly_charge.py
0 23 * * * TESSIE_ACCESS_TOKEN=... TESSIE_VIN=... /usr/bin/python3 /path/to/nightly_charge.py
```

### Running the price-gated charging loop

An alternative to `nightly_charge.py` that doesn't need `at`/cron: run it
once (e.g. in the evening, or under `systemd`/`tmux`/`screen`) and it keeps
charging in sync with the spot price until the battery target is hit, then
exits. Requires the Tessie environment variables from above.

```bash
python price_gated_charge.py --max-price-snt-per-kwh 5.0 --max-battery-percent 80
```

- `--max-price-snt-per-kwh` (default `5.0`) – charging is only allowed while
  the current 15-minute slot's spot price (snt/kWh, incl. VAT) is below this.
- `--max-battery-percent` (default `80`) – once the battery reaches this
  level, charging is stopped and the script exits.

Every check and every start/stop action is logged to STDOUT and to
`log/<script-start-time>.log`, e.g. `log/2026-07-10T13.50.log`.

### Checking status / manual control

```bash
python get_charging_status.py   # current charge/plug state
python start_charge.py          # wake car + start charging now
python stop_charge.py           # wake car + stop charging now
```

## Running with Docker

A minimal `Dockerfile` is included, but it currently only packages the
price-fetching and cost-calculation scripts (`min_cost.py` as entrypoint) —
it does **not** include `nightly_charge.py` or the Tessie-calling scripts.
Use it for computing/plotting the cheapest window; run the orchestrator and
Tessie scripts directly with Python for now.

Build the image once:

```bash
docker build -t night-charger .
```

### Basic run

Calculates the cheapest 5 h charging window for tomorrow morning using prices
fetched live from the API:

```bash
docker run --rm night-charger
```

### Passing arguments

`min_cost.py` is the container entrypoint, so any flags after the image name
are forwarded to it:

```bash
docker run --rm night-charger --hours 4 --charging-power-kw 7.4
docker run --rm night-charger --latest-end 07:00 --date 2026-05-23
```

### Using a local price JSON file

Mount the current directory into `/data` and point `--prices` at the mounted
file:

```bash
docker run --rm -v "$PWD:/data" night-charger --prices /data/spot_prices.json
```

### CLI options

| Flag                  | Default            | Meaning                                              |
| --------------------- | ------------------ | ---------------------------------------------------- |
| `--hours`             | `5.0`              | Charging duration in hours                           |
| `--latest-end`        | `06:00`            | Latest allowed end time on the target date           |
| `--charging-power-kw` | `11.0`             | Effective charging power in kW                       |
| `--prices`            | *(fetch from API)* | Path to a JSON file with cached spot prices          |
| `--date`              | *(tomorrow)*       | Target date in ISO format, e.g. `2026-05-23`         |

## SPOT API

The free spot-hinta.fi endpoint used for today and tomorrow:

```bash
curl -i "https://api.spot-hinta.fi/TodayAndDayForward"
```
