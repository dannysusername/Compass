"""Class CRUD: /classes (POST, GET .json), /classes/{id}/delete.

The cascade rule is the load-bearing thing: deleting a class must NULL
its tasks' class_id (so they survive as Personal) rather than cascading
the delete. CalendarEvents and Documents go away with the class.
"""
from sqlmodel import select

import main
from .conftest import create_class, create_task, db_get, db_query_all, db_query_first


def test_create_class_normalizes_code(auth_client):
    """Code is uppercased + stripped before storage."""
    r = auth_client.post(
        "/classes",
        data={"code": "  cs101 ", "name": "Intro CS"},
        follow_redirects=False,
    )
    assert r.status_code in (302, 303)
    cls = db_query_first(select(main.Class).where(main.Class.user_id == 1))
    assert cls.code == "CS101"
    assert cls.name == "Intro CS"


def test_classes_json_returns_only_owned(auth_client, second_user_client):
    """/classes.json must scope to the requesting user."""
    create_class(auth_client, code="MINE")
    create_class(second_user_client, code="THEIRS")
    r = auth_client.get("/classes.json")
    codes = [c["code"] for c in r.json()]
    assert "MINE" in codes
    assert "THEIRS" not in codes


def test_delete_class_nulls_task_class_ids(auth_client):
    """Tasks survive class deletion as Personal (class_id = None).
    The user keeps their work even when they delete the class."""
    cls = create_class(auth_client, code="DROP101")
    t = create_task(auth_client, class_id=cls.id, title="Survivor")
    auth_client.post(f"/classes/{cls.id}/delete", follow_redirects=False)
    db_t = db_get(main.Task, t["id"])
    assert db_t is not None, "task was cascade-deleted with the class"
    assert db_t.class_id is None, "task.class_id should be NULL after class delete"


def test_delete_class_removes_calendar_events(auth_client):
    """CalendarEvents (syllabus-extracted) DO cascade with the class —
    they're class-scoped by definition."""
    cls = create_class(auth_client)
    # Manually insert a CalendarEvent; we have no syllabus parsing in
    # tests, but the cascade rule applies regardless of source.
    from sqlmodel import Session
    with Session(main.engine) as s:
        ev = main.CalendarEvent(
            class_id=cls.id, class_code=cls.code, title="Quiz 1",
            kind="quiz", actionable=True,
        )
        s.add(ev)
        s.commit()
        ev_id = ev.id
    auth_client.post(f"/classes/{cls.id}/delete", follow_redirects=False)
    assert db_get(main.CalendarEvent, ev_id) is None


def test_cannot_delete_other_users_class(auth_client, second_user_client):
    cls = create_class(auth_client)
    r = second_user_client.post(
        f"/classes/{cls.id}/delete", follow_redirects=False,
    )
    # Either 404 or 303 to login; either way the class survives.
    assert db_get(main.Class, cls.id) is not None


def test_class_detail_returns_404_for_other_user(auth_client, second_user_client):
    cls = create_class(auth_client)
    r = second_user_client.get(f"/classes/{cls.id}", follow_redirects=False)
    assert r.status_code == 404
