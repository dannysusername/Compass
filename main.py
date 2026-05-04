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
    cards: List["Card"] = Relationship(
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


class Policy(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    class_id: int = Field(foreign_key="class.id")
    kind: str  # late_policy | grading | office_hours | attendance_policy | instructor
    content: str
    source_text: Optional[str] = Field(default=None)
    cls: Optional[Class] = Relationship(back_populates="policies")


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


class Card(SQLModel, table=True):
    """User-curated snippet from the syllabus. kind=quote keeps the verbatim
    highlighted text; kind=summary stores Grok's summarized version with the
    original passage retained in original_text for verification.

    Both text fields may contain [TABLE:N] markers that index into tables_json
    (a JSON list of {rows: [[cell,...],...]}). Each card snapshots only the
    tables it actually references, with markers renumbered 0..M-1."""
    id: Optional[int] = Field(default=None, primary_key=True)
    class_id: int = Field(foreign_key="class.id")
    kind: str  # "quote" | "summary" — aggregate (summary if any section was summarized)
    label: Optional[str] = Field(default=None)
    original_text: str
    display_text: str
    tables_json: Optional[str] = Field(default=None)
    sections_meta_json: Optional[str] = Field(default=None)  # [{heading, kind}, ...]
    tailor_prompt: Optional[str] = Field(default=None)  # user instructions for tailor sections
    position: int = Field(default=0)  # user-defined order; ties broken by created_at
    created_at: datetime
    cls: Optional[Class] = Relationship(back_populates="cards")


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


def _split_card_sections(display_text: Optional[str]) -> list[dict]:
    """Split a card's display_text into a list of {heading, body} sections,
    using line-start `## Heading` markers as boundaries. Text before the
    first `## ` becomes a section with heading=None (preamble).

    Used by the class page to render each section in its own collapsible
    block instead of one undifferentiated blob."""
    if not display_text:
        return []
    text = display_text.replace("\r\n", "\n")
    parts = _re.split(r"(?:^|\n)## (.+?)(?:\n|$)", text)
    out: list[dict] = []
    if parts and parts[0].strip():
        out.append({"heading": None, "body": parts[0].strip()})
    for i in range(1, len(parts), 2):
        heading = parts[i].strip()
        body = parts[i + 1].strip() if i + 1 < len(parts) else ""
        out.append({"heading": heading, "body": body})
    return out


templates.env.filters["split_card_sections"] = _split_card_sections


def _section_kinds_for_card(card: "Card") -> dict:
    """Return {heading: 'summary' | 'verbatim'} for each section in a card.

    Prefer the stored sections_meta_json (deterministic, populated for all
    cards created after this column was added). Fall back to comparing the
    section body in display_text against original_text — if they're identical
    (whitespace-normalized), the section was kept verbatim; else summarized.
    Used by the class page to show a per-section badge."""
    if card.sections_meta_json:
        try:
            meta = json.loads(card.sections_meta_json)
            out = {}
            for m in meta:
                heading = m.get("heading")
                kind = m.get("kind")
                # Back-compat: old "summary" label is now "tailor"
                if kind == "summary":
                    kind = "tailor"
                if heading and kind in ("tailor", "verbatim"):
                    out[heading] = kind
            return out
        except (ValueError, TypeError):
            pass

    # Fallback: derive by comparing display vs original section bodies
    def _norm(s: str) -> str:
        return _re.sub(r"\s+", " ", (s or "").strip()).lower()

    display_secs = {s["heading"]: _norm(s["body"]) for s in _split_card_sections(card.display_text) if s.get("heading")}
    original_secs = {s["heading"]: _norm(s["body"]) for s in _split_card_sections(card.original_text) if s.get("heading")}
    out: dict[str, str] = {}
    for heading, dbody in display_secs.items():
        obody = original_secs.get(heading)
        if obody is None:
            out[heading] = "tailor"  # only in display → must have been generated
        elif dbody == obody:
            out[heading] = "verbatim"
        else:
            out[heading] = "tailor"
    return out


templates.env.filters["section_kinds_for_card"] = _section_kinds_for_card


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
outline_jobs: dict[int, str] = {}


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


SUMMARIZE_SYSTEM_PROMPT = """You summarize highlighted syllabus passages for students. Your output renders as Markdown — use it.

PRESERVE EXACTLY (never paraphrase): names, email addresses, phone numbers, room numbers, building names, dates, times, percentages, dollar amounts, URLs, deadlines.

DROP: filler ("It is important to note that..."), restated obvious context, generic university policy boilerplate the student already knows.

OUTPUT FORMAT — match the structural shape of the input:
- If the input is bullet-listed, output as a bullet list (`- item`). Keep one item per concept; collapse repetitive items.
- If the input has multiple paragraphs, output as multiple paragraphs separated by blank lines.
- If the input has section sub-headings (e.g. `## Heading` from joined sections), keep the headings and summarize each subsection under its heading.
- If the input is one short paragraph, output one paragraph.
- For combined multi-section inputs: keep the `## Heading` between sections so they stay visually separated.

Use `**bold**` for the most important terms (deadlines, dollar amounts, key contact info). Don't over-bold.

If a passage is already short and concrete (under ~25 words), return it nearly verbatim — don't pad it.

TABLE MARKERS: the input may contain bracketed tokens like `[TABLE:0]`, `[TABLE:3]`. These are placeholders for tables.
- If the table is the substantive content (a grading breakdown, a rubric, a schedule), KEEP the marker on its own line with blank lines around it. The table will render there.
- If the table is incidental (the prose already summarizes it), drop the marker.
- Never rename, renumber, or describe the marker."""


def summarize_passage(text: str) -> str:
    """Call Grok to summarize a highlighted syllabus passage. Returns plain text."""
    api_key = os.environ.get("XAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("XAI_API_KEY is not set.")
    client = XAIClient(api_key=api_key)
    chat = client.chat.create(
        model=XAI_MODEL,
        messages=[xai_system(SUMMARIZE_SYSTEM_PROMPT)],
    )
    chat.append(xai_user(text))
    response = chat.sample()
    out = (response.content or "").strip()
    if not out:
        raise RuntimeError("Grok returned empty summary")
    return out


def tailor_passage(text: str, user_prompt: str) -> str:
    """Call Grok with the user's custom instructions to process a passage.
    Used by the +Tailor button — lets the user ask for whatever shape they
    want (simpler version, action items, contact-only, etc.)."""
    api_key = os.environ.get("XAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("XAI_API_KEY is not set.")
    user_prompt = (user_prompt or "").strip()
    if not user_prompt:
        raise RuntimeError("Tailor prompt is empty.")
    client = XAIClient(api_key=api_key)
    chat = client.chat.create(
        model=XAI_MODEL,
        messages=[xai_system(TAILOR_SYSTEM_PROMPT)],
    )
    chat.append(xai_user(f"Instructions: {user_prompt}\n\n---\n\n{text}"))
    response = chat.sample()
    out = (response.content or "").strip()
    if not out:
        raise RuntimeError("Grok returned empty tailor response")
    return out


TAILOR_SYSTEM_PROMPT = """You process a syllabus passage according to the user's instructions. The user is a college student curating their own study reference.

THE USER'S INSTRUCTIONS will arrive in the user message, prefixed with "Instructions:". Follow them faithfully — that's the whole job. Examples of instructions you might see:
- "What do I need to do to prepare for class?"
- "Make this simpler"
- "List only the most important points"
- "Keep just the contact info and office hours"
- "Pull out anything I should add to my calendar"

CONSTRAINTS that always apply, regardless of the user's instructions:
- PRESERVE EXACTLY (never paraphrase): names, email addresses, phone numbers, room numbers, building names, dates, times, percentages, dollar amounts, URLs, deadlines.
- Output Markdown. Use bullet lists, paragraphs, and headings as appropriate to the content.
- Use `**bold**` to highlight the most important terms (deadlines, dollar amounts, key contact info).
- TABLE MARKERS like `[TABLE:0]`, `[TABLE:3]` in the passage are placeholders for tables. If the table contains substantive content the user would want, KEEP the marker on its own line (with blank lines around it). If the user's instructions clearly don't need the table, drop it. Never describe, rename, or renumber a marker.
- If the passage starts with a `## Heading`, keep that heading at the top of your output so the section's identity is preserved.

Plain text + Markdown only. No preamble like "Here's the tailored content:" — just the result."""


EXTRACT_EVENT_SYSTEM_PROMPT = """Extract a single calendar event from the highlighted syllabus passage.

Return: {title, kind, starts_at, ends_at}.
- kind: one of `exam`, `assignment`, `project`, `milestone`.
- title: short label (e.g. "Midterm Exam", "Project 2 Due").
- starts_at: naive ISO 8601 datetime ("2026-09-15T18:00:00"). Date only → 23:59:00. If no explicit calendar date, return null.
- ends_at: only when the passage gives an explicit end time; else null.
- DO NOT INVENT DATES. If the passage references a week number with no calendar date, set starts_at: null.
"""


class SinglePassageEvent(BaseModel):
    title: str
    kind: EventKind
    starts_at: Optional[str] = None
    ends_at: Optional[str] = None


OUTLINE_SYSTEM_PROMPT = """You parse a syllabus into a hierarchical outline of its sections.

INPUT: the full text of a course syllabus (typically from a Simple Syllabus template — Course Description, Office Hours, Late Work, AI Policy, Schedule, etc.).

OUTPUT: a flat list of sections in document order. Each section has:
- heading: the literal heading text as it appears (e.g. "Course Description", "Late Work Policy", "Required Materials"). Strip trailing colons. Title-case if the original is ALL CAPS.
- level: 1 for top-level headings, 2 for subheadings under a level-1, 3 for sub-subheadings.
- body: the prose/content under that heading, up to but NOT including the next heading. Trim leading/trailing whitespace. If the section has only subheadings (no direct prose), set body to an empty string.

RULES:
- Identify ALL headings, even short ones. Headings are typically: short (≤ 80 chars), on their own line, often bold or all-caps in the original (you'll see them as plain text but the surrounding structure makes them obvious — short line surrounded by blank lines or followed by a colon, then prose).
- Do NOT invent headings. If the document is one big paragraph with no structure, return a single section with heading="(Unstructured content)" and the full text as body.
- KEEP every heading you see, including schedule-like headings such as "Schedule", "Canvas Schedule", "Course Calendar", "Schedule of Topics", "Important Dates". A user-relevant heading is a user-relevant heading; do not drop it because it sounds like a schedule.
- For schedule sections specifically: do NOT enumerate individual dated events in the body (those are extracted separately into a calendar). Instead, keep the body terse — typically just the `[TABLE:N]` marker that represents the schedule's table, plus any introductory prose. If there is no table marker, leave the body empty (we'll skip rendering the body but still surface the heading).
- Do NOT include filler page-number lines (e.g. "Page 9 of 1544"), headers/footers, or table-of-contents entries.
- Preserve the document order in the returned list (don't reorder by importance).

BODY FORMATTING — emit Markdown. The body will be rendered as HTML with bullet lists, paragraphs, and headings. Specifically:
- If the source uses bullets (•, ▪, ◦, –, *, "○") convert them to `- ` at the start of each line. Each bullet on its own line.
- If the source uses numbered lists ("1.", "2)") preserve those.
- Separate paragraphs with one blank line.
- Keep sub-headings inside the body (e.g. `**Required:**` or `### Recommended Texts`) — they help structure long sections.
- Don't smash bullet lists into running prose. A 5-item bulleted list in the source must come out as 5 bullet lines in the body.

TABLE MARKERS: the input may contain bracketed tokens like `[TABLE:0]`, `[TABLE:7]`. These are placeholders for tables that will be rendered as real HTML tables later.
- Preserve them VERBATIM in the body of whichever section they belong to. Do not rename, renumber, omit, or describe them.
- Place each marker on its OWN LINE with a blank line before and after, so the rendered table appears as its own block (not glued into a paragraph).
- CRITICAL: every `[TABLE:N]` marker that appears in the input MUST appear in exactly one section's body in your output. If you're unsure which section, attach it to the nearest preceding heading. Never drop a marker — even on schedule sections.

EXAMPLE INPUT (excerpt):
"COURSE DESCRIPTION
This course introduces students to...

INSTRUCTOR INFORMATION
Dr. Jane Doe
Office: Room 207
Email: jane@uni.edu

  Office Hours
  Tuesday 2-4pm

GRADING
- Homework: 30%
- Midterm: 30%
- Final: 40%"

EXAMPLE OUTPUT (sections):
[
  {"heading": "Course Description", "level": 1, "body": "This course introduces students to..."},
  {"heading": "Instructor Information", "level": 1, "body": "**Dr. Jane Doe**\\n\\n- Office: Room 207\\n- Email: jane@uni.edu\\n- Phone: 555-1234"},
  {"heading": "Office Hours", "level": 2, "body": "Tuesday 2-4pm"},
  {"heading": "Grading", "level": 1, "body": "Course grades follow the breakdown below.\\n\\n[TABLE:3]\\n\\nLetter grades use the standard FIU scale.\\n\\n[TABLE:4]"}
]
"""


class OutlineSection(BaseModel):
    heading: str
    level: Literal[1, 2, 3]
    body: str


class Outline(BaseModel):
    sections: List[OutlineSection] = PydanticField(default_factory=list)


def extract_outline(text: str) -> Outline:
    """Pass 1.5: ask Grok for the syllabus outline (headings + bodies). Cached
    on the Syllabus row so subsequent /sections visits don't re-call."""
    api_key = os.environ.get("XAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("XAI_API_KEY is not set.")
    client = XAIClient(api_key=api_key)
    chat = client.chat.create(
        model=XAI_MODEL,
        messages=[xai_system(OUTLINE_SYSTEM_PROMPT)],
    )
    chat.append(xai_user(text))
    response, parsed = chat.parse(Outline)
    if parsed is None:
        raise RuntimeError("Grok returned no outline")
    return parsed


def extract_event_from_passage(text: str) -> SinglePassageEvent:
    """Call Grok to pull a single event out of a highlighted passage."""
    api_key = os.environ.get("XAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("XAI_API_KEY is not set.")
    client = XAIClient(api_key=api_key)
    chat = client.chat.create(
        model=XAI_MODEL,
        messages=[xai_system(EXTRACT_EVENT_SYSTEM_PROMPT)],
    )
    chat.append(xai_user(text))
    response, parsed = chat.parse(SinglePassageEvent)
    if parsed is None:
        raise RuntimeError("Grok returned no event")
    return parsed


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
    """Background task: run BOTH Grok extractions (events + section outline) in
    parallel, then write everything to the DB. Pre-caching the outline here
    means the /sections page is instant when the user clicks Continue.

    The two calls are independent and run on separate threads — total wait
    time is max(events, outline) instead of the sum."""
    from concurrent.futures import ThreadPoolExecutor
    parse_jobs[syllabus_id] = "running"
    try:
        with Session(engine) as session:
            syllabus = session.get(Syllabus, syllabus_id)
            if not syllabus:
                parse_jobs[syllabus_id] = "error: syllabus not found"
                return
            raw_text = syllabus.raw_text

        # Both Grok calls happen WITHOUT a session held — sessions aren't
        # thread-safe and the calls take 5-30s each.
        with ThreadPoolExecutor(max_workers=2) as ex:
            events_future = ex.submit(parse_syllabus_with_grok, raw_text)
            outline_future = ex.submit(extract_outline, raw_text)

            try:
                data = events_future.result()
            except grpc.RpcError as e:
                parse_jobs[syllabus_id] = f"error: {_grpc_error_message(e)}"
                return
            except json.JSONDecodeError as e:
                parse_jobs[syllabus_id] = f"error: Grok output was not valid JSON. {str(e)[:200]}"
                return
            except RuntimeError as e:
                parse_jobs[syllabus_id] = f"error: {e}"
                return

            # Outline extraction failure is non-fatal — the lazy fallback in
            # the /sections route will retry on first visit. We log but don't
            # block "done".
            outline_dump: Optional[list] = None
            try:
                outline = outline_future.result()
                outline_dump = [s.model_dump() for s in outline.sections]
            except Exception as oe:
                log.warning("outline pre-extract failed for syllabus %d: %s", syllabus_id, oe)

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
            # User-curated cards are preserved (the user owns those).
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

            if outline_dump is not None:
                syllabus.outline_json = json.dumps(outline_dump)
                session.add(syllabus)

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
        cards = sorted(cls.cards, key=lambda c: ((c.position or 0), c.created_at))
        latest_syllabus = max(cls.syllabi, key=lambda s: s.parsed_at) if cls.syllabi else None
    # Sidebar shows TODAY's tasks across ALL classes, identical to the home
    # page's todo list. Add-task form defaults to the current class.
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
        "cards": cards,
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


# ---- Routes: Sections picker + Cards ----

MAX_PASSAGE_CHARS = 16000  # rough cap on what we send to Grok per card

import re as _re

_TABLE_MARKER_RE = _re.compile(r"\[TABLE:(\d+)\]")


def _snapshot_tables(text: str, source_tables: list) -> tuple[str, list]:
    """Find every [TABLE:N] in text. Build a renumbered subset of source_tables
    containing only the referenced ones, and rewrite the markers in text to
    point at the new compact indices. Returns (new_text, snapshot_list).

    Cards store this snapshot so they survive even if the parent syllabus is
    deleted or its tables_json shifts."""
    if not source_tables:
        return text, []
    seen: dict[int, int] = {}
    snapshot: list = []

    def _remap(m: "_re.Match[str]") -> str:
        old = int(m.group(1))
        if old < 0 or old >= len(source_tables):
            return m.group(0)  # leave broken marker as-is
        if old not in seen:
            seen[old] = len(snapshot)
            snapshot.append(source_tables[old])
        return f"[TABLE:{seen[old]}]"

    new_text = _TABLE_MARKER_RE.sub(_remap, text)
    return new_text, snapshot


def _extract_outline_into_syllabus(syllabus_id: int) -> None:
    """Background task: run extract_outline + persist to the Syllabus row.
    Updates outline_jobs so the polling page knows when to reload."""
    outline_jobs[syllabus_id] = "running"
    try:
        with Session(engine) as session:
            syllabus = session.get(Syllabus, syllabus_id)
            if not syllabus:
                outline_jobs[syllabus_id] = "error: syllabus not found"
                return
            raw_text = syllabus.raw_text
        outline = extract_outline(raw_text)
        with Session(engine) as session:
            syllabus = session.get(Syllabus, syllabus_id)
            if not syllabus:
                outline_jobs[syllabus_id] = "error: syllabus deleted during extraction"
                return
            syllabus.outline_json = json.dumps([s.model_dump() for s in outline.sections])
            session.add(syllabus)
            session.commit()
        outline_jobs[syllabus_id] = "done"
    except grpc.RpcError as e:
        outline_jobs[syllabus_id] = f"error: {_grpc_error_message(e)}"
    except Exception as e:
        outline_jobs[syllabus_id] = f"error: {type(e).__name__}: {str(e)[:240]}"


@app.get("/syllabus/{syllabus_id}/sections", response_class=HTMLResponse)
def sections_page(request: Request, syllabus_id: int, background_tasks: BackgroundTasks):
    """Show the section picker. If the outline isn't cached yet, render a
    loading state that triggers extraction in the background and polls for
    completion — keeps the user informed instead of hanging on a blank page."""
    if STUDYFLOW_TOKEN and not has_valid_cookie(request):
        return RedirectResponse("/setup-token", status_code=303)
    with Session(engine, expire_on_commit=False) as session:
        syllabus = session.get(Syllabus, syllabus_id)
        if not syllabus:
            raise HTTPException(404, "Syllabus not found")
        cls = session.get(Class, syllabus.class_id)
        sections: list[dict] = []
        if syllabus.outline_json:
            try:
                sections = json.loads(syllabus.outline_json)
            except (ValueError, TypeError):
                sections = []
        existing_cards = sorted(cls.cards, key=lambda c: c.created_at)

    if not sections:
        # Kick off background extraction if not already running, then render
        # the loading template that polls /outline-status.json.
        if outline_jobs.get(syllabus_id) != "running":
            outline_jobs[syllabus_id] = "running"
            background_tasks.add_task(_extract_outline_into_syllabus, syllabus_id)
        return templates.TemplateResponse(request, "sections.html", {
            "cls": cls,
            "syllabus": syllabus,
            "sections": [],
            "existing_cards": existing_cards,
            "extracting": True,
        })

    return templates.TemplateResponse(request, "sections.html", {
        "cls": cls,
        "syllabus": syllabus,
        "sections": sections,
        "existing_cards": existing_cards,
        "extracting": False,
    })


@app.get("/syllabus/{syllabus_id}/outline-status.json")
def outline_status_json(syllabus_id: int):
    """Polled by the loading state of the section picker. Returns whether the
    outline is ready (or what error fired)."""
    with Session(engine) as session:
        syllabus = session.get(Syllabus, syllabus_id)
        if not syllabus:
            outline_jobs.pop(syllabus_id, None)
            return JSONResponse({"status": "missing"})
        if syllabus.outline_json:
            outline_jobs.pop(syllabus_id, None)
            return JSONResponse({"status": "done"})
    return JSONResponse({"status": outline_jobs.get(syllabus_id, "running")})


@app.post("/syllabus/{syllabus_id}/sections", dependencies=[Depends(require_token)])
async def sections_create_cards(syllabus_id: int, request: Request):
    """Create cards from the user's picked groups. Body shape:
    {"groups": [{"sections": [{"index": 0, "kind": "summary"}, {"index": 5, "kind": "verbatim"}]}, ...]}

    Each group becomes ONE card. Within a card, each section can be either
    'summary' (run through Grok) or 'verbatim' (used as-is). Sections are
    rendered in document order. card.kind is 'summary' if any section is
    summarized, else 'quote'."""
    payload = await request.json()
    groups = payload.get("groups") or []
    if not isinstance(groups, list) or not groups:
        raise HTTPException(400, "groups must be a non-empty list")

    with Session(engine) as session:
        syllabus = session.get(Syllabus, syllabus_id)
        if not syllabus:
            raise HTTPException(404, "Syllabus not found")
        try:
            sections = json.loads(syllabus.outline_json or "[]")
        except (ValueError, TypeError):
            sections = []
        if not sections:
            raise HTTPException(400, "No outline available — re-visit the picker page first")
        try:
            syllabus_tables = json.loads(syllabus.tables_json or "[]")
        except (ValueError, TypeError):
            syllabus_tables = []

        created_cards = []
        for g in groups:
            raw_picks = g.get("sections")
            # Back-compat: old client format was {section_indices, kind} per group.
            if raw_picks is None and "section_indices" in g:
                fallback_kind = g.get("kind", "tailor")
                raw_picks = [{"index": i, "kind": fallback_kind} for i in g.get("section_indices", [])]
            if not raw_picks:
                continue

            tailor_prompt = (g.get("prompt") or "").strip() or None

            # Normalize, validate, sort by index for document order
            picks = []
            for p in raw_picks:
                try:
                    idx = int(p.get("index"))
                except (TypeError, ValueError):
                    continue
                if not (0 <= idx < len(sections)):
                    continue
                kind = p.get("kind") or "verbatim"
                # Back-compat: 'summary' was the old name; treat as tailor with default prompt
                if kind == "summary":
                    kind = "tailor"
                if kind not in ("tailor", "verbatim"):
                    raise HTTPException(400, f"invalid kind '{kind}' (must be tailor|verbatim)")
                picks.append({"index": idx, "kind": kind})

            if any(p["kind"] == "tailor" for p in picks) and not tailor_prompt:
                raise HTTPException(400, "Tailor card needs a prompt — tell us what you want from the section.")
            # Dedupe by index, keep last kind seen
            by_idx: dict[int, dict] = {}
            for p in picks:
                by_idx[p["index"]] = p
            picks = sorted(by_idx.values(), key=lambda p: p["index"])
            if not picks:
                continue

            # Build the card body section-by-section. Each section's verbatim
            # body is sent to Grok individually if marked summary. Original
            # text always keeps the verbatim joined form so the user can verify.
            display_parts: list[str] = []
            original_parts: list[str] = []
            for p in picks:
                sec = sections[p["index"]]
                heading = (sec.get("heading") or "").strip()
                body = (sec.get("body") or "").strip()
                section_passage_parts = []
                if heading:
                    section_passage_parts.append(f"## {heading}")
                if body:
                    section_passage_parts.append(body)
                section_passage = "\n".join(section_passage_parts).strip()
                if not section_passage:
                    continue
                original_parts.append(section_passage)

                if p["kind"] == "tailor" and body:
                    try:
                        section_tailored = tailor_passage(section_passage, tailor_prompt or "")
                    except grpc.RpcError as e:
                        raise HTTPException(502, f"Grok tailor failed: {e.details() if hasattr(e, 'details') else e}")
                    except RuntimeError as e:
                        raise HTTPException(500, str(e))
                    if heading and f"## {heading}" not in section_tailored:
                        section_tailored = f"## {heading}\n\n{section_tailored.strip()}"
                    display_parts.append(section_tailored.strip())
                else:
                    display_parts.append(section_passage)

            if not display_parts:
                continue

            display_raw = "\n\n".join(display_parts).strip()
            joined_original = "\n\n".join(original_parts).strip()
            if len(joined_original) > MAX_PASSAGE_CHARS:
                joined_original = joined_original[:MAX_PASSAGE_CHARS]
            if len(display_raw) > MAX_PASSAGE_CHARS:
                display_raw = display_raw[:MAX_PASSAGE_CHARS]

            # Card title = all section headings joined. Lets the user see at
            # a glance everything that's inside without expanding the card.
            picked_headings = [
                (sections[p["index"]].get("heading") or "").strip()
                for p in picks
            ]
            picked_headings = [h for h in picked_headings if h]
            label = " · ".join(picked_headings) if picked_headings else None

            stored_kind = "tailor" if any(p["kind"] == "tailor" for p in picks) else "quote"
            sections_meta = [
                {
                    "heading": (sections[p["index"]].get("heading") or "").strip() or None,
                    "kind": p["kind"],
                }
                for p in picks
            ]

            # Snapshot referenced tables, renumber markers
            combined_for_scan = joined_original + "\n\n" + display_raw
            seen_old: dict[int, int] = {}
            snapshot: list = []
            for m in _TABLE_MARKER_RE.finditer(combined_for_scan):
                old = int(m.group(1))
                if 0 <= old < len(syllabus_tables) and old not in seen_old:
                    seen_old[old] = len(snapshot)
                    snapshot.append(syllabus_tables[old])
            def _remap(m: "_re.Match[str]") -> str:
                old = int(m.group(1))
                return f"[TABLE:{seen_old[old]}]" if old in seen_old else m.group(0)
            original_text = _TABLE_MARKER_RE.sub(_remap, joined_original)
            display_text = _TABLE_MARKER_RE.sub(_remap, display_raw)

            card = Card(
                class_id=syllabus.class_id,
                kind=stored_kind,
                label=label,
                original_text=original_text,
                display_text=display_text,
                tables_json=(json.dumps(snapshot) if snapshot else None),
                sections_meta_json=json.dumps(sections_meta),
                tailor_prompt=tailor_prompt,
                created_at=datetime.now(timezone.utc),
            )
            session.add(card)
            session.flush()
            created_cards.append(card.id)
        session.commit()
        return JSONResponse({
            "created": created_cards,
            "redirect_to": f"/classes/{syllabus.class_id}",
        })


@app.post("/classes/{class_id}/cards", dependencies=[Depends(require_token)])
def create_card(
    class_id: int,
    kind: str = Form(...),
    original_text: str = Form(...),
    label: str = Form(""),
):
    """Create a card from a highlighted passage. kind=quote saves verbatim;
    kind=summary calls Grok to summarize, keeping original for verification."""
    if kind not in ("quote", "summary"):
        raise HTTPException(400, "kind must be 'quote' or 'summary'")
    text = original_text.strip()
    if not text:
        raise HTTPException(400, "original_text is empty")
    if len(text) > MAX_PASSAGE_CHARS:
        raise HTTPException(400, f"passage too long (max {MAX_PASSAGE_CHARS} chars)")

    with Session(engine) as session:
        cls = session.get(Class, class_id)
        if not cls:
            raise HTTPException(404, "Class not found")

        if kind == "summary":
            try:
                display = summarize_passage(text)
            except grpc.RpcError as e:
                raise HTTPException(502, f"Grok summarization failed: {e.details() if hasattr(e, 'details') else e}")
            except RuntimeError as e:
                raise HTTPException(500, str(e))
        else:
            display = text

        card = Card(
            class_id=class_id,
            kind=kind,
            label=(label.strip() or None),
            original_text=text,
            display_text=display,
            created_at=datetime.now(timezone.utc),
        )
        session.add(card)
        session.commit()
        session.refresh(card)
        return JSONResponse({
            "id": card.id,
            "kind": card.kind,
            "label": card.label,
            "original_text": card.original_text,
            "display_text": card.display_text,
            "created_at": card.created_at.isoformat(),
        })


@app.post("/classes/{class_id}/cards/event", dependencies=[Depends(require_token)])
def create_event_from_passage(
    class_id: int,
    original_text: str = Form(...),
):
    """Highlight → Save as event. Sends the passage to Grok for structured event
    extraction and writes it to the calendar."""
    text = original_text.strip()
    if not text:
        raise HTTPException(400, "original_text is empty")
    if len(text) > MAX_PASSAGE_CHARS:
        raise HTTPException(400, f"passage too long (max {MAX_PASSAGE_CHARS} chars)")

    with Session(engine) as session:
        cls = session.get(Class, class_id)
        if not cls:
            raise HTTPException(404, "Class not found")
        try:
            ev = extract_event_from_passage(text)
        except grpc.RpcError as e:
            raise HTTPException(502, f"Grok event extraction failed: {e.details() if hasattr(e, 'details') else e}")
        except RuntimeError as e:
            raise HTTPException(500, str(e))

        new_event = CalendarEvent(
            class_id=class_id,
            class_code=cls.code,
            title=ev.title or "Untitled",
            kind=ev.kind,
            starts_at=parse_iso_dt(ev.starts_at) if ev.starts_at else None,
            ends_at=parse_iso_dt(ev.ends_at) if ev.ends_at else None,
            source_text=text[:240],
        )
        session.add(new_event)
        session.commit()
        session.refresh(new_event)
        return JSONResponse({
            "id": new_event.id,
            "title": new_event.title,
            "kind": new_event.kind,
            "starts_at": new_event.starts_at.isoformat() if new_event.starts_at else None,
            "ends_at": new_event.ends_at.isoformat() if new_event.ends_at else None,
        })


@app.post("/classes/{class_id}/cards/reorder", dependencies=[Depends(require_token)])
async def reorder_cards(class_id: int, request: Request):
    """Persist card drag-and-drop ordering. Body: {card_ids: [id, id, ...]}.
    Cards are written with position = their index in the list. IDs that don't
    belong to this class are silently ignored."""
    payload = await request.json()
    raw_ids = payload.get("card_ids") or []
    if not isinstance(raw_ids, list):
        raise HTTPException(400, "card_ids must be a list")
    try:
        card_ids = [int(i) for i in raw_ids]
    except (TypeError, ValueError):
        raise HTTPException(400, "card_ids entries must be integers")

    with Session(engine) as session:
        cls = session.get(Class, class_id)
        if not cls:
            raise HTTPException(404, "Class not found")
        valid = {c.id for c in cls.cards}
        updated = 0
        for pos, cid in enumerate(card_ids):
            if cid not in valid:
                continue
            card = session.get(Card, cid)
            if card is None:
                continue
            card.position = pos
            session.add(card)
            updated += 1
        session.commit()
    return JSONResponse({"reordered": updated})


@app.post("/cards/{card_id}/edit", dependencies=[Depends(require_token)])
def edit_card(
    card_id: int,
    label: str = Form(""),
    display_text: str = Form(...),
):
    with Session(engine) as session:
        c = session.get(Card, card_id)
        if not c:
            raise HTTPException(404)
        c.label = label.strip() or None
        c.display_text = display_text.strip() or c.display_text
        cls_id = c.class_id
        session.add(c)
        session.commit()
    return RedirectResponse(f"/classes/{cls_id}", status_code=303)


@app.post("/cards/{card_id}/delete", dependencies=[Depends(require_token)])
def delete_card(card_id: int, request: Request):
    with Session(engine) as session:
        c = session.get(Card, card_id)
        if not c:
            raise HTTPException(404)
        cls_id = c.class_id
        session.delete(c)
        session.commit()
    # AJAX requests get JSON so the page can fade out the card without a
    # full reload. Form posts (no JS) still get the redirect.
    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse({"deleted": card_id, "class_id": cls_id})
    return RedirectResponse(f"/classes/{cls_id}", status_code=303)


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
