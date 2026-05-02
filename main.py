from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List
from zoneinfo import ZoneInfo
import json
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
import anthropic
import pdfplumber


# ---- Config ----

LOCAL_TZ = ZoneInfo("America/New_York")  # change to your timezone
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6").strip()
MAX_UPLOAD_MB = 25
STUDYFLOW_TOKEN = os.environ.get("STUDYFLOW_TOKEN", "").strip()  # empty = dev mode (no auth)
COOKIE_NAME = "studyflow_token"


# ---- Database models ----

class Class(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    code: str
    syllabi: List["Syllabus"] = Relationship(
        back_populates="cls",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )
    policies: List["Policy"] = Relationship(
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


class Syllabus(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    class_id: int = Field(foreign_key="class.id")
    filename: str
    raw_text: str
    parsed_at: datetime
    cls: Optional[Class] = Relationship(back_populates="syllabi")


class Policy(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    class_id: int = Field(foreign_key="class.id")
    kind: str  # late_policy | grading | office_hours | attendance_policy
    content: str
    cls: Optional[Class] = Relationship(back_populates="policies")


class CalendarEvent(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    class_id: int = Field(foreign_key="class.id")
    class_code: str
    title: str
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    kind: str  # exam | assignment | project | milestone
    cls: Optional[Class] = Relationship(back_populates="events")


class Document(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    class_id: int = Field(foreign_key="class.id")
    title: str
    filename: str
    uploaded_at: datetime
    cls: Optional[Class] = Relationship(back_populates="documents")


# ---- App setup ----

DB_PATH = Path(__file__).parent / "studyflow.db"
UPLOAD_DIR = Path(__file__).parent / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

engine = create_engine(
    f"sqlite:///{DB_PATH}",
    connect_args={"check_same_thread": False},
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    SQLModel.metadata.create_all(engine)
    yield


app = FastAPI(title="StudyFlow", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


# ---- Parse-job status (in-memory; resets on restart) ----

parse_jobs: dict[int, str] = {}  # syllabus_id -> "pending"|"running"|"done"|"error: ..."


# ---- Auth ----

def _token_matches(candidate: str) -> bool:
    if not candidate or not STUDYFLOW_TOKEN:
        return False
    return secrets.compare_digest(candidate, STUDYFLOW_TOKEN)


def require_token(request: Request) -> None:
    """Dependency: enforce auth on mutating routes if STUDYFLOW_TOKEN is set."""
    if not STUDYFLOW_TOKEN:
        return  # dev mode
    header = request.headers.get("x-studyflow-token", "")
    if _token_matches(header):
        return
    cookie = request.cookies.get(COOKIE_NAME, "")
    if _token_matches(cookie):
        return
    qp = request.query_params.get("token", "")
    if _token_matches(qp):
        return
    raise HTTPException(401, "Missing or invalid token. Set X-StudyFlow-Token header or visit /setup-token in a browser.")


def has_valid_cookie(request: Request) -> bool:
    if not STUDYFLOW_TOKEN:
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
    """Parse an ISO datetime string, attaching LOCAL_TZ if naive. Returns None on failure."""
    if not value or not isinstance(value, str):
        return None
    try:
        cleaned = value.strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=LOCAL_TZ)
        return dt
    except (ValueError, TypeError):
        return None


def extract_pdf_text(path: Path) -> str:
    """Extract text. dedupe_chars handles 'fake bold' double-stamped glyphs
    (e.g. 'CCIISS33995500' instead of 'CIS 3950') that show up in many
    professor-authored syllabi. Safe on already-clean PDFs (no-op)."""
    chunks: list[str] = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            try:
                cleaned = page.dedupe_chars()
                chunks.append(cleaned.extract_text() or "")
            except Exception:
                chunks.append(page.extract_text() or "")
    return "\n\n".join(chunks)


SYLLABUS_SYSTEM_PROMPT = """You are a structured-extraction system for university course syllabi.

INPUT: the full text of a syllabus (any subject, any university). The text may include header metadata, instructor info, course description, learning outcomes, grading breakdown, attendance and late-submission policies, office-hour schedules, and a calendar/schedule of dated events.

OUTPUT: a single JSON object exactly matching the enforced schema. Fields you cannot determine return null (or empty arrays for list types). Do not include preamble, commentary, or markdown — only the JSON.

# Field-by-field extraction rules

## course_code
The course identifier as it appears in the syllabus header. UPPERCASE with a single space between the department prefix and number.
- "math 250" → "MATH 250"
- "CIS3950" → "CIS 3950"
- "ENGL-1010" → "ENGL 1010"
- "Bio II Lab Section 003" → "BIO II" (strip section/lab modifiers if present in a separate field; keep only the course identifier)
If no clear course code is present, return null.

## course_name
The full course title as listed in the header (e.g. "Calculus II", "Introduction to Computer Science", "American Literature: 1865 to Present"). Don't include the course code, the section number, or the term ("Fall 2026"). If the syllabus has both a short title and an expanded title, prefer the expanded title.

## instructor
Primary instructor's full name including title if given ("Dr. Jane Doe", "Prof. Smith"). If multiple instructors are listed, use the first ("Primary Instructor", "Lead Faculty", or the one listed first by position). For TA-only or co-taught courses with no clear primary, use the first name listed.

## late_policy
A concise summary of the late-submission rules (verbatim quote or summary, ≤ 300 chars). Examples:
- "10% deduction per day late, max 5 days"
- "No late work accepted without medical excuse"
- "Late work loses 1 letter grade per 24 hours"
If the syllabus doesn't address late work, return null. Do NOT invent a default policy.

## attendance_policy
Same shape as late_policy but for attendance/absence rules. Examples:
- "Required; more than 3 unexcused absences lowers final grade"
- "Attendance not graded but recommended"
If the syllabus doesn't address attendance, return null.

## grading_breakdown
Array of {category, weight_percent} objects. Categories should be the human label as written ("Homework", "Midterm 1", "Final Project", "Class Participation"). Weights should be numbers (not strings). If percentages are given, use them. If only points are given, convert to percentages where total points are stated:
- "Homework 80 of 100 points" → {"category": "Homework", "weight_percent": 80}
If the breakdown is purely qualitative ("homework counts heavily"), return an empty array. Weights should sum to 100 when the syllabus is fully specified, but don't fabricate to make them sum.

## office_hours
Array of {day, time, location} objects. One entry per scheduled office-hours block:
- "Tuesdays 2-4pm in Room 304" → {"day": "Tuesday", "time": "14:00-16:00", "location": "Room 304"}
- "MWF 10-11am, by appointment" → two entries: one structured, one with location: "by appointment"
- "Mon to Fri 10-10:30 AM via Zoom" → {"day": "Mon-Fri", "time": "10:00-10:30", "location": "Zoom"}
Times should be in 24-hour format (HH:MM-HH:MM) when convertible. Day can be a single day, a comma list ("Monday, Wednesday"), or a range ("Mon-Fri"). location can be a room, "Zoom", a Zoom URL, "by appointment", or null if unspecified.

## events
Array of dated occurrences from the course schedule: exams, project deadlines, major assignment due dates, important milestones. Each entry: {title, kind, starts_at, ends_at}.

- title: short human label ("Midterm 1", "Project 2 Due", "Final Exam", "Paper 1 Draft Due")
- kind: one of `exam`, `assignment`, `project`, `milestone`
  - exam: graded sit-down assessment
  - assignment: homework/problem set/short paper due
  - project: longer multi-stage project due
  - milestone: anything else with a deadline (registration, drop deadline, presentation slot)
- starts_at: ISO 8601 datetime in local time without timezone offset (e.g. "2026-09-15T18:00:00"). If only a date is given, use 23:59:00 of that day.
- ends_at: ISO 8601 datetime, or null. Only set if the syllabus specifies a duration ("Final Exam: Dec 14, 8-10am" → starts_at "2026-12-14T08:00:00", ends_at "2026-12-14T10:00:00").

DATE HANDLING — STRICT:
- DO NOT INVENT DATES. If the syllabus says "Week 5 — Midterm" without an explicit calendar date, set starts_at: null and put the descriptor in the title ("Week 5 — Midterm").
- If you can resolve a date from term context (e.g. syllabus says "Fall 2026" + "Week 5 of class"), still leave starts_at: null. Term-relative dates require knowing the academic calendar; you don't.
- Recurring weekly assignments ("Problem Set due every Friday") are too low-signal to enumerate as events. Skip them. Capture only specific dated milestones.
- Time zones: the syllabus may state a time zone explicitly. Ignore it — emit naive ISO datetimes; the application handles timezone attachment.

# Examples

## Example 1 — typical CS syllabus

INPUT (excerpted):
"CS 101 - Intro to Programming
Spring 2026 Syllabus
Instructor: Dr. Jane Doe (jane@university.edu)
Office Hours: Tuesday and Thursday, 1-2pm, Room 207

Late Policy: -10% per day late, no submissions accepted after 5 days late.
Attendance: required for lab sections; lecture attendance not graded.

Grading:
- Homework: 30%
- Lab Reports: 20%
- Midterm: 20%
- Final Project: 30%

Important Dates:
- Midterm Exam: March 15, 2026 at 6:00pm
- Final Project Due: May 7, 2026
- Final Project Presentations: May 10, 2026, 9am-12pm"

OUTPUT:
{
  "course_code": "CS 101",
  "course_name": "Intro to Programming",
  "instructor": "Dr. Jane Doe",
  "late_policy": "-10% per day late, no submissions accepted after 5 days late",
  "attendance_policy": "Required for lab sections; lecture attendance not graded",
  "grading_breakdown": [
    {"category": "Homework", "weight_percent": 30},
    {"category": "Lab Reports", "weight_percent": 20},
    {"category": "Midterm", "weight_percent": 20},
    {"category": "Final Project", "weight_percent": 30}
  ],
  "office_hours": [
    {"day": "Tuesday", "time": "13:00-14:00", "location": "Room 207"},
    {"day": "Thursday", "time": "13:00-14:00", "location": "Room 207"}
  ],
  "events": [
    {"title": "Midterm Exam", "kind": "exam", "starts_at": "2026-03-15T18:00:00", "ends_at": null},
    {"title": "Final Project Due", "kind": "project", "starts_at": "2026-05-07T23:59:00", "ends_at": null},
    {"title": "Final Project Presentations", "kind": "project", "starts_at": "2026-05-10T09:00:00", "ends_at": "2026-05-10T12:00:00"}
  ]
}

## Example 2 — humanities syllabus, week-relative dates

INPUT (excerpted):
"ENGL 245 - American Literature 1865-Present
Prof. Maria Lopez, mlopez@uni.edu
Office hours by appointment only (email to schedule).

Grading is based on three papers (20% each), one final exam (30%), and class participation (10%).

Course Schedule:
- Week 3: Paper 1 due
- Week 7: Paper 2 due
- Week 11: Paper 3 due
- Final Exam: December 18, 2026"

OUTPUT:
{
  "course_code": "ENGL 245",
  "course_name": "American Literature 1865-Present",
  "instructor": "Prof. Maria Lopez",
  "late_policy": null,
  "attendance_policy": null,
  "grading_breakdown": [
    {"category": "Papers", "weight_percent": 60},
    {"category": "Final Exam", "weight_percent": 30},
    {"category": "Class Participation", "weight_percent": 10}
  ],
  "office_hours": [
    {"day": null, "time": null, "location": "by appointment"}
  ],
  "events": [
    {"title": "Week 3: Paper 1 due", "kind": "assignment", "starts_at": null, "ends_at": null},
    {"title": "Week 7: Paper 2 due", "kind": "assignment", "starts_at": null, "ends_at": null},
    {"title": "Week 11: Paper 3 due", "kind": "assignment", "starts_at": null, "ends_at": null},
    {"title": "Final Exam", "kind": "exam", "starts_at": "2026-12-18T23:59:00", "ends_at": null}
  ]
}

# Final reminders

- Return only the JSON object, matching the enforced schema exactly.
- When uncertain, prefer null over inventing.
- The user message contains the full syllabus text. Read it all before responding."""


# JSON schema for output_config.format. Sonnet 4.6 will constrain output to this shape.
SYLLABUS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "course_code": {"type": ["string", "null"]},
        "course_name": {"type": ["string", "null"]},
        "instructor": {"type": ["string", "null"]},
        "late_policy": {"type": ["string", "null"]},
        "attendance_policy": {"type": ["string", "null"]},
        "grading_breakdown": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "category": {"type": "string"},
                    "weight_percent": {"type": "number"},
                },
                "required": ["category", "weight_percent"],
            },
        },
        "office_hours": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "day": {"type": ["string", "null"]},
                    "time": {"type": ["string", "null"]},
                    "location": {"type": ["string", "null"]},
                },
                "required": ["day", "time", "location"],
            },
        },
        "events": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "title": {"type": "string"},
                    "kind": {"type": "string", "enum": ["exam", "assignment", "project", "milestone"]},
                    "starts_at": {"type": ["string", "null"]},
                    "ends_at": {"type": ["string", "null"]},
                },
                "required": ["title", "kind", "starts_at", "ends_at"],
            },
        },
    },
    "required": [
        "course_code", "course_name", "instructor",
        "late_policy", "attendance_policy",
        "grading_breakdown", "office_hours", "events",
    ],
}


def parse_syllabus_with_claude(text: str) -> dict:
    """Call Claude (Sonnet 4.6 by default) with structured-output enforcement and
    a cached system prompt. Raises anthropic exceptions on failure; caller logs."""
    if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Put your key in .anthropic_key next to "
            "main.py, or export it before launching."
        )
    client = anthropic.Anthropic()
    response = client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=8192,
        system=[
            {
                "type": "text",
                "text": SYLLABUS_SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": text}],
        output_config={"format": {"type": "json_schema", "schema": SYLLABUS_SCHEMA}},
    )
    text_block = next((b.text for b in response.content if b.type == "text"), None)
    if not text_block:
        raise RuntimeError("Claude returned no text content")
    return json.loads(text_block)


def process_syllabus(syllabus_id: int) -> None:
    """Background task: run LLM, save policies + events, update class metadata."""
    parse_jobs[syllabus_id] = "running"
    try:
        with Session(engine) as session:
            syllabus = session.get(Syllabus, syllabus_id)
            if not syllabus:
                parse_jobs[syllabus_id] = "error: syllabus not found"
                return
            try:
                data = parse_syllabus_with_claude(syllabus.raw_text)
            except anthropic.AuthenticationError:
                parse_jobs[syllabus_id] = (
                    "error: ANTHROPIC_API_KEY is invalid. Check your key at "
                    "https://console.anthropic.com/settings/keys"
                )
                return
            except anthropic.RateLimitError as e:
                retry_after = e.response.headers.get("retry-after", "unknown") if hasattr(e, "response") else "unknown"
                parse_jobs[syllabus_id] = f"error: rate limited by Anthropic API. Retry after {retry_after}s."
                return
            except anthropic.APIConnectionError:
                parse_jobs[syllabus_id] = "error: could not reach the Anthropic API. Check your internet connection."
                return
            except anthropic.BadRequestError as e:
                parse_jobs[syllabus_id] = f"error: bad request to Claude. {str(e)[:300]}"
                return
            except anthropic.APIStatusError as e:
                parse_jobs[syllabus_id] = f"error: Anthropic API returned {e.status_code}. {str(e)[:200]}"
                return
            except json.JSONDecodeError as e:
                parse_jobs[syllabus_id] = f"error: Claude output was not valid JSON. {str(e)[:200]}"
                return
            except RuntimeError as e:
                parse_jobs[syllabus_id] = f"error: {e}"
                return

            cls = session.get(Class, syllabus.class_id)
            if data.get("course_code"):
                cls.code = str(data["course_code"]).upper().strip()
            if data.get("course_name"):
                cls.name = str(data["course_name"]).strip()

            # Wipe prior policies/events for this class — re-upload replaces, doesn't accumulate
            session.exec(delete(Policy).where(Policy.class_id == cls.id))
            session.exec(delete(CalendarEvent).where(CalendarEvent.class_id == cls.id))
            session.flush()

            # Free-text policies
            for kind in ("late_policy", "attendance_policy"):
                value = data.get(kind)
                if value:
                    session.add(Policy(class_id=cls.id, kind=kind, content=str(value)))

            # Structured policies stored as JSON in content
            grading = data.get("grading_breakdown")
            if grading:
                session.add(Policy(class_id=cls.id, kind="grading", content=json.dumps(grading)))

            office_hours = data.get("office_hours")
            if office_hours:
                session.add(Policy(class_id=cls.id, kind="office_hours", content=json.dumps(office_hours)))

            instructor = data.get("instructor")
            if instructor:
                session.add(Policy(class_id=cls.id, kind="instructor", content=str(instructor)))

            # Events
            for ev in data.get("events", []) or []:
                if not isinstance(ev, dict):
                    continue
                session.add(CalendarEvent(
                    class_id=cls.id,
                    class_code=cls.code,
                    title=str(ev.get("title") or "Untitled"),
                    starts_at=parse_iso_dt(ev.get("starts_at")),
                    ends_at=parse_iso_dt(ev.get("ends_at")),
                    kind=str(ev.get("kind") or "milestone"),
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
        "auth_required": bool(STUDYFLOW_TOKEN),
        "already_set": has_valid_cookie(request),
    })


@app.post("/setup-token")
def setup_token_submit(token: str = Form(...)):
    if STUDYFLOW_TOKEN and not _token_matches(token.strip()):
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
    if STUDYFLOW_TOKEN and not has_valid_cookie(request):
        return RedirectResponse("/setup-token", status_code=303)
    with Session(engine) as session:
        classes = session.exec(select(Class).order_by(Class.code)).all()
    return templates.TemplateResponse(request, "home.html", {"classes": classes})


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
    if STUDYFLOW_TOKEN and not has_valid_cookie(request):
        return RedirectResponse("/setup-token", status_code=303)
    with Session(engine) as session:
        cls = session.get(Class, class_id)
        if not cls:
            raise HTTPException(404, "Class not found")
        policies = sorted(cls.policies, key=lambda p: p.kind)
        events = sorted(cls.events, key=event_sort_key)
        documents = sorted(cls.documents, key=lambda d: d.uploaded_at, reverse=True)
        # Pre-decode any JSON-stored policies for the template
        decoded_policies = []
        for p in policies:
            row = {"id": p.id, "kind": p.kind, "content": p.content, "structured": None}
            if p.kind in ("grading", "office_hours"):
                try:
                    row["structured"] = json.loads(p.content)
                except (ValueError, TypeError):
                    pass
            decoded_policies.append(row)
    return templates.TemplateResponse(request, "class.html", {
        "cls": cls,
        "policies": decoded_policies,
        "events": events,
        "documents": documents,
    })


@app.post("/classes/{class_id}/delete", dependencies=[Depends(require_token)])
def delete_class(class_id: int):
    with Session(engine) as session:
        cls = session.get(Class, class_id)
        if not cls:
            raise HTTPException(404, "Class not found")
        session.delete(cls)
        session.commit()
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
    status = parse_jobs.get(syllabus_id, "unknown")
    with Session(engine) as session:
        syllabus = session.get(Syllabus, syllabus_id)
        if not syllabus:
            raise HTTPException(404)
        class_id = syllabus.class_id
    return templates.TemplateResponse(request, "syllabus_status.html", {
        "syllabus_id": syllabus_id,
        "class_id": class_id,
        "status": status,
    })


@app.get("/syllabus/{syllabus_id}/status.json")
def syllabus_status_json(syllabus_id: int):
    status = parse_jobs.get(syllabus_id, "unknown")
    with Session(engine) as session:
        syllabus = session.get(Syllabus, syllabus_id)
        if not syllabus:
            return JSONResponse({"status": "unknown"})
        return JSONResponse({"status": status, "class_id": syllabus.class_id})


# ---- Routes: Event + Policy edit/delete ----

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
        ev.kind = kind.strip() or "milestone"
        ev.starts_at = parse_iso_dt(starts_at) if starts_at else None
        ev.ends_at = parse_iso_dt(ends_at) if ends_at else None
        cls_id = ev.class_id
        session.add(ev)
        session.commit()
    return RedirectResponse(f"/classes/{cls_id}", status_code=303)


@app.post("/events/{event_id}/delete", dependencies=[Depends(require_token)])
def delete_event(event_id: int):
    with Session(engine) as session:
        ev = session.get(CalendarEvent, event_id)
        if not ev:
            raise HTTPException(404)
        cls_id = ev.class_id
        session.delete(ev)
        session.commit()
    return RedirectResponse(f"/classes/{cls_id}", status_code=303)


@app.post("/policies/{policy_id}/edit", dependencies=[Depends(require_token)])
def edit_policy(policy_id: int, content: str = Form(...)):
    with Session(engine) as session:
        p = session.get(Policy, policy_id)
        if not p:
            raise HTTPException(404)
        p.content = content
        cls_id = p.class_id
        session.add(p)
        session.commit()
    return RedirectResponse(f"/classes/{cls_id}", status_code=303)


@app.post("/policies/{policy_id}/delete", dependencies=[Depends(require_token)])
def delete_policy(policy_id: int):
    with Session(engine) as session:
        p = session.get(Policy, policy_id)
        if not p:
            raise HTTPException(404)
        cls_id = p.class_id
        session.delete(p)
        session.commit()
    return RedirectResponse(f"/classes/{cls_id}", status_code=303)


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
    cal.add("prodid", "-//StudyFlow//Local//EN")
    cal.add("version", "2.0")
    cal.add("x-wr-calname", "StudyFlow")
    cal.add("x-wr-timezone", str(LOCAL_TZ))

    with Session(engine) as session:
        events = session.exec(
            select(CalendarEvent).where(CalendarEvent.starts_at != None)
        ).all()
        for ev in events:
            ie = ICalEvent()
            ie.add("uid", f"studyflow-event-{ev.id}@local")
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
