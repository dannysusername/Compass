from contextlib import asynccontextmanager
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Optional, List
from zoneinfo import ZoneInfo
import json
import logging
import os
import secrets
import uuid

from fastapi import (
    Depends, FastAPI, BackgroundTasks, File, Form, HTTPException,
    Request, UploadFile,
)
from fastapi.responses import (
    FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlmodel import (
    SQLModel, Field, Relationship, Session, create_engine, select, delete,
)
from icalendar import Calendar, Event as ICalEvent
from pydantic import BaseModel, Field as PydanticField
from typing import Literal
import grpc
from xai_sdk import Client as XAIClient
from xai_sdk.chat import system as xai_system, user as xai_user
import pdfplumber


# ---- Config ----

LOCAL_TZ = ZoneInfo("America/New_York")  # change to your timezone
XAI_MODEL = os.environ.get("XAI_MODEL", "grok-4-fast-reasoning").strip()
MAX_UPLOAD_MB = 25
COMPASS_TOKEN = os.environ.get("COMPASS_TOKEN", "").strip()  # empty = dev mode (no auth)
COOKIE_NAME = "compass_token"


# Logger that writes to the same compass.log the tray launcher uses.
# Hand-configured (not basicConfig) so it works whether main.py is the entry
# point or runs as a uvicorn subprocess with stdout/stderr suppressed.
log = logging.getLogger("compass")
if not log.handlers:
    log.setLevel(logging.INFO)
    _h = logging.FileHandler(Path(__file__).parent / "compass.log", encoding="utf-8")
    _h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    log.addHandler(_h)
    log.propagate = False


# ---- Database models ----

class Class(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    code: str
    syllabi: List["Syllabus"] = Relationship(
        back_populates="cls",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )
    events: List["CalendarEvent"] = Relationship(
        back_populates="cls",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )
    documents: List["Document"] = Relationship(
        back_populates="cls",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )
    tasks: List["Task"] = Relationship(
        back_populates="cls",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )


class Syllabus(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    class_id: int = Field(foreign_key="class.id")
    filename: str
    raw_text: str
    parsed_at: datetime
    outline_json: Optional[str] = Field(default=None)  # cached Pass-1.5 outline
    cls: Optional[Class] = Relationship(back_populates="syllabi")


class CalendarEvent(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    class_id: int = Field(foreign_key="class.id")
    class_code: str
    title: str
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    kind: str  # free-form lowercase noun (quiz, lab, lecture, exam, ...)
    actionable: bool = Field(default=True)  # False = context (lecture topic, holiday)
    position: int = Field(default=0)  # drag-to-reorder priority, shared with Task
    source_text: Optional[str] = Field(default=None)
    completed_at: Optional[datetime] = Field(default=None)
    cls: Optional[Class] = Relationship(back_populates="events")


class Task(SQLModel, table=True):
    """User-typed to-do item attached to a class. Sits alongside CalendarEvent
    on the today/week views — both can be marked done with the circular
    button. Tasks are entirely manual; no AI/syllabus auto-generation."""
    id: Optional[int] = Field(default=None, primary_key=True)
    class_id: int = Field(foreign_key="class.id")
    title: str
    starts_at: Optional[datetime] = None  # range start; None = single-date task
    due_at: Optional[datetime] = None     # range end / deadline
    completed_at: Optional[datetime] = None
    position: int = Field(default=0)  # drag-to-reorder priority
    created_at: datetime
    tag_id: Optional[int] = Field(default=None, foreign_key="tag.id")
    cls: Optional[Class] = Relationship(back_populates="tasks")
    tag: Optional["Tag"] = Relationship(back_populates="tasks")


class Tag(SQLModel, table=True):
    """User-defined category (e.g. 'Reading', 'Lab') applied to tasks.
    Free-form name + free-form hex color. Globally scoped (not per-class).

    `is_system=True` marks tags seeded by the app to mirror Grok-generated
    event kinds. Users can rename and recolor system tags but can't
    delete them.

    `system_key` is an immutable canonical slug (matches the original
    `CalendarEvent.kind` string). Events are linked to their system tag
    by this key — so renaming a system tag's user-facing `name` doesn't
    sever the link to all the events with that kind."""
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    color: str  # hex string like #a83232
    is_system: bool = Field(default=False)
    system_key: Optional[str] = Field(default=None, index=True)
    tasks: List["Task"] = Relationship(back_populates="tag")


# Names + colors mirror the event sub-kinds so a user task tagged
# "milestone" looks identical to an event with kind="milestone". Each
# kind gets its own muted, paper-aesthetic color so users can tell them
# apart at a glance.
SYSTEM_TAGS = [
    ("exam",       "#a04528"),  # rust — urgent
    ("assignment", "#2c5f7c"),  # deep teal
    ("project",    "#7b3f61"),  # mauve
    ("milestone",  "#9e7b2c"),  # ochre
]

# Palette for auto-assigning colors to brand-new kinds Grok returns
# (quiz, lab, lecture, holiday, ...). Hash the kind name → palette index
# so the same kind always gets the same color across runs.
TAG_PALETTE = [
    "#a04528",  # rust
    "#2c5f7c",  # deep teal
    "#7b3f61",  # mauve
    "#9e7b2c",  # ochre
    "#5c8a3a",  # forest green
    "#506b87",  # slate blue
    "#8a4f7a",  # plum
    "#6e6b35",  # olive
    "#3a6b6e",  # pine
    "#a85f3a",  # terracotta
    "#4d6b4f",  # sage
    "#7a5b8c",  # iris
]


def _pick_tag_color(kind: str) -> str:
    """Deterministic palette pick so re-extraction never changes a kind's color."""
    h = sum(ord(c) for c in kind.lower()) if kind else 0
    return TAG_PALETTE[h % len(TAG_PALETTE)]


def _ensure_system_tag(session: "Session", kind: str) -> None:
    """Make sure a system tag exists for `kind`. Idempotent. Re-uses an
    existing tag (system or user) by name and backfills `system_key` so
    the collector can resolve color/name for events with this kind."""
    if not kind:
        return
    kind = kind.lower().strip()
    if not kind:
        return
    existing = session.exec(
        select(Tag).where(
            (Tag.system_key == kind) | (Tag.name == kind)
        )
    ).first()
    if existing is None:
        seeded = dict(SYSTEM_TAGS)
        color = seeded.get(kind) or _pick_tag_color(kind)
        session.add(Tag(name=kind, color=color, is_system=True, system_key=kind))
        return
    changed = False
    if not existing.is_system:
        existing.is_system = True
        changed = True
    if not existing.system_key:
        existing.system_key = kind
        changed = True
    if changed:
        session.add(existing)


def _seed_system_tags() -> None:
    """Insert system tags on startup if they don't already exist. Once
    a row exists, we leave its name alone so user edits stick. We:
      • force-set `is_system`,
      • backfill `system_key` (for rows created before that column existed),
      • lowercase any uppercase hex color (HTML5 `<input type="color">`
        rejects uppercase, falling back to a default — which made the
        manage-tags color picker show the wrong swatch)."""
    with Session(engine) as session:
        # Heal uppercase hex on every row, system or not — this is a
        # general data-quality fix, not a system-tag-specific one.
        for tag in session.exec(select(Tag)).all():
            if tag.color and tag.color != tag.color.lower():
                tag.color = tag.color.lower()
                session.add(tag)
        for name, color in SYSTEM_TAGS:
            existing = session.exec(
                select(Tag).where(
                    (Tag.system_key == name) | (Tag.name == name)
                )
            ).first()
            if existing is None:
                session.add(Tag(
                    name=name, color=color, is_system=True, system_key=name,
                ))
            else:
                existing.is_system = True
                if not existing.system_key:
                    existing.system_key = name
                session.add(existing)
        session.commit()


class Document(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    class_id: int = Field(foreign_key="class.id")
    title: str
    filename: str
    uploaded_at: datetime
    cls: Optional[Class] = Relationship(back_populates="documents")


# ---- App setup ----

DB_PATH = Path(__file__).parent / "compass.db"
UPLOAD_DIR = Path(__file__).parent / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

engine = create_engine(
    f"sqlite:///{DB_PATH}",
    connect_args={"check_same_thread": False},
)


def _add_column_if_missing(conn, table: str, column: str, ddl: str) -> None:
    """Add a column to an existing SQLite table if it isn't already there.
    SQLModel.create_all only creates missing tables, not missing columns."""
    rows = conn.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
    existing = {r[1] for r in rows}  # row[1] = column name
    if column not in existing:
        conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    SQLModel.metadata.create_all(engine)
    with engine.begin() as conn:
        _add_column_if_missing(conn, "policy", "source_text", "TEXT")
        _add_column_if_missing(conn, "calendarevent", "source_text", "TEXT")
        _add_column_if_missing(conn, "syllabus", "outline_json", "TEXT")
        _add_column_if_missing(conn, "calendarevent", "completed_at", "TIMESTAMP")
        _add_column_if_missing(conn, "calendarevent", "actionable", "INTEGER NOT NULL DEFAULT 1")
        _add_column_if_missing(conn, "calendarevent", "position", "INTEGER NOT NULL DEFAULT 0")
        _add_column_if_missing(conn, "task", "position", "INTEGER NOT NULL DEFAULT 0")
        _add_column_if_missing(conn, "task", "tag_id", "INTEGER")
        _add_column_if_missing(conn, "task", "starts_at", "TIMESTAMP")
        _add_column_if_missing(conn, "tag", "is_system", "INTEGER NOT NULL DEFAULT 0")
        _add_column_if_missing(conn, "tag", "system_key", "TEXT")
    _seed_system_tags()
    yield


app = FastAPI(title="Compass", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


def _static_v(path: str) -> str:
    """Cache-busting version stamp for a static file. Appends `?v=<mtime>` so
    Brave/Chrome reload the asset whenever it's changed on disk — keeps users
    from seeing stale JS/CSS after we ship a fix."""
    try:
        full = Path(__file__).parent / "static" / path.lstrip("/")
        return f"{path}?v={int(full.stat().st_mtime)}"
    except OSError:
        return path


templates.env.filters["static_v"] = _static_v


# ---- Parse-job status (in-memory; resets on restart) ----

parse_jobs: dict[int, str] = {}  # syllabus_id -> "pending"|"running"|"done"|"error: ..."

# Outline extraction (Pass 1.5) status, separate from upload-time parse_jobs so
# users can re-trigger outline extraction without conflicting with the
# upload-time events extraction. syllabus_id -> "running"|"done"|"error: ..."


# ---- Auth ----

def _token_matches(candidate: str) -> bool:
    if not candidate or not COMPASS_TOKEN:
        return False
    return secrets.compare_digest(candidate, COMPASS_TOKEN)


def require_token(request: Request) -> None:
    """Dependency: enforce auth on mutating routes if COMPASS_TOKEN is set."""
    if not COMPASS_TOKEN:
        return  # dev mode
    header = request.headers.get("x-compass-token", "")
    if _token_matches(header):
        return
    cookie = request.cookies.get(COOKIE_NAME, "")
    if _token_matches(cookie):
        return
    qp = request.query_params.get("token", "")
    if _token_matches(qp):
        return
    raise HTTPException(401, "Missing or invalid token. Set X-Compass-Token header or visit /setup-token in a browser.")


def has_valid_cookie(request: Request) -> bool:
    if not COMPASS_TOKEN:
        return True
    return _token_matches(request.cookies.get(COOKIE_NAME, ""))


# ---- Helpers ----

def safe_filename(name: str) -> str:
    """Strip path components to prevent traversal. Keep only the basename."""
    return os.path.basename(name).replace("\\", "_").replace("/", "_") or "file"


def validate_upload(content: bytes, max_mb: int = MAX_UPLOAD_MB) -> None:
    if len(content) > max_mb * 1024 * 1024:
        raise HTTPException(400, f"File too large (max {max_mb}MB)")


def validate_pdf(content: bytes) -> None:
    validate_upload(content)
    if not content.startswith(b"%PDF"):
        raise HTTPException(400, "Not a valid PDF (bad magic bytes)")


def event_sort_key(e) -> tuple:
    """None last, then naive comparison (SQLite drops tz on roundtrip)."""
    dt = e.starts_at
    if dt is None:
        return (1, datetime.max)
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return (0, dt)


def parse_iso_dt(value) -> Optional[datetime]:
    """Parse an ISO datetime string, attaching LOCAL_TZ if naive. Returns None on failure.
    Tolerates the `YYYY-MM-DDTHH:MM` form emitted by HTML <input type=datetime-local>
    on Python <3.11 (whose fromisoformat is strict about seconds)."""
    if not value or not isinstance(value, str):
        return None
    try:
        cleaned = value.strip().replace("Z", "+00:00")
        if _re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$", cleaned):
            cleaned += ":00"
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=LOCAL_TZ)
        return dt
    except (ValueError, TypeError):
        return None


def extract_pdf_text(path: Path) -> str:
    """Pull plain text out of a PDF. dedupe_chars handles 'fake bold'
    double-stamped glyphs that show up in many professor-authored syllabi
    (cells like 'CCIISS33995500' come out as 'CIS 3950').

    No table detection — pdfplumber's plain extract_text() keeps schedule
    grids inline as readable rows, which is what Grok needs to find every
    quiz/lecture/deadline. The previous table-detection path stripped grid
    cells out of the prose entirely, hiding most of the schedule from
    extraction."""
    chunks: list[str] = []
    with pdfplumber.open(path) as pdf:
        total = len(pdf.pages)
        for page in pdf.pages:
            try:
                source = page.dedupe_chars()
            except Exception:
                source = page
            try:
                text = source.extract_text() or ""
            except Exception:
                text = page.extract_text() or ""
            page_text = text.strip()
            if page_text:
                chunks.append(page_text)
    log.info(
        "extract_pdf_text: %d of %d pages had text (%s)",
        len(chunks), total, path.name,
    )
    return "\n\n".join(chunks)


SYLLABUS_SYSTEM_PROMPT = """You are an extractor for university course syllabi. The student needs full visibility into what's happening when, so they don't miss a deadline or walk into a lecture unprepared. Be inclusive, not conservative.

INPUT: the full text of a syllabus.

OUTPUT: a single JSON object matching the enforced schema. No preamble, commentary, or markdown.

# course_code
UPPERCASE with one space between department prefix and number ("math 250" → "MATH 250", "CIS3950" → "CIS 3950"). Strip section/lab modifiers. Null if absent.

# course_name
Full course title as listed in the header. Don't include the course code, section, or term. Prefer the expanded title over a short one.

# events
EVERY dated item the student would put on a calendar — actionable AND contextual. Each: {title, kind, actionable, starts_at, ends_at, source_text}.

CAST A WIDE NET. Capture:
- Submission deadlines: assignments, problem sets, papers, projects, lab reports, drafts, peer reviews, anything turned in.
- Assessments: quizzes, exams, midterms, finals, oral exams, presentations.
- Class meetings with topics or readings: lectures, discussion sections, labs, recitations, guest speakers.
- Course logistics: drop/add deadlines, withdrawal deadlines, registration deadlines, holidays / no-class days.
- One-off events: field trips, reviews, office-hours specials.

If the syllabus lists each instance of a recurring item with its own date (Quiz 1 = Sep 10, Quiz 2 = Sep 17, …), emit ONE EVENT PER INSTANCE. Don't summarize them into one entry.

# kind
Lowercase noun describing what type it is, taken from the syllabus's own language. Examples: `quiz`, `exam`, `midterm`, `final`, `assignment`, `problem set`, `lab`, `project`, `paper`, `presentation`, `reading`, `lecture`, `discussion`, `recitation`, `holiday`, `deadline`. Use whatever fits — new kinds get auto-tagged after extraction. Prefer specific over generic (`quiz` over `assignment`).

# actionable
Boolean. `true` if the student has to do something on/by this date (submit, take an exam, give a presentation, drop a class, attend a special session). `false` if it's pure context — lecture topics with no associated submission, holidays, no-class days, optional study sessions, things that just inform the schedule. When uncertain, prefer `true` (better to surface than hide).

# starts_at
Naive ISO 8601 ("2026-09-15T18:00:00"). Date-only → 23:59:00 of that day for deadlines, 09:00:00 for class meetings without a stated time. DO NOT INVENT DATES. "Week 5 — Midterm" with no explicit date → starts_at: null, descriptor goes in title.

# ends_at
Only when the syllabus gives an explicit duration ("Final Exam: Dec 14, 8-10am" → starts_at 08:00, ends_at 10:00). Else null.

# source_text
Short verbatim quote (≤ 240 chars) showing where this came from. Null if derived.

Ignore syllabus-stated time zones; emit naive datetimes. When uncertain about a date, prefer null over inventing.

# Example

INPUT (excerpted): "CS 101 - Intro to Programming. Spring 2026. Sep 8: Lecture — Variables. Sep 10: Quiz 1 (in class, 15min). Sep 15: Lecture — Control flow. Reading: ch.3. Sep 17: Quiz 2. Oct 5: Problem Set 1 due 11:59pm. Nov 1: No class (Fall break). Midterm Exam: March 15, 2026 at 6:00pm. Final Project Due: May 7, 2026."

OUTPUT:
{
  "course_code": "CS 101",
  "course_name": "Intro to Programming",
  "events": [
    {"title": "Lecture: Variables", "kind": "lecture", "actionable": false, "starts_at": "2026-09-08T09:00:00", "ends_at": null, "source_text": "Sep 8: Lecture — Variables"},
    {"title": "Quiz 1", "kind": "quiz", "actionable": true, "starts_at": "2026-09-10T09:00:00", "ends_at": null, "source_text": "Sep 10: Quiz 1 (in class, 15min)"},
    {"title": "Lecture: Control flow (read ch.3)", "kind": "lecture", "actionable": false, "starts_at": "2026-09-15T09:00:00", "ends_at": null, "source_text": "Sep 15: Lecture — Control flow. Reading: ch.3"},
    {"title": "Quiz 2", "kind": "quiz", "actionable": true, "starts_at": "2026-09-17T09:00:00", "ends_at": null, "source_text": "Sep 17: Quiz 2"},
    {"title": "Problem Set 1 due", "kind": "problem set", "actionable": true, "starts_at": "2026-10-05T23:59:00", "ends_at": null, "source_text": "Oct 5: Problem Set 1 due 11:59pm"},
    {"title": "No class (Fall break)", "kind": "holiday", "actionable": false, "starts_at": "2026-11-01T09:00:00", "ends_at": null, "source_text": "Nov 1: No class (Fall break)"},
    {"title": "Midterm Exam", "kind": "midterm", "actionable": true, "starts_at": "2026-03-15T18:00:00", "ends_at": null, "source_text": "Midterm Exam: March 15, 2026 at 6:00pm"},
    {"title": "Final Project Due", "kind": "project", "actionable": true, "starts_at": "2026-05-07T23:59:00", "ends_at": null, "source_text": "Final Project Due: May 7, 2026"}
  ]
}"""


# Pydantic models for upload-time extraction. Now narrow: only course identity
# and dated events (the calendar feed needs structured dates). Everything else
# the student cares about is captured per-passage via the highlight UI.

class EventItem(BaseModel):
    title: str
    # Free-form so Grok can return whatever the syllabus says (quiz, lab,
    # problem set, reading, lecture, etc.). New kinds get auto-tagged
    # post-extraction in process_syllabus.
    kind: str
    actionable: bool = True  # False = context-only (lecture topic, holiday)
    starts_at: Optional[str] = None
    ends_at: Optional[str] = None
    source_text: Optional[str] = None


class SyllabusExtraction(BaseModel):
    course_code: Optional[str] = None
    course_name: Optional[str] = None
    events: List[EventItem] = PydanticField(default_factory=list)


def parse_syllabus_with_grok(text: str) -> dict:
    """Call xAI Grok (grok-4-latest by default) via the native xai-sdk with
    structured-output enforcement. Raises grpc.RpcError on API failures; caller logs."""
    api_key = os.environ.get("XAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "XAI_API_KEY is not set. Put your key in .xai_key next to "
            "main.py, or export it before launching."
        )
    client = XAIClient(api_key=api_key)
    chat = client.chat.create(
        model=XAI_MODEL,
        messages=[xai_system(SYLLABUS_SYSTEM_PROMPT)],
    )
    chat.append(xai_user(text))
    response, parsed = chat.parse(SyllabusExtraction)
    if parsed is None:
        raise RuntimeError("Grok returned no structured content")
    return parsed.model_dump()


def _grpc_error_message(e: "grpc.RpcError") -> str:
    """Translate a gRPC error into a user-readable status string."""
    code = e.code() if hasattr(e, "code") else None
    details = e.details() if hasattr(e, "details") else str(e)
    if code == grpc.StatusCode.UNAUTHENTICATED:
        return "XAI_API_KEY is invalid. Check your key at https://console.x.ai/"
    if code == grpc.StatusCode.PERMISSION_DENIED:
        return f"permission denied by xAI API. {details[:200]}"
    if code == grpc.StatusCode.RESOURCE_EXHAUSTED:
        return f"rate limited by xAI API. {details[:200]}"
    if code == grpc.StatusCode.UNAVAILABLE:
        return "could not reach the xAI API. Check your internet connection."
    if code == grpc.StatusCode.DEADLINE_EXCEEDED:
        return "xAI API request timed out."
    if code == grpc.StatusCode.INVALID_ARGUMENT:
        return f"bad request to Grok. {details[:300]}"
    return f"xAI API call failed ({code}). {details[:200]}"


def process_syllabus(syllabus_id: int) -> None:
    """Background task: extract calendar events from the syllabus via Grok,
    then write them to the DB. Outline/section parsing was removed — the
    PDF viewer is the user's primary way to read syllabus content now."""
    parse_jobs[syllabus_id] = "running"
    try:
        with Session(engine) as session:
            syllabus = session.get(Syllabus, syllabus_id)
            if not syllabus:
                parse_jobs[syllabus_id] = "error: syllabus not found"
                return
            raw_text = syllabus.raw_text

        try:
            data = parse_syllabus_with_grok(raw_text)
        except grpc.RpcError as e:
            parse_jobs[syllabus_id] = f"error: {_grpc_error_message(e)}"
            return
        except json.JSONDecodeError as e:
            parse_jobs[syllabus_id] = f"error: Grok output was not valid JSON. {str(e)[:200]}"
            return
        except RuntimeError as e:
            parse_jobs[syllabus_id] = f"error: {e}"
            return

        with Session(engine) as session:
            syllabus = session.get(Syllabus, syllabus_id)
            if not syllabus:
                parse_jobs[syllabus_id] = "error: syllabus deleted during processing"
                return
            cls = session.get(Class, syllabus.class_id)
            if data.get("course_code"):
                cls.code = str(data["course_code"]).upper().strip()
            if data.get("course_name"):
                cls.name = str(data["course_name"]).strip()

            # Wipe prior auto-extracted events — re-upload replaces, doesn't accumulate.
            session.exec(delete(CalendarEvent).where(CalendarEvent.class_id == cls.id))
            session.flush()

            # First pass: auto-create system tags for any new kinds Grok
            # returned (quiz, lab, holiday, ...) so the collector can resolve
            # color + name for them right away.
            kinds_seen: set[str] = set()
            for ev in data.get("events", []) or []:
                if not isinstance(ev, dict):
                    continue
                k = str(ev.get("kind") or "milestone").lower().strip() or "milestone"
                kinds_seen.add(k)
            for k in kinds_seen:
                _ensure_system_tag(session, k)
            session.flush()

            for ev in data.get("events", []) or []:
                if not isinstance(ev, dict):
                    continue
                kind = str(ev.get("kind") or "milestone").lower().strip() or "milestone"
                actionable = ev.get("actionable")
                # Default to actionable when the model omits the field — better
                # to surface an item than hide it.
                if not isinstance(actionable, bool):
                    actionable = True
                session.add(CalendarEvent(
                    class_id=cls.id,
                    class_code=cls.code,
                    title=str(ev.get("title") or "Untitled"),
                    starts_at=parse_iso_dt(ev.get("starts_at")),
                    ends_at=parse_iso_dt(ev.get("ends_at")),
                    kind=kind,
                    actionable=actionable,
                    source_text=ev.get("source_text") or None,
                ))

            session.commit()
        parse_jobs[syllabus_id] = "done"
    except Exception as e:
        msg = f"{type(e).__name__}: {str(e)[:300]}"
        parse_jobs[syllabus_id] = f"error: {msg}"


# ---- Routes: Home & class CRUD ----

@app.get("/setup-token", response_class=HTMLResponse)
def setup_token_page(request: Request):
    return templates.TemplateResponse(request, "setup_token.html", {
        "auth_required": bool(COMPASS_TOKEN),
        "already_set": has_valid_cookie(request),
    })


@app.post("/setup-token")
def setup_token_submit(token: str = Form(...)):
    if COMPASS_TOKEN and not _token_matches(token.strip()):
        raise HTTPException(401, "Wrong token")
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie(
        COOKIE_NAME,
        token.strip() or "dev",
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 365,
    )
    return resp


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    if COMPASS_TOKEN and not has_valid_cookie(request):
        return RedirectResponse("/setup-token", status_code=303)
    today_start = _today_local()
    today_end = today_start + timedelta(days=1)
    today_items = _collect_items_in_range(today_start, today_end)
    overdue = _collect_overdue()
    with Session(engine, expire_on_commit=False) as session:
        classes = session.exec(select(Class).order_by(Class.code)).all()
        all_tags = session.exec(select(Tag).order_by(Tag.name)).all()
    return templates.TemplateResponse(request, "home.html", {
        "classes": classes,
        "today": today_start,
        "today_items": today_items,
        "overdue": overdue,
        "default_class_id": (classes[0].id if classes else None),
        "all_tags": all_tags,
    })


@app.post("/classes", dependencies=[Depends(require_token)])
def add_class(name: str = Form(...), code: str = Form(...)):
    with Session(engine) as session:
        cls = Class(name=name.strip(), code=code.strip().upper())
        session.add(cls)
        session.commit()
    return RedirectResponse(url="/", status_code=303)


@app.get("/classes.json", dependencies=[Depends(require_token)])
def classes_json():
    with Session(engine) as session:
        classes = session.exec(select(Class).order_by(Class.code)).all()
        return JSONResponse([
            {"id": c.id, "code": c.code, "name": c.name} for c in classes
        ])


@app.get("/classes/{class_id}", response_class=HTMLResponse)
def class_detail(request: Request, class_id: int):
    if COMPASS_TOKEN and not has_valid_cookie(request):
        return RedirectResponse("/setup-token", status_code=303)
    with Session(engine) as session:
        cls = session.get(Class, class_id)
        if not cls:
            raise HTTPException(404, "Class not found")
        events = sorted(cls.events, key=event_sort_key)
        documents = sorted(cls.documents, key=lambda d: d.uploaded_at, reverse=True)
        latest_syllabus = max(cls.syllabi, key=lambda s: s.parsed_at) if cls.syllabi else None
    # Floating tasks panel reuses the home page's today list. Add-task form
    # defaults to the current class.
    today_start = _today_local()
    today_end = today_start + timedelta(days=1)
    today_items = _collect_items_in_range(today_start, today_end)
    overdue = _collect_overdue()
    with Session(engine, expire_on_commit=False) as session:
        all_classes = session.exec(select(Class).order_by(Class.code)).all()
        all_tags = session.exec(select(Tag).order_by(Tag.name)).all()
    return templates.TemplateResponse(request, "class.html", {
        "cls": cls,
        "events": events,
        "documents": documents,
        "syllabus": latest_syllabus,
        "today": today_start,
        "today_items": today_items,
        "overdue": overdue,
        "all_classes": all_classes,
        "default_class_id": cls.id,
        "all_tags": all_tags,
    })


@app.post("/classes/{class_id}/delete", dependencies=[Depends(require_token)])
def delete_class(class_id: int):
    with Session(engine) as session:
        cls = session.get(Class, class_id)
        if not cls:
            raise HTTPException(404, "Class not found")
        # Remember syllabus IDs being cascade-deleted so we can clean up the
        # in-memory parse_jobs dict — otherwise stale "done" entries linger
        # and confuse status pages for re-used IDs.
        deleted_syllabus_ids = [s.id for s in cls.syllabi]
        session.delete(cls)
        session.commit()
    for sid in deleted_syllabus_ids:
        parse_jobs.pop(sid, None)
    return RedirectResponse(url="/", status_code=303)


# ---- Routes: Syllabus upload + parsing ----

@app.post("/syllabus", dependencies=[Depends(require_token)])
async def syllabus_upload(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
):
    content = await file.read()
    validate_pdf(content)

    safe_name = safe_filename(file.filename or "syllabus.pdf")
    filename = f"{uuid.uuid4().hex[:8]}_{safe_name}"
    upload_path = UPLOAD_DIR / filename
    upload_path.write_bytes(content)

    try:
        raw_text = extract_pdf_text(upload_path)
    except Exception as e:
        raise HTTPException(400, f"Could not extract text from PDF: {e}")

    if not raw_text.strip():
        raise HTTPException(400, "PDF appears to have no extractable text (might be a scanned image)")

    with Session(engine) as session:
        cls = Class(code="TBD", name="Parsing...")
        session.add(cls)
        session.flush()
        syllabus = Syllabus(
            class_id=cls.id,
            filename=filename,
            raw_text=raw_text,
            parsed_at=datetime.now(timezone.utc),
        )
        session.add(syllabus)
        session.commit()
        syllabus_id = syllabus.id

    parse_jobs[syllabus_id] = "pending"
    background_tasks.add_task(process_syllabus, syllabus_id)

    return RedirectResponse(url=f"/syllabus/{syllabus_id}/status", status_code=303)


@app.get("/syllabus/{syllabus_id}/status", response_class=HTMLResponse)
def syllabus_status(request: Request, syllabus_id: int):
    """Render the parsing status page. If the syllabus row is gone (the user
    deleted its class in another tab), render a friendly missing-state card
    with a Home link instead of 404'ing."""
    with Session(engine) as session:
        syllabus = session.get(Syllabus, syllabus_id)
        if not syllabus:
            return templates.TemplateResponse(request, "syllabus_status.html", {
                "syllabus_id": syllabus_id,
                "class_id": None,
                "status": "missing",
            })
        class_id = syllabus.class_id
    status = parse_jobs.get(syllabus_id, "unknown")
    return templates.TemplateResponse(request, "syllabus_status.html", {
        "syllabus_id": syllabus_id,
        "class_id": class_id,
        "status": status,
    })


@app.get("/syllabus/{syllabus_id}/status.json")
def syllabus_status_json(syllabus_id: int):
    with Session(engine) as session:
        syllabus = session.get(Syllabus, syllabus_id)
        if not syllabus:
            # Syllabus row deleted (e.g. user wiped its class in another tab).
            # Distinct from "unknown" so the polling page can stop and show a
            # clear missing-state UI instead of looping forever.
            parse_jobs.pop(syllabus_id, None)  # cleanup stale memory entry
            return JSONResponse({"status": "missing"})
        status = parse_jobs.get(syllabus_id, "unknown")
        return JSONResponse({"status": status, "class_id": syllabus.class_id})


# ---- Routes: Event edit/delete ----

@app.post("/events/{event_id}/edit", dependencies=[Depends(require_token)])
def edit_event(
    event_id: int,
    title: str = Form(...),
    kind: str = Form(...),
    starts_at: str = Form(""),
    ends_at: str = Form(""),
):
    with Session(engine) as session:
        ev = session.get(CalendarEvent, event_id)
        if not ev:
            raise HTTPException(404)
        ev.title = title.strip() or "Untitled"
        new_kind = (kind.strip() or "milestone").lower()
        ev.kind = new_kind
        # First-time use of a kind from manual edit auto-creates its tag,
        # same as Grok-extracted kinds. Without this the collector finds
        # no system tag and the pill renders uncolored.
        _ensure_system_tag(session, new_kind)
        ev.starts_at = parse_iso_dt(starts_at) if starts_at else None
        ev.ends_at = parse_iso_dt(ends_at) if ends_at else None
        cls_id = ev.class_id
        session.add(ev)
        session.commit()
    return RedirectResponse(f"/classes/{cls_id}", status_code=303)


@app.post("/events/{event_id}/clone", dependencies=[Depends(require_token)])
def clone_event(event_id: int, request: Request):
    """Duplicate an event so it shows on the calendar as a separate row.
    Same class, same title/kind/starts_at/ends_at — fresh id. If the
    user clicks twice and gets two copies, that's on them to clean up."""
    with Session(engine) as session:
        ev = session.get(CalendarEvent, event_id)
        if not ev:
            raise HTTPException(404)
        copy = CalendarEvent(
            class_id=ev.class_id,
            class_code=ev.class_code,
            title=ev.title,
            starts_at=ev.starts_at,
            ends_at=ev.ends_at,
            kind=ev.kind,
            source_text=ev.source_text,
        )
        session.add(copy)
        session.commit()
        session.refresh(copy)
        cls_id = copy.class_id
        new_id = copy.id
    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse({"id": new_id})
    return RedirectResponse(f"/classes/{cls_id}", status_code=303)


@app.post("/events/{event_id}/delete", dependencies=[Depends(require_token)])
def delete_event(event_id: int, request: Request):
    with Session(engine) as session:
        ev = session.get(CalendarEvent, event_id)
        if not ev:
            raise HTTPException(404)
        cls_id = ev.class_id
        session.delete(ev)
        session.commit()
    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse({"deleted": event_id})
    return RedirectResponse(f"/classes/{cls_id}", status_code=303)


# ---- Helpers shared by PDF table marker rendering ----

import re as _re


# ---- Routes: Tasks ----

def _today_local() -> datetime:
    """Today's date at midnight in the user's local timezone."""
    now = datetime.now(LOCAL_TZ)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def _to_local(dt: Optional[datetime]) -> Optional[datetime]:
    """Attach LOCAL_TZ if naive, else convert to LOCAL_TZ. None passes through."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=LOCAL_TZ)
    return dt.astimezone(LOCAL_TZ)


def _normalize_task_range(starts_at: str, due_at: str) -> tuple[Optional[datetime], Optional[datetime]]:
    """Coerce raw ISO strings into a (starts_dt, due_dt) pair where:
       - starts_dt is None unless the task is an actual range,
       - if only one was supplied, it becomes due_dt (a single-date task),
       - if both were supplied in reverse order, swap them so due is later."""
    starts_dt = parse_iso_dt(starts_at) if starts_at else None
    due_dt = parse_iso_dt(due_at) if due_at else None
    if starts_dt and not due_dt:
        starts_dt, due_dt = None, starts_dt
    if starts_dt and due_dt and starts_dt > due_dt:
        starts_dt, due_dt = due_dt, starts_dt
    if starts_dt and due_dt and starts_dt == due_dt:
        starts_dt = None
    return starts_dt, due_dt


@app.post("/classes/{class_id}/tasks", dependencies=[Depends(require_token)])
async def create_task(class_id: int, request: Request,
                      title: str = Form(...), due_at: str = Form(""),
                      starts_at: str = Form(""), tag_id: str = Form("")):
    """Create a manual task on a class. Form-post adds via the class sidebar;
    AJAX clients get JSON, plain forms get a redirect (preserves no-JS path)."""
    title = title.strip()
    if not title:
        raise HTTPException(400, "Title required")
    starts_dt, due_dt = _normalize_task_range(starts_at, due_at)
    tag_pk: Optional[int] = None
    if tag_id:
        try:
            tag_pk = int(tag_id)
        except ValueError:
            tag_pk = None
    with Session(engine) as session:
        cls = session.get(Class, class_id)
        if not cls:
            raise HTTPException(404, "Class not found")
        if tag_pk is not None and not session.get(Tag, tag_pk):
            tag_pk = None  # silently drop bad tag id
        task = Task(
            class_id=class_id,
            title=title,
            starts_at=starts_dt,
            due_at=due_dt,
            tag_id=tag_pk,
            created_at=datetime.now(timezone.utc),
        )
        session.add(task)
        session.commit()
        session.refresh(task)
        if "application/json" in request.headers.get("accept", ""):
            return JSONResponse({
                "id": task.id,
                "title": task.title,
                "due_at": task.due_at.isoformat() if task.due_at else None,
                "completed_at": None,
            })
    return RedirectResponse(f"/classes/{class_id}", status_code=303)


@app.get("/tags.json", dependencies=[Depends(require_token)])
def list_tags():
    with Session(engine) as session:
        tags = session.exec(
            select(Tag).order_by(Tag.is_system.desc(), Tag.name)
        ).all()
        return JSONResponse([
            {"id": t.id, "name": t.name, "color": t.color, "is_system": t.is_system}
            for t in tags
        ])


_HEX_COLOR_RE = _re.compile(r"^#[0-9a-fA-F]{6}$")


@app.post("/tags", dependencies=[Depends(require_token)])
async def create_tag(name: str = Form(...), color: str = Form(...)):
    name = name.strip()
    color = color.strip()
    if not name:
        raise HTTPException(400, "Name required")
    if not _HEX_COLOR_RE.match(color):
        raise HTTPException(400, "Color must be #rrggbb hex")
    with Session(engine) as session:
        tag = Tag(name=name, color=color)
        session.add(tag)
        session.commit()
        session.refresh(tag)
        return JSONResponse({"id": tag.id, "name": tag.name, "color": tag.color})


@app.post("/tasks/{task_id}/toggle", dependencies=[Depends(require_token)])
def toggle_task(task_id: int):
    """Flip a task between completed and pending. AJAX-only; returns JSON."""
    with Session(engine) as session:
        t = session.get(Task, task_id)
        if not t:
            raise HTTPException(404)
        t.completed_at = None if t.completed_at else datetime.now(timezone.utc)
        completed = t.completed_at is not None
        session.add(t)
        session.commit()
    return JSONResponse({"id": task_id, "completed": completed})


@app.post("/events/{event_id}/toggle", dependencies=[Depends(require_token)])
def toggle_event(event_id: int):
    """Same toggle for syllabus-extracted CalendarEvents."""
    with Session(engine) as session:
        ev = session.get(CalendarEvent, event_id)
        if not ev:
            raise HTTPException(404)
        ev.completed_at = None if ev.completed_at else datetime.now(timezone.utc)
        completed = ev.completed_at is not None
        session.add(ev)
        session.commit()
    return JSONResponse({"id": event_id, "completed": completed})


@app.post("/tasks/{task_id}/edit", dependencies=[Depends(require_token)])
async def edit_task(task_id: int, request: Request,
                    title: str = Form(...), due_at: str = Form(""),
                    starts_at: str = Form(""),
                    tag_id: str = Form("__unset__")):
    """Update title, due_at, and (optionally) tag_id on a task. The form
    sends tag_id as '' for 'no tag', or a numeric id. The sentinel
    '__unset__' (default) means 'don't touch the tag' — so an old client
    that doesn't send the field still works."""
    title = title.strip()
    if not title:
        raise HTTPException(400, "Title required")
    starts_dt, due_dt = _normalize_task_range(starts_at, due_at)
    with Session(engine) as session:
        t = session.get(Task, task_id)
        if not t:
            raise HTTPException(404)
        t.title = title
        t.starts_at = starts_dt
        t.due_at = due_dt
        if tag_id != "__unset__":
            if tag_id == "":
                t.tag_id = None
            else:
                try:
                    tpk = int(tag_id)
                except ValueError:
                    tpk = None
                if tpk is not None and session.get(Tag, tpk):
                    t.tag_id = tpk
                else:
                    t.tag_id = None
        session.add(t)
        session.commit()
        session.refresh(t)
    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse({
            "id": t.id,
            "title": t.title,
            "due_at": t.due_at.isoformat() if t.due_at else None,
            "completed_at": t.completed_at.isoformat() if t.completed_at else None,
        })
    return RedirectResponse(f"/classes/{t.class_id}", status_code=303)


@app.post("/tags/{tag_id}/edit", dependencies=[Depends(require_token)])
async def edit_tag(tag_id: int, name: str = Form(...), color: str = Form(...)):
    """Rename and/or recolor a tag. System tags can be edited (their
    `system_key` stays canonical so events keep matching), but cannot
    be deleted — see delete_tag."""
    name = name.strip()
    color = color.strip()
    if not name:
        raise HTTPException(400, "Name required")
    if not _HEX_COLOR_RE.match(color):
        raise HTTPException(400, "Color must be #rrggbb hex")
    with Session(engine) as session:
        tag = session.get(Tag, tag_id)
        if not tag:
            raise HTTPException(404)
        tag.name = name
        tag.color = color
        # system_key is intentionally NOT touched — that's the key the
        # collector uses to keep events linked to this tag across renames.
        session.add(tag)
        session.commit()
        session.refresh(tag)
        return JSONResponse({"id": tag.id, "name": tag.name, "color": tag.color})


@app.post("/tags/{tag_id}/delete", dependencies=[Depends(require_token)])
async def delete_tag(tag_id: int):
    with Session(engine) as session:
        tag = session.get(Tag, tag_id)
        if not tag:
            raise HTTPException(404)
        if tag.is_system:
            raise HTTPException(400, "System tags cannot be deleted")
        # Null out tag_id on any tasks that reference this tag.
        for t in session.exec(select(Task).where(Task.tag_id == tag_id)).all():
            t.tag_id = None
            session.add(t)
        session.delete(tag)
        session.commit()
        return JSONResponse({"deleted": tag_id})


@app.post("/tasks/reorder", dependencies=[Depends(require_token)])
async def reorder_tasks(request: Request):
    """Persist drag-to-reorder priority across tasks AND events. Body shape:
       {items: [{kind:'task'|'event', id:int}, ...]}
       Older clients may still send {task_ids:[...]}; we treat that as
       all-tasks for backwards compat. Position 0..N-1 is assigned in the
       order received; non-listed rows are left alone."""
    payload = await request.json()
    raw_items = payload.get("items")
    if raw_items is None:
        # Legacy: {task_ids: [...]}
        raw_ids = payload.get("task_ids") or []
        if not isinstance(raw_ids, list):
            raise HTTPException(400, "task_ids must be a list")
        try:
            raw_items = [{"kind": "task", "id": int(i)} for i in raw_ids]
        except (TypeError, ValueError):
            raise HTTPException(400, "task_ids entries must be integers")
    if not isinstance(raw_items, list):
        raise HTTPException(400, "items must be a list")
    with Session(engine) as session:
        updated = 0
        for pos, entry in enumerate(raw_items):
            if not isinstance(entry, dict):
                continue
            kind = entry.get("kind")
            try:
                eid = int(entry.get("id"))
            except (TypeError, ValueError):
                continue
            if kind == "task":
                row = session.get(Task, eid)
            elif kind == "event":
                row = session.get(CalendarEvent, eid)
            else:
                continue
            if row is None:
                continue
            row.position = pos
            session.add(row)
            updated += 1
        session.commit()
    return JSONResponse({"reordered": updated})


@app.post("/tasks/{task_id}/delete", dependencies=[Depends(require_token)])
def delete_task(task_id: int, request: Request):
    with Session(engine) as session:
        t = session.get(Task, task_id)
        if not t:
            raise HTTPException(404)
        cls_id = t.class_id
        session.delete(t)
        session.commit()
    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse({"deleted": task_id})
    return RedirectResponse(f"/classes/{cls_id}", status_code=303)


# ---- Routes: Today + Week views ----

def _collect_items_in_range(start: datetime, end: datetime) -> dict:
    """Return {class_id: {class, items: [{kind, id, title, due_at, completed}]}}
    for tasks + events whose due/start datetime falls in [start, end). Both
    open and completed items are included; the template decides display."""
    out: dict[int, dict] = {}

    def _add(cls: "Class", kind: str, item_id: int, title: str,
             when: Optional[datetime], completed: bool,
             position: int = 0, sub_kind: Optional[str] = None,
             tag_color: Optional[str] = None, tag_name: Optional[str] = None,
             tag_id: Optional[int] = None, tag_is_system: bool = False,
             sub_kind_color: Optional[str] = None,
             sub_kind_id: Optional[int] = None,
             starts_at: Optional[datetime] = None,
             is_range: bool = False, is_range_day: bool = False,
             actionable: bool = True):
        slot = out.setdefault(cls.id, {"cls": cls, "items": []})
        slot["items"].append({
            "kind": kind,
            "sub_kind": sub_kind,
            "sub_kind_color": sub_kind_color,
            "sub_kind_id": sub_kind_id,
            "id": item_id,
            "class_id": cls.id,
            "title": title,
            "due_at": when,
            "starts_at": starts_at,
            "is_range": is_range,
            "is_range_day": is_range_day,
            "actionable": actionable,
            "completed": completed,
            "position": position,
            "tag_color": tag_color,
            "tag_name": tag_name,
            "tag_id": tag_id,
            "tag_is_system": tag_is_system,
        })

    with Session(engine, expire_on_commit=False) as session:
        sys_tag_by_key = {
            t.system_key: t
            for t in session.exec(select(Tag).where(Tag.is_system == True)).all()
            if t.system_key
        }
        for cls in session.exec(select(Class)).all():
            for t in cls.tasks:
                tcolor = t.tag.color if t.tag else None
                tname = t.tag.name if t.tag else None
                tpk = t.tag.id if t.tag else None
                tsys = t.tag.is_system if t.tag else False
                tag_kw = dict(tag_color=tcolor, tag_name=tname,
                              tag_id=tpk, tag_is_system=tsys)
                if t.due_at is None and t.starts_at is None:
                    # No dates — show on today only if uncompleted (acts
                    # like an open backlog item)
                    if start <= _today_local() < end and not t.completed_at:
                        _add(cls, "task", t.id, t.title, None, False,
                             t.position or 0, **tag_kw)
                    continue
                # Range tasks: emit one entry per day from starts_at.date()
                # through due_at.date() that falls in [start, end). Every
                # entry keeps the *actual* due_at and starts_at — only
                # is_range_day differs day-to-day — so the edit modal
                # repopulates correctly no matter which day the user clicks.
                if t.starts_at is not None and t.due_at is not None:
                    starts_local = _to_local(t.starts_at)
                    due_local = _to_local(t.due_at)
                    last_date = due_local.date()
                    d = starts_local.date()
                    while d <= last_date:
                        day_start = datetime.combine(d, time.min, tzinfo=LOCAL_TZ)
                        day_end = day_start + timedelta(days=1)
                        if day_start < end and day_end > start:
                            is_deadline = (d == last_date)
                            _add(cls, "task", t.id, t.title, due_local,
                                 t.completed_at is not None, t.position or 0,
                                 starts_at=starts_local, is_range=True,
                                 is_range_day=not is_deadline, **tag_kw)
                        d += timedelta(days=1)
                    continue
                local_due = _to_local(t.due_at)
                if start <= local_due < end:
                    _add(cls, "task", t.id, t.title, local_due,
                         t.completed_at is not None, t.position or 0, **tag_kw)
            for ev in cls.events:
                if ev.starts_at is None:
                    continue
                local_when = _to_local(ev.starts_at)
                if start <= local_when < end:
                    sys_tag = sys_tag_by_key.get(ev.kind)
                    _add(cls, "event", ev.id, ev.title, local_when,
                         ev.completed_at is not None, ev.position or 0,
                         sub_kind=sys_tag.name if sys_tag else ev.kind,
                         sub_kind_color=sys_tag.color if sys_tag else None,
                         sub_kind_id=sys_tag.id if sys_tag else None,
                         actionable=ev.actionable)
    # Sort: position first (user's drag priority), then due time.
    for slot in out.values():
        slot["items"].sort(key=lambda it: (
            it["position"],
            it["due_at"] is None,
            it["due_at"] or datetime.max.replace(tzinfo=LOCAL_TZ),
        ))
    return out


def _collect_overdue() -> dict:
    """All non-completed tasks/events with due dates in the past."""
    now = datetime.now(LOCAL_TZ)
    out: dict[int, dict] = {}
    with Session(engine, expire_on_commit=False) as session:
        sys_tag_by_key = {
            t.system_key: t
            for t in session.exec(select(Tag).where(Tag.is_system == True)).all()
            if t.system_key
        }
        for cls in session.exec(select(Class)).all():
            for t in cls.tasks:
                if t.completed_at:
                    continue
                if t.due_at is None:
                    continue
                local_due = _to_local(t.due_at)
                if local_due < now and local_due >= now - timedelta(days=30):
                    out.setdefault(cls.id, {"cls": cls, "items": []})["items"].append({
                        "kind": "task", "sub_kind": None, "sub_kind_color": None,
                        "sub_kind_id": None,
                        "id": t.id,
                        "class_id": cls.id, "title": t.title,
                        "due_at": local_due, "completed": False,
                        "starts_at": _to_local(t.starts_at) if t.starts_at else None,
                        "is_range": t.starts_at is not None,
                        "is_range_day": False,
                        "actionable": True,
                        "position": t.position or 0,
                        "tag_color": t.tag.color if t.tag else None,
                        "tag_name": t.tag.name if t.tag else None,
                        "tag_id": t.tag.id if t.tag else None,
                        "tag_is_system": t.tag.is_system if t.tag else False,
                    })
            for ev in cls.events:
                if ev.completed_at:
                    continue
                if ev.starts_at is None:
                    continue
                # Non-actionable events (past lectures, holidays) aren't
                # "overdue" — nothing to chase. Skip.
                if not ev.actionable:
                    continue
                local_when = _to_local(ev.starts_at)
                if local_when < now and local_when >= now - timedelta(days=30):
                    sys_tag = sys_tag_by_key.get(ev.kind)
                    out.setdefault(cls.id, {"cls": cls, "items": []})["items"].append({
                        "kind": "event",
                        "sub_kind": sys_tag.name if sys_tag else ev.kind,
                        "sub_kind_color": sys_tag.color if sys_tag else None,
                        "sub_kind_id": sys_tag.id if sys_tag else None,
                        "id": ev.id,
                        "class_id": cls.id, "title": ev.title,
                        "due_at": local_when, "completed": False,
                        "starts_at": None, "is_range": False, "is_range_day": False,
                        "actionable": True,
                        "position": ev.position or 0,
                        "tag_color": None,
                        "tag_name": None,
                        "tag_id": None,
                        "tag_is_system": False,
                    })
    for slot in out.values():
        slot["items"].sort(key=lambda it: (it["position"], it["due_at"] or datetime.max.replace(tzinfo=LOCAL_TZ)))
    return out


@app.get("/today", response_class=HTMLResponse)
def today_view(request: Request):
    """Tasks and events due today, plus anything overdue."""
    if COMPASS_TOKEN and not has_valid_cookie(request):
        return RedirectResponse("/setup-token", status_code=303)
    today_start = _today_local()
    today_end = today_start + timedelta(days=1)
    today_items = _collect_items_in_range(today_start, today_end)
    overdue = _collect_overdue()
    return templates.TemplateResponse(request, "today.html", {
        "today": today_start,
        "today_items": today_items,
        "overdue": overdue,
    })


@app.get("/week", response_class=HTMLResponse)
def week_view(request: Request, month: Optional[str] = None):
    """Month-grid view (Mon-Sun, 6 weeks) for the requested YYYY-MM.
    Defaults to the current month."""
    if COMPASS_TOKEN and not has_valid_cookie(request):
        return RedirectResponse("/setup-token", status_code=303)
    today_start = _today_local()
    # Parse the requested month; fall back to today's month on bad input.
    target_year, target_month = today_start.year, today_start.month
    if month:
        try:
            y, m = month.split("-")
            ty, tm = int(y), int(m)
            if 1 <= tm <= 12:
                target_year, target_month = ty, tm
        except (ValueError, AttributeError):
            pass
    first_of_month = datetime(target_year, target_month, 1, tzinfo=LOCAL_TZ)
    # Grid starts on the Monday on-or-before the 1st.
    grid_start = first_of_month - timedelta(days=first_of_month.weekday())
    days = []
    for i in range(42):  # 6 weeks × 7 days
        day_start = grid_start + timedelta(days=i)
        day_end = day_start + timedelta(days=1)
        items_by_class = _collect_items_in_range(day_start, day_end)
        days.append({
            "date": day_start,
            "in_month": day_start.month == target_month,
            "is_today": day_start == today_start,
            "items_by_class": items_by_class,
        })
    # Prev / next month nav.
    if target_month == 1:
        prev_y, prev_m = target_year - 1, 12
    else:
        prev_y, prev_m = target_year, target_month - 1
    if target_month == 12:
        next_y, next_m = target_year + 1, 1
    else:
        next_y, next_m = target_year, target_month + 1
    with Session(engine, expire_on_commit=False) as session:
        all_classes = session.exec(select(Class).order_by(Class.code)).all()
        all_tags = session.exec(select(Tag).order_by(Tag.name)).all()
    return templates.TemplateResponse(request, "week.html", {
        "first_of_month": first_of_month,
        "days": days,
        "prev_month": f"{prev_y:04d}-{prev_m:02d}",
        "next_month": f"{next_y:04d}-{next_m:02d}",
        "all_classes": all_classes,
        "default_class_id": (all_classes[0].id if all_classes else None),
        "all_tags": all_tags,
    })


# ---- Routes: Document upload ----

@app.post("/classes/{class_id}/docs", dependencies=[Depends(require_token)])
async def upload_doc(
    class_id: int,
    file: UploadFile = File(...),
    title: str = Form(""),
):
    content = await file.read()
    validate_upload(content)
    safe_name = safe_filename(file.filename or "doc")
    filename = f"{uuid.uuid4().hex[:8]}_{safe_name}"
    (UPLOAD_DIR / filename).write_bytes(content)

    with Session(engine) as session:
        cls = session.get(Class, class_id)
        if not cls:
            raise HTTPException(404)
        doc = Document(
            class_id=class_id,
            title=(title.strip() or safe_name),
            filename=filename,
            uploaded_at=datetime.now(timezone.utc),
        )
        session.add(doc)
        session.commit()
    return RedirectResponse(f"/classes/{class_id}", status_code=303)


@app.post("/docs/{doc_id}/delete", dependencies=[Depends(require_token)])
def delete_doc(doc_id: int):
    with Session(engine) as session:
        d = session.get(Document, doc_id)
        if not d:
            raise HTTPException(404)
        cls_id = d.class_id
        # remove file from disk too (best-effort)
        try:
            (UPLOAD_DIR / d.filename).unlink(missing_ok=True)
        except Exception:
            pass
        session.delete(d)
        session.commit()
    return RedirectResponse(f"/classes/{cls_id}", status_code=303)


@app.get("/uploads/{filename}")
def serve_upload(filename: str):
    safe = safe_filename(filename)
    path = UPLOAD_DIR / safe
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(path)


# ---- Routes: iCal feed ----

@app.get("/calendar.ics")
def ical_feed():
    cal = Calendar()
    cal.add("prodid", "-//Compass//Local//EN")
    cal.add("version", "2.0")
    cal.add("x-wr-calname", "Compass")
    cal.add("x-wr-timezone", str(LOCAL_TZ))

    with Session(engine) as session:
        events = session.exec(
            select(CalendarEvent).where(CalendarEvent.starts_at != None)
        ).all()
        for ev in events:
            ie = ICalEvent()
            ie.add("uid", f"compass-event-{ev.id}@local")
            ie.add("summary", f"[{ev.class_code}] {ev.title}")
            starts = ev.starts_at if ev.starts_at.tzinfo else ev.starts_at.replace(tzinfo=LOCAL_TZ)
            ie.add("dtstart", starts)
            if ev.ends_at:
                ends = ev.ends_at if ev.ends_at.tzinfo else ev.ends_at.replace(tzinfo=LOCAL_TZ)
                ie.add("dtend", ends)
            ie.add("dtstamp", datetime.now(timezone.utc))
            ie.add("description", f"{ev.kind.title()} for {ev.class_code}")
            cal.add_component(ie)

    return Response(content=cal.to_ical(), media_type="text/calendar")
