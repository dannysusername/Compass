# The helpers `home()` calls

Picks up where `02-home-route.md` ends. That note covered the `home()`
function itself; this one drills into the three helpers it calls before
handing data to the template:

| Helper                       | Where in `main.py` |
|------------------------------|--------------------|
| `_today_local()`             | line 1125          |
| `_collect_items_in_range()`  | line 1269          |
| `_collect_overdue()`         | line 1320          |

A fourth helper, `_to_local()` (line 1131), is used internally by the
other two — covered here too.

## `_today_local()` — "midnight today, in my timezone"

```python
def _today_local() -> datetime:
    """Today's date at midnight in the user's local timezone."""
    now = datetime.now(LOCAL_TZ)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)
```

`LOCAL_TZ` is set at the top of `main.py` (line 35):

```python
LOCAL_TZ = ZoneInfo("America/New_York")
```

`datetime.now(LOCAL_TZ)` returns the current moment **with timezone
attached** — important, because Python distinguishes "naive" datetimes
(no timezone) from "aware" ones (timezone known). Mixing them throws.

`.replace(hour=0, minute=0, ...)` returns a *new* datetime with those
fields zeroed. So if it's `2026-05-05 14:32:11 EDT`, the function returns
`2026-05-05 00:00:00 EDT`.

## `_to_local()` — "make sure this datetime has my timezone"

```python
def _to_local(dt: Optional[datetime]) -> Optional[datetime]:
    """Attach LOCAL_TZ if naive, else convert to LOCAL_TZ. None passes through."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=LOCAL_TZ)
    return dt.astimezone(LOCAL_TZ)
```

Called for every `due_at` / `starts_at` pulled from the database.
SQLite stores datetimes without timezone info, so we get back naive
datetimes. This helper makes them comparable to `today_start`/`today_end`
(which are aware).

Three branches:

1. `dt is None` → just return `None` (some tasks have no due date).
2. `dt.tzinfo is None` → naive datetime from SQLite — stamp it with
   `LOCAL_TZ` (we *assume* SQLite's value was already in local time).
3. Otherwise → convert to `LOCAL_TZ` (handles the rare case it came in
   as UTC or another zone).


## `_collect_items_in_range(start, end)` — "what's happening in this window"

The bigger one. Returns every task and event whose due/start time falls
in the half-open window `[start, end)`, grouped by class.

### Signature + return shape

```python
def _collect_items_in_range(start: datetime, end: datetime) -> dict:
```

Returns:

```python
{
    1: {"cls": <Class CS101>,  "items": [{"kind": "task", "id": 5, ...}, ...]},
    2: {"cls": <Class MATH250>,"items": [...]},
}
```

Keyed by `class.id`. Each value has the `Class` object plus a flat list
of items belonging to that class.

### The `_add` inner function

```python
def _add(cls, kind, item_id, title, when, completed, position=0, sub_kind=None):
    slot = out.setdefault(cls.id, {"cls": cls, "items": []})
    slot["items"].append({...})
```

A **closure** — defined inside `_collect_items_in_range`, captures the
`out` dict from the enclosing scope. Java would force you to write a
private method or a lambda with explicit captures; Python lets you nest
freely.

`out.setdefault(key, default)` — returns `out[key]` if it exists,
otherwise inserts `default` first and returns it. 

So the first time we see a class we create its slot; subsequent items
append to the existing slot's `items` list.

### Walking tasks

```python
with Session(engine, expire_on_commit=False) as session:
    for cls in session.exec(select(Class)).all():
        for t in cls.tasks:
            if t.due_at is None:
                if start <= _today_local() < end and not t.completed_at:
                    _add(cls, "task", t.id, t.title, None, False, t.position or 0)
                continue
            local_due = _to_local(t.due_at)
            if start <= local_due < end:
                _add(cls, "task", t.id, t.title, local_due,
                     t.completed_at is not None, t.position or 0)
```

`cls.tasks` is a SQLModel relationship — translates to `SELECT * FROM
task WHERE class_id = ?` lazily. `expire_on_commit=False` keeps the
loaded objects usable after the session exits (otherwise accessing
`.tasks` later would hit a closed-session error).

Two cases per task:

- **No due date.** Show it on the today view as an "open backlog" item,
  but only if the window includes today and the task isn't completed.
  Anywhere else (week view, future ranges) it's invisible.
- **Has a due date.** Convert to local time, then check it falls in
  `[start, end)`. The half-open interval is deliberate — midnight
  belongs to the *new* day, not the old one.

`t.position or 0` — Python's `or` returns the first truthy operand. If
`t.position` is `None` or `0`, you get `0`. Equivalent to Java's
`t.position != null ? t.position : 0` for nullable ints.

### Walking events

```python
for ev in cls.events:
    if ev.starts_at is None:
        continue
    local_when = _to_local(ev.starts_at)
    if start <= local_when < end:
        _add(cls, "event", ev.id, ev.title, local_when,
             ev.completed_at is not None, 0, sub_kind=ev.kind)
```

Same pattern as tasks but simpler — events without a `starts_at` are
skipped entirely (no "backlog" semantics). `sub_kind=ev.kind` lets the
template distinguish quiz/exam/etc. events.

### The sort

```python
for slot in out.values():
    slot["items"].sort(key=lambda it: (
        it["position"],
        it["due_at"] is None,
        it["due_at"] or datetime.max.replace(tzinfo=LOCAL_TZ),
    ))
```

Items are sorted by a **tuple key** — Python compares tuples
element-by-element, like a multi-column SQL `ORDER BY`:

1. `it["position"]` — user's drag-priority order (lower first).
2. `it["due_at"] is None` — `False` (`0`) sorts before `True` (`1`),
   so dated items come before undated ones.
3. The due time itself — `None` is replaced with `datetime.max` to
   avoid a `TypeError` when comparing.

Java equivalent (using `Comparator.comparing().thenComparing()` chains):

```java
slot.items.sort(
    Comparator.<Item, Integer>comparing(it -> it.position)
        .thenComparing(it -> it.dueAt == null)
        .thenComparing(it -> it.dueAt != null ? it.dueAt : DateTime.MAX)
);
```

## `_collect_overdue()` — "what did I miss"

```python
def _collect_overdue() -> dict:
    now = datetime.now(LOCAL_TZ)
    out: dict[int, dict] = {}
    with Session(engine, expire_on_commit=False) as session:
        for cls in session.exec(select(Class)).all():
            for t in cls.tasks:
                if t.completed_at: continue
                if t.due_at is None: continue
                local_due = _to_local(t.due_at)
                if local_due < now and local_due >= now - timedelta(days=30):
                    out.setdefault(cls.id, {"cls": cls, "items": []})["items"].append({...})
            for ev in cls.events:
                ...
```

Same shape as `_collect_items_in_range`, but the filter is different:

- Skip completed items (`t.completed_at` truthy → done).
- Skip items with no due date (can't be overdue if undated).
- Keep only items where `now - 30 days <= local_due < now`.

The 30-day cap stops the overdue list from growing forever — anything
older than a month drops off and won't clutter the today page.

Note this helper builds the dicts inline (no `_add` closure) — the
output rows have slightly different defaults (`position: 0` for events,
`completed: False` always since completed ones are skipped above).

Final sort is the same tuple trick, just two-level instead of three:

```python
slot["items"].sort(key=lambda it: (it["position"], it["due_at"] or datetime.max...))
```

## How the helpers feed `home()`

```
home() called
   │
   ├──▶ _today_local()               → today_start  (midnight, local)
   │      then today_end = +1 day    → today_end
   │
   ├──▶ _collect_items_in_range(today_start, today_end)
   │       │
   │       ├──▶ for each class:
   │       │     for each task: filter by due_at in window
   │       │     for each event: filter by starts_at in window
   │       │     (each match → _add() → out[class_id].items)
   │       │
   │       └──▶ sort each class's items by (position, dated?, due_at)
   │
   ├──▶ _collect_overdue()
   │       │
   │       └──▶ for each class:
   │             tasks/events with due_at in [now-30d, now), not completed
   │
   └──▶ session.exec(select(Class).order_by(Class.code)).all()
           classes list
   │
   ▼
templates.TemplateResponse("home.html", {classes, today, today_items, overdue, ...})
```

## Why `expire_on_commit=False` shows up everywhere

SQLAlchemy's default behavior: when a session commits or closes, every
loaded object becomes "expired" — accessing any attribute on it would
trigger a fresh DB query, which fails because the session is gone.

`expire_on_commit=False` keeps the objects usable after the `with`
block. We want this because we hand `Class` objects (with their `.tasks`
already loaded into `out["cls"]`) up to the template — and Jinja will
read attributes off them later, after the session has closed.

You'll see the same flag on `home()`'s class query and on both
collectors. It's a small but load-bearing detail.
