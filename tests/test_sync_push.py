"""POST /sync — the PUSH side of local-first sync (step 2).

Guards: create (client_id → server id map), newest-wins updates, deletes
write a tombstone that pull surfaces, and cross-user pushes are rejected.
"""
import main
from .conftest import db_get


def test_push_create_maps_client_id(auth_client):
    body = {"changes": {"tasks": [
        {"client_id": "c1", "title": "Pushed task",
         "due_at": "2026-05-23T10:00", "updated_at": "2026-05-23T09:00:00+00:00"},
    ]}}
    r = auth_client.post("/sync", json=body)
    assert r.status_code == 200
    sid = r.json()["id_map"]["c1"]
    assert isinstance(sid, int)
    # it really landed + is owned by the user
    row = db_get(main.Task, sid)
    assert row is not None and row.title == "Pushed task"
    # and it comes back on a pull
    titles = [t["title"] for t in auth_client.get("/sync").json()["tasks"]]
    assert "Pushed task" in titles


def test_push_update_is_newest_wins(auth_client):
    auth_client.post("/tasks", data={"title": "Orig", "due_at": "2026-05-23T10:00"})
    tid = next(t["id"] for t in auth_client.get("/sync").json()["tasks"] if t["title"] == "Orig")

    # newer push wins
    auth_client.post("/sync", json={"changes": {"tasks": [
        {"id": tid, "title": "Newer wins", "updated_at": "2099-01-01T00:00:00+00:00"},
    ]}})
    assert db_get(main.Task, tid).title == "Newer wins"

    # older push is ignored (server copy is newer)
    auth_client.post("/sync", json={"changes": {"tasks": [
        {"id": tid, "title": "Stale loser", "updated_at": "2000-01-01T00:00:00+00:00"},
    ]}})
    assert db_get(main.Task, tid).title == "Newer wins"


def test_push_delete_removes_and_tombstones(auth_client):
    auth_client.post("/tasks", data={"title": "Delete me", "due_at": "2026-05-23T10:00"})
    first = auth_client.get("/sync").json()
    tid = next(t["id"] for t in first["tasks"] if t["title"] == "Delete me")
    cursor = first["server_time"]

    r = auth_client.post("/sync", json={"deletes": {"tasks": [tid]}})
    assert r.status_code == 200
    assert db_get(main.Task, tid) is None  # gone

    delta = auth_client.get("/sync", params={"since": cursor}).json()
    assert {"task", tid} <= {delta["deletions"][0]["kind"], delta["deletions"][0]["id"]} \
        or any(d["kind"] == "task" and d["id"] == tid for d in delta["deletions"])


def test_push_cross_user_is_rejected(auth_client, second_user_client):
    auth_client.post("/tasks", data={"title": "A owns this", "due_at": "2026-05-23T10:00"})
    tid = next(t["id"] for t in auth_client.get("/sync").json()["tasks"]
               if t["title"] == "A owns this")

    # user B tries to overwrite A's task
    second_user_client.post("/sync", json={"changes": {"tasks": [
        {"id": tid, "title": "B hijack", "updated_at": "2099-01-01T00:00:00+00:00"},
    ]}})
    assert db_get(main.Task, tid).title == "A owns this"  # untouched


def test_push_create_class_and_tag(auth_client):
    r = auth_client.post("/sync", json={"changes": {
        "classes": [{"client_id": "c1", "code": "CHEM101", "name": "Chemistry",
                     "updated_at": "2026-05-23T09:00:00+00:00"}],
        "tags": [{"client_id": "t1", "name": "Lab", "color": "#123456",
                  "updated_at": "2026-05-23T09:00:00+00:00"}],
    }})
    assert r.status_code == 200
    idm = r.json()["id_map"]
    cls = db_get(main.Class, idm["c1"])
    tag = db_get(main.Tag, idm["t1"])
    assert cls and cls.code == "CHEM101"
    assert tag and tag.name == "Lab" and tag.color == "#123456"


def test_push_delete_class_tombstones(auth_client):
    auth_client.post("/classes", data={"name": "Bye", "code": "BYE101"})
    first = auth_client.get("/sync").json()
    cid = next(c["id"] for c in first["classes"] if c["code"] == "BYE101")
    cursor = first["server_time"]
    auth_client.post("/sync", json={"deletes": {"classes": [cid]}})
    assert db_get(main.Class, cid) is None
    delta = auth_client.get("/sync", params={"since": cursor}).json()
    assert any(d["kind"] == "class" and d["id"] == cid for d in delta["deletions"])


def test_push_event_create_then_complete(auth_client):
    auth_client.post("/classes", data={"name": "Sci", "code": "SCI101"})
    cid = next(c["id"] for c in auth_client.get("/sync").json()["classes"]
               if c["code"] == "SCI101")
    r = auth_client.post("/sync", json={"changes": {"events": [
        {"client_id": "e1", "class_id": cid, "title": "Lecture", "kind": "lecture",
         "updated_at": "2026-05-23T09:00:00+00:00"},
    ]}})
    eid = r.json()["id_map"]["e1"]
    ev = db_get(main.CalendarEvent, eid)
    assert ev and ev.title == "Lecture" and ev.class_id == cid
    # complete it via a newer push
    auth_client.post("/sync", json={"changes": {"events": [
        {"id": eid, "completed_at": "2026-05-23T10:00:00+00:00",
         "updated_at": "2099-01-01T00:00:00+00:00"},
    ]}})
    assert db_get(main.CalendarEvent, eid).completed_at is not None


def test_push_event_with_foreign_class_is_skipped(auth_client, second_user_client):
    second_user_client.post("/classes", data={"name": "B", "code": "BCLASS"})
    b_cid = second_user_client.get("/sync").json()["classes"][0]["id"]
    r = auth_client.post("/sync", json={"changes": {"events": [
        {"client_id": "x", "class_id": b_cid, "title": "sneaky",
         "updated_at": "2026-05-23T09:00:00+00:00"},
    ]}})
    assert "x" not in r.json()["id_map"]  # creation skipped — class not owned


def test_push_resolves_same_batch_temp_ids(auth_client):
    """Offline you can create a tag/class AND a task that references it in one
    push: the task's tag_id/class_id is the new row's client_id, and the
    server resolves it to the freshly-created server id."""
    r = auth_client.post("/sync", json={"changes": {
        "tags": [{"client_id": "tag-tmp", "name": "Reading", "color": "#abc123",
                  "updated_at": "2026-05-23T09:00:00+00:00"}],
        "classes": [{"client_id": "cls-tmp", "code": "HIST200", "name": "History",
                     "updated_at": "2026-05-23T09:00:00+00:00"}],
        "tasks": [{"client_id": "task-tmp", "title": "Read ch.1",
                   "tag_id": "tag-tmp", "class_id": "cls-tmp",
                   "due_at": "2026-05-23T10:00", "updated_at": "2026-05-23T09:00:00+00:00"}],
    }})
    idm = r.json()["id_map"]
    task = db_get(main.Task, idm["task-tmp"])
    assert task.tag_id == idm["tag-tmp"]      # resolved temp tag id → real id
    assert task.class_id == idm["cls-tmp"]    # resolved temp class id → real id


def test_push_task_cannot_point_at_another_users_class(auth_client, second_user_client):
    # B makes a class; A pushes a task claiming B's class_id → must be nulled.
    second_user_client.post("/classes", data={"name": "Bio", "code": "BIO101"})
    others = second_user_client.get("/sync").json()["classes"]
    b_class_id = others[0]["id"] if others else 999
    r = auth_client.post("/sync", json={"changes": {"tasks": [
        {"client_id": "x", "title": "Sneaky", "class_id": b_class_id,
         "updated_at": "2026-05-23T09:00:00+00:00"},
    ]}})
    sid = r.json()["id_map"]["x"]
    assert db_get(main.Task, sid).class_id is None  # foreign class rejected
