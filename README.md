# StudyFlow

Personal school task tracker that:

- Syncs your Canvas calendar + your Apple Calendar + StudyFlow's own exam-date calendar into one overlaid Apple Calendar view.
- Auto-extracts late-grade policy, grading breakdown, office hours, and exam dates from syllabus PDFs (local LLM via Ollama — no API key, no cloud).
- Holds a curated list of "important" docs per class so you don't have to dig through Canvas.

Designed for one user, runs locally on a Windows laptop, accessed from your iPhone over home WiFi.

## Setup

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

You'll also need [Ollama](https://ollama.com) and the model:

```powershell
winget install Ollama.Ollama
ollama pull qwen2.5:7b
```

## Run

```powershell
uvicorn main:app --host 0.0.0.0 --port 8000 --no-reload
```

Then visit `http://localhost:8000` on your laptop, or `http://<laptop-local-ip>:8000` on your iPhone (same WiFi).

## Architecture

See `~/.gstack/projects/StudyFlow/danni-main-design-*.md` for the full design doc.
