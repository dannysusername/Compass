"""/me.json + /settings/timezone + calendar token rotation. The
timezone endpoint is auto-called from base.html on every page load,
so its short-circuit-when-unchanged behavior matters for steady-state
performance, not just correctness."""
from sqlmodel import select

import main
from .conftest import db_query_first, get_user


def test_me_json_includes_timezone(auth_client):
    user = get_user("test@example.com")
    user.timezone = "America/Los_Angeles"
    from sqlmodel import Session
    with Session(main.engine) as s:
        s.merge(user)
        s.commit()
    body = auth_client.get("/me.json").json()
    assert body["email"] == "test@example.com"
    assert body["timezone"] == "America/Los_Angeles"


# ---- /settings/timezone ---------------------------------------------------

def test_set_timezone_saves_valid_iana(auth_client):
    r = auth_client.post(
        "/settings/timezone",
        data={"tz": "Europe/Berlin"},
    )
    assert r.status_code == 200
    assert get_user("test@example.com").timezone == "Europe/Berlin"


def test_set_timezone_rejects_invalid(auth_client):
    r = auth_client.post(
        "/settings/timezone",
        data={"tz": "Mars/Olympus_Mons"},
    )
    assert r.status_code in (400, 401)
    assert get_user("test@example.com").timezone is None


def test_set_timezone_rejects_empty(auth_client):
    r = auth_client.post("/settings/timezone", data={"tz": ""})
    assert r.status_code in (400, 401)


def test_set_timezone_short_circuits_when_unchanged(auth_client):
    """base.html JS posts on every page load; if the value matches,
    the route should NOT bother writing to the DB. The 'unchanged' flag
    in the response tells the test no DB write happened."""
    auth_client.post("/settings/timezone", data={"tz": "America/Chicago"})
    r = auth_client.post("/settings/timezone", data={"tz": "America/Chicago"})
    body = r.json()
    assert body.get("saved") is True
    assert body.get("unchanged") is True


# ---- Calendar token rotation ----------------------------------------------

def test_calendar_token_rotation_changes_token(auth_client):
    user = get_user("test@example.com")
    original = user.calendar_token
    auth_client.post(
        "/settings/calendar/regenerate", follow_redirects=False,
    )
    user2 = get_user("test@example.com")
    assert user2.calendar_token != original


def test_old_token_404s_after_rotation(auth_client, client):
    """Subscribing to the old URL after the user rotates their token
    must return 404 — that's the 'kick stale Apple subscriptions' fix."""
    user = get_user("test@example.com")
    old_token = user.calendar_token
    auth_client.post(
        "/settings/calendar/regenerate", follow_redirects=False,
    )
    r = client.get(f"/calendar/{old_token}.ics")
    assert r.status_code == 404


# ---- Auth surface --------------------------------------------------------

def test_login_with_correct_password(client):
    client.post("/signup", data={"email": "u@u.com", "password": "password1"},
                follow_redirects=False)
    r = client.post("/login", data={"email": "u@u.com", "password": "password1"},
                    follow_redirects=False)
    assert r.status_code in (302, 303)


def test_login_wrong_password_rejects(client):
    client.post("/signup", data={"email": "u@u.com", "password": "password1"},
                follow_redirects=False)
    r = client.post("/login", data={"email": "u@u.com", "password": "wrong-pw"},
                    follow_redirects=False)
    assert r.status_code in (400, 401)


def test_login_unknown_email_rejects(client):
    r = client.post("/login", data={"email": "ghost@x.com", "password": "password1"},
                    follow_redirects=False)
    assert r.status_code in (400, 401)


def test_logout_clears_session(auth_client):
    auth_client.post("/logout", follow_redirects=False)
    r = auth_client.get("/me.json", headers={"Accept": "application/json"})
    assert r.status_code == 401
