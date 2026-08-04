"""
Parses captured SinoTrack AppJson.asp responses (raw_responses.json produced
by sinotrack_scraper.py) into a daily report table: one row per vehicle,
with distance, speed, and rough activity stats.

Assumptions (documented so you can adjust if wrong):
- nMileage is a cumulative odometer reading in METERS. Daily distance =
  max(nMileage) - min(nMileage) for that vehicle's pings that day.
- nTime is a Unix timestamp (seconds).
- nSpeed is in km/h.
- "Idle time" is estimated by summing the gap between consecutive pings
  wherever nSpeed == 0, capped at 10 minutes per gap (so a vehicle parked
  overnight between report windows doesn't inflate idle time into the
  tens of thousands of seconds).
- "Driving time" sums gaps wherever nSpeed > 0, same cap.
These are estimates from ping density, not exact -- pings aren't evenly
spaced, so treat these numbers as approximate, not authoritative.

Note on landmarks: stop/last-known locations are reverse-geocoded via
OpenStreetMap's Nominatim, which means those coordinates ARE sent to a
third-party service (not "no data leaves this machine"). Results are
cached to sinotrack_data/landmark_cache.json and shared with
generate_dashboard.py, so any given coordinate is only ever looked up
once, not re-fetched every run.
"""

import json
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# Map device ID (strTEID) -> human-readable vehicle name
VEHICLE_NAMES = {
    "9176002605": "Alphard (CBF849)",
    "9176002530": "vf",
    "9176002503": "Pajero (BDF237)",
}

GAP_CAP_SECONDS = 600  # 10 minutes; caps any single gap in the idle/driving split

# --- Landmark (reverse-geocoding) cache -------------------------------------
#
# Persisted to disk and shared with generate_dashboard.py so a coordinate
# is only ever sent to Nominatim once, total -- not once per script, not
# once per day. Without this, generate_dashboard.py's history-wide sweep
# would re-fetch every location it's ever seen on every single run, which
# both gets slower every day (1 req/sec rate limit) and needlessly hammers
# a free public service.

LANDMARK_CACHE_PATH = Path("./sinotrack_data/landmark_cache.json")


def _load_landmark_cache():
    if LANDMARK_CACHE_PATH.exists():
        try:
            return json.loads(LANDMARK_CACHE_PATH.read_text())
        except Exception:
            return {}
    return {}


def _save_landmark_cache(cache):
    LANDMARK_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    LANDMARK_CACHE_PATH.write_text(json.dumps(cache, indent=2))


_landmark_cache = _load_landmark_cache()


def _landmark_key(lat, lon):
    return f"{round(lat, 5)},{round(lon, 5)}"


def get_landmark(lat, lon):
    """Returns the nearest landmark / place name for a lat/lon pair.

    Tries named places first (amenity, tourism, road), then falls back to
    suburb / area. Returns an empty string if nothing useful is found or
    if the network call fails. Cached to disk (see LANDMARK_CACHE_PATH) so
    each unique coordinate is only ever looked up once, ever -- shared
    across runs and with generate_dashboard.py."""
    if lat is None or lon is None:
        return ""
    key = _landmark_key(lat, lon)
    if key in _landmark_cache:
        return _landmark_cache[key]

    try:
        url = (
            f"https://nominatim.openstreetmap.org/reverse"
            f"?lat={lat}&lon={lon}&format=json&addressdetails=1&namedetails=1"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "sinotrack-report/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        a = data.get("address", {})
        nd = data.get("namedetails", {})
        landmark = (
            nd.get("name")
            or a.get("amenity")
            or a.get("tourism")
            or a.get("leisure")
            or a.get("shop")
            or a.get("road")
            or a.get("pedestrian")
            or a.get("footway")
            or ""
        )
        area = (
            a.get("suburb")
            or a.get("neighbourhood")
            or a.get("village")
            or a.get("town")
            or a.get("city_district")
            or a.get("city")
            or ""
        )
        parts = [p for p in [landmark, area] if p]
        result = ", ".join(parts)
        _landmark_cache[key] = result
        _save_landmark_cache(_landmark_cache)  # persist immediately -- crash-safe
        time.sleep(1)  # Nominatim rate limit: 1 req/sec
        return result
    except Exception:
        _landmark_cache[key] = ""
        _save_landmark_cache(_landmark_cache)
        return ""


def load_records(json_path):
    """Reads the captured responses file and extracts every telemetry
    record (rows with the standard GPS ping field set) across all pages.

    Newer capture files (from the fixed scraper) tag each response with
    the vehicle it was captured for, since capture is now scoped to the
    window right after that vehicle's Download click -- that tag is used
    as the source of truth when present. Older capture files (from before
    that fix) don't have the tag, so we fall back to grouping by strTEID
    as before; those older files may still contain pre-fix noise."""
    data = json.loads(Path(json_path).read_text())

    records = []
    for response in data:
        body = response.get("body", {})
        fields = body.get("m_arrField", [])
        rows = body.get("m_arrRecord", [])
        response_vehicle = response.get("vehicle")  # None for old captures

        # Only care about responses that look like GPS ping data --
        # confirmed by requiring these specific fields to be present.
        required = {"strTEID", "nTime", "nSpeed", "nMileage"}
        if not required.issubset(set(fields)):
            continue

        field_index = {name: i for i, name in enumerate(fields)}

        has_latlon = "dbLat" in field_index and "dbLon" in field_index

        for row in rows:
            teid = row[field_index["strTEID"]]
            records.append(
                {
                    "teid": teid,
                    "vehicle_tag": response_vehicle,
                    "time": int(row[field_index["nTime"]]),
                    "speed": int(row[field_index["nSpeed"]]),
                    "mileage": int(row[field_index["nMileage"]]),
                    "lat": float(row[field_index["dbLat"]]) if has_latlon else None,
                    "lon": float(row[field_index["dbLon"]]) if has_latlon else None,
                }
            )
    return records


def summarize_vehicle(records):
    """records: list of dicts for ONE vehicle, unsorted, possibly with
    duplicates (the raw capture has some overlapping/duplicate pings from
    different report pages). Returns a summary dict."""
    if not records:
        return None

    # De-duplicate by (time, mileage) -- the raw capture has real dupes
    # from multiple overlapping API calls.
    seen = set()
    deduped = []
    for r in records:
        key = (r["time"], r["mileage"], r["speed"])
        if key not in seen:
            seen.add(key)
            deduped.append(r)

    deduped.sort(key=lambda r: r["time"])

    mileages = [r["mileage"] for r in deduped]
    speeds = [r["speed"] for r in deduped]
    times = [r["time"] for r in deduped]

    distance_m = max(mileages) - min(mileages)
    max_speed = max(speeds)

    idle_seconds = 0
    driving_seconds = 0
    for i in range(1, len(deduped)):
        gap = min(times[i] - times[i - 1], GAP_CAP_SECONDS)
        if gap <= 0:
            continue
        if deduped[i - 1]["speed"] == 0:
            idle_seconds += gap
        else:
            driving_seconds += gap

    first_time = datetime.fromtimestamp(times[0], tz=timezone.utc)
    last_time = datetime.fromtimestamp(times[-1], tz=timezone.utc)

    # Stop events: every transition from moving (speed>0) to stopped
    # (speed==0), with the location it stopped at and how long it stayed
    # stopped (until the next move, or until the last ping if it never
    # moved again). This is a "stop" in the sense of the ping stream --
    # it doesn't distinguish a red light from a destination.
    stop_locations = []
    for i in range(1, len(deduped)):
        if deduped[i - 1]["speed"] > 0 and deduped[i]["speed"] == 0:
            stop_start = deduped[i]["time"]
            # find when it starts moving again (or fall back to the end
            # of the day's data if it's still stopped at the last ping)
            resume_time = times[-1]
            for j in range(i + 1, len(deduped)):
                if deduped[j]["speed"] > 0:
                    resume_time = deduped[j]["time"]
                    break
            stop_locations.append(
                {
                    "time_utc": datetime.fromtimestamp(stop_start, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                    "lat": deduped[i]["lat"],
                    "lon": deduped[i]["lon"],
                    "duration_hms": seconds_to_hms(max(resume_time - stop_start, 0)),
                }
            )
    stops = len(stop_locations)

    # First move: first ping where speed > 0.
    first_move = next((r for r in deduped if r["speed"] > 0), None)
    first_move_utc = (
        datetime.fromtimestamp(first_move["time"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        if first_move
        else None
    )

    last_stop_utc = stop_locations[-1]["time_utc"] if stop_locations else None

    # Last known location: lat/lon of the most recent ping that has them.
    last_located = next((r for r in reversed(deduped) if r["lat"] is not None), None)
    last_lat = last_located["lat"] if last_located else None
    last_lon = last_located["lon"] if last_located else None

    return {
        "ping_count": len(deduped),
        "distance_km": round(distance_m / 1000, 2),
        "max_speed_kmh": max_speed,
        "first_ping_utc": first_time.strftime("%Y-%m-%d %H:%M:%S"),
        "last_ping_utc": last_time.strftime("%Y-%m-%d %H:%M:%S"),
        "idle_time_hms": seconds_to_hms(idle_seconds),
        "driving_time_hms": seconds_to_hms(driving_seconds),
        "stops": stops,
        "stop_locations": stop_locations,
        "first_move_utc": first_move_utc,
        "last_stop_utc": last_stop_utc,
        "last_lat": last_lat,
        "last_lon": last_lon,
    }


def seconds_to_hms(total_seconds):
    h = total_seconds // 3600
    m = (total_seconds % 3600) // 60
    s = total_seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def build_report(json_path):
    records = load_records(json_path)

    has_vehicle_tags = any(r["vehicle_tag"] for r in records)

    report_rows = []
    for teid, name in VEHICLE_NAMES.items():
        # Only build rows for the 3 target vehicles -- other company
        # vehicles occasionally show up in captured "overview" responses
        # and would otherwise clutter the report with 0-distance rows.
        if has_vehicle_tags:
            # Preferred path: use the scraper's own record of which
            # vehicle each response was captured for.
            vehicle_records = [r for r in records if r["vehicle_tag"] == name]
        else:
            # Fallback for capture files made before the scraper tagged
            # responses -- group by device ID instead, same as before.
            vehicle_records = [r for r in records if r["teid"] == teid]

        summary = summarize_vehicle(vehicle_records)
        if summary is None:
            # No pings at all for this vehicle today. This could mean the
            # vehicle was genuinely offline (GPS unit off/no signal) --
            # but it could also mean the scraper failed to capture that
            # vehicle's response. Emit an explicit placeholder row rather
            # than silently omitting the vehicle, so the two cases aren't
            # indistinguishable from a missing row in the report/dashboard.
            summary = {
                "ping_count": 0,
                "distance_km": 0.0,
                "max_speed_kmh": 0,
                "first_ping_utc": "N/A (no pings captured)",
                "last_ping_utc": "N/A (no pings captured)",
                "idle_time_hms": "00:00:00",
                "driving_time_hms": "00:00:00",
                "stops": 0,
                "stop_locations": [],
                "first_move_utc": None,
                "last_stop_utc": None,
                "last_lat": None,
                "last_lon": None,
            }
        summary["vehicle"] = name
        summary["device_id"] = teid
        summary["last_reported"] = compute_last_reported(summary)
        report_rows.append(summary)

    return report_rows


def compute_last_reported(summary):
    """How long ago the last ping was, phrased as a plain fact rather than
    "Online"/"Offline". This report covers a day that's already over, so
    the last ping is *always* many hours old by the time anyone reads
    this -- an Online/Offline label made that look like a fault every
    single time, when it's actually just guaranteed by the report being
    a daily digest rather than a live view. If real-time status is ever
    needed, that requires the scraper to run continuously through the
    day, not once -- a different tool than this daily report."""
    if summary["ping_count"] == 0:
        return "No data"
    last_ping = datetime.strptime(summary["last_ping_utc"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    age_minutes = (datetime.now(timezone.utc) - last_ping).total_seconds() / 60
    if age_minutes < 1:
        return "just now"
    if age_minutes < 60:
        return f"{int(age_minutes)}m ago"
    return f"{int(age_minutes // 60)}h {int(age_minutes % 60)}m ago"


HISTORY_PATH = Path("./sinotrack_data/report_history.json")


def update_history(report_rows, report_date, history_path=HISTORY_PATH):
    """Upserts today's rows into the running history file the dashboard
    reads from. Keyed by (date, vehicle) so re-running the parser for a
    day you already captured overwrites that day's row instead of
    duplicating it."""
    history_path.parent.mkdir(parents=True, exist_ok=True)

    if history_path.exists():
        history = json.loads(history_path.read_text())
    else:
        history = []

    existing = {(h["date"], h["vehicle"]): i for i, h in enumerate(history)}

    for row in report_rows:
        entry = dict(row)
        entry["date"] = report_date
        key = (report_date, entry["vehicle"])
        if key in existing:
            history[existing[key]] = entry
        else:
            history.append(entry)

    history.sort(key=lambda h: (h["date"], h["vehicle"]))
    history_path.write_text(json.dumps(history, indent=2))
    return history


def print_report(report_rows):
    for row in report_rows:
        if row["last_lat"] is not None:
            coords = f"{row['last_lat']:.5f}, {row['last_lon']:.5f}"
            landmark = get_landmark(row["last_lat"], row["last_lon"])
            loc = f"{coords}  {landmark}".rstrip() if landmark else coords
        else:
            loc = "N/A"
        print(f"--- {row['vehicle']}  (device {row['device_id']}) ---")
        print(f"  Last reported:     {row['last_reported']}")
        print(f"  Distance today:    {row['distance_km']} km")
        print(f"  Travel time:       {row['driving_time_hms']}")
        print(f"  Idle time:         {row['idle_time_hms']}")
        print(f"  Stops:             {row['stops']}")
        print(f"  First move (UTC):  {row['first_move_utc'] or 'N/A'}")
        print(f"  Last stop (UTC):   {row['last_stop_utc'] or 'N/A'}")
        print(f"  Max speed:         {row['max_speed_kmh']} km/h")
        print(f"  Last location:     {loc}")
        print(f"  Pings:             {row['ping_count']}")
        if row["stop_locations"]:
            print(f"  Stop locations:")
            for s in row["stop_locations"]:
                coords = f"{s['lat']:.5f}, {s['lon']:.5f}"
                landmark = get_landmark(s["lat"], s["lon"])
                place = f"  {landmark}" if landmark else ""
                print(f"    {s['time_utc']} UTC  ({coords}){place}  stayed {s['duration_hms']}")
        print()


def infer_report_date(json_path, records):
    """Prefers the target_date tag written by the fixed scraper (present
    on every response); falls back to the YYYY-MM-DD prefix in the
    filename for older capture files that don't have the tag."""
    data = json.loads(Path(json_path).read_text())
    for response in data:
        if response.get("target_date"):
            return response["target_date"]

    stem = Path(json_path).stem  # e.g. "2026-07-27_raw_responses"
    return stem.split("_")[0]


if __name__ == "__main__":
    import sys

    DATA_DIR = Path("sinotrack_data")

    if len(sys.argv) > 1:
        json_path = sys.argv[1]
    else:
        # No file given -- use whichever capture is most recent by
        # filename (they're YYYY-MM-DD prefixed, so this sorts correctly)
        # rather than a hardcoded date that inevitably goes stale.
        candidates = sorted(DATA_DIR.glob("*_raw_responses.json"))
        if not candidates:
            print(f"No capture files found in {DATA_DIR}\\ -- run sinotrack_scraper.py first.")
            sys.exit(1)
        json_path = str(candidates[-1])
        print(f"No file given -- using most recent capture: {json_path}\n")

    if not Path(json_path).exists():
        print(f"File not found: {json_path}")
        available = sorted(DATA_DIR.glob("*_raw_responses.json")) if DATA_DIR.exists() else []
        if available:
            print("Available capture files:")
            for f in available:
                print(f"  {f}")
        else:
            print(f"No capture files found in {DATA_DIR}\\ either -- run sinotrack_scraper.py first.")
        print("\nUsage: python parse_report.py sinotrack_data\\<date>_raw_responses.json")
        sys.exit(1)

    rows = build_report(json_path)
    print_report(rows)

    report_date = infer_report_date(json_path, rows)
    update_history(rows, report_date)
    print(f"\nHistory updated -> {HISTORY_PATH} ({report_date})")