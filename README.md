# StudyFlow

Personal school task tracker that:

- Syncs your Canvas calendar + your Apple Calendar + StudyFlow's own exam-date calendar into one overlaid Apple Calendar view.
- Auto-extracts late-grade policy, grading breakdown, office hours, and exam dates from syllabus PDFs (xAI Grok API — grok-4).
- Holds a curated list of "important" docs per class so you don't have to dig through Canvas.

Designed for one user, runs locally on a Windows laptop, accessed from your iPhone over home WiFi.

## Setup

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### xAI API key

Get a key from https://console.x.ai/, then either:

**Option A — file (recommended; the desktop launcher loads it automatically):**

```powershell
"xai-...your-key..." | Out-File -Encoding ascii -NoNewline .xai_key
```

**Option B — env var (per-shell):**

```powershell
$env:XAI_API_KEY = "xai-...your-key..."
```

`.xai_key` is gitignored.

### Model selection (optional)

Default model is `grok-4-fast-reasoning` — fast enough for the live picker (~10–30s per syllabus, ~5–10s per summary card). To override:

- **Per-shell:** `$env:XAI_MODEL = "grok-4-latest"`
- **File (the launcher loads it on Restart):** `"grok-4-latest" | Out-File -Encoding ascii -NoNewline .xai_model`

Common picks:
- `grok-4-fast-reasoning` — current default. Best speed/quality balance.
- `grok-4-fast-non-reasoning` — fastest, slightly less accurate on tricky syllabi.
- `grok-4-latest` — slowest, deepest reasoning. Use if `fast-reasoning` is splitting headings or dropping table markers.

## Run

Either double-click the **StudyFlow** desktop shortcut, or from a terminal:

```powershell
uvicorn main:app --host 0.0.0.0 --port 8000 --no-reload
```

Then visit `http://localhost:8000` on your laptop, or `http://<laptop-local-ip>:8000` on your iPhone (same WiFi).

## Auth (optional)

Set `STUDYFLOW_TOKEN` env var (or write to `.studyflow_token`) to gate mutating routes against random devices on your home WiFi. Without it, dev mode = no auth.

## Cost

- See https://docs.x.ai/docs/models for current Grok pricing.
- A typical syllabus parse runs ~10K input + ~2K output tokens, so a few syllabi per semester is well under a dollar at current rates.

## Architecture

See `~/.gstack/projects/StudyFlow/danni-main-design-*.md` for the full design doc.
