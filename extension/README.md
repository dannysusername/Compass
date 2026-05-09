# Compass browser extension

Quick-add tasks to your Compass calendar from any tab. Talks to the
existing Compass FastAPI server — no database, no auth, no logic of its
own; the extension is a thin client.

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

1. Click the Compass icon. The popup will say "Sign in to Compass".
2. Click **Log in to Compass** — it opens `http://localhost:8000/login` in a tab.
3. Log in there.
4. Click the Compass icon again — the quick-add form appears.

The popup uses your existing browser cookie, so once you're logged in to
Compass in any tab, the extension is authenticated.

## Settings

Right-click the Compass icon → **Options** to change the server URL. The
default is `http://localhost:8000`. When you eventually deploy to Heroku,
change it here and grant the permission prompt — no rebuild needed.

## Files

- `manifest.json` — Manifest V3. host_permissions for the local Compass URLs, sidePanel permission, background service worker.
- `popup.html` / `popup.js` / `popup.css` — the quick-add form (icon click).
- `sidepanel.html` / `sidepanel.js` / `sidepanel.css` — the Today list (opened from the popup's "Today list →" link). Read-only in this iteration.
- `options.html` / `options.js` — settings page (Compass URL).
- `background.js` — service worker, fallback path for opening the side panel.
- `lib/api.js` — fetch wrapper + auth detection. All network goes here.

## Surfaces

- **Popup** — quick-add. Click the toolbar icon, type a task, hit Add.
- **Side panel** — pinned Today list. Open from the popup's "Today list →" link. Stays alongside whatever browsing you're doing. Read-only for now (toggle/edit/delete coming next).

Future phases: Week view + per-class detail in the side panel.
