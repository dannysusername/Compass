"""POST /tasks (Personal) and POST /classes/{id}/tasks — task creation
variants. The partial-update edge cases live in test_tasks_edit.py;
this file covers the creation side: what shapes are accepted, what
gets normalized, smart defaults, and class-ownership validation."""
import main
from .conftest import create_class, create_task, db_get


def test_create_personal_task_has_no_class(auth_client):
    """POST /tasks creates a Personal task — class_id stays NULL."""
    t = create_task(auth_client, title="Buy milk")
    db_t = db_get(main.Task, t["id"])
    assert db_t.class_id is None


def test_create_class_task_links_to_class(auth_client):
    cls = create_class(auth_client)
    t = create_task(auth_client, class_id=cls.id, title="Read")
    db_t = db_get(main.Task, t["id"])
    assert db_t.class_id == cls.id


def test_create_task_in_other_users_class_blocked(auth_client, second_user_client):
    cls = create_class(auth_client)
    r = second_user_client.post(
        f"/classes/{cls.id}/tasks",
        data={"title": "Hijack"},
        headers={"Accept": "application/json"},
    )
    assert r.status_code == 404


def test_create_task_with_due_at(auth_client):
    t = create_task(auth_client, title="Quiz",
                    due_at="2030-06-15T17:00")
    db_t = db_get(main.Task, t["id"])
    assert db_t.due_at is not None
    assert db_t.starts_at is None


def test_create_task_with_range_keeps_both(auth_client):
    """starts_at + due_at on different days → multi-day range task."""
    t = create_task(auth_client, title="Project",
                    starts_at="2030-06-10T09:00",
                    due_at="2030-06-15T17:00")
    db_t = db_get(main.Task, t["id"])
    assert db_t.starts_at is not None
    assert db_t.due_at is not None
    assert db_t.starts_at < db_t.due_at


def test_create_task_starts_only_promotes_to_due(auth_client):
    """_normalize_task_range: lone starts_at becomes due_at (single-date
    task at that time). Convention is "due_at is the deadline; starts_at
    is the optional range start"."""
    t = create_task(auth_client, title="Single",
                    starts_at="2030-06-15T17:00")
    db_t = db_get(main.Task, t["id"])
    assert db_t.starts_at is None
    assert db_t.due_at is not None


def test_create_task_inverted_range_swaps(auth_client):
    """User typed starts > due — the normaliser swaps them so the row
    is sane regardless of input order. (Web app validates client-side
    too, but this is the server-side belt.)"""
    t = create_task(auth_client, title="Backwards",
                    starts_at="2030-06-15T17:00",
                    due_at="2030-06-10T09:00")
    db_t = db_get(main.Task, t["id"])
    assert db_t.starts_at < db_t.due_at


def test_create_task_with_rrule(auth_client):
    t = create_task(auth_client, title="Daily",
                    due_at="2030-06-15T17:00",
                    rrule="FREQ=DAILY")
    db_t = db_get(main.Task, t["id"])
    assert db_t.rrule == "FREQ=DAILY"


def test_create_task_invalid_rrule_dropped(auth_client):
    """rrule isn't in the allow-list → stored as None."""
    t = create_task(auth_client, title="Junk",
                    due_at="2030-06-15T17:00",
                    rrule="FREQ=BANANA")
    db_t = db_get(main.Task, t["id"])
    assert db_t.rrule is None


def test_create_task_all_day(auth_client):
    t = create_task(auth_client, title="Holiday",
                    due_at="2030-06-15T00:00",
                    is_all_day="1")
    db_t = db_get(main.Task, t["id"])
    assert db_t.is_all_day is True


def test_create_task_with_notes(auth_client):
    t = create_task(auth_client, title="With notes",
                    notes="Call mom about the thing")
    db_t = db_get(main.Task, t["id"])
    assert db_t.notes == "Call mom about the thing"


def test_create_task_with_tag(auth_client):
    r = auth_client.post(
        "/tags", data={"name": "Reading", "color": "#a04528"},
        headers={"Accept": "application/json"},
    )
    tag_id = r.json()["id"]
    t = create_task(auth_client, title="Tagged", tag_id=str(tag_id))
    db_t = db_get(main.Task, t["id"])
    assert db_t.tag_id == tag_id


def test_create_task_with_other_users_tag_silently_drops(auth_client, second_user_client):
    """Cross-user tag id → tag_id stays None, task creation succeeds.
    No leakage of other users' tags onto your tasks."""
    r = second_user_client.post(
        "/tags", data={"name": "Theirs", "color": "#000000"},
        headers={"Accept": "application/json"},
    )
    foreign_tag_id = r.json()["id"]
    t = create_task(auth_client, title="Try foreign", tag_id=str(foreign_tag_id))
    db_t = db_get(main.Task, t["id"])
    assert db_t.tag_id is None


def test_create_task_empty_title_rejected(auth_client):
    r = auth_client.post(
        "/tasks",
        data={"title": ""},
        headers={"Accept": "application/json"},
    )
    assert r.status_code in (400, 422)
