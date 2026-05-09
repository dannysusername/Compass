"""Shared pytest fixtures for the Compass test suite.

Critical: env vars MUST be set BEFORE importing main, because main reads
DATABASE_URL at module load to build the engine. The first test file to
import main (via the `client` fixture) freezes that engine, so we point
it at a per-session temp SQLite DB instead of the real compass.db.

Each test gets a fresh table state via the autouse `reset_db` fixture
— drops and recreates all tables, so test isolation is by construction.
"""
import os
import pathlib
import shutil
import tempfile

# ---- Pre-import env setup ----
# Use a fresh temp dir for the test session. compass.db, uploads/, and
# compass.log all stay out of this; nothing pollutes the real project.
_TEST_DIR = pathlib.Path(tempfile.mkdtemp(prefix="compass-test-"))
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DIR / 'test.db'}"
os.environ["COMPASS_SECRET_KEY"] = "test-secret-key-not-for-prod-32chars"
os.environ["COMPASS_ENV"] = "test"
os.environ["STORAGE_BACKEND"] = "local"
os.environ["UPLOAD_DIR"] = str(_TEST_DIR / "uploads")
(_TEST_DIR / "uploads").mkdir(exist_ok=True)

import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, Session, select

import main


# ---- Cleanup ----

def pytest_sessionfinish(session, exitstatus):
    """Wipe the temp dir after the suite runs. Logs at the start of the
    output if cleanup fails so we don't accumulate stale temp dirs."""
    try:
        shutil.rmtree(_TEST_DIR, ignore_errors=True)
    except Exception as e:
        print(f"WARN: couldn't clean up {_TEST_DIR}: {e}")


# ---- DB isolation ----

@pytest.fixture(autouse=True)
def reset_db():
    """Drop and recreate every table before each test. SQLModel.metadata
    knows the full schema (Task, Class, Tag, User, etc.), so this gives
    each test a known-empty starting state without any cross-test
    leakage. Faster than recreating the engine."""
    SQLModel.metadata.drop_all(main.engine)
    SQLModel.metadata.create_all(main.engine)
    yield


# ---- HTTP clients ----

@pytest.fixture
def client():
    """Plain TestClient. Tests that need authenticated state use the
    `auth_client` fixture instead. Wrapped as a context manager so
    FastAPI's lifespan hook runs (sets up tables, etc.)."""
    with TestClient(main.app) as c:
        yield c


@pytest.fixture
def auth_client(client):
    """TestClient with an authenticated session (test@example.com).
    Cookies persist across requests on a TestClient instance, so once
    /signup completes, every later call rides the same session."""
    r = client.post(
        "/signup",
        data={"email": "test@example.com", "password": "password1"},
        follow_redirects=False,
    )
    # /signup redirects (303) on success; 200 means it returned the
    # signup page with an error which is a test setup failure.
    assert r.status_code in (302, 303), (
        f"signup failed: {r.status_code} — {r.text[:200]}"
    )
    return client


@pytest.fixture
def second_user_client():
    """A SEPARATE TestClient logged in as a second user. Used by
    cross-user-access tests to verify ownership scoping."""
    with TestClient(main.app) as c:
        r = c.post(
            "/signup",
            data={"email": "other@example.com", "password": "password1"},
            follow_redirects=False,
        )
        assert r.status_code in (302, 303)
        yield c


# ---- DB inspection helpers (for assertions) ----

def db_get(model, pk):
    """Fetch a row by primary key from the test DB. Used in assertions
    to verify route-level behavior actually reached the database."""
    with Session(main.engine) as session:
        return session.get(model, pk)


def db_query_first(stmt):
    """Run a SELECT and return the first row, or None."""
    with Session(main.engine) as session:
        return session.exec(stmt).first()


def db_query_all(stmt):
    with Session(main.engine) as session:
        return session.exec(stmt).all()


def get_user(email="test@example.com"):
    """Fetch the auth_client's user row."""
    return db_query_first(select(main.User).where(main.User.email == email))


# ---- Domain helpers ----

def create_class(client, code="TEST101", name="Test Class"):
    """POST /classes with the auth'd client. Returns the new Class id
    by querying the DB (the route redirects to /, no JSON body).

    Sorts by id desc so cross-user tests where two clients each create a
    class with the same default code still resolve to the *just-created*
    one rather than the first-ever match."""
    r = client.post(
        "/classes",
        data={"code": code, "name": name},
        follow_redirects=False,
    )
    assert r.status_code in (302, 303), f"create_class: {r.status_code}"
    cls = db_query_first(
        select(main.Class)
        .where(main.Class.code == code.upper().strip())
        .order_by(main.Class.id.desc())
    )
    assert cls is not None
    return cls


def create_task(client, class_id=None, **fields):
    """POST /tasks (Personal) or /classes/{id}/tasks (class-scoped).
    Returns the parsed JSON response which has at minimum {id, title}.
    Pass fields as kwargs: title, due_at, starts_at, notes, rrule, etc."""
    fields.setdefault("title", "Test task")
    url = f"/classes/{class_id}/tasks" if class_id is not None else "/tasks"
    r = client.post(
        url,
        data=fields,
        headers={"Accept": "application/json"},
    )
    assert r.status_code == 200, f"create_task: {r.status_code} — {r.text[:200]}"
    return r.json()


# Re-export so test files can `from .conftest import ...` cleanly. pytest
# discovers fixtures by name, but plain helpers need explicit imports.
__all__ = [
    "db_get", "db_query_first", "db_query_all",
    "get_user", "create_class", "create_task",
]
