"""Real-UI test for importing a calendar onto the Weekly page.

Drives the actual import modal (not a direct POST): open modal → name +
.ics file → Import → the calendar shows in the legend and its events land
on the grid. Then exercises the Hide toggle and Delete (with confirm).
"""
from datetime import datetime

import pytest
from playwright.sync_api import expect


def _ics_today(summary="Imported Standup") -> bytes:
    """A single timed event dated today, so it lands in the current-month grid."""
    today = datetime.now().strftime("%Y%m%d")
    return (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Test//EN\r\n"
        "BEGIN:VEVENT\r\nUID:1@t\r\n"
        f"SUMMARY:{summary}\r\nDTSTART:{today}T140000Z\r\nDTEND:{today}T143000Z\r\n"
        "END:VEVENT\r\nEND:VCALENDAR\r\n"
    ).encode()


def _grid_titles(page):
    return page.locator(".day-cell-title", has_text="Imported Standup")


def test_import_calendar_flow(signed_in_page, server_url):
    page = signed_in_page
    page.goto(server_url + "/week")

    # Open the import modal and fill it.
    page.click('[data-open-modal="import-calendar-modal"]')
    modal = page.locator("#import-calendar-modal")
    expect(modal).to_be_visible()
    modal.locator('input[name="name"]').fill("WorkCal")
    page.set_input_files('#import-calendar-modal input[name="file"]', files=[{
        "name": "work.ics", "mimeType": "text/calendar", "buffer": _ics_today(),
    }])
    modal.locator('button[type="submit"]').click()

    # Back on /week: legend lists the calendar, event is on the grid.
    page.wait_for_url(server_url + "/week")
    expect(page.locator(".cal-legend")).to_contain_text("WorkCal")
    expect(_grid_titles(page)).to_have_count(1)

    # Hide → event drops from the grid; legend still lists it (dimmed).
    page.locator('.cal-legend-item form[action$="/toggle"] button').click()
    page.wait_for_url(server_url + "/week")
    expect(page.locator(".cal-legend")).to_contain_text("WorkCal")
    expect(_grid_titles(page)).to_have_count(0)

    # Show again → event returns.
    page.locator('.cal-legend-item form[action$="/toggle"] button').click()
    page.wait_for_url(server_url + "/week")
    expect(_grid_titles(page)).to_have_count(1)

    # Delete (accept the confirm) → calendar + its events gone.
    page.once("dialog", lambda d: d.accept())
    page.locator('.cal-legend-item form[action$="/delete"] button').click()
    page.wait_for_url(server_url + "/week")
    expect(page.locator(".cal-legend")).not_to_contain_text("WorkCal")
    expect(_grid_titles(page)).to_have_count(0)


def test_import_cancelled_delete_keeps_calendar(signed_in_page, server_url):
    """Dismissing the delete confirm must NOT remove the calendar."""
    page = signed_in_page
    page.goto(server_url + "/week")
    page.click('[data-open-modal="import-calendar-modal"]')
    page.locator('#import-calendar-modal input[name="name"]').fill("KeepCal")
    page.set_input_files('#import-calendar-modal input[name="file"]', files=[{
        "name": "keep.ics", "mimeType": "text/calendar", "buffer": _ics_today(),
    }])
    page.locator('#import-calendar-modal button[type="submit"]').click()
    page.wait_for_url(server_url + "/week")
    expect(page.locator(".cal-legend")).to_contain_text("KeepCal")

    page.once("dialog", lambda d: d.dismiss())  # cancel
    page.locator('.cal-legend-item form[action$="/delete"] button').click()
    page.wait_for_timeout(300)
    expect(page.locator(".cal-legend")).to_contain_text("KeepCal")
