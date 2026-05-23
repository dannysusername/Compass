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
