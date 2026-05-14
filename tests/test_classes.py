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


# ---- /classes/{id}.json (extension class-detail surface) ------------------

def test_class_detail_json_returns_owned_class(auth_client):
    cls = create_class(auth_client, code="CIS101", name="Intro CIS")
    create_task(auth_client, class_id=cls.id, title="Read chapter 1",
                due_at="2030-06-15T17:00")
    r = auth_client.get(f"/classes/{cls.id}.json")
    assert r.status_code == 200
    body = r.json()
    assert body["class"]["code"] == "CIS101"
    assert body["class"]["name"] == "Intro CIS"
    titles = [t["title"] for t in body["tasks"]]
    assert "Read chapter 1" in titles


def test_class_detail_json_omits_completed_tasks(auth_client):
    cls = create_class(auth_client)
    open_t = create_task(auth_client, class_id=cls.id, title="Open")
    done_t = create_task(auth_client, class_id=cls.id, title="Done")
    auth_client.post(f"/tasks/{done_t['id']}/toggle",
                     headers={"Accept": "application/json"})
    body = auth_client.get(f"/classes/{cls.id}.json").json()
    titles = [t["title"] for t in body["tasks"]]
    assert "Open" in titles
    assert "Done" not in titles, "completed tasks should not appear in class detail"


def test_class_detail_json_includes_events_sorted(auth_client):
    cls = create_class(auth_client)
    from sqlmodel import Session
    from datetime import datetime
    with Session(main.engine) as s:
        s.add(main.CalendarEvent(
            class_id=cls.id, class_code=cls.code, title="Quiz Z",
            kind="quiz", actionable=True,
            starts_at=datetime(2030, 7, 1),
        ))
        s.add(main.CalendarEvent(
            class_id=cls.id, class_code=cls.code, title="Quiz A",
            kind="quiz", actionable=True,
            starts_at=datetime(2030, 6, 1),
        ))
        s.commit()
    body = auth_client.get(f"/classes/{cls.id}.json").json()
    titles = [e["title"] for e in body["events"]]
    assert titles == ["Quiz A", "Quiz Z"], (
        f"events should be chronological, got {titles}"
    )


def test_class_detail_json_404_for_other_user(auth_client, second_user_client):
    cls = create_class(auth_client)
    r = second_user_client.get(f"/classes/{cls.id}.json")
    assert r.status_code == 404


# ---- Syllabus + Documents in /classes/{id}.json ---------------------------

def test_class_detail_json_syllabus_null_when_absent(auth_client):
    """No syllabus uploaded → JSON shape carries `syllabus: null` rather
    than omitting the key. The extension UI keys off this to hide its
    Syllabus section."""
    cls = create_class(auth_client)
    body = auth_client.get(f"/classes/{cls.id}.json").json()
    assert "syllabus" in body
    assert body["syllabus"] is None


def test_class_detail_json_returns_latest_syllabus(auth_client):
    """When multiple Syllabus rows exist (re-uploads), the JSON returns
    the one with the latest parsed_at."""
    cls = create_class(auth_client)
    from sqlmodel import Session
    from datetime import datetime, timezone
    with Session(main.engine) as s:
        s.add(main.Syllabus(
            class_id=cls.id, filename="old.pdf", raw_text="old",
            parsed_at=datetime(2030, 1, 1, tzinfo=timezone.utc),
        ))
        s.add(main.Syllabus(
            class_id=cls.id, filename="new.pdf", raw_text="new",
            parsed_at=datetime(2030, 6, 1, tzinfo=timezone.utc),
        ))
        s.commit()
    body = auth_client.get(f"/classes/{cls.id}.json").json()
    assert body["syllabus"] is not None
    assert body["syllabus"]["filename"] == "new.pdf"


def test_class_detail_json_documents_sorted_newest_first(auth_client):
    """Two uploaded docs → JSON returns them sorted newest-first by
    uploaded_at, the order the extension renders without re-sorting."""
    cls = create_class(auth_client)
    from sqlmodel import Session
    from datetime import datetime, timezone
    with Session(main.engine) as s:
        s.add(main.Document(
            class_id=cls.id, title="Older", filename="old.pdf",
            uploaded_at=datetime(2030, 1, 1, tzinfo=timezone.utc),
        ))
        s.add(main.Document(
            class_id=cls.id, title="Newer", filename="new.pdf",
            uploaded_at=datetime(2030, 6, 1, tzinfo=timezone.utc),
        ))
        s.commit()
    body = auth_client.get(f"/classes/{cls.id}.json").json()
    titles = [d["title"] for d in body["documents"]]
    assert titles == ["Newer", "Older"], (
        f"docs should be newest-first, got {titles}"
    )


def test_class_detail_json_documents_empty_when_none(auth_client):
    cls = create_class(auth_client)
    body = auth_client.get(f"/classes/{cls.id}.json").json()
    assert body["documents"] == []


# ---- POST /classes/{id}/docs JSON branch ----------------------------------

def test_upload_doc_returns_json_when_accept_json(auth_client):
    """The extension POSTs with Accept: application/json — the route
    must return a JSON body with the new doc's id/title/filename rather
    than 303-redirecting."""
    cls = create_class(auth_client)
    r = auth_client.post(
        f"/classes/{cls.id}/docs",
        files={"file": ("notes.txt", b"hello world", "text/plain")},
        data={"title": "Lecture notes"},
        headers={"Accept": "application/json"},
    )
    assert r.status_code == 200, f"got {r.status_code}: {r.text[:200]}"
    body = r.json()
    assert body["title"] == "Lecture notes"
    assert "filename" in body
    assert body["filename"].endswith("notes.txt")
    assert "uploaded_at" in body
    # Verify the row really landed.
    doc = db_get(main.Document, body["id"])
    assert doc is not None and doc.class_id == cls.id


def test_upload_doc_html_branch_still_redirects(auth_client):
    """The website's POST (no Accept header) keeps the 303 redirect path
    so its templates' form-submit flow continues to work."""
    cls = create_class(auth_client)
    r = auth_client.post(
        f"/classes/{cls.id}/docs",
        files={"file": ("a.txt", b"abc", "text/plain")},
        data={"title": "A"},
        follow_redirects=False,
    )
    assert r.status_code in (302, 303)


# ---- POST /docs/{id}/delete JSON branch -----------------------------------

def test_delete_doc_returns_json_when_accept_json(auth_client):
    cls = create_class(auth_client)
    upload = auth_client.post(
        f"/classes/{cls.id}/docs",
        files={"file": ("z.txt", b"z", "text/plain")},
        data={"title": "Z"},
        headers={"Accept": "application/json"},
    ).json()
    r = auth_client.post(
        f"/docs/{upload['id']}/delete",
        headers={"Accept": "application/json"},
    )
    assert r.status_code == 200
    assert r.json() == {"deleted": upload["id"]}
    assert db_get(main.Document, upload["id"]) is None


def test_delete_doc_blocked_for_other_user(auth_client, second_user_client):
    cls = create_class(auth_client)
    upload = auth_client.post(
        f"/classes/{cls.id}/docs",
        files={"file": ("y.txt", b"y", "text/plain")},
        data={"title": "Y"},
        headers={"Accept": "application/json"},
    ).json()
    r = second_user_client.post(
        f"/docs/{upload['id']}/delete",
        headers={"Accept": "application/json"},
        follow_redirects=False,
    )
    assert r.status_code in (404, 401, 403), f"unexpected {r.status_code}"
    # Doc still there.
    assert db_get(main.Document, upload["id"]) is not None
