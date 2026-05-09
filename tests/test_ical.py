"""iCal feed shape — /calendar/{token}.ics and /calendar.ics. Pins down
the rules that have already burned us once:

  - rrule + range: emit DTSTART=due_at, NO DTEND. Mirrors `_emit_task`'s
    "rrule trumps range" rule. Pre-fix this emitted DTSTART=starts +
    DTEND=due + RRULE which Apple Calendar turned into a flood.
  - all-day: DTSTART;VALUE=DATE (no time).
  - non-recurring multi-day range: DTSTART + DTEND span.
  - single-date timed: DTSTART only.
  - Token routing: the public token URL returns the same body as the
    cookie URL, and an unknown token is 404 (not 401, doesn't leak).
"""
import main
from .conftest import create_class, create_task, db_get, get_user


def _feed_for(client):
    """Cookie-auth fetch of /calendar.ics — the one driven from a
    logged-in tab. Returns the raw text body."""
    r = client.get("/calendar.ics")
    assert r.status_code == 200
    return r.text


def _vevent_blocks(body):
    """Split a feed body into individual VEVENT blocks for inspection."""
    blocks, current = [], None
    for line in body.splitlines():
        if line == "BEGIN:VEVENT":
            current = []
        elif line == "END:VEVENT":
            if current is not None:
                blocks.append(current)
            current = None
        elif current is not None:
            current.append(line)
    return blocks


def _block_for_uid(blocks, uid_substr):
    for b in blocks:
        if any(uid_substr in line for line in b):
            return b
    return None


# ---- Single-date timed task ----------------------------------------------

def test_feed_emits_single_date_task(auth_client):
    cls = create_class(auth_client, code="MATH")
    t = create_task(auth_client, class_id=cls.id, title="Quiz",
                    due_at="2030-06-15T17:00")
    body = _feed_for(auth_client)
    block = _block_for_uid(_vevent_blocks(body), f"compass-task-{t['id']}@compass")
    assert block is not None
    assert any(line.startswith("DTSTART") for line in block)
    # Single-date task: no DTEND. (Range tasks have it; this one shouldn't.)
    assert not any(line.startswith("DTEND") for line in block)


# ---- All-day -------------------------------------------------------------

def test_feed_all_day_uses_value_date(auth_client):
    cls = create_class(auth_client)
    t = create_task(auth_client, class_id=cls.id, title="Holiday",
                    due_at="2030-06-15T00:00", is_all_day="1")
    body = _feed_for(auth_client)
    block = _block_for_uid(_vevent_blocks(body), f"compass-task-{t['id']}@compass")
    assert block is not None
    dtstart = next(line for line in block if line.startswith("DTSTART"))
    assert "VALUE=DATE" in dtstart, (
        f"all-day task should serialize DTSTART;VALUE=DATE, got: {dtstart}"
    )


# ---- Multi-day range, NON-recurring --------------------------------------

def test_feed_range_non_recurring_emits_dtstart_and_dtend(auth_client):
    cls = create_class(auth_client)
    t = create_task(auth_client, class_id=cls.id, title="Project window",
                    starts_at="2030-06-10T09:00", due_at="2030-06-15T17:00")
    body = _feed_for(auth_client)
    block = _block_for_uid(_vevent_blocks(body), f"compass-task-{t['id']}@compass")
    assert block is not None
    assert any(line.startswith("DTSTART") for line in block)
    assert any(line.startswith("DTEND") for line in block)


# ---- rrule + range — the regression ---------------------------------------

def test_feed_recurring_with_range_emits_no_dtend(auth_client):
    """A task with BOTH rrule AND starts_at must emit DTSTART=due_at +
    RRULE only — no DTEND. Otherwise Apple Calendar interprets each
    daily occurrence as a multi-day banner repeating daily, which the
    user sees as a flood of duplicate events. The web app's _emit_task
    already ignores starts_at when rrule is set; the feed must mirror."""
    cls = create_class(auth_client)
    t = create_task(auth_client, class_id=cls.id, title="Daily class",
                    starts_at="2030-06-10T09:00",
                    due_at="2030-06-15T10:00",
                    rrule="FREQ=DAILY")
    body = _feed_for(auth_client)
    block = _block_for_uid(_vevent_blocks(body), f"compass-task-{t['id']}@compass")
    assert block is not None
    assert any(line.startswith("RRULE") for line in block)
    assert not any(line.startswith("DTEND") for line in block), (
        "rrule + range emitted DTEND — Apple will flood the calendar"
    )


def test_feed_recurring_emits_rrule(auth_client):
    cls = create_class(auth_client)
    t = create_task(auth_client, class_id=cls.id, title="Daily",
                    due_at="2030-06-15T17:00", rrule="FREQ=DAILY")
    body = _feed_for(auth_client)
    block = _block_for_uid(_vevent_blocks(body), f"compass-task-{t['id']}@compass")
    assert any("FREQ=DAILY" in line for line in block)


def test_feed_recurring_with_until_emits_until_in_rrule(auth_client):
    """rrule_until is folded into the RRULE line as UNTIL=...Z (UTC)."""
    cls = create_class(auth_client)
    t = create_task(auth_client, class_id=cls.id, title="Capped",
                    due_at="2030-06-15T17:00", rrule="FREQ=DAILY")
    auth_client.post(
        f"/tasks/{t['id']}/end-after",
        data={"occurrence_at": "2030-06-20T17:00"},
    )
    body = _feed_for(auth_client)
    block = _block_for_uid(_vevent_blocks(body), f"compass-task-{t['id']}@compass")
    rrule_line = next((line for line in block if line.startswith("RRULE")), "")
    assert "UNTIL=" in rrule_line


# ---- Excluded tasks --------------------------------------------------------

def test_feed_emits_exdate_for_excluded_occurrence(auth_client):
    cls = create_class(auth_client)
    t = create_task(auth_client, class_id=cls.id, title="Recurring",
                    due_at="2030-06-15T17:00", rrule="FREQ=DAILY")
    auth_client.post(
        f"/tasks/{t['id']}/exclude",
        data={"occurrence_at": "2030-06-17T17:00"},
    )
    body = _feed_for(auth_client)
    block = _block_for_uid(_vevent_blocks(body), f"compass-task-{t['id']}@compass")
    assert any(line.startswith("EXDATE") for line in block)


# ---- Completed task is omitted -------------------------------------------

def test_feed_omits_completed_tasks(auth_client):
    cls = create_class(auth_client)
    t = create_task(auth_client, class_id=cls.id, title="Done",
                    due_at="2030-06-15T17:00")
    auth_client.post(f"/tasks/{t['id']}/toggle",
                     headers={"Accept": "application/json"})
    body = _feed_for(auth_client)
    assert f"compass-task-{t['id']}@compass" not in body


# ---- Token routing -------------------------------------------------------

def test_token_route_returns_feed(auth_client):
    """The public-but-unguessable URL is what Apple Calendar polls."""
    create_class(auth_client)
    user = get_user("test@example.com")
    r = auth_client.get(f"/calendar/{user.calendar_token}.ics")
    assert r.status_code == 200
    assert "BEGIN:VCALENDAR" in r.text


def test_token_route_unknown_token_returns_404(client):
    r = client.get("/calendar/totallyfaketoken.ics")
    assert r.status_code == 404


def test_token_route_does_not_leak_other_users(auth_client, second_user_client):
    """user A's token URL must not surface user B's tasks."""
    cls = create_class(auth_client)
    create_task(auth_client, class_id=cls.id, title="Mine",
                due_at="2030-06-15T17:00")
    cls_b = create_class(second_user_client)
    create_task(second_user_client, class_id=cls_b.id, title="Theirs",
                due_at="2030-06-15T17:00")
    user_a = get_user("test@example.com")
    r = auth_client.get(f"/calendar/{user_a.calendar_token}.ics")
    body = r.text
    assert "Mine" in body
    assert "Theirs" not in body


# ---- Calendar-level metadata ---------------------------------------------

def test_feed_carries_x_published_ttl(auth_client):
    create_class(auth_client)
    body = _feed_for(auth_client)
    # PT15M is what Apple polls on; pinned by an earlier bug fix.
    assert "X-PUBLISHED-TTL:PT15M" in body
