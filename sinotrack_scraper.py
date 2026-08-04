"""
Setup (run once):
    pip install playwright python-dotenv
    playwright install chromium
"""

import json
import os
import re
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

load_dotenv()  # reads variables from a .env file in the project root into os.environ

# --- Config ---------------------------------------------------------------

PORTAL_URL = "https://www.sinotrack.com/"

# Never hardcode credentials in the script. Set these in your .env file.
USERNAME = os.environ["SINOTRACK_USER"]
PASSWORD = os.environ["SINOTRACK_PASS"]

# The 3 vehicles you're monitoring, by the display name/plate shown in the
# "Please select a Device" list on the portal.
VEHICLE_NAMES = [
    "Alphard (CBF849)",
    "vf",
    "Pajero (BDF237)",
]

OUTPUT_DIR = Path("./sinotrack_data")
OUTPUT_DIR.mkdir(exist_ok=True)

# Report window: yesterday, full day.
TARGET_DATE = date.today() - timedelta(days=1)


def select_calendar_date(page, field_locator, target_date: date):
    """Opens the iView-style date picker and clicks through to the correct
    day for target_date.

    Note: the portal has multiple date pickers loaded in the DOM at once
    (for other reports/fields) even when only one is visibly open — every
    locator here is scoped with :visible to avoid matching the hidden ones.
    """
    field_locator.click()  # opens the calendar popup
    page.wait_for_timeout(300)  # let the calendar popup finish rendering

    target_month = target_date.strftime("%B")  # e.g. "July"
    target_year = str(target_date.year)         # e.g. "2026"

    next_month_button = page.locator(".ivu-date-picker-next-btn-arrow:visible")

    for _ in range(24):  # safety cap so a mismatch can't loop forever
        labels = page.locator(".ivu-date-picker-header-label:visible")
        month_text = labels.nth(0).inner_text()
        year_text = labels.nth(1).inner_text()
        if month_text == target_month and year_text == target_year:
            break
        next_month_button.click()
    else:
        raise RuntimeError(f"Could not navigate calendar to {target_month} {target_year}")

    # Day numbers are <em> tags (confirmed via recording), matched by the
    # "emphasis" role — scoped to visible ones, and still excluding a
    # grayed adjacent-month day that happens to share the same number.
    day_cell = page.locator(
        ".ivu-date-picker-cells-cell:visible"
        ":not(.ivu-date-picker-cells-cell-prev-month)"
        ":not(.ivu-date-picker-cells-cell-next-month)",
        has_text=str(target_date.day),
    )

    count = day_cell.count()
    if count > 1:
        # Dual-panel range calendar (Start + End shown together) can
        # match the same day number in both panels. Click whichever
        # instance is NOT already marked selected/focused -- that's the
        # one still needing a click.
        clicked = False
        for i in range(count):
            classes = day_cell.nth(i).get_attribute("class") or ""
            if "selected" not in classes and "focused" not in classes:
                day_cell.nth(i).click(force=True)
                clicked = True
                break
        if not clicked:
            # Every matching cell is already selected -- nothing to do,
            # but click anyway (force=True) in case the UI still expects
            # a click event to confirm the range, without waiting for a
            # visual "stability" change that won't happen.
            day_cell.first.click(force=True)
    else:
        day_cell.click(force=True)


# --- Response capture -------------------------------------------------------

captured_responses = []

# Response capture is gated by this flag instead of running for the whole
# session. Earlier attempts captured every AppJson.asp response from the
# moment the page loaded -- including background calls fired when a
# vehicle is first selected (before its date range is even set), which
# leaves stale/default-range pings mixed into the same file as the real
# single-day query. Now capture is only "live" for the brief window
# between clicking Download and the wait_for_timeout that follows it, so
# only the response(s) actually produced by that click get saved -- and
# each one is tagged with which vehicle/date it belongs to.
capture_state = {"active": False, "vehicle": None, "date": None}


def handle_response(response):
    """Fires for every network response Playwright sees. We only care
    about calls to AppJson.asp that return JSON data, and only while
    capture_state["active"] is True (i.e. right after a Download click)."""
    if "AppJson.asp" not in response.url:
        return
    if not capture_state["active"]:
        return
    try:
        body = response.json()
    except Exception:
        return
    captured_responses.append(
        {
            "vehicle": capture_state["vehicle"],
            "target_date": capture_state["date"],
            "url": response.url,
            "status": response.status,
            "body": body,
        }
    )


# --- Main flow ---------------------------------------------------------------

def run():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.on("response", handle_response)

        # --- Step 1: log in ---
        page.goto(PORTAL_URL)

        page.get_by_role("textbox", name="User / Device ID / SN /").fill(USERNAME)
        page.locator("input[type=\"password\"]").fill(PASSWORD)

        # One-time consent checkbox — may not appear on every run, so this
        # is conditional rather than a hard requirement.
        agree_checkbox = page.get_by_role("checkbox", name="Agree")
        if agree_checkbox.is_visible():
            agree_checkbox.check()

        page.get_by_role("button", name="Login").click()

        # Navigate to the report ONCE, before the vehicle loop — not
        # inside it.
        page.get_by_text("Report", exact=True).click()
        page.get_by_text("Travel Report", exact=True).click()

        # --- Step 2: per vehicle, select it, set the date, run the report ---
        for vehicle_name in VEHICLE_NAMES:
            page.get_by_role("textbox", name="Please select a Device").click()
            page.get_by_role("listitem").filter(has_text=vehicle_name).click()

            # Set BOTH Start and End to the same single day (yesterday).
            # Previously only Start was being set, which left End
            # defaulting to today — producing a multi-day window instead
            # of a single day.
            select_calendar_date(
                page,
                page.get_by_role("textbox").nth(1),  # Start date field
                TARGET_DATE,
            )
            select_calendar_date(
                page,
                page.get_by_role("textbox").nth(3),  # End date field
                TARGET_DATE,
            )

            # DEBUG: print what actually landed in the Start/End fields
            # right now, before clicking Download. If these don't both
            # show TARGET_DATE, the calendar clicks aren't sticking.
            start_value = page.get_by_role("textbox").nth(1).input_value()
            end_value = page.get_by_role("textbox").nth(3).input_value()
            print(f"[DEBUG] {vehicle_name}: Start field = {start_value!r}, End field = {end_value!r}, target = {TARGET_DATE}")

            # Only capture AppJson.asp responses produced by THIS click --
            # not any of the background/default-range calls that may have
            # fired earlier while selecting the vehicle or date.
            capture_state["active"] = True
            capture_state["vehicle"] = vehicle_name
            capture_state["date"] = TARGET_DATE.isoformat()

            page.get_by_role("button", name="Download").click()
            page.wait_for_timeout(2000)  # give the AppJson.asp request time to land

            capture_state["active"] = False

        browser.close()

    # --- Step 3: save what we captured ---
    if not captured_responses:
        # Nothing captured at all almost always means something upstream
        # broke (login failed, portal layout changed, network issue) --
        # fail loudly with a non-zero exit rather than silently writing an
        # empty file, so a scheduled run shows up as failed.
        raise RuntimeError("No AppJson.asp responses captured -- login or navigation likely failed")

    out_file = OUTPUT_DIR / f"{TARGET_DATE.isoformat()}_raw_responses.json"
    out_file.write_text(json.dumps(captured_responses, indent=2))
    print(f"Captured {len(captured_responses)} AppJson.asp responses -> {out_file}")


if __name__ == "__main__":
    import sys

    try:
        run()
    except Exception as exc:
        print(f"[FAILED] {exc}", file=sys.stderr)
        sys.exit(1)