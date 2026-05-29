"""Import an external calendar (.ics file or fetched URL) into Compass.

Each VEVENT becomes a Task carrying imported_calendar_id, so it reuses the
task pipeline (recurrence, done-toggle, edit/delete, week/today rendering).
Imported events show in-app (week/today) gated by the calendar's visibility,
but are deliberately excluded from the iCal export feed (the user already
subscribes to the source elsewhere). Manage routes 404 on cross-user access.
"""
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select

import main
from .conftest import db_query_all


def _ics(events_block: str) -> bytes:
    return (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Test//EN\r\n"
        + events_block
        + "END:VCALENDAR\r\n"
    ).encode()


def _import_file(client, ics: bytes, name="My Cal", color="#5c8a3a"):
    return client.post(
        "/calendars/import",
        data={"name": name, "color": color},
        files={"file": ("cal.ics", ics, "text/calendar")},
        headers={"Accept": "application/json"},
    )


def _calendars(user_email="test@example.com"):
    return db_query_all(select(main.ImportedCalendar))


def _imported_tasks():
    return db_query_all(
        select(main.Task).where(main.Task.imported_calendar_id != None))  # noqa: E711


# ---- File import ------------------------------------------------------------

def test_file_import_creates_calendar_and_tasks(auth_client):
    ics = _ics(
        "BEGIN:VEVENT\r\nUID:1@t\r\nSUMMARY:Standup\r\n"
        "DTSTART:20300601T140000Z\r\nDTEND:20300601T143000Z\r\nEND:VEVENT\r\n"
    )
    r = _import_file(auth_client, ics, name="Work", color="#3a6b6e")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "Work" and body["color"] == "#3a6b6e"
    assert body["event_count"] == 1

    cals = _calendars()
    assert len(cals) == 1 and cals[0].name == "Work" and cals[0].visible is True
    tasks = _imported_tasks()
    assert len(tasks) == 1
    t = tasks[0]
    assert t.title == "Standup" and t.class_id is None
    assert t.imported_calendar_id == cals[0].id


def test_all_day_and_recurring_mapping(auth_client):
    ics = _ics(
        # All-day single event (VALUE=DATE) → is_all_day, no range.
        "BEGIN:VEVENT\r\nUID:a@t\r\nSUMMARY:Holiday\r\n"
        "DTSTART;VALUE=DATE:20300704\r\nEND:VEVENT\r\n"
        # Weekly recurring → best-effort expand (rrule stored).
        "BEGIN:VEVENT\r\nUID:b@t\r\nSUMMARY:Weekly sync\r\n"
        "DTSTART:20300602T150000Z\r\nRRULE:FREQ=WEEKLY\r\nEND:VEVENT\r\n"
    )
    assert _import_file(auth_client, ics).status_code == 200
    by_title = {t.title: t for t in _imported_tasks()}
    assert by_title["Holiday"].is_all_day is True
    assert by_title["Holiday"].starts_at is None  # single all-day, not a range
    assert by_title["Weekly sync"].rrule == "FREQ=WEEKLY"


def test_biweekly_interval_falls_back_to_single_instance(auth_client):
    """INTERVAL!=1 is approximated as a single instance (documented limit)."""
    ics = _ics(
        "BEGIN:VEVENT\r\nUID:c@t\r\nSUMMARY:Biweekly\r\n"
        "DTSTART:20300602T150000Z\r\nRRULE:FREQ=WEEKLY;INTERVAL=2\r\nEND:VEVENT\r\n"
    )
    assert _import_file(auth_client, ics).status_code == 200
    t = {x.title: x for x in _imported_tasks()}["Biweekly"]
    assert t.rrule is None


def test_malformed_ics_is_rejected(auth_client):
    r = _import_file(auth_client, b"this is not a calendar")
    assert r.status_code == 400


def test_import_requires_a_source(auth_client):
    r = auth_client.post("/calendars/import", data={"name": "Empty"},
                         headers={"Accept": "application/json"})
    assert r.status_code == 400
    assert r.json()["error"] == "no_source"


# ---- URL import + SSRF guard ------------------------------------------------

def test_url_import_success(auth_client, monkeypatch):
    ics = _ics(
        "BEGIN:VEVENT\r\nUID:u@t\r\nSUMMARY:From URL\r\n"
        "DTSTART:20300601T090000Z\r\nEND:VEVENT\r\n"
    )
    # Bypass the network: pretend the host is public + return canned bytes.
    monkeypatch.setattr(main, "_is_public_url", lambda parsed: True)

    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self, n=-1): return ics
    monkeypatch.setattr(main.urllib.request, "urlopen", lambda *a, **k: _Resp())

    r = auth_client.post(
        "/calendars/import",
        data={"name": "Linked", "color": "#a04528",
              "source_url": "https://example.com/cal.ics"},
        headers={"Accept": "application/json"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["event_count"] == 1
    assert {t.title for t in _imported_tasks()} == {"From URL"}


def test_url_import_rejects_non_http_scheme(auth_client):
    r = auth_client.post(
        "/calendars/import",
        data={"name": "Bad", "source_url": "ftp://example.com/cal.ics"},
        headers={"Accept": "application/json"},
    )
    assert r.status_code == 400


def test_url_import_blocks_private_address(auth_client):
    """SSRF guard: localhost resolves to a loopback IP → rejected."""
    r = auth_client.post(
        "/calendars/import",
        data={"name": "SSRF", "source_url": "http://localhost/secret.ics"},
        headers={"Accept": "application/json"},
    )
    assert r.status_code == 400


# ---- Visibility + collectors + iCal -----------------------------------------

def test_visibility_hides_from_today_but_toggle_restores(auth_client):
    # Event dated "today" so it lands in the today collector.
    today = datetime.now().strftime("%Y%m%dT100000Z")
    ics = _ics(
        f"BEGIN:VEVENT\r\nUID:v@t\r\nSUMMARY:Visible event\r\n"
        f"DTSTART:{today}\r\nEND:VEVENT\r\n"
    )
    assert _import_file(auth_client, ics).status_code == 200
    cal_id = _calendars()[0].id

    assert "Visible event" in auth_client.get("/today").text
    # Hide → drops from the view.
    auth_client.post(f"/calendars/{cal_id}/toggle", follow_redirects=False)
    assert "Visible event" not in auth_client.get("/today").text
    # Show again → back.
    auth_client.post(f"/calendars/{cal_id}/toggle", follow_redirects=False)
    assert "Visible event" in auth_client.get("/today").text


def test_imported_events_excluded_from_ical_feed(auth_client):
    ics = _ics(
        "BEGIN:VEVENT\r\nUID:f@t\r\nSUMMARY:Should not export\r\n"
        "DTSTART:20300601T140000Z\r\nEND:VEVENT\r\n"
    )
    assert _import_file(auth_client, ics).status_code == 200
    assert "Should not export" not in auth_client.get("/calendar.ics").text


def test_legend_shows_calendar_on_week_page(auth_client):
    ics = _ics(
        "BEGIN:VEVENT\r\nUID:l@t\r\nSUMMARY:Legend event\r\n"
        "DTSTART:20300601T140000Z\r\nEND:VEVENT\r\n"
    )
    _import_file(auth_client, ics, name="LegendCal")
    assert "LegendCal" in auth_client.get("/week").text


# ---- Ownership scoping ------------------------------------------------------

def test_manage_routes_are_cross_user_404(auth_client, second_user_client):
    ics = _ics(
        "BEGIN:VEVENT\r\nUID:o@t\r\nSUMMARY:Owned\r\n"
        "DTSTART:20300601T140000Z\r\nEND:VEVENT\r\n"
    )
    _import_file(auth_client, ics)
    cal_id = _calendars()[0].id
    for path in (f"/calendars/{cal_id}/toggle",
                 f"/calendars/{cal_id}/edit",
                 f"/calendars/{cal_id}/delete"):
        r = second_user_client.post(path, headers={"Accept": "application/json"})
        assert r.status_code == 404, f"{path} → {r.status_code}"


def test_delete_removes_calendar_and_its_tasks(auth_client):
    ics = _ics(
        "BEGIN:VEVENT\r\nUID:d@t\r\nSUMMARY:Doomed\r\n"
        "DTSTART:20300601T140000Z\r\nEND:VEVENT\r\n"
    )
    _import_file(auth_client, ics)
    cal_id = _calendars()[0].id
    r = auth_client.post(f"/calendars/{cal_id}/delete",
                         headers={"Accept": "application/json"})
    assert r.status_code == 200
    assert _calendars() == []
    assert _imported_tasks() == []
