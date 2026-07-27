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
"""

import json
from datetime import datetime, timezone
from pathlib import Path

# Map device ID (strTEID) -> human-readable vehicle name
VEHICLE_NAMES = {
    "9176002605": "Alphard (CBF849)",
    "9176002530": "vf",
    "9176002503": "Pajero (BDF237)",
}

GAP_CAP_SECONDS = 600  # 10 minutes; caps any single gap in the idle/driving split


def load_records(json_path):
    """Reads the captured responses file and extracts every telemetry
    record (rows with the standard GPS ping field set) across all pages."""
    data = json.loads(Path(json_path).read_text())

    records = []
    for response in data:
        body = response.get("body", {})
        fields = body.get("m_arrField", [])
        rows = body.get("m_arrRecord", [])

        # Only care about responses that look like GPS ping data --
        # confirmed by requiring these specific fields to be present.
        required = {"strTEID", "nTime", "nSpeed", "nMileage"}
        if not required.issubset(set(fields)):
            continue

        field_index = {name: i for i, name in enumerate(fields)}

        for row in rows:
            teid = row[field_index["strTEID"]]
            records.append(
                {
                    "teid": teid,
                    "time": int(row[field_index["nTime"]]),
                    "speed": int(row[field_index["nSpeed"]]),
                    "mileage": int(row[field_index["nMileage"]]),
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

    return {
        "ping_count": len(deduped),
        "distance_km": round(distance_m / 1000, 2),
        "max_speed_kmh": max_speed,
        "first_ping_utc": first_time.strftime("%Y-%m-%d %H:%M:%S"),
        "last_ping_utc": last_time.strftime("%Y-%m-%d %H:%M:%S"),
        "idle_time_hms": seconds_to_hms(idle_seconds),
        "driving_time_hms": seconds_to_hms(driving_seconds),
    }


def seconds_to_hms(total_seconds):
    h = total_seconds // 3600
    m = (total_seconds % 3600) // 60
    s = total_seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def build_report(json_path):
    records = load_records(json_path)

    by_vehicle = {}
    for r in records:
        by_vehicle.setdefault(r["teid"], []).append(r)

    report_rows = []
    for teid, vehicle_records in by_vehicle.items():
        name = VEHICLE_NAMES.get(teid, teid)
        summary = summarize_vehicle(vehicle_records)
        if summary is None:
            continue
        summary["vehicle"] = name
        summary["device_id"] = teid
        report_rows.append(summary)

    return report_rows


def print_report(report_rows):
    print(f"{'Vehicle':<20} {'Distance(km)':>12} {'MaxSpeed':>9} {'Driving':>10} {'Idle':>10} {'FirstPing (UTC)':>20} {'LastPing (UTC)':>20} {'Pings':>6}")
    for row in report_rows:
        print(
            f"{row['vehicle']:<20} {row['distance_km']:>12} {row['max_speed_kmh']:>9} "
            f"{row['driving_time_hms']:>10} {row['idle_time_hms']:>10} "
            f"{row['first_ping_utc']:>20} {row['last_ping_utc']:>20} {row['ping_count']:>6}"
        )


if __name__ == "__main__":
    import sys

    json_path = sys.argv[1] if len(sys.argv) > 1 else "sinotrack_data/2026-07-23_raw_responses.json"
    rows = build_report(json_path)
    print_report(rows)