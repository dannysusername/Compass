"""Extension-parity surface: JSON branches of routes the website
historically only redirected from. The browser extension calls these
with Accept: application/json — these tests pin both the success shapes
and the validation-error shapes.

Covers /signup, POST /classes, /classes/{id}/delete, /events/{id}/edit,
/tags, /tags/{id}/edit, /tags/{id}/delete, POST /settings,
/settings/calendar/regenerate, POST /syllabus (no-key path), and the
extended /me.json shape.
"""
from sqlmodel import Session, select

import main
from .conftest import (
    create_class, create_task, db_get, db_query_first, db_query_all
)


JSON = {"Accept": "application/json"}


# ---- /me.json extended fields ---------------------------------------------

def test_me_json_includes_settings_fields(auth_client):
    r = auth_client.get("/me.json")
    assert r.status_code == 200
    body = r.json()
    for key in ("id", "email", "timezone",
                "xai_api_key_set", "xai_api_key_masked",
                "calendar_token", "calendar_urls"):
        assert key in body, f"/me.json missing {key}"
    assert body["xai_api_key_set"] is False
    assert body["xai_api_key_masked"] is None
    assert body["calendar_token"]  # truthy non-empty string
    urls = body["calendar_urls"]
    assert urls.get("webcal_url", "").startswith("webcal://")
    assert urls.get("https_url", "").startswith("http")


# ---- /signup JSON branch --------------------------------------------------

def test_signup_json_success(client):
    r = client.post(
        "/signup",
        data={"email": "newbie@example.com", "password": "longenough"},
        headers=JSON,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["email"] == "newbie@example.com"
    assert "id" in body
    # Cookie was set — /me.json should now succeed.
    assert client.get("/me.json", headers=JSON).status_code == 200


def test_signup_json_short_password(client):
    r = client.post(
        "/signup",
        data={"email": "x@y.com", "password": "short"},
        headers=JSON,
    )
    assert r.status_code == 400
    assert "8 characters" in r.json()["error"]


def test_signup_json_bad_email(client):
    r = client.post(
        "/signup",
        data={"email": "not-an-email", "password": "longenough"},
        headers=JSON,
    )
    assert r.status_code == 400
    assert "valid email" in r.json()["error"]


def test_signup_json_duplicate_email(client):
    client.post(
        "/signup",
        data={"email": "dup@example.com", "password": "longenough"},
        headers=JSON,
    )
    r = client.post(
        "/signup",
        data={"email": "dup@example.com", "password": "longenough"},
        headers=JSON,
    )
    assert r.status_code == 400
    assert "already registered" in r.json()["error"]


# ---- POST /classes JSON branch --------------------------------------------

def test_create_class_json_returns_new_id(auth_client):
    r = auth_client.post(
        "/classes",
        data={"code": "math250", "name": "Calc II"},
        headers=JSON,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == "MATH250"  # uppercased
    assert body["name"] == "Calc II"
    assert "id" in body
    cls = db_get(main.Class, body["id"])
    assert cls is not None


def test_create_class_html_branch_still_redirects(auth_client):
    """The website's POST (no Accept header) keeps the 303 redirect."""
    r = auth_client.post(
        "/classes",
        data={"code": "OLD101", "name": "Old"},
        follow_redirects=False,
    )
    assert r.status_code in (302, 303)


# ---- POST /classes/{id}/delete JSON branch --------------------------------

def test_delete_class_json(auth_client):
    cls = create_class(auth_client, code="DROP")
    r = auth_client.post(f"/classes/{cls.id}/delete", headers=JSON)
    assert r.status_code == 200
    assert r.json() == {"deleted": cls.id}
    assert db_get(main.Class, cls.id) is None


# ---- POST /events/{id}/edit JSON branch -----------------------------------

def _create_event(auth_client, class_id, **fields):
    """Insert an event directly via the DB so we don't depend on the
    syllabus pipeline. Returns the event id."""
    from datetime import datetime
    fields.setdefault("title", "Quiz 1")
    fields.setdefault("kind", "quiz")
    fields.setdefault("actionable", True)
    fields.setdefault("starts_at", datetime(2030, 6, 15, 14, 0))
    with Session(main.engine) as s:
        cls = s.get(main.Class, class_id)
        ev = main.CalendarEvent(class_id=class_id, class_code=cls.code, **fields)
        s.add(ev)
        s.commit()
        s.refresh(ev)
        return ev.id


def test_edit_event_json(auth_client):
    cls = create_class(auth_client)
    eid = _create_event(auth_client, cls.id, title="Old", kind="quiz")
    r = auth_client.post(
        f"/events/{eid}/edit",
        data={
            "title": "New title",
            "kind": "exam",
            "starts_at": "2030-07-01T10:00:00",
            "ends_at": "2030-07-01T12:00:00",
        },
        headers=JSON,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["title"] == "New title"
    assert body["kind"] == "exam"
    assert body["starts_at"].startswith("2030-07-01T10:00")
    assert body["ends_at"].startswith("2030-07-01T12:00")
    assert body["class_id"] == cls.id


def test_edit_event_blocked_for_other_user(auth_client, second_user_client):
    cls = create_class(auth_client)
    eid = _create_event(auth_client, cls.id)
    r = second_user_client.post(
        f"/events/{eid}/edit",
        data={"title": "Hacked", "kind": "quiz"},
        headers=JSON,
        follow_redirects=False,
    )
    assert r.status_code in (404, 401, 403)


# ---- POST /events/{id}/clone JSON branch (already existed; pin it) --------

def test_clone_event_json(auth_client):
    cls = create_class(auth_client)
    eid = _create_event(auth_client, cls.id, title="Quiz Z")
    r = auth_client.post(f"/events/{eid}/clone", headers=JSON)
    assert r.status_code == 200
    body = r.json()
    assert "id" in body and body["id"] != eid
    # Both rows now exist.
    rows = db_query_all(
        select(main.CalendarEvent).where(main.CalendarEvent.class_id == cls.id)
    )
    assert len(rows) == 2


# ---- /tags create + edit + delete ----------------------------------------

def test_create_tag_json(auth_client):
    r = auth_client.post(
        "/tags",
        data={"name": "Reading", "color": "#A04528"},
        headers=JSON,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "Reading"
    assert body["color"] == "#A04528"
    assert "id" in body


def test_edit_tag_json(auth_client):
    created = auth_client.post(
        "/tags",
        data={"name": "Old", "color": "#111111"},
        headers=JSON,
    ).json()
    r = auth_client.post(
        f"/tags/{created['id']}/edit",
        data={"name": "New", "color": "#222222"},
        headers=JSON,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "New"
    assert body["color"] == "#222222"


def test_delete_tag_json(auth_client):
    created = auth_client.post(
        "/tags",
        data={"name": "Dropme", "color": "#A04528"},
        headers=JSON,
    ).json()
    r = auth_client.post(f"/tags/{created['id']}/delete", headers=JSON)
    assert r.status_code == 200
    assert r.json() == {"deleted": created["id"]}
    assert db_get(main.Tag, created["id"]) is None


def test_delete_system_tag_blocked(auth_client):
    """System tags can be renamed/recolored but not deleted — the rule
    is enforced server-side."""
    sys_tag = db_query_first(
        select(main.Tag).where(main.Tag.is_system == True)
    )
    assert sys_tag is not None, "expected seed system tags after signup"
    r = auth_client.post(f"/tags/{sys_tag.id}/delete", headers=JSON)
    assert r.status_code == 400


def test_edit_tag_blocked_for_other_user(auth_client, second_user_client):
    created = auth_client.post(
        "/tags",
        data={"name": "Mine", "color": "#A04528"},
        headers=JSON,
    ).json()
    r = second_user_client.post(
        f"/tags/{created['id']}/edit",
        data={"name": "Stolen", "color": "#000000"},
        headers=JSON,
        follow_redirects=False,
    )
    assert r.status_code in (404, 401, 403)


# ---- POST /settings (xai key) JSON branch ---------------------------------

def test_settings_save_xai_json(auth_client):
    r = auth_client.post(
        "/settings",
        data={"xai_api_key": "xai-testkey-1234567890"},
        headers=JSON,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["saved"] is True
    assert body["xai_api_key_set"] is True
    assert body["xai_api_key_masked"]
    # Confirm /me.json now reports the key as set.
    me = auth_client.get("/me.json").json()
    assert me["xai_api_key_set"] is True


def test_settings_save_xai_invalid_prefix(auth_client):
    r = auth_client.post(
        "/settings",
        data={"xai_api_key": "sk-not-an-xai-key"},
        headers=JSON,
    )
    assert r.status_code == 400
    assert "xai-" in r.json()["error"]


def test_settings_clear_xai_json(auth_client):
    auth_client.post(
        "/settings",
        data={"xai_api_key": "xai-testkey"},
        headers=JSON,
    )
    r = auth_client.post(
        "/settings",
        data={"xai_api_key": ""},
        headers=JSON,
    )
    assert r.status_code == 200
    assert r.json()["xai_api_key_set"] is False


# ---- POST /settings/calendar/regenerate JSON branch -----------------------

def test_calendar_regenerate_json(auth_client):
    before = auth_client.get("/me.json").json()["calendar_token"]
    r = auth_client.post("/settings/calendar/regenerate", headers=JSON)
    assert r.status_code == 200
    body = r.json()
    assert "calendar_token" in body
    assert body["calendar_token"] != before
    assert body["calendar_urls"]["webcal_url"].startswith("webcal://")


# ---- POST /syllabus JSON: no-xAI-key path --------------------------------

def test_syllabus_upload_without_key_json(auth_client):
    """Without an xAI key set, the JSON branch should reply with an
    error payload rather than a 303 redirect to /settings."""
    r = auth_client.post(
        "/syllabus",
        files={"file": ("syl.pdf", b"%PDF-1.4\n%%EOF\n", "application/pdf")},
        headers=JSON,
        follow_redirects=False,
    )
    assert r.status_code == 400
    assert r.json() == {"error": "need_key"}


def test_syllabus_upload_html_branch_redirects(auth_client):
    """The website's no-Accept POST keeps the 303 redirect to /settings."""
    r = auth_client.post(
        "/syllabus",
        files={"file": ("syl.pdf", b"%PDF-1.4\n%%EOF\n", "application/pdf")},
        follow_redirects=False,
    )
    assert r.status_code in (302, 303)
    assert "/settings" in r.headers.get("location", "")


# ---- Hybrid free-pool / own-key syllabus entitlement ---------------------

def _stub_parse_pipeline(monkeypatch):
    """Neutralize the heavy parts of /syllabus so these tests exercise only
    the entitlement gate + counter — not PDF extraction, disk writes, or a
    real Grok call."""
    monkeypatch.setattr(main, "validate_pdf", lambda content: None)
    monkeypatch.setattr(main, "extract_pdf_text", lambda src: "syllabus text")
    monkeypatch.setattr(main.storage, "save", lambda *a, **k: None)
    monkeypatch.setattr(main, "process_syllabus", lambda syllabus_id: None)


def _upload(client, accept_json=True):
    return client.post(
        "/syllabus",
        files={"file": ("syl.pdf", b"%PDF-1.4 fake", "application/pdf")},
        headers=(JSON if accept_json else {}),
        follow_redirects=False,
    )


def test_me_json_includes_parse_usage_fields(auth_client):
    """/me.json carries the derived entitlement so the extension can show
    'N free parses left' and gate the Upload button."""
    body = auth_client.get("/me.json").json()
    for key in ("own_key", "server_key_available",
                "free_parses_used", "free_parse_limit",
                "free_parses_remaining"):
        assert key in body, f"/me.json missing {key}"
    assert body["own_key"] is False
    assert body["server_key_available"] is False  # no XAI_API_KEY in tests
    assert body["free_parses_used"] == 0
    assert body["free_parses_remaining"] == body["free_parse_limit"]


def test_free_pool_upload_increments_counter_then_caps(auth_client, monkeypatch):
    monkeypatch.setattr(main, "XAI_API_KEY", "xai-server-test")
    monkeypatch.setattr(main, "FREE_PARSE_LIMIT", 2)
    _stub_parse_pipeline(monkeypatch)

    uid = auth_client.get("/me.json").json()["id"]

    r1 = _upload(auth_client)
    assert r1.status_code == 200, r1.text
    assert "syllabus_id" in r1.json()
    assert db_get(main.User, uid).free_parses_used == 1

    me = auth_client.get("/me.json").json()
    assert me["server_key_available"] is True
    assert me["free_parses_used"] == 1
    assert me["free_parses_remaining"] == 1

    r2 = _upload(auth_client)
    assert r2.status_code == 200
    assert db_get(main.User, uid).free_parses_used == 2

    # Third upload is over the cap → blocked, counter unchanged.
    r3 = _upload(auth_client)
    assert r3.status_code == 400
    assert r3.json() == {"error": "limit_reached", "free_parse_limit": 2}
    assert db_get(main.User, uid).free_parses_used == 2
    assert auth_client.get("/me.json").json()["free_parses_remaining"] == 0


def test_free_pool_cap_html_branch_redirects_to_limit(auth_client, monkeypatch):
    monkeypatch.setattr(main, "XAI_API_KEY", "xai-server-test")
    monkeypatch.setattr(main, "FREE_PARSE_LIMIT", 0)
    _stub_parse_pipeline(monkeypatch)
    r = _upload(auth_client, accept_json=False)
    assert r.status_code in (302, 303)
    assert "/settings?limit=1" in r.headers.get("location", "")


def test_own_key_bypasses_the_cap(auth_client, monkeypatch):
    monkeypatch.setattr(main, "XAI_API_KEY", "xai-server-test")
    monkeypatch.setattr(main, "FREE_PARSE_LIMIT", 0)  # free pool exhausted
    _stub_parse_pipeline(monkeypatch)

    # User brings their own key → uncapped, never touches the free counter.
    auth_client.post("/settings",
                     data={"xai_api_key": "xai-personal-key-123456"},
                     headers=JSON)
    uid = auth_client.get("/me.json").json()["id"]

    r = _upload(auth_client)
    assert r.status_code == 200, r.text
    assert db_get(main.User, uid).free_parses_used == 0

    me = auth_client.get("/me.json").json()
    assert me["own_key"] is True
    assert me["free_parses_remaining"] is None  # unlimited


# ---- Admin dashboard (ADMIN_EMAILS allowlist) ---------------------------

def test_admin_routes_404_for_non_admin(auth_client):
    """No ADMIN_EMAILS set → the dashboard doesn't exist for anyone.
    404 (not 403) so the route's existence isn't disclosed."""
    assert auth_client.get("/admin").status_code == 404
    r = auth_client.post("/admin/users/1/unlimited", data={"grant": "1"})
    assert r.status_code == 404


def test_me_json_is_admin_false_for_normal_user(auth_client):
    assert auth_client.get("/me.json").json()["is_admin"] is False


def test_admin_can_list_users_and_grant_unlimited(auth_client, monkeypatch):
    monkeypatch.setattr(main, "ADMIN_EMAILS", {"test@example.com"})
    monkeypatch.setattr(main, "XAI_API_KEY", "xai-server-test")
    monkeypatch.setattr(main, "FREE_PARSE_LIMIT", 0)  # cap everyone immediately
    _stub_parse_pipeline(monkeypatch)

    uid = auth_client.get("/me.json").json()["id"]

    # Admin sees the dashboard + the user listed.
    page = auth_client.get("/admin")
    assert page.status_code == 200
    assert "test@example.com" in page.text

    # Before the grant the capped user is blocked.
    assert _upload(auth_client).json() == {"error": "limit_reached",
                                           "free_parse_limit": 0}

    # Grant unlimited via the dashboard.
    g = auth_client.post(f"/admin/users/{uid}/unlimited",
                         data={"grant": "1"}, headers=JSON)
    assert g.status_code == 200 and g.json()["unlimited"] is True
    assert db_get(main.User, uid).unlimited_parses is True

    me = auth_client.get("/me.json").json()
    assert me["unlimited_grant"] is True
    assert me["free_parses_remaining"] is None  # uncapped
    assert me["is_admin"] is True

    # Now the same upload sails through, and the counter is NOT touched.
    assert _upload(auth_client).status_code == 200
    assert db_get(main.User, uid).free_parses_used == 0

    # Revoke → capped again.
    auth_client.post(f"/admin/users/{uid}/unlimited",
                     data={"grant": "0"}, headers=JSON)
    assert db_get(main.User, uid).unlimited_parses is False
    assert _upload(auth_client).json()["error"] == "limit_reached"


def test_admin_grant_needs_the_server_pool(auth_client, monkeypatch):
    """An admin grant without XAI_API_KEY configured can't conjure a key —
    the user still hits need_key."""
    monkeypatch.setattr(main, "ADMIN_EMAILS", {"test@example.com"})
    monkeypatch.setattr(main, "XAI_API_KEY", "")  # no shared pool
    _stub_parse_pipeline(monkeypatch)
    uid = auth_client.get("/me.json").json()["id"]
    auth_client.post(f"/admin/users/{uid}/unlimited",
                     data={"grant": "1"}, headers=JSON)
    assert _upload(auth_client).json() == {"error": "need_key"}
