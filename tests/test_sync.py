"""GET /sync — the PULL side of local-first sync (step 2 foundation).

Guards: auth required, returns the user's data, the `since` delta filter
ships only changed rows, and results are scoped to the requesting user.
"""
import time


def test_sync_requires_login(client):
    r = client.get("/sync", headers={"Accept": "application/json"})
    assert r.status_code == 401


def test_sync_pull_returns_user_data(auth_client):
    auth_client.post("/tasks", data={"title": "Pull me", "due_at": "2026-05-23T10:00"})
    auth_client.post("/tags", data={"name": "Reading", "color": "#a04528"})

    r = auth_client.get("/sync")
    assert r.status_code == 200
    data = r.json()
    assert "server_time" in data
    titles = [t["title"] for t in data["tasks"]]
    assert "Pull me" in titles
    # change-tracking field is exposed for the client's next delta pull
    assert all("updated_at" in t for t in data["tasks"])
    tag_names = [t["name"] for t in data["tags"]]
    assert "Reading" in tag_names
    # the 4 system tags are seeded at signup, so they ride along too
    assert "exam" in tag_names


def test_sync_since_returns_only_the_delta(auth_client):
    auth_client.post("/tasks", data={"title": "Old task", "due_at": "2026-05-23T10:00"})
    cursor = auth_client.get("/sync").json()["server_time"]
    time.sleep(0.02)  # ensure the next row's updated_at is strictly later
    auth_client.post("/tasks", data={"title": "New task", "due_at": "2026-05-23T10:00"})

    delta = auth_client.get("/sync", params={"since": cursor}).json()
    titles = [t["title"] for t in delta["tasks"]]
    assert "New task" in titles
    assert "Old task" not in titles  # unchanged since the cursor → excluded


def test_sync_edit_bumps_updated_at(auth_client):
    """Editing a task must bump updated_at so a delta pull catches it —
    this is the load-bearing onupdate hook. Without it, edits never sync."""
    auth_client.post("/tasks", data={"title": "EditSync", "due_at": "2026-05-23T10:00"})
    first = auth_client.get("/sync").json()
    tid = next(t["id"] for t in first["tasks"] if t["title"] == "EditSync")
    cursor = first["server_time"]
    time.sleep(0.02)
    auth_client.post(f"/tasks/{tid}/edit", data={"title": "EditSync RENAMED"})

    delta = auth_client.get("/sync", params={"since": cursor}).json()
    titles = [t["title"] for t in delta["tasks"]]
    assert "EditSync RENAMED" in titles, "an edit did not bump updated_at → won't sync"


def test_sync_scoped_to_requesting_user(auth_client, second_user_client):
    auth_client.post("/tasks", data={"title": "MINE", "due_at": "2026-05-23T10:00"})
    second_user_client.post("/tasks", data={"title": "THEIRS", "due_at": "2026-05-23T10:00"})

    titles = [t["title"] for t in auth_client.get("/sync").json()["tasks"]]
    assert "MINE" in titles
    assert "THEIRS" not in titles
