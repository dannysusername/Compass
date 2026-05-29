"""Reparse a syllabus: POST /syllabus/{id}/reparse re-runs the Grok parse,
which (via process_syllabus) wipes + recreates the class's events. Gated +
counted exactly like a fresh upload, and 404s on cross-user access.
"""
from datetime import datetime, timezone

from sqlmodel import Session, select

import main
from .conftest import create_class, db_query_all, get_user


def _make_syllabus(cls):
    """Insert a Syllabus row for the class (as upload would have)."""
    with Session(main.engine) as s:
        syl = main.Syllabus(
            class_id=cls.id, filename="syllabus.pdf",
            raw_text="Quiz 1 on Sept 5. Midterm Oct 10.",
            parsed_at=datetime.now(timezone.utc),
        )
        s.add(syl)
        s.commit()
        s.refresh(syl)
        return syl.id


def _seed_event(cls, title):
    with Session(main.engine) as s:
        ev = main.CalendarEvent(
            class_id=cls.id, class_code=cls.code, title=title, kind="quiz",
            actionable=True, added_to_calendar=False,
            starts_at=datetime(2030, 9, 1, 14, 0),
        )
        s.add(ev)
        s.commit()


def _set_own_key(email="test@example.com", key="xai-test-key"):
    with Session(main.engine) as s:
        u = s.exec(select(main.User).where(main.User.email == email)).first()
        u.xai_api_key = key
        s.add(u)
        s.commit()


def test_reparse_replaces_events(auth_client, monkeypatch):
    """Reparse wipes the old parsed events and creates fresh pending ones."""
    cls = create_class(auth_client)
    syl_id = _make_syllabus(cls)
    _seed_event(cls, "STALE old event")
    _set_own_key()  # own key → entitlement gate passes, uncapped

    # Stub the Grok call so the background task runs deterministically.
    monkeypatch.setattr(main, "parse_syllabus_with_grok", lambda text, user_key: {
        "events": [
            {"title": "FRESH quiz", "kind": "quiz", "actionable": True,
             "starts_at": "2030-10-01T13:00:00"},
        ],
    })

    r = auth_client.post(f"/syllabus/{syl_id}/reparse",
                         headers={"Accept": "application/json"})
    assert r.status_code == 200, r.text
    assert r.json()["syllabus_id"] == syl_id

    titles = [e.title for e in db_query_all(
        select(main.CalendarEvent).where(main.CalendarEvent.class_id == cls.id))]
    assert "STALE old event" not in titles  # wiped
    assert "FRESH quiz" in titles           # recreated
    # New parses land pending (added_to_calendar False).
    fresh = db_query_all(select(main.CalendarEvent).where(
        main.CalendarEvent.title == "FRESH quiz"))
    assert fresh and fresh[0].added_to_calendar is False


def test_reparse_blocked_without_key(auth_client):
    """No own key + no server key (tests don't set XAI_API_KEY) → need_key."""
    cls = create_class(auth_client)
    syl_id = _make_syllabus(cls)
    r = auth_client.post(f"/syllabus/{syl_id}/reparse",
                         headers={"Accept": "application/json"})
    assert r.status_code == 400
    assert r.json()["error"] == "need_key"


def test_reparse_html_bounces_to_settings(auth_client):
    cls = create_class(auth_client)
    syl_id = _make_syllabus(cls)
    r = auth_client.post(f"/syllabus/{syl_id}/reparse", follow_redirects=False)
    assert r.status_code == 303
    assert "/settings?need_key=1" in r.headers["location"]


def test_reparse_cross_user_404(auth_client, second_user_client):
    """A second user cannot reparse the first user's syllabus."""
    cls = create_class(auth_client)
    syl_id = _make_syllabus(cls)
    r = second_user_client.post(f"/syllabus/{syl_id}/reparse",
                                headers={"Accept": "application/json"})
    assert r.status_code == 404
