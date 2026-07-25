"""
SinoTrack daily report data puller — Playwright approach.

Strategy: instead of reconstructing SinoTrack's obfuscated request signing
(strSign / strToken), we drive a real browser through Playwright that logs
in normally. Their own JavaScript computes the signature correctly, and we
just listen for the AppJson.asp network responses as they come back and
save the JSON.

Setup (run once):
    pip install playwright python-dotenv
    playwright install chromium
"""

import json
import os
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
    day_cell.click()


# --- Response capture -------------------------------------------------------

captured_responses = []


def handle_response(response):
    """Fires for every network response Playwright sees. We only care
    about calls to AppJson.asp that return JSON data."""
    if "AppJson.asp" not in response.url:
        return
    try:
        body = response.json()
    except Exception:
        return
    captured_responses.append(
        {
            "url": response.url,
            "status": response.status,
            "body": body,
        }
    )


# --- Main flow ---------------------------------------------------------------

def run():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
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

            select_calendar_date(
                page,
                page.get_by_role("textbox").nth(1),
                TARGET_DATE,
            )

            page.get_by_role("button", name="Download").click()
            page.wait_for_timeout(2000)  # give the AppJson.asp request time to land

        browser.close()

    # --- Step 3: save what we captured ---
    out_file = OUTPUT_DIR / f"{TARGET_DATE.isoformat()}_raw_responses.json"
    out_file.write_text(json.dumps(captured_responses, indent=2))
    print(f"Captured {len(captured_responses)} AppJson.asp responses -> {out_file}")


if __name__ == "__main__":
    run()