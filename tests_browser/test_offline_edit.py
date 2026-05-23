"""Web (PWA) offline editing — the write-queue in static/sync.js.

When a task write fails because we're offline, the page keeps the optimistic
change and queues it (window.CompassSync); on reconnect it replays to the
server via POST /sync. These drive a real Chromium with the network toggled
off via context.set_offline.

(The service worker — which serves cached HTML offline — is disabled in the
test env, so we exercise the queue/replay loop directly rather than a true
offline reload. applyToDom is unit-tested on its own.)
"""
import datetime
import re

from playwright.sync_api import expect


def _today_iso(h, m=0):
    d = datetime.datetime.now().replace(hour=h, minute=m, second=0, microsecond=0)
    return d.strftime("%Y-%m-%dT%H:%M")


def _make_task(page, server_url, title):
    page.request.post(server_url + "/tasks", form={"title": title, "due_at": _today_iso(10)})
    page.goto(server_url + "/")
    return page.locator(f".todo-row[data-title='{title}']").first


def _queue_len(page):
    return page.evaluate("async () => (await window.CompassSync.getQueue()).length")


def test_offline_toggle_queues_then_syncs_on_reconnect(signed_in_page, server_url):
    page = signed_in_page
    row = _make_task(page, server_url, "Off toggle")
    expect(row).to_be_visible()

    page.context.set_offline(True)
    row.locator(".todo-toggle").click()
    expect(row).to_have_class(re.compile(r"\bdone\b"))     # optimistic, kept (not rolled back)
    assert _queue_len(page) == 1                            # queued for later

    # Reconnect → sync.js 'online' handler replays the queue to the server.
    page.context.set_offline(False)
    page.wait_for_function("async () => (await window.CompassSync.getQueue()).length === 0")

    # Server persisted it: a task completed today still renders (crossed-out).
    page.goto(server_url + "/")
    expect(page.locator(".todo-row[data-title='Off toggle']").first).to_have_class(
        re.compile(r"\bdone\b")
    )


def test_offline_delete_queues_then_syncs_on_reconnect(signed_in_page, server_url):
    page = signed_in_page
    row = _make_task(page, server_url, "Off delete")
    expect(row).to_be_visible()

    page.on("dialog", lambda d: d.accept())  # confirm("Delete this task?")
    page.context.set_offline(True)
    row.locator(".todo-row-main").click()    # open drawer
    row.locator(".todo-del").click()
    expect(page.locator(".todo-row[data-title='Off delete']")).to_have_count(0)  # removed
    assert _queue_len(page) == 1

    page.context.set_offline(False)
    page.wait_for_function("async () => (await window.CompassSync.getQueue()).length === 0")

    page.goto(server_url + "/")
    expect(page.locator(".todo-row[data-title='Off delete']")).to_have_count(0)  # gone on server


def test_apply_to_dom_reinstates_a_queued_toggle(signed_in_page, server_url):
    """applyToDom re-marks a queued toggle on a freshly-rendered page (this is
    what makes an offline edit survive a reload behind the service worker)."""
    page = signed_in_page
    row = _make_task(page, server_url, "Apply me")
    page.context.set_offline(True)
    row.locator(".todo-toggle").click()
    expect(row).to_have_class(re.compile(r"\bdone\b"))

    # Simulate a fresh server render where the row is NOT done, then replay
    # the queue onto the DOM.
    page.evaluate("""() => {
        document.querySelectorAll('.todo-row[data-title="Apply me"]').forEach((r) => r.classList.remove('done'));
    }""")
    page.evaluate("async () => { await window.CompassSync.applyToDom(); }")
    expect(page.locator(".todo-row[data-title='Apply me']").first).to_have_class(
        re.compile(r"\bdone\b")
    )
