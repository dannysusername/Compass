from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
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
STUDYFLOW_TOKEN = os.environ.get("STUDYFLOW_TOKEN", "").strip()  # empty = dev mode (no auth)
COOKIE_NAME = "studyflow_token"


# Logger that writes to the same studyflow.log the tray launcher uses.
# Hand-configured (not basicConfig) so it works whether main.py is the entry
# point or runs as a uvicorn subprocess with stdout/stderr suppressed.
log = logging.getLogger("studyflow")
if not log.handlers:
    log.setLevel(logging.INFO)
    _h = logging.FileHandler(Path(__file__).parent / "studyflow.log", encoding="utf-8")
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
    raw_text: str  # contains [TABLE:N] markers where pdfplumber detected tables
    parsed_at: datetime
    outline_json: Optional[str] = Field(default=None)  # cached Pass-1.5 outline
    tables_json: Optional[str] = Field(default=None)   # JSON list of {rows: [[cell,...],...]}
    cls: Optional[Class] = Relationship(back_populates="syllabi")


class CalendarEvent(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    class_id: int = Field(foreign_key="class.id")
    class_code: str
    title: str
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    kind: str  # exam | assignment | project | milestone
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
    due_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    position: int = Field(default=0)  # drag-to-reorder priority
    created_at: datetime
    cls: Optional[Class] = Relationship(back_populates="tasks")


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
        _add_column_if_missing(conn, "syllabus", "tables_json", "TEXT")
        _add_column_if_missing(conn, "card", "tables_json", "TEXT")
        _add_column_if_missing(conn, "card", "sections_meta_json", "TEXT")
        _add_column_if_missing(conn, "card", "tailor_prompt", "TEXT")
        _add_column_if_missing(conn, "card", "position", "INTEGER NOT NULL DEFAULT 0")
        _add_column_if_missing(conn, "calendarevent", "completed_at", "TIMESTAMP")
        _add_column_if_missing(conn, "task", "position", "INTEGER NOT NULL DEFAULT 0")
    yield


app = FastAPI(title="StudyFlow", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


def _render_text_with_tables(text: str, tables_json: Optional[str]) -> "Markup":
    """Jinja filter: render Markdown text + replace [TABLE:N] markers with
    rendered HTML tables. `tables_json` is the JSON-encoded list stored on
    Syllabus or Card. Each non-table text segment is run through
    python-markdown so bullets/paragraphs/headings/bold come out as proper
    HTML, then the rendered tables are spliced in at marker positions."""
    import html as _html
    import markdown as _md
    from markupsafe import Markup
    if not text:
        return Markup("")
    try:
        tables = json.loads(tables_json or "[]")
    except (ValueError, TypeError):
        tables = []

    parts = _TABLE_MARKER_RE.split(text)
    # split() interleaves: [text, idx, text, idx, ..., text]
    out: list[str] = []
    for i, part in enumerate(parts):
        if i % 2 == 0:
            stripped = part.strip()
            if stripped:
                out.append(_md.markdown(stripped, extensions=["sane_lists"]))
        else:
            try:
                idx = int(part)
                table = tables[idx]
                rows = table.get("rows") if isinstance(table, dict) else None
                if not rows:
                    out.append(f"[TABLE:{part}]")
                    continue
                row_html = []
                for r_i, row in enumerate(rows):
                    cell_tag = "th" if r_i == 0 else "td"
                    cells_html = "".join(
                        f"<{cell_tag}>{_html.escape((c or '').strip())}</{cell_tag}>"
                        for c in row
                    )
                    row_html.append(f"<tr>{cells_html}</tr>")
                out.append(f'<table class="syllabus-table"><tbody>{"".join(row_html)}</tbody></table>')
            except (ValueError, IndexError, TypeError):
                out.append(f"[TABLE:{part}]")
    return Markup("".join(out))


templates.env.filters["render_text_with_tables"] = _render_text_with_tables


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


def _stitch_wrapped_cells(rows: list) -> list:
    """Merge adjacent rows where the next non-blank row is a wrapped-cell
    continuation of the previous one. When a long cell value spans a PDF page
    break, pdfplumber emits the spillover as its own row (often after one or
    more blank rows from the page boundary) with a single filled cell — we
    fold that fragment back into the matching column of the previous row.

    A row counts as a continuation when:
    - exactly 1 cell is filled, the fragment is ≤ 60 chars, AND
    - either (a) the previous row's same-column cell ends in hanging
      punctuation (`-`, `/`, `,`, `&`, `:`, `(`, `[`), OR
    - (b) the fragment itself starts with lowercase / hanging punctuation /
      a sentence-continuing word (`and`, `or`, `with`, etc.)

    Blank rows between the truncated row and its continuation are dropped
    (they're page-boundary artifacts), but only when a continuation is
    actually found; otherwise they stay in the output."""
    if len(rows) < 2:
        return rows

    HANGING = "-/&,([:"

    def _is_blank(row: list) -> bool:
        return not any((c or "").strip() for c in row)

    out: list[list] = []
    i = 0
    while i < len(rows):
        merged = list(rows[i])
        j = i + 1
        while True:
            # Look ahead past any blank rows for the next non-blank.
            scan = j
            while scan < len(rows) and _is_blank(rows[scan]):
                scan += 1
            if scan >= len(rows):
                break
            nxt = rows[scan]
            filled = [(k, (c or "").strip()) for k, c in enumerate(nxt) if (c or "").strip()]
            if len(filled) != 1:
                break
            col_idx, frag = filled[0]
            if len(frag) > 60:
                break

            prev_cell = (merged[col_idx] or "").strip() if col_idx < len(merged) else ""
            prev_ends_hanging = bool(prev_cell) and prev_cell[-1] in HANGING

            first_char = frag[0]
            looks_like_continuation = (
                prev_ends_hanging
                or first_char.islower()
                or first_char in "/-,&)]"
                or frag.lower().startswith(("and ", "or ", "with ", "the ", "of ", "in "))
            )
            if not looks_like_continuation:
                break

            prev_filled = sum(1 for c in merged if (c or "").strip())
            if len(merged) and prev_filled / len(merged) < 0.5:
                break

            if col_idx < len(merged):
                existing = (merged[col_idx] or "").strip()
                merged[col_idx] = (existing + " " + frag).strip()
            j = scan + 1  # advance past everything consumed (including blanks)
        out.append(merged)
        i = j
    return out


def _find_real_header_row(rows: list) -> int:
    """Return the index of the first row that looks like a real column header
    (most cells populated, each populated cell short). Returns -1 if no
    header-like row is found in the first 6 rows.

    Used to trim layout prose that pdfplumber sometimes glues to the front of
    a real table (e.g., a section heading + descriptive paragraph immediately
    above the actual grid). Without this trim, those rows pollute the table
    cells and visibly bleed neighboring section text into our rendered HTML
    table — the exact bug that prompted this function."""
    for i, row in enumerate(rows[:6]):
        cells = [(c or "").strip() for c in row]
        if not cells:
            continue
        n_filled = sum(1 for c in cells if c)
        n_short = sum(1 for c in cells if c and len(c) <= 50)
        # At least 60% of cells populated AND every populated cell is short
        # (prose paragraphs run long; column headers don't).
        if n_filled / len(cells) >= 0.6 and n_short == n_filled:
            return i
    return -1


def _looks_like_continuation(prev_rows: list, new_rows: list) -> bool:
    """Heuristic: is `new_rows` a multi-page continuation of `prev_rows`
    (no repeated header on the second page)? True when the first row of
    new_rows looks like a body row, not a header — e.g., short cells,
    digits, or content that fits the same shape as previous body rows.
    Used to merge split tables in Simple Syllabus and similar templates."""
    if not prev_rows or not new_rows:
        return False
    # Header rows usually have all cells populated; continuation body rows
    # often don't. If the new table's first row has a blank cell while the
    # previous header didn't, it's almost certainly a body row.
    prev_header = prev_rows[0]
    new_first = new_rows[0]
    prev_header_all_filled = all((c or "").strip() for c in prev_header)
    new_first_has_blank = any(not (c or "").strip() for c in new_first)
    if prev_header_all_filled and new_first_has_blank:
        return True
    # If the previous body rows are short cells (e.g. grade letters or
    # dates) and the new first row matches that pattern, likely continuation.
    if len(prev_rows) > 1:
        prev_body_first = prev_rows[1]
        max_prev_cell = max((len((c or "").strip()) for c in prev_body_first), default=0)
        max_new_cell = max((len((c or "").strip()) for c in new_first), default=0)
        if max_prev_cell <= 40 and max_new_cell <= 40:
            return True
    return False


def extract_pdf_text(path: Path) -> tuple[str, list[dict]]:
    """Extract text + tables. dedupe_chars handles 'fake bold' double-stamped
    glyphs that show up in many professor-authored syllabi.

    For each table pdfplumber detects, we (a) append a list entry to the
    returned tables list and (b) inject a `[TABLE:N]` marker into the page
    text at the table's vertical position. Markers ride along through Grok
    outline + summarization (we instruct Grok to preserve them) and are
    swapped for rendered HTML tables at display time.

    Returns (text_with_markers, tables) where tables is
    [{"rows": [[cell, ...], ...]}, ...] and N indexes that list."""
    chunks: list[str] = []
    tables: list[dict] = []
    with pdfplumber.open(path) as pdf:
        total = len(pdf.pages)
        for page in pdf.pages:
            # Dedupe glyphs once and use the filtered view for both text and
            # tables so cells like "CCIISS33995500" come out as "CIS 3950".
            try:
                source = page.dedupe_chars()
            except Exception:
                source = page

            page_tables: list[tuple[float, str]] = []  # (top_y, marker)
            accepted_bboxes: list[tuple] = []  # bboxes of tables we kept, used to filter chars
            try:
                found = source.find_tables()
            except Exception:
                found = []
            for tbl in found:
                try:
                    rows = tbl.extract()
                except Exception:
                    continue
                if not rows or len(rows) < 2:
                    continue
                max_cols = max((len(r) for r in rows), default=0)
                if max_cols < 2:
                    continue

                # Find the real header row. If the table starts with layout
                # prose (a heading or paragraph above the actual grid),
                # `header_idx` will be > 0 and we trim those rows. If we
                # can't find a header at all, this isn't really a data table
                # — skip it.
                header_idx = _find_real_header_row(rows)
                if header_idx == -1:
                    continue
                table_bbox_top = tbl.bbox[1] if hasattr(tbl, "bbox") else 0
                if header_idx > 0:
                    rows = rows[header_idx:]
                    if len(rows) < 2:
                        continue
                    # Shrink the bbox top to the kept rows' top so the layout
                    # prose above appears in regular text instead of being
                    # filtered out as "inside the table region".
                    try:
                        kept_top = tbl.rows[header_idx].bbox[1]
                        if kept_top > table_bbox_top:
                            table_bbox_top = kept_top
                    except (AttributeError, IndexError):
                        pass

                non_blank = sum(1 for r in rows for c in r if (c or "").strip())
                if non_blank < 3:
                    continue
                body = rows[1:] if len(rows) > 1 else rows
                if body:
                    body_cells = [c for r in body for c in r]
                    body_blank = sum(1 for c in body_cells if not (c or "").strip())
                    if body_cells and body_blank / len(body_cells) > 0.5:
                        continue

                merged = False
                if tables:
                    prev_rows = tables[-1]["rows"]
                    if prev_rows and len(prev_rows[0]) == len(rows[0]):
                        if prev_rows[0] == rows[0]:
                            tables[-1]["rows"].extend(rows[1:])
                            tables[-1]["rows"] = _stitch_wrapped_cells(tables[-1]["rows"])
                            merged = True
                        elif _looks_like_continuation(prev_rows, rows):
                            tables[-1]["rows"].extend(rows)
                            tables[-1]["rows"] = _stitch_wrapped_cells(tables[-1]["rows"])
                            merged = True
                # Build the bbox we'll use for char filtering — substitutes
                # the trimmed-table's top so layout prose above the real
                # data isn't pulled into the table region.
                if hasattr(tbl, "bbox"):
                    bx0, _bt, bx1, bb = tbl.bbox
                    use_bbox = (bx0, table_bbox_top, bx1, bb)
                else:
                    use_bbox = None

                if merged:
                    if use_bbox is not None:
                        accepted_bboxes.append(use_bbox)
                    continue

                idx = len(tables)
                tables.append({"rows": _stitch_wrapped_cells(rows)})
                page_tables.append((table_bbox_top, f"[TABLE:{idx}]"))
                if use_bbox is not None:
                    accepted_bboxes.append(use_bbox)

            # Extract text with characters inside accepted table bboxes removed,
            # so the prose version of "A 100% / A- 92%..." doesn't duplicate
            # what the rendered HTML table will show.
            #
            # The bbox is shrunk inward by a small margin before testing
            # containment. pdfplumber's table bboxes often include a few
            # points of padding above the visible grid (where rule lines
            # extend), so a section heading sitting just above the table can
            # have its baseline inside the raw bbox and disappear from the
            # prose. Margin keeps headings safely on the outside.
            BBOX_TOP_MARGIN = 6.0   # shrink top edge by this many points
            BBOX_BOT_MARGIN = 3.0
            BBOX_X_MARGIN = 2.0
            try:
                if accepted_bboxes:
                    shrunk = [
                        (x0 + BBOX_X_MARGIN, t0 + BBOX_TOP_MARGIN,
                         x1 - BBOX_X_MARGIN, b1 - BBOX_BOT_MARGIN)
                        for (x0, t0, x1, b1) in accepted_bboxes
                    ]

                    def _outside_tables(obj: dict) -> bool:
                        x = (obj.get("x0", 0) + obj.get("x1", 0)) / 2
                        y = (obj.get("top", 0) + obj.get("bottom", 0)) / 2
                        for x0, t0, x1, b1 in shrunk:
                            if t0 >= b1 or x0 >= x1:
                                continue  # bbox shrunk past itself; skip
                            if x0 <= x <= x1 and t0 <= y <= b1:
                                return False
                        return True
                    text_source = source.filter(_outside_tables)
                else:
                    text_source = source
                text = text_source.extract_text() or ""
            except Exception:
                text = page.extract_text() or ""

            # Inject markers in document order. Without per-line y-positions we
            # just append markers to the end of the page in their detected
            # order, separated by blank lines so Grok treats them as their own
            # paragraph. Good enough for syllabus tables (they typically sit
            # alone on a page or at the end of a section).
            page_tables.sort(key=lambda t: t[0])
            page_text = text.strip()
            if page_tables:
                marker_block = "\n\n".join(m for _, m in page_tables)
                page_text = (page_text + "\n\n" + marker_block).strip() if page_text else marker_block
            if page_text:
                chunks.append(page_text)

    log.info(
        "extract_pdf_text: %d of %d pages had text, %d tables found (%s)",
        len(chunks), total, len(tables), path.name,
    )
    return "\n\n".join(chunks), tables


SYLLABUS_SYSTEM_PROMPT = """You are a focused extractor for university course syllabi. The user does the heavy curation themselves by highlighting passages on a separate page; your job is narrow.

INPUT: the full text of a syllabus.

OUTPUT: a single JSON object matching the enforced schema. No preamble, commentary, or markdown.

# course_code
UPPERCASE with one space between department prefix and number ("math 250" → "MATH 250", "CIS3950" → "CIS 3950"). Strip section/lab modifiers. Null if absent.

# course_name
Full course title as listed in the header. Don't include the course code, section, or term. Prefer the expanded title over a short one.

# events
Dated milestones the student needs on a calendar: exams, assignment/project due dates, presentation slots, drop deadlines. Each: {title, kind, starts_at, ends_at, source_text}.

- kind: one of `exam`, `assignment`, `project`, `milestone`.
- starts_at: naive ISO 8601 ("2026-09-15T18:00:00"). Date only → use 23:59:00 of that day.
- ends_at: only when the syllabus gives an explicit duration ("Final Exam: Dec 14, 8-10am" → starts_at 08:00, ends_at 10:00); else null.
- source_text: short verbatim quote (≤ 240 chars) from the syllabus showing where this came from. Null if you derived it.

DATE HANDLING — STRICT:
- DO NOT INVENT DATES. "Week 5 — Midterm" with no explicit date → starts_at: null, put the descriptor in the title.
- Skip recurring weekly work ("PSet due every Friday"). Capture only specific dated milestones.
- Ignore syllabus-stated time zones; emit naive datetimes.

# Example

INPUT (excerpted): "CS 101 - Intro to Programming. Spring 2026. Midterm Exam: March 15, 2026 at 6:00pm. Final Project Due: May 7, 2026. Final Project Presentations: May 10, 2026, 9am-12pm."

OUTPUT:
{
  "course_code": "CS 101",
  "course_name": "Intro to Programming",
  "events": [
    {"title": "Midterm Exam", "kind": "exam", "starts_at": "2026-03-15T18:00:00", "ends_at": null, "source_text": "Midterm Exam: March 15, 2026 at 6:00pm"},
    {"title": "Final Project Due", "kind": "project", "starts_at": "2026-05-07T23:59:00", "ends_at": null, "source_text": "Final Project Due: May 7, 2026"},
    {"title": "Final Project Presentations", "kind": "project", "starts_at": "2026-05-10T09:00:00", "ends_at": "2026-05-10T12:00:00", "source_text": "Final Project Presentations: May 10, 2026, 9am-12pm"}
  ]
}

When uncertain, prefer null over inventing."""


# Pydantic models for upload-time extraction. Now narrow: only course identity
# and dated events (the calendar feed needs structured dates). Everything else
# the student cares about is captured per-passage via the highlight UI.

EventKind = Literal["exam", "assignment", "project", "milestone"]


class EventItem(BaseModel):
    title: str
    kind: EventKind
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
    today_start = _today_local()
    today_end = today_start + timedelta(days=1)
    today_items = _collect_items_in_range(today_start, today_end)
    overdue = _collect_overdue()
    with Session(engine, expire_on_commit=False) as session:
        classes = session.exec(select(Class).order_by(Class.code)).all()
    return templates.TemplateResponse(request, "home.html", {
        "classes": classes,
        "today": today_start,
        "today_items": today_items,
        "overdue": overdue,
        "default_class_id": (classes[0].id if classes else None),
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
    if STUDYFLOW_TOKEN and not has_valid_cookie(request):
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
        raw_text, tables = extract_pdf_text(upload_path)
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
            tables_json=json.dumps(tables) if tables else None,
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


# ---- Helpers shared by PDF table marker rendering ----

MAX_PASSAGE_CHARS = 16000  # rough cap on what we send to Grok per request

import re as _re

_TABLE_MARKER_RE = _re.compile(r"\[TABLE:(\d+)\]")


# ---- Routes: PDF viewer + AI transform sandbox ----


def _format_tables_for_cross_reference(tables_json: Optional[str]) -> str:
    """Render a syllabus's pdfplumber-detected tables as markdown blocks
    suitable for inclusion in a Grok prompt. Returns empty string if
    there are no tables. Capped so very large syllabi don't blow the
    context window."""
    if not tables_json:
        return ""
    try:
        tables = json.loads(tables_json)
    except (ValueError, TypeError):
        return ""
    if not isinstance(tables, list) or not tables:
        return ""
    blocks: list[str] = []
    budget = MAX_PASSAGE_CHARS // 2  # leave room for user's highlight
    used = 0
    for i, t in enumerate(tables):
        rows = (t or {}).get("rows") if isinstance(t, dict) else None
        if not rows:
            continue
        block_lines = [f"\nTable {i}:"]
        for r in rows:
            cells = [(c or "").strip().replace("\n", " ") for c in r]
            block_lines.append("| " + " | ".join(cells) + " |")
        block = "\n".join(block_lines)
        if used + len(block) > budget:
            break
        blocks.append(block)
        used += len(block)
    if not blocks:
        return ""
    return ("\n\nSTRUCTURED TABLES (cross-reference, detected by automated parser):"
            + "".join(blocks))


TEST_TRANSFORM_SYSTEM_PROMPT = """You transform a snippet of text the user highlighted from their syllabus, following their instruction.

OUTPUT: markdown only. No preamble like "Here's the transformed text:".

==============================================================
CRITICAL — STRUCTURED TABLES ARE AUTHORITATIVE
==============================================================
The user message may include a section labelled "STRUCTURED TABLES (cross-reference)". These were extracted from the same PDF by a deterministic table parser. When one of them matches the user's highlight, it IS the answer — your job is to print it, not to rebuild it.

BEFORE responding, do this check:
1. Scan each structured table.
2. Does any structured table share content with the highlighted text? (matching column headers, or row labels appearing in both)
3. If YES — that table IS the answer the user wants. Output it as-is in markdown pipe-table form:
   - SAME column count. Do not merge or split columns.
   - SAME column names, in the SAME order.
   - EVERY row from the structured table, in the SAME order, no rows added or dropped.
   - EVERY cell value character-for-character. Do not abbreviate ("< 95" stays "< 95"), do not round, do not "clean up" punctuation.
   - The user's instruction ("clean", "no colors", "simple", "make a table") is satisfied automatically by markdown format. They want different APPEARANCE, not different DATA.
4. If NO structured table matches — ignore the cross-reference entirely and work only from the highlighted text below.

==============================================================
GENERAL RULES (apply when no structured table matches)
==============================================================
- Follow the user's instruction faithfully. Examples: "simplify this", "make a clean table", "bullet list", "summarize in 3 lines".
- DO NOT invent content. If the snippet doesn't contain what the user asks about, say so in one sentence.
- For tables, use markdown pipe-table syntax (`| col | col |\n|---|---|`).
- Preserve EXACTLY: names, emails, phone numbers, room numbers, dates, times, percentages, dollar amounts, URLs.
- Strip noise like "Page X of Y", repeated headers, or copy-paste artifacts.
- Output the transformed content only — no commentary.
"""


@app.post("/test/transform", dependencies=[Depends(require_token)])
async def test_transform(request: Request):
    """Sandbox endpoint: take user-selected text + an instruction,
    return markdown. No persistence. If `syllabus_id` is provided, the
    syllabus's pdfplumber-detected tables are sent as cross-reference
    so Grok can fill in cells the browser's copy missed."""
    payload = await request.json()
    text = (payload.get("text") or "").strip()
    prompt = (payload.get("prompt") or "").strip()
    raw_sid = payload.get("syllabus_id")
    if not text:
        raise HTTPException(400, "Selected text is empty")
    if not prompt:
        raise HTTPException(400, "Instruction is required")
    if len(text) > MAX_PASSAGE_CHARS:
        text = text[:MAX_PASSAGE_CHARS]

    cross_ref = ""
    if raw_sid is not None:
        try:
            sid = int(raw_sid)
        except (TypeError, ValueError):
            sid = None
        if sid is not None:
            with Session(engine) as session:
                syllabus = session.get(Syllabus, sid)
                if syllabus:
                    cross_ref = _format_tables_for_cross_reference(syllabus.tables_json)

    api_key = os.environ.get("XAI_API_KEY", "").strip()
    if not api_key:
        raise HTTPException(500, "XAI_API_KEY is not set")
    try:
        client = XAIClient(api_key=api_key)
        chat = client.chat.create(
            model=XAI_MODEL,
            messages=[xai_system(TEST_TRANSFORM_SYSTEM_PROMPT)],
        )
        chat.append(xai_user(
            f"Instruction: {prompt}\n\n---\n\nHighlighted text:\n{text}{cross_ref}"
        ))
        response = chat.sample()
        out = (response.content or "").strip()
        if not out:
            raise HTTPException(502, "Grok returned an empty response")
    except grpc.RpcError as e:
        raise HTTPException(502, f"Grok call failed: {_grpc_error_message(e)}")

    rendered = _render_text_with_tables(out, None)
    return JSONResponse({
        "markdown": out,
        "html": str(rendered),
        "cross_reference_used": bool(cross_ref),
    })


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


@app.post("/classes/{class_id}/tasks", dependencies=[Depends(require_token)])
async def create_task(class_id: int, request: Request,
                      title: str = Form(...), due_at: str = Form("")):
    """Create a manual task on a class. Form-post adds via the class sidebar;
    AJAX clients get JSON, plain forms get a redirect (preserves no-JS path)."""
    title = title.strip()
    if not title:
        raise HTTPException(400, "Title required")
    due_dt = parse_iso_dt(due_at) if due_at else None
    with Session(engine) as session:
        cls = session.get(Class, class_id)
        if not cls:
            raise HTTPException(404, "Class not found")
        task = Task(
            class_id=class_id,
            title=title,
            due_at=due_dt,
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
                    title: str = Form(...), due_at: str = Form("")):
    """Update title and/or due_at on an existing task. AJAX returns JSON."""
    title = title.strip()
    if not title:
        raise HTTPException(400, "Title required")
    due_dt = parse_iso_dt(due_at) if due_at else None
    with Session(engine) as session:
        t = session.get(Task, task_id)
        if not t:
            raise HTTPException(404)
        t.title = title
        t.due_at = due_dt
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


@app.post("/tasks/reorder", dependencies=[Depends(require_token)])
async def reorder_tasks(request: Request):
    """Persist drag-to-reorder priority. Body: {task_ids: [...]} — order in
    list becomes position 0..N-1 for the given tasks. IDs not in the body
    are left alone."""
    payload = await request.json()
    raw_ids = payload.get("task_ids") or []
    if not isinstance(raw_ids, list):
        raise HTTPException(400, "task_ids must be a list")
    try:
        task_ids = [int(i) for i in raw_ids]
    except (TypeError, ValueError):
        raise HTTPException(400, "task_ids entries must be integers")
    with Session(engine) as session:
        updated = 0
        for pos, tid in enumerate(task_ids):
            t = session.get(Task, tid)
            if t is None:
                continue
            t.position = pos
            session.add(t)
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
             position: int = 0, sub_kind: Optional[str] = None):
        slot = out.setdefault(cls.id, {"cls": cls, "items": []})
        slot["items"].append({
            "kind": kind,
            "sub_kind": sub_kind,
            "id": item_id,
            "class_id": cls.id,
            "title": title,
            "due_at": when,
            "completed": completed,
            "position": position,
        })

    with Session(engine, expire_on_commit=False) as session:
        for cls in session.exec(select(Class)).all():
            for t in cls.tasks:
                if t.due_at is None:
                    # No due date — show on today only if uncompleted (acts
                    # like an open backlog item)
                    if start <= _today_local() < end and not t.completed_at:
                        _add(cls, "task", t.id, t.title, None, False, t.position or 0)
                    continue
                local_due = _to_local(t.due_at)
                if start <= local_due < end:
                    _add(cls, "task", t.id, t.title, local_due,
                         t.completed_at is not None, t.position or 0)
            for ev in cls.events:
                if ev.starts_at is None:
                    continue
                local_when = _to_local(ev.starts_at)
                if start <= local_when < end:
                    _add(cls, "event", ev.id, ev.title, local_when,
                         ev.completed_at is not None, 0, sub_kind=ev.kind)
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
        for cls in session.exec(select(Class)).all():
            for t in cls.tasks:
                if t.completed_at:
                    continue
                if t.due_at is None:
                    continue
                local_due = _to_local(t.due_at)
                if local_due < now and local_due >= now - timedelta(days=30):
                    out.setdefault(cls.id, {"cls": cls, "items": []})["items"].append({
                        "kind": "task", "sub_kind": None, "id": t.id,
                        "class_id": cls.id, "title": t.title,
                        "due_at": local_due, "completed": False,
                        "position": t.position or 0,
                    })
            for ev in cls.events:
                if ev.completed_at:
                    continue
                if ev.starts_at is None:
                    continue
                local_when = _to_local(ev.starts_at)
                if local_when < now and local_when >= now - timedelta(days=30):
                    out.setdefault(cls.id, {"cls": cls, "items": []})["items"].append({
                        "kind": "event", "sub_kind": ev.kind, "id": ev.id,
                        "class_id": cls.id, "title": ev.title,
                        "due_at": local_when, "completed": False,
                        "position": 0,
                    })
    for slot in out.values():
        slot["items"].sort(key=lambda it: (it["position"], it["due_at"] or datetime.max.replace(tzinfo=LOCAL_TZ)))
    return out


@app.get("/today", response_class=HTMLResponse)
def today_view(request: Request):
    """Tasks and events due today, plus anything overdue."""
    if STUDYFLOW_TOKEN and not has_valid_cookie(request):
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
def week_view(request: Request):
    """Next two weeks' items, grouped by day → class."""
    if STUDYFLOW_TOKEN and not has_valid_cookie(request):
        return RedirectResponse("/setup-token", status_code=303)
    today_start = _today_local()
    # Two-week window starts on this week's Monday.
    monday = today_start - timedelta(days=today_start.weekday())
    days = []
    for i in range(14):
        day_start = monday + timedelta(days=i)
        day_end = day_start + timedelta(days=1)
        items_by_class = _collect_items_in_range(day_start, day_end)
        days.append({
            "date": day_start,
            "is_today": day_start == today_start,
            "items_by_class": items_by_class,
        })
    return templates.TemplateResponse(request, "week.html", {
        "monday": monday,
        "days": days,
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
