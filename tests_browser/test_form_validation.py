"""Browser tests for the JS-side form rules — the things pytest can't
reach because they live in `static/todo.js` and only fire when a real
form is being driven by a real browser.

What's covered:
  - The "start date must be before end date" alert in both the add-task
    modal and the edit-task modal.
  - All-day checkbox disables the Starts-on input.
  - Picking a Repeat option disables the Starts-on input (rrule + range
    is mutually exclusive).
  - Toggling either of those off re-enables Starts-on iff the other is
    also off (the OR'd disable condition we wired up earlier).

Each test signs up a fresh user (browser_user_<n>@example.com) so
state from earlier tests can't leak in.
"""
from datetime import datetime

from playwright.sync_api import expect


def _today_iso(hour: int, minute: int = 0) -> str:
    """`YYYY-MM-DDTHH:MM` for today at the given local time. Tasks dated
    today land on the home page's Today list, which is where the row
    selectors below look for them."""
    d = datetime.now().replace(hour=hour, minute=minute, second=0, microsecond=0)
    return d.strftime("%Y-%m-%dT%H:%M")


def _open_add_task_modal(page):
    """Click the '+ Add task' button on the home page. Modal markup
    lives in the today list partial; modal.js hoists it to <body> at
    page load so it isn't trapped inside an overflow:auto ancestor."""
    page.click("button[data-open-modal='add-task-modal']")
    # Modal becomes visible — wait for the first input so subsequent
    # `.fill()` calls don't race the open animation.
    expect(page.locator("#add-task-modal input[name='title']")).to_be_visible()


def _create_task_via_modal(page, title, starts=None, due=None):
    """Fill the add-task modal and submit. Returns once the modal is
    closed (signalling submit succeeded)."""
    _open_add_task_modal(page)
    page.fill("#add-task-modal input[name='title']", title)
    if starts:
        page.fill("#add-task-modal input[name='starts_at']", starts)
    if due:
        page.fill("#add-task-modal input[name='due_at']", due)
    page.click("#add-task-modal button[type='submit']")


# ---- Date validation alerts ----

def test_add_task_alerts_when_starts_after_due(signed_in_page):
    """Submitting the add-task modal with starts > due must trigger the
    'Cannot save event' alert (via window.alert) and NOT submit. We
    register a dialog handler before clicking so Playwright captures
    the message, then assert the modal is still open afterwards."""
    page = signed_in_page
    captured = []
    page.on("dialog", lambda d: (captured.append(d.message), d.accept()))

    _open_add_task_modal(page)
    page.fill("#add-task-modal input[name='title']", "Backwards")
    page.fill("#add-task-modal input[name='starts_at']", "2030-09-10T17:00")
    page.fill("#add-task-modal input[name='due_at']", "2030-09-05T09:00")
    page.click("#add-task-modal button[type='submit']")

    # Alert should have fired with the date-order message.
    assert any("start date must be before" in m.lower() for m in captured), (
        f"expected start/end alert, got: {captured!r}"
    )
    # Modal stays open since the submit handler returned early.
    expect(page.locator("#add-task-modal input[name='title']")).to_be_visible()


def test_edit_task_alerts_when_starts_after_due(signed_in_page):
    """Same rule on the edit modal. First create a normal task, then
    open it via the row's drawer + Edit button, then try a backwards
    range and assert the alert fires."""
    page = signed_in_page
    # Seed: create a normal task so there's something to edit. Date it
    # today so the row lands on the home page's Today list (where the
    # selector below picks it up).
    _create_task_via_modal(
        page, "To edit",
        starts=_today_iso(9, 0),
        due=_today_iso(17, 0),
    )
    # Wait for the row to render. softRefresh re-pulls the today list
    # after submit; the row appears once that's done.
    row = page.locator(".todo-row[data-kind='task']").first
    expect(row).to_be_visible()
    # Open the row drawer (click the row body), then Edit.
    row.locator(".todo-row-main").click()
    row.locator(".todo-edit").click()
    expect(page.locator("#edit-task-modal input[name='title']")).to_be_visible()

    captured = []
    page.on("dialog", lambda d: (captured.append(d.message), d.accept()))

    # Set backwards range and try to save.
    page.fill("#edit-task-modal input[name='starts_at']", "2030-09-10T17:00")
    page.fill("#edit-task-modal input[name='due_at']", "2030-09-05T09:00")
    page.click("#edit-task-modal button[type='submit']")

    assert any("start date must be before" in m.lower() for m in captured), (
        f"expected start/end alert, got: {captured!r}"
    )
    expect(page.locator("#edit-task-modal input[name='title']")).to_be_visible()


# ---- Mutually-exclusive disables ----

def test_all_day_keeps_starts_on_enabled_as_date(signed_in_page):
    """Toggling All-day keeps Starts-on USABLE as a date-only input — an
    all-day task may span multiple days (start date → due date). Only a
    Repeat disables Starts-on. (Regression: All-day used to disable it,
    which both removed the multi-day span and — via the smart-default
    start the edit modal injected — silently blocked saves.)"""
    page = signed_in_page
    _open_add_task_modal(page)

    starts = page.locator("#add-task-modal input[name='starts_at']")
    expect(starts).not_to_be_disabled()  # baseline

    page.check("#add-task-modal input[data-task-all-day]")
    expect(starts).not_to_be_disabled()
    expect(starts).to_have_attribute("type", "date")

    page.uncheck("#add-task-modal input[data-task-all-day]")
    expect(starts).not_to_be_disabled()
    expect(starts).to_have_attribute("type", "datetime-local")


def test_repeat_disables_starts_on(signed_in_page):
    """Picking any Repeat option clears + disables Starts-on. rrule +
    range is mutually exclusive (a task repeats OR spans days, not both)."""
    page = signed_in_page
    _open_add_task_modal(page)

    starts = page.locator("#add-task-modal input[name='starts_at']")
    expect(starts).not_to_be_disabled()

    page.select_option(
        "#add-task-modal select[data-task-rrule]",
        "FREQ=DAILY",
    )
    expect(starts).to_be_disabled()
    expect(starts).to_have_value("")

    # Back to "Doesn't repeat" → Starts-on returns.
    page.select_option("#add-task-modal select[data-task-rrule]", "")
    expect(starts).not_to_be_disabled()


def test_all_day_and_repeat_or_their_disable_state(signed_in_page):
    """When both All-day and Repeat are on, toggling ONE off does NOT
    re-enable Starts-on — the other still wants it disabled. Only
    when both are off is Starts-on editable again. (This was the
    interaction we wired earlier between bindAllDayBehavior and
    bindRruleVisibility.)"""
    page = signed_in_page
    _open_add_task_modal(page)

    starts = page.locator("#add-task-modal input[name='starts_at']")
    page.check("#add-task-modal input[data-task-all-day]")
    page.select_option("#add-task-modal select[data-task-rrule]", "FREQ=DAILY")
    expect(starts).to_be_disabled()

    # Toggle All-day off — Repeat is still set, so Starts stays disabled.
    page.uncheck("#add-task-modal input[data-task-all-day]")
    expect(starts).to_be_disabled()

    # Now clear Repeat — Starts becomes editable.
    page.select_option("#add-task-modal select[data-task-rrule]", "")
    expect(starts).not_to_be_disabled()
