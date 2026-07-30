# SinoTrack Fleet Daily Report

```
sinotrack_scraper.py   -- logs into the SinoTrack portal (headless
                           Chromium via Playwright), pulls yesterday's
                           GPS data per vehicle, saves the raw responses
    |
    v
parse_report.py         -- turns raw pings into per-vehicle stats:
                           distance, driving/idle time, max speed,
                           stops (with location + duration), last
                           reported time. Appends to a running history
                           file.
    |
    v
generate_dashboard.py   -- renders sinotrack_data/dashboard.html from
                           that history. Single file, no server, no
                           internet needed to view it.
```
## Repo contents

| File | Purpose |
|---|---|
| `sinotrack_scraper.py` | Pulls yesterday's data from the SinoTrack portal |
| `parse_report.py` | Parses raw captures into the daily report + history |
| `generate_dashboard.py` | Builds `sinotrack_data/dashboard.html` |
| `run_daily.ps1` | Runs the full pipeline + delivers the dashboard; what Task Scheduler calls |
| `requirements.txt` | Python dependencies |
| `.env` | **Not committed** — your SinoTrack login (see setup below) |
| `sinotrack_data/` | **Not committed** — raw captures, `report_history.json`, `dashboard.html` |

## One-time setup

1. Clone the repo, then from the project folder:
   ```
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   playwright install chromium
   ```
2. Create a `.env` file in the project root (this file is gitignored —
   never commit it):
   ```
   SINOTRACK_USER=your_username
   SINOTRACK_PASS=your_password
   ```
3. Check `VEHICLE_NAMES` near the top of `sinotrack_scraper.py` and
   `parse_report.py` — these must match the exact device names shown in
   the SinoTrack portal's "Please select a Device" list. Update both if
   your fleet changes.

## Running it manually (one-off)

```
python sinotrack_scraper.py
python parse_report.py sinotrack_data\<date>_raw_responses.json
python generate_dashboard.py
```

The scraper prints the exact filename it wrote — use that for the second
command. Then open `sinotrack_data\dashboard.html` in a browser.

The first run pops a visible Chromium window (`headless=False`) so you
can watch the login/report steps succeed before trusting it unattended.

## Data notes (read before trusting the numbers)

- **Distance** is the odometer delta (`nMileage`) between the day's
  first and last ping — this comes straight from the device.
- **Driving/idle time and stop counts are estimates**, derived from gaps
  between pings (capped at 10 min per gap so an overnight parked gap
  doesn't inflate idle time). They will not exactly match a
  second-by-second ground truth — treat them as approximate.
- **"Last reported"** is a plain fact ("13h 50m ago"), not a live
  online/offline status. This report covers a day that's already over,
  so it will always be many hours old by the time anyone reads it —
  that's expected, not a fault. Real-time status would need the scraper
  running continuously through the day, which this isn't built for.
- **"Stops"** counts every transition from moving to speed-0 in the raw
  data, including brief pauses (traffic lights) — not just meaningful
  destinations.

## Troubleshooting

- **A field shows a blank dash on the dashboard** — usually means
  `parse_report.py` and `generate_dashboard.py` are out of sync (e.g.
  history was written by an older version of the parser). Re-run
  `parse_report.py` against that day's raw file with the current script
  version to fix it.
- **Scraper exits non-zero / "No AppJson.asp responses captured"** —
  login or portal navigation failed. Temporarily set `headless=False` in
  `sinotrack_scraper.py` and run manually to watch what happens.
- **Numbers look off** — cross-check against a manual single-day export
  from the portal's own Travel Report for one vehicle. If a max speed
  looks too high, check whether the portal's trip table has more rows
  than are visible on screen before assuming it's a bug.

## Security

`.env`, `sinotrack_data/`, and `logs/` are gitignored — they contain
credentials and vehicle location history and should never be committed.
If credentials are ever accidentally committed, rotate the SinoTrack
password immediately, since removing a file from a future commit does
not remove it from git history.