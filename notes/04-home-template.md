# Rendering `home.html`

Picks up where `03-home-helpers.md` ends. `home()` handed Jinja a context
dict — now Jinja takes over and produces the HTML string that goes back
to Brave.

Three files are involved:

| File                          | Role                                                   |
|-------------------------------|--------------------------------------------------------|
| `templates/base.html`         | The page shell (header, nav, theme toggle).            |
| `templates/home.html`         | The home page body — courses list + the today panel.   |
| `templates/_today_list.html`  | Reusable partial showing today's tasks and overdue.    |

`home.html` *extends* `base.html` and *includes* `_today_list.html`.
That gives you a layered render: shell → page → partial.

## How template rendering actually starts

Recall the last line of `home()`:

```python
return templates.TemplateResponse(request, "home.html", {...})
```

Jinja loads `home.html`, sees `{% extends "base.html" %}` on line 1,
loads that too, then "fills in" the blocks defined in `base.html` with
content from `home.html`. The result is a single HTML string.

## `base.html` — the page shell

```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="theme-color" content="#F4F1E8">
    <title>{% block title %}Compass{% endblock %}</title>
    <link rel="stylesheet" href="{{ '/static/styles.css' | static_v }}">
```

`{% block title %}Compass{% endblock %}` is a **named hole** the child
template can override. The text between the tags is the default (used if
nothing fills the block). `home.html` overrides it with `{% block title
%}Compass{% endblock %}` (same value here, but it could differ).

`{{ '/static/styles.css' | static_v }}` is two things:

- `{{ ... }}` — print expression. Anything between double-braces is
  evaluated and the result is HTML-escaped into the output.
- `| static_v` — a Jinja filter. Filters are pipes: input goes in the
  left, output comes out the right. `static_v` is a custom filter
  registered in `main.py` that appends a cache-busting version string
  (e.g., `/static/styles.css?v=1730918400`).

```html
    <script>
      (function () {
        try {
          var t = localStorage.getItem("compass-theme");
          if (t === "dark") document.documentElement.classList.add("dark");
        } catch (e) {}
      })();
    </script>
```

A tiny inline script that runs **before** any visible markup paints. It
reads the saved theme from `localStorage` and sets `class="dark"` on
`<html>` if the user picked dark mode last time. Inline + synchronous +
in `<head>` = no flash of light mode while CSS loads.

```html
<body>
    <header>
        <a href="/" class="logo"><em>Study</em>flow<span class="dot">.</span></a>
        <nav class="top-nav">
            <a href="/">Classes</a>
            <a href="/today">Today</a>
            <a href="/week">Week</a>
        </nav>
        <button type="button" class="theme-toggle" data-theme-toggle aria-label="Toggle dark mode">Dark</button>
    </header>
    <main>
        {% block content %}{% endblock %}
    </main>
    <script src="{{ '/static/upload.js' | static_v }}" defer></script>
</body>
</html>
```

The header is fixed across every page — logo, nav links, theme button.
`{% block content %}{% endblock %}` is the second hole — this one's
empty by default and the child template fills it. That's where
`home.html` injects the page body.

`upload.js` loads on every page (`<script defer>`), so PDF drag-drop
works wherever an upload form exists.

## `home.html` — the page body

```html
{% extends "base.html" %}
{% block title %}Compass{% endblock %}
{% block content %}
```

Line 1 declares the parent. Lines 2–3 open the two blocks `base.html`
defined. Everything until `{% endblock %}` at the bottom replaces the
empty `{% block content %}` in the parent.

### The two-column layout

```html
<div class="home-layout">
<main class="home-main-col">
    ...
</main>

<aside class="home-todo-col">
    {% with all_classes = classes %}
    {% include "_today_list.html" %}
    {% endwith %}
</aside>
</div>
```

A flex container with two children:

- `home-main-col` — courses + manual-add form + calendar footer.
- `home-todo-col` — today/overdue list (delegated to the partial).

`{% with all_classes = classes %}` creates a local variable for the
duration of the block. The partial expects a variable called
`all_classes` (so it works on both home and class pages, where the
incoming list might be named differently). On the home page we just
alias `classes` → `all_classes`. The variable disappears at `{% endwith
%}`.

`{% include "_today_list.html" %}` slots the partial's HTML in here. The
partial inherits the *current* template context — it can read `classes`,
`today`, `today_items`, `overdue`, `default_class_id`, *and* `all_classes`
because we just defined that.

### Courses list

```html
<section>
    <h2>
        Your courses
        <button type="button" class="add-syllabus-btn" data-open-modal="syllabus-modal" aria-label="Add a syllabus">+</button>
    </h2>
    {% if classes %}
    <ul class="class-list">
        {% for c in classes %}
        <li>
            <a href="/classes/{{ c.id }}" class="class-card">
                <span class="code">{{ c.code }}</span>
                <span class="name">{{ c.name }}</span>
            </a>
        </li>
        {% endfor %}
    </ul>
    {% else %}
    <p class="empty">No courses yet. Click <strong>+</strong> to upload your first syllabus.</p>
    {% endif %}
</section>
```

Three Jinja constructs:

- `{% if classes %}` — empty list / `None` is falsy. So we render the
  list when there's at least one class, otherwise the "no courses" hint.
- `{% for c in classes %}` ... `{% endfor %}` — straight loop. `c` is
  the loop variable (one `Class` row at a time).
- `{{ c.id }}`, `{{ c.code }}`, `{{ c.name }}` — print attributes off
  the SQLModel object. Because `home()` used
  `expire_on_commit=False`, these reads don't try to hit a closed
  session.

`data-open-modal="syllabus-modal"` is a hook for `modal.js` — clicking
the button finds `<div id="syllabus-modal">` and unhides it.

### Manual-add fallback

```html
<details class="manual-add">
    <summary>No syllabus yet? Add a class manually</summary>
    <form method="post" action="/classes" class="add-class">
        <label>
            Code
            <input type="text" name="code" placeholder="MATH 250" required>
        </label>
        <label>
            Name
            <input type="text" name="name" placeholder="Calculus II" required>
        </label>
        <button type="submit">Add class</button>
    </form>
</details>
```

`<details>` / `<summary>` is plain HTML — a built-in disclosure widget
that toggles open on click, no JS needed. The form posts to
`POST /classes` (the `add_class` route at `main.py:805`), which inserts
a row and redirects back to `/`.

### Calendar feed footer

```html
<footer class="cal-link">
    <p>Calendar feed: <code>/calendar.ics</code> &mdash; subscribe in Apple Calendar.</p>
</footer>
```

Just a static hint. The actual ICS feed is a separate route.

### Syllabus modal

```html
<div class="modal-overlay" id="syllabus-modal" hidden>
    <div class="modal-dialog" role="dialog" aria-labelledby="syllabus-modal-title">
        ...
        <form method="post" action="/syllabus" enctype="multipart/form-data">
            <div class="drop-zone" data-upload-zone>
                <input type="file" name="file" accept="application/pdf,.pdf" required>
                ...
            </div>
            <div class="actions">
                <button type="submit">Upload syllabus</button>
            </div>
        </form>
    </div>
</div>
```

The modal sits in the DOM at all times, hidden by the `hidden` attribute.
`modal.js` toggles it via the `data-open-modal` and `data-close-modal`
hooks. `enctype="multipart/form-data"` is required for file uploads —
without it the file body wouldn't actually be sent.

### Script tags at the bottom

```html
<script src="{{ '/static/todo.js' | static_v }}" defer></script>
<script src="{{ '/static/modal.js' | static_v }}" defer></script>
{% endblock %}
```

`defer` means: download in parallel, run *after* the document parses.
That's why these tags can sit inside `{% block content %}` and still run
in the right order. `{% endblock %}` closes the `content` block — Jinja
splices everything between `{% block content %}` and here into the
parent's empty `{% block content %}{% endblock %}`.

## `_today_list.html` — the today/overdue partial

This is where most of the context dict gets spent.

```jinja
{# Shared today-todo list. ...
   Required context:
     today_items   — dict {class_id: {cls, items}} from _collect_items_in_range
     overdue       — same shape, items with due dates in the past
     all_classes   — list of Class for the add-task class picker
     default_class_id — pre-selected class in the add-task form
#}
```

`{# ... #}` is a Jinja comment — stripped at render time, doesn't appear
in the output HTML. The note documents what context the partial expects.

### Header

```html
<section class="today-list-block">
    <div class="today-list-head">
        <h2>Today <span class="subtle">{{ today.strftime('%a %b %d') }}</span></h2>
    </div>
```

`today` is the `today_start` datetime from `home()`. `.strftime(...)` is
a Python method called *from inside Jinja* — Jinja can invoke any method
the object supports. `'%a %b %d'` formats like `Tue May 05`.

```html
{% if all_classes %}
<button type="button" class="add-task-btn" data-open-modal="add-task-modal">+ Add task</button>
{% endif %}
```

Only show the add-task button if at least one class exists — adding a
task with no classes available would dead-end.

### The `render_item` macro

```jinja
{% macro render_item(it) -%}
<li class="todo-row {% if it.completed %}done{% endif %}"
    data-kind="{{ it.kind }}" data-id="{{ it.id }}" data-class-id="{{ it.class_id }}"
    data-title="{{ it.title }}"
    data-due-date="{{ it.due_at.strftime('%Y-%m-%d') if it.due_at else '' }}">
    <span class="todo-drag-handle" title="Drag to reorder priority" aria-label="Drag">
        <span class="todo-burger" aria-hidden="true"></span>
    </span>
    <button type="button" class="todo-toggle" aria-pressed="{{ 'true' if it.completed else 'false' }}" aria-label="Toggle done">
        <span class="todo-circle"></span>
    </button>
    <span class="todo-title">{{ it.title }}</span>
    {% if it.sub_kind %}<span class="todo-sub-kind">{{ it.sub_kind }}</span>{% endif %}
    {% if it.due_at and (it.due_at.hour or it.due_at.minute) %}<span class="todo-when">{{ it.due_at.strftime('%H:%M') }}</span>{% endif %}
    {% if it.kind == 'task' %}
    <button type="button" class="todo-edit" data-id="{{ it.id }}" aria-label="Edit task" title="Edit">
        <span class="todo-edit-icon" aria-hidden="true">✎</span>
    </button>
    <button type="button" class="todo-del" data-id="{{ it.id }}" aria-label="Delete task">×</button>
    {% endif %}
</li>
{%- endmacro %}
```

A **macro** is a reusable mini-template — like a function that returns
HTML. Defined once, called many times. Reduces duplication: overdue and
today both render their items the same way.

A few details:

- `{% macro render_item(it) -%}` and `{%- endmacro %}` — the dashes
  (`-%}`, `{%-`) strip surrounding whitespace, which keeps the rendered
  HTML tighter.
- `{{ it.kind }}`, `{{ it.id }}`, `{{ it.class_id }}` — pulled into
  `data-*` attributes so JS (`todo.js`) can find rows by class id, etc.
  These attributes are how the JS layer talks to the server later
  (toggling, editing, deleting).
- `{{ 'true' if it.completed else 'false' }}` — Jinja's ternary. Used
  for `aria-pressed` so screen readers know the toggle's state.
- `{% if it.due_at and (it.due_at.hour or it.due_at.minute) %}` — only
  show the time if the due datetime has a real time-of-day. A task due
  "tomorrow" with no specific time will have hour=0 and minute=0, and
  we'd rather hide `00:00` than show it.
- `{% if it.kind == 'task' %}` — edit and delete buttons appear only
  for tasks. Events come from the syllabus parser, so they're
  intentionally not editable from the today list.

### Overdue section

```jinja
{% if overdue %}
<div class="todo-section overdue-section">
    <h3 class="todo-section-head">Overdue</h3>
    {% for slot in overdue.values() %}
    <div class="class-block">
        <a href="/classes/{{ slot.cls.id }}" class="class-block-head">
            <span class="class-code">{{ slot.cls.code }}</span>
            <span class="class-name">{{ slot.cls.name }}</span>
        </a>
        <ul class="todo-list todo-list-draggable">
            {% for it in slot["items"] %}{{ render_item(it) }}{% endfor %}
        </ul>
    </div>
    {% endfor %}
</div>
{% endif %}
```

Three nested loops:

1. `{% if overdue %}` — empty dict is falsy, so the whole block hides
   when there's nothing overdue.
2. `{% for slot in overdue.values() %}` — `overdue` is a dict; `.values()`
   gives the slot dicts (`{"cls": ..., "items": ...}`).
3. `{% for it in slot["items"] %}` — each item invokes the macro.

`slot.cls` and `slot["items"]` are equivalent in Jinja — both forms work
because Jinja transparently tries attribute access then dict lookup.

### Today section

```jinja
{% if today_items %}
    {% for slot in today_items.values() %}
    <div class="class-block">
        <a href="/classes/{{ slot.cls.id }}" class="class-block-head">
            <span class="class-code">{{ slot.cls.code }}</span>
            <span class="class-name">{{ slot.cls.name }}</span>
        </a>
        <ul class="todo-list todo-list-draggable">
            {% for it in slot["items"] %}{{ render_item(it) }}{% endfor %}
        </ul>
    </div>
    {% endfor %}
{% else %}
    {% if not overdue %}<p class="empty">Nothing for today. Click <strong>+ Add task</strong> to add one.</p>{% endif %}
{% endif %}
```

Same structure as overdue, with one extra detail: the empty-state
message only appears if there's nothing to show *anywhere* — both
`today_items` and `overdue` are empty. Showing "Nothing for today"
underneath an Overdue list would be confusing.

### Edit + Add task modals

```jinja
{% if all_classes %}
<div class="modal-overlay" id="edit-task-modal" hidden>
    ...
</div>

<div class="modal-overlay" id="add-task-modal" hidden>
    ...
    <select name="class_id" data-add-task-class>
        {% for c in all_classes %}
        <option value="{{ c.id }}" {% if c.id == default_class_id %}selected{% endif %}>{{ c.code }} — {{ c.name }}</option>
        {% endfor %}
    </select>
    ...
</div>
{% endif %}
```

Two modals, both hidden until the user clicks the `+` or pencil button.

The `<select>` is the only place `default_class_id` gets used: pre-select
the option whose `c.id` matches it. On the home page that's the first
class alphabetically; on the class detail page that's the current class
being viewed (the floating panel reuses this same partial).

The whole `{% if all_classes %}` block is skipped on a fresh install
with zero classes — no point rendering an add-task modal if there's no
class to attach the task to.

## End-to-end: dict → HTML

```
home() builds context dict: {classes, today, today_items, overdue, default_class_id}
        │
        ▼
TemplateResponse loads home.html
        │
        ▼
home.html says {% extends "base.html" %}
        │
        ▼
base.html renders <html><head>...<main>{% block content %}{% endblock %}</main>...</html>
   with content block filled by home.html's body
        │
        ▼
home.html body:
   • for each c in classes → <li><a>...</a></li>
   • {% with all_classes = classes %}{% include "_today_list.html" %}{% endwith %}
        │
        ▼
_today_list.html:
   • header with today.strftime(...)
   • if overdue: loop overdue.values() → render_item(it) per item
   • if today_items: loop today_items.values() → render_item(it) per item
   • else if no overdue: "Nothing for today" message
   • add-task / edit-task modals (if all_classes)
        │
        ▼
Single HTML string returned to Brave with HTTP 200, Content-Type: text/html
```

That string is what Brave receives — at this point the server's done.
What happens next (theme toggle, drag-and-drop, modal opens, AJAX task
toggles) is the JS layer taking over in the browser, which is where
`todo.js`, `modal.js`, and `upload.js` come in.
