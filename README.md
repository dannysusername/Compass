# Compass

A school task tracker that:

- Syncs your Canvas calendar + your Apple Calendar + Compass's own exam-date calendar into one overlaid Apple Calendar view.
- Auto-extracts late-grade policy, grading breakdown, office hours, and exam dates from syllabus PDFs (xAI Grok API — grok-4).
- Holds a curated list of "important" docs per class so you don't have to dig through Canvas.

Multi-user: each account has its own classes, tasks, and syllabi.

## Setup

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### xAI API key

Each user supplies their own xAI key after signing up — visit `/settings`
and paste it in. Compass calls Grok against your account, not anyone
else's. Get a key from https://console.x.ai/.

### Model selection (optional)

Default model is `grok-4-fast-reasoning` — fast enough for the live picker (~10–30s per syllabus, ~5–10s per summary card). To override:

- **Per-shell:** `$env:XAI_MODEL = "grok-4-latest"`
- **File (the launcher loads it on Restart):** `"grok-4-latest" | Out-File -Encoding ascii -NoNewline .xai_model`

Common picks:
- `grok-4-fast-reasoning` — current default. Best speed/quality balance.
- `grok-4-fast-non-reasoning` — fastest, slightly less accurate on tricky syllabi.
- `grok-4-latest` — slowest, deepest reasoning. Use if `fast-reasoning` is splitting headings or dropping table markers.

## Run

Either double-click the **Compass** desktop shortcut, or from a terminal:

```powershell
uvicorn main:app --host 0.0.0.0 --port 8000 --no-reload
```

Then visit `http://localhost:8000` on your laptop, or `http://<laptop-local-ip>:8000` on your iPhone (same WiFi).

## Auth

Compass uses email + password. Sign up at `/signup`; sessions are signed cookies.

In **development**, sessions are signed with an ephemeral key generated at startup — they reset on every restart. Fine locally.

In **production**, set both:

- `COMPASS_ENV=production` — enables `Secure` cookies and rejects startup if no key is set.
- `COMPASS_SECRET_KEY=<32+ random bytes>` — used to sign session cookies. Generate via `python -c "import secrets; print(secrets.token_urlsafe(32))"`. Rotating this key logs everyone out.

For a stable local dev key (so sessions survive restarts), write the key to `.compass_secret_key` (gitignored, picked up by the desktop launcher).

## Cost

- See https://docs.x.ai/docs/models for current Grok pricing.
- A typical syllabus parse runs ~10K input + ~2K output tokens, so a few syllabi per semester is well under a dollar at current rates.

## Deploy (Heroku)

The app is wired for Heroku out of the box: `Procfile`, `runtime.txt`,
Postgres-aware DB engine, and a storage abstraction that uses
S3-compatible object storage (Cloudflare R2 recommended) instead of the
ephemeral filesystem.

### One-time setup

```bash
heroku create compass-<your-handle>
heroku addons:create heroku-postgresql:mini   # ~$5/mo
heroku ps:type web=basic                       # ~$7/mo, no sleep

# Cookie-signing secret (rotate to log everyone out)
heroku config:set COMPASS_SECRET_KEY="$(python -c 'import secrets;print(secrets.token_urlsafe(32))')"
heroku config:set COMPASS_ENV=production

# Object storage — Cloudflare R2 example (free egress)
heroku config:set STORAGE_BACKEND=s3
heroku config:set STORAGE_BUCKET=compass-uploads
heroku config:set STORAGE_ENDPOINT_URL=https://<account_id>.r2.cloudflarestorage.com
heroku config:set STORAGE_ACCESS_KEY_ID=...
heroku config:set STORAGE_SECRET_ACCESS_KEY=...
heroku config:set STORAGE_REGION=auto

git push heroku main
```

### Per-user xAI keys

Each user must paste their own `xai-...` key on `/settings` before
uploading a syllabus. There is no shared server key — every parse is
billed to the user's own xAI account.

## Architecture

See `~/.gstack/projects/Compass/danni-main-design-*.md` for the full design doc.
