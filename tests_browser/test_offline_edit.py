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
    # the queue write is async (in the failed-toggle catch) — poll for it
    page.wait_for_function("async () => (await window.CompassSync.getQueue()).length === 1")

    # Reconnect → sync.js 'online' handler replays the queue to the server.
    page.context.set_offline(False)
    page.wait_for_function("async () => (await window.CompassSync.getQueue()).length === 0")

    # Verify the server actually persisted the completion (robust check via a
    # fresh /sync pull — doesn't depend on how a completed task re-renders).
    data = page.evaluate(
        "async () => (await fetch('/sync', {credentials:'same-origin', headers:{Accept:'application/json'}})).json()"
    )
    task = next(t for t in data["tasks"] if t["title"] == "Off toggle")
    assert task["completed_at"], "offline toggle did not sync to the server"


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


def test_offline_add_queues_then_syncs_on_reconnect(signed_in_page, server_url):
    page = signed_in_page
    page.goto(server_url + "/")
    page.on("dialog", lambda d: d.accept())  # "Saved offline" alert

    page.context.set_offline(True)
    page.click("button[data-open-modal='add-task-modal']")
    page.fill("#add-task-modal input[name='title']", "Added offline")
    # Clear the auto-filled start so it can't be after Due (the add form's
    # smart-default start would otherwise trip the start<due guard).
    page.fill("#add-task-modal input[name='starts_at']", "")
    page.fill("#add-task-modal input[name='due_at']", _today_iso(11))
    page.click("#add-task-modal button[type='submit']")

    page.wait_for_function("async () => (await window.CompassSync.getQueue()).length === 1")
    q = page.evaluate("async () => await window.CompassSync.getQueue()")
    assert q[0]["op"] == "upsert" and q[0]["data"]["client_id"]
    assert q[0]["data"]["title"] == "Added offline"

    page.context.set_offline(False)
    page.wait_for_function("async () => (await window.CompassSync.getQueue()).length === 0")
    page.goto(server_url + "/")
    expect(page.locator(".todo-row[data-title='Added offline']").first).to_be_visible()


def test_offline_add_shows_optimistic_row_immediately(signed_in_page, server_url):
    """The bug a user hit: an offline-added task didn't appear in the list until
    a manual refresh. It must show the instant it's added (optimistic row), and
    stay after reconnect once the canonical server row swaps in."""
    page = signed_in_page
    page.goto(server_url + "/")
    page.on("dialog", lambda d: d.accept())  # "Saved offline" alert

    page.context.set_offline(True)
    page.click("button[data-open-modal='add-task-modal']")
    page.fill("#add-task-modal input[name='title']", "Shows offline")
    page.fill("#add-task-modal input[name='starts_at']", "")
    page.fill("#add-task-modal input[name='due_at']", _today_iso(11))
    page.click("#add-task-modal button[type='submit']")

    # Visible WHILE STILL OFFLINE — no reload, no server round-trip.
    row = page.locator(".todo-row[data-title='Shows offline']").first
    expect(row).to_be_visible()
    # and it's a real interactive row (toggle/edit/delete present)
    expect(row.locator(".todo-toggle")).to_have_count(1)
    expect(row.locator(".todo-edit")).to_have_count(1)

    # Reconnect → queue flushes and the canonical row takes its place; the task
    # stays visible without the user touching anything.
    page.context.set_offline(False)
    page.wait_for_function("async () => (await window.CompassSync.getQueue()).length === 0")
    expect(page.locator(".todo-row[data-title='Shows offline']").first).to_be_visible()


def test_offline_full_edit_queues_then_syncs(signed_in_page, server_url):
    page = signed_in_page
    row = _make_task(page, server_url, "Edit me offline")
    expect(row).to_be_visible()
    tid = int(row.get_attribute("data-id"))

    page.context.set_offline(True)
    row.locator(".todo-row-main").click()      # open the drawer
    row.locator(".todo-edit").click()          # open the edit modal
    expect(page.locator("#edit-task-modal")).to_be_visible()
    page.fill("#edit-task-modal input[name='title']", "Edited while offline")
    page.click("#edit-task-modal button[type='submit']")
    page.wait_for_function("async () => (await window.CompassSync.getQueue()).length === 1")

    page.context.set_offline(False)
    page.wait_for_function("async () => (await window.CompassSync.getQueue()).length === 0")
    data = page.evaluate(
        "async () => (await fetch('/sync', {credentials:'same-origin', headers:{Accept:'application/json'}})).json()"
    )
    task = next(t for t in data["tasks"] if t["id"] == tid)
    assert task["title"] == "Edited while offline", "offline edit did not sync"


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
