# StudyFlow

Personal school task tracker that:

- Syncs your Canvas calendar + your Apple Calendar + StudyFlow's own exam-date calendar into one overlaid Apple Calendar view.
- Auto-extracts late-grade policy, grading breakdown, office hours, and exam dates from syllabus PDFs (Claude API — Sonnet 4.6).
- Holds a curated list of "important" docs per class so you don't have to dig through Canvas.

Designed for one user, runs locally on a Windows laptop, accessed from your iPhone over home WiFi.

## Setup

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### Anthropic API key

Get a key from https://console.anthropic.com/settings/keys, then either:

**Option A — file (recommended; the desktop launcher loads it automatically):**

```powershell
"sk-ant-...your-key..." | Out-File -Encoding ascii -NoNewline .anthropic_key
```

**Option B — env var (per-shell):**

```powershell
$env:ANTHROPIC_API_KEY = "sk-ant-...your-key..."
```

`.anthropic_key` is gitignored.

## Run

Either double-click the **StudyFlow** desktop shortcut, or from a terminal:

```powershell
uvicorn main:app --host 0.0.0.0 --port 8000 --no-reload
```

Then visit `http://localhost:8000` on your laptop, or `http://<laptop-local-ip>:8000` on your iPhone (same WiFi).

## Auth (optional)

Set `STUDYFLOW_TOKEN` env var (or write to `.studyflow_token`) to gate mutating routes against random devices on your home WiFi. Without it, dev mode = no auth.

## Cost

- Sonnet 4.6 input: $3 / 1M tokens, output: $15 / 1M tokens.
- Typical syllabus parse: ~10K tokens input + ~2K output ≈ **$0.06 per syllabus**, dropping to ~$0.005 with prompt caching after the first call.
- ~5 syllabi per semester ≈ **$0.30 / semester** total.

## Architecture

See `~/.gstack/projects/StudyFlow/danni-main-design-*.md` for the full design doc.
