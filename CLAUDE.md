# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Compass

Multi-user school task tracker. Users sign up with email + password, attach syllabus PDFs to classes, and Compass parses them via xAI Grok into a per-class calendar of events. Tasks/tags can be added manually. Everything is exposed as an iCal feed for Apple Calendar.

Same `main.py` powers both local dev (Windows tray + SQLite) and Heroku prod (Postgres + R2 object storage). Environment differences are picked up at startup from env vars.

## Run

Local server only:
```
.venv/Scripts/python.exe -m uvicorn main:app --host 0.0.0.0 --port 8000 --no-access-log
```

Local with desktop tray (Windows): double-click the `Compass` desktop shortcut, or `pythonw compass_tray.py`. The tray spawns uvicorn inside a Windows Job Object so the server dies cleanly when the tray exits.

Tests live in `tests/` (in-process API tests via FastAPI's TestClient) and `tests_browser/` (real Chromium via Playwright). Run `.venv/Scripts/python.exe -m pytest tests/ tests_browser/` for the full ~49s, 134-test suite. The default `pytest` (no args) only runs `tests/` thanks to `testpaths = tests` in `pytest.ini`. Coverage:

- **`tests/test_smoke.py`** — auth, signup, /me.json, foundation
- **`tests/test_classes.py`** — class CRUD, delete-cascade-nulls-task-class_id
- **`tests/test_tags.py`** — tag CRUD, system-tag protection, cross-user denial
- **`tests/test_tasks_create.py`** — create variants (range, all-day, rrule, notes, tag, cross-user blocks)
- **`tests/test_tasks_edit.py`** — partial-update preservation (the load-bearing bug class)
- **`tests/test_tasks_lifecycle.py`** — toggle, delete, cascade to alerts
- **`tests/test_recurrence.py`** — exclude / end-after / stop_recurrence
- **`tests/test_collectors.py`** — today/overdue dedupe, hide_completed
- **`tests/test_reorder.py`** — /tasks/reorder, /tasks/reorder-day, /classes/reorder
- **`tests/test_settings.py`** — /me.json, /settings/timezone, login/logout
- **`tests/test_week_render.py`** — rrule expansion across the month grid
- **`tests/test_ical.py`** — feed shape, RRULE/EXDATE emission, token routing
- **`tests_browser/test_form_validation.py`** — JS alerts + all-day/repeat disable interactions
- **`tests_browser/test_crud_flows.py`** — full add/edit/delete/toggle cycles in real Chromium

Each API test gets a fresh schema via the autouse `reset_db` fixture in `tests/conftest.py`; browser tests sign up a unique user per test (`browser_user_<n>@example.com`) and run against a uvicorn subprocess on a random port pointing at an isolated test DB. Adding a new edit-task field? Add a `test_edit_only_<field>_preserves_*` case to `test_tasks_edit.py` — the partial-update bug class has bitten three times. Adding new client-side validation? Add a Playwright test in `tests_browser/`. `test_syllabus.pdf` (gitignored) is a sample for manual upload tests.

## Architecture

### Single-file FastAPI app
`main.py` (~1700 lines) holds every SQLModel table, the lifespan migration, every route, and the syllabus-parsing pipeline. Don't refactor it into a package — `compass_tray.py` and the Heroku `Procfile` import `main:app` directly.

### Auth
Email + password, bcrypt-hashed in `User.password_hash`. Sessions are signed cookies via Starlette's `SessionMiddleware`, storing only `user_id`. Every protected route declares `user: User = Depends(require_login)`; if the session is empty, `require_login` raises `NotAuthenticatedError` and an exception handler 303s to `/login` for HTML clients OR returns a 401 JSON body when the request asked for `application/json` (the browser extension relies on this — a `chrome-extension://` origin can't render the login template, so it needs a status code it can detect). There is **no shared-token / dev-bypass** mode — every route requires a real account.

### Browser extension
A Manifest V3 Chromium extension lives in `extension/`. It's a thin client over the existing FastAPI server: `sidepanel.html` is the **only** UI surface (the toolbar icon opens the side panel directly via `chrome.sidePanel.setPanelBehavior({openPanelOnActionClick: true})` in `background.js`; there is no popup), `lib/api.js` wraps `fetch()` with `credentials: 'include'` so the user's session cookie rides along, `options.html` lets the user point the extension at any Compass URL (defaults to `http://localhost:8000`). Login happens **inline in the side panel** — the side panel POSTs to `/login` with `redirect: 'manual'` (303 success becomes `opaqueredirect`, 401 stays 401) and verifies via `/me.json` before swapping the panel into the app. No tab redirect, no second click. CORS is wired up in `main.py` with `CORSMiddleware(allow_origin_regex=r"^chrome-extension://[a-p]{32}$", allow_credentials=True)` — extensions are the only cross-origin clients allowed.

The side panel renders three views over the same `lib/api.js` client: **Today** (`/today.json`, class-bucketed today + overdue), **Month** (`/month.json?month=YYYY-MM`, all days in the requested calendar month as vertical day-cards with prev/next month nav), and a **class-detail drill-down** (`/classes/{id}.json`). The add-task form has full website parity: title, class, starts_at, due, all-day, tag (system + user, optgrouped), repeat, repeat-until, reminder chips, attachments (buffered then POSTed to `/tasks/{id}/attachments` after create), notes. Attachments + alerts upload only after the create call returns the new id. The extension itself stores nothing except the Compass URL in `chrome.storage.local`.

### Per-user data scoping
`Class`, `Task`, `Tag` carry `user_id` FKs directly. `Syllabus`, `CalendarEvent`, `Document` are scoped indirectly through `class_id` (their owning Class's `user_id`). When fetching by id, **always** use the ownership helpers (`_own_class`, `_own_task`, `_own_tag`, `_own_event`, `_own_document`, `_own_syllabus`) — they 404 on cross-user access. Don't write `session.get(Class, id)` in route code; it bypasses the policy.

System tags (`exam`, `assignment`, `project`, `milestone`) are **per-user**, not global — seeded into each new user's account at signup via `_seed_system_tags_for_user`. Auto-generated tags from Grok parses (e.g. `quiz`, `lab`) flow through `_ensure_system_tag(session, user_id, kind)`.

### Personal tasks (no class)
`Task.class_id` is **nullable**. A NULL `class_id` means a Personal task — a non-class to-do (groceries, errands). Routes:
- `POST /tasks` creates a Personal task; `POST /classes/{id}/tasks` creates one tied to a class. Both go through the shared `_create_task_for_user` helper.
- `delete_class` does NOT cascade-delete tasks; it nulls their `class_id` first so the user keeps their work as Personal tasks.
- The collectors (`_collect_items_in_range`, `_collect_overdue`) bucket Personal tasks under a synthetic `PERSONAL_BUCKET` (`SimpleNamespace(id=0, code="Personal", is_personal=True)`) alongside real-class buckets. Templates check `slot.cls.is_personal` to render a non-clickable header instead of a class link.
- Home + Today views merge today's items and overdue into a single `today_buckets` dict via `_merge_today_with_overdue` — one `class-block` per class, today's tasks in `slot["items"]`, past-due tasks in `slot["overdue_items"]`. Both lists go in the SAME `<ul class="todo-list todo-list-draggable">` (today first, overdue at the bottom). The merge **dedupes by `(kind, id)`**: a task due earlier today shows up in both collectors (today's date range AND `due_at < now`), so without the dedupe it'd render twice in the same class block and look like the row duplicated. Past-due wins — the dupe is dropped from `items` so the row appears only under the Overdue cap. The `render_item(it, is_overdue)` macro stamps `.is-overdue` on overdue rows, which paints the title red via CSS, and prepends a `.todo-drawer-overdue` "Overdue" cap inside the row drawer when expanded. No inline divider, no per-row "overdue" badge — the red text is the only inline signal. The old layout had two separate sections (overdue-section + today section) which duplicated class headers when a class had both today and overdue tasks. Today/home/class-page views also pass `hide_completed=True` to `_collect_items_in_range`, but the filter is **scoped to the visible window** rather than dropping every completed row: a task whose `completed_at` falls inside `[start, end)` still renders (crossed out) so the user sees their just-checked-off work for the rest of today, and rows whose `completed_at` predates the window are hidden (completed-and-overdue is "done", not "pending"). Tomorrow's pull naturally drops today's completions once `completed_at` is out of range. Week view doesn't pass `hide_completed` at all (calendar grid keeps the crossed-out history).

### Per-user timezone
`User.timezone` (IANA string) overrides the server-wide `LOCAL_TZ` default for that user's date math. `_user_tz(user)` resolves it (or falls back to LOCAL_TZ on missing/invalid). `_today_local(tz)`, `_to_local(dt, tz)`, `_collect_items_in_range(..., tz=...)`, `_collect_overdue(user_id, tz=...)` all accept a tz parameter; `home`, `today_view`, `class_view`, `week_view` resolve `tz = _user_tz(user)` once and thread it through. The iCal builder (`_build_ical_for_user`) does the same for the per-user feed (`x-wr-timezone` + naive-datetime fallbacks). Helpers without a user in scope (`parse_iso_dt`, allow-listed defaults) keep LOCAL_TZ as the static fallback. Auto-saved on every page load via `base.html` JS — reads `Intl.DateTimeFormat().resolvedOptions().timeZone` and POSTs to `/settings/timezone`, which validates against ZoneInfo and short-circuits if unchanged.
- The edit-task modal includes the class dropdown, so users can move a task between classes (or to/from Personal). `edit_task` reads the form via `request.form()` instead of `Form()` parameters because FastAPI's Form() collapses empty-string and missing into the same default — direct read lets us distinguish "clear this field" from "don't touch it" (needed for nullable `notes`, `tag_id`, `class_id`).

### Class display order + cross-class task drag
`User.class_order_json` is a JSON array of bucket-key strings (`"1"`, `"0"`, `"3"`, etc., where `"0"` = Personal) defining the user's preferred order on home/today views. `_apply_class_order(out, user_id)` re-keys the collector dicts into that order; missing buckets append at the end. `POST /classes/reorder` takes `{"order": [...]}`, validates ownership, persists. Client-side drag is bound on `[data-class-block-list]` containers (today + overdue sections) — class blocks reorder by their handle, **tasks can drag across any class block in the container** and the drop persists via `POST /tasks/{id}/edit` with `class_id=<bucketKey>`. Events stay confined to their source list (CalendarEvent.class_id is non-nullable). Personal bucket key is `"0"`; the server treats `class_id=""` as NULL.

### Per-day position overrides (week tab)
`Task.position` and `CalendarEvent.position` are **global** — one row, one priority. That works for home/today/class-page drag, but the week tab renders a multi-day task once per spanned day, so a global reorder from one day shuffles every other day too. `DayItemPosition(user_id, kind, item_id, day_date, position)` stores per-day overrides keyed on the YYYY-MM-DD the user dragged on. `_collect_items_in_range(...)` accepts a `day_for_overrides` arg; when set (only `week_view` passes it), the sort key is `overrides.get((kind, id), it["position"])`. `POST /tasks/reorder-day` (`{day, items: [...]}`) replaces all overrides for that user+day with the new list; rows missing from the new list are deleted so they revert to global. The day modal's `[data-class-block-list]` carries `data-day-date`; `persistDrop` in `static/todo.js` reads it to choose between `/tasks/reorder` (other contexts) and `/tasks/reorder-day` (in `#day-modal`). `refreshMonthGridOnly()` is safe to call after a day-modal drag: the open modal's body was cloned from the template at click-time and lives under `#day-modal` (hoisted to `<body>`), not inside `.month-grid` — replacing the grid updates the calendar pills + the next-open template without snapping the visible drag back.

### Partial-update edits
`POST /tasks/{id}/edit` only touches columns whose form fields are present in the request. The full edit modal sends every field, so explicit clears (notes='', tag_id='') still work. Drag-to-different-class sends only `class_id` and the rest of the row's data survives. When you add a new mutable column, follow the same `if "X" in form` gate — never unconditionally overwrite from form.get().

### Task notes
`Task.notes` is free-form text, optional. Surfaced in:
- The add/edit task modals as a `<textarea name="notes">` (rows=4, vertical resize).
- The row's expand drawer (see "Row drawer UX" below) — full notes text appears when the user clicks the row.
- iCal feed's `DESCRIPTION` field — flows through to Apple Calendar event details. Falls back to "Compass task" when notes are empty.

### Recurrence, alerts, attachments, all-day
Per-task power features layered onto the simple-by-default UX:

- **`Task.rrule`**: an iCalendar RRULE fragment (`FREQ=DAILY` / `FREQ=WEEKLY` / `FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR` / `FREQ=MONTHLY`, validated against `_ALLOWED_RRULES`). Compass's today/week views render the task on every occurrence date by calling `_expand_rrule_in_window` from `_emit_task` — DON'T use `due_at` alone for recurring tasks. The iCal feed emits `RRULE:FREQ=…` so Apple Calendar shows the same instances. **rrule + `starts_at` is mutually exclusive**: `_emit_task` and the iCal feed both ignore `starts_at` when `rrule` is set (a task repeats OR spans days, not both), and `bindRruleVisibility` in `static/todo.js` greys out + clears the Starts-on field whenever the Repeat dropdown isn't "Doesn't repeat" — keeping web-app rendering, the iCal feed, and the form in sync. Same field is also disabled by the All-day checkbox; the two togglers OR their disable conditions so toggling one off doesn't re-enable the field while the other is still on.
- **`Task.rrule_until`**: optional UNTIL cap on recurrence. The iCal feed embeds it inside the RRULE as `UNTIL=YYYYMMDDTHHMMSSZ` (UTC). Modal exposes it as the "End date (optional)" datetime input, JS-hidden until the user picks a Repeat option. **`edit_task` "stop recurrence here" branch**: when the form changes `rrule` from a set value to empty AND the form's `due_at` (which carries the picked occurrence date for recurring rows via the row's data-due-at) is strictly later than the task's anchor, treat it as end-after rather than wipe-all — set `rrule_until = cap - 1s`, leave `rrule` intact, and skip the normal `due_at`/`starts_at`/`rrule_until` form-field handlers so the anchor stays put. Editing the FIRST occurrence (cap ≤ anchor) falls through to the wipe path so the task becomes a plain single-date row.
- **`Task.rrule_exdates`**: JSON list of ISO datetimes to skip — populated when the user picks **"Delete this date only"** on a recurring row. The expander filters them out, and the iCal feed emits one `EXDATE` per entry. Two routes drive the recurring-delete dialog: `POST /tasks/{id}/exclude` (adds an exdate) and `POST /tasks/{id}/end-after` (sets `rrule_until` to 1s before the picked occurrence). `POST /tasks/{id}/delete` is reserved for the entire-task case (i.e., non-recurring tasks).
- **`Task.is_all_day`**: when true, the row renders "All day" instead of a time, and the iCal feed emits `DTSTART;VALUE=DATE:YYYYMMDD` (no time). The "+ Add start date" range toggle was removed; existing range tasks continue to render their span until the user re-edits, after which they become single-date.
- **`TaskAlert`**: 0..N rows per task, each with `minutes_before`. The iCal feed emits one `VALARM` per row. Smart defaults pick offsets by tag (exam → 1 day + 1 hour, quiz → 1 day, etc.) when the create form omits the field; passing an empty `alerts=` explicitly clears reminders.
- **`TaskAttachment`**: files stored through `storage.py` (local or R2). Emitted in iCal as `ATTACH;FMTTYPE=…:URI` using a token-authenticated URL (`/calendar/{token}/attachments/{filename}`) so Apple Calendar's paperclip icon can fetch without a session cookie. The cookie-auth `/uploads/{filename}` route also recognizes task attachments for in-app downloads from the drawer.

Per-tag color flows to Apple via the iCal feed: `_hex_to_css3_color` maps Compass's hex palette to the closest CSS3 named color (RFC 7986 requires CSS3 names for `COLOR`), and `X-APPLE-CALENDAR-COLOR` carries the raw hex as a fallback.

### Row drawer UX
Task rows are Apple-minimal at rest: drag handle, toggle circle, title, time-or-"All day", tag pill — that's it. **No inline indicator emojis** (no 📝, 🔁, 📎). Everything else (notes, recurrence, reminders, attachments) is surfaced inside the Edit modal — that's the canonical home for editable detail.

The `.todo-drawer` (collapsed by default, opens on row click) holds the full notes text and the Edit/Delete buttons. It does NOT show "Repeats: weekly" / "Reminders: …" status — that would re-introduce the clutter the minimalism rule is trying to avoid.

This pattern is duplicated across `_today_list.html` and `week.html` (each has its own `render_item` macro). Keep them in sync — diverging the row layouts by view leaves users wondering why the same task looks different in different places.

The drawer state is preserved across `softRefresh()` in `static/todo.js` so edit-saves don't visually destroy the open drawer.

### Database engine
Reads `DATABASE_URL` first (Heroku sets `postgres://...`, rewritten to `postgresql+psycopg://`), falls back to local `compass.db` (SQLite). The `IS_SQLITE` flag gates SQLite-only `_add_column_if_missing` migrations in the lifespan; Postgres deploys rely on `SQLModel.metadata.create_all` from a fresh DB.

### Storage abstraction (`storage.py`)
Syllabus PDFs and class documents go through `storage.save / read / delete / exists / serve` — never `UPLOAD_DIR / filename` directly. `STORAGE_BACKEND=local` (default, dev) writes to `./uploads/`; `STORAGE_BACKEND=s3` uses an S3-compatible bucket (Cloudflare R2 in prod). Heroku's filesystem is ephemeral so prod must use S3. `extract_pdf_text` accepts either a `Path` or `bytes` so callers don't round-trip through a tempfile when storage is remote.

### Syllabus parsing
Uploads land in `storage`, then a FastAPI `BackgroundTask` runs `process_syllabus(syllabus_id)`. That calls `parse_syllabus_with_grok(text, user_key)` against xAI's API using **the class owner's per-user `xai_api_key`** (set on `/settings`). There is no server-wide xAI fallback — uploads from accounts without a key are blocked at the `/syllabus` route with a redirect to `/settings?need_key=1`.

In-memory `parse_jobs: dict[int, str]` tracks per-syllabus parse state (`"pending"`, `"running"`, `"done"`, `"error: …"`); `/syllabus/{id}/status[.json]` polling pages read from it. State resets on server restart — fine since parses take seconds, not minutes.

### iCal feed
Must mirror `_emit_task`'s expansion rules so what users see in the web app matches what Apple Calendar renders. Specifically: **when `rrule` is set, emit a single-instant `DTSTART=due_at` and skip `DTEND` even if `starts_at` is populated.** Otherwise Apple interprets `DTSTART=starts_at + DTEND=due_at + RRULE` as a multi-day banner repeating daily, generating one full-span occurrence per anchor day — the user sees a flood of overlapping events while the web app shows nothing (its `_emit_task` ignores range data once `rrule` is non-empty).

Two routes serve the same content from `_build_ical_for_user(user_id)`:
- `GET /calendar.ics` — cookie-auth, for in-browser tabs.
- `GET /calendar/{token}.ics` — public-but-unguessable, for Apple Calendar / Google Calendar / etc. that can't carry a session cookie across long-lived subscriptions. The token is `User.calendar_token` (`secrets.token_urlsafe(32)`, set at signup, backfilled into existing rows during the lifespan migration). Rotating it via `/settings/calendar/regenerate` revokes all subscriptions instantly.

The feed includes both syllabus-extracted `CalendarEvent` rows AND user-created `Task` rows that have a `due_at` and aren't completed. Stable UIDs (`compass-event-{id}@compass`, `compass-task-{id}@compass`) so client updates propagate cleanly without duplicates. Each VEVENT carries a 15-min `VALARM` for actionable items.

`/settings` exposes a `webcal://` link so a single tap opens the Apple Calendar subscribe dialog.

### Tray launcher (`compass_tray.py`)
Windows desktop entry point. Spawns uvicorn as a subprocess inside a Windows Job Object with `KILL_ON_JOB_CLOSE` so orphan servers can't survive a force-killed tray. Loads `.compass_secret_key`, `.xai_key`, `.xai_model` from disk into env vars on each Restart so users can rotate keys without touching system env. Not used in production.

## Conventions

- HTML routes use `Depends(require_login)` and accept the `User` as a parameter. Never inline `request.session.get("user_id")` checks in route bodies — that path is reserved for the templates.
- When scoping a query, write `select(X).where(X.user_id == user.id)` rather than filtering after the fetch.
- Log via the module-level `log = logging.getLogger("compass")`. The handler writes to `compass.log` so the tray can tail it.
- **Keep this file current.** After any architecture-level change (new models, new auth mechanism, new top-level files, new env vars, deploy target changes), update the relevant section here in the same turn. Skip for typos / renames / bug fixes.

## Skill routing

When the user's request matches an available skill, invoke it via the Skill tool. When in doubt, invoke the skill.

Key routing rules:
- Product ideas/brainstorming → invoke /office-hours
- Strategy/scope → invoke /plan-ceo-review
- Architecture → invoke /plan-eng-review
- Design system/plan review → invoke /design-consultation or /plan-design-review
- Full review pipeline → invoke /autoplan
- Bugs/errors → invoke /investigate
- QA/testing site behavior → invoke /qa or /qa-only
- Code review/diff check → invoke /review
- Visual polish → invoke /design-review
- Ship/deploy/PR → invoke /ship or /land-and-deploy
- Save progress → invoke /context-save
- Resume context → invoke /context-restore
