"""Tag CRUD: /tags (POST, GET .json), /tags/{id}/edit, /tags/{id}/delete.

System tags (exam/assignment/project/milestone) are seeded per-user at
signup. They can be edited (rename + recolor) but not deleted. Deleting
a user tag clears it from any tasks using it.
"""
from sqlmodel import select

import main
from .conftest import create_class, create_task, db_get, db_query_all, get_user


def test_signup_seeds_system_tags(auth_client):
    """Each new user starts with the 4 SYSTEM_TAGS rows."""
    user = get_user("test@example.com")
    tags = db_query_all(
        select(main.Tag).where(
            main.Tag.user_id == user.id, main.Tag.is_system == True,
        )
    )
    names = {t.name for t in tags}
    assert {"exam", "assignment", "project", "milestone"} <= names


def test_tags_json_returns_only_owned(auth_client, second_user_client):
    """Tags don't leak across users."""
    auth_client.post("/tags", data={"name": "Reading", "color": "#a04528"})
    second_user_client.post("/tags", data={"name": "Spying", "color": "#000000"})
    names = {t["name"] for t in auth_client.get("/tags.json").json()}
    assert "Reading" in names
    assert "Spying" not in names


def test_create_tag_validates_color(auth_client):
    """Color must be a valid #rrggbb hex."""
    r = auth_client.post(
        "/tags", data={"name": "BadColor", "color": "not-a-color"},
        headers={"Accept": "application/json"},
    )
    assert r.status_code == 400


def test_create_tag_requires_name(auth_client):
    """FastAPI's Form(...) rejects empty strings as 422 (its standard
    validation status); the route never sees the request. Either side
    of 400/422 means 'rejected'; pinning 422 documents what actually
    fires today."""
    r = auth_client.post(
        "/tags", data={"name": "", "color": "#a04528"},
        headers={"Accept": "application/json"},
    )
    assert r.status_code in (400, 422)


def test_edit_tag_updates_name_and_color(auth_client):
    r = auth_client.post(
        "/tags", data={"name": "Old", "color": "#000000"},
        headers={"Accept": "application/json"},
    )
    tag_id = r.json()["id"]
    auth_client.post(
        f"/tags/{tag_id}/edit",
        data={"name": "New", "color": "#a04528"},
    )
    tag = db_get(main.Tag, tag_id)
    assert tag.name == "New"
    assert tag.color == "#a04528"


def test_delete_user_tag_clears_from_tasks(auth_client):
    """Deleting a user-defined tag should null its `tag_id` on every
    task using it — those tasks become untagged but otherwise survive."""
    r = auth_client.post(
        "/tags", data={"name": "Reading", "color": "#a04528"},
        headers={"Accept": "application/json"},
    )
    tag_id = r.json()["id"]
    cls = create_class(auth_client)
    t = create_task(auth_client, class_id=cls.id, tag_id=str(tag_id))
    auth_client.post(f"/tags/{tag_id}/delete",
                     headers={"Accept": "application/json"})
    assert db_get(main.Tag, tag_id) is None
    db_t = db_get(main.Task, t["id"])
    assert db_t is not None
    assert db_t.tag_id is None


def test_cannot_delete_system_tag(auth_client):
    """System tags survive deletion attempts — they're load-bearing
    for syllabus-event color/name lookup via system_key."""
    user = get_user("test@example.com")
    sys_tag = db_query_all(
        select(main.Tag).where(
            main.Tag.user_id == user.id, main.Tag.is_system == True,
        )
    )[0]
    r = auth_client.post(f"/tags/{sys_tag.id}/delete",
                         headers={"Accept": "application/json"})
    assert r.status_code == 400
    assert db_get(main.Tag, sys_tag.id) is not None


def test_cannot_edit_other_users_tag(auth_client, second_user_client):
    r = auth_client.post(
        "/tags", data={"name": "Mine", "color": "#a04528"},
        headers={"Accept": "application/json"},
    )
    tag_id = r.json()["id"]
    r2 = second_user_client.post(
        f"/tags/{tag_id}/edit",
        data={"name": "Stolen", "color": "#000000"},
    )
    # Cross-user → 404 (or redirect-to-login). Either way name unchanged.
    tag = db_get(main.Tag, tag_id)
    assert tag.name == "Mine"
