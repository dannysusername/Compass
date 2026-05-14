# Compass browser extension

Quick-add tasks and view your Today / Month list from any tab. Talks to
the existing Compass FastAPI server — no database, no auth, no logic of
its own; the extension is a thin client.

## Install (development)

1. Make sure Compass is running locally:
   ```
   .venv/Scripts/python.exe -m uvicorn main:app --host 0.0.0.0 --port 8000 --no-access-log
   ```
2. Open Chrome / Edge / Brave / Arc → `chrome://extensions`.
3. Toggle **Developer mode** on (top-right).
4. Click **Load unpacked** → select this `extension/` directory.
5. Pin the Compass icon to the toolbar.

## First use

1. Click the Compass icon. The side panel opens with an inline login form.
2. Type your Compass email + password, hit **Log in**.
3. The panel swaps into the app — Today by default, Month tab next to it.

The session cookie that lands on the Compass origin during login is
shared with the Compass website, so logging in here also logs you in
on `localhost:8000`.

## Settings

Right-click the Compass icon → **Options** to change the server URL. The
default is `http://localhost:8000`. When you deploy to Heroku, change it
here and grant the host-permission prompt — no rebuild needed.

## Files

- `manifest.json` — Manifest V3. host_permissions for the local Compass URLs, sidePanel permission, background service worker. The toolbar icon opens the side panel directly (no popup).
- `sidepanel.html` / `sidepanel.js` / `sidepanel.css` — the entire app surface: inline login, Today + Month views, full add-task form, task editor, class-detail drill-down.
- `popup.css` — design tokens (--paper, --ink, --serif). Loaded by `sidepanel.html`. Named `popup.css` for historical reasons; there is no popup any more.
- `options.html` / `options.js` — settings page (Compass URL).
- `background.js` — service worker. Sets `openPanelOnActionClick` so toolbar clicks open the side panel; also wires the right-click "Add to Compass" context menu.
- `lib/api.js` — fetch wrapper + auth detection. All network goes here.

## Surfaces

- **Side panel** — the only surface. Opens directly when you click the toolbar icon. Inline login when logged out; Today + Month views, full add-task form (every field the website's modal exposes — class, dates, all-day, repeat + end date, tag, reminder chips, attachments, notes), task editor, class-detail drill-down, drag-to-reorder.
- **Right-click → "Add to Compass"** — works on any page or with text selected. Posts straight to `/tasks` (Personal task) using your existing session cookie. Toolbar icon flashes a green ✓ on success or red ! on failure (most likely cause: not logged into Compass).
