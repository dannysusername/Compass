"""Browser-driven forgot-password flow in real Chromium.

Exercises the full happy path (request → emailed link via the
COMPASS_ENV-gated test hook → set new password → forced re-login) plus
the generic invalid-link page. The server subprocess runs
COMPASS_ENV=test so send_email uses the no-network log backend; the link
is retrieved via /__test__/last_reset_link.
"""
import re

from playwright.sync_api import expect

PW_OLD = "password1"
PW_NEW = "brandnewpw9"
_n = {"i": 0}


def _email():
    _n["i"] += 1
    return f"pwreset_{_n['i']}@example.com"


def _signup(page, server_url, email):
    page.goto(server_url + "/signup")
    page.fill("input[name='email']", email)
    page.fill("input[name='password']", PW_OLD)
    page.click("button[type='submit']")
    page.wait_for_url(server_url + "/")


def test_full_reset_flow(page, server_url):
    email = _email()
    _signup(page, server_url, email)
    page.context.clear_cookies()  # simulate a logged-out browser

    # Reach /forgot via the link on the login page.
    page.goto(server_url + "/login")
    page.click("a[href='/forgot']")
    page.wait_for_url(server_url + "/forgot")
    page.fill("input[name='email']", email)
    page.click("button[type='submit']")
    expect(page.locator(".setup-card")).to_contain_text("on its way")

    link = page.request.get(
        server_url + "/__test__/last_reset_link").json()["link"]
    assert "/reset/" in link, link

    page.goto(link)
    expect(page.locator("input[name='password']")).to_be_visible()
    page.fill("input[name='password']", PW_NEW)
    page.fill("input[name='confirm']", PW_NEW)
    page.click("button[type='submit']")
    page.wait_for_url(re.compile(r"/login"))
    expect(page.locator(".setup-card")).to_contain_text(
        "password has been updated")

    # New password logs in.
    page.fill("input[name='email']", email)
    page.fill("input[name='password']", PW_NEW)
    page.click("button[type='submit']")
    page.wait_for_url(server_url + "/")

    # Old password no longer works.
    page.context.clear_cookies()
    page.goto(server_url + "/login")
    page.fill("input[name='email']", email)
    page.fill("input[name='password']", PW_OLD)
    page.click("button[type='submit']")
    expect(page.locator(".error-message")).to_be_visible()


def test_used_link_is_dead(page, server_url):
    email = _email()
    _signup(page, server_url, email)
    page.context.clear_cookies()
    page.request.post(server_url + "/forgot", form={"email": email})
    link = page.request.get(
        server_url + "/__test__/last_reset_link").json()["link"]
    page.goto(link)
    page.fill("input[name='password']", PW_NEW)
    page.fill("input[name='confirm']", PW_NEW)
    page.click("button[type='submit']")
    page.wait_for_url(re.compile(r"/login"))
    # Re-visiting the consumed link shows the generic invalid page.
    page.goto(link)
    expect(page.locator(".setup-card")).to_contain_text("invalid")


def test_invalid_reset_link_page(page, server_url):
    page.goto(server_url + "/reset/totally-bogus-token-not-real-xyz")
    expect(page.locator(".setup-card")).to_contain_text("invalid")
