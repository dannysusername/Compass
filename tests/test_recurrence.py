"""Recurring-task endpoints — /tasks/{id}/exclude, /tasks/{id}/end-after,
plus the stop_recurrence branch in /tasks/{id}/edit. These are the paths
the recurring-delete dialog and the "change Repeat to Doesn't repeat"
flow use; they're load-bearing for the iCal feed staying in sync with
what the user sees on the web."""
import json
from datetime import timedelta

import main
from .conftest import create_class, create_task, db_get


def _make_recurring(client, **overrides):
    """Daily-recurring task on the given anchor with no until-cap."""
    cls = create_class(client, code="REC101")
    fields = {
        "title": "Recurring",
        "due_at": "2030-06-15T17:00",
        "rrule": "FREQ=DAILY",
    }
    fields.update(overrides)
    t = create_task(client, class_id=cls.id, **fields)
    return db_get(main.Task, t["id"])


# ---- exclude endpoint -----------------------------------------------------

def test_exclude_adds_exdate(auth_client):
    t = _make_recurring(auth_client)
    r = auth_client.post(
        f"/tasks/{t.id}/exclude",
        data={"occurrence_at": "2030-06-17T17:00"},
    )
    assert r.status_code == 200
    db_t = db_get(main.Task, t.id)
    exdates = json.loads(db_t.rrule_exdates)
    assert any("2030-06-17" in s for s in exdates)


def test_exclude_appends_multiple(auth_client):
    t = _make_recurring(auth_client)
    auth_client.post(f"/tasks/{t.id}/exclude", data={"occurrence_at": "2030-06-17T17:00"})
    auth_client.post(f"/tasks/{t.id}/exclude", data={"occurrence_at": "2030-06-19T17:00"})
    db_t = db_get(main.Task, t.id)
    exdates = json.loads(db_t.rrule_exdates)
    assert len(exdates) == 2


def test_exclude_requires_occurrence_at(auth_client):
    t = _make_recurring(auth_client)
    r = auth_client.post(f"/tasks/{t.id}/exclude", data={})
    assert r.status_code == 400


def test_exclude_cross_user_blocked(auth_client, second_user_client):
    t = _make_recurring(auth_client)
    r = second_user_client.post(
        f"/tasks/{t.id}/exclude",
        data={"occurrence_at": "2030-06-17T17:00"},
    )
    assert r.status_code == 404


# ---- end-after endpoint ---------------------------------------------------

def test_end_after_caps_until_one_second_before(auth_client):
    """rrule_until is set to occurrence - 1s so the picked occurrence
    AND everything after disappear from the expander."""
    t = _make_recurring(auth_client)
    r = auth_client.post(
        f"/tasks/{t.id}/end-after",
        data={"occurrence_at": "2030-06-20T17:00"},
    )
    assert r.status_code == 200
    db_t = db_get(main.Task, t.id)
    assert db_t.rrule_until is not None
    # The cap should be 1 second before the picked occurrence (exclusive).
    # SQLite roundtrips drop tz; compare wall-clock components.
    until = db_t.rrule_until
    assert until.year == 2030 and until.month == 6 and until.day == 20
    assert until.hour == 16 and until.minute == 59 and until.second == 59


def test_end_after_rejects_non_recurring(auth_client):
    """The endpoint refuses to cap a task that has no rrule to begin
    with — would just be silent confusion otherwise."""
    cls = create_class(auth_client)
    t = create_task(auth_client, class_id=cls.id,
                    title="Single", due_at="2030-06-15T17:00")
    r = auth_client.post(
        f"/tasks/{t['id']}/end-after",
        data={"occurrence_at": "2030-06-15T17:00"},
    )
    assert r.status_code == 400


# ---- stop_recurrence branch in edit_task ----------------------------------

def test_stop_recurrence_preserves_anchor(auth_client):
    """Editing a recurring task on a LATER occurrence and clearing rrule
    must NOT move t.due_at to the form's value (the form's value is the
    occurrence the user clicked, not a new anchor). It caps the rrule
    instead. Same task as the equivalent test in test_tasks_edit, kept
    here too because it's the load-bearing case for the iCal feed."""
    t = _make_recurring(auth_client)
    original_due = t.due_at
    auth_client.post(
        f"/tasks/{t.id}/edit",
        data={
            "title": t.title,
            "due_at": "2030-06-25T17:00",  # later occurrence
            "rrule": "",
        },
        headers={"Accept": "application/json"},
    )
    db_t = db_get(main.Task, t.id)
    assert db_t.due_at == original_due, (
        "anchor moved during stop-recurrence — should stay at first occurrence"
    )
    assert db_t.rrule == "FREQ=DAILY"
    assert db_t.rrule_until is not None


def test_stop_recurrence_on_first_occurrence_wipes(auth_client):
    """On the FIRST occurrence (form's due_at == anchor), clearing rrule
    falls through to the wipe path: rrule None, rrule_until None."""
    t = _make_recurring(auth_client)
    auth_client.post(
        f"/tasks/{t.id}/edit",
        data={
            "title": t.title,
            "due_at": "2030-06-15T17:00",  # same as anchor
            "rrule": "",
        },
        headers={"Accept": "application/json"},
    )
    db_t = db_get(main.Task, t.id)
    assert db_t.rrule is None
    assert db_t.rrule_until is None


def test_clearing_rrule_clears_exdates(auth_client):
    """When the wipe path runs, rrule_exdates clears too — otherwise
    stale exdates would haunt a 'make recurring again' edit."""
    t = _make_recurring(auth_client)
    auth_client.post(f"/tasks/{t.id}/exclude",
                     data={"occurrence_at": "2030-06-17T17:00"})
    # Wipe-path (first occurrence + rrule="").
    auth_client.post(
        f"/tasks/{t.id}/edit",
        data={
            "title": t.title,
            "due_at": "2030-06-15T17:00",
            "rrule": "",
        },
        headers={"Accept": "application/json"},
    )
    db_t = db_get(main.Task, t.id)
    assert db_t.rrule_exdates is None


# ---- delete entire recurring task ----------------------------------------

def test_delete_recurring_task_removes_all(auth_client):
    """POST /tasks/{id}/delete on a recurring task hard-deletes the row;
    no remnant rrule_until / exdates."""
    t = _make_recurring(auth_client)
    auth_client.post(f"/tasks/{t.id}/delete",
                     headers={"Accept": "application/json"})
    db_t = db_get(main.Task, t.id)
    assert db_t is None
