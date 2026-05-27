"""Pending-review flow for syllabus-parsed events.

After parse, events live on the class page in a "pending review" state
(`added_to_calendar=False`) — invisible to the iCal feed, the today/week
collectors, and the overdue list — until the user confirms. Per-event
and bulk toggles flip the flag; delete-all is permanent.
"""
from datetime import datetime, timezone

from sqlmodel import Session, select

import main
from .conftest import create_class, db_get, db_query_all


def _add_pending_event(cls, *, title="Quiz 1", kind="quiz",
                       starts_at=datetime(2030, 9, 1, 14, 0)):
    """Insert a parsed-but-pending event the way process_syllabus would."""
    with Session(main.engine) as s:
        ev = main.CalendarEvent(
            class_id=cls.id, class_code=cls.code, title=title, kind=kind,
            actionable=True, added_to_calendar=False, starts_at=starts_at,
        )
        s.add(ev)
        s.commit()
        s.refresh(ev)
        return ev.id


# ---- Defaults ---------------------------------------------------------------

def test_new_calendarevent_defaults_to_on_calendar(auth_client):
    """Model default is True so legacy/clone/extension code paths that
    don't pass the field keep showing on the calendar — only the parser
    creates pending rows."""
    cls = create_class(auth_client)
    with Session(main.engine) as s:
        ev = main.CalendarEvent(class_id=cls.id, class_code=cls.code,
                                title="X", kind="quiz")
        s.add(ev); s.commit(); s.refresh(ev)
        assert ev.added_to_calendar is True


# ---- iCal + collector filtering --------------------------------------------

def test_pending_event_is_excluded_from_ical(auth_client):
    cls = create_class(auth_client)
    _add_pending_event(cls, title="Pending midterm")
    r = auth_client.get("/calendar.ics")
    assert r.status_code == 200
    assert "Pending midterm" not in r.text


def test_added_event_appears_in_ical(auth_client):
    cls = create_class(auth_client)
    eid = _add_pending_event(cls, title="Confirmed midterm")
    auth_client.post(f"/events/{eid}/add-to-calendar", follow_redirects=False)
    r = auth_client.get("/calendar.ics")
    assert "Confirmed midterm" in r.text


def test_pending_event_is_excluded_from_today_collector(auth_client):
    """Schedule a pending event for today and confirm it doesn't show up
    in the today view (which reads through _collect_items_in_range)."""
    cls = create_class(auth_client)
    today = datetime.now(timezone.utc).replace(hour=14, minute=0, second=0,
                                                microsecond=0)
    _add_pending_event(cls, title="Pending lecture", starts_at=today)
    r = auth_client.get("/today")
    assert r.status_code == 200
    assert "Pending lecture" not in r.text


# ---- Per-event toggle ------------------------------------------------------

def test_add_to_calendar_endpoint_flips_flag(auth_client):
    cls = create_class(auth_client)
    eid = _add_pending_event(cls)
    r = auth_client.post(f"/events/{eid}/add-to-calendar", follow_redirects=False)
    assert r.status_code in (302, 303)
    assert db_get(main.CalendarEvent, eid).added_to_calendar is True


def test_remove_from_calendar_endpoint_flips_flag(auth_client):
    cls = create_class(auth_client)
    eid = _add_pending_event(cls)
    auth_client.post(f"/events/{eid}/add-to-calendar", follow_redirects=False)
    auth_client.post(f"/events/{eid}/remove-from-calendar", follow_redirects=False)
    assert db_get(main.CalendarEvent, eid).added_to_calendar is False


def test_add_to_calendar_returns_json_when_requested(auth_client):
    cls = create_class(auth_client)
    eid = _add_pending_event(cls)
    r = auth_client.post(f"/events/{eid}/add-to-calendar",
                          headers={"Accept": "application/json"})
    assert r.status_code == 200
    assert r.json() == {"id": eid, "added_to_calendar": True}


def test_cannot_toggle_other_users_event(auth_client, second_user_client):
    cls = create_class(auth_client)
    eid = _add_pending_event(cls)
    r = second_user_client.post(f"/events/{eid}/add-to-calendar",
                                  follow_redirects=False)
    assert r.status_code == 404
    assert db_get(main.CalendarEvent, eid).added_to_calendar is False


# ---- Bulk add-all / remove-all ---------------------------------------------

def test_add_all_flips_only_pending(auth_client):
    cls = create_class(auth_client)
    pending_ids = [_add_pending_event(cls, title=f"P{i}") for i in range(3)]
    # Manually add one already-on-calendar event — bulk shouldn't bump it.
    with Session(main.engine) as s:
        on_cal = main.CalendarEvent(class_id=cls.id, class_code=cls.code,
                                     title="Already on cal", kind="quiz",
                                     added_to_calendar=True)
        s.add(on_cal); s.commit(); s.refresh(on_cal)
        on_cal_id = on_cal.id
        on_cal_ts = on_cal.updated_at
    r = auth_client.post(f"/classes/{cls.id}/events/add-all",
                          headers={"Accept": "application/json"})
    assert r.status_code == 200
    assert r.json()["added"] == 3
    for pid in pending_ids:
        assert db_get(main.CalendarEvent, pid).added_to_calendar is True
    # The already-on-calendar one wasn't re-touched (updated_at preserved).
    assert db_get(main.CalendarEvent, on_cal_id).updated_at == on_cal_ts


def test_remove_all_flips_only_added(auth_client):
    cls = create_class(auth_client)
    eid = _add_pending_event(cls)
    auth_client.post(f"/events/{eid}/add-to-calendar", follow_redirects=False)
    pending_eid = _add_pending_event(cls, title="Pending one")
    r = auth_client.post(f"/classes/{cls.id}/events/remove-all",
                          headers={"Accept": "application/json"})
    assert r.json()["removed"] == 1
    assert db_get(main.CalendarEvent, eid).added_to_calendar is False
    # Pending one stays pending.
    assert db_get(main.CalendarEvent, pending_eid).added_to_calendar is False


# ---- Bulk delete-all (permanent + tombstones) ------------------------------

def test_delete_all_removes_every_event_and_writes_tombstones(auth_client):
    cls = create_class(auth_client)
    pending_id = _add_pending_event(cls, title="P")
    added_id = _add_pending_event(cls, title="A")
    auth_client.post(f"/events/{added_id}/add-to-calendar",
                      follow_redirects=False)
    r = auth_client.post(f"/classes/{cls.id}/events/delete-all",
                          headers={"Accept": "application/json"})
    assert r.json()["deleted"] == 2
    assert db_get(main.CalendarEvent, pending_id) is None
    assert db_get(main.CalendarEvent, added_id) is None
    tombs = db_query_all(
        select(main.Tombstone).where(
            main.Tombstone.kind == "event",
            main.Tombstone.item_id.in_([pending_id, added_id]),
        )
    )
    assert len(tombs) == 2


def test_delete_all_for_other_users_class_404s(auth_client, second_user_client):
    cls = create_class(auth_client)
    eid = _add_pending_event(cls)
    r = second_user_client.post(f"/classes/{cls.id}/events/delete-all",
                                  follow_redirects=False)
    assert r.status_code == 404
    assert db_get(main.CalendarEvent, eid) is not None


# ---- Class page rendering --------------------------------------------------

def test_class_page_shows_review_banner_when_pending(auth_client):
    cls = create_class(auth_client)
    _add_pending_event(cls, title="Pending one")
    _add_pending_event(cls, title="Pending two")
    r = auth_client.get(f"/classes/{cls.id}")
    assert r.status_code == 200
    # Banner copy + bulk action present.
    assert "2 events found" in r.text
    assert f"/classes/{cls.id}/events/add-all" in r.text
    assert "Add all to calendar" in r.text


def test_class_page_no_banner_when_no_pending(auth_client):
    cls = create_class(auth_client)
    eid = _add_pending_event(cls, title="Confirmed")
    auth_client.post(f"/events/{eid}/add-to-calendar", follow_redirects=False)
    r = auth_client.get(f"/classes/{cls.id}")
    # No banner copy when nothing's pending.
    assert "events found" not in r.text


def test_class_page_delete_all_is_reachable_from_top(auth_client):
    """Delete-all must be present in the upper region of the events
    section regardless of pending vs. on-calendar state — that's the
    no-scroll guarantee. It rides inside the banner when pending events
    exist, and inside the top toolbar when only on-calendar events do."""
    cls = create_class(auth_client)
    delete_form_action = f"/classes/{cls.id}/events/delete-all"

    # Case 1: pending only → delete-all appears in the banner, BEFORE
    # the Pending review subsection header.
    _add_pending_event(cls, title="P")
    body = auth_client.get(f"/classes/{cls.id}").text
    assert "events-review-banner" in body
    delete_idx = body.find(delete_form_action)
    pending_head_idx = body.find("Pending review")
    assert 0 < delete_idx < pending_head_idx, (
        "delete-all should appear before the Pending review subsection"
    )

    # Case 2: all added → no banner, top toolbar carries delete-all
    # ABOVE the On calendar subsection header.
    from sqlmodel import Session, select as sselect
    with Session(main.engine) as s:
        for ev in s.exec(sselect(main.CalendarEvent).where(
            main.CalendarEvent.class_id == cls.id,
        )).all():
            ev.added_to_calendar = True
            s.add(ev)
        s.commit()
    body = auth_client.get(f"/classes/{cls.id}").text
    assert "events-review-banner" not in body
    toolbar_idx = body.find("events-section-toolbar")
    on_cal_idx = body.find("On calendar")
    delete_idx = body.find(delete_form_action)
    assert 0 < toolbar_idx < on_cal_idx, (
        "top toolbar must precede the On calendar subsection"
    )
    assert 0 < delete_idx < on_cal_idx


# ---- Offline replay (web + extension queue pushes here) --------------------

def test_sync_push_can_flip_added_to_calendar(auth_client):
    """The web/extension offline queues push their queued upserts through
    POST /sync as `events` rows with added_to_calendar set. The server
    must accept the field and persist it — that's the round-trip that
    makes offline event toggles actually reconcile."""
    cls = create_class(auth_client)
    eid = _add_pending_event(cls)
    r = auth_client.post("/sync", json={
        "changes": {"events": [
            {"id": eid, "added_to_calendar": True,
             "updated_at": "2099-01-01T00:00:00+00:00"},
        ]},
    })
    assert r.status_code == 200
    assert db_get(main.CalendarEvent, eid).added_to_calendar is True


def test_sync_push_can_delete_event(auth_client):
    """delete-all offline → N queueDelete calls → /sync deletes payload."""
    cls = create_class(auth_client)
    eid = _add_pending_event(cls)
    r = auth_client.post("/sync", json={"deletes": {"events": [eid]}})
    assert r.status_code == 200
    assert db_get(main.CalendarEvent, eid) is None


# ---- Class detail JSON (extension surface) ---------------------------------

def test_class_detail_json_includes_added_to_calendar(auth_client):
    cls = create_class(auth_client)
    pending_id = _add_pending_event(cls, title="P")
    added_id = _add_pending_event(cls, title="A",
                                   starts_at=datetime(2030, 10, 1))
    auth_client.post(f"/events/{added_id}/add-to-calendar",
                      follow_redirects=False)
    body = auth_client.get(f"/classes/{cls.id}.json").json()
    by_id = {e["id"]: e for e in body["events"]}
    assert by_id[pending_id]["added_to_calendar"] is False
    assert by_id[added_id]["added_to_calendar"] is True
