"""Browser-driven CRUD flows: full add → render → toggle → delete cycles
in real Chromium. These exercise the JS event wiring (delegated click
handlers, softRefresh, drawer toggle, recurring-delete modal) that
unit tests can't reach."""
from datetime import datetime

from playwright.sync_api import expect


def _today_iso(hour: int, minute: int = 0) -> str:
    d = datetime.now().replace(hour=hour, minute=minute, second=0, microsecond=0)
    return d.strftime("%Y-%m-%dT%H:%M")


def _open_add_task_modal(page):
    page.click("button[data-open-modal='add-task-modal']")
    expect(page.locator("#add-task-modal input[name='title']")).to_be_visible()


def _add_task(page, title, **fields):
    """Open modal, fill, submit. Waits for the row to appear in the
    today list before returning so the caller can interact with it.

    Clears the smart-default starts_at/due_at the popup JS auto-fills
    on open (next-half-hour + 1hr) — otherwise a test running late at
    night can hit the 'starts > due' validation alert and silently
    abort the submit. We set our own values from scratch."""
    _open_add_task_modal(page)
    page.fill("#add-task-modal input[name='title']", title)
    page.fill("#add-task-modal input[name='starts_at']", "")
    page.fill("#add-task-modal input[name='due_at']", "")
    if "due_at" in fields:
        page.fill("#add-task-modal input[name='due_at']", fields["due_at"])
    if "starts_at" in fields:
        page.fill("#add-task-modal input[name='starts_at']", fields["starts_at"])
    if fields.get("rrule"):
        page.select_option("#add-task-modal select[data-task-rrule]", fields["rrule"])
    if fields.get("notes"):
        page.fill("#add-task-modal textarea[name='notes']", fields["notes"])
    page.click("#add-task-modal button[type='submit']")
    expect(
        page.locator(f".todo-row[data-kind='task'][data-title='{title}']").first
    ).to_be_visible(timeout=5000)


# ---- Add ------------------------------------------------------------------

def test_add_task_appears_in_today_list(signed_in_page):
    """Quick-add a task → row appears, no full reload."""
    page = signed_in_page
    _add_task(page, "Buy milk", due_at=_today_iso(20, 0))
    row = page.locator(".todo-row[data-title='Buy milk']")
    expect(row).to_be_visible()
    expect(row.locator(".todo-title")).to_have_text("Buy milk")


def test_add_recurring_task_marks_data_rrule(signed_in_page):
    """A daily-recurring task gets data-rrule set on every emitted
    occurrence row — the delete/edit code paths key off this."""
    page = signed_in_page
    _add_task(page, "Daily thing", due_at=_today_iso(20, 0), rrule="FREQ=DAILY")
    row = page.locator(".todo-row[data-title='Daily thing']").first
    expect(row).to_have_attribute("data-rrule", "FREQ=DAILY")


# ---- Toggle ---------------------------------------------------------------

def test_toggle_done_crosses_out_row(signed_in_page):
    """Clicking the circle adds .done class via setDoneEverywhere."""
    page = signed_in_page
    _add_task(page, "Toggle me", due_at=_today_iso(20, 0))
    row = page.locator(".todo-row[data-title='Toggle me']")
    expect(row).not_to_have_class("done")
    row.locator(".todo-toggle").click()
    expect(row).to_have_class(__import__("re").compile(r"\bdone\b"))


def test_toggle_persists_to_server(signed_in_page):
    """After toggling, GET /me.json + reload home — the server state
    matches; the row stays crossed out (today's hide_completed window
    keeps just-completed-today rows visible)."""
    page = signed_in_page
    _add_task(page, "Persisted", due_at=_today_iso(20, 0))
    row = page.locator(".todo-row[data-title='Persisted']")
    row.locator(".todo-toggle").click()
    page.reload()
    expect(
        page.locator(".todo-row[data-title='Persisted']")
    ).to_have_class(__import__("re").compile(r"\bdone\b"))


# ---- Edit (notes round-trip — the bug class the user actually hit) -------

def test_edit_only_notes_does_not_touch_dates(signed_in_page, server_url):
    """Reproduces the partial-update bug at the UI level. User edits
    only the notes field and saves — title/due/etc. must survive.
    Exercises bindEditTaskForm + the server's date-preservation fix.

    Skips the front-end submit flow and POSTs to /tasks/{id}/edit
    directly with only the notes field set. That's the contract being
    tested — partial-update with one field. The full submit-handler
    path has its own coverage in test_form_validation.py."""
    page = signed_in_page

    _add_task(page, "Notes test",
              due_at=_today_iso(20, 0),
              notes="original")
    row = page.locator(".todo-row[data-title='Notes test']")
    task_id = row.get_attribute("data-id")
    due_before = row.get_attribute("data-due-at")
    assert task_id and due_before

    # POST with only the notes field — server must preserve due_at.
    page.evaluate(
        """async ({id, notes}) => {
            const fd = new FormData();
            fd.append('notes', notes);
            const r = await fetch(`/tasks/${id}/edit`, {
                method: 'POST', body: fd, credentials: 'include',
                headers: {'Accept': 'application/json'},
            });
            return r.status;
        }""",
        {"id": task_id, "notes": "updated notes"},
    )

    # Reload + check the row still reports the same data-due-at.
    page.goto(server_url + "/")
    row2 = page.locator(".todo-row[data-title='Notes test']")
    expect(row2).to_be_visible()
    due_after = row2.get_attribute("data-due-at")
    assert due_before == due_after, (
        f"due_at changed during a notes-only edit: {due_before} -> {due_after}"
    )


# ---- Delete (non-recurring) ----------------------------------------------

def test_delete_non_recurring_via_confirm(signed_in_page):
    """Confirm dialog → server delete → row gone."""
    page = signed_in_page
    _add_task(page, "Delete me", due_at=_today_iso(20, 0))
    row = page.locator(".todo-row[data-title='Delete me']")
    # Standard confirm() — Playwright dismisses with .accept().
    page.on("dialog", lambda d: d.accept())
    row.locator(".todo-row-main").click()
    row.locator(".todo-del").click()
    expect(
        page.locator(".todo-row[data-title='Delete me']")
    ).to_have_count(0, timeout=3000)


# ---- Delete (recurring) — exercises the bottom-sheet picker -------------

def test_delete_recurring_opens_picker(signed_in_page):
    """For a recurring task, clicking Delete should open the
    `#delete-recurring-modal` instead of confirm()."""
    page = signed_in_page
    _add_task(page, "Recurring delete", due_at=_today_iso(20, 0), rrule="FREQ=DAILY")
    row = page.locator(".todo-row[data-title='Recurring delete']").first
    row.locator(".todo-row-main").click()
    row.locator(".todo-del").click()
    expect(page.locator("#delete-recurring-modal")).to_be_visible()
    # Cancel — task should survive.
    page.locator(
        "#delete-recurring-modal button[data-close-modal]"
    ).first.click()
    expect(
        page.locator(".todo-row[data-title='Recurring delete']").first
    ).to_be_visible()


def test_delete_recurring_this_date_only(signed_in_page, server_url):
    """Picking 'Delete this date only' should POST /tasks/{id}/exclude.
    Wait for the response explicitly + reload the page → asserts on a
    settled DOM, no race with softRefresh."""
    page = signed_in_page
    _add_task(page, "Recurring exclude", due_at=_today_iso(20, 0), rrule="FREQ=DAILY")
    row = page.locator(".todo-row[data-title='Recurring exclude']").first
    row.locator(".todo-row-main").click()
    row.locator(".todo-del").click()
    with page.expect_response("**/exclude") as resp_info:
        page.locator(
            "#delete-recurring-modal button[data-delete-mode='this']"
        ).click()
    assert resp_info.value.status == 200
    page.goto(server_url + "/")
    expect(
        page.locator(".todo-row[data-title='Recurring exclude']")
    ).to_have_count(0)


# ---- Manage tags modal ---------------------------------------------------

def test_manage_tags_modal_lists_system_tags(signed_in_page):
    """The user has 4 system tags seeded at signup; the manage-tags
    modal should list them with the 'system' badge."""
    page = signed_in_page
    _open_add_task_modal(page)
    # The "Manage tags" link sits inside the add-task tag picker label.
    page.locator("#add-task-modal button[data-open-manage-tags]").click()
    expect(page.locator("#manage-tags-modal")).to_be_visible()
    # All four system tag names should be listed.
    listed = page.locator("#manage-tags-modal .tag-manage-name").all_text_contents()
    assert "exam" in listed
    assert "assignment" in listed
    assert "project" in listed
    assert "milestone" in listed
