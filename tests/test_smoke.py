"""Foundation tests — if these don't pass, the rest of the suite is
running against a broken setup. Keep them tight and dependency-free."""
from .conftest import get_user, create_class, create_task


def test_signup_creates_user(client):
    r = client.post(
        "/signup",
        data={"email": "new@example.com", "password": "password1"},
        follow_redirects=False,
    )
    assert r.status_code in (302, 303)
    u = get_user("new@example.com")
    assert u is not None
    assert u.email == "new@example.com"


def test_signup_short_password_rejected(client):
    r = client.post(
        "/signup",
        data={"email": "x@y.com", "password": "short"},
        follow_redirects=False,
    )
    assert r.status_code == 400


def test_me_json_returns_user_when_logged_in(auth_client):
    r = auth_client.get("/me.json", headers={"Accept": "application/json"})
    assert r.status_code == 200
    assert r.json()["email"] == "test@example.com"


def test_me_json_returns_401_when_logged_out(client):
    r = client.get("/me.json", headers={"Accept": "application/json"})
    assert r.status_code == 401


def test_classes_json_empty_for_new_user(auth_client):
    r = auth_client.get("/classes.json", headers={"Accept": "application/json"})
    assert r.status_code == 200
    assert r.json() == []


def test_create_personal_task(auth_client):
    t = create_task(auth_client, title="Buy milk")
    assert t["id"] > 0
    assert t["title"] == "Buy milk"


def test_create_class_task(auth_client):
    cls = create_class(auth_client, code="MATH101", name="Calculus")
    t = create_task(auth_client, class_id=cls.id, title="Read chapter 3")
    assert t["title"] == "Read chapter 3"


def test_today_json_returns_buckets(auth_client):
    create_task(auth_client, title="Today's task", due_at="2030-01-01T12:00")
    r = auth_client.get("/today.json", headers={"Accept": "application/json"})
    assert r.status_code == 200
    body = r.json()
    assert "buckets" in body
    assert "today" in body
