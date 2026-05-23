"""Forgot-password / reset flow — in-process spec.

Encodes the acceptance criteria + edge cases from
`/Users/danieli/.claude/plans/typed-booping-pascal.md`. Written BEFORE the
implementation (spec-driven): on a clean tree this whole module is red
(routes + `PasswordResetToken` absent) and goes green once the feature
lands.

The sender is monkeypatched so the reset link is captured deterministically
instead of scraped from `compass.log` (conftest sets COMPASS_ENV=test, so
the real backend is the no-network `_log_send` anyway).
"""
import re
from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import Session, select

import main
from .conftest import db_query_all, db_query_first, get_user

LINK_RE = re.compile(r"https?://[^\s\"'<>]+/reset/([A-Za-z0-9_\-]{20,})")


@pytest.fixture(autouse=True)
def _reset_module_state():
    """reset_db wipes the DB but not module globals. The /forgot throttle
    dict + last-link sink persist across tests in one process, so clear
    them per test for isolation."""
    main._forgot_hits.clear()
    main._last_reset_link = None
    yield


@pytest.fixture
def sent(monkeypatch):
    """Capture every send_email call; expose the list to the test."""
    box = []

    def _capture(*args, **kwargs):
        box.append(kwargs or {"args": args})

    monkeypatch.setattr(main, "send_email", _capture)
    return box


def _link_from(sent_box):
    assert sent_box, "no email was sent"
    blob = " ".join(str(v) for kw in sent_box for v in kw.values())
    m = LINK_RE.search(blob)
    assert m, f"no reset link in sent email: {sent_box!r}"
    return m.group(0), m.group(1)  # full url, raw token


def _request_reset(client, email, sent_box):
    r = client.post("/forgot", data={"email": email}, follow_redirects=False)
    assert r.status_code == 200
    return _link_from(sent_box)


def _tokens():
    return db_query_all(select(main.PasswordResetToken))


# ---- /forgot ----

def test_login_links_to_forgot(client):
    r = client.get("/login")
    assert r.status_code == 200
    assert 'href="/forgot"' in r.text  # AC1


def test_forgot_page_renders(client):
    r = client.get("/forgot")
    assert r.status_code == 200
    assert 'name="email"' in r.text  # AC1


def test_forgot_known_email_creates_token_and_sends(client, auth_client, sent):
    # auth_client created test@example.com
    r = client.post("/forgot", data={"email": "test@example.com"},
                     follow_redirects=False)
    assert r.status_code == 200  # AC2 neutral 200, not a redirect
    rows = _tokens()
    assert len(rows) == 1
    assert rows[0].used_at is None
    assert rows[0].expires_at > datetime.now(timezone.utc).replace(tzinfo=None) \
        or rows[0].expires_at > datetime.now(timezone.utc)
    assert len(sent) == 1
    _link_from(sent)


def test_forgot_unknown_email_is_indistinguishable(client, auth_client, sent):
    known = client.post("/forgot", data={"email": "test@example.com"},
                         follow_redirects=False)
    sent.clear()
    unknown = client.post("/forgot", data={"email": "nobody@example.com"},
                           follow_redirects=False)
    assert known.status_code == unknown.status_code == 200  # AC3
    assert known.text == unknown.text  # byte-identical
    assert db_query_all(select(main.PasswordResetToken))  # only the known one
    assert len(_tokens()) == 1
    assert sent == []  # zero sends for unknown


def test_forgot_email_normalized(client, auth_client, sent):
    # E14: case/whitespace variance still matches the stored row
    _request_reset(client, "  TEST@Example.COM ", sent)
    assert len(_tokens()) == 1


def test_forgot_invalidates_prior_token(client, auth_client, sent):
    _, _ = _request_reset(client, "test@example.com", sent)
    old_url, old_tok = _link_from(sent)
    sent.clear()
    new_url, new_tok = _request_reset(client, "test@example.com", sent)
    assert old_tok != new_tok  # E12 newest-wins
    assert client.get(f"/reset/{old_tok}").status_code == 400  # old dead
    assert client.get(f"/reset/{new_tok}").status_code == 200  # new live


def test_forgot_send_failure_still_neutral(client, auth_client, monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("smtp down")

    monkeypatch.setattr(main, "send_email", _boom)
    r = client.post("/forgot", data={"email": "test@example.com"},
                     follow_redirects=False)
    assert r.status_code == 200  # E13 anti-enumeration: still neutral
    assert len(_tokens()) == 1  # row kept


def test_forgot_rate_limited(client, auth_client, sent):
    codes = [client.post("/forgot", data={"email": "test@example.com"},
                          follow_redirects=False).status_code
             for _ in range(6)]
    assert all(c == 200 for c in codes)  # E15 always neutral
    assert 0 < len(sent) <= 3  # throttle bounds sends


# ---- /reset GET ----

def test_reset_get_valid(client, auth_client, sent):
    url, tok = _request_reset(client, "test@example.com", sent)
    r = client.get(f"/reset/{tok}")
    assert r.status_code == 200  # AC5
    assert 'name="password"' in r.text and 'name="confirm"' in r.text


@pytest.mark.parametrize("bad", ["garbage", "x",
                                 "invalidtokeninvalidtokeninvalid", "a" * 60])
def test_reset_get_invalid_tokens_generic_400(client, bad):
    r = client.get(f"/reset/{bad}")
    assert r.status_code == 400  # AC6/E3 — one generic page


def test_reset_get_expired(client, auth_client, sent):
    url, tok = _request_reset(client, "test@example.com", sent)
    with Session(main.engine) as s:
        row = s.exec(select(main.PasswordResetToken)).first()
        row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        s.add(row)
        s.commit()
    assert client.get(f"/reset/{tok}").status_code == 400  # E1


def test_reset_get_used(client, auth_client, sent):
    url, tok = _request_reset(client, "test@example.com", sent)
    with Session(main.engine) as s:
        row = s.exec(select(main.PasswordResetToken)).first()
        row.used_at = datetime.now(timezone.utc)
        s.add(row)
        s.commit()
    assert client.get(f"/reset/{tok}").status_code == 400  # E2


def test_reset_get_deleted_user(client, auth_client, sent):
    url, tok = _request_reset(client, "test@example.com", sent)
    with Session(main.engine) as s:
        u = s.exec(select(main.User).where(
            main.User.email == "test@example.com")).first()
        s.delete(u)
        s.commit()
    assert client.get(f"/reset/{tok}").status_code == 400  # E6


# ---- /reset POST ----

def test_reset_post_success(client, auth_client, sent):
    old_hash = get_user().password_hash
    url, tok = _request_reset(client, "test@example.com", sent)
    r = client.post(f"/reset/{tok}",
                     data={"password": "newpassword1", "confirm": "newpassword1"},
                     follow_redirects=False)
    assert r.status_code in (302, 303)  # AC7
    assert "/login" in r.headers.get("location", "")
    u = get_user()
    assert u.password_hash != old_hash
    assert main.verify_password("newpassword1", u.password_hash)
    assert not main.verify_password("password1", u.password_hash)  # AC10
    row = db_query_first(select(main.PasswordResetToken))
    assert row.used_at is not None
    # session cleared: the auth_client cookie no longer authenticates
    me = client.get("/me.json", headers={"Accept": "application/json"})
    assert me.status_code == 401  # AC7 session-fixation defense


def test_reset_post_single_use(client, auth_client, sent):
    url, tok = _request_reset(client, "test@example.com", sent)
    first = client.post(f"/reset/{tok}",
                        data={"password": "newpassword1", "confirm": "newpassword1"},
                        follow_redirects=False)
    assert first.status_code in (302, 303)
    again = client.post(f"/reset/{tok}",
                        data={"password": "another12345", "confirm": "another12345"},
                        follow_redirects=False)
    assert again.status_code == 400  # AC9/E11
    assert main.verify_password("newpassword1", get_user().password_hash)


@pytest.mark.parametrize("pw,confirm", [
    ("short", "short"),                 # E7 <8
    ("", ""),                           # E7 empty
    ("x" * 73, "x" * 73),               # E8 >72 bytes
    ("goodpass123", "mismatch123"),     # E9 confirm mismatch
])
def test_reset_post_invalid_password(client, auth_client, sent, pw, confirm):
    old_hash = get_user().password_hash
    url, tok = _request_reset(client, "test@example.com", sent)
    r = client.post(f"/reset/{tok}", data={"password": pw, "confirm": confirm},
                     follow_redirects=False)
    assert r.status_code == 400  # AC8
    assert get_user().password_hash == old_hash  # unchanged
    # token NOT consumed by a failed attempt
    assert db_query_first(select(main.PasswordResetToken)).used_at is None


def test_full_flow_login_with_new_password(client, auth_client, sent):
    url, tok = _request_reset(client, "test@example.com", sent)
    client.post(f"/reset/{tok}",
                data={"password": "brandnewpw1", "confirm": "brandnewpw1"},
                follow_redirects=False)
    bad = client.post("/login",
                       data={"email": "test@example.com", "password": "password1"},
                       follow_redirects=False)
    assert bad.status_code in (400, 401)  # old password rejected
    good = client.post("/login",
                        data={"email": "test@example.com", "password": "brandnewpw1"},
                        follow_redirects=False)
    assert good.status_code in (302, 303)  # AC10 new password works


# ---- test-only retrieval hook (browser tests rely on it) ----

def test_test_hook_returns_last_link(client, auth_client, sent):
    client.post("/forgot", data={"email": "test@example.com"},
                follow_redirects=False)
    r = client.get("/__test__/last_reset_link")
    assert r.status_code == 200
    assert "/reset/" in r.text


def test_login_reset_banner(client):
    r = client.get("/login?reset=1")
    assert r.status_code == 200
    assert r.text != client.get("/login").text  # a banner appears
