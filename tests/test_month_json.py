"""GET /month.json — full-month JSON shape used by the browser
extension's Month view. Covers:

  - Default (no `month=` arg) returns the current month, anchored to the
    server's today.
  - Explicit `month=YYYY-MM` returns exactly that month's days, with the
    right number of days (28-31) and prev/next strings that wrap years.
  - A non-recurring task on a given day shows up under its class bucket
    on that day only.
  - A daily-recurring task lights up every day in the month it spans.
  - Invalid `month=` strings fall back to the current month rather than
    500ing.
"""
from datetime import date

import main
from .conftest import create_class, create_task


def _month_for(client, m=None):
    url = "/month.json" + (f"?month={m}" if m else "")
    r = client.get(url)
    assert r.status_code == 200, f"{url} -> {r.status_code}: {r.text[:200]}"
    return r.json()


def test_month_json_default_is_current_month(auth_client):
    """No `month=` arg → server picks today's YYYY-MM. The number of days
    should match the calendar month."""
    data = _month_for(auth_client)
    today = date.today()
    expected = f"{today.year:04d}-{today.month:02d}"
    assert data["month"] == expected
    # Quick sanity: every "days[i].date" is in the current month.
    for d in data["days"]:
        y, mo, _ = d["date"].split("-")
        assert (int(y), int(mo)) == (today.year, today.month)


def test_month_json_explicit_month_has_correct_day_count(auth_client):
    """June has 30 days, July has 31, February 2024 has 29."""
    cases = {
        "2030-06": 30,
        "2030-07": 31,
        "2030-02": 28,
        "2024-02": 29,  # leap
    }
    for m, n in cases.items():
        data = _month_for(auth_client, m)
        assert data["month"] == m
        assert len(data["days"]) == n, f"{m} expected {n} days, got {len(data['days'])}"


def test_month_json_prev_next_wrap_at_year_boundary(auth_client):
    jan = _month_for(auth_client, "2030-01")
    assert jan["prev_month"] == "2029-12"
    assert jan["next_month"] == "2030-02"
    dec = _month_for(auth_client, "2030-12")
    assert dec["prev_month"] == "2030-11"
    assert dec["next_month"] == "2031-01"


def test_month_json_includes_task_on_its_day_only(auth_client):
    """Non-recurring task with due_at = 2030-06-15 17:00 should appear
    in June 15's bucket and nowhere else in the month."""
    cls = create_class(auth_client)
    create_task(auth_client, class_id=cls.id, title="OneShot",
                due_at="2030-06-15T17:00")
    data = _month_for(auth_client, "2030-06")
    days_with = [d["date"] for d in data["days"]
                 for b in d["buckets"]
                 for it in b["items"] if it["title"] == "OneShot"]
    assert days_with == ["2030-06-15"], f"expected only 2030-06-15, got {days_with}"


def test_month_json_recurring_task_lights_every_day(auth_client):
    """FREQ=DAILY anchored 2030-06-15 should render on June 15..30 (16
    days) when looking at the June month payload — no overflow into July."""
    cls = create_class(auth_client)
    create_task(auth_client, class_id=cls.id, title="Daily",
                due_at="2030-06-15T17:00",
                rrule="FREQ=DAILY")
    data = _month_for(auth_client, "2030-06")
    days_with = [d["date"] for d in data["days"]
                 for b in d["buckets"]
                 for it in b["items"] if it["title"] == "Daily"]
    # June 15 .. June 30 inclusive = 16 days.
    assert len(days_with) == 16, f"expected 16, got {len(days_with)}: {days_with}"
    assert days_with[0] == "2030-06-15"
    assert days_with[-1] == "2030-06-30"


def test_month_json_invalid_month_falls_back_to_current(auth_client):
    """Garbage month string shouldn't 500 — server returns the current
    month per the explicit fallback in `month_json`."""
    r = auth_client.get("/month.json?month=banana")
    assert r.status_code == 200
    today = date.today()
    assert r.json()["month"] == f"{today.year:04d}-{today.month:02d}"


def test_month_json_does_not_leak_other_users_tasks(auth_client, second_user_client):
    cls = create_class(auth_client)
    create_task(auth_client, class_id=cls.id, title="Mine",
                due_at="2030-06-15T17:00")
    cls_b = create_class(second_user_client, code="OTHER")
    create_task(second_user_client, class_id=cls_b.id, title="Theirs",
                due_at="2030-06-15T17:00")
    data = _month_for(auth_client, "2030-06")
    titles = [it["title"]
              for d in data["days"]
              for b in d["buckets"]
              for it in b["items"]]
    assert "Mine" in titles
    assert "Theirs" not in titles


def test_month_json_requires_login(client):
    """Unauthenticated callers get 401 (the extension converts that to
    showLogin())."""
    r = client.get("/month.json", headers={"Accept": "application/json"})
    assert r.status_code == 401
