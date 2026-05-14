# Compass Extension (experimental) — rebuild SPEC

## What we're building

A rebuilt Chromium side-panel extension for Compass / StudyFlow that becomes
the canonical client surface going forward. Same core capability as the
existing `extension/` folder (Today / Month / Classes / settings / syllabus
upload), but with every dead button wired, every web-app feature reachable,
and the code split into small modules so future features are cheap to add.

The rebuild lives entirely inside `extension-experimental/`; the existing
`extension/` is reference material, not a dependency. When we're done, this
folder is what gets shipped.

## Scope

### In scope
- Fix every dead/broken affordance found in the audit (refresh button, open-app link, class-detail return-after-edit, etc.).
- Full **edit-task parity** with the web app: alerts and attachments editable from the edit modal (currently you have to bounce to the website).
- Add **delete-attachment** support for existing tasks.
- Auto-save the user's **timezone** to `/settings/timezone` on panel open (web app already does this).
- **Month view redesign**: vertical day-card list. Busy days render full cards; empty days collapse to thin 1-line dim rows; **every** day (busy or empty) is tappable to open Add-task pre-filled with that date.
- Add **"Today" button** to month nav so the user can jump back from arbitrary months.
- Honor **`default_class_id`** in add-task so users with classes don't have to switch off Personal every time.
- **Split `sidepanel.js`** into per-surface modules under `lib/` (one file per view + form + behavior set). Total ~10 small files instead of one 2,300-line file.

### Out of scope (explicit non-goals)
- New backend routes. Everything routes against `main.py` as it exists today.
- Changing the website (`/today`, `/week` HTML routes, templates) — only the extension changes.
- Removing the right-click "Add to Compass" capture; it works and stays.
- Dark mode toggle (the styling token system already adapts; not a redesign target).
- Mobile / Firefox port (Manifest V3 Chromium-family only).
- A real Week view in the panel — Month is the broad-window surface; the web app's `/week` route is itself a month grid, so Month covers parity.
- Test scaffolding for the extension code (manual QA per the quality bar below). Backend tests in `tests/` and `tests_browser/` continue to gate the server.

## Surfaces & screens

All surfaces live inside the side panel (the toolbar icon opens it directly via `chrome.sidePanel.setPanelBehavior`). Surfaces use view-swap (one visible at a time) inside `#logged-in`; the side panel is too narrow for stacked modals.

### 1. Login / Signup
**Elements**: header, email + password inputs, primary button, status line, server URL hint with "Change" link, link to swap between login and signup. Signup has a confirm-password field.

**States**:
- Empty: form pre-focused on email.
- Loading: status line shows "Signing in…".
- Error: red status line ("Wrong email or password.", "Couldn't reach Compass: …").
- Success: form clears, swap to logged-in surfaces.

**Interactions**: Enter submits. Server URL hint click opens the options page. Swap-to-signup / back-to-login links toggle surfaces inline.

**Edge cases**: bad server URL → readable error pointing at the options page; server unreachable → same path; passwords-don't-match (signup) → inline error.

### 2. Header + view tabs (always visible when logged in)
- Logo + dynamic context label ("Today · Fri May 09" / "May 2026" / "Classes").
- Refresh button (↻) — **wires to `load()`** so it actually refreshes. (Bug fix from audit.)
- Tabs: Today / Month / Classes — switching tab calls `setView()` which resets sub-views and reloads.

### 3. Today view
**Elements**: class blocks (one per class with items today or overdue), Personal block, drag handles on rows and class headers, "+ Add task" floating button bottom-right.

**States**:
- Empty: "Nothing for today. Use Quick Add to capture something."
- Loading: "Loading…"
- Error: "Couldn't load: <message>"
- Many items: scrollable; row drawer click-to-expand stays.

**Interactions**:
- Tap row body → opens Edit-task or Edit-event surface.
- Tap circle → toggle complete (optimistic flip; revert on failure).
- Tap × → confirm + delete; recurring tasks open the bottom-sheet picker (this date / future / all).
- Drag row → reorder within or across class blocks; cross-class moves the task's `class_id`.
- Drag class header → reorder class blocks.
- Tap class header → drill into class detail.

### 4. Month view (the redesign)
**Elements**: top bar (`‹ May 2026 ›` + "Today" button), vertical scrollable day list.

**Day card variants**:
- **Busy day**: full card. Header (weekday + date, "today" pill if applicable), class-bucketed items grouped under a tag-stripe block, drag handles per-row.
- **Empty day**: thin 1-line dim row showing weekday + date. Tappable → Add-task pre-filled with that date.
- **Today**: always rendered as a busy card (even if empty), to anchor the user.

**States**:
- Empty month: every day rendered as a thin row.
- Loading: "Loading…".
- Error: same as Today.

**Interactions**:
- Tap any day card or empty-day row → Add-task with that date.
- Inside a busy card, drag rows to reorder *within that day* via `/tasks/reorder-day`.
- `‹` `›` paginate prev/next month. "Today" jumps back to the current month.
- Class header inside a day card → drill into class detail.

### 5. Classes view
**Elements**: action bar ("+ Add class", "+ Upload syllabus" — disabled with tooltip if no xAI key set), list of class cards (code + name).

**States**:
- Empty: "No classes yet. Tap + Add class above to start."
- Otherwise: vertical list, click any to drill in.

**Interactions**: Add class → swap to add-class form. Upload syllabus → swap to upload surface (or swap to settings with "Set your xAI key" message if missing).

### 6. Class detail
**Elements**: ← back, code + name header, collapsible sections — Syllabus (PDF iframe + open-in-tab + download), Documents (list with × delete + upload form), Tasks (list), Events (list), Delete-class button at bottom.

**States**:
- Loading: "Loading…" header, sections empty.
- Error: "Couldn't load" + message.
- Each section has its own empty state ("No tasks yet", "No documents yet", etc.).

**Interactions**:
- Document title → opens in new tab (`chrome.tabs.create`).
- Document × → confirm + delete.
- Task / event row → opens edit surface; **on save, returns to class-detail** (audit-fix from current behavior of dumping back to Today).
- Delete class → confirm + delete; tasks survive as Personal (server cascades nulls `class_id`).

### 7. Add-task (FAB)
**Elements**: title, class dropdown (Personal + classes), starts_at, due_at, all-day toggle, repeat dropdown, end-date (optional), tag dropdown (system + yours, "+ New tag" inline form), reminders chips with "+ Add reminder", attachments list with "+ Add file", notes textarea, Add / Cancel.

**Smart defaults**: starts_at = next 30-min mark; due_at = +1h. **Class default = `default_class_id` from `/me.json`** (audit-fix); falls back to Personal.

**States / interactions** unchanged from existing — but the pre-fill from a Month-view day-card click sets `due_at` and `starts_at` to the picked day.

**Edge cases**: starts_at > due_at → inline error; tag = `__new__` without finishing inline create → inline error; rrule + starts_at mutually disabled (CSS `.disabled` on the label); all-day mutually disables starts_at.

### 8. Edit-task (the parity work)
**Elements**: every field Add-task has, including **alerts** and **attachments** (currently missing).

- **Alerts**: chips list pre-populated from `/tasks/{id}/details.json`; same "+ Add reminder" picker. Submit sends `alerts=` so server's partial-update knows to replace.
- **Attachments**: existing attachments listed with `× delete` (calls `POST /attachments/{id}/delete`); "+ Add file" appends new files which POST to `/tasks/{id}/attachments` after save.
- The "Reminders & attachments: edit in Compass" hint at the bottom **goes away** — they're editable here now.

**Interactions**: same as Add-task. Save → reload current view (Today / Month / Class-detail) and return to it. **Saving from Class-detail returns to Class-detail** (audit-fix).

### 9. Edit-event
**Elements**: title, kind (with system-tag autocomplete dropdown — minor improvement; currently free text), starts_at, ends_at, Save / Duplicate / Cancel.

**Interactions**: Save → reload + return to source view (today / month / class-detail). Duplicate → confirm + clone + same return.

### 10. Add-class
**Elements**: code, name, Add / Cancel. Unchanged from current.

### 11. Settings
**Elements** (sections):
- **Account**: email + Logout.
- **Timezone**: auto-detected line. **New**: "(auto-saved)" indicator after the panel POSTs `/settings/timezone` on open.
- **xAI key**: set / clear / masked-display.
- **Calendar**: webcal subscribe link, full URL display, Regenerate token (with confirm).
- **Manage tags**: list with inline rename + recolor + delete; "+ Add" form for new tags. System tags can rename + recolor but not delete.

### 12. Syllabus upload
**Elements**: drop zone + file picker, Upload button, polling status section with "Try again" button.

**States**: pre-pick / picked / uploading / parsing (poll every 2s) / done (drill into the new class) / error (show retry).

**Interactions**: drop a PDF or click to pick (≤25 MB, PDF only). Back cancels and clears the polling timer.

### 13. Recurring delete bottom sheet
Unchanged: prompts with the picked date, three options (this date / this and future / entire task) + Cancel.

### 14. Right-click "Add to Compass" (background.js)
Unchanged: context menu on selection / page / link → POST to `/tasks` with the title and source URL in notes. Toolbar badge flashes ✓ or !.

## Data model

No new server tables or columns. The extension is a thin client over the existing FastAPI server's models (`User`, `Class`, `Task`, `Tag`, `CalendarEvent`, `Document`, `TaskAlert`, `TaskAttachment`, `Syllabus`, `DayItemPosition`).

Client-side state held only in module-scope variables:
- `cachedMe` — last `/me.json` payload (email, timezone, xAI status, calendar URLs).
- `classesPromise`, `tagsPromise` — cached fetches, busted on mutation.
- `currentView`, `currentMonth`, `currentClassId` — navigation state.
- Per-form transient state (alerts, attachments, drag state).

`chrome.storage.local` holds **only** the configured server URL (`compass_url`). No other persistence.

## Tech stack

- **Manifest V3** Chromium extension, vanilla ES modules.
- **No build step.** `<script type="module" src="sidepanel.js">` loads everything; modules import each other via relative paths.
- **No external libraries.** Same posture as the existing extension.
- **Side panel API** (`chrome.sidePanel.setPanelBehavior`) for direct toolbar-icon → panel.
- **`fetch()` with `credentials: "include"`** for cookie-shared session with the Compass website.
- **Targets**: Chrome / Edge / Brave / Arc (Chromium-family with Manifest V3 + side panel API).
- **Server URL configurable** via `options.html` (defaults to `http://localhost:8000`; users point at their Heroku URL in prod).

### Module layout (split from current `sidepanel.js`)

```
extension-experimental/
├── manifest.json
├── background.js              (unchanged: side-panel-on-action + right-click capture)
├── options.html / options.js  (unchanged: server URL config)
├── popup.css                  (design tokens — kept as-is)
├── sidepanel.html             (updated: refresh-button hookup, alerts/attachments in editor)
├── sidepanel.css              (updated: month redesign + empty-day rows)
├── sidepanel.js               (slim entrypoint — boots, wires tab nav, delegates to modules)
└── lib/
    ├── api.js                 (unchanged shape; adds deleteAttachment + saveTimezone)
    ├── state.js               (cachedMe, classesPromise, tagsPromise, currentView, currentClassId)
    ├── nav.js                 (returnToList, setView, view-show/hide helpers)
    ├── views/
    │   ├── today.js           (renderBucket, renderRow, load-today)
    │   ├── month.js           (busy-day cards + empty-day strips, month-nav, "Today" button)
    │   ├── classes.js         (list + drill-down + class-detail surface)
    │   └── login.js           (login + signup forms + boot decision)
    ├── forms/
    │   ├── add-task.js        (FAB form: smart defaults, alerts chips, attachments)
    │   ├── edit-task.js       (parity edit form: alerts + attachments included)
    │   ├── event-editor.js    (title/kind/starts/ends + Duplicate)
    │   ├── add-class.js       (code + name)
    │   ├── settings.js        (account/tz/xai/calendar/manage-tags)
    │   └── syllabus.js        (drop-zone + parse polling)
    └── behaviors/
        ├── drag.js            (row drag + class-block drag)
        ├── recurring-sheet.js (delete-this/future/all picker)
        ├── tag-inline.js      (+ New tag mini-form on dropdowns)
        └── timezone.js        (one-shot tz auto-save on boot)
```

## Quality bar

### Critical paths (must work end-to-end)
1. **First-time signup** → land in app → see empty Today → tap FAB → add a Personal task → see it appear → toggle complete → reload → still complete.
2. **Existing user login** → Today loads with class buckets → drag a task across class blocks → reload → drag persisted.
3. **Add-class** → upload syllabus → wait for parse → drill into the new class → see parsed events + tasks → edit one → save returns to class-detail.
4. **Edit recurring task** → change end date → only this-and-future occurrences honor it (this is server logic; surface checks: form fields land correctly).
5. **Edit task with alerts + attachments** → add an alert chip + attach a file → save → reopen → both still there → delete one of each → save → both gone.
6. **Month view** → navigate forward 4 months → tap an empty day → Add-task opens with that day pre-filled → save → returning to month shows the new card on that day.
7. **Logout** → land on login → log back in → app is restored without artifacts from the previous session.

### Done criteria
- Manual QA in Chrome (latest stable) running locally against `http://localhost:8000`.
- All seven critical paths verified by clicking through with the dev tools console open — no red errors.
- All audit-found dead UI is wired (refresh button, open-app link, return-to-class-detail).
- All web-app parity gaps closed (alerts, attachments, attachment delete, timezone auto-save, default class).
- Existing backend test suite (`tests/` + `tests_browser/`) still passes — the extension changes don't touch `main.py`, but I'll run tests as a sanity gate before declaring done.

### Must-never-breaks
- Never lose a user's session cookie path. `credentials: "include"` on every fetch; never store passwords or tokens client-side.
- Never blow away unrelated task fields on partial-update (server handles this, but the form must always send the field set the server expects, never half).
- Never edit files outside `extension-experimental/` (freeze in effect for this rebuild).
- Never mutate user data without confirmation for destructive actions: delete class, delete tag, delete document, delete recurring task, regenerate calendar token.

## Open questions

None. Ready to build pending approval.

## Process

**No code is written until this SPEC is approved.** Reply "approved", "go", or "yes start" to begin Phase 6 (incremental build, one task at a time, with each surface re-loaded in Chrome and clicked through before moving on).

If anything in this SPEC is wrong or you want changed, say so now — I'll update the SPEC and ask again before writing code.
