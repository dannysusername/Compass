"""Browser-test fixtures. Distinct from `tests/conftest.py` because:

  - These tests need a real HTTP server (Playwright drives a real
    Chromium that has to fetch pages over the network). The TestClient
    pattern from the API tests is in-process and doesn't open a port.
  - We keep them in a separate directory so `pytest tests/` runs the
    fast in-process suite (~16s) while `pytest tests_browser/` runs the
    slower browser suite (~5-10s for a few tests, but each is heavier).

Env-var setup happens BEFORE the uvicorn subprocess launches so the
server runs against a fresh test DB, not the user's real `compass.db`.
"""
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import tempfile
import time

import pytest


# ---- Test DB / env ----
_TEST_DIR = pathlib.Path(tempfile.mkdtemp(prefix="compass-browser-"))
_TEST_DB = _TEST_DIR / "test.db"
(_TEST_DIR / "uploads").mkdir(exist_ok=True)


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_TEST_DIR, ignore_errors=True)


def _free_port():
    """OS-assigned free port. Beats hard-coding 8001 / 8002 / etc.
    because parallel test runs would collide."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="session")
def server_url():
    """Boot uvicorn against a throwaway SQLite DB, return its base URL.
    Killed at session end. Lifespan hooks run normally so the schema
    is built fresh."""
    port = _free_port()
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{_TEST_DB}"
    env["COMPASS_SECRET_KEY"] = "browser-test-secret-not-for-prod-32chrs"
    env["COMPASS_ENV"] = "test"
    env["UPLOAD_DIR"] = str(_TEST_DIR / "uploads")
    env["STORAGE_BACKEND"] = "local"
    # Fixed admin allowlist so the mobile sweep can exercise /admin.
    # Regular per-test users (browser_user_N@) are never in it, so
    # existing tests see no behaviour change.
    env["ADMIN_EMAILS"] = "admin_browser@example.com"
    # Pin the external base URL so emailed reset links point at this
    # test server (exercises the prod-style APP_BASE_URL path). No
    # SENDGRID_* set → send_email uses the no-network log backend.
    env["APP_BASE_URL"] = f"http://127.0.0.1:{port}"

    proc = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn", "main:app",
            "--host", "127.0.0.1",
            "--port", str(port),
            "--no-access-log",
        ],
        env=env,
        cwd=str(pathlib.Path(__file__).resolve().parent.parent),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    base = f"http://127.0.0.1:{port}"
    # Wait for the port to accept connections (~10 sec budget).
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                break
        except OSError:
            time.sleep(0.1)
    else:
        proc.terminate()
        raise RuntimeError(f"uvicorn at {base} didn't accept connections")

    yield base

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


# ---- User isolation ----
# Each test gets its own user (e.g. test_42@example.com). Cheaper than
# wiping tables between tests because we'd need to coordinate with the
# server subprocess; per-test users sidestep the whole problem.
_USER_COUNTER = 0


def _next_user_email() -> str:
    global _USER_COUNTER
    _USER_COUNTER += 1
    return f"browser_user_{_USER_COUNTER}@example.com"


@pytest.fixture(scope="session")
def test_db_path():
    """Expose the throwaway browser-test SQLite path so individual tests
    can seed rows the UI has no creation flow for (e.g. CalendarEvent —
    those normally come from syllabus parses)."""
    return str(_TEST_DB)


@pytest.fixture
def signed_in_page(page, server_url):
    """Playwright `page` already signed up + logged in to a fresh user.
    Lands on `/`. Tests that don't need auth can use the bare `page`
    fixture from pytest-playwright instead."""
    email = _next_user_email()
    page.goto(server_url + "/signup")
    page.fill("input[name='email']", email)
    page.fill("input[name='password']", "password1")
    page.click("button[type='submit']")
    # Signup redirects to /. Wait for that page so subsequent test code
    # can interact without racing the redirect.
    page.wait_for_url(server_url + "/")
    return page
