from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional, List

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlmodel import SQLModel, Field, Relationship, Session, create_engine, select


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
    kind: str
    content: str
    cls: Optional[Class] = Relationship(back_populates="policies")


class CalendarEvent(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    class_id: int = Field(foreign_key="class.id")
    class_code: str
    title: str
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    kind: str
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

engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})


@asynccontextmanager
async def lifespan(app: FastAPI):
    SQLModel.metadata.create_all(engine)
    yield


app = FastAPI(title="StudyFlow", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


# ---- Routes ----

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    with Session(engine) as session:
        classes = session.exec(select(Class).order_by(Class.code)).all()
    return templates.TemplateResponse("home.html", {"request": request, "classes": classes})


@app.post("/classes")
def add_class(name: str = Form(...), code: str = Form(...)):
    with Session(engine) as session:
        cls = Class(name=name.strip(), code=code.strip())
        session.add(cls)
        session.commit()
    return RedirectResponse(url="/", status_code=303)


@app.get("/classes.json")
def classes_json():
    with Session(engine) as session:
        classes = session.exec(select(Class).order_by(Class.code)).all()
        return JSONResponse([{"id": c.id, "code": c.code, "name": c.name} for c in classes])


@app.get("/classes/{class_id}", response_class=HTMLResponse)
def class_detail(request: Request, class_id: int):
    with Session(engine) as session:
        cls = session.get(Class, class_id)
        if not cls:
            raise HTTPException(404, "Class not found")
        # Force-load relationships before session closes
        policies = list(cls.policies)
        events = sorted(cls.events, key=lambda e: e.starts_at or datetime.max)
        documents = list(cls.documents)
    return templates.TemplateResponse(
        "class.html",
        {"request": request, "cls": cls, "policies": policies, "events": events, "documents": documents},
    )


@app.post("/classes/{class_id}/delete")
def delete_class(class_id: int):
    with Session(engine) as session:
        cls = session.get(Class, class_id)
        if not cls:
            raise HTTPException(404, "Class not found")
        session.delete(cls)
        session.commit()
    return RedirectResponse(url="/", status_code=303)
