"""Mobile layout sweep — enforces tests_browser/MOBILE-SPEC.md across
every page at 320 / 390 / 768 px.

Spec-driven: the assertions encode "looks good on mobile" so a broken
layout fails CI instead of relying on someone eyeballing it. Written to
fail on the pre-fix /admin table (S1 overflow) — that's the red step.

Screenshots of each page at 390px land in ./mobile-screenshots/
(gitignored) for the visual pass.
"""
import pathlib
from datetime import datetime

from playwright.sync_api import expect

WIDTHS = [320, 390, 768]
HEIGHT = 844
ADMIN_EMAIL = "admin_browser@example.com"  # matches conftest ADMIN_EMAILS
PW = "password1"

_SHOTS = pathlib.Path(__file__).resolve().parent.parent / "mobile-screenshots"
_SHOTS.mkdir(exist_ok=True)

_n = {"i": 0}


def _uniq_email():
    _n["i"] += 1
    return f"sweep_user_{_n['i']}@example.com"


def _signup(page, server_url, email):
    page.goto(server_url + "/signup")
    page.fill("input[name='email']", email)
    page.fill("input[name='password']", PW)
    page.click("button[type='submit']")
    page.wait_for_url(server_url + "/")


def _today_noon():
    return datetime.now().replace(
        hour=12, minute=0, second=0, microsecond=0
    ).strftime("%Y-%m-%dT%H:%M")


def _seed_class_and_task(page, server_url):
    """Create one class + one task due today so the home / today / week /
    class pages render real content, not just empty states. page.request
    rides the browser context's session cookie."""
    r = page.request.post(
        server_url + "/classes",
        form={"name": "Calculus II", "code": "MATH 250"},
        headers={"Accept": "application/json"},
    )
    assert r.ok, f"seed class failed: {r.status}"
    cid = r.json()["id"]
    page.request.post(
        f"{server_url}/classes/{cid}/tasks",
        form={"title": "Read chapter 4", "due_at": _today_noon()},
    )
    return cid


# ---- Spec assertions (MOBILE-SPEC.md) ----

def _s1_no_overflow(page, label):
    sw = page.evaluate("() => document.documentElement.scrollWidth")
    iw = page.evaluate("() => window.innerWidth")
    assert sw <= iw + 1, f"S1 {label}: horizontal overflow (scrollWidth={sw} > innerWidth={iw})"


def _s2_container_in_bounds(page, label):
    box = page.evaluate(
        "() => { const m = document.querySelector('main');"
        " if (!m) return null; const r = m.getBoundingClientRect();"
        " return {left: r.left, right: r.right}; }"
    )
    if box is None:
        return
    iw = page.evaluate("() => window.innerWidth")
    assert box["left"] >= -1, f"S2 {label}: content off-screen left ({box['left']})"
    assert box["right"] <= iw + 1, f"S2 {label}: content past right edge ({box['right']} > {iw})"


def _check(page, label):
    _s1_no_overflow(page, label)
    _s2_container_in_bounds(page, label)


# ---- Tests ----

def test_logged_out_pages_fit(page, server_url):
    for path in ("/login", "/signup", "/forgot"):
        for w in WIDTHS:
            page.set_viewport_size({"width": w, "height": HEIGHT})
            page.goto(server_url + path)
            page.wait_for_load_state("networkidle")
            _check(page, f"{path}@{w}")
        page.set_viewport_size({"width": 390, "height": HEIGHT})
        page.goto(server_url + path)
        page.screenshot(path=str(_SHOTS / f"sweep-{path.strip('/')}.png"), full_page=True)


def test_regular_pages_fit(page, server_url):
    _signup(page, server_url, _uniq_email())
    cid = _seed_class_and_task(page, server_url)
    pages = {
        "home": "/",
        "today": "/today",
        "week": "/week",
        "settings": "/settings",
        "class": f"/classes/{cid}",
    }
    for name, path in pages.items():
        for w in WIDTHS:
            page.set_viewport_size({"width": w, "height": HEIGHT})
            page.goto(server_url + path)
            page.wait_for_load_state("networkidle")
            _check(page, f"{name}@{w}")
            if w == 390:
                # S3 (subset): the always-present nav hamburger is a
                # reachable ~44px target on mobile widths.
                tb = page.locator(".nav-toggle").bounding_box()
                assert tb and tb["height"] >= 40, f"S3 {name}: nav toggle too small"
        page.set_viewport_size({"width": 390, "height": HEIGHT})
        page.goto(server_url + path)
        page.wait_for_load_state("networkidle")
        page.screenshot(path=str(_SHOTS / f"sweep-{name}.png"), full_page=True)


def test_admin_page_fits(page, server_url):
    _signup(page, server_url, ADMIN_EMAIL)
    for w in WIDTHS:
        page.set_viewport_size({"width": w, "height": HEIGHT})
        page.goto(server_url + "/admin")
        page.wait_for_load_state("networkidle")
        expect(page.locator(".admin-table")).to_be_visible()
        _check(page, f"admin@{w}")
    page.set_viewport_size({"width": 390, "height": HEIGHT})
    page.goto(server_url + "/admin")
    page.screenshot(path=str(_SHOTS / "sweep-admin.png"), full_page=True)


def test_add_task_modal_fits(page, server_url):
    _signup(page, server_url, _uniq_email())
    page.set_viewport_size({"width": 390, "height": HEIGHT})
    page.goto(server_url + "/")
    page.wait_for_load_state("networkidle")
    page.click("button[data-open-modal='add-task-modal']")
    dialog = page.locator("#add-task-modal .modal-dialog")
    expect(dialog).to_be_visible()
    box = dialog.bounding_box()
    assert box["width"] <= 390 + 1, f"S4: modal wider than viewport ({box['width']})"
    close = page.locator("#add-task-modal .modal-close").bounding_box()
    assert 0 <= close["x"] and close["x"] + close["width"] <= 390 + 1, "S4: modal close button off-screen"
    page.screenshot(path=str(_SHOTS / "sweep-modal.png"))


def test_reset_page_fits(page, server_url):
    """The /reset/{token} page (a real token) must fit every width —
    it's a logged-out, security-critical surface."""
    email = _uniq_email()
    _signup(page, server_url, email)
    r = page.request.post(server_url + "/forgot", form={"email": email})
    assert r.ok, r.status
    link = page.request.get(
        server_url + "/__test__/last_reset_link").json()["link"]
    assert "/reset/" in link, link
    for w in WIDTHS:
        page.set_viewport_size({"width": w, "height": HEIGHT})
        page.goto(link)
        page.wait_for_load_state("networkidle")
        _check(page, f"reset@{w}")
    page.set_viewport_size({"width": 390, "height": HEIGHT})
    page.goto(link)
    page.screenshot(path=str(_SHOTS / "sweep-reset.png"), full_page=True)
