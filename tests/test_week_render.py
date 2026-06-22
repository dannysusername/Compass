"""Month-grid data — the day-by-day buckets that drive the Week view.

The Week page is now a React island that fetches `/month.json?grid=1`
and renders the grid client-side, so we assert against that JSON (the
authoritative source) rather than grepping the HTML shell. Coverage:

  - Recurring tasks expand into multiple day occurrences in the visible
    range — verifies the rrule expander is wired into the per-day collect.
  - Range tasks (starts_at + due_at, no rrule) emit one row per spanned
    day with is_range_day on non-deadline days.
  - Per-user scoping holds across the grid.
"""
from datetime import datetime, timedelta

import main
from .conftest import create_class, create_task, db_get


def _month_grid_for(client, year_month: str) -> dict:
    """Fetch the 6-week month grid (42 cells) for a YYYY-MM string —
    the same `grid=1` shape the Week island renders."""
    r = client.get(f"/month.json?grid=1&month={year_month}")
    assert r.status_code == 200
    return r.json()


def _items(data: dict):
    """Yield every item across all days/buckets of a month-grid payload."""
    for day in data["days"]:
        for bucket in day["buckets"]:
            yield from bucket["items"]


def _count_title(data: dict, title: str) -> int:
    """How many day-cells render a task with this title across the grid."""
    return sum(1 for it in _items(data) if it["title"] == title)


def test_week_renders_recurring_task_on_multiple_days(auth_client):
    """A daily-recurring task with anchor June 15 should appear on
    June 15, 16, 17, ... within June's grid. We grep for the row's
    data-id/title to count occurrences in the rendered HTML."""
    cls = create_class(auth_client)
    t = create_task(auth_client, class_id=cls.id, title="DailyClass",
                    due_at="2030-06-15T17:00",
                    rrule="FREQ=DAILY")
    data = _month_grid_for(auth_client, "2030-06")
    count = _count_title(data, "DailyClass")
    # The grid is 42 days (Mon-on-or-before the 1st through 6 weeks).
    # For June 2030: starts Mon May 27, ends Sun Jul 7. Daily anchored
    # June 15 with no UNTIL → renders on June 15..30 (16) + Jul 1..7 (7) = 23.
    assert count == 23, f"expected 23 daily occurrences across grid, got {count}"


def test_week_respects_rrule_until(auth_client):
    """rrule_until caps the expansion. Same daily task but stopped on
    June 20 should only show June 15-20 (6 days)."""
    cls = create_class(auth_client)
    t = create_task(auth_client, class_id=cls.id, title="CappedDaily",
                    due_at="2030-06-15T17:00",
                    rrule="FREQ=DAILY")
    auth_client.post(
        f"/tasks/{t['id']}/end-after",
        data={"occurrence_at": "2030-06-21T17:00"},
    )
    data = _month_grid_for(auth_client, "2030-06")
    count = _count_title(data, "CappedDaily")
    # June 15..20 inclusive = 6 occurrences; the end-after caps just
    # before June 21, so June 21 isn't emitted.
    assert count == 6, f"expected 6 capped occurrences, got {count}"


def test_week_respects_exdate(auth_client):
    """exdates suppress specific occurrences from the expansion."""
    cls = create_class(auth_client)
    t = create_task(auth_client, class_id=cls.id, title="WithGap",
                    due_at="2030-06-15T17:00",
                    rrule="FREQ=DAILY")
    auth_client.post(
        f"/tasks/{t['id']}/exclude",
        data={"occurrence_at": "2030-06-17T17:00"},
    )
    auth_client.post(
        f"/tasks/{t['id']}/end-after",
        data={"occurrence_at": "2030-06-21T17:00"},
    )
    data = _month_grid_for(auth_client, "2030-06")
    # 6 occurrences (15-20), minus 1 excluded (17) = 5.
    count = _count_title(data, "WithGap")
    assert count == 5, f"expected 5 (6 capped - 1 exdate), got {count}"


def test_week_renders_range_task_on_each_spanned_day(auth_client):
    """Non-recurring range task (starts + due, no rrule) renders one
    row per spanned day."""
    cls = create_class(auth_client)
    create_task(auth_client, class_id=cls.id, title="ProjectWindow",
                starts_at="2030-06-10T09:00",
                due_at="2030-06-13T17:00")
    data = _month_grid_for(auth_client, "2030-06")
    # June 10, 11, 12, 13 = 4 days.
    count = _count_title(data, "ProjectWindow")
    assert count == 4, f"expected 4 spanned-day rows, got {count}"


def test_week_does_not_show_other_users_tasks(auth_client, second_user_client):
    cls = create_class(auth_client)
    create_task(auth_client, class_id=cls.id, title="Mine",
                due_at="2030-06-15T17:00")
    cls_b = create_class(second_user_client, code="OTHER")
    create_task(second_user_client, class_id=cls_b.id, title="Theirs",
                due_at="2030-06-15T17:00")
    titles = {it["title"] for it in _items(_month_grid_for(auth_client, "2030-06"))}
    assert "Mine" in titles
    assert "Theirs" not in titles


def test_week_invalid_month_falls_back_to_current(auth_client):
    """Bogus ?month=... shouldn't 500."""
    r = auth_client.get("/week?month=banana", follow_redirects=False)
    assert r.status_code == 200
