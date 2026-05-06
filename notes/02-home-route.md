# Page Load: The `home()` route

What happens on the web server side when Brave sends `GET /`. Picks up
where `01b-tray-icon-and-event-loop.md` ends.

This file: walks through the function that runs when Brave asks for the
home page. Code lives in `main.py` starting at line 786.

## The door — `@app.get("/")`

```python
@app.get("/", response_class=HTMLResponse)
def home(request: Request):
```

`@app.get("/")` is the **route registration**. Like Spring's `@GetMapping("/")`.
Tells FastAPI: "when anyone sends a `GET` to `/`, run the function below."

- `@app.get(...)` is a *decorator* — it wraps `home()` and registers it in
  FastAPI's internal route table. You never call `home()` yourself; FastAPI
  calls it when a matching request arrives.
- `/` is the path — matches `http://localhost:8000/`.
- `response_class=HTMLResponse` sets the default content type to `text/html`.
- `request: Request` is filled in by FastAPI — like Java's
  `HttpServletRequest req` parameter.

## Step 1 — Auth check

```python
if COMPASS_TOKEN and not has_valid_cookie(request):
    return RedirectResponse("/setup-token", status_code=303)
```

"If a token is configured and the browser doesn't have one, redirect to
`/setup-token`."

In dev mode `COMPASS_TOKEN` is empty string — falsy in Python — so this
block is **skipped**.

## Step 2 — Compute today's date range

```python
today_start = _today_local()
today_end = today_start + timedelta(days=1)
```

`_today_local()` (line 1125) returns today at 00:00:00 in your timezone
(`America/New_York`).

`today_end` is midnight tomorrow. The window `[today_start, today_end)` =
"all of today."

Java equivalent:

```java
LocalDateTime todayStart = LocalDate.now(ZoneId.of("America/New_York")).atStartOfDay();
LocalDateTime todayEnd = todayStart.plusDays(1);
```

## Step 3 — Query the database (twice)

```python
today_items = _collect_items_in_range(today_start, today_end)
overdue = _collect_overdue()
```

Two helpers that run database queries:

| Variable      | What it returns                                                       |
|---------------|------------------------------------------------------------------------|
| `today_items` | Dict keyed by class — every task/event due today, grouped by class.   |
| `overdue`     | Dict keyed by class — uncompleted tasks/events past due (last 30 days).|

Shape of each value:

```python
{
    1: {"cls": <Class CS101>, "items": [{"kind": "task", "id": 5, ...}]},
    2: {"cls": <Class MATH250>, "items": [...]},
}
```

## Step 4 — Query the class list

```python
with Session(engine, expire_on_commit=False) as session:
    classes = session.exec(select(Class).order_by(Class.code)).all()
```

`with ... as ...` is Python's try-with-resources — the session auto-closes
when the block exits.

`session.exec(select(Class).order_by(Class.code)).all()` is SQLModel's way
of writing:

```sql
SELECT * FROM class ORDER BY code;
```

Returns every class, alphabetical by code.

## Step 5 — Hand it all to the template

```python
return templates.TemplateResponse(request, "home.html", {
    "classes": classes,
    "today": today_start,
    "today_items": today_items,
    "overdue": overdue,
    "default_class_id": (classes[0].id if classes else None),
})
```

Tells FastAPI: "render `home.html` using this data, send the result back as
the HTTP response."

The dict is the **template context** — every key in it becomes a variable
inside the template.

That last line is a Python ternary:

```python
classes[0].id if classes else None
```

Java equivalent:

```java
classes.isEmpty() ? null : classes.get(0).id
```

`templates.TemplateResponse(...)` does three things:

1. Loads `templates/home.html` from disk.
2. Runs Jinja to fill in all the `{% for %}` and `{{ }}` holes using the
   context dict.
3. Wraps the resulting HTML string in `HTTP 200 OK` with
   `Content-Type: text/html`.

## Flow

```
home(request) called
        │
        ▼
auth check (skipped — dev mode)
        │
        ▼
compute today's date range
        │
        ▼
query DB → today_items   (today's tasks + events, grouped by class)
        │
        ▼
query DB → overdue       (uncompleted past-due tasks)
        │
        ▼
query DB → classes       (sorted class list)
        │
        ▼
hand all of that + "home.html" to TemplateResponse
        │
        ▼
return HTTP 200 with the rendered HTML
```

## Side note: how Brave even knew to make this request

Browsers send `GET /` automatically when you load a URL. That is just how
the web works — no code on your side asked Brave to do it.
