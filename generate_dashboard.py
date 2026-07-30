"""
Builds a self-contained dashboard.html from sinotrack_data/report_history.json
(the running log parse_report.py appends to each day).

No server, no external fonts/scripts, no internet needed to view it --
just open the .html file in a browser. Re-run this after parse_report.py
each day (or as the last step of the scheduled pipeline) to refresh it
with the latest history.

Run:
    python generate_dashboard.py
"""

import json
from datetime import datetime, timezone
from pathlib import Path

HISTORY_PATH = Path("./sinotrack_data/report_history.json")
OUTPUT_PATH = Path("./sinotrack_data/dashboard.html")

TREND_DAYS = 14  # how many most-recent days feed each vehicle's sparkline


def load_history():
    if not HISTORY_PATH.exists():
        return []
    return json.loads(HISTORY_PATH.read_text())


def build_html(history):
    vehicles = sorted({h["vehicle"] for h in history})
    by_vehicle = {v: sorted([h for h in history if h["vehicle"] == v], key=lambda h: h["date"]) for v in vehicles}
    latest_date = max((h["date"] for h in history), default=None)

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    history_json = json.dumps(history)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Fleet Manifest</title>
<style>
  :root {{
    --paper: #F3EFE6;
    --paper-raised: #FBF9F3;
    --ink: #232323;
    --ink-soft: #5B5648;
    --hairline: #C9C1B0;
    --route-blue: #3A5A78;
    --moving-green: #4B7A51;
    --idle-rust: #8B4A3B;
    --stub-shadow: rgba(35,35,35,0.14);
  }}

  * {{ box-sizing: border-box; }}

  body {{
    margin: 0;
    background: var(--paper);
    background-image:
      radial-gradient(var(--hairline) 0.6px, transparent 0.6px);
    background-size: 22px 22px;
    color: var(--ink);
    font-family: -apple-system, "Segoe UI", "Helvetica Neue", Arial, sans-serif;
    padding: 32px 24px 64px;
  }}

  .mono {{
    font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, "Courier New", monospace;
    font-variant-numeric: tabular-nums;
  }}

  header.manifest-head {{
    max-width: 1080px;
    margin: 0 auto 28px;
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    border-bottom: 2px solid var(--ink);
    padding-bottom: 14px;
  }}

  header.manifest-head .title {{
    font-family: inherit;
    font-weight: 800;
    font-size: 22px;
    letter-spacing: 0.12em;
    text-transform: uppercase;
  }}

  header.manifest-head .subtitle {{
    font-size: 12px;
    color: var(--ink-soft);
    letter-spacing: 0.04em;
  }}

  .punch-row {{
    max-width: 1080px;
    margin: 0 auto 20px;
    display: flex;
    gap: 6px;
  }}
  .punch-row span {{
    width: 6px; height: 6px; border-radius: 50%;
    background: var(--hairline);
  }}

  .cards {{
    max-width: 1080px;
    margin: 0 auto;
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
    gap: 22px;
  }}

  .ticket {{
    position: relative;
    background: var(--paper-raised);
    border: 1px solid var(--hairline);
    border-radius: 3px;
    box-shadow: 0 2px 0 var(--stub-shadow);
    padding: 18px 18px 14px;
  }}
  .ticket::before {{
    content: "";
    position: absolute;
    left: -1px; top: 46px;
    width: 12px; height: 12px;
    background: var(--paper);
    border: 1px solid var(--hairline);
    border-radius: 50%;
    transform: translateX(-50%);
  }}
  .ticket .accent-line {{
    position: absolute; top: 0; left: 0; right: 0; height: 4px;
    background: var(--route-blue);
    border-radius: 3px 3px 0 0;
  }}

  .ticket .vname {{
    font-size: 12px;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: var(--ink-soft);
    margin: 6px 0 2px;
  }}

  .ticket .distance {{
    font-size: 40px;
    font-weight: 700;
    line-height: 1;
    margin: 4px 0 2px;
  }}
  .ticket .distance .unit {{
    font-size: 14px;
    font-weight: 500;
    color: var(--ink-soft);
    margin-left: 4px;
  }}

  .badge {{
    display: inline-block;
    font-size: 10px;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    padding: 3px 8px;
    border-radius: 2px;
    margin: 6px 0 12px;
    border: 1px solid transparent;
  }}
  .badge.active {{ background: rgba(75,122,81,0.12); color: var(--moving-green); border-color: var(--moving-green); }}
  .badge.idle {{ background: rgba(139,74,59,0.10); color: var(--idle-rust); border-color: var(--idle-rust); }}
  .badge.none {{ background: rgba(91,86,72,0.10); color: var(--ink-soft); border-color: var(--hairline); }}

  .stat-grid {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 8px 12px;
    font-size: 12px;
    border-top: 1px dashed var(--hairline);
    padding-top: 10px;
    margin-top: 4px;
  }}
  .stat-grid .k {{ color: var(--ink-soft); }}
  .stat-grid .v {{ text-align: right; }}

  .sparkline {{ margin-top: 12px; }}
  .sparkline svg {{ width: 100%; height: 34px; display: block; }}
  .sparkline .cap {{
    font-size: 10px; color: var(--ink-soft); letter-spacing: 0.05em;
    text-transform: uppercase; margin-bottom: 4px;
  }}

  .ping-window {{
    font-size: 10.5px;
    color: var(--ink-soft);
    margin-top: 10px;
    border-top: 1px solid var(--hairline);
    padding-top: 8px;
  }}

  details.stops {{
    margin-top: 8px;
    font-size: 11px;
  }}
  details.stops summary {{
    cursor: pointer;
    color: var(--route-blue);
    font-size: 11px;
    letter-spacing: 0.02em;
  }}
  details.stops ul {{
    list-style: none;
    margin: 6px 0 0;
    padding: 0;
    max-height: 160px;
    overflow-y: auto;
  }}
  details.stops li {{
    padding: 3px 0;
    border-bottom: 1px dashed var(--hairline);
    color: var(--ink-soft);
    font-size: 10.5px;
  }}

  .log {{
    max-width: 1080px;
    margin: 40px auto 0;
  }}
  .log h2 {{
    font-size: 12px;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: var(--ink-soft);
    border-bottom: 1px solid var(--ink);
    padding-bottom: 8px;
  }}
  table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 12.5px;
  }}
  th {{
    text-align: left;
    font-weight: 600;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    font-size: 10.5px;
    color: var(--ink-soft);
    padding: 8px 10px;
    border-bottom: 1px solid var(--hairline);
  }}
  td {{
    padding: 7px 10px;
    border-bottom: 1px dashed var(--hairline);
  }}
  td.num {{ text-align: right; }}
  tr:hover td {{ background: rgba(58,90,120,0.05); }}

  footer {{
    max-width: 1080px;
    margin: 28px auto 0;
    font-size: 11px;
    color: var(--ink-soft);
  }}

  .empty {{
    max-width: 1080px; margin: 0 auto;
    padding: 40px 0; color: var(--ink-soft); font-size: 13px;
  }}
</style>
</head>
<body>

<header class="manifest-head">
  <div>
    <div class="title">Fleet Manifest</div>
    <div class="subtitle">Latest capture: {latest_date or "no data yet"}</div>
  </div>
  <div class="subtitle mono">generated {generated_at}</div>
</header>
<div class="punch-row">{''.join('<span></span>' for _ in range(48))}</div>

<div id="cards" class="cards"></div>

<section class="log">
  <h2>Daily log</h2>
  <table>
    <thead>
      <tr>
        <th>Date</th><th>Vehicle</th>
        <th style="text-align:right">Distance (km)</th>
        <th style="text-align:right">Max speed</th>
        <th style="text-align:right">Driving</th>
        <th style="text-align:right">Idle</th>
        <th style="text-align:right">Pings</th>
      </tr>
    </thead>
    <tbody id="log-body"></tbody>
  </table>
</section>

<footer>
  Distance is odometer delta for the day; driving/idle time are estimated from ping gaps (capped at 10 min each) and are approximate, not authoritative. Generated locally from report_history.json &mdash; no data leaves this machine.
</footer>

<script>
const HISTORY = {history_json};
const TREND_DAYS = {TREND_DAYS};

function hmsToSeconds(hms) {{
  const [h, m, s] = hms.split(':').map(Number);
  return h * 3600 + m * 60 + s;
}}

function sparklinePath(values, w, h) {{
  if (values.length < 2) return null;
  const max = Math.max(...values, 0.001);
  const min = 0;
  const step = w / (values.length - 1);
  return values.map((v, i) => {{
    const x = i * step;
    const y = h - ((v - min) / (max - min)) * (h - 4) - 2;
    return `${{i === 0 ? 'M' : 'L'}}${{x.toFixed(1)}},${{y.toFixed(1)}}`;
  }}).join(' ');
}}

function renderCards() {{
  const vehicles = [...new Set(HISTORY.map(h => h.vehicle))].sort();
  const container = document.getElementById('cards');

  if (vehicles.length === 0) {{
    container.innerHTML = '<div class="empty">No history yet &mdash; run parse_report.py against a capture file first.</div>';
    return;
  }}

  vehicles.forEach(v => {{
    const rows = HISTORY.filter(h => h.vehicle === v).sort((a, b) => a.date.localeCompare(b.date));
    const latest = rows[rows.length - 1];
    const trend = rows.slice(-TREND_DAYS);

    const drivingSec = hmsToSeconds(latest.driving_time_hms);
    const idleSec = hmsToSeconds(latest.idle_time_hms);
    let badgeClass = 'none', badgeText = 'No data';
    if (latest.ping_count > 0) {{
      if (drivingSec >= idleSec && latest.distance_km > 0) {{ badgeClass = 'active'; badgeText = 'Active day'; }}
      else {{ badgeClass = 'idle'; badgeText = 'Mostly idle'; }}
    }}

    const path = sparklinePath(trend.map(r => r.distance_km), 240, 30);

    const el = document.createElement('div');
    el.className = 'ticket';
    el.innerHTML = `
      <div class="accent-line"></div>
      <div class="vname">${{v}}</div>
      <div class="distance mono">${{latest.distance_km.toFixed(1)}}<span class="unit">km &middot; ${{latest.date}}</span></div>
      <div class="badge ${{badgeClass}}">${{badgeText}}</div>
      <div class="stat-grid mono">
        <div class="k">Max speed</div><div class="v">${{latest.max_speed_kmh}} km/h</div>
        <div class="k">Driving</div><div class="v">${{latest.driving_time_hms}}</div>
        <div class="k">Idle</div><div class="v">${{latest.idle_time_hms}}</div>
        <div class="k">Stops</div><div class="v">${{latest.stops ?? '&mdash;'}}</div>
        <div class="k">Pings</div><div class="v">${{latest.ping_count}}</div>
        <div class="k">Last reported</div><div class="v" style="text-align:right; font-size:10.5px;">${{latest.last_reported || '&mdash;'}}</div>
      </div>
      ${{path ? `<div class="sparkline"><div class="cap">Distance, last ${{trend.length}} days</div><svg viewBox="0 0 240 34" preserveAspectRatio="none"><path d="${{path}}" fill="none" stroke="var(--route-blue)" stroke-width="1.5"/></svg></div>` : ''}}
      <div class="ping-window mono">${{latest.first_ping_utc}} &rarr; ${{latest.last_ping_utc}} UTC</div>
      ${{latest.last_lat != null ? `<div class="ping-window mono">Last seen: ${{latest.last_lat.toFixed(5)}}, ${{latest.last_lon.toFixed(5)}}</div>` : ''}}
      ${{(latest.stop_locations && latest.stop_locations.length) ? `
      <details class="stops">
        <summary>${{latest.stop_locations.length}} stop${{latest.stop_locations.length === 1 ? '' : 's'}}</summary>
        <ul class="mono">
          ${{latest.stop_locations.map(s => `<li>${{s.time_utc.split(' ')[1]}} &middot; ${{s.lat.toFixed(4)}}, ${{s.lon.toFixed(4)}} &middot; ${{s.duration_hms}}</li>`).join('')}}
        </ul>
      </details>` : ''}}
    `;
    container.appendChild(el);
  }});
}}

function renderLog() {{
  const body = document.getElementById('log-body');
  const rows = [...HISTORY].sort((a, b) => (b.date + b.vehicle).localeCompare(a.date + a.vehicle));
  body.innerHTML = rows.map(r => `
    <tr>
      <td class="mono">${{r.date}}</td>
      <td>${{r.vehicle}}</td>
      <td class="num mono">${{r.distance_km.toFixed(1)}}</td>
      <td class="num mono">${{r.max_speed_kmh}}</td>
      <td class="num mono">${{r.driving_time_hms}}</td>
      <td class="num mono">${{r.idle_time_hms}}</td>
      <td class="num mono">${{r.ping_count}}</td>
    </tr>
  `).join('');
}}

renderCards();
renderLog();
</script>
</body>
</html>
"""


def main():
    history = load_history()
    html = build_html(history)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(html, encoding="utf-8")
    print(f"Dashboard written -> {OUTPUT_PATH} ({len(history)} history rows)")


if __name__ == "__main__":
    main()