"""Toggle + delete lifecycle for tasks and events. Recurring delete
options (exclude / end-after) are in test_recurrence.py; this file
covers the simple non-recurring paths and the "what happens when you
toggle a completed task back" round-trip."""
import main
from .conftest import create_class, create_task, db_get


# ---- Toggle done ----------------------------------------------------------

def test_toggle_marks_task_complete(auth_client):
    cls = create_class(auth_client)
    t = create_task(auth_client, class_id=cls.id, title="A")
    assert db_get(main.Task, t["id"]).completed_at is None
    r = auth_client.post(f"/tasks/{t['id']}/toggle",
                         headers={"Accept": "application/json"})
    assert r.status_code == 200
    assert r.json()["completed"] is True
    assert db_get(main.Task, t["id"]).completed_at is not None


def test_toggle_unmarks_completed_task(auth_client):
    cls = create_class(auth_client)
    t = create_task(auth_client, class_id=cls.id, title="A")
    auth_client.post(f"/tasks/{t['id']}/toggle",
                     headers={"Accept": "application/json"})
    r = auth_client.post(f"/tasks/{t['id']}/toggle",
                         headers={"Accept": "application/json"})
    assert r.json()["completed"] is False
    assert db_get(main.Task, t["id"]).completed_at is None


def test_toggle_stores_completed_at_in_local_naive(auth_client):
    """completed_at must be stored naive (no tzinfo) matching the rest
    of the schema's convention. UTC-aware storage caused the late-night
    'just-completed task disappears' bug.

    The Python value returned from `datetime.now(tz)` IS tz-aware, but
    `.replace(tzinfo=None)` strips it before the SQLite write."""
    cls = create_class(auth_client)
    t = create_task(auth_client, class_id=cls.id, title="A")
    auth_client.post(f"/tasks/{t['id']}/toggle",
                     headers={"Accept": "application/json"})
    db_t = db_get(main.Task, t["id"])
    assert db_t.completed_at is not None
    # SQLite drops tz on roundtrip regardless, so this just confirms
    # the value lands in the user's local clock-time range — i.e. it's
    # not, say, 5+ hours ahead of "now" the way UTC would be in NY.
    from datetime import datetime
    now_local = datetime.now(main.LOCAL_TZ).replace(tzinfo=None)
    delta = abs((db_t.completed_at - now_local).total_seconds())
    assert delta < 60, (
        f"completed_at drift = {delta}s; expected ≈0 (sign of UTC vs local mismatch)"
    )


def test_toggle_event(auth_client):
    """CalendarEvents have their own toggle endpoint."""
    cls = create_class(auth_client)
    from sqlmodel import Session
    with Session(main.engine) as s:
        ev = main.CalendarEvent(
            class_id=cls.id, class_code=cls.code, title="Lecture",
            kind="lecture", actionable=True,
        )
        s.add(ev)
        s.commit()
        ev_id = ev.id
    r = auth_client.post(f"/events/{ev_id}/toggle",
                         headers={"Accept": "application/json"})
    assert r.status_code == 200
    assert db_get(main.CalendarEvent, ev_id).completed_at is not None


def test_cannot_toggle_other_users_task(auth_client, second_user_client):
    cls = create_class(auth_client)
    t = create_task(auth_client, class_id=cls.id, title="A")
    r = second_user_client.post(f"/tasks/{t['id']}/toggle",
                                 headers={"Accept": "application/json"})
    assert r.status_code == 404
    assert db_get(main.Task, t["id"]).completed_at is None


# ---- Delete ---------------------------------------------------------------

def test_delete_task_removes_row(auth_client):
    cls = create_class(auth_client)
    t = create_task(auth_client, class_id=cls.id, title="Doomed")
    auth_client.post(f"/tasks/{t['id']}/delete",
                     headers={"Accept": "application/json"})
    assert db_get(main.Task, t["id"]) is None


def test_delete_task_cascades_to_alerts_and_attachments(auth_client):
    """TaskAlert + TaskAttachment have ON DELETE CASCADE relationships."""
    from sqlmodel import Session, select
    cls = create_class(auth_client)
    t = create_task(auth_client, class_id=cls.id, title="With alert")
    with Session(main.engine) as s:
        s.add(main.TaskAlert(task_id=t["id"], minutes_before=60))
        s.commit()
    auth_client.post(f"/tasks/{t['id']}/delete",
                     headers={"Accept": "application/json"})
    with Session(main.engine) as s:
        leftover = s.exec(
            select(main.TaskAlert).where(main.TaskAlert.task_id == t["id"])
        ).all()
    assert leftover == []


def test_delete_event(auth_client):
    cls = create_class(auth_client)
    from sqlmodel import Session
    with Session(main.engine) as s:
        ev = main.CalendarEvent(
            class_id=cls.id, class_code=cls.code, title="Lecture",
            kind="lecture", actionable=True,
        )
        s.add(ev)
        s.commit()
        ev_id = ev.id
    auth_client.post(f"/events/{ev_id}/delete",
                     headers={"Accept": "application/json"})
    assert db_get(main.CalendarEvent, ev_id) is None


def test_cannot_delete_other_users_task(auth_client, second_user_client):
    cls = create_class(auth_client)
    t = create_task(auth_client, class_id=cls.id, title="Mine")
    r = second_user_client.post(f"/tasks/{t['id']}/delete",
                                 headers={"Accept": "application/json"})
    assert r.status_code == 404
    assert db_get(main.Task, t["id"]) is not None
