"""Browser-level coverage of the parsed-syllabus event review flow on
the class page. Two failure modes drove this file:

  1. "Remove all from calendar" / "Delete all events" used to fire
     anyway when the confirm() prompt was cancelled — event-review.js
     intercepts the submit, and a returned-false onsubmit attribute
     doesn't stop addEventListener handlers. Regression-guard tests
     below click Cancel and assert events stayed put.

  2. The per-event Add/Remove/Delete row buttons are wired through the
     same JS hijack; tests below confirm both halves (network success
     reload + the individual delete confirm) actually behave.

Events have no creation UI (they only land via Grok syllabus parses),
so the seed_class_with_events fixture writes rows straight into the
throwaway SQLite the server fixture spun up."""
import sqlite3
from datetime import datetime, timezone

import pytest
from playwright.sync_api import expect


def _seed_events(db_path, class_id, class_code, specs):
    """specs: list of (title, kind, added_to_calendar). Returns the new
    event ids in the same order."""
    conn = sqlite3.connect(db_path)
    try:
        ids = []
        now = datetime.now(timezone.utc).isoformat()
        starts = datetime(2026, 9, 15, 23, 59).isoformat()
        for title, kind, added in specs:
            cur = conn.execute(
                "INSERT INTO calendarevent "
                "(class_id, class_code, title, kind, starts_at, "
                " actionable, added_to_calendar, position, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 1, ?, 0, ?)",
                (class_id, class_code, title, kind, starts,
                 1 if added else 0, now),
            )
            ids.append(cur.lastrowid)
        conn.commit()
        return ids
    finally:
        conn.close()


@pytest.fixture
def seed_class_with_events(signed_in_page, server_url, test_db_path):
    """Sign in, create a class via POST /classes (the supported route),
    seed two pending + two on-calendar events, return everything the
    tests need plus a `goto_class()` callable."""
    page = signed_in_page

    # Create class via the same form the home page uses.
    resp = page.evaluate(
        """async () => {
            const fd = new FormData();
            fd.append('name', 'Intro Test');
            fd.append('code', 'TST101');
            const r = await fetch('/classes', {
                method: 'POST', body: fd, credentials: 'include',
                headers: {'Accept': 'application/json'},
            });
            return await r.json();
        }"""
    )
    class_id = resp["id"]

    pending_ids = _seed_events(
        test_db_path, class_id, "TST101",
        [("Quiz 1", "quiz", False), ("Lab 1", "lab", False)],
    )
    added_ids = _seed_events(
        test_db_path, class_id, "TST101",
        [("Midterm", "exam", True), ("Final", "exam", True)],
    )

    def goto_class():
        page.goto(f"{server_url}/classes/{class_id}")
        # Wait for the events section to be rendered.
        expect(page.locator(".class-section-body").first).to_be_visible()

    return {
        "page": page,
        "server_url": server_url,
        "class_id": class_id,
        "pending_ids": pending_ids,
        "added_ids": added_ids,
        "goto_class": goto_class,
    }


# ---- Bulk: Remove all from calendar --------------------------------------

def test_remove_all_cancel_keeps_events(seed_class_with_events):
    """Click 'Remove all from calendar', dismiss the confirm() —
    on-calendar events must stay on calendar. Regression for the
    JS hijack firing through a cancelled confirm."""
    ctx = seed_class_with_events
    page = ctx["page"]
    ctx["goto_class"]()

    # Sanity: both on-calendar rows are rendered in the "On calendar" list.
    expect(page.locator("h3", has_text="On calendar")).to_be_visible()
    on_cal_list = page.locator(".events-subsection", has=page.locator(
        "h3", has_text="On calendar")).locator(".event-list li")
    expect(on_cal_list).to_have_count(2)

    # Dismiss confirm() — Playwright `dialog` is .dismiss().
    page.once("dialog", lambda d: d.dismiss())
    page.locator(
        "form[action*='/events/remove-all'] button[type='submit']"
    ).click()
    # No reload should have happened. Give the (buggy) fetch + reload
    # a moment to fire if it were going to.
    page.wait_for_timeout(500)

    # Still on the class page, still showing both on-calendar rows.
    expect(page.locator("h3", has_text="On calendar")).to_be_visible()
    on_cal_list = page.locator(".events-subsection", has=page.locator(
        "h3", has_text="On calendar")).locator(".event-list li")
    expect(on_cal_list).to_have_count(2)


def test_remove_all_confirm_moves_events_to_pending(seed_class_with_events):
    """Click 'Remove all from calendar', accept confirm() —
    on-calendar section disappears, all 4 events end up in pending."""
    ctx = seed_class_with_events
    page = ctx["page"]
    ctx["goto_class"]()

    page.once("dialog", lambda d: d.accept())
    with page.expect_response("**/events/remove-all") as resp_info:
        page.locator(
            "form[action*='/events/remove-all'] button[type='submit']"
        ).click()
    assert resp_info.value.status in (200, 303)

    # The JS hijack does a window.location.reload() — wait for it.
    page.wait_for_load_state("networkidle")

    # No "On calendar" subsection anymore; pending list now has 4 rows.
    expect(page.locator("h3", has_text="On calendar")).to_have_count(0)
    pending_list = page.locator(".events-subsection", has=page.locator(
        "h3", has_text="Pending review")).locator(".event-list li")
    expect(pending_list).to_have_count(4)


# ---- Bulk: Delete all events --------------------------------------------

def test_delete_all_cancel_keeps_events(seed_class_with_events):
    """Click 'Delete all events', dismiss the confirm() —
    every event survives. Same regression as the remove-all case."""
    ctx = seed_class_with_events
    page = ctx["page"]
    ctx["goto_class"]()

    page.once("dialog", lambda d: d.dismiss())
    # The banner delete-all form sits inside the events review banner.
    page.locator(
        ".events-review-banner form[action*='/events/delete-all'] button[type='submit']"
    ).click()
    page.wait_for_timeout(500)

    # All 4 events still on the page.
    rows = page.locator(".class-section-body .event-list li")
    expect(rows).to_have_count(4)


def test_delete_all_confirm_wipes_events(seed_class_with_events):
    """Click 'Delete all events', accept confirm() — every event
    disappears (both pending + on-calendar)."""
    ctx = seed_class_with_events
    page = ctx["page"]
    ctx["goto_class"]()

    page.once("dialog", lambda d: d.accept())
    with page.expect_response("**/events/delete-all") as resp_info:
        page.locator(
            ".events-review-banner form[action*='/events/delete-all'] button[type='submit']"
        ).click()
    assert resp_info.value.status in (200, 303)
    page.wait_for_load_state("networkidle")

    expect(page.locator(".class-section-body .event-list li")).to_have_count(0)
    expect(page.locator(".class-section-body .empty")).to_have_text(
        "No events yet."
    )


# ---- Per-event: Add to calendar -----------------------------------------

def test_add_individual_event_to_calendar(seed_class_with_events):
    """Click '+ Add to calendar' on a single pending row → that row
    moves under 'On calendar'; the other pending row stays put."""
    ctx = seed_class_with_events
    page = ctx["page"]
    ctx["goto_class"]()

    pending_section = page.locator(".events-subsection", has=page.locator(
        "h3", has_text="Pending review"))
    target_row = pending_section.locator(".event-list li", has=page.locator(
        ".title", has_text="Quiz 1"))
    expect(target_row).to_be_visible()

    with page.expect_response("**/add-to-calendar") as resp_info:
        target_row.locator("button.event-cal-add").click()
    assert resp_info.value.status in (200, 303)
    page.wait_for_load_state("networkidle")

    # Quiz 1 now under On calendar; Lab 1 still under Pending review.
    on_cal_section = page.locator(".events-subsection", has=page.locator(
        "h3", has_text="On calendar"))
    expect(on_cal_section.locator(".event-list li", has=page.locator(
        ".title", has_text="Quiz 1"))).to_have_count(1)
    pending_section = page.locator(".events-subsection", has=page.locator(
        "h3", has_text="Pending review"))
    expect(pending_section.locator(".event-list li", has=page.locator(
        ".title", has_text="Lab 1"))).to_have_count(1)
    expect(pending_section.locator(".event-list li", has=page.locator(
        ".title", has_text="Quiz 1"))).to_have_count(0)


# ---- Per-event: Remove from calendar ------------------------------------

def test_remove_individual_event_from_calendar(seed_class_with_events):
    """Click '− Remove from calendar' on a single on-calendar row →
    that row moves back to pending; the other on-calendar row stays."""
    ctx = seed_class_with_events
    page = ctx["page"]
    ctx["goto_class"]()

    on_cal_section = page.locator(".events-subsection", has=page.locator(
        "h3", has_text="On calendar"))
    target_row = on_cal_section.locator(".event-list li", has=page.locator(
        ".title", has_text="Midterm"))
    expect(target_row).to_be_visible()

    with page.expect_response("**/remove-from-calendar") as resp_info:
        target_row.locator("button.event-cal-remove").click()
    assert resp_info.value.status in (200, 303)
    page.wait_for_load_state("networkidle")

    pending_section = page.locator(".events-subsection", has=page.locator(
        "h3", has_text="Pending review"))
    expect(pending_section.locator(".event-list li", has=page.locator(
        ".title", has_text="Midterm"))).to_have_count(1)
    on_cal_section = page.locator(".events-subsection", has=page.locator(
        "h3", has_text="On calendar"))
    expect(on_cal_section.locator(".event-list li", has=page.locator(
        ".title", has_text="Final"))).to_have_count(1)
    expect(on_cal_section.locator(".event-list li", has=page.locator(
        ".title", has_text="Midterm"))).to_have_count(0)


# ---- Per-event: Delete (native confirm, no JS hijack) -------------------

def test_delete_individual_event_cancel_keeps_it(seed_class_with_events):
    """The per-row Delete form has a native onsubmit confirm and is NOT
    bound by event-review.js (the JS hijack targets bulk + add/remove
    only). Cancelling the confirm leaves the event in place."""
    ctx = seed_class_with_events
    page = ctx["page"]
    ctx["goto_class"]()

    pending_section = page.locator(".events-subsection", has=page.locator(
        "h3", has_text="Pending review"))
    target_row = pending_section.locator(".event-list li", has=page.locator(
        ".title", has_text="Quiz 1"))
    # Open the per-row Edit/Delete drawer.
    target_row.locator("details.row-actions summary").click()

    page.once("dialog", lambda d: d.dismiss())
    target_row.locator(
        "form[action*='/delete'] button[type='submit']"
    ).click()
    page.wait_for_timeout(300)

    # Quiz 1 still rendered.
    pending_section = page.locator(".events-subsection", has=page.locator(
        "h3", has_text="Pending review"))
    expect(pending_section.locator(".event-list li", has=page.locator(
        ".title", has_text="Quiz 1"))).to_have_count(1)


def test_delete_individual_event_confirm_removes_it(seed_class_with_events):
    """Accept the per-row Delete confirm → row is gone, sibling rows
    untouched."""
    ctx = seed_class_with_events
    page = ctx["page"]
    ctx["goto_class"]()

    pending_section = page.locator(".events-subsection", has=page.locator(
        "h3", has_text="Pending review"))
    target_row = pending_section.locator(".event-list li", has=page.locator(
        ".title", has_text="Quiz 1"))
    target_row.locator("details.row-actions summary").click()

    page.once("dialog", lambda d: d.accept())
    with page.expect_response("**/delete") as resp_info:
        target_row.locator(
            "form[action*='/delete'] button[type='submit']"
        ).click()
    assert resp_info.value.status in (200, 303)
    page.wait_for_load_state("networkidle")

    # Quiz 1 gone; Lab 1 still pending; both on-calendar rows still on calendar.
    pending_section = page.locator(".events-subsection", has=page.locator(
        "h3", has_text="Pending review"))
    expect(pending_section.locator(".event-list li", has=page.locator(
        ".title", has_text="Quiz 1"))).to_have_count(0)
    expect(pending_section.locator(".event-list li", has=page.locator(
        ".title", has_text="Lab 1"))).to_have_count(1)
    on_cal_section = page.locator(".events-subsection", has=page.locator(
        "h3", has_text="On calendar"))
    expect(on_cal_section.locator(".event-list li")).to_have_count(2)
