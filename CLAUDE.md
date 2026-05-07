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

There is no test suite. Verify changes by running the server and clicking through. `test_syllabus.pdf` (gitignored) is a sample for manual upload tests.

## Architecture

### Single-file FastAPI app
`main.py` (~1700 lines) holds every SQLModel table, the lifespan migration, every route, and the syllabus-parsing pipeline. Don't refactor it into a package — `compass_tray.py` and the Heroku `Procfile` import `main:app` directly.

### Auth
Email + password, bcrypt-hashed in `User.password_hash`. Sessions are signed cookies via Starlette's `SessionMiddleware`, storing only `user_id`. Every protected route declares `user: User = Depends(require_login)`; if the session is empty, `require_login` raises `NotAuthenticatedError` and an exception handler 303s to `/login`. There is **no shared-token / dev-bypass** mode — every route requires a real account.

### Per-user data scoping
`Class`, `Task`, `Tag` carry `user_id` FKs directly. `Syllabus`, `CalendarEvent`, `Document` are scoped indirectly through `class_id` (their owning Class's `user_id`). When fetching by id, **always** use the ownership helpers (`_own_class`, `_own_task`, `_own_tag`, `_own_event`, `_own_document`, `_own_syllabus`) — they 404 on cross-user access. Don't write `session.get(Class, id)` in route code; it bypasses the policy.

System tags (`exam`, `assignment`, `project`, `milestone`) are **per-user**, not global — seeded into each new user's account at signup via `_seed_system_tags_for_user`. Auto-generated tags from Grok parses (e.g. `quiz`, `lab`) flow through `_ensure_system_tag(session, user_id, kind)`.

### Personal tasks (no class)
`Task.class_id` is **nullable**. A NULL `class_id` means a Personal task — a non-class to-do (groceries, errands). Routes:
- `POST /tasks` creates a Personal task; `POST /classes/{id}/tasks` creates one tied to a class. Both go through the shared `_create_task_for_user` helper.
- `delete_class` does NOT cascade-delete tasks; it nulls their `class_id` first so the user keeps their work as Personal tasks.
- The collectors (`_collect_items_in_range`, `_collect_overdue`) bucket Personal tasks under a synthetic `PERSONAL_BUCKET` (`SimpleNamespace(id=0, code="Personal", is_personal=True)`) alongside real-class buckets. Templates check `slot.cls.is_personal` to render a non-clickable header instead of a class link.
- The edit-task modal includes the class dropdown, so users can move a task between classes (or to/from Personal). `edit_task` reads the form via `request.form()` instead of `Form()` parameters because FastAPI's Form() collapses empty-string and missing into the same default — direct read lets us distinguish "clear this field" from "don't touch it" (needed for nullable `notes`, `tag_id`, `class_id`).

### Class display order
`User.class_order_json` is a JSON array of bucket-key strings (`"1"`, `"0"`, `"3"`, etc., where `"0"` = Personal) defining the user's preferred order on home/today views. `_apply_class_order(out, user_id)` re-keys the collector dicts into that order; missing buckets append at the end. `POST /classes/reorder` takes `{"order": [...]}`, validates ownership, persists. Client-side drag is bound on `[data-class-block-list]` containers — class blocks can be reordered as units, but tasks can't be dragged across class boundaries (per-class `.todo-list-draggable` is the task drag scope).

### Task notes
`Task.notes` is free-form text, optional. Surfaced in:
- The add/edit task modals as a `<textarea name="notes">` (rows=4, vertical resize).
- The row's expand drawer (see "Row drawer UX" below) — full notes text appears when the user clicks the row.
- A subtle 📝 indicator on the row (not a button — just a hint that notes exist).
- iCal feed's `DESCRIPTION` field — flows through to Apple Calendar event details. Falls back to "Compass task" when notes are empty.

### Row drawer UX
Task rows are intentionally minimal at rest: drag handle, toggle circle, title, tag pill, time. The 📝 indicator is the only nod to "extra content lives here." Edit/delete buttons are NOT inline — they live in a `.todo-drawer` that's collapsed by default. Clicking anywhere on `.todo-row-main` (except the toggle / drag handle / inner buttons) toggles the drawer. The drawer holds the full notes text, an Edit button (opens the edit modal), and a Delete button.

This pattern lives in `_today_list.html` and `week.html` (duplicated row macros — keep in sync). The drawer state is preserved across `softRefresh()` in `static/todo.js` so edit-saves don't visually destroy the open drawer.

### Database engine
Reads `DATABASE_URL` first (Heroku sets `postgres://...`, rewritten to `postgresql+psycopg://`), falls back to local `compass.db` (SQLite). The `IS_SQLITE` flag gates SQLite-only `_add_column_if_missing` migrations in the lifespan; Postgres deploys rely on `SQLModel.metadata.create_all` from a fresh DB.

### Storage abstraction (`storage.py`)
Syllabus PDFs and class documents go through `storage.save / read / delete / exists / serve` — never `UPLOAD_DIR / filename` directly. `STORAGE_BACKEND=local` (default, dev) writes to `./uploads/`; `STORAGE_BACKEND=s3` uses an S3-compatible bucket (Cloudflare R2 in prod). Heroku's filesystem is ephemeral so prod must use S3. `extract_pdf_text` accepts either a `Path` or `bytes` so callers don't round-trip through a tempfile when storage is remote.

### Syllabus parsing
Uploads land in `storage`, then a FastAPI `BackgroundTask` runs `process_syllabus(syllabus_id)`. That calls `parse_syllabus_with_grok(text, user_key)` against xAI's API using **the class owner's per-user `xai_api_key`** (set on `/settings`). There is no server-wide xAI fallback — uploads from accounts without a key are blocked at the `/syllabus` route with a redirect to `/settings?need_key=1`.

In-memory `parse_jobs: dict[int, str]` tracks per-syllabus parse state (`"pending"`, `"running"`, `"done"`, `"error: …"`); `/syllabus/{id}/status[.json]` polling pages read from it. State resets on server restart — fine since parses take seconds, not minutes.

### iCal feed
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
