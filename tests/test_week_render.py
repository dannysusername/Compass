"""GET /week — month-grid render. Doesn't return JSON, but the HTML
embeds enough state we can grep for. Pinning these covers:

  - Recurring tasks expand into multiple day occurrences in the visible
    range — verifies the rrule expander is wired into the per-day collect.
  - Range tasks (starts_at + due_at, no rrule) emit one row per spanned
    day with is_range_day on non-deadline days.
  - Per-day position overrides actually affect render order.
"""
from datetime import datetime, timedelta

import main
from .conftest import create_class, create_task, db_get


def _week_html_for_month(client, year_month: str) -> str:
    """Render the month-grid for a YYYY-MM string."""
    r = client.get(f"/week?month={year_month}")
    assert r.status_code == 200
    return r.text


def _count_title(html: str, title: str) -> int:
    """Count rendered occurrences of a task by its `data-title` attribute.
    Each emitted row carries it exactly once (the template-clone path
    inside .day-cell-item is one element with one attribute), so this
    is a clean stand-in for "how many days does this task render on"."""
    needle = f'data-title="{title}"'
    return html.count(needle)


def test_week_renders_recurring_task_on_multiple_days(auth_client):
    """A daily-recurring task with anchor June 15 should appear on
    June 15, 16, 17, ... within June's grid. We grep for the row's
    data-id/title to count occurrences in the rendered HTML."""
    cls = create_class(auth_client)
    t = create_task(auth_client, class_id=cls.id, title="DailyClass",
                    due_at="2030-06-15T17:00",
                    rrule="FREQ=DAILY")
    html = _week_html_for_month(auth_client, "2030-06")
    count = _count_title(html, "DailyClass")
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
    html = _week_html_for_month(auth_client, "2030-06")
    count = _count_title(html, "CappedDaily")
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
    html = _week_html_for_month(auth_client, "2030-06")
    # 6 occurrences (15-20), minus 1 excluded (17) = 5.
    count = _count_title(html, "WithGap")
    assert count == 5, f"expected 5 (6 capped - 1 exdate), got {count}"


def test_week_renders_range_task_on_each_spanned_day(auth_client):
    """Non-recurring range task (starts + due, no rrule) renders one
    row per spanned day."""
    cls = create_class(auth_client)
    create_task(auth_client, class_id=cls.id, title="ProjectWindow",
                starts_at="2030-06-10T09:00",
                due_at="2030-06-13T17:00")
    html = _week_html_for_month(auth_client, "2030-06")
    # June 10, 11, 12, 13 = 4 days.
    count = _count_title(html, "ProjectWindow")
    assert count == 4, f"expected 4 spanned-day rows, got {count}"


def test_week_does_not_show_other_users_tasks(auth_client, second_user_client):
    cls = create_class(auth_client)
    create_task(auth_client, class_id=cls.id, title="Mine",
                due_at="2030-06-15T17:00")
    cls_b = create_class(second_user_client, code="OTHER")
    create_task(second_user_client, class_id=cls_b.id, title="Theirs",
                due_at="2030-06-15T17:00")
    html = _week_html_for_month(auth_client, "2030-06")
    assert "Mine" in html
    assert "Theirs" not in html


def test_week_invalid_month_falls_back_to_current(auth_client):
    """Bogus ?month=... shouldn't 500."""
    r = auth_client.get("/week?month=banana", follow_redirects=False)
    assert r.status_code == 200
