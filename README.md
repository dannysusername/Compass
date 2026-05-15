# Compass

**Compass is live — just open it in your browser:**

### 👉 https://dannibar-compass-44cf6055d5e3.herokuapp.com/

No install, no setup, nothing to download. Sign up with an email + password and you're in. Everything below is for people who want to *develop* Compass; if you just want to *use* it, the link above is all you need.

---

## What it is

A multi-user school task tracker:

- **Attach syllabus PDFs to a class** and Compass parses them with xAI's Grok into a per-class calendar of exams, assignments, and milestones.
- **Add tasks and tags by hand** — class-bound or personal to-dos, with recurrence, reminders, attachments, all-day, and notes.
- **One iCal feed** exposes everything so it shows up natively in Apple Calendar (or Google Calendar, or anything that subscribes to a calendar URL).
- **Browser extension** (Chromium side panel) gives you the same app docked next to whatever you're working on.

Each account is fully isolated — its own classes, tasks, tags, syllabi, and calendar token.

## Using it

1. **Sign up** at the link above.
2. **Make a class**, then **upload its syllabus PDF**. Parsing takes ~10–30s and drops exam/assignment dates straight onto your calendar.
   - You get a handful of **free parses on the house** — no API key required. That's enough to try it on a real semester.
   - Want unlimited? Paste your own xAI/Grok key in **Settings** (https://console.x.ai/) and the cap goes away — those parses run on your quota.
3. **Subscribe your calendar**: Settings has a one-tap `webcal://` link that opens the Apple Calendar subscribe dialog. From then on your phone and laptop calendars stay in sync automatically.
4. Optionally install the **browser extension** (see below) and point it at the URL above.

## Browser extension

A Manifest V3 Chromium extension lives in `extension-experimental/`. It's a thin client over the same server — the toolbar icon opens a side panel with full website parity (Today / Month / Classes, add/edit tasks, syllabus upload, settings). Load it unpacked via `chrome://extensions` → *Load unpacked* → pick `extension-experimental/`, then set the Compass URL in its options (defaults to `http://localhost:8000`; set it to the live link to use prod).

---

## Local development

Compass is one FastAPI app (`main.py`) that runs the same on every OS. It uses SQLite locally and Postgres in production, picked up from `DATABASE_URL` at startup.

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt   # Windows: .venv\Scripts\pip
```

**Run the server:**

- **macOS / Linux:** `./compass-ctl.sh start|stop|restart|status` (runs uvicorn detached, logs to `compass.log`), or use the `Compass.app` bundle for a Finder-clickable tray.
- **Windows:** double-click the **Compass** desktop shortcut (tray app), or `pythonw compass_tray.py`.
- **Anywhere, foreground:** `uvicorn main:app --host 0.0.0.0 --port 8000 --no-access-log`

> The server runs **without `--reload`** — restart it after code changes or your edits won't be live.

Then open `http://localhost:8000` (or `http://<your-lan-ip>:8000` from your phone on the same WiFi).

**Local secrets** (all gitignored): drop these files in the project root and `main.py` loads them into the matching env vars at startup *iff* the env var is unset:

| File | Env var | Purpose |
|---|---|---|
| `.compass_secret_key` | `COMPASS_SECRET_KEY` | Signs session cookies (stable so logins survive restarts) |
| `.xai_key` | `XAI_API_KEY` | Shared Grok key powering the free-parse pool |
| `.xai_model` | `XAI_MODEL` | Grok model override (default `grok-4-fast-reasoning`) |
| `.admin_emails` | `ADMIN_EMAILS` | Comma-separated admin allowlist for `/admin` |

**Tests:**

```bash
.venv/bin/python -m pytest tests/                   # fast in-process suite (~60s, gated in CI)
.venv/bin/python -m pytest tests/ tests_browser/    # full suite incl. real Chromium via Playwright
```

## Configuration (production env vars)

| Var | Required | Notes |
|---|---|---|
| `COMPASS_ENV=production` | ✅ | Enables `Secure` cookies; refuses to start without a secret key |
| `COMPASS_SECRET_KEY` | ✅ | 32+ random bytes. Rotating it logs everyone out. |
| `DATABASE_URL` | ✅ (prod) | `postgres://…` — auto-rewritten for psycopg3; Neon-pooler-safe |
| `XAI_API_KEY` | recommended | Shared key for the free-parse pool. Unset = keyless users must bring their own key. |
| `FREE_PARSE_LIMIT` | optional | Free parses per account on the shared key (default 5) |
| `ADMIN_EMAILS` | optional | Comma-separated emails that can reach `/admin` and grant uncapped parsing |
| `STORAGE_BACKEND=s3` + `STORAGE_*` | ✅ (prod) | Heroku's filesystem is ephemeral — use S3/R2 for uploads |

### Parse entitlement model

There is **no BYO-key requirement**. Keyless accounts use the shared `XAI_API_KEY` up to `FREE_PARSE_LIMIT`; adding your own key removes the cap (your quota); an admin can grant a specific account unlimited parsing on the shared key from the `/admin` dashboard (gated by the `ADMIN_EMAILS` allowlist — non-admins get a 404, not a 403). Set a hard spend cap in the xAI console as the real backstop.

## Deployment & CI/CD

Pushing to GitHub deploys itself:

```
git push origin main
  → GitHub Actions runs pytest tests/        (every push)
  → if it passes on main → auto-deploy to the STAGING Heroku app
  → Heroku runs `python migrate.py` in the release phase, then goes live
```

- **Staging** (`dannibar-compass-staging`) is auto-deployed on every green push to `main`.
- **Production** (`dannibar-compass`, the link at the top) is **never auto-deployed** — it's promoted deliberately once staging looks good.
- `migrate.py` runs as Heroku's **release phase**: it auto-applies *additive* schema changes (new columns) to the database, and a failed migration **aborts the release** so a broken build can't reach users.
- ⚠️ **Non-additive schema changes** (type changes, drops, renames, constraint edits) are *not* automated — apply them to the target database by hand **before** the deploy that needs them.

Workflow lives in `.github/workflows/ci.yml`; deploy auth is the `HEROKU_API_KEY` GitHub Actions secret.

## Architecture

`main.py` is a single ~1700-line FastAPI app: every SQLModel table, the startup migration, every route, and the syllabus-parsing pipeline. It is intentionally *not* split into a package (`compass_tray.py` and the Heroku `Procfile` import `main:app` directly). For the full architecture — auth, per-user data scoping, recurrence/alerts, the iCal feed, the storage abstraction, the extension contract — see **[CLAUDE.md](CLAUDE.md)**, which is kept current with every architecture-level change.
