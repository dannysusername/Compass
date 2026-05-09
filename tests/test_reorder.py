"""Drag-to-reorder endpoints:

  - POST /tasks/reorder         — global Task/Event.position
  - POST /tasks/reorder-day     — per-day DayItemPosition overrides
  - POST /classes/reorder       — User.class_order_json (display order)

The per-day endpoint is the one that recently shipped; it lets a user
reorder a multi-day task on Friday without shuffling the same task on
Saturday or Sunday. The override is keyed on (kind, item_id, day_date)
so other days fall back to the global position.
"""
from sqlmodel import select

import main
from .conftest import create_class, create_task, db_get, db_query_all, db_query_first


def test_reorder_writes_position(auth_client):
    cls = create_class(auth_client)
    t1 = create_task(auth_client, class_id=cls.id, title="A")
    t2 = create_task(auth_client, class_id=cls.id, title="B")
    t3 = create_task(auth_client, class_id=cls.id, title="C")

    # User drags so order becomes B, A, C.
    r = auth_client.post(
        "/tasks/reorder",
        json={"items": [
            {"kind": "task", "id": t2["id"]},
            {"kind": "task", "id": t1["id"]},
            {"kind": "task", "id": t3["id"]},
        ]},
        headers={"Accept": "application/json"},
    )
    assert r.status_code == 200
    assert db_get(main.Task, t2["id"]).position == 0
    assert db_get(main.Task, t1["id"]).position == 1
    assert db_get(main.Task, t3["id"]).position == 2


def test_reorder_silently_skips_other_users_items(auth_client, second_user_client):
    """Defense-in-depth: even if the client sends another user's id,
    that row's position is not touched."""
    cls = create_class(auth_client)
    mine = create_task(auth_client, class_id=cls.id, title="Mine")
    cls_b = create_class(second_user_client, code="OTHER")
    theirs = create_task(second_user_client, class_id=cls_b.id, title="Theirs")
    auth_client.post(
        "/tasks/reorder",
        json={"items": [
            {"kind": "task", "id": theirs["id"]},  # not mine
            {"kind": "task", "id": mine["id"]},
        ]},
        headers={"Accept": "application/json"},
    )
    # The other user's task position is untouched (still 0); only mine
    # got assigned a position. The pos in the input was 1, and that's
    # what mine got.
    assert db_get(main.Task, mine["id"]).position == 1
    assert db_get(main.Task, theirs["id"]).position == 0  # unchanged


def test_reorder_legacy_task_ids_format_still_works(auth_client):
    """Old clients sent {task_ids: [...]} — accept that shape too."""
    cls = create_class(auth_client)
    t1 = create_task(auth_client, class_id=cls.id, title="A")
    t2 = create_task(auth_client, class_id=cls.id, title="B")
    r = auth_client.post(
        "/tasks/reorder",
        json={"task_ids": [t2["id"], t1["id"]]},
        headers={"Accept": "application/json"},
    )
    assert r.status_code == 200
    assert db_get(main.Task, t2["id"]).position == 0
    assert db_get(main.Task, t1["id"]).position == 1


# ---- Per-day overrides ---------------------------------------------------

def test_reorder_day_creates_overrides(auth_client):
    cls = create_class(auth_client)
    t1 = create_task(auth_client, class_id=cls.id, title="A")
    t2 = create_task(auth_client, class_id=cls.id, title="B")
    auth_client.post(
        "/tasks/reorder-day",
        json={"day": "2030-06-15", "items": [
            {"kind": "task", "id": t2["id"]},
            {"kind": "task", "id": t1["id"]},
        ]},
        headers={"Accept": "application/json"},
    )
    rows = db_query_all(
        select(main.DayItemPosition).where(
            main.DayItemPosition.day_date == "2030-06-15"
        )
    )
    by_id = {r.item_id: r.position for r in rows}
    assert by_id[t2["id"]] == 0
    assert by_id[t1["id"]] == 1


def test_reorder_day_replaces_existing_overrides(auth_client):
    """Each call is authoritative — items not in the new list have
    their override DELETED so they revert to global position."""
    cls = create_class(auth_client)
    t1 = create_task(auth_client, class_id=cls.id, title="A")
    t2 = create_task(auth_client, class_id=cls.id, title="B")
    # First call: both items.
    auth_client.post(
        "/tasks/reorder-day",
        json={"day": "2030-06-15", "items": [
            {"kind": "task", "id": t1["id"]},
            {"kind": "task", "id": t2["id"]},
        ]},
        headers={"Accept": "application/json"},
    )
    # Second call: only t1. t2's override should disappear.
    auth_client.post(
        "/tasks/reorder-day",
        json={"day": "2030-06-15", "items": [
            {"kind": "task", "id": t1["id"]},
        ]},
        headers={"Accept": "application/json"},
    )
    rows = db_query_all(
        select(main.DayItemPosition).where(
            main.DayItemPosition.day_date == "2030-06-15"
        )
    )
    item_ids = {r.item_id for r in rows}
    assert t1["id"] in item_ids
    assert t2["id"] not in item_ids


def test_reorder_day_isolates_days(auth_client):
    """Reordering on Friday must NOT affect Saturday's overrides."""
    cls = create_class(auth_client)
    t = create_task(auth_client, class_id=cls.id, title="Multi-day")
    auth_client.post(
        "/tasks/reorder-day",
        json={"day": "2030-06-15", "items": [{"kind": "task", "id": t["id"]}]},
        headers={"Accept": "application/json"},
    )
    auth_client.post(
        "/tasks/reorder-day",
        json={"day": "2030-06-16", "items": [{"kind": "task", "id": t["id"]}]},
        headers={"Accept": "application/json"},
    )
    rows = db_query_all(select(main.DayItemPosition))
    days = {r.day_date for r in rows}
    assert days == {"2030-06-15", "2030-06-16"}


def test_reorder_day_rejects_bad_day_format(auth_client):
    cls = create_class(auth_client)
    t = create_task(auth_client, class_id=cls.id, title="A")
    r = auth_client.post(
        "/tasks/reorder-day",
        json={"day": "not-a-date", "items": [{"kind": "task", "id": t["id"]}]},
        headers={"Accept": "application/json"},
    )
    assert r.status_code == 400


# ---- Class display order -------------------------------------------------

def test_classes_reorder_persists_user_preference(auth_client):
    """POST /classes/reorder writes a JSON array to User.class_order_json.
    The home/today views key off this for class-block ordering."""
    cls1 = create_class(auth_client, code="AAA")
    cls2 = create_class(auth_client, code="BBB")
    auth_client.post(
        "/classes/reorder",
        json={"order": [str(cls2.id), str(cls1.id), "0"]},
        headers={"Accept": "application/json"},
    )
    user = db_query_first(select(main.User).where(main.User.email == "test@example.com"))
    import json
    saved = json.loads(user.class_order_json)
    assert saved == [str(cls2.id), str(cls1.id), "0"]


def test_classes_reorder_drops_unowned_ids(auth_client, second_user_client):
    """Other users' class ids in the order array are silently filtered."""
    mine = create_class(auth_client, code="MINE")
    theirs = create_class(second_user_client, code="THEIRS")
    auth_client.post(
        "/classes/reorder",
        json={"order": [str(theirs.id), str(mine.id)]},
        headers={"Accept": "application/json"},
    )
    user = db_query_first(select(main.User).where(main.User.email == "test@example.com"))
    import json
    saved = json.loads(user.class_order_json)
    # `theirs.id` got dropped; only mine and the personal sentinel remain.
    assert str(theirs.id) not in saved
    assert str(mine.id) in saved
