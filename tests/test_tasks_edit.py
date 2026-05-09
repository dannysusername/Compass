"""POST /tasks/{id}/edit — partial update behavior.

These tests pin down the contract: editing field X must NEVER touch
field Y unless the caller intended to. The user reported a bug where
"dates changed unexpectedly" — most plausibly a path where saving one
field silently clobbered another. Each test below sends a tightly
scoped form payload and asserts that everything else survived.
"""
import main
from .conftest import create_class, create_task, db_get


# ---- Helpers --------------------------------------------------------------

def _make_full_task(client, **overrides):
    """A task with EVERY field populated, so any "preservation" test can
    detect a wrongful overwrite. Caller can override individual fields
    via kwargs."""
    cls = create_class(client, code="HIST101", name="History")
    fields = {
        "title": "Original title",
        "due_at": "2030-06-15T17:00",
        "starts_at": "2030-06-10T09:00",
        "notes": "Original notes",
    }
    fields.update(overrides)
    t = create_task(client, class_id=cls.id, **fields)
    return cls, db_get(main.Task, t["id"])


def _edit(client, task_id, **fields):
    """POST /tasks/{id}/edit with the given form fields and assert success."""
    r = client.post(
        f"/tasks/{task_id}/edit",
        data=fields,
        headers={"Accept": "application/json"},
    )
    assert r.status_code == 200, f"edit failed: {r.status_code} — {r.text[:200]}"
    return r.json()


# ---- Title-only edit ------------------------------------------------------

def test_edit_only_title_preserves_everything_else(auth_client):
    _, t0 = _make_full_task(auth_client)
    _edit(auth_client, t0.id, title="Updated title")
    t = db_get(main.Task, t0.id)
    assert t.title == "Updated title"
    assert t.due_at == t0.due_at
    assert t.starts_at == t0.starts_at
    assert t.notes == t0.notes
    assert t.class_id == t0.class_id


def test_edit_empty_title_rejected(auth_client):
    _, t0 = _make_full_task(auth_client)
    r = auth_client.post(
        f"/tasks/{t0.id}/edit",
        data={"title": ""},
        headers={"Accept": "application/json"},
    )
    assert r.status_code == 400
    # Original title preserved.
    t = db_get(main.Task, t0.id)
    assert t.title == "Original title"


# ---- Notes-only edit ------------------------------------------------------

def test_edit_only_notes_preserves_dates(auth_client):
    _, t0 = _make_full_task(auth_client)
    _edit(auth_client, t0.id, notes="Updated notes")
    t = db_get(main.Task, t0.id)
    assert t.notes == "Updated notes"
    assert t.due_at == t0.due_at
    assert t.starts_at == t0.starts_at


def test_edit_clears_notes(auth_client):
    _, t0 = _make_full_task(auth_client)
    _edit(auth_client, t0.id, notes="")
    t = db_get(main.Task, t0.id)
    assert t.notes is None


# ---- Class-only edit (drag-to-different-class flow) -----------------------

def test_edit_only_class_id_preserves_dates(auth_client):
    """The drag-to-class flow PATCHes class_id only. Dates must survive."""
    _, t0 = _make_full_task(auth_client)
    cls2 = create_class(auth_client, code="ART200", name="Art")
    _edit(auth_client, t0.id, class_id=str(cls2.id))
    t = db_get(main.Task, t0.id)
    assert t.class_id == cls2.id
    assert t.due_at == t0.due_at
    assert t.starts_at == t0.starts_at
    assert t.title == t0.title


def test_edit_class_id_zero_makes_personal(auth_client):
    """class_id='0' is the Personal sentinel from the dropdown."""
    _, t0 = _make_full_task(auth_client)
    _edit(auth_client, t0.id, class_id="0")
    t = db_get(main.Task, t0.id)
    assert t.class_id is None


# ---- Date edits — the "dates changed unexpectedly" bug class --------------

def test_edit_only_due_at_preserves_starts_at(auth_client):
    """If the form sends due_at without starts_at, the partial-update
    must NOT silently clear starts_at. (Pre-fix this clobbered it via
    _normalize_task_range receiving an empty starts_at.)"""
    _, t0 = _make_full_task(auth_client)
    original_starts = t0.starts_at
    _edit(auth_client, t0.id, due_at="2030-07-01T17:00")
    t = db_get(main.Task, t0.id)
    assert t.due_at is not None and t.due_at != t0.due_at
    assert t.starts_at == original_starts, (
        "starts_at was clobbered by a due_at-only edit"
    )


def test_edit_only_starts_at_preserves_due_at(auth_client):
    _, t0 = _make_full_task(auth_client)
    original_due = t0.due_at
    _edit(auth_client, t0.id, starts_at="2030-06-09T09:00")
    t = db_get(main.Task, t0.id)
    assert t.starts_at is not None and t.starts_at != t0.starts_at
    assert t.due_at == original_due, (
        "due_at was clobbered by a starts_at-only edit"
    )


def test_edit_both_dates_updates_both(auth_client):
    _, t0 = _make_full_task(auth_client)
    _edit(
        auth_client, t0.id,
        starts_at="2030-08-01T09:00",
        due_at="2030-08-05T17:00",
    )
    t = db_get(main.Task, t0.id)
    assert t.starts_at != t0.starts_at
    assert t.due_at != t0.due_at


def test_edit_clears_dates_when_explicitly_empty(auth_client):
    """Sending due_at='' AND starts_at='' is the documented 'clear both'
    path. Both get None."""
    _, t0 = _make_full_task(auth_client)
    _edit(auth_client, t0.id, due_at="", starts_at="")
    t = db_get(main.Task, t0.id)
    assert t.due_at is None
    assert t.starts_at is None


def test_edit_swaps_inverted_range(auth_client):
    """_normalize_task_range swaps starts/due when starts > due so the
    server stays consistent regardless of which order the user typed."""
    _, t0 = _make_full_task(auth_client)
    _edit(
        auth_client, t0.id,
        starts_at="2030-09-10T17:00",  # later
        due_at="2030-09-05T09:00",     # earlier
    )
    t = db_get(main.Task, t0.id)
    assert t.starts_at < t.due_at


# ---- Tag edits ------------------------------------------------------------

def test_edit_clears_tag(auth_client):
    """tag_id='' clears the tag back to None."""
    # Seed a tag for this user.
    r = auth_client.post(
        "/tags",
        data={"name": "Reading", "color": "#a04528"},
        headers={"Accept": "application/json"},
    )
    assert r.status_code == 200, f"create tag failed: {r.text[:200]}"
    tag_id = r.json()["id"]
    _, t0 = _make_full_task(auth_client, tag_id=str(tag_id))
    assert t0.tag_id == tag_id
    _edit(auth_client, t0.id, tag_id="")
    t = db_get(main.Task, t0.id)
    assert t.tag_id is None


# ---- All-day -------------------------------------------------------------

def test_edit_toggle_all_day_preserves_dates(auth_client):
    _, t0 = _make_full_task(auth_client)
    _edit(auth_client, t0.id, is_all_day="1")
    t = db_get(main.Task, t0.id)
    assert t.is_all_day is True
    assert t.due_at == t0.due_at, "all-day toggle must not move due_at"


def test_edit_clears_all_day(auth_client):
    _, t0 = _make_full_task(auth_client, is_all_day="1")
    _edit(auth_client, t0.id, is_all_day="")
    t = db_get(main.Task, t0.id)
    assert t.is_all_day is False


# ---- rrule transitions ---------------------------------------------------

def test_edit_set_rrule_on_non_recurring(auth_client):
    _, t0 = _make_full_task(auth_client)
    assert t0.rrule is None
    _edit(auth_client, t0.id, rrule="FREQ=DAILY")
    t = db_get(main.Task, t0.id)
    assert t.rrule == "FREQ=DAILY"


def test_edit_clear_rrule_on_first_occurrence_wipes(auth_client):
    """Editing on the FIRST occurrence (form's due_at == anchor) and
    setting rrule to empty falls through to the wipe path: rrule is
    None, rrule_until/exdates cleared, the task becomes single-date."""
    cls = create_class(auth_client)
    t = create_task(
        auth_client, class_id=cls.id,
        title="Recurring", due_at="2030-06-15T17:00",
        rrule="FREQ=DAILY",
    )
    # Editing on the original anchor day (due_at unchanged).
    _edit(auth_client, t["id"],
          due_at="2030-06-15T17:00", rrule="")
    db_t = db_get(main.Task, t["id"])
    assert db_t.rrule is None
    assert db_t.rrule_until is None


def test_edit_clear_rrule_on_later_occurrence_caps(auth_client):
    """Editing on a LATER occurrence (form's due_at > anchor) and
    setting rrule to empty triggers stop_recurrence: rrule stays,
    rrule_until is capped one second before this occurrence, anchor
    (Task.due_at) is NOT moved."""
    cls = create_class(auth_client)
    t = create_task(
        auth_client, class_id=cls.id,
        title="Recurring", due_at="2030-06-15T17:00",
        rrule="FREQ=DAILY",
    )
    original_due = db_get(main.Task, t["id"]).due_at
    # Editing on a later occurrence — form's due_at = future.
    _edit(auth_client, t["id"],
          due_at="2030-06-20T17:00", rrule="")
    db_t = db_get(main.Task, t["id"])
    # rrule preserved, rrule_until capped, anchor (due_at) NOT moved.
    assert db_t.rrule == "FREQ=DAILY"
    assert db_t.rrule_until is not None
    assert db_t.due_at == original_due, (
        "anchor moved during stop_recurrence — should stay at first occurrence"
    )


def test_edit_invalid_rrule_normalizes_to_none(auth_client):
    """_normalize_rrule allow-lists FREQ=DAILY/WEEKLY/etc. Junk → None."""
    _, t0 = _make_full_task(auth_client)
    _edit(auth_client, t0.id, rrule="FREQ=BANANA")
    t = db_get(main.Task, t0.id)
    assert t.rrule is None


# ---- Cross-user access ---------------------------------------------------

def test_cannot_edit_other_users_task(auth_client, second_user_client):
    _, t0 = _make_full_task(auth_client)
    r = second_user_client.post(
        f"/tasks/{t0.id}/edit",
        data={"title": "Hijacked"},
        headers={"Accept": "application/json"},
    )
    assert r.status_code == 404
    # Title unchanged.
    t = db_get(main.Task, t0.id)
    assert t.title == "Original title"


def test_edit_nonexistent_task_returns_404(auth_client):
    r = auth_client.post(
        "/tasks/999999/edit",
        data={"title": "ghost"},
        headers={"Accept": "application/json"},
    )
    assert r.status_code == 404
