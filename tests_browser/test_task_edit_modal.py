"""Edit-task MODAL regression tests (pencil → fill → Save), in real Chromium.

These cover gaps the older suite missed by always using an 8pm due time:
the edit modal used to inject a 6pm smart-default *start* into any task
that had none, which then tripped the client-side "start must be before
due" guard and SILENTLY blocked the save for:
  - every overdue task (past due < present-day default start), and
  - any task due earlier than ~6pm (e.g. a 10am task).

Also guards the all-day behavior: All-day must keep the Starts-on field
usable (date-only multi-day span), and must never yield a date-less task.
"""
import datetime
import re

from playwright.sync_api import expect


def _today_iso(h, m=0):
    d = datetime.datetime.now().replace(hour=h, minute=m, second=0, microsecond=0)
    return d.strftime("%Y-%m-%dT%H:%M")


def _add_task_via_api(page, server_url, title, due_at):
    """Create a task through the server (fast, deterministic) and return its id."""
    page.request.post(server_url + "/tasks", form={"title": title, "due_at": due_at})
    page.goto(server_url + "/")
    return page.locator(f".todo-row[data-title='{title}']").first.get_attribute("data-id")


def _edit_title_via_modal(page, server_url, title, new_title):
    """Drive the real edit modal: open drawer, pencil, change title, Save.
    Returns the /edit response status (or None if no POST fired)."""
    row = page.locator(f".todo-row[data-title='{title}']").first
    row.locator("[data-row-toggle]").click()
    row.locator(".todo-edit").click()
    modal = page.locator("#edit-task-modal")
    expect(modal).to_be_visible()
    modal.locator("input[name='title']").fill(new_title)
    try:
        with page.expect_response(re.compile(r"/tasks/\d+/edit"), timeout=4000) as ri:
            modal.locator("button[type='submit']").click()
        return ri.value.status
    except Exception:
        return None


def test_edit_overdue_task_saves_via_modal(signed_in_page, server_url):
    """THE overdue bug: editing a past-due task via the modal must save."""
    page = signed_in_page
    tid = _add_task_via_api(page, server_url, "Overdue edit", _today_iso(10, 0))
    twoago = (datetime.date.today() - datetime.timedelta(days=2)).strftime("%Y-%m-%dT10:00")
    page.request.post(server_url + f"/tasks/{tid}/edit", form={"due_at": twoago})
    page.goto(server_url + "/")
    expect(page.locator("li.todo-row[data-title='Overdue edit']").first).to_have_class(
        re.compile(r"is-overdue")
    )

    status = _edit_title_via_modal(page, server_url, "Overdue edit", "Overdue edit RENAMED")
    assert status == 200, f"overdue edit did not POST/save (status={status})"
    page.goto(server_url + "/")
    expect(page.locator(".todo-row[data-title='Overdue edit RENAMED']").first).to_be_visible()


def test_edit_morning_task_saves_via_modal(signed_in_page, server_url):
    """A task due before 6pm must also save (same phantom-start guard)."""
    page = signed_in_page
    _add_task_via_api(page, server_url, "Morning task", _today_iso(10, 0))
    status = _edit_title_via_modal(page, server_url, "Morning task", "Morning task RENAMED")
    assert status == 200, f"morning-due edit did not POST/save (status={status})"
    page.goto(server_url + "/")
    expect(page.locator(".todo-row[data-title='Morning task RENAMED']").first).to_be_visible()


def test_all_day_keeps_start_field_enabled(signed_in_page):
    """All-day must allow a (date-only) start — it must NOT disable Starts-on."""
    page = signed_in_page
    page.click("button[data-open-modal='add-task-modal']")
    modal = page.locator("#add-task-modal")
    starts = modal.locator("input[name='starts_at']")
    modal.locator("[data-task-all-day]").check()
    expect(starts).to_be_enabled()
    expect(starts).to_have_attribute("type", "date")


def test_all_day_cleared_date_still_gets_a_date(signed_in_page, server_url):
    """Submitting All-day with the date cleared must NOT create a date-less
    orphan — the server backstops a missing date to today."""
    page = signed_in_page
    page.click("button[data-open-modal='add-task-modal']")
    modal = page.locator("#add-task-modal")
    modal.locator("input[name='title']").fill("No date allday")
    modal.locator("[data-task-all-day]").check()
    modal.locator("input[name='due_at']").fill("")
    modal.locator("input[name='starts_at']").fill("")
    modal.locator("button[type='submit']").click()
    page.goto(server_url + "/")
    row = page.locator(".todo-row[data-title='No date allday']").first
    expect(row).to_be_visible()
    assert row.get_attribute("data-due-at"), "all-day task was created with no date (orphan)"
