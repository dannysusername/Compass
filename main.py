from contextlib import asynccontextmanager
from datetime import datetime, time, timedelta, timezone
from email.message import EmailMessage
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Optional, List, Union
from zoneinfo import ZoneInfo
import hashlib
import json
import logging
import os
import secrets
import smtplib
import ssl
import uuid

from fastapi import (
    Depends, FastAPI, BackgroundTasks, File, Form, HTTPException,
    Request, UploadFile,
)
from fastapi.responses import (
    FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response,
)
from fastapi.middleware.cors import CORSMiddleware
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
import bcrypt
from starlette.middleware.sessions import SessionMiddleware

import storage


# ---- Config ----

LOCAL_TZ = ZoneInfo("America/New_York")  # change to your timezone

# Dev convenience: load local `.<name>` secret files into the environment
# before anything reads them. Mirrors compass_tray.py (Windows-only) so
# running `uvicorn main:app` directly on macOS/Linux picks up the same
# secrets. An already-set env var always wins; missing/blank files are
# skipped. Production (Heroku) ships none of these files and sets real
# config vars, so this loop is a no-op there.
for _fname, _var in (
    (".compass_secret_key", "COMPASS_SECRET_KEY"),
    (".xai_key", "XAI_API_KEY"),
    (".xai_model", "XAI_MODEL"),
    (".admin_emails", "ADMIN_EMAILS"),
    (".sendgrid_api_key", "SENDGRID_API_KEY"),
    (".email_from", "EMAIL_FROM"),
    (".app_base_url", "APP_BASE_URL"),
):
    if not os.environ.get(_var):
        _p = Path(__file__).parent / _fname
        if _p.is_file():
            _val = _p.read_text(encoding="utf-8").strip()
            if _val:
                os.environ[_var] = _val

XAI_MODEL = os.environ.get("XAI_MODEL", "grok-4-fast-reasoning").strip()
# Shared/server-owned Grok key. When set, accounts WITHOUT their own
# xai_api_key can still parse syllabi (the demo eats the cost), capped per
# account at FREE_PARSE_LIMIT. Empty in dev/tests → keyless upload falls
# back to the old "add a key" block. A user who sets their own key bypasses
# the cap entirely (their quota, their bill).
XAI_API_KEY = os.environ.get("XAI_API_KEY", "").strip()
try:
    FREE_PARSE_LIMIT = max(0, int(os.environ.get("FREE_PARSE_LIMIT", "5")))
except ValueError:
    FREE_PARSE_LIMIT = 5
# Owner allowlist for the admin dashboard. Comma-separated emails, matched
# case-insensitively against the logged-in user's email. Empty = nobody is
# admin (the /admin routes 404 for everyone). This is the security boundary:
# it lives in the environment, so it can't be escalated via the app or a DB
# edit and survives a DB reset.
ADMIN_EMAILS = {
    e.strip().lower()
    for e in os.environ.get("ADMIN_EMAILS", "").split(",")
    if e.strip()
}
MAX_UPLOAD_MB = 25
COMPASS_ENV = os.environ.get("COMPASS_ENV", "development").strip().lower()
COMPASS_SECRET_KEY = os.environ.get("COMPASS_SECRET_KEY", "").strip()
if not COMPASS_SECRET_KEY:
    if COMPASS_ENV == "production":
        raise RuntimeError("COMPASS_SECRET_KEY must be set in production")
    # Dev fallback: ephemeral key. Sessions reset on restart, which is fine
    # for local dev and prevents accidental "I forgot to set the key" deploys.
    COMPASS_SECRET_KEY = secrets.token_urlsafe(32)


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


# ---- Email (password-reset delivery) ----
# Production sends via the SendGrid SMTP relay (validated against Twilio's
# MCP: smtp.sendgrid.net:587, STARTTLS, username literal "apikey",
# password = the SendGrid API key). Everywhere else — dev, CI, tests —
# the no-network `_log_send` backend records the link to compass.log + an
# in-memory sink, so the whole flow is exercisable with zero setup and no
# real mail. `send_email` is a module global on purpose so tests can
# monkeypatch it. NOTE: SendGrid rejects any send whose From isn't a
# verified Sender Identity; EMAIL_FROM must be a domain-authenticated
# address (not a free gmail/outlook/yahoo address) — see the plan.
SENDGRID_API_KEY = os.environ.get("SENDGRID_API_KEY", "").strip()
EMAIL_FROM = os.environ.get("EMAIL_FROM", "").strip()
APP_BASE_URL = os.environ.get("APP_BASE_URL", "").strip().rstrip("/")

# Last reset link, exposed ONLY through the COMPASS_ENV != "production"
# /__test__/last_reset_link route so browser tests can fetch it without
# scraping the log. Never populated/served in production.
_last_reset_link: Optional[str] = None


def _log_send(to: str, subject: str, text_body: str,
              html_body: Optional[str] = None) -> None:
    log.info("email (dev backend) to=%s subject=%r body=%s",
             to, subject, text_body)


def _smtp_send(to: str, subject: str, text_body: str,
               html_body: Optional[str] = None) -> None:
    msg = EmailMessage()
    msg["From"] = EMAIL_FROM
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(text_body)
    if html_body:
        msg.add_alternative(html_body, subtype="html")
    ctx = ssl.create_default_context()
    with smtplib.SMTP("smtp.sendgrid.net", 587, timeout=15) as s:
        s.ehlo()
        s.starttls(context=ctx)
        s.login("apikey", SENDGRID_API_KEY)
        refused = s.send_message(msg)
    if refused:
        raise RuntimeError(f"SendGrid refused recipient(s): {refused}")


def send_email(to: str, subject: str, text_body: str,
               html_body: Optional[str] = None) -> None:
    """Prod → SendGrid SMTP relay; dev/CI/tests → log backend. Gating on
    COMPASS_ENV + creds means non-prod never touches the network."""
    if COMPASS_ENV == "production" and SENDGRID_API_KEY and EMAIL_FROM:
        _smtp_send(to, subject, text_body, html_body)
    else:
        _log_send(to, subject, text_body, html_body)


def _reset_link(request: Request, raw_token: str) -> str:
    """Absolute URL of the reset page. APP_BASE_URL is authoritative in
    prod — Heroku runs uvicorn without --proxy-headers so request.base_url
    yields the wrong internal scheme/host. request.base_url is the dev
    fallback only."""
    base = APP_BASE_URL or str(request.base_url).rstrip("/")
    return f"{base}/reset/{raw_token}"


def _reset_email_bodies(link: str) -> tuple[str, str]:
    """(plain-text, HTML) bodies for the reset email. The HTML version
    gives a real clickable button + brand styling; the plain-text part is
    the fallback for clients that don't render HTML. Table-based layout +
    inline styles for broad email-client support (Gmail/Outlook strip
    <style> blocks and most modern CSS)."""
    text = (
        "We received a request to reset your Compass password.\n\n"
        "Reset it here (this link expires in 1 hour and can be used "
        f"once):\n{link}\n\n"
        "If you didn't request this, you can safely ignore this email "
        "— your password is unchanged."
    )
    sans = ("-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,"
            "Arial,sans-serif")
    html = f"""\
<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#F4F1E8;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#F4F1E8;padding:32px 16px;">
    <tr><td align="center">
      <table role="presentation" cellpadding="0" cellspacing="0" width="480" style="max-width:480px;width:100%;background:#ffffff;border:1px solid #E2DCCA;border-radius:12px;">
        <tr><td style="padding:30px 32px 4px 32px;">
          <span style="font-family:Georgia,'Times New Roman',serif;font-size:22px;font-style:italic;font-weight:bold;color:#1F3D7A;">Compass<span style="color:#5b9d5b;font-style:normal;">.</span></span>
        </td></tr>
        <tr><td style="padding:12px 32px 0 32px;">
          <h1 style="margin:0 0 12px 0;font-family:{sans};font-size:20px;color:#1F3D7A;">Reset your password</h1>
          <p style="margin:0 0 24px 0;font-family:{sans};font-size:15px;line-height:1.5;color:#2B2B2B;">We received a request to reset your Compass password. Click the button below to choose a new one.</p>
        </td></tr>
        <tr><td align="center" style="padding:0 32px;">
          <a href="{link}" style="display:inline-block;background:#1F3D7A;color:#ffffff;text-decoration:none;font-family:{sans};font-size:15px;font-weight:600;padding:13px 30px;border-radius:8px;">Reset password</a>
        </td></tr>
        <tr><td style="padding:20px 32px 0 32px;">
          <p style="margin:0 0 12px 0;font-family:{sans};font-size:13px;color:#7A7468;">This link expires in <strong>1 hour</strong> and can be used once.</p>
          <p style="margin:0 0 6px 0;font-family:{sans};font-size:13px;color:#7A7468;">If the button doesn't work, copy and paste this link into your browser:</p>
          <p style="margin:0;font-family:monospace;font-size:12px;line-height:1.45;word-break:break-all;"><a href="{link}" style="color:#1F3D7A;">{link}</a></p>
        </td></tr>
        <tr><td style="padding:24px 32px 30px 32px;">
          <div style="border-top:1px solid #ECE7D9;padding-top:16px;">
            <p style="margin:0;font-family:{sans};font-size:13px;line-height:1.5;color:#7A7468;">If you didn't request this, you can safely ignore this email — your password won't change.</p>
          </div>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""
    return text, html


# ---- Database models ----

class Class(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
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
    # No cascade on tasks: deleting a class preserves its tasks (their
    # class_id gets nulled out in delete_class), so the user doesn't lose
    # work just because they cleaned up an old class.
    tasks: List["Task"] = Relationship(back_populates="cls")


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
    """User-typed to-do item, optionally attached to a class. Sits alongside
    CalendarEvent on the today/week views — both can be marked done with the
    circular button. Tasks are entirely manual; no AI/syllabus auto-generation.

    `class_id` is nullable: a NULL class_id is a "Personal" task with no
    course association (e.g. groceries, errands). The home/today/week views
    bucket personal tasks under a synthetic "Personal" group.

    `rrule` is an iCalendar RRULE fragment (no leading "RRULE:") describing
    repetition — e.g. "FREQ=DAILY", "FREQ=WEEKLY", "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR".
    Empty/None means non-recurring.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    class_id: Optional[int] = Field(default=None, foreign_key="class.id")
    title: str
    notes: Optional[str] = Field(default=None)  # free-form, surfaced in iCal DESCRIPTION
    starts_at: Optional[datetime] = None  # range start; None = single-date task
    due_at: Optional[datetime] = None     # range end / deadline
    completed_at: Optional[datetime] = None
    position: int = Field(default=0)  # drag-to-reorder priority
    created_at: datetime
    tag_id: Optional[int] = Field(default=None, foreign_key="tag.id")
    rrule: Optional[str] = Field(default=None)
    # Optional UNTIL date for recurring tasks. Stored UTC; the iCal feed
    # serializes it into the RRULE as `UNTIL=YYYYMMDDTHHMMSSZ`.
    rrule_until: Optional[datetime] = Field(default=None)
    # JSON list of ISO datetimes to skip — populated when the user picks
    # "Delete only this date" on a recurring row.
    rrule_exdates: Optional[str] = Field(default=None)
    is_all_day: bool = Field(default=False)
    cls: Optional[Class] = Relationship(back_populates="tasks")
    tag: Optional["Tag"] = Relationship(back_populates="tasks")
    alerts: List["TaskAlert"] = Relationship(
        back_populates="task",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )
    attachments: List["TaskAttachment"] = Relationship(
        back_populates="task",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )


class TaskAlert(SQLModel, table=True):
    """One reminder per row. Multiple rows mean multiple VALARM blocks on the
    iCal event. `minutes_before` is positive (15, 60, 1440, 10080…) — the
    feed converts to a negative TRIGGER offset."""
    id: Optional[int] = Field(default=None, primary_key=True)
    task_id: int = Field(foreign_key="task.id", index=True)
    minutes_before: int
    task: Optional["Task"] = Relationship(back_populates="alerts")


class TaskAttachment(SQLModel, table=True):
    """File the user attached to a task. Storage backend (local or R2) is
    handled through `storage.py` — `filename` is the storage key; `original_name`
    is what the user sees. Token-authenticated download for Apple Calendar
    lives at `/calendar/{token}/attachments/{filename}`."""
    id: Optional[int] = Field(default=None, primary_key=True)
    task_id: int = Field(foreign_key="task.id", index=True)
    filename: str
    original_name: str
    content_type: str
    uploaded_at: datetime
    task: Optional["Task"] = Relationship(back_populates="attachments")


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
    user_id: int = Field(foreign_key="user.id", index=True)
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


def _ensure_system_tag(session: "Session", user_id: int, kind: str) -> None:
    """Make sure a system tag exists for `kind` for the given user.
    Idempotent. Re-uses an existing tag (system or user) by name and
    backfills `system_key` so the collector can resolve color/name for
    events with this kind."""
    if not kind:
        return
    kind = kind.lower().strip()
    if not kind:
        return
    existing = session.exec(
        select(Tag).where(
            Tag.user_id == user_id,
            (Tag.system_key == kind) | (Tag.name == kind),
        )
    ).first()
    if existing is None:
        seeded = dict(SYSTEM_TAGS)
        color = seeded.get(kind) or _pick_tag_color(kind)
        session.add(Tag(
            user_id=user_id, name=kind, color=color,
            is_system=True, system_key=kind,
        ))
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


def _seed_system_tags_for_user(user_id: int) -> None:
    """Insert the four default system tags for a newly-created user.
    Called once at signup, idempotent on re-call."""
    with Session(engine) as session:
        for name, color in SYSTEM_TAGS:
            existing = session.exec(
                select(Tag).where(
                    Tag.user_id == user_id,
                    (Tag.system_key == name) | (Tag.name == name),
                )
            ).first()
            if existing is None:
                session.add(Tag(
                    user_id=user_id, name=name, color=color,
                    is_system=True, system_key=name,
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


class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(unique=True, index=True)
    password_hash: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    # Optional user-supplied xAI API key. When set, syllabus parses run on
    # this key (the user's own quota) with NO cap. When empty, Compass uses
    # the shared server XAI_API_KEY, capped at FREE_PARSE_LIMIT parses per
    # account (tracked in free_parses_used).
    xai_api_key: Optional[str] = Field(default=None)
    # Number of syllabus parses this account has spent on the shared server
    # key. Only incremented when the user has no key of their own. Once it
    # reaches FREE_PARSE_LIMIT the /syllabus route blocks further uploads
    # until the user adds their own xAI key.
    free_parses_used: int = Field(default=0)
    # Admin-granted (via /admin) uncapped parsing on the SHARED server key —
    # same effect as bringing your own key, but spent on the owner's quota.
    # Set/cleared only by an ADMIN_EMAILS account; users can't self-grant.
    unlimited_parses: bool = Field(default=False)
    # Unguessable token embedded in the iCal subscription URL. Lets Apple
    # Calendar (and other clients) poll the feed without sending a session
    # cookie — cookies don't survive long-lived subscriptions. Regenerating
    # the token revokes all existing subscriptions.
    calendar_token: str = Field(
        default_factory=lambda: secrets.token_urlsafe(32),
        unique=True, index=True,
    )
    # JSON list of class-bucket keys ("1", "0", "3", ...) defining the
    # user's preferred display order on home/today views. "0" is the
    # Personal bucket. Buckets not listed here append in default
    # alphabetical-by-code order.
    class_order_json: Optional[str] = Field(default=None)
    # IANA timezone string ("America/Los_Angeles", "Europe/Berlin"...).
    # When set, replaces the server-wide LOCAL_TZ default for THIS user's
    # today/overdue/week date math + iCal feed. Auto-populated on every
    # page load by base.html JS (Intl.DateTimeFormat resolved tz) and
    # POSTed to /settings/timezone. NULL means use LOCAL_TZ — keeps the
    # legacy single-user behavior intact for accounts that haven't loaded
    # any page since the field was added.
    timezone: Optional[str] = Field(default=None)


class PasswordResetToken(SQLModel, table=True):
    """Single-use, time-limited password-reset grant. Only the SHA-256
    hash of the raw token is stored — the raw value lives solely in the
    emailed link, so a DB/backup leak yields nothing usable (the token is
    256-bit high-entropy so a fast hash is sufficient; bcrypt would buy
    nothing and hit its 72-byte cap). A new /forgot for the same user
    marks prior live tokens used (newest-wins); `used_at` is stamped in
    the same transaction as the password change. Brand-new table →
    auto-created by the lifespan create_all + migrate.py on both Neon
    DBs; no manual DDL anywhere."""
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    token_hash: str = Field(unique=True, index=True)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime
    used_at: Optional[datetime] = Field(default=None)


class DayItemPosition(SQLModel, table=True):
    """Per-day position override for a task or event on the week tab.

    A multi-day task renders once per day it spans, so dragging it on
    Friday's day modal must not change Saturday's order. We store an
    override keyed on (user_id, kind, item_id, day_date); when the week
    view collects items for a given day, it prefers the day-scoped
    position over the global Task/CalendarEvent.position.

    Other views (home/today, class page) keep using the global position —
    drag there is "always today" so per-day overrides aren't needed."""
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    kind: str  # 'task' or 'event'
    item_id: int = Field(index=True)
    day_date: str = Field(index=True)  # YYYY-MM-DD, plain string for portability
    position: int


# ---- App setup ----

# DATABASE_URL is set by Heroku; it arrives as `postgres://...`. Rewrite to
# the psycopg3 driver explicitly so SQLAlchemy doesn't reach for psycopg2
# (which we don't install). Falls back to a local SQLite file for dev.
DB_PATH = Path(__file__).parent / "compass.db"
_db_url = os.environ.get("DATABASE_URL", "").strip()
if _db_url.startswith("postgres://"):
    _db_url = "postgresql+psycopg://" + _db_url[len("postgres://"):]
elif _db_url.startswith("postgresql://"):
    _db_url = "postgresql+psycopg://" + _db_url[len("postgresql://"):]
if not _db_url:
    _db_url = f"sqlite:///{DB_PATH}"

IS_SQLITE = _db_url.startswith("sqlite")
if IS_SQLITE:
    engine = create_engine(_db_url, connect_args={"check_same_thread": False})
else:
    # Postgres: pool_pre_ping survives Heroku's idle-connection drops.
    # prepare_threshold=None disables psycopg3's prepared-statement cache —
    # required when DATABASE_URL points at Neon's pooled (-pooler) endpoint,
    # since pgbouncer in transaction mode invalidates prepared statements
    # between transactions. Safe no-op on direct connections.
    engine = create_engine(_db_url, pool_pre_ping=True, connect_args={"prepare_threshold": None})


def _add_column_if_missing(conn, table: str, column: str, ddl: str) -> None:
    """Add a column to an existing SQLite table if it isn't already there.
    SQLModel.create_all only creates missing tables, not missing columns.
    No-op when the table itself doesn't exist — fresh databases (and
    test DBs) skip migrations for tables that were removed from the
    model upstream."""
    rows = conn.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
    if not rows:
        return  # table doesn't exist; nothing to migrate
    existing = {r[1] for r in rows}  # row[1] = column name
    if column not in existing:
        conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    SQLModel.metadata.create_all(engine)
    # The PRAGMA-based ALTER TABLE migrations below are SQLite-specific
    # (they rely on PRAGMA table_info introspection). Postgres deployments
    # start from a fresh DB created by metadata.create_all above, so we
    # skip them entirely there.
    if IS_SQLITE:
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
            _add_column_if_missing(conn, "class", "user_id", "INTEGER")
            _add_column_if_missing(conn, "task", "user_id", "INTEGER")
            _add_column_if_missing(conn, "tag", "user_id", "INTEGER")
            _add_column_if_missing(conn, "user", "xai_api_key", "TEXT")
            _add_column_if_missing(conn, "user", "free_parses_used", "INTEGER NOT NULL DEFAULT 0")
            _add_column_if_missing(conn, "user", "unlimited_parses", "INTEGER NOT NULL DEFAULT 0")
            _add_column_if_missing(conn, "user", "calendar_token", "TEXT")
            _add_column_if_missing(conn, "user", "class_order_json", "TEXT")
            _add_column_if_missing(conn, "user", "timezone", "TEXT")
            _add_column_if_missing(conn, "task", "notes", "TEXT")
            _add_column_if_missing(conn, "task", "rrule", "TEXT")
            _add_column_if_missing(conn, "task", "is_all_day", "INTEGER NOT NULL DEFAULT 0")
            _add_column_if_missing(conn, "task", "rrule_until", "TIMESTAMP")
            _add_column_if_missing(conn, "task", "rrule_exdates", "TEXT")
            # SQLite has no in-place ALTER COLUMN to drop NOT NULL, so
            # rebuild the task table when its class_id is still marked
            # NOT NULL. Idempotent — subsequent boots see notnull=0 and
            # skip. Postgres deploys start fresh from create_all (which
            # respects the model's Optional[int]) so this never runs there.
            cols = conn.exec_driver_sql("PRAGMA table_info(task)").fetchall()
            class_id_meta = next((c for c in cols if c[1] == "class_id"), None)
            if class_id_meta and class_id_meta[3] == 1:
                log.info("rebuilding task table to make class_id nullable")
                col_csv = ", ".join(c[1] for c in cols)
                conn.exec_driver_sql("PRAGMA foreign_keys = OFF")
                conn.exec_driver_sql("ALTER TABLE task RENAME TO _task_pre_phase5")
                # Recreate task from the current SQLModel definition (now
                # has class_id as nullable).
                Task.__table__.create(conn)
                conn.exec_driver_sql(
                    f"INSERT INTO task ({col_csv}) SELECT {col_csv} FROM _task_pre_phase5"
                )
                conn.exec_driver_sql("DROP TABLE _task_pre_phase5")
                conn.exec_driver_sql("PRAGMA foreign_keys = ON")
            users = conn.exec_driver_sql('SELECT id FROM "user" ORDER BY id').fetchall()
            if users:
                owner_id = users[0][0]
                for tbl in ("class", "task", "tag"):
                    conn.exec_driver_sql(
                        f"UPDATE {tbl} SET user_id = {owner_id} WHERE user_id IS NULL"
                    )
                log.info("backfilled user_id=%s on existing class/task/tag rows", owner_id)
    # Make sure every user has the four default system tags (signup also
    # calls this, but we re-run for the existing-data case). Also backfill
    # calendar_token for any user that pre-dates the iCal-token column.
    with Session(engine) as session:
        for u in session.exec(select(User)).all():
            _seed_system_tags_for_user(u.id)
            if not (u.calendar_token or "").strip():
                u.calendar_token = secrets.token_urlsafe(32)
                session.add(u)
        session.commit()
        # Dedupe system tags within each user: pre-Phase-2 data may have
        # ended up with multiple tags sharing a system_key after the
        # backfill. Keep the oldest, repoint any tasks at the duplicates,
        # then delete the duplicates.
        all_users = session.exec(select(User)).all()
        for u in all_users:
            seen: dict[str, int] = {}  # system_key -> kept tag id
            for tag in session.exec(
                select(Tag).where(Tag.user_id == u.id, Tag.system_key != None)
                .order_by(Tag.id)
            ).all():
                key = tag.system_key
                if key not in seen:
                    seen[key] = tag.id
                    continue
                # Duplicate: repoint tasks pointing here to the kept one,
                # then delete this row.
                kept_id = seen[key]
                for t in session.exec(
                    select(Task).where(Task.tag_id == tag.id)
                ).all():
                    t.tag_id = kept_id
                    session.add(t)
                session.delete(tag)
        session.commit()
    # Backfill date-less orphan tasks. Pre-fix data could contain a task
    # with no due_at, no starts_at, and no rrule — it renders only on
    # "today" on the web and NOT AT ALL in the extension, so it became
    # impossible to reach and delete. Anchor each to its creation date so
    # it resurfaces (as an overdue row) and can be edited/deleted on every
    # surface. Idempotent: once anchored there are no NULL-date orphans
    # left to match. New tasks can't become orphans — _create_task_for_user
    # backstops a missing date to today at creation time.
    with Session(engine) as session:
        orphans = session.exec(
            select(Task).where(Task.due_at.is_(None), Task.starts_at.is_(None))
        ).all()
        fixed = 0
        for t in orphans:
            if (t.rrule or "").strip():
                continue  # recurring tasks carry their anchor elsewhere
            t.due_at = t.created_at or datetime.now(timezone.utc)
            session.add(t)
            fixed += 1
        if fixed:
            session.commit()
            log.info("backfilled due_at on %s date-less orphan task(s)", fixed)
    yield


app = FastAPI(title="Compass", lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=COMPASS_SECRET_KEY,
    same_site="lax",
    https_only=(COMPASS_ENV == "production"),
    max_age=60 * 60 * 24 * 30,  # 30 days
)
# Browser-extension support: the popup/side-panel runs at
# `chrome-extension://<id>` and calls the FastAPI server with credentials
# (cookies) so the user's existing session rides along. Allow that origin
# pattern + credentialed requests; the regex matches any Chromium ext id
# (32 lowercase letters), which is safe because the extension still has
# to be installed on the user's browser to make the call. Added AFTER
# SessionMiddleware so it wraps as the outer layer (handles preflights
# before session lookup runs).
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^chrome-extension://[a-p]{32}$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


# ---- PWA (installable app + offline viewing) ----
# The service worker MUST be served from the root so its scope covers the
# whole app ("/"). The manifest makes Compass installable. Both live in
# static/ but are exposed at the root here.
@app.get("/sw.js", include_in_schema=False)
def service_worker():
    return FileResponse(
        "static/sw.js",
        media_type="application/javascript",
        # no-cache so a new service worker is picked up promptly on deploy.
        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"},
    )


@app.get("/manifest.webmanifest", include_in_schema=False)
def web_manifest():
    return FileResponse("static/manifest.webmanifest",
                        media_type="application/manifest+json")


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
# Exposed to templates so base.html can skip service-worker registration in
# the test env (a live SW would cache responses and flake the browser suite).
templates.env.globals["COMPASS_ENV"] = COMPASS_ENV


# ---- Parse-job status (in-memory; resets on restart) ----

parse_jobs: dict[int, str] = {}  # syllabus_id -> "pending"|"running"|"done"|"error: ..."

# Outline extraction (Pass 1.5) status, separate from upload-time parse_jobs so
# users can re-trigger outline extraction without conflicting with the
# upload-time events extraction. syllabus_id -> "running"|"done"|"error: ..."


# ---- Auth ----

# bcrypt has a 72-byte password limit. Signup enforces a max length below
# this so the bcrypt call never has to truncate or raise.
MAX_PASSWORD_LENGTH = 72


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False


class NotAuthenticatedError(Exception):
    """Raised by require_login when a request has no valid session.
    Centralized exception handler turns this into a redirect to /login."""


def current_user_optional(request: Request) -> Optional[User]:
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    with Session(engine) as session:
        return session.get(User, user_id)


def require_login(request: Request) -> User:
    user = current_user_optional(request)
    if not user:
        request.session.clear()
        raise NotAuthenticatedError()
    return user


@app.exception_handler(NotAuthenticatedError)
async def _redirect_to_login(request: Request, exc: NotAuthenticatedError):
    # Extension/API clients ask for JSON — they need a 401 they can detect,
    # not a 303 to an HTML login page (a chrome-extension:// origin can't
    # render Compass's login template anyway). HTML browsers still get the
    # redirect.
    accept = request.headers.get("accept", "")
    if "application/json" in accept:
        return JSONResponse({"error": "not_authenticated"}, status_code=401)
    return RedirectResponse("/login", status_code=303)


def _is_admin(user: User) -> bool:
    """True iff the user's email is in the ADMIN_EMAILS env allowlist
    (case-insensitive). The only gate to the admin dashboard."""
    return (user.email or "").strip().lower() in ADMIN_EMAILS


def require_admin(user: User = Depends(require_login)) -> User:
    """Admin-only routes depend on this. Non-admins (and logged-out users,
    via require_login) get a 404 — not a 403 — so the dashboard's existence
    isn't even disclosed to accounts that can't use it."""
    if not _is_admin(user):
        raise HTTPException(404)
    return user


# ---- Ownership helpers ----
# Each loads a row by id and 404s if it doesn't belong to the given user.
# Centralizes the per-user scoping so individual routes stay readable.

def _lookup_owned_item(session: "Session", kind: str, eid: int, user_id: int):
    """Bulk-ops cousin of `_own_task` / `_own_event`. Returns the row when
    it exists AND belongs to `user_id`, otherwise None — never raises.
    Routes that batch over user-supplied id lists (`/tasks/reorder`,
    `/tasks/reorder-day`) want silent skip on bad ids, not a 404 that
    aborts the whole request."""
    if kind == "task":
        row = session.get(Task, eid)
        return row if row and row.user_id == user_id else None
    if kind == "event":
        ev = session.get(CalendarEvent, eid)
        if ev is None:
            return None
        cls = session.get(Class, ev.class_id)
        return ev if cls and cls.user_id == user_id else None
    return None


def _own_class(session: "Session", class_id: int, user_id: int) -> "Class":
    cls = session.get(Class, class_id)
    if not cls or cls.user_id != user_id:
        raise HTTPException(404, "Class not found")
    return cls


def _own_event(session: "Session", event_id: int, user_id: int) -> "CalendarEvent":
    ev = session.get(CalendarEvent, event_id)
    if not ev:
        raise HTTPException(404, "Event not found")
    cls = session.get(Class, ev.class_id)
    if not cls or cls.user_id != user_id:
        raise HTTPException(404, "Event not found")
    return ev


def _own_task(session: "Session", task_id: int, user_id: int) -> "Task":
    t = session.get(Task, task_id)
    if not t or t.user_id != user_id:
        raise HTTPException(404, "Task not found")
    return t


def _own_tag(session: "Session", tag_id: int, user_id: int) -> "Tag":
    tag = session.get(Tag, tag_id)
    if not tag or tag.user_id != user_id:
        raise HTTPException(404, "Tag not found")
    return tag


def _own_attachment(session: "Session", attachment_id: int, user_id: int) -> "TaskAttachment":
    a = session.get(TaskAttachment, attachment_id)
    if not a:
        raise HTTPException(404, "Attachment not found")
    t = session.get(Task, a.task_id)
    if not t or t.user_id != user_id:
        raise HTTPException(404, "Attachment not found")
    return a


def _own_document(session: "Session", doc_id: int, user_id: int) -> "Document":
    doc = session.get(Document, doc_id)
    if not doc:
        raise HTTPException(404, "Document not found")
    cls = session.get(Class, doc.class_id)
    if not cls or cls.user_id != user_id:
        raise HTTPException(404, "Document not found")
    return doc


def _own_syllabus(session: "Session", syllabus_id: int, user_id: int) -> Optional["Syllabus"]:
    """Returns None if not found or not owned — caller decides whether to
    404 or render a 'missing' status page."""
    syl = session.get(Syllabus, syllabus_id)
    if not syl:
        return None
    cls = session.get(Class, syl.class_id)
    if not cls or cls.user_id != user_id:
        return None
    return syl


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


def extract_pdf_text(source: Union[Path, bytes]) -> str:
    """Pull plain text out of a PDF. Accepts either a Path (dev/local-disk
    path) or raw bytes (so callers can pass content fetched from object
    storage without writing a tempfile). dedupe_chars handles 'fake bold'
    double-stamped glyphs that show up in many professor-authored syllabi
    (cells like 'CCIISS33995500' come out as 'CIS 3950').

    No table detection — pdfplumber's plain extract_text() keeps schedule
    grids inline as readable rows, which is what Grok needs to find every
    quiz/lecture/deadline. The previous table-detection path stripped grid
    cells out of the prose entirely, hiding most of the schedule from
    extraction."""
    chunks: list[str] = []
    label = source.name if isinstance(source, Path) else f"<bytes {len(source)}>"
    opener = source if isinstance(source, Path) else BytesIO(source)
    with pdfplumber.open(opener) as pdf:
        total = len(pdf.pages)
        for page in pdf.pages:
            try:
                page_src = page.dedupe_chars()
            except Exception:
                page_src = page
            try:
                text = page_src.extract_text() or ""
            except Exception:
                text = page.extract_text() or ""
            page_text = text.strip()
            if page_text:
                chunks.append(page_text)
    log.info(
        "extract_pdf_text: %d of %d pages had text (%s)",
        len(chunks), total, label,
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


def parse_syllabus_with_grok(text: str, user_key: str) -> dict:
    """Call xAI Grok (grok-4-latest by default) via the native xai-sdk with
    structured-output enforcement. Raises grpc.RpcError on API failures; caller logs.

    The xAI key is always per-user (set in /settings) — no server-wide
    fallback, so one user's account can never spend another's quota."""
    api_key = (user_key or "").strip()
    if not api_key:
        raise RuntimeError(
            "No xAI API key on your account. Add one in /settings to parse syllabi."
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
            # Prefer the class owner's own xAI key (runs on their quota,
            # uncapped). Fall back to the shared server key for keyless
            # accounts — the /syllabus route already enforced the per-account
            # cap before queuing this job.
            cls = session.get(Class, syllabus.class_id)
            owner = session.get(User, cls.user_id) if cls else None
            own_key = (owner.xai_api_key or "").strip() if owner else ""
            effective_key = own_key or XAI_API_KEY

        try:
            data = parse_syllabus_with_grok(raw_text, user_key=effective_key)
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
            owner_id = cls.user_id  # tag-seeding scope follows the class owner
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
                _ensure_system_tag(session, owner_id, k)
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

@app.get("/signup", response_class=HTMLResponse)
def signup_page(request: Request):
    if request.session.get("user_id"):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "signup.html", {"error": None, "email": ""})


@app.post("/signup")
def signup_submit(request: Request, email: str = Form(...), password: str = Form(...)):
    wants_json = "application/json" in request.headers.get("accept", "")
    email = email.strip().lower()

    def _err(msg: str):
        if wants_json:
            return JSONResponse({"error": msg}, status_code=400)
        return templates.TemplateResponse(
            request, "signup.html",
            {"error": msg, "email": email},
            status_code=400,
        )

    if "@" not in email or "." not in email.split("@", 1)[-1]:
        return _err("Please enter a valid email address.")
    if len(password) < 8:
        return _err("Password must be at least 8 characters.")
    if len(password.encode("utf-8")) > MAX_PASSWORD_LENGTH:
        return _err(f"Password must be at most {MAX_PASSWORD_LENGTH} characters.")
    with Session(engine) as session:
        existing = session.exec(select(User).where(User.email == email)).first()
        if existing:
            return _err("That email is already registered. Try logging in.")
        user = User(email=email, password_hash=hash_password(password))
        session.add(user)
        session.commit()
        session.refresh(user)
        request.session["user_id"] = user.id
    _seed_system_tags_for_user(user.id)
    if wants_json:
        return JSONResponse({"id": user.id, "email": user.email})
    return RedirectResponse("/", status_code=303)


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if request.session.get("user_id"):
        return RedirectResponse("/", status_code=303)
    reset_done = request.query_params.get("reset") == "1"
    return templates.TemplateResponse(
        request, "login.html",
        {"error": None, "email": "", "reset_done": reset_done})


@app.post("/login")
def login_submit(request: Request, email: str = Form(...), password: str = Form(...)):
    email = email.strip().lower()
    with Session(engine) as session:
        user = session.exec(select(User).where(User.email == email)).first()
        if not user or not verify_password(password, user.password_hash):
            return templates.TemplateResponse(
                request, "login.html",
                {"error": "Invalid email or password.", "email": email},
                status_code=401,
            )
        request.session["user_id"] = user.id
    return RedirectResponse("/", status_code=303)


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


# ---- Password reset ----

PASSWORD_RESET_TTL = timedelta(hours=1)
_FORGOT_WINDOW = timedelta(minutes=15)
_FORGOT_MAX_EMAIL = 3
# IP cap is deliberately lenient: this is a school app, whole campuses
# sit behind one NAT'd public IP, so a strict per-IP limit would lock
# out a building. Per-email (above) is the real abuse guard.
_FORGOT_MAX_IP = 30
# email/ip -> [datetime]. In-process, per-dyno, resets on restart —
# best-effort, mirrors the parse_jobs in-memory pattern.
_forgot_hits: dict = {}


def _token_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _forgot_throttled(key: str, limit: int) -> bool:
    now = datetime.now(timezone.utc)
    hits = [t for t in _forgot_hits.get(key, []) if now - t < _FORGOT_WINDOW]
    hits.append(now)
    _forgot_hits[key] = hits
    return len(hits) > limit


def _valid_reset(session: Session, token: str):
    """Return (row, user) for a usable token, else (None, None). One
    generic failure for not-found / used / expired / deleted-user so the
    caller can't build an oracle."""
    if not token or len(token) < 20:
        return None, None
    h = _token_hash(token)
    row = session.exec(
        select(PasswordResetToken).where(
            PasswordResetToken.token_hash == h)).first()
    if not row or row.used_at is not None:
        return None, None
    if not secrets.compare_digest(row.token_hash, h):
        return None, None
    exp = row.expires_at
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    if exp <= datetime.now(timezone.utc):
        return None, None
    user = session.get(User, row.user_id)
    if not user:
        return None, None
    return row, user


def _reset_resp(request: Request, ctx: dict, status: int = 200):
    r = templates.TemplateResponse(request, "reset.html", ctx,
                                   status_code=status)
    # Token is in the URL path — keep it out of the Referer header.
    r.headers["Referrer-Policy"] = "no-referrer"
    return r


@app.get("/forgot", response_class=HTMLResponse)
def forgot_page(request: Request):
    return templates.TemplateResponse(
        request, "forgot.html", {"sent": False, "error": None})


@app.post("/forgot", response_class=HTMLResponse)
def forgot_submit(request: Request, email: str = Form(...)):
    global _last_reset_link
    email = email.strip().lower()
    ip = request.client.host if request.client else "?"
    throttled = (_forgot_throttled(f"e:{email}", _FORGOT_MAX_EMAIL)
                 or _forgot_throttled(f"i:{ip}", _FORGOT_MAX_IP))
    if not throttled:
        with Session(engine) as session:
            user = session.exec(
                select(User).where(User.email == email)).first()
            if user:
                now = datetime.now(timezone.utc)
                # newest-wins: invalidate prior live tokens
                for old in session.exec(
                    select(PasswordResetToken).where(
                        PasswordResetToken.user_id == user.id,
                        PasswordResetToken.used_at.is_(None),
                    )
                ).all():
                    old.used_at = now
                    session.add(old)
                raw = secrets.token_urlsafe(32)
                session.add(PasswordResetToken(
                    user_id=user.id,
                    token_hash=_token_hash(raw),
                    expires_at=now + PASSWORD_RESET_TTL,
                ))
                session.commit()
                link = _reset_link(request, raw)
                _last_reset_link = link
                text_body, html_body = _reset_email_bodies(link)
                try:
                    send_email(
                        to=user.email,
                        subject="Reset your Compass password",
                        text_body=text_body,
                        html_body=html_body,
                    )
                except Exception:
                    # Swallowing keeps the response existence-independent
                    # (anti-enumeration); the token row is intentionally
                    # kept so a transient SMTP blip doesn't strand the user.
                    log.exception("password reset email send failed")
    # Anti-enumeration: byte-identical neutral 200 no matter what happened
    # above. Deliberately no email echoed, no redirect, no JSON branch.
    return templates.TemplateResponse(
        request, "forgot.html", {"sent": True, "error": None})


@app.get("/reset/{token}", response_class=HTMLResponse)
def reset_page(request: Request, token: str):
    with Session(engine) as session:
        row, _user = _valid_reset(session, token)
    if not row:
        return _reset_resp(request,
                           {"invalid": True, "token": "", "error": None}, 400)
    return _reset_resp(request,
                       {"invalid": False, "token": token, "error": None})


@app.post("/reset/{token}", response_class=HTMLResponse)
def reset_submit(request: Request, token: str,
                 password: str = Form(""), confirm: str = Form("")):
    with Session(engine) as session:
        row, user = _valid_reset(session, token)
        if not row:
            return _reset_resp(
                request, {"invalid": True, "token": "", "error": None}, 400)
        if len(password) < 8:
            return _reset_resp(request, {
                "invalid": False, "token": token,
                "error": "Password must be at least 8 characters."}, 400)
        if len(password.encode("utf-8")) > MAX_PASSWORD_LENGTH:
            return _reset_resp(request, {
                "invalid": False, "token": token,
                "error": f"Password must be at most "
                         f"{MAX_PASSWORD_LENGTH} characters."}, 400)
        if password != confirm:
            return _reset_resp(request, {
                "invalid": False, "token": token,
                "error": "Passwords don't match."}, 400)
        now = datetime.now(timezone.utc)
        user.password_hash = hash_password(password)
        row.used_at = now
        session.add(user)
        session.add(row)
        # Invalidate any other outstanding tokens for this user.
        for other in session.exec(
            select(PasswordResetToken).where(
                PasswordResetToken.user_id == user.id,
                PasswordResetToken.used_at.is_(None),
            )
        ).all():
            other.used_at = now
            session.add(other)
        session.commit()
    # Kill any pre-existing session (fixation defense); force a fresh
    # login with the new password.
    request.session.clear()
    resp = RedirectResponse("/login?reset=1", status_code=303)
    resp.headers["Referrer-Policy"] = "no-referrer"
    return resp


@app.get("/__test__/last_reset_link")
def _test_last_reset_link():
    """Test-only: lets browser tests fetch the most recent reset link
    without parsing compass.log. Hard 404 in production."""
    if COMPASS_ENV == "production":
        raise HTTPException(404)
    return JSONResponse({"link": _last_reset_link or ""})


def _mask_key(key: Optional[str]) -> Optional[str]:
    """Show enough of the key to confirm which one is saved without
    exposing the secret. Used by the /settings page."""
    if not key:
        return None
    if len(key) <= 12:
        return "set"
    return f"{key[:6]}…{key[-4:]}"


def _parse_usage(user: User) -> dict:
    """Single source of truth for syllabus-parse entitlement, shared by
    /me.json, /settings and the /syllabus gate so the count the user sees
    always matches the count the server enforces.

    own_key        → user pays, uncapped (free_parses_remaining is None).
    unlimited_grant → admin flipped this user uncapped on the SHARED key
                   (free_parses_remaining is None, but no own key needed).
    server_key_available → the shared key is configured; keyless accounts
                   can parse up to FREE_PARSE_LIMIT.
    Neither        → keyless upload is blocked (old "add a key" behaviour).

    free_parses_remaining is None whenever the account is uncapped (own key
    OR admin grant) — the /syllabus gate and counter both key off that
    `None`, so a single test ("is there a finite budget?") covers both
    uncapped paths and never compares None to a number."""
    own = bool((user.xai_api_key or "").strip())
    granted = bool(getattr(user, "unlimited_parses", False))
    uncapped = own or granted
    used = user.free_parses_used or 0
    return {
        "own_key": own,
        "unlimited_grant": granted,
        "server_key_available": bool(XAI_API_KEY),
        "free_parses_used": used,
        "free_parse_limit": FREE_PARSE_LIMIT,
        # None == unlimited (own key or admin-granted).
        "free_parses_remaining": None if uncapped else max(0, FREE_PARSE_LIMIT - used),
    }


def _calendar_urls(request: Request, token: str) -> dict:
    """Build the subscribe URLs shown on /settings. `webcal_url` triggers
    Apple Calendar (and other clients that register the scheme) to pop up
    a 'subscribe?' dialog when clicked."""
    base = str(request.base_url).rstrip("/")
    https_url = f"{base}/calendar/{token}.ics"
    # Strip any scheme from the base, then prefix with webcal:// so Apple
    # Calendar etc. intercept the click.
    no_scheme = https_url.split("://", 1)[-1]
    return {
        "https_url": https_url,
        "webcal_url": f"webcal://{no_scheme}",
    }


@app.get("/settings", response_class=HTMLResponse)
def settings_page(
    request: Request,
    saved: int = 0,
    need_key: int = 0,
    limit: int = 0,
    user: User = Depends(require_login),
):
    return templates.TemplateResponse(request, "settings.html", {
        "user": user,
        "masked_key": _mask_key(user.xai_api_key),
        "saved": bool(saved),
        "need_key": bool(need_key),
        "limit_reached": bool(limit),
        "usage": _parse_usage(user),
        "is_admin": _is_admin(user),
        "error": None,
        "calendar_urls": _calendar_urls(request, user.calendar_token),
    })


@app.post("/settings/timezone")
async def settings_set_timezone(request: Request, user: User = Depends(require_login)):
    """Auto-saved on every page load via base.html JS. Validates the
    string against ZoneInfo's known set so a malicious or misconfigured
    client can't poison the user's saved timezone (we'd then render dates
    in a bogus tz). Silent no-op when the new value matches what's stored
    so we don't write to DB on every navigation."""
    form = await request.form()
    raw = (form.get("tz") or "").strip()
    if not raw:
        return JSONResponse({"saved": False, "reason": "empty"}, status_code=400)
    try:
        ZoneInfo(raw)
    except Exception:
        return JSONResponse({"saved": False, "reason": "invalid"}, status_code=400)
    if user.timezone == raw:
        return JSONResponse({"saved": True, "unchanged": True})
    with Session(engine) as session:
        u = session.get(User, user.id)
        u.timezone = raw
        session.add(u)
        session.commit()
    return JSONResponse({"saved": True})


@app.post("/settings/calendar/regenerate")
def settings_regenerate_calendar_token(request: Request, user: User = Depends(require_login)):
    """Rotate the iCal subscription token. Existing subscriptions break
    immediately — anyone holding the old URL gets 404. User must
    re-subscribe with the new URL."""
    with Session(engine) as session:
        u = session.get(User, user.id)
        u.calendar_token = secrets.token_urlsafe(32)
        session.add(u)
        session.commit()
        new_token = u.calendar_token
    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse({
            "calendar_token": new_token,
            "calendar_urls": _calendar_urls(request, new_token),
        })
    return RedirectResponse("/settings?saved=1", status_code=303)


@app.post("/settings")
def settings_save(
    request: Request,
    xai_api_key: str = Form(""),
    user: User = Depends(require_login),
):
    wants_json = "application/json" in request.headers.get("accept", "")
    key = xai_api_key.strip()
    if key and not key.startswith("xai-"):
        msg = "xAI keys start with 'xai-'. Get one at https://console.x.ai/"
        if wants_json:
            return JSONResponse({"error": msg}, status_code=400)
        return templates.TemplateResponse(request, "settings.html", {
            "user": user,
            "masked_key": _mask_key(user.xai_api_key),
            "saved": False,
            "need_key": False,
            "limit_reached": False,
            "usage": _parse_usage(user),
            "is_admin": _is_admin(user),
            "error": msg,
            "calendar_urls": _calendar_urls(request, user.calendar_token),
        }, status_code=400)
    with Session(engine) as session:
        u = session.get(User, user.id)
        u.xai_api_key = key or None
        session.add(u)
        session.commit()
        new_key = u.xai_api_key
    if wants_json:
        return JSONResponse({
            "saved": True,
            "xai_api_key_set": bool(new_key),
            "xai_api_key_masked": _mask_key(new_key),
        })
    return RedirectResponse("/settings?saved=1", status_code=303)


# ---- Routes: Admin dashboard (ADMIN_EMAILS allowlist only) ----

@app.get("/admin", response_class=HTMLResponse)
def admin_dashboard(request: Request, admin: User = Depends(require_admin)):
    with Session(engine) as session:
        users = session.exec(select(User).order_by(User.created_at)).all()
        rows = []
        for u in users:
            usage = _parse_usage(u)
            if usage["own_key"]:
                status = "Own key (uncapped)"
            elif usage["unlimited_grant"]:
                status = "Unlimited — granted"
            elif not usage["server_key_available"]:
                status = "No key configured"
            else:
                status = f"Capped {usage['free_parses_used']}/{usage['free_parse_limit']}"
            rows.append({
                "id": u.id,
                "email": u.email,
                "created_at": u.created_at,
                "free_parses_used": u.free_parses_used or 0,
                "unlimited": bool(u.unlimited_parses),
                "is_self": u.id == admin.id,
                "status": status,
            })
    return templates.TemplateResponse(request, "admin.html", {
        "rows": rows,
        "free_parse_limit": FREE_PARSE_LIMIT,
        "saved": bool(request.query_params.get("saved")),
    })


@app.post("/admin/users/{user_id}/unlimited")
def admin_set_unlimited(
    request: Request,
    user_id: int,
    grant: str = Form(...),
    admin: User = Depends(require_admin),
):
    """Toggle a user's admin-granted uncapped parsing. Cross-user by
    design — this is the one sanctioned place we touch another account's
    row directly (the ownership helpers deliberately 404 cross-user)."""
    want = grant.strip() == "1"
    with Session(engine) as session:
        target = session.get(User, user_id)
        if not target:
            raise HTTPException(404)
        target.unlimited_parses = want
        session.add(target)
        session.commit()
    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse({"id": user_id, "unlimited": want})
    return RedirectResponse("/admin?saved=1", status_code=303)


@app.get("/", response_class=HTMLResponse)
def home(request: Request, user: User = Depends(require_login)):
    tz = _user_tz(user)
    today_start = _today_local(tz)
    today_end = today_start + timedelta(days=1)
    today_items = _collect_items_in_range(today_start, today_end, user.id,
                                          tz=tz, hide_completed=True)
    overdue = _collect_overdue(user.id, tz=tz)
    today_buckets = _merge_today_with_overdue(today_items, overdue, user.id)
    with Session(engine, expire_on_commit=False) as session:
        classes = session.exec(
            select(Class).where(Class.user_id == user.id).order_by(Class.code)
        ).all()
        all_tags = session.exec(
            select(Tag).where(Tag.user_id == user.id).order_by(Tag.name)
        ).all()
    return templates.TemplateResponse(request, "home.html", {
        "classes": classes,
        "today": today_start,
        "today_buckets": today_buckets,
        "default_class_id": (classes[0].id if classes else None),
        "all_tags": all_tags,
    })


@app.post("/classes")
def add_class(
    request: Request,
    name: str = Form(...),
    code: str = Form(...),
    user: User = Depends(require_login),
):
    with Session(engine) as session:
        cls = Class(user_id=user.id, name=name.strip(), code=code.strip().upper())
        session.add(cls)
        session.commit()
        session.refresh(cls)
        if "application/json" in request.headers.get("accept", ""):
            return JSONResponse({"id": cls.id, "code": cls.code, "name": cls.name})
    return RedirectResponse(url="/", status_code=303)


@app.get("/me.json")
def me_json(request: Request, user: User = Depends(require_login)):
    """Light auth-check + identity endpoint. Also carries the fields the
    extension's Settings surface needs (xAI key set/masked, calendar
    token + URLs) so a single fetch boots the entire panel."""
    return JSONResponse({
        "id": user.id,
        "email": user.email,
        "timezone": user.timezone,
        "xai_api_key_set": bool(user.xai_api_key),
        "xai_api_key_masked": _mask_key(user.xai_api_key),
        # Syllabus-parse entitlement so the panel can show "N free parses
        # left" and decide whether to enable the Upload-syllabus button.
        **_parse_usage(user),
        "is_admin": _is_admin(user),
        "calendar_token": user.calendar_token,
        "calendar_urls": _calendar_urls(request, user.calendar_token),
    })


@app.get("/classes.json")
def classes_json(user: User = Depends(require_login)):
    with Session(engine) as session:
        classes = session.exec(
            select(Class).where(Class.user_id == user.id).order_by(Class.code)
        ).all()
        return JSONResponse([
            {"id": c.id, "code": c.code, "name": c.name} for c in classes
        ])


def _serialize_item(it: dict) -> dict:
    """Flatten a collector item dict into JSON-safe shape — datetimes as
    ISO strings, plus an `is_personal` hint for the bucket header. Used
    by `/today.json` for the browser extension's side panel."""
    def iso(dt):
        return dt.isoformat() if dt is not None else None
    return {
        "kind": it["kind"],
        "id": it["id"],
        "class_id": it["class_id"],
        "title": it["title"],
        "due_at": iso(it["due_at"]),
        "starts_at": iso(it["starts_at"]),
        "is_range": it["is_range"],
        "is_range_day": it["is_range_day"],
        "is_all_day": it["is_all_day"],
        "completed": it["completed"],
        "actionable": it.get("actionable", True),
        "tag_color": it["tag_color"],
        "tag_name": it["tag_name"],
        "tag_id": it["tag_id"],
        "sub_kind": it.get("sub_kind"),
        "sub_kind_color": it.get("sub_kind_color"),
        "notes": it.get("notes"),
        "rrule": it.get("rrule"),
    }


@app.get("/today.json")
def today_json(user: User = Depends(require_login)):
    """JSON shape of today's view (today + overdue, merged + class-scoped).
    Powers the browser extension's side panel; the HTML `/today` route
    keeps doing its thing for the web app."""
    tz = _user_tz(user)
    today_start = _today_local(tz)
    today_end = today_start + timedelta(days=1)
    today_items = _collect_items_in_range(today_start, today_end, user.id,
                                          tz=tz, hide_completed=True)
    overdue = _collect_overdue(user.id, tz=tz)
    today_buckets = _merge_today_with_overdue(today_items, overdue, user.id)
    return JSONResponse({
        "today": today_start.date().isoformat(),
        "buckets": [
            {
                "class_id": slot["cls"].id,
                "code": slot["cls"].code,
                "name": getattr(slot["cls"], "name", "") or "",
                "is_personal": getattr(slot["cls"], "is_personal", False),
                "items": [_serialize_item(it) for it in slot["items"]],
                "overdue_items": [_serialize_item(it) for it in slot["overdue_items"]],
            }
            for slot in today_buckets.values()
        ],
    })


@app.get("/week.json")
def week_json(user: User = Depends(require_login), days: int = 7):
    """Rolling N-day window starting today, JSON shape. Powers the side
    panel's Week tab. Each day carries its own bucket list (one entry
    per class with at least one item that day). Caps `days` at 14 so a
    pathological caller can't ask for an unbounded window."""
    days = max(1, min(days, 14))
    tz = _user_tz(user)
    today_start = _today_local(tz)
    out_days = []
    for i in range(days):
        day_start = today_start + timedelta(days=i)
        day_end = day_start + timedelta(days=1)
        items_by_class = _collect_items_in_range(
            day_start, day_end, user.id,
            tz=tz,
            day_for_overrides=day_start.strftime("%Y-%m-%d"),
        )
        out_days.append({
            "date": day_start.date().isoformat(),
            "is_today": i == 0,
            "buckets": [
                {
                    "class_id": slot["cls"].id,
                    "code": slot["cls"].code,
                    "name": getattr(slot["cls"], "name", "") or "",
                    "is_personal": getattr(slot["cls"], "is_personal", False),
                    "items": [_serialize_item(it) for it in slot["items"]],
                }
                for slot in items_by_class.values()
            ],
        })
    return JSONResponse({
        "today": today_start.date().isoformat(),
        "days": out_days,
    })


@app.get("/month.json")
def month_json(user: User = Depends(require_login), month: Optional[str] = None):
    """All days in the requested YYYY-MM, JSON shape. Powers the
    extension side panel's Month view — vertical list of day-cards with
    prev/next month nav. Same per-day bucket shape as `/week.json`."""
    tz = _user_tz(user)
    today_start = _today_local(tz)
    target_year, target_month = today_start.year, today_start.month
    if month:
        try:
            y, m = month.split("-")
            ty, tm = int(y), int(m)
            if 1 <= tm <= 12:
                target_year, target_month = ty, tm
        except (ValueError, AttributeError):
            pass
    anchor = datetime(target_year, target_month, 1, tzinfo=tz)
    next_first = (anchor.replace(day=28) + timedelta(days=4)).replace(day=1)
    n_days = (next_first - anchor).days
    out_days = []
    for i in range(n_days):
        day_start = anchor + timedelta(days=i)
        day_end = day_start + timedelta(days=1)
        items_by_class = _collect_items_in_range(
            day_start, day_end, user.id,
            tz=tz,
            day_for_overrides=day_start.strftime("%Y-%m-%d"),
        )
        out_days.append({
            "date": day_start.date().isoformat(),
            "is_today": day_start.date() == today_start.date(),
            "buckets": [
                {
                    "class_id": slot["cls"].id,
                    "code": slot["cls"].code,
                    "name": getattr(slot["cls"], "name", "") or "",
                    "is_personal": getattr(slot["cls"], "is_personal", False),
                    "items": [_serialize_item(it) for it in slot["items"]],
                }
                for slot in items_by_class.values()
            ],
        })
    if target_month == 1:
        prev_y, prev_m = target_year - 1, 12
    else:
        prev_y, prev_m = target_year, target_month - 1
    if target_month == 12:
        next_y, next_m = target_year + 1, 1
    else:
        next_y, next_m = target_year, target_month + 1
    return JSONResponse({
        "month": f"{target_year:04d}-{target_month:02d}",
        "label": anchor.strftime("%B %Y"),
        "today": today_start.date().isoformat(),
        "days": out_days,
        "prev_month": f"{prev_y:04d}-{prev_m:02d}",
        "next_month": f"{next_y:04d}-{next_m:02d}",
    })


@app.get("/classes/{class_id}.json")
def class_detail_json(class_id: int, user: User = Depends(require_login)):
    """Class-scoped event + task list for the browser extension's class
    detail surface. Events come back sorted chronologically; tasks are
    sorted by drag-priority then due date. Past events are included so
    the side panel can show the full course schedule (the UI decides
    whether to collapse them)."""
    tz = _user_tz(user)
    with Session(engine, expire_on_commit=False) as session:
        cls = _own_class(session, class_id, user.id)
        # System-tag lookup for event sub_kind color/name (mirrors the
        # collector). Cached per-call; cheap.
        sys_tag_by_key = {
            t.system_key: t
            for t in session.exec(
                select(Tag).where(Tag.is_system == True, Tag.user_id == user.id)
            ).all()
            if t.system_key
        }
        events = sorted(
            (e for e in cls.events if e.starts_at is not None),
            key=lambda e: e.starts_at,
        )
        tasks = sorted(
            (t for t in cls.tasks if t.completed_at is None),
            key=lambda t: (t.position or 0, t.due_at or datetime.max),
        )
        def _ev_dict(ev):
            sys_tag = sys_tag_by_key.get(ev.kind)
            return {
                "kind": "event",
                "id": ev.id,
                "class_id": ev.class_id,
                "title": ev.title,
                "due_at": _to_local(ev.starts_at, tz).isoformat() if ev.starts_at else None,
                "starts_at": None,
                "is_range": False,
                "is_range_day": False,
                "is_all_day": False,
                "completed": ev.completed_at is not None,
                "actionable": ev.actionable,
                "sub_kind": sys_tag.name if sys_tag else ev.kind,
                "sub_kind_color": sys_tag.color if sys_tag else None,
                "tag_color": None, "tag_name": None, "tag_id": None,
                "notes": None,
                "rrule": None,
            }
        def _t_dict(t):
            return {
                "kind": "task",
                "id": t.id,
                "class_id": t.class_id,
                "title": t.title,
                "due_at": _to_local(t.due_at, tz).isoformat() if t.due_at else None,
                "starts_at": _to_local(t.starts_at, tz).isoformat() if t.starts_at else None,
                "is_range": t.starts_at is not None,
                "is_range_day": False,
                "is_all_day": t.is_all_day,
                "completed": False,
                "actionable": True,
                "sub_kind": None, "sub_kind_color": None,
                "tag_color": t.tag.color if t.tag else None,
                "tag_name": t.tag.name if t.tag else None,
                "tag_id": t.tag.id if t.tag else None,
                "notes": t.notes,
                "rrule": t.rrule,
            }
        latest_syllabus = (
            max(cls.syllabi, key=lambda s: s.parsed_at) if cls.syllabi else None
        )
        documents = sorted(cls.documents, key=lambda d: d.uploaded_at, reverse=True)
        return JSONResponse({
            "class": {
                "id": cls.id, "code": cls.code, "name": cls.name,
                "is_personal": False,
            },
            "events": [_ev_dict(ev) for ev in events],
            "tasks": [_t_dict(t) for t in tasks],
            "syllabus": (
                {
                    "id": latest_syllabus.id,
                    "filename": latest_syllabus.filename,
                    "parsed_at": latest_syllabus.parsed_at.isoformat()
                        if latest_syllabus.parsed_at else None,
                }
                if latest_syllabus else None
            ),
            "documents": [
                {
                    "id": d.id,
                    "title": d.title,
                    "filename": d.filename,
                    "uploaded_at": d.uploaded_at.isoformat() if d.uploaded_at else None,
                }
                for d in documents
            ],
        })


@app.get("/classes/{class_id}", response_class=HTMLResponse)
def class_detail(request: Request, class_id: int, user: User = Depends(require_login)):
    with Session(engine) as session:
        cls = session.get(Class, class_id)
        if not cls or cls.user_id != user.id:
            raise HTTPException(404, "Class not found")
        events = sorted(cls.events, key=event_sort_key)
        documents = sorted(cls.documents, key=lambda d: d.uploaded_at, reverse=True)
        latest_syllabus = max(cls.syllabi, key=lambda s: s.parsed_at) if cls.syllabi else None
    # Floating tasks panel reuses the home page's today list. Add-task form
    # defaults to the current class.
    tz = _user_tz(user)
    today_start = _today_local(tz)
    today_end = today_start + timedelta(days=1)
    today_items = _collect_items_in_range(today_start, today_end, user.id,
                                          tz=tz, hide_completed=True)
    overdue = _collect_overdue(user.id, tz=tz)
    today_buckets = _merge_today_with_overdue(today_items, overdue, user.id)
    with Session(engine, expire_on_commit=False) as session:
        all_classes = session.exec(
            select(Class).where(Class.user_id == user.id).order_by(Class.code)
        ).all()
        all_tags = session.exec(
            select(Tag).where(Tag.user_id == user.id).order_by(Tag.name)
        ).all()
    return templates.TemplateResponse(request, "class.html", {
        "cls": cls,
        "events": events,
        "documents": documents,
        "syllabus": latest_syllabus,
        "today": today_start,
        "today_buckets": today_buckets,
        "all_classes": all_classes,
        "default_class_id": cls.id,
        "all_tags": all_tags,
    })


@app.post("/classes/{class_id}/delete")
def delete_class(class_id: int, request: Request, user: User = Depends(require_login)):
    with Session(engine) as session:
        cls = session.get(Class, class_id)
        if not cls or cls.user_id != user.id:
            raise HTTPException(404, "Class not found")
        # Remember syllabus IDs being cascade-deleted so we can clean up the
        # in-memory parse_jobs dict — otherwise stale "done" entries linger
        # and confuse status pages for re-used IDs.
        deleted_syllabus_ids = [s.id for s in cls.syllabi]
        # Detach tasks first so they survive the class deletion as
        # Personal tasks. Without this, the FK would either cascade-delete
        # them (old behavior) or fail.
        for t in cls.tasks:
            t.class_id = None
            session.add(t)
        session.flush()
        session.delete(cls)
        session.commit()
    for sid in deleted_syllabus_ids:
        parse_jobs.pop(sid, None)
    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse({"deleted": class_id})
    return RedirectResponse(url="/", status_code=303)


# ---- Routes: Syllabus upload + parsing ----

@app.post("/syllabus")
async def syllabus_upload(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    user: User = Depends(require_login),
):
    wants_json = "application/json" in request.headers.get("accept", "")
    usage = _parse_usage(user)
    # Entitlement gate. Users on their own key sail through (uncapped).
    # Keyless users ride the shared server key up to FREE_PARSE_LIMIT; past
    # that they must add their own key. If no server key is configured at
    # all (dev/tests), keyless upload is blocked outright as before.
    if not usage["own_key"]:
        if not usage["server_key_available"]:
            if wants_json:
                return JSONResponse({"error": "need_key"}, status_code=400)
            return RedirectResponse("/settings?need_key=1", status_code=303)
        # remaining is None for an admin-granted account → uncapped, falls
        # through. A finite remaining at/below 0 is the only block here.
        if usage["free_parses_remaining"] is not None and usage["free_parses_remaining"] <= 0:
            if wants_json:
                return JSONResponse(
                    {"error": "limit_reached",
                     "free_parse_limit": FREE_PARSE_LIMIT},
                    status_code=400,
                )
            return RedirectResponse("/settings?limit=1", status_code=303)
    content = await file.read()
    validate_pdf(content)

    safe_name = safe_filename(file.filename or "syllabus.pdf")
    filename = f"{uuid.uuid4().hex[:8]}_{safe_name}"
    storage.save(filename, content, content_type="application/pdf")

    try:
        raw_text = extract_pdf_text(content)
    except Exception as e:
        raise HTTPException(400, f"Could not extract text from PDF: {e}")

    if not raw_text.strip():
        raise HTTPException(400, "PDF appears to have no extractable text (might be a scanned image)")

    with Session(engine) as session:
        cls = Class(user_id=user.id, code="TBD", name="Parsing...")
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
        class_id = cls.id

    # Spend one free parse — only on the capped path (no own key AND no
    # admin grant; both make free_parses_remaining None). Counting at
    # enqueue (not on parse success) keeps the remaining count the user
    # sees immediately truthful and stops a burst of uploads from slipping
    # past the cap before any job finishes. A failed parse therefore still
    # costs a credit; acceptable at this cap.
    if usage["free_parses_remaining"] is not None:
        with Session(engine) as session:
            u = session.get(User, user.id)
            u.free_parses_used = (u.free_parses_used or 0) + 1
            session.add(u)
            session.commit()

    parse_jobs[syllabus_id] = "pending"
    background_tasks.add_task(process_syllabus, syllabus_id)

    if wants_json:
        return JSONResponse({"syllabus_id": syllabus_id, "class_id": class_id})
    return RedirectResponse(url=f"/syllabus/{syllabus_id}/status", status_code=303)


@app.get("/syllabus/{syllabus_id}/status", response_class=HTMLResponse)
def syllabus_status(request: Request, syllabus_id: int, user: User = Depends(require_login)):
    """Render the parsing status page. If the syllabus row is gone (the user
    deleted its class in another tab), render a friendly missing-state card
    with a Home link instead of 404'ing."""
    with Session(engine) as session:
        syllabus = _own_syllabus(session, syllabus_id, user.id)
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
def syllabus_status_json(syllabus_id: int, user: User = Depends(require_login)):
    with Session(engine) as session:
        syllabus = _own_syllabus(session, syllabus_id, user.id)
        if not syllabus:
            # Syllabus row deleted (e.g. user wiped its class in another tab).
            # Distinct from "unknown" so the polling page can stop and show a
            # clear missing-state UI instead of looping forever.
            parse_jobs.pop(syllabus_id, None)  # cleanup stale memory entry
            return JSONResponse({"status": "missing"})
        status = parse_jobs.get(syllabus_id, "unknown")
        return JSONResponse({"status": status, "class_id": syllabus.class_id})


# ---- Routes: Event edit/delete ----

@app.post("/events/{event_id}/edit")
def edit_event(
    event_id: int,
    request: Request,
    title: str = Form(...),
    kind: str = Form(...),
    starts_at: str = Form(""),
    ends_at: str = Form(""),
    user: User = Depends(require_login),
):
    with Session(engine) as session:
        ev = _own_event(session, event_id, user.id)
        ev.title = title.strip() or "Untitled"
        new_kind = (kind.strip() or "milestone").lower()
        ev.kind = new_kind
        # First-time use of a kind from manual edit auto-creates its tag,
        # same as Grok-extracted kinds. Without this the collector finds
        # no system tag and the pill renders uncolored.
        _ensure_system_tag(session, user.id, new_kind)
        ev.starts_at = parse_iso_dt(starts_at) if starts_at else None
        ev.ends_at = parse_iso_dt(ends_at) if ends_at else None
        cls_id = ev.class_id
        session.add(ev)
        session.commit()
        session.refresh(ev)
        if "application/json" in request.headers.get("accept", ""):
            return JSONResponse({
                "id": ev.id,
                "title": ev.title,
                "kind": ev.kind,
                "starts_at": ev.starts_at.isoformat() if ev.starts_at else None,
                "ends_at": ev.ends_at.isoformat() if ev.ends_at else None,
                "class_id": cls_id,
            })
    return RedirectResponse(f"/classes/{cls_id}", status_code=303)


@app.post("/events/{event_id}/clone")
def clone_event(event_id: int, request: Request, user: User = Depends(require_login)):
    """Duplicate an event so it shows on the calendar as a separate row.
    Same class, same title/kind/starts_at/ends_at — fresh id. If the
    user clicks twice and gets two copies, that's on them to clean up."""
    with Session(engine) as session:
        ev = _own_event(session, event_id, user.id)
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


@app.post("/events/{event_id}/delete")
def delete_event(event_id: int, request: Request, user: User = Depends(require_login)):
    with Session(engine) as session:
        ev = _own_event(session, event_id, user.id)
        cls_id = ev.class_id
        session.delete(ev)
        session.commit()
    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse({"deleted": event_id})
    return RedirectResponse(f"/classes/{cls_id}", status_code=303)


# ---- Helpers shared by PDF table marker rendering ----

import re as _re


# ---- Routes: Tasks ----

def _user_tz(user) -> ZoneInfo:
    """Resolve the IANA tz string on a User row to a ZoneInfo, falling
    back to LOCAL_TZ on missing/invalid values. Used by the today/
    overdue/week paths and the iCal feed so each account renders dates
    in its own local time instead of the server's hardcoded LOCAL_TZ."""
    raw = getattr(user, "timezone", None) if user is not None else None
    if not raw:
        return LOCAL_TZ
    try:
        return ZoneInfo(raw)
    except Exception:
        return LOCAL_TZ


def _today_local(tz: ZoneInfo = LOCAL_TZ) -> datetime:
    """Today's date at midnight in the given tz (default LOCAL_TZ)."""
    now = datetime.now(tz)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def _to_local(dt: Optional[datetime], tz: ZoneInfo = LOCAL_TZ) -> Optional[datetime]:
    """Attach tz if naive, else convert. None passes through. The default
    tz is LOCAL_TZ so single-user / unscoped callers keep working."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt.astimezone(tz)


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


# Smart default alerts by tag: keyed by lowercase tag name OR system_key.
# Picked up at task-creation time when the client doesn't pass an explicit
# alerts list. Users can override (add/remove) in the modal afterwards.
_DEFAULT_ALERTS_BY_TAG = {
    "exam":        [1440, 60],   # 1 day + 1 hour before
    "midterm":     [1440, 60],
    "final":       [1440, 60],
    "quiz":        [1440],       # 1 day before
    "project":     [1440],
    "paper":       [1440],
    "problem set": [60],
    "assignment":  [60],
    "deadline":    [60],
    "milestone":   [1440],
}
_DEFAULT_ALERT_FALLBACK = [15]  # 15 min before — what we used to ship for everything


def _default_alerts_for_tag(tag: Optional["Tag"]) -> list[int]:
    if not tag:
        return list(_DEFAULT_ALERT_FALLBACK)
    for key in (tag.system_key, tag.name):
        if not key:
            continue
        preset = _DEFAULT_ALERTS_BY_TAG.get(key.lower().strip())
        if preset is not None:
            return list(preset)
    return list(_DEFAULT_ALERT_FALLBACK)


def _replace_task_alerts(session: "Session", task_id: int, minutes_list: list[int]) -> None:
    """Remove all existing alerts for the task and write the given list.
    Dedupes and clamps to 0..40320 (4 weeks) so we don't store nonsense."""
    for existing in session.exec(
        select(TaskAlert).where(TaskAlert.task_id == task_id)
    ).all():
        session.delete(existing)
    seen: set[int] = set()
    for raw in minutes_list:
        try:
            m = int(raw)
        except (TypeError, ValueError):
            continue
        if m < 0 or m > 40320:
            continue
        if m in seen:
            continue
        seen.add(m)
        session.add(TaskAlert(task_id=task_id, minutes_before=m))


def _parse_alerts_form(raw: Optional[str]) -> Optional[list[int]]:
    """Parse the comma-separated alerts string the client sends. Returns
    None when the field isn't present at all (caller should fall back to
    smart defaults), or a (possibly empty) list when the user explicitly
    set it (including 'no alerts' = empty)."""
    if raw is None:
        return None
    raw = raw.strip()
    if not raw:
        return []
    out: list[int] = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            out.append(int(chunk))
        except ValueError:
            continue
    return out


# Allow-list of RRULE patterns the modal can send. Validating against this
# prevents stored-XSS-style injection of weird RRULE fragments and keeps the
# edit surface narrow. "Custom…" RRULEs aren't supported in v1.
_ALLOWED_RRULES = {
    "FREQ=DAILY",
    "FREQ=WEEKLY",
    "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR",
    "FREQ=MONTHLY",
}


def _normalize_rrule(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    s = raw.strip().upper()
    if not s:
        return None
    return s if s in _ALLOWED_RRULES else None


def _parse_exdates(raw: Optional[str]) -> set:
    """Parse the JSON list of ISO datetime strings stored on
    `Task.rrule_exdates` into a set of LOCAL_TZ-aware datetimes for fast
    membership checks during expansion. Bad JSON / bad entries are ignored
    silently — we'd rather emit too many occurrences than break the view."""
    if not raw:
        return set()
    try:
        items = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return set()
    if not isinstance(items, list):
        return set()
    out = set()
    for s in items:
        dt = parse_iso_dt(s) if isinstance(s, str) else None
        if dt is not None:
            out.add(dt)
    return out


def _expand_rrule_in_window(
    anchor: datetime,
    rrule: Optional[str],
    window_start: datetime,
    window_end: datetime,
    until: Optional[datetime] = None,
    exdates: Optional[set] = None,
) -> list[datetime]:
    """Return every occurrence of a recurring task that falls in
    [window_start, window_end). `anchor` is the original due_at.

    No `rrule` → returns the anchor if it's in the window, else empty.
    Iterates day-by-day (or month-by-month for FREQ=MONTHLY) so today/week
    views can render an instance per occurrence. Capped at 2000 iterations
    so a degenerate inputs can't burn the request.

    `until` (optional) caps the recurrence — occurrences strictly after
    this datetime are dropped. `exdates` is a set of LOCAL_TZ datetimes
    to skip (typically populated when the user "deletes just this one"
    on a recurring instance)."""
    MAX = 2000
    skip = exdates or set()
    if anchor is None:
        return []
    if not rrule:
        if window_start <= anchor < window_end and anchor not in skip:
            if until is None or anchor <= until:
                return [anchor]
        return []

    out: list[datetime] = []

    def _accept(cur: datetime) -> bool:
        if cur < window_start or cur >= window_end:
            return False
        if until is not None and cur > until:
            return False
        if cur in skip:
            return False
        return True

    if rrule == "FREQ=DAILY":
        # Fast-forward to window if the task started in the past, else
        # we'd iterate years of history.
        cur = anchor
        if cur < window_start:
            delta_days = (window_start.date() - cur.date()).days
            cur = cur + timedelta(days=delta_days)
        i = 0
        while cur < window_end and i < MAX:
            if until is not None and cur > until:
                break
            if _accept(cur):
                out.append(cur)
            cur = cur + timedelta(days=1)
            i += 1

    elif rrule == "FREQ=WEEKLY":
        cur = anchor
        if cur < window_start:
            weeks = (window_start.date() - cur.date()).days // 7
            cur = cur + timedelta(weeks=weeks)
            while cur < window_start:
                cur = cur + timedelta(days=7)
        i = 0
        while cur < window_end and i < MAX:
            if until is not None and cur > until:
                break
            if _accept(cur):
                out.append(cur)
            cur = cur + timedelta(days=7)
            i += 1

    elif rrule == "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR":
        # Step day-by-day, emit only Mon-Fri. Anchor's time-of-day applies
        # to every occurrence — that matches RFC 5545 BYDAY semantics.
        cur = anchor
        if cur < window_start:
            cur = window_start.replace(
                hour=anchor.hour, minute=anchor.minute,
                second=anchor.second, microsecond=anchor.microsecond,
            )
        i = 0
        while cur < window_end and i < MAX:
            if until is not None and cur > until:
                break
            if cur.weekday() < 5 and _accept(cur):
                out.append(cur)
            cur = cur + timedelta(days=1)
            i += 1

    elif rrule == "FREQ=MONTHLY":
        # Same day-of-month each month; clamp to last day if the target
        # month doesn't have it (e.g. Jan 31 → Feb 28).
        import calendar as _cal
        cur = anchor
        i = 0
        while cur < window_end and i < MAX:
            if until is not None and cur > until:
                break
            if _accept(cur):
                out.append(cur)
            year, month = cur.year, cur.month + 1
            if month > 12:
                month, year = 1, year + 1
            day = min(anchor.day, _cal.monthrange(year, month)[1])
            cur = cur.replace(year=year, month=month, day=day)
            i += 1

    else:
        # Unrecognized rrule — degrade to single-anchor occurrence.
        if _accept(anchor):
            out.append(anchor)

    return out


# Hex → CSS3 color name mapping for iCal COLOR (RFC 7986 requires CSS3 names).
# Covers our system palette so Apple Calendar / other clients that respect
# per-event color render the right shade. User-defined hex colors fall back
# to the closest match in this small table — exact matches first, then
# nearest-neighbor by RGB distance.
_HEX_TO_CSS3 = {
    "#a04528": "indianred",
    "#2c5f7c": "steelblue",
    "#7b3f61": "mediumvioletred",
    "#9e7b2c": "darkgoldenrod",
    "#5c8a3a": "olivedrab",
    "#506b87": "slategray",
    "#8a4f7a": "mediumvioletred",
    "#6e6b35": "darkkhaki",
    "#3a6b6e": "darkcyan",
    "#a85f3a": "sienna",
    "#4d6b4f": "darkseagreen",
    "#7a5b8c": "mediumpurple",
}

_CSS3_NAMED = [
    ("indianred", (205, 92, 92)),
    ("firebrick", (178, 34, 34)),
    ("crimson", (220, 20, 60)),
    ("tomato", (255, 99, 71)),
    ("coral", (255, 127, 80)),
    ("orangered", (255, 69, 0)),
    ("darkorange", (255, 140, 0)),
    ("orange", (255, 165, 0)),
    ("gold", (255, 215, 0)),
    ("darkgoldenrod", (184, 134, 11)),
    ("goldenrod", (218, 165, 32)),
    ("darkkhaki", (189, 183, 107)),
    ("olive", (128, 128, 0)),
    ("olivedrab", (107, 142, 35)),
    ("yellowgreen", (154, 205, 50)),
    ("darkseagreen", (143, 188, 143)),
    ("forestgreen", (34, 139, 34)),
    ("seagreen", (46, 139, 87)),
    ("teal", (0, 128, 128)),
    ("darkcyan", (0, 139, 139)),
    ("steelblue", (70, 130, 180)),
    ("slategray", (112, 128, 144)),
    ("royalblue", (65, 105, 225)),
    ("mediumpurple", (147, 112, 219)),
    ("purple", (128, 0, 128)),
    ("mediumvioletred", (199, 21, 133)),
    ("sienna", (160, 82, 45)),
    ("saddlebrown", (139, 69, 19)),
    ("gray", (128, 128, 128)),
]


def _hex_to_rgb(hex_color: str) -> Optional[tuple[int, int, int]]:
    s = (hex_color or "").strip().lstrip("#")
    if len(s) != 6:
        return None
    try:
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
    except ValueError:
        return None


def _hex_to_css3_color(hex_color: Optional[str]) -> Optional[str]:
    """Return the CSS3 color name closest to the given hex. Used in the iCal
    feed's COLOR property. Returns None for unparseable input."""
    if not hex_color:
        return None
    direct = _HEX_TO_CSS3.get(hex_color.lower())
    if direct:
        return direct
    rgb = _hex_to_rgb(hex_color)
    if rgb is None:
        return None
    best = None
    best_dist = None
    for name, ref in _CSS3_NAMED:
        d = sum((a - b) ** 2 for a, b in zip(rgb, ref))
        if best_dist is None or d < best_dist:
            best_dist = d
            best = name
    return best


def _create_task_for_user(
    user: User, class_id: Optional[int], request: Request,
    title: str, due_at: str, starts_at: str, tag_id: str, notes: str,
    rrule: str = "", alerts: Optional[list[int]] = None,
    is_all_day: bool = False, rrule_until: str = "",
):
    """Shared body for both /tasks (no class) and /classes/{id}/tasks."""
    title = title.strip()
    if not title:
        raise HTTPException(400, "Title required")
    starts_dt, due_dt = _normalize_task_range(starts_at, due_at)
    # A task must be anchored to at least one date. A dateless task renders
    # only on "today" and then becomes hard to reach to edit/delete (the
    # extension can't surface it at all) — so a missing date silently
    # creates an unmanageable orphan. Default a date-less, non-recurring
    # task's due to *today* so it's always visible and editable. Recurring
    # tasks always carry their anchor in due_at, so they're exempt.
    if due_dt is None and starts_dt is None and not _normalize_rrule(rrule):
        due_dt = _today_local(_user_tz(user)).replace(hour=23, minute=59)
    tag_pk: Optional[int] = None
    if tag_id:
        try:
            tag_pk = int(tag_id)
        except ValueError:
            tag_pk = None
    notes_clean = (notes or "").strip() or None
    rrule_clean = _normalize_rrule(rrule)
    rrule_until_dt = parse_iso_dt(rrule_until) if (rrule_clean and rrule_until) else None
    with Session(engine) as session:
        if class_id is not None:
            _own_class(session, class_id, user.id)  # 404 if not the user's class
        tag_obj: Optional[Tag] = None
        if tag_pk is not None:
            tag_obj = session.get(Tag, tag_pk)
            if not tag_obj or tag_obj.user_id != user.id:
                tag_pk = None
                tag_obj = None
        task = Task(
            user_id=user.id,
            class_id=class_id,
            title=title,
            notes=notes_clean,
            starts_at=starts_dt,
            due_at=due_dt,
            tag_id=tag_pk,
            rrule=rrule_clean,
            rrule_until=rrule_until_dt,
            is_all_day=bool(is_all_day),
            created_at=datetime.now(timezone.utc),
        )
        session.add(task)
        session.flush()
        # Alerts: client-provided list wins, even if empty (= "no alerts").
        # Falling through to smart defaults only when the field was absent
        # from the form entirely.
        chosen_alerts = alerts if alerts is not None else _default_alerts_for_tag(tag_obj)
        if due_dt is not None:
            _replace_task_alerts(session, task.id, chosen_alerts)
        session.commit()
        session.refresh(task)
        if "application/json" in request.headers.get("accept", ""):
            return JSONResponse({
                "id": task.id,
                "title": task.title,
                "due_at": task.due_at.isoformat() if task.due_at else None,
                "completed_at": None,
            })
    redirect_to = f"/classes/{class_id}" if class_id is not None else "/"
    return RedirectResponse(redirect_to, status_code=303)


def _parse_bool_form(raw: Optional[str]) -> bool:
    if not raw:
        return False
    return str(raw).strip().lower() in ("1", "true", "on", "yes")


@app.post("/classes/{class_id}/tasks")
async def create_task(class_id: int, request: Request,
                      title: str = Form(...), due_at: str = Form(""),
                      starts_at: str = Form(""), tag_id: str = Form(""),
                      notes: str = Form(""), rrule: str = Form(""),
                      alerts: Optional[str] = Form(None),
                      is_all_day: str = Form(""),
                      rrule_until: str = Form(""),
                      user: User = Depends(require_login)):
    """Create a manual task on a class."""
    return _create_task_for_user(user, class_id, request,
                                 title, due_at, starts_at, tag_id, notes,
                                 rrule=rrule, alerts=_parse_alerts_form(alerts),
                                 is_all_day=_parse_bool_form(is_all_day),
                                 rrule_until=rrule_until)


@app.post("/tasks")
async def create_personal_task(request: Request,
                               title: str = Form(...), due_at: str = Form(""),
                               starts_at: str = Form(""), tag_id: str = Form(""),
                               notes: str = Form(""), rrule: str = Form(""),
                               alerts: Optional[str] = Form(None),
                               is_all_day: str = Form(""),
                               rrule_until: str = Form(""),
                               user: User = Depends(require_login)):
    """Create a Personal task (no class). Used by the home / today / week
    add-task forms when the user picks the 'Personal' option in the class
    dropdown."""
    return _create_task_for_user(user, None, request,
                                 title, due_at, starts_at, tag_id, notes,
                                 rrule=rrule, alerts=_parse_alerts_form(alerts),
                                 is_all_day=_parse_bool_form(is_all_day),
                                 rrule_until=rrule_until)


@app.get("/tags.json")
def list_tags(user: User = Depends(require_login)):
    with Session(engine) as session:
        tags = session.exec(
            select(Tag).where(Tag.user_id == user.id).order_by(Tag.is_system.desc(), Tag.name)
        ).all()
        return JSONResponse([
            {"id": t.id, "name": t.name, "color": t.color, "is_system": t.is_system}
            for t in tags
        ])


_HEX_COLOR_RE = _re.compile(r"^#[0-9a-fA-F]{6}$")


@app.post("/tags")
async def create_tag(
    name: str = Form(...),
    color: str = Form(...),
    user: User = Depends(require_login),
):
    name = name.strip()
    color = color.strip()
    if not name:
        raise HTTPException(400, "Name required")
    if not _HEX_COLOR_RE.match(color):
        raise HTTPException(400, "Color must be #rrggbb hex")
    with Session(engine) as session:
        tag = Tag(user_id=user.id, name=name, color=color)
        session.add(tag)
        session.commit()
        session.refresh(tag)
        return JSONResponse({"id": tag.id, "name": tag.name, "color": tag.color})


def _now_user_naive(user) -> datetime:
    """Current wall-clock time in the user's local tz, with tzinfo
    stripped. Used for `completed_at` so the SQLite roundtrip (which
    drops tz) lands on a value `_to_local` reads back correctly —
    every other naive datetime in the schema (`due_at`, `starts_at`,
    `rrule_until`) follows the same convention. Storing UTC here was
    the source of the late-night-completion-disappears bug."""
    return datetime.now(_user_tz(user)).replace(tzinfo=None)


@app.post("/tasks/{task_id}/toggle")
def toggle_task(task_id: int, user: User = Depends(require_login)):
    """Flip a task between completed and pending. AJAX-only; returns JSON."""
    with Session(engine) as session:
        t = _own_task(session, task_id, user.id)
        t.completed_at = None if t.completed_at else _now_user_naive(user)
        completed = t.completed_at is not None
        session.add(t)
        session.commit()
    return JSONResponse({"id": task_id, "completed": completed})


@app.post("/events/{event_id}/toggle")
def toggle_event(event_id: int, user: User = Depends(require_login)):
    """Same toggle for syllabus-extracted CalendarEvents."""
    with Session(engine) as session:
        ev = _own_event(session, event_id, user.id)
        ev.completed_at = None if ev.completed_at else _now_user_naive(user)
        completed = ev.completed_at is not None
        session.add(ev)
        session.commit()
    return JSONResponse({"id": event_id, "completed": completed})


@app.post("/tasks/{task_id}/edit")
async def edit_task(task_id: int, request: Request,
                    user: User = Depends(require_login)):
    """Update any subset of task fields. Only fields PRESENT in the
    submitted form are touched — that lets partial-update callers (the
    drag-to-different-class handler, future bulk operations) PATCH a
    single column without clobbering the rest. The full edit modal
    sends every field, so explicit clears (e.g. notes='', tag_id='')
    still work as expected.

    Reads the form directly via `request.form()` because FastAPI's
    Form() collapses "" and "field absent" into the same value, which
    breaks the "did the client intend to clear this?" semantics."""
    form = await request.form()
    with Session(engine) as session:
        t = _own_task(session, task_id, user.id)
        # "Stop recurrence here" mode: the user opened a recurring task
        # on a middle occurrence and switched Repeat from Daily/Weekly/
        # etc. to Doesn't-repeat. Per the chosen UX, that should END the
        # recurrence at this occurrence — past instances keep rendering
        # via the original rrule, current + future drop. We detect this
        # here so the due_at and rrule_until handlers below know to skip
        # their normal updates (which would clobber the anchor / re-clear
        # the cap we're about to set).
        # The form's due_at carries the OCCURRENCE the user clicked Edit
        # on, because the row's data-due-at is set to the expansion date
        # for recurring tasks. Editing the FIRST occurrence falls through
        # to the wipe path (cap_at <= anchor would yield zero renders);
        # there the simpler "make this single-date" behavior is what the
        # user actually wants.
        tz = _user_tz(user)
        cap_at_dt = parse_iso_dt(form.get("due_at") or "") if "due_at" in form else None
        anchor_raw = t.due_at or t.starts_at
        anchor_dt = _to_local(anchor_raw, tz) if anchor_raw else None
        stop_recurrence = bool(
            "rrule" in form
            and not _normalize_rrule(form.get("rrule"))
            and t.rrule
            and cap_at_dt is not None
            and anchor_dt is not None
            and cap_at_dt > anchor_dt
        )
        if "title" in form:
            title = (form.get("title") or "").strip()
            if not title:
                raise HTTPException(400, "Title required")
            t.title = title
        # due_at / starts_at: only modify the field(s) actually present
        # in the form — preserves the other from the DB. Sending only
        # due_at must NOT silently clobber starts_at and vice versa.
        # (The create-time `_normalize_task_range` promotes a lone
        # starts_at to due_at, which is wrong semantics for a partial
        # edit: the user sent ONE field, expecting the other to stay.)
        # In stop-recurrence mode we skip dates entirely; the form's
        # due_at is the cap-occurrence, not a new anchor.
        if ("due_at" in form or "starts_at" in form) and not stop_recurrence:
            if "due_at" in form:
                raw = (form.get("due_at") or "").strip()
                t.due_at = parse_iso_dt(raw) if raw else None
            if "starts_at" in form:
                raw = (form.get("starts_at") or "").strip()
                t.starts_at = parse_iso_dt(raw) if raw else None
            # Swap inverted ranges (defensive, mirrors the create-time
            # normaliser); collapse equal endpoints to single-date.
            # _to_local normalizes naive (DB roundtrip strips tz) and
            # tz-aware (parse_iso_dt output) values onto the same tz.
            if t.starts_at and t.due_at:
                s_cmp = _to_local(t.starts_at)
                d_cmp = _to_local(t.due_at)
                if s_cmp > d_cmp:
                    t.starts_at, t.due_at = t.due_at, t.starts_at
                elif s_cmp == d_cmp:
                    t.starts_at = None
        if "tag_id" in form:
            raw_tag = (form.get("tag_id") or "").strip()
            if not raw_tag:
                t.tag_id = None
            else:
                try:
                    tpk = int(raw_tag)
                    tag = session.get(Tag, tpk)
                    t.tag_id = tpk if (tag and tag.user_id == user.id) else None
                except ValueError:
                    t.tag_id = None
        if "notes" in form:
            t.notes = (form.get("notes") or "").strip() or None
        if "rrule" in form:
            if stop_recurrence:
                # Cap the existing rrule one second before this occurrence
                # — past instances keep rendering with the original rrule
                # (so opening one shows Repeat = Daily, matching the user
                # mental model), the current and future ones drop. Leave
                # t.rrule itself alone; recurrence is capped, not removed.
                t.rrule_until = cap_at_dt - timedelta(seconds=1)
                t.rrule_exdates = None
            else:
                new_rrule = _normalize_rrule(form.get("rrule"))
                # Switching off recurrence clears the per-occurrence cruft
                # so a later "make it recurring again" doesn't inherit
                # stale exdates or a long-expired UNTIL.
                if not new_rrule and t.rrule:
                    t.rrule_until = None
                    t.rrule_exdates = None
                t.rrule = new_rrule
        if "rrule_until" in form and not stop_recurrence:
            raw = (form.get("rrule_until") or "").strip()
            t.rrule_until = parse_iso_dt(raw) if raw else None
        if "is_all_day" in form:
            t.is_all_day = _parse_bool_form(form.get("is_all_day"))
        if "alerts" in form:
            parsed = _parse_alerts_form(form.get("alerts"))
            if parsed is not None:
                _replace_task_alerts(session, t.id, parsed)
        if "class_id" in form:
            raw_class = (form.get("class_id") or "").strip()
            if not raw_class or raw_class == "0":
                # "0" is the Personal sentinel from the dropdown; "" works too.
                t.class_id = None
            else:
                try:
                    cpk = int(raw_class)
                    cls = session.get(Class, cpk)
                    if cls and cls.user_id == user.id:
                        t.class_id = cpk
                    # Foreign class id is silently ignored; don't reset to None
                    # because that would surprise the user.
                except ValueError:
                    pass
        session.add(t)
        session.commit()
        session.refresh(t)
    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse({
            "id": t.id,
            "title": t.title,
            "due_at": t.due_at.isoformat() if t.due_at else None,
            "notes": t.notes,
            "completed_at": t.completed_at.isoformat() if t.completed_at else None,
        })
    redirect_to = f"/classes/{t.class_id}" if t.class_id is not None else "/"
    return RedirectResponse(redirect_to, status_code=303)


@app.post("/tags/{tag_id}/edit")
async def edit_tag(
    tag_id: int,
    name: str = Form(...),
    color: str = Form(...),
    user: User = Depends(require_login),
):
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
        tag = _own_tag(session, tag_id, user.id)
        tag.name = name
        tag.color = color
        # system_key is intentionally NOT touched — that's the key the
        # collector uses to keep events linked to this tag across renames.
        session.add(tag)
        session.commit()
        session.refresh(tag)
        return JSONResponse({"id": tag.id, "name": tag.name, "color": tag.color})


@app.post("/tags/{tag_id}/delete")
async def delete_tag(tag_id: int, user: User = Depends(require_login)):
    with Session(engine) as session:
        tag = _own_tag(session, tag_id, user.id)
        if tag.is_system:
            raise HTTPException(400, "System tags cannot be deleted")
        # Null out tag_id on any of the user's tasks that reference this tag.
        for t in session.exec(
            select(Task).where(Task.tag_id == tag_id, Task.user_id == user.id)
        ).all():
            t.tag_id = None
            session.add(t)
        session.delete(tag)
        session.commit()
        return JSONResponse({"deleted": tag_id})


@app.post("/classes/reorder")
async def reorder_classes(request: Request, user: User = Depends(require_login)):
    """Persist the user's preferred class display order. Body shape:
       {"order": ["3", "0", "1", "2"]} — class ids as strings, with "0"
       representing the Personal bucket. Foreign / unknown ids are dropped."""
    payload = await request.json()
    raw = payload.get("order")
    if not isinstance(raw, list):
        raise HTTPException(400, "order must be a list")
    with Session(engine) as session:
        owned = {
            c.id for c in session.exec(
                select(Class).where(Class.user_id == user.id)
            ).all()
        }
        cleaned: list[int] = []
        seen: set[int] = set()
        for x in raw:
            try:
                v = int(x)
            except (TypeError, ValueError):
                continue
            if v in seen:
                continue
            if v == 0 or v in owned:
                cleaned.append(v)
                seen.add(v)
        u = session.get(User, user.id)
        u.class_order_json = json.dumps([str(v) for v in cleaned])
        session.add(u)
        session.commit()
    return JSONResponse({"ok": True, "order": cleaned})


@app.post("/tasks/reorder")
async def reorder_tasks(request: Request, user: User = Depends(require_login)):
    """Persist drag-to-reorder priority across tasks AND events. Body shape:
       {items: [{kind:'task'|'event', id:int}, ...]}
       Older clients may still send {task_ids:[...]}; we treat that as
       all-tasks for backwards compat. Position 0..N-1 is assigned in the
       order received; non-listed rows are left alone. Skips any row that
       isn't owned by the current user (defense-in-depth)."""
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
            try:
                eid = int(entry.get("id"))
            except (TypeError, ValueError):
                continue
            row = _lookup_owned_item(session, entry.get("kind"), eid, user.id)
            if row is None:
                continue
            row.position = pos
            session.add(row)
            updated += 1
        session.commit()
    return JSONResponse({"reordered": updated})


@app.post("/tasks/reorder-day")
async def reorder_tasks_for_day(request: Request, user: User = Depends(require_login)):
    """Per-day position override (week tab day modal). Body shape:
       {day: 'YYYY-MM-DD', items: [{kind:'task'|'event', id:int}, ...]}

    Each item gets a DayItemPosition row at index N in the list — that
    day's render uses these in place of the global Task/Event.position so
    multi-day tasks can be reordered on one day without disturbing the
    others. Items missing here fall back to the global position (handy
    for partial reorders, though the client always sends the full list)."""
    payload = await request.json()
    day = (payload.get("day") or "").strip()
    # Strict YYYY-MM-DD shape — `len == 10` would let "not-a-date" through.
    # Garbage strings would never match in render-time lookups but would
    # accumulate as DayItemPosition rows that nothing reads.
    if not _re.match(r"^\d{4}-\d{2}-\d{2}$", day):
        raise HTTPException(400, "day must be YYYY-MM-DD")
    raw_items = payload.get("items")
    if not isinstance(raw_items, list):
        raise HTTPException(400, "items must be a list")
    with Session(engine) as session:
        # Replace any existing overrides for this user+day so the new list
        # is authoritative — avoids stale rows from prior reorders.
        existing = session.exec(
            select(DayItemPosition).where(
                DayItemPosition.user_id == user.id,
                DayItemPosition.day_date == day,
            )
        ).all()
        existing_by_key = {(r.kind, r.item_id): r for r in existing}
        seen_keys: set[tuple[str, int]] = set()
        updated = 0
        for pos, entry in enumerate(raw_items):
            if not isinstance(entry, dict):
                continue
            kind = entry.get("kind")
            try:
                eid = int(entry.get("id"))
            except (TypeError, ValueError):
                continue
            if _lookup_owned_item(session, kind, eid, user.id) is None:
                continue
            key = (kind, eid)
            seen_keys.add(key)
            existing_row = existing_by_key.get(key)
            if existing_row is None:
                session.add(DayItemPosition(
                    user_id=user.id, kind=kind, item_id=eid,
                    day_date=day, position=pos,
                ))
            else:
                existing_row.position = pos
                session.add(existing_row)
            updated += 1
        # Drop overrides for items no longer in the list (e.g. a task
        # that's been removed from this day) so they revert to global.
        for key, row in existing_by_key.items():
            if key not in seen_keys:
                session.delete(row)
        session.commit()
    return JSONResponse({"reordered": updated})


@app.get("/tasks/{task_id}/details.json")
def task_details_json(task_id: int, user: User = Depends(require_login)):
    """Pull the detail bundle the drawer + edit modal need: rrule, alert
    offsets, and attachment list. Lets the drawer show what's set without
    bloating every row's data attributes."""
    with Session(engine) as session:
        t = _own_task(session, task_id, user.id)
        alerts = sorted(
            (a.minutes_before for a in t.alerts), reverse=True
        )
        attachments = [
            {
                "id": a.id,
                "filename": a.filename,
                "original_name": a.original_name,
                "content_type": a.content_type,
            }
            for a in sorted(t.attachments, key=lambda x: x.uploaded_at)
        ]
        return JSONResponse({
            "id": t.id,
            "rrule": t.rrule or "",
            "rrule_until": (
                t.rrule_until.strftime('%Y-%m-%dT%H:%M') if t.rrule_until else ""
            ),
            "alerts": alerts,
            "attachments": attachments,
        })


@app.post("/tasks/{task_id}/attachments")
async def upload_task_attachment(
    task_id: int,
    file: UploadFile = File(...),
    user: User = Depends(require_login),
):
    """Attach a file to a task. Stored through the storage abstraction so
    local dev (./uploads/) and prod (R2) work the same."""
    content = await file.read()
    validate_upload(content)
    safe_name = safe_filename(file.filename or "attachment")
    storage_key = f"task-{task_id}-{uuid.uuid4().hex[:8]}_{safe_name}"
    content_type = file.content_type or "application/octet-stream"
    with Session(engine) as session:
        _own_task(session, task_id, user.id)
        storage.save(storage_key, content, content_type=content_type)
        att = TaskAttachment(
            task_id=task_id,
            filename=storage_key,
            original_name=safe_name,
            content_type=content_type,
            uploaded_at=datetime.now(timezone.utc),
        )
        session.add(att)
        session.commit()
        session.refresh(att)
        return JSONResponse({
            "id": att.id,
            "filename": att.filename,
            "original_name": att.original_name,
            "content_type": att.content_type,
        })


@app.post("/attachments/{attachment_id}/delete")
def delete_task_attachment(attachment_id: int, user: User = Depends(require_login)):
    with Session(engine) as session:
        att = _own_attachment(session, attachment_id, user.id)
        storage.delete(att.filename)
        session.delete(att)
        session.commit()
    return JSONResponse({"deleted": attachment_id})


@app.get("/calendar/{token}/attachments/{filename}")
def serve_attachment_by_token(token: str, filename: str):
    """Token-authenticated attachment download. Same secret-by-URL pattern
    as the iCal feed — Apple Calendar can't carry session cookies, so the
    user's `calendar_token` doubles as the auth key for ATTACH URIs in the
    feed. Files are still scoped to the token owner's tasks."""
    safe = safe_filename(filename)
    with Session(engine) as session:
        u = session.exec(select(User).where(User.calendar_token == token)).first()
        if not u:
            raise HTTPException(404)
        att = session.exec(
            select(TaskAttachment).where(TaskAttachment.filename == safe)
        ).first()
        if not att:
            raise HTTPException(404)
        # _own_attachment 404s on cross-user; reuse it instead of the
        # hand-rolled task lookup + user_id compare.
        _own_attachment(session, att.id, u.id)
        if not storage.exists(safe):
            raise HTTPException(404)
        return storage.serve(safe, content_type=att.content_type)


@app.post("/tasks/{task_id}/exclude")
async def exclude_task_occurrence(
    task_id: int, request: Request,
    user: User = Depends(require_login),
):
    """Suppress a single occurrence of a recurring task. Reads
    `occurrence_at` (ISO datetime) from the form and appends it to the
    task's `rrule_exdates` JSON list. The expander + iCal feed both
    honor that list so the deleted instance disappears."""
    form = await request.form()
    raw = (form.get("occurrence_at") or "").strip()
    occ = parse_iso_dt(raw)
    if occ is None:
        raise HTTPException(400, "occurrence_at required")
    with Session(engine) as session:
        t = _own_task(session, task_id, user.id)
        if not t.rrule:
            raise HTTPException(400, "Not a recurring task")
        existing = []
        if t.rrule_exdates:
            try:
                parsed = json.loads(t.rrule_exdates)
                if isinstance(parsed, list):
                    existing = [s for s in parsed if isinstance(s, str)]
            except (json.JSONDecodeError, TypeError):
                existing = []
        # Store as ISO with the LOCAL_TZ offset so re-parsing later lands
        # on the same naive datetime the expander iterates against.
        iso = occ.isoformat()
        if iso not in existing:
            existing.append(iso)
        t.rrule_exdates = json.dumps(existing)
        session.add(t)
        session.commit()
    return JSONResponse({"excluded": iso})


@app.post("/tasks/{task_id}/end-after")
async def end_recurrence_after(
    task_id: int, request: Request,
    user: User = Depends(require_login),
):
    """Stop a recurring task at — and including — a given occurrence.
    Sets `rrule_until` to the moment just before the occurrence so the
    expander caps there. Used by the 'Delete this and all future'
    option on a recurring row's delete dialog."""
    form = await request.form()
    raw = (form.get("occurrence_at") or "").strip()
    occ = parse_iso_dt(raw)
    if occ is None:
        raise HTTPException(400, "occurrence_at required")
    with Session(engine) as session:
        t = _own_task(session, task_id, user.id)
        if not t.rrule:
            raise HTTPException(400, "Not a recurring task")
        # 1 second before the deleted occurrence — UNTIL is inclusive in
        # RFC 5545 but we want the deleted instance to also disappear.
        new_until = occ - timedelta(seconds=1)
        t.rrule_until = new_until
        session.add(t)
        session.commit()
    return JSONResponse({"until": new_until.isoformat()})


@app.post("/tasks/{task_id}/delete")
def delete_task(task_id: int, request: Request, user: User = Depends(require_login)):
    with Session(engine) as session:
        t = _own_task(session, task_id, user.id)
        cls_id = t.class_id
        session.delete(t)
        session.commit()
    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse({"deleted": task_id})
    return RedirectResponse(f"/classes/{cls_id}", status_code=303)


# ---- Routes: Today + Week views ----

# Synthetic "class" for tasks that aren't tied to an actual course. Lives
# under id=0 so it slots into the {class_id: bucket} dicts without colliding
# with real class ids (which start at 1). Templates check is_personal to
# render it as a non-clickable header instead of a real class link.
PERSONAL_BUCKET = SimpleNamespace(id=0, code="Personal", name="", is_personal=True)


def _merge_today_with_overdue(today_items: dict, overdue: dict, user_id: int) -> dict:
    """Combine today's items + overdue into one {class_id: bucket} dict.
    Each bucket carries both `items` (today) and `overdue_items` (past).
    Templates render ONE class-block per class with a small "Overdue"
    divider between the two lists, so a class that has both today and
    overdue tasks doesn't end up with duplicated headers across separate
    sections (the old "overdue-section then today-section" layout did).
    Class display order is re-applied across the union of class ids so
    overdue-only classes still slot into the user's preferred order.

    Dedupes by `(kind, id)`: an open task due earlier today is in BOTH
    `_collect_items_in_range` (today's date range) and `_collect_overdue`
    (due_at < now), so without this filter it'd render twice — once in
    `items`, once in `overdue_items` — and look exactly like a duplicate.
    Past-due wins: the user wants the task called out under the Overdue
    cap, not buried in the today list."""
    overdue_keys: set[tuple[str, int]] = set()
    for slot in overdue.values():
        for it in slot["items"]:
            overdue_keys.add((it["kind"], it["id"]))
    merged: dict[int, dict] = {}
    for cls_id, slot in today_items.items():
        kept = [it for it in slot["items"]
                if (it["kind"], it["id"]) not in overdue_keys]
        merged[cls_id] = {
            "cls": slot["cls"],
            "items": kept,
            "overdue_items": [],
        }
    for cls_id, slot in overdue.items():
        if cls_id in merged:
            merged[cls_id]["overdue_items"] = list(slot["items"])
        else:
            merged[cls_id] = {
                "cls": slot["cls"],
                "items": [],
                "overdue_items": list(slot["items"]),
            }
    return _apply_class_order(merged, user_id)


def _apply_class_order(out: dict, user_id: int) -> dict:
    """Re-key `out` (a {class_id: bucket} dict) into the user's preferred
    display order. Buckets not mentioned in the saved order append at the
    end in their existing order (which is class-table insertion order)."""
    if not out:
        return out
    with Session(engine) as session:
        u = session.get(User, user_id)
        saved: list[int] = []
        if u and u.class_order_json:
            try:
                saved = [int(x) for x in json.loads(u.class_order_json)]
            except (ValueError, TypeError, json.JSONDecodeError):
                saved = []
    present = set(out.keys())
    ordered_keys = [k for k in saved if k in present]
    ordered_keys.extend(k for k in out.keys() if k not in ordered_keys)
    return {k: out[k] for k in ordered_keys}


def _collect_items_in_range(start: datetime, end: datetime, user_id: int,
                             day_for_overrides: Optional[str] = None,
                             tz: ZoneInfo = LOCAL_TZ,
                             hide_completed: bool = False) -> dict:
    """Return {class_id: {class, items: [{kind, id, title, due_at, completed, notes}]}}
    for tasks + events whose due/start datetime falls in [start, end). Scoped
    to the given user. Personal tasks (class_id IS NULL) bucket under
    PERSONAL_BUCKET (key 0). Both open and completed items are included
    by default; pass `hide_completed=True` (today/home views) to drop them.

    `tz`: the timezone to anchor "today" / range comparisons in. Default
    LOCAL_TZ keeps single-user paths working; per-user callers should
    pass `_user_tz(user)`.

    `day_for_overrides` (YYYY-MM-DD): when set, look up DayItemPosition
    overrides for that date and use them as the sort key in place of the
    global Task/Event.position. Used by the week page so reordering a
    multi-day task in one day's modal doesn't shuffle other days."""
    out: dict[int, dict] = {}

    def _add(cls, kind: str, item_id: int, title: str,
             when: Optional[datetime], completed: bool,
             position: int = 0, sub_kind: Optional[str] = None,
             tag_color: Optional[str] = None, tag_name: Optional[str] = None,
             tag_id: Optional[int] = None, tag_is_system: bool = False,
             sub_kind_color: Optional[str] = None,
             sub_kind_id: Optional[int] = None,
             starts_at: Optional[datetime] = None,
             is_range: bool = False, is_range_day: bool = False,
             actionable: bool = True,
             notes: Optional[str] = None,
             rrule: Optional[str] = None,
             is_all_day: bool = False):
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
            "notes": notes,
            "rrule": rrule,
            "is_all_day": is_all_day,
        })

    def _emit_task(cls, t):
        """Walk one task into one or more _add() calls.
          - Recurring tasks emit one occurrence per match in the window.
          - Range tasks (no rrule) emit one row per spanned day.
          - Single-date tasks emit once if in window."""
        if hide_completed and t.completed_at is not None:
            # Keep "just-checked-off-today" rows visible (crossed out)
            # for the rest of today — that's what a to-do list view
            # actually means. Once tomorrow arrives, completed_at falls
            # out of the [start, end) window and the row drops on the
            # next page render. Completed-and-overdue tasks (completed
            # before today) are hidden — they're not pending work.
            completed_local = _to_local(t.completed_at, tz)
            if not (start <= completed_local < end):
                return
        tcolor = t.tag.color if t.tag else None
        tname = t.tag.name if t.tag else None
        tpk = t.tag.id if t.tag else None
        tsys = t.tag.is_system if t.tag else False
        tag_kw = dict(tag_color=tcolor, tag_name=tname,
                      tag_id=tpk, tag_is_system=tsys, notes=t.notes,
                      rrule=t.rrule)
        if t.due_at is None and t.starts_at is None:
            if start <= _today_local(tz) < end and not t.completed_at:
                _add(cls, "task", t.id, t.title, None, False,
                     t.position or 0, is_all_day=t.is_all_day, **tag_kw)
            return
        # Recurring tasks: expand rrule across the window. Range data is
        # ignored for recurrence — a task can repeat OR span days, not both.
        if t.rrule:
            anchor = _to_local(t.due_at, tz) if t.due_at is not None else _to_local(t.starts_at, tz)
            until_local = _to_local(t.rrule_until, tz) if t.rrule_until else None
            exdates = _parse_exdates(t.rrule_exdates)
            for occ in _expand_rrule_in_window(
                anchor, t.rrule, start, end,
                until=until_local, exdates=exdates,
            ):
                _add(cls, "task", t.id, t.title, occ,
                     t.completed_at is not None, t.position or 0,
                     is_all_day=t.is_all_day, **tag_kw)
            return
        if t.starts_at is not None and t.due_at is not None:
            starts_local = _to_local(t.starts_at, tz)
            due_local = _to_local(t.due_at, tz)
            last_date = due_local.date()
            d = starts_local.date()
            while d <= last_date:
                day_start = datetime.combine(d, time.min, tzinfo=tz)
                day_end = day_start + timedelta(days=1)
                if day_start < end and day_end > start:
                    is_deadline = (d == last_date)
                    _add(cls, "task", t.id, t.title, due_local,
                         t.completed_at is not None, t.position or 0,
                         starts_at=starts_local, is_range=True,
                         is_range_day=not is_deadline,
                         is_all_day=t.is_all_day, **tag_kw)
                d += timedelta(days=1)
            return
        local_due = _to_local(t.due_at, tz)
        if start <= local_due < end:
            _add(cls, "task", t.id, t.title, local_due,
                 t.completed_at is not None, t.position or 0,
                 is_all_day=t.is_all_day, **tag_kw)

    with Session(engine, expire_on_commit=False) as session:
        sys_tag_by_key = {
            t.system_key: t
            for t in session.exec(
                select(Tag).where(Tag.is_system == True, Tag.user_id == user_id)
            ).all()
            if t.system_key
        }
        for cls in session.exec(select(Class).where(Class.user_id == user_id)).all():
            for t in cls.tasks:
                _emit_task(cls, t)
            for ev in cls.events:
                if ev.starts_at is None:
                    continue
                if hide_completed and ev.completed_at is not None:
                    # Same "just-completed-today" rule as tasks above.
                    completed_local = _to_local(ev.completed_at, tz)
                    if not (start <= completed_local < end):
                        continue
                local_when = _to_local(ev.starts_at, tz)
                if start <= local_when < end:
                    sys_tag = sys_tag_by_key.get(ev.kind)
                    _add(cls, "event", ev.id, ev.title, local_when,
                         ev.completed_at is not None, ev.position or 0,
                         sub_kind=sys_tag.name if sys_tag else ev.kind,
                         sub_kind_color=sys_tag.color if sys_tag else None,
                         sub_kind_id=sys_tag.id if sys_tag else None,
                         actionable=ev.actionable)
        # Personal tasks (no class) — bucket under PERSONAL_BUCKET.
        personal_tasks = session.exec(
            select(Task).where(Task.user_id == user_id, Task.class_id == None)
        ).all()
        for t in personal_tasks:
            _emit_task(PERSONAL_BUCKET, t)
    # Per-day position overrides (week tab only). Map (kind, item_id) →
    # override position; missing keys fall back to the row's global position.
    overrides: dict[tuple[str, int], int] = {}
    if day_for_overrides:
        with Session(engine) as session:
            for row in session.exec(
                select(DayItemPosition).where(
                    DayItemPosition.user_id == user_id,
                    DayItemPosition.day_date == day_for_overrides,
                )
            ).all():
                overrides[(row.kind, row.item_id)] = row.position
    # Sort: position first (user's drag priority), then due time.
    for slot in out.values():
        slot["items"].sort(key=lambda it: (
            overrides.get((it["kind"], it["id"]), it["position"]),
            it["due_at"] is None,
            it["due_at"] or datetime.max.replace(tzinfo=tz),
        ))
    return _apply_class_order(out, user_id)


def _collect_overdue(user_id: int, tz: ZoneInfo = LOCAL_TZ) -> dict:
    """All non-completed tasks/events with due dates in the past, scoped to
    the given user. Personal tasks (no class) bucket under PERSONAL_BUCKET.
    `tz` anchors "now" / 30-day window in the user's local time."""
    now = datetime.now(tz)
    out: dict[int, dict] = {}

    def _emit_overdue_task(cls, t):
        if t.completed_at or t.due_at is None:
            return
        # Recurring tasks emit one row per occurrence via _emit_task in
        # the per-day collector. Showing the anchor as "overdue" here
        # would (a) double-up with today's expanded row when the dedupe
        # in _merge_today_with_overdue picks one (overdue wins, hiding
        # the actual today occurrence), and (b) ignore exdates/until,
        # so excluded or capped occurrences would still show as overdue.
        # Skipping recurring entirely keeps "overdue" meaning what users
        # expect: a non-recurring missed deadline.
        if t.rrule:
            return
        local_due = _to_local(t.due_at, tz)
        if not (local_due < now and local_due >= now - timedelta(days=30)):
            return
        out.setdefault(cls.id, {"cls": cls, "items": []})["items"].append({
            "kind": "task", "sub_kind": None, "sub_kind_color": None,
            "sub_kind_id": None,
            "id": t.id,
            "class_id": cls.id, "title": t.title,
            "due_at": local_due, "completed": False,
            "starts_at": _to_local(t.starts_at, tz) if t.starts_at else None,
            "is_range": t.starts_at is not None,
            "is_range_day": False,
            "actionable": True,
            "position": t.position or 0,
            "tag_color": t.tag.color if t.tag else None,
            "tag_name": t.tag.name if t.tag else None,
            "tag_id": t.tag.id if t.tag else None,
            "tag_is_system": t.tag.is_system if t.tag else False,
            "notes": t.notes,
            "rrule": t.rrule,
            "is_all_day": t.is_all_day,
        })

    with Session(engine, expire_on_commit=False) as session:
        sys_tag_by_key = {
            t.system_key: t
            for t in session.exec(
                select(Tag).where(Tag.is_system == True, Tag.user_id == user_id)
            ).all()
            if t.system_key
        }
        for cls in session.exec(select(Class).where(Class.user_id == user_id)).all():
            for t in cls.tasks:
                _emit_overdue_task(cls, t)
            for ev in cls.events:
                if ev.completed_at:
                    continue
                if ev.starts_at is None:
                    continue
                # Non-actionable events (past lectures, holidays) aren't
                # "overdue" — nothing to chase. Skip.
                if not ev.actionable:
                    continue
                local_when = _to_local(ev.starts_at, tz)
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
                        "notes": None,
                        "rrule": None,
                        "is_all_day": False,
                    })
        # Personal tasks (no class) — bucket under PERSONAL_BUCKET.
        for t in session.exec(
            select(Task).where(Task.user_id == user_id, Task.class_id == None)
        ).all():
            _emit_overdue_task(PERSONAL_BUCKET, t)
    for slot in out.values():
        slot["items"].sort(key=lambda it: (it["position"], it["due_at"] or datetime.max.replace(tzinfo=tz)))
    return _apply_class_order(out, user_id)


@app.get("/today", response_class=HTMLResponse)
def today_view(request: Request, user: User = Depends(require_login)):
    """Tasks and events due today, plus anything overdue."""
    tz = _user_tz(user)
    today_start = _today_local(tz)
    today_end = today_start + timedelta(days=1)
    today_items = _collect_items_in_range(today_start, today_end, user.id,
                                          tz=tz, hide_completed=True)
    overdue = _collect_overdue(user.id, tz=tz)
    today_buckets = _merge_today_with_overdue(today_items, overdue, user.id)
    with Session(engine, expire_on_commit=False) as session:
        all_classes = session.exec(
            select(Class).where(Class.user_id == user.id).order_by(Class.code)
        ).all()
        all_tags = session.exec(
            select(Tag).where(Tag.user_id == user.id).order_by(Tag.name)
        ).all()
    return templates.TemplateResponse(request, "today.html", {
        "today": today_start,
        "today_buckets": today_buckets,
        "all_classes": all_classes,
        "all_tags": all_tags,
        "default_class_id": (all_classes[0].id if all_classes else None),
    })


@app.get("/week", response_class=HTMLResponse)
def week_view(request: Request, user: User = Depends(require_login), month: Optional[str] = None):
    """Month-grid view (Mon-Sun, 6 weeks) for the requested YYYY-MM.
    Defaults to the current month."""
    tz = _user_tz(user)
    today_start = _today_local(tz)
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
    first_of_month = datetime(target_year, target_month, 1, tzinfo=tz)
    # Grid starts on the Monday on-or-before the 1st.
    grid_start = first_of_month - timedelta(days=first_of_month.weekday())
    days = []
    for i in range(42):  # 6 weeks × 7 days
        day_start = grid_start + timedelta(days=i)
        day_end = day_start + timedelta(days=1)
        items_by_class = _collect_items_in_range(
            day_start, day_end, user.id,
            day_for_overrides=day_start.strftime("%Y-%m-%d"),
            tz=tz,
        )
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
        all_classes = session.exec(
            select(Class).where(Class.user_id == user.id).order_by(Class.code)
        ).all()
        all_tags = session.exec(
            select(Tag).where(Tag.user_id == user.id).order_by(Tag.name)
        ).all()
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

@app.post("/classes/{class_id}/docs")
async def upload_doc(
    class_id: int,
    request: Request,
    file: UploadFile = File(...),
    title: str = Form(""),
    user: User = Depends(require_login),
):
    content = await file.read()
    validate_upload(content)
    safe_name = safe_filename(file.filename or "doc")
    filename = f"{uuid.uuid4().hex[:8]}_{safe_name}"
    storage.save(filename, content, content_type=file.content_type or "application/octet-stream")

    with Session(engine) as session:
        _own_class(session, class_id, user.id)
        doc = Document(
            class_id=class_id,
            title=(title.strip() or safe_name),
            filename=filename,
            uploaded_at=datetime.now(timezone.utc),
        )
        session.add(doc)
        session.commit()
        session.refresh(doc)
        if "application/json" in request.headers.get("accept", ""):
            return JSONResponse({
                "id": doc.id,
                "title": doc.title,
                "filename": doc.filename,
                "uploaded_at": doc.uploaded_at.isoformat(),
            })
    return RedirectResponse(f"/classes/{class_id}", status_code=303)


@app.post("/docs/{doc_id}/delete")
def delete_doc(doc_id: int, request: Request, user: User = Depends(require_login)):
    with Session(engine) as session:
        d = _own_document(session, doc_id, user.id)
        cls_id = d.class_id
        storage.delete(d.filename)
        session.delete(d)
        session.commit()
    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse({"deleted": doc_id})
    return RedirectResponse(f"/classes/{cls_id}", status_code=303)


@app.get("/uploads/{filename}")
def serve_upload(filename: str, user: User = Depends(require_login)):
    """Serve an uploaded file (syllabus PDF or attached doc) only if it
    belongs to a class owned by the requesting user. Filename uniqueness
    is enforced at upload time so we can match by filename alone."""
    safe = safe_filename(filename)
    if not storage.exists(safe):
        raise HTTPException(404)
    with Session(engine) as session:
        # File could be a Document, Syllabus, or TaskAttachment upload.
        owned = False
        content_type: Optional[str] = None
        doc = session.exec(select(Document).where(Document.filename == safe)).first()
        if doc:
            cls = session.get(Class, doc.class_id)
            owned = bool(cls and cls.user_id == user.id)
        if not owned:
            syl = session.exec(select(Syllabus).where(Syllabus.filename == safe)).first()
            if syl:
                cls = session.get(Class, syl.class_id)
                owned = bool(cls and cls.user_id == user.id)
        if not owned:
            att = session.exec(select(TaskAttachment).where(TaskAttachment.filename == safe)).first()
            if att:
                t = session.get(Task, att.task_id)
                owned = bool(t and t.user_id == user.id)
                content_type = att.content_type or None
        if not owned:
            raise HTTPException(404)
    if content_type is None:
        content_type = "application/pdf" if safe.lower().endswith(".pdf") else None
    return storage.serve(safe, content_type=content_type)


# ---- Routes: iCal feed ----
# Two routes serve the same content with different auth:
#   GET /calendar.ics                — requires session cookie (browser/tab use)
#   GET /calendar/{token}.ics        — public-but-unguessable; for Apple
#                                      Calendar / Google Calendar / etc. that
#                                      can't carry a cookie across long-lived
#                                      subscriptions. Token lives on the
#                                      User row and can be rotated from /settings.

def _build_ical_for_user(user_id: int, request: Optional[Request] = None) -> bytes:
    """Generate a full iCal feed (events + dated tasks) for one user.
    Used by both the cookie-auth and token-auth routes."""
    from icalendar import Alarm

    cal = Calendar()
    cal.add("prodid", "-//Compass//EN")
    cal.add("version", "2.0")
    cal.add("x-wr-calname", "Compass")
    # Apple Calendar caches subscribed feeds aggressively — drop the poll
    # hint to 15 minutes so deletes/edits land faster. icalendar has no
    # default serializer for RFC 7986's REFRESH-INTERVAL, so x-published-ttl
    # is the only hint we emit (newer clients also honor it).
    cal.add("x-published-ttl", "PT15M")

    def _attach_reminder(component, minutes_before: int) -> None:
        alarm = Alarm()
        alarm.add("action", "DISPLAY")
        alarm.add("description", "Compass reminder")
        alarm.add("trigger", timedelta(minutes=-minutes_before))
        component.add_component(alarm)

    def _apply_color(component, hex_color: Optional[str]) -> None:
        css = _hex_to_css3_color(hex_color)
        if css:
            component.add("color", css)
        if hex_color:
            # Apple-specific extension; some clients honor it when COLOR is
            # absent/ignored. Cheap to send alongside.
            component.add("x-apple-calendar-color", hex_color)

    with Session(engine) as session:
        # Single user lookup powers both the per-user timezone (calendar
        # property + naive-datetime fallbacks) and the token-authenticated
        # ATTACH URIs Apple Calendar fetches. Body still emits UTC for
        # timed events, so wall-clock times stay correct regardless of tz.
        feed_user = session.get(User, user_id)
        user_zone = _user_tz(feed_user)
        cal.add("x-wr-timezone", str(user_zone))
        token = feed_user.calendar_token if feed_user else ""
        base_url = str(request.base_url).rstrip("/") if request is not None else ""

        owned_class_ids = [
            c.id for c in session.exec(
                select(Class).where(Class.user_id == user_id)
            ).all()
        ]
        if not owned_class_ids:
            return cal.to_ical()

        sys_tag_by_key = {
            t.system_key: t
            for t in session.exec(
                select(Tag).where(Tag.is_system == True, Tag.user_id == user_id)
            ).all()
            if t.system_key
        }

        # Auto-extracted events from syllabi.
        events = session.exec(
            select(CalendarEvent).where(
                CalendarEvent.class_id.in_(owned_class_ids),
                CalendarEvent.starts_at != None,
            )
        ).all()
        for ev in events:
            ie = ICalEvent()
            ie.add("uid", f"compass-event-{ev.id}@compass")
            ie.add("summary", f"[{ev.class_code}] {ev.title}")
            ie.add("dtstart", _to_local(ev.starts_at, user_zone))
            if ev.ends_at:
                ie.add("dtend", _to_local(ev.ends_at, user_zone))
            ie.add("dtstamp", datetime.now(timezone.utc))
            ie.add("description", f"{ev.kind.title()} for {ev.class_code}")
            sys_tag = sys_tag_by_key.get(ev.kind)
            if sys_tag:
                _apply_color(ie, sys_tag.color)
            # Only remind for things the user actually has to act on. A
            # lecture topic at 9am doesn't need a 8:45am ping.
            if ev.actionable and not ev.completed_at:
                _attach_reminder(ie, 15)
            cal.add_component(ie)

        # Manual tasks (only those with a due date — undated backlog tasks
        # can't appear on a calendar, and completed tasks aren't worth
        # cluttering the feed with).
        tasks = session.exec(
            select(Task).where(
                Task.user_id == user_id,
                Task.due_at != None,
                Task.completed_at == None,
            )
        ).all()
        # Build a class_id -> code map for the task summary prefix.
        class_codes = {
            c.id: c.code for c in session.exec(
                select(Class).where(Class.id.in_(owned_class_ids))
            ).all()
        }
        for t in tasks:
            ie = ICalEvent()
            ie.add("uid", f"compass-task-{t.id}@compass")
            code = class_codes.get(t.class_id) if t.class_id is not None else None
            prefix = f"[{code}] " if code else "[Personal] "
            ie.add("summary", f"{prefix}{t.title}")
            due = _to_local(t.due_at, user_zone)
            if t.is_all_day:
                # All-day events use VALUE=DATE in iCal — Apple Calendar
                # renders them as a banner across the day instead of a
                # timed slot. Pass `datetime.date` so icalendar serializes
                # it as DTSTART;VALUE=DATE:YYYYMMDD.
                ie.add("dtstart", due.date())
            elif t.rrule:
                # Recurring task: match the web app's `_emit_task` rule —
                # rrule trumps range (a task repeats OR spans days, not
                # both). Emit a single-instant DTSTART at due_at so Apple
                # expands the recurrence on the same anchor; without this
                # we'd emit DTSTART=starts_at + DTEND=due_at + RRULE,
                # which Apple turns into a multi-day banner repeating
                # daily — every occurrence is the FULL span, all stacked
                # on top of each other (looks like a flood of dupes).
                ie.add("dtstart", due)
            elif t.starts_at is not None:
                # Non-recurring range task — render as a multi-day event.
                ie.add("dtstart", _to_local(t.starts_at, user_zone))
                ie.add("dtend", due)
            else:
                ie.add("dtstart", due)
            ie.add("dtstamp", datetime.now(timezone.utc))
            # User notes flow through to Apple Calendar's event description.
            # Falls back to a marker so the field isn't blank.
            ie.add("description", t.notes or "Compass task")
            _apply_color(ie, t.tag.color if t.tag else None)
            # Recurrence: pass through the stored RRULE fragment if set,
            # tacking on UNTIL when the user set a stop date. Apple
            # Calendar uses both to know when to stop showing instances.
            if t.rrule:
                from icalendar import vRecur
                rrule_str = t.rrule
                if t.rrule_until:
                    until_utc = _to_local(t.rrule_until, user_zone).astimezone(timezone.utc)
                    rrule_str = f"{rrule_str};UNTIL={until_utc.strftime('%Y%m%dT%H%M%SZ')}"
                try:
                    ie.add("rrule", vRecur.from_ical(rrule_str))
                except Exception:
                    pass  # malformed stored RRULE — silently drop, don't break the feed
                # EXDATE per excluded occurrence — Apple Calendar honors
                # these to suppress individual instances.
                for ex in _parse_exdates(t.rrule_exdates):
                    ie.add("exdate", _to_local(ex, user_zone))
            # Alerts: emit one VALARM per stored offset. Empty list = no
            # reminders (user explicitly opted out). Falls back to a 15-min
            # default for legacy tasks that pre-date the alerts table.
            alert_offsets = [a.minutes_before for a in t.alerts]
            if alert_offsets:
                for m in alert_offsets:
                    _attach_reminder(ie, m)
            else:
                # Legacy task with no alerts row at all → preserve the old
                # 15-min default so existing subscriptions don't lose alarms.
                # New tasks always get explicit alert rows (smart defaults).
                _attach_reminder(ie, 15)
            # Attachments: emit ATTACH;FMTTYPE=...;VALUE=URI for each.
            # Apple Calendar shows these as paperclip items the user can
            # tap to download. URLs use the calendar_token so subscribers
            # don't need a session cookie.
            if t.attachments and base_url and token:
                for att in t.attachments:
                    uri = f"{base_url}/calendar/{token}/attachments/{att.filename}"
                    ie.add(
                        "attach",
                        uri,
                        parameters={"FMTTYPE": att.content_type or "application/octet-stream"},
                    )
            cal.add_component(ie)

    return cal.to_ical()


# Tell every cache layer (iOS Calendar, browsers, CDNs) not to serve a stale
# copy. Without this, Apple Calendar can show events from a fetch that
# happened an hour ago even when the user pulls-to-refresh.
_ICAL_NO_CACHE_HEADERS = {
    "Cache-Control": "no-cache, no-store, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}


@app.get("/calendar.ics")
def ical_feed(request: Request, user: User = Depends(require_login)):
    return Response(
        content=_build_ical_for_user(user.id, request),
        media_type="text/calendar",
        headers=_ICAL_NO_CACHE_HEADERS,
    )


@app.get("/calendar/{token}.ics")
def ical_feed_by_token(token: str, request: Request):
    """Token-authenticated feed for Apple Calendar etc. No login required —
    the token itself is the secret. 404 (not 401) on invalid token so we
    don't leak which tokens exist."""
    with Session(engine) as session:
        user = session.exec(select(User).where(User.calendar_token == token)).first()
        if not user:
            raise HTTPException(404)
        return Response(
            content=_build_ical_for_user(user.id, request),
            media_type="text/calendar",
            headers=_ICAL_NO_CACHE_HEADERS,
        )
