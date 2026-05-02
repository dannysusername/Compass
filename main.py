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
import pdfplumber


# ---- Config ----

LOCAL_TZ = ZoneInfo("America/New_York")  # change to your timezone
OLLAMA_MODEL = "qwen2.5:7b"
MAX_UPLOAD_MB = 25
MAX_SYLLABUS_CHARS = 30_000  # cap text fed to LLM
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
    with pdfplumber.open(path) as pdf:
        chunks = [page.extract_text() or "" for page in pdf.pages]
    return "\n\n".join(chunks)[:MAX_SYLLABUS_CHARS]


def build_syllabus_prompt(text: str) -> str:
    return f"""You are a syllabus parser. Given the text of a university course syllabus, extract the following as a single JSON object:

{{
  "course_code": "string — the course identifier, e.g. 'MATH 250' or 'CS 101'",
  "course_name": "string — full course title, e.g. 'Calculus II'",
  "instructor": "string or null",
  "late_policy": "string or null — describe the late submission policy",
  "attendance_policy": "string or null",
  "grading_breakdown": [{{"category": "string", "weight_percent": number}}],
  "office_hours": [{{"day": "string", "time": "string", "location": "string"}}],
  "events": [
    {{
      "title": "string — e.g. 'Midterm 1' or 'Project 2 due'",
      "kind": "exam|assignment|project|milestone",
      "starts_at": "ISO 8601 datetime in local time, or null",
      "ends_at": "ISO 8601 datetime, or null"
    }}
  ]
}}

Rules:
- Return ONLY valid JSON. No markdown, no commentary.
- If a field cannot be determined, return null (or empty array for lists).
- DO NOT invent dates. If a date isn't explicitly in the syllabus, use null.
- For events without a date (e.g. "Week 5 — Midterm"), set starts_at to null and put context in the title.
- If only a date is given (no time), use 23:59:00 of that day.
- Course code should be UPPERCASE with a single space (e.g. "MATH 250").

SYLLABUS TEXT:
{text}"""


def parse_syllabus_with_ollama(text: str) -> dict:
    """Call Ollama with format=json. Raises on failure."""
    from ollama import chat
    resp = chat(
        model=OLLAMA_MODEL,
        messages=[{"role": "user", "content": build_syllabus_prompt(text)}],
        format="json",
        options={"temperature": 0.0},
    )
    return json.loads(resp["message"]["content"])


def process_syllabus(syllabus_id: int) -> None:
    """Background task: run LLM, save policies + events, update class metadata."""
    parse_jobs[syllabus_id] = "running"
    try:
        with Session(engine) as session:
            syllabus = session.get(Syllabus, syllabus_id)
            if not syllabus:
                parse_jobs[syllabus_id] = "error: syllabus not found"
                return
            data = parse_syllabus_with_ollama(syllabus.raw_text)

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
