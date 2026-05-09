"""Today + Overdue collectors and the merge that powers /today.json.

These tests pin down the rules that recently regressed:
  - A task due earlier today AND past `now` shows up in BOTH collectors
    pre-dedupe; the merge must drop the today copy so it renders once
    under the Overdue cap.
  - hide_completed=True (today/home/class views) must hide tasks
    completed YESTERDAY, but keep tasks completed TODAY (in window).
  - Personal tasks bucket under PERSONAL_BUCKET (id=0) regardless of
    where they came from."""
from datetime import datetime, timedelta, timezone
from sqlmodel import Session, select

import main
from .conftest import create_class, create_task, db_get, get_user


def _set_due(task_id, when):
    """Set t.due_at directly to a specific datetime — useful for
    constructing 'this is overdue' / 'this is in the past' fixtures
    without going through the form-parsing pipeline."""
    with Session(main.engine) as session:
        t = session.get(main.Task, task_id)
        t.due_at = when
        session.add(t)
        session.commit()


def _set_completed(task_id, when):
    with Session(main.engine) as session:
        t = session.get(main.Task, task_id)
        t.completed_at = when
        session.add(t)
        session.commit()


def _now_local():
    return datetime.now(main.LOCAL_TZ)


# ---- Today range filter ---------------------------------------------------

def test_today_includes_due_today(auth_client):
    """A task due today (any time) should be in today's view — could
    land in `items` (still pending) OR `overdue_items` (past now).
    Both count as 'today'; the dedupe just decides which list. We
    check the union so this isn't time-of-day flaky."""
    cls = create_class(auth_client)
    later_today = _now_local().replace(hour=23, minute=0, second=0, microsecond=0)
    t = create_task(auth_client, class_id=cls.id, title="Tonight",
                    due_at=later_today.strftime("%Y-%m-%dT%H:%M"))
    body = auth_client.get("/today.json").json()
    titles = [it["title"]
              for b in body["buckets"]
              for it in (b["items"] + b["overdue_items"])]
    assert "Tonight" in titles


def test_today_excludes_due_tomorrow(auth_client):
    cls = create_class(auth_client)
    tomorrow = _now_local() + timedelta(days=1)
    create_task(auth_client, class_id=cls.id, title="Future",
                due_at=tomorrow.strftime("%Y-%m-%dT%H:%M"))
    body = auth_client.get("/today.json").json()
    titles = [it["title"]
              for b in body["buckets"]
              for it in (b["items"] + b["overdue_items"])]
    assert "Future" not in titles


# ---- Overdue dedupe (the regression) -------------------------------------

def test_past_due_today_renders_once_in_overdue(auth_client):
    """A task due 2h ago today is technically in BOTH collectors:
    - _collect_items_in_range (today's date range): yes, due IS today.
    - _collect_overdue (past now, within 30 days): yes, past now.
    Without dedupe it shows up twice. _merge_today_with_overdue must
    drop the today copy so the row renders once under the Overdue cap."""
    cls = create_class(auth_client)
    earlier_today = _now_local() - timedelta(hours=2)
    t = create_task(auth_client, class_id=cls.id, title="Past today",
                    due_at=earlier_today.strftime("%Y-%m-%dT%H:%M"))

    body = auth_client.get("/today.json").json()
    items_count = sum(
        1 for b in body["buckets"] for it in b["items"]
        if it["title"] == "Past today"
    )
    overdue_count = sum(
        1 for b in body["buckets"] for it in b["overdue_items"]
        if it["title"] == "Past today"
    )
    assert items_count == 0, "past-due-today task should NOT be in items"
    assert overdue_count == 1, "past-due-today task should appear ONCE in overdue_items"


# ---- hide_completed semantics --------------------------------------------

def test_today_hides_task_completed_yesterday(auth_client):
    """Completed-and-overdue is 'done', not pending. Drops off Today.
    Use a clearly-yesterday completion in the user's local tz (naive,
    matching the toggle endpoint's storage convention) so the test
    isn't sensitive to the late-night UTC-vs-local edge case."""
    cls = create_class(auth_client)
    today_due = _now_local().replace(hour=12, minute=0, second=0, microsecond=0)
    t = create_task(auth_client, class_id=cls.id, title="Yesterday's done",
                    due_at=today_due.strftime("%Y-%m-%dT%H:%M"))
    yesterday_local = (_now_local() - timedelta(days=1)).replace(tzinfo=None)
    _set_completed(t["id"], yesterday_local)
    body = auth_client.get("/today.json").json()
    titles = [it["title"]
              for b in body["buckets"]
              for it in (b["items"] + b["overdue_items"])]
    assert "Yesterday's done" not in titles


def test_today_keeps_task_completed_today(auth_client):
    """A task ticked off in the current today-window stays visible
    (crossed out) so the user sees their just-completed work."""
    cls = create_class(auth_client)
    today_due = _now_local().replace(hour=18, minute=0, second=0, microsecond=0)
    t = create_task(auth_client, class_id=cls.id, title="Just done",
                    due_at=today_due.strftime("%Y-%m-%dT%H:%M"))
    now_local = _now_local().replace(tzinfo=None)
    _set_completed(t["id"], now_local)
    body = auth_client.get("/today.json").json()
    titles = [it["title"]
              for b in body["buckets"]
              for it in (b["items"] + b["overdue_items"])]
    assert "Just done" in titles


# ---- Personal bucket ------------------------------------------------------

def test_personal_task_buckets_under_zero(auth_client):
    """Personal task (no class_id) goes under PERSONAL_BUCKET id=0."""
    later_today = _now_local().replace(hour=23, minute=0, second=0, microsecond=0)
    create_task(auth_client, title="Groceries",
                due_at=later_today.strftime("%Y-%m-%dT%H:%M"))
    body = auth_client.get("/today.json").json()
    personal = [b for b in body["buckets"] if b["is_personal"]]
    assert len(personal) == 1
    items_in_personal = personal[0]["items"] + personal[0]["overdue_items"]
    assert any(it["title"] == "Groceries" for it in items_in_personal)


# ---- Cross-user scoping --------------------------------------------------

def test_today_does_not_leak_other_users_tasks(auth_client, second_user_client):
    """Each user's /today.json is strictly scoped — no leakage."""
    later_today = _now_local().replace(hour=23, minute=0, second=0, microsecond=0)
    iso = later_today.strftime("%Y-%m-%dT%H:%M")
    create_task(auth_client, title="Mine", due_at=iso)
    create_task(second_user_client, title="Theirs", due_at=iso)
    body = auth_client.get("/today.json").json()
    titles = [it["title"]
              for b in body["buckets"]
              for it in (b["items"] + b["overdue_items"])]
    assert "Mine" in titles
    assert "Theirs" not in titles
