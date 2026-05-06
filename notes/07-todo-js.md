# `todo.js` — the today list, alive

The biggest of the three JS files (302 lines). It owns every
interactive thing the user can do with a row in the today list:
checking it off, dragging it to reorder, adding a new one, editing the
title or due date, deleting it.

File: `static/todo.js`. Loaded from `home.html` and (on the class page)
from `class.html` — anywhere the today list shows up.

## The plain-English story

Picture the today list. Each row is a task or event with:

- a drag-handle on the left (three lines / "burger"),
- a circle to check it done,
- a title,
- on the right, an edit pencil and a delete X (only for tasks).

Plus, above the list, a `+ Add task` button that opens a popup, and
elsewhere on the page an Edit popup that opens when you click a
pencil.

This file's job is: when you do anything to one of these rows, talk
to the server about it, and update the visible page so it stays
honest. There's a lot of "talk to the server" in here — six different
endpoints get hit, all of them on `main.py`'s task/event routes.

The pattern, repeated for every behavior:

1. Find the relevant button or form on page load.
2. Attach a click/submit listener.
3. When the user does the thing, send an HTTP request to the server
   (using `fetch`).
4. On success, update what the user sees. On failure, show an error
   and roll back.

A nice trick used here: most updates are **optimistic** — the page
flips first, the request goes second, and we only undo if the request
fails. The user sees instant feedback even though the server is
authoritative.

## The IIFE wrapper

```js
(function () {
    ...
})();
```

Same sealed-box pattern as `modal.js` and `upload.js`. The whole file's
helpers live inside, can't collide with names elsewhere.

## The "bind once" pattern

You'll see this near the top of every binding function:

```js
if (btn.dataset.bound === '1') return;
btn.dataset.bound = '1';
```

**Plain English:** "Has this element already been wired up? If yes,
do nothing. Otherwise, mark it as wired-up and continue."

`btn.dataset.bound` reads/writes the `data-bound` attribute on the
element. The first time we touch a button, `dataset.bound` is
undefined → we proceed and set it to `'1'`. Any future call with the
same button bails out at the top.

Why does this matter? At the bottom of the file there's
`window.bindTodoToggles = bindAll`, which exposes the wire-up
function globally. Other code (or a future feature) can call
`bindTodoToggles()` after injecting new rows — it would re-scan the
DOM and wire up new rows, while skipping ones already wired. Without
the guard, every existing row would get a duplicate listener and
clicking once would fire twice.

## Toggle — checking a row off

```js
function bindToggle(btn) {
    if (btn.dataset.bound === '1') return;
    btn.dataset.bound = '1';
    btn.addEventListener('click', async () => {
        const row = btn.closest('.todo-row');
        if (!row) return;
        const kind = row.dataset.kind;
        const id = row.dataset.id;
        const url = kind === 'event' ? `/events/${id}/toggle` : `/tasks/${id}/toggle`;
        const wasDone = row.classList.contains('done');
        row.classList.toggle('done');
        btn.setAttribute('aria-pressed', wasDone ? 'false' : 'true');
        try {
            const r = await fetch(url, {
                method: 'POST',
                headers: { 'Accept': 'application/json' },
            });
            if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
        } catch (err) {
            row.classList.toggle('done');
            btn.setAttribute('aria-pressed', wasDone ? 'true' : 'false');
            console.error('toggle failed:', err);
        }
    });
}
```

**Plain English:** "When the user clicks the circle on a row, flip
the row's 'done' look right away (instant feedback), then tell the
server about it. If the server complains, flip the look back."

This is the **optimistic UI** pattern. Don't wait for the server
before showing the change — show it immediately, undo only on
failure. The user almost never sees a failure (toggling never really
fails), so almost every click feels instantaneous.

**Line by line:**

- `btn.closest('.todo-row')` — walk up from the toggle button to find
  the row it belongs to. (Same `closest` we saw in `modal.js`.)
- `row.dataset.kind` and `row.dataset.id` — these come from the
  template (`data-kind="task"`, `data-id="5"`). They tell us which
  endpoint to hit. Note the data is on the *row*, not on the button —
  one row, one piece of state.
- `kind === 'event' ? '/events/.../toggle' : '/tasks/.../toggle'` — two
  endpoints, picked by kind. Tasks and events both have toggle
  endpoints in `main.py`.
- `const wasDone = row.classList.contains('done')` — remember the
  state *before* we flip, so we can undo on failure.
- `row.classList.toggle('done')` — flip the `done` class. CSS
  styles `.done` rows differently (faded, strikethrough title).
- `btn.setAttribute('aria-pressed', ...)` — keep the accessibility
  attribute in sync with the visual state.
- `await fetch(url, { method: 'POST', headers: { 'Accept': 'application/json' } })`
  — send a POST to the toggle endpoint. The `Accept: application/json`
  header tells the server "respond with JSON" (a lot of routes in
  `main.py` decide whether to redirect or return JSON based on this).
- `if (!r.ok) throw ...` — `fetch` does NOT throw on 4xx/5xx
  responses; you have to check `.ok` (true if status is 200–299) and
  throw yourself.
- The `catch` block undoes both visual changes — flips the class
  back, restores `aria-pressed`. No popup; we just log to the console.
  A toggle failing is rare; if the user sees the row didn't actually
  change, they can try again.

The `async` keyword on the click handler lets us use `await` inside
it. `await` pauses the function until the fetch completes, so the
`try/catch` works naturally.

## Delete — remove a task

```js
function bindDelete(btn) {
    if (btn.dataset.bound === '1') return;
    btn.dataset.bound = '1';
    btn.addEventListener('click', async () => {
        if (!confirm('Delete this task?')) return;
        const id = btn.dataset.id;
        try {
            const r = await fetch(`/tasks/${id}/delete`, {
                method: 'POST',
                headers: { 'Accept': 'application/json' },
            });
            if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
            document.querySelectorAll(
                `.todo-row[data-kind="task"][data-id="${id}"]`
            ).forEach((row) => {
                row.style.transition = 'opacity 0.15s ease, max-height 0.15s ease';
                row.style.opacity = '0';
                row.style.maxHeight = '0';
                setTimeout(() => row.remove(), 160);
            });
        } catch (err) {
            alert('Could not delete task: ' + err.message);
        }
    });
}
```

**Plain English:** "When the user clicks the X, ask 'Are you sure?'.
If they confirm, ask the server to delete it. On success, fade and
collapse every visible copy of that row, then remove them from the
page. On failure, show an alert."

Note this one is *not* optimistic — we wait for the server before
removing the row. Deletes are destructive; if they fail, you don't
want the row to vanish and the user to think it worked.

**Line by line:**

- `confirm('Delete this task?')` — built-in browser dialog with OK /
  Cancel. Returns true if the user clicked OK.
- The fetch and the `if (!r.ok)` check are the same shape as toggle.
- `document.querySelectorAll('.todo-row[data-kind="task"][data-id="${id}"]')`
  — find every row on the page with this kind+id combo. Why "every"?
  The same task can show up in multiple lists at once: today's list,
  the floating panel on a class page, the week view. Removing all
  copies keeps them in sync.
- The `forEach` block animates the row away: set CSS transitions for
  opacity and max-height, then change those properties so the row
  fades and collapses. After 160 ms, the `setTimeout` callback fires
  and `row.remove()` actually deletes the DOM node.

`160` is just slightly longer than the 150 ms transition — gives the
animation time to finish before we yank the element out.

## Edit — opening the edit popup

```js
function bindEditButton(btn) {
    if (btn.dataset.bound === '1') return;
    btn.dataset.bound = '1';
    btn.addEventListener('click', () => {
        const row = btn.closest('.todo-row');
        const modal = document.getElementById('edit-task-modal');
        if (!row || !modal) return;
        const form = modal.querySelector('form[data-edit-task]');
        if (!form) return;
        form.querySelector('input[name="task_id"]').value = row.dataset.id || '';
        form.querySelector('input[name="title"]').value = row.dataset.title || '';
        form.querySelector('input[name="due_at"]').value = row.dataset.dueDate || '';
        modal.hidden = false;
        document.body.classList.add('modal-open');
        const titleInput = form.querySelector('input[name="title"]');
        if (titleInput) { titleInput.focus(); titleInput.select(); }
    });
}
```

**Plain English:** "When the user clicks the pencil on a row, find
the (single, shared) edit popup, copy the row's title / due date /
id into the form fields, show the popup, and put the cursor in the
title field with the existing text selected so the user can just
start typing to replace it."

Recall the row in `_today_list.html`:

```html
<li class="todo-row" data-kind="task" data-id="5"
    data-title="Read chapter 4"
    data-due-date="2026-05-08">
```

The current values live on the row's data attributes. The edit form
is shared — same form for all rows — so we have to *copy* the values
in each time the popup opens.

Note this function opens the popup directly (sets `hidden = false`,
adds the `modal-open` class) rather than going through `modal.js`'s
opener. Why? Because `modal.js`'s opener only knows how to find a
modal by id and reveal it — it has no idea we need to copy values
into form fields first. The opening is bundled in here so the
sequence "fill values → reveal" is atomic.

`titleInput.focus()` puts the cursor in the title field;
`titleInput.select()` highlights the existing text. Together, the
user can just start typing and it replaces what's there.

## Edit — submitting the edit form

```js
function bindEditTaskForm(form) {
    if (form.dataset.bound === '1') return;
    form.dataset.bound = '1';
    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        const id = form.querySelector('input[name="task_id"]').value;
        const title = (form.querySelector('input[name="title"]').value || '').trim();
        const due = form.querySelector('input[name="due_at"]').value;
        if (!id || !title) return;
        const fd = new FormData();
        fd.append('title', title);
        if (due) fd.append('due_at', due + 'T23:59:00');
        try {
            const r = await fetch(`/tasks/${id}/edit`, {
                method: 'POST',
                headers: { 'Accept': 'application/json' },
                body: fd,
            });
            if (!r.ok) {
                let detail = `${r.status} ${r.statusText}`;
                try { const j = await r.json(); if (j.detail) detail = j.detail; } catch (_) {}
                throw new Error(detail);
            }
            document.querySelectorAll(
                `.todo-row[data-kind="task"][data-id="${id}"]`
            ).forEach((row) => {
                row.dataset.title = title;
                row.dataset.dueDate = due || '';
                const titleEl = row.querySelector('.todo-title');
                if (titleEl) titleEl.textContent = title;
            });
            const modal = document.getElementById('edit-task-modal');
            if (modal) {
                modal.hidden = true;
                document.body.classList.remove('modal-open');
            }
        } catch (err) {
            alert('Could not save: ' + err.message);
        }
    });
}
```

**Plain English:** "When the user submits the edit form, stop the
browser's default submit behavior. Read the new title and due date,
build a fake form payload, POST it. On success, update every visible
copy of the row (both the visible title and the row's data
attributes), then close the popup. On failure, show an alert."

A few new ideas in this one:

`e.preventDefault()` — by default, submitting a form makes the
browser navigate to the form's `action` URL with the form data. We
want to send the data ourselves via fetch and *stay* on the page.
This cancels the default.

`new FormData()` — builds a payload that mimics what a form submit
would send (an `application/x-www-form-urlencoded` or
`multipart/form-data` body). `fd.append('title', title)` adds a field.
`fd.append('due_at', due + 'T23:59:00')` adds the due date in
ISO-with-time format — the date input gives us only `2026-05-08`, we
append `T23:59:00` so the server reads it as "due end-of-day."

The server endpoint (`/tasks/{id}/edit` in `main.py`) accepts a form
post and returns JSON when `Accept: application/json` is set.

The error-detail dance:

```js
let detail = `${r.status} ${r.statusText}`;
try { const j = await r.json(); if (j.detail) detail = j.detail; } catch (_) {}
throw new Error(detail);
```

If the response wasn't OK, we want a useful message. Start with the
HTTP status (`400 Bad Request`). Try to parse the body as JSON in
case the server included a `detail` field with a friendly message
(FastAPI's `HTTPException(400, "Title required")` produces this).
If the body wasn't JSON or had no detail, we fall back to the status
line.

The "patch in place" updates after success:

```js
document.querySelectorAll(
    `.todo-row[data-kind="task"][data-id="${id}"]`
).forEach((row) => {
    row.dataset.title = title;
    row.dataset.dueDate = due || '';
    const titleEl = row.querySelector('.todo-title');
    if (titleEl) titleEl.textContent = title;
});
```

Why patch instead of reload the page? The comment in the source
explains:

> A reload would re-render the today list with today's filter,
> dropping any task whose new due date moved out of today/overdue —
> making a rescheduled task look like it was deleted.

So if you change a task's due date from today to next week, a reload
would correctly hide it from "Today" — but the user just edited it
and expects to see their change. Patching in place keeps it visible
on the current page; the next page load will filter it correctly.

## Drag to reorder — the most complex part

```js
function bindDrag(list) {
    if (list.dataset.dragBound === '1') return;
    list.dataset.dragBound = '1';
    let dragRow = null;
    let pointerStart = null;
    let isDragging = false;
    const MOVE_THRESHOLD = 5;
    ...
}
```

**Plain English:** "When the user grabs a row's burger handle and
drags it up or down, reorder the rows visually with a smooth
animation. When they let go, tell the server the new order so the
priority sticks across page loads."

`list` here is one `<ul>` (the rows for one class). `dragRow` /
`pointerStart` / `isDragging` are state variables shared between the
inner functions — they all need to know "is a drag happening, which
row is being dragged, where did the cursor start?"

`MOVE_THRESHOLD = 5` — the user has to move the cursor at least 5
pixels before we treat it as a drag (vs. an accidental wiggle on a
plain click).

### `applyFlipReorder(insertBeforeRow)` — animate a reorder

This is the "FLIP" animation technique. It's clever and worth a
careful look.

**Plain English:** "I want to move row A above row B in the list,
and I want every row that gets pushed around to slide smoothly into
its new position. Here's the trick: measure where every row is right
now, instantly move row A in the DOM, measure where every row ended
up, then for each row that moved, instantly position it back where
it *was* (using a transform), and finally animate that transform
back to zero. Visually it looks like the rows slide into place."

FLIP = First / Last / Invert / Play.

- **First:** measure each row's position before any change.
- **Last:** make the change, then measure each row's position after.
- **Invert:** for each row, work out the difference (`first.top -
  last.top`) and apply a `translateY` that puts it visually back at
  First.
- **Play:** turn on a CSS transition and clear the transform — it
  animates back to zero (i.e., the actual new position).

```js
function applyFlipReorder(insertBeforeRow) {
    const currentNext = dragRow.nextSibling;
    if (insertBeforeRow === dragRow) return;
    if (insertBeforeRow && insertBeforeRow === currentNext) return;
    if (!insertBeforeRow && currentNext === null) return;

    const all = Array.from(list.querySelectorAll('.todo-row'));
    const firstRects = new Map();
    all.forEach((c) => firstRects.set(c, c.getBoundingClientRect()));
    if (insertBeforeRow) list.insertBefore(dragRow, insertBeforeRow);
    else list.appendChild(dragRow);
    all.forEach((c) => {
        const first = firstRects.get(c);
        const last = c.getBoundingClientRect();
        const dy = first.top - last.top;
        if (Math.abs(dy) < 1) return;
        c.style.transition = 'none';
        c.style.transform = `translateY(${dy}px)`;
        void c.offsetHeight;
        c.style.transition = 'transform 0.18s cubic-bezier(0.22, 1, 0.36, 1)';
        c.style.transform = '';
    });
}
```

**Line by line:**

- The three early returns short-circuit no-op moves: trying to insert
  the dragged row before itself, or in the position it's already in.
- `Array.from(list.querySelectorAll('.todo-row'))` — get every row in
  the list as a real array.
- `firstRects` is a `Map` keyed by row → its rectangle (position +
  size on screen). `getBoundingClientRect()` is the browser's "where
  is this thing right now."
- `list.insertBefore(dragRow, insertBeforeRow)` (or `appendChild`) —
  actually move the dragged row in the DOM. Other rows shift to make
  room.
- The second `forEach` does the FLIP magic for each row:
  - Read the new rect (`last`).
  - Compute `dy = first.top - last.top` — how far the row "needs" to
    move *up* to look like it didn't change.
  - If less than 1 pixel of difference, skip (no animation needed).
  - `transition = 'none'` + `transform = translateY(dy)` — instantly
    snap the row back to where it was. Without this, the user
    would see the rows jump.
  - `void c.offsetHeight` — a deliberate read of a layout property to
    force the browser to apply the snap before the next line. Without
    this the browser would batch the snap and the animation together
    and skip the visual snap step entirely.
  - `transition = 'transform 0.18s cubic-bezier(...)' ` + `transform
    = ''` — turn animation back on, clear the transform. The browser
    animates back to the real position smoothly.

That's it — the rows slide instead of jumping, and the user's
dragging feels physical.

### `moveTowards(clientY)` — pick where to insert

```js
function moveTowards(clientY) {
    if (!dragRow) return;
    const others = Array.from(list.querySelectorAll('.todo-row:not(.dragging)'));
    let insertBefore = null;
    for (const target of others) {
        const rect = target.getBoundingClientRect();
        if (clientY < rect.top + rect.height / 2) { insertBefore = target; break; }
    }
    applyFlipReorder(insertBefore);
}
```

**Plain English:** "Walk down the list of other rows. The first row
whose middle is below the cursor is the row we should insert *before*.
If no such row exists, the cursor is below all of them — append to
the end (`insertBefore = null`)."

Then call `applyFlipReorder` with that target. (`applyFlipReorder`
short-circuits when nothing actually changes, so calling it on every
mouse-move is fine.)

### `persistOrder()` — tell the server the new order

```js
function persistOrder() {
    const ids = Array.from(list.querySelectorAll('.todo-row'))
        .filter((el) => el.dataset.kind === 'task')
        .map((el) => parseInt(el.dataset.id, 10))
        .filter((n) => !Number.isNaN(n));
    if (ids.length === 0) return;
    fetch('/tasks/reorder', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
        body: JSON.stringify({ task_ids: ids }),
    }).catch((err) => console.error('reorder failed:', err));
}
```

**Plain English:** "Read the current row order from the DOM. Pull
out the task IDs (skip events — they don't have a position). POST
the list of IDs to the reorder endpoint. If it fails, just log; the
visual order is already correct, the server will catch up next page
load… or not, but the failure isn't worth interrupting the user."

`Content-Type: application/json` plus a `JSON.stringify(...)` body
sends actual JSON (vs. the form-data body we used for edit/add).
The reorder endpoint expects `{ "task_ids": [3, 5, 1, 7] }`.

### Pointer event handlers

```js
function onPointerMove(e) {
    if (!dragRow || !pointerStart) return;
    const dy = e.clientY - pointerStart.y;
    if (!isDragging && Math.abs(dy) < MOVE_THRESHOLD) return;
    if (!isDragging) {
        isDragging = true;
        dragRow.classList.add('dragging');
        document.body.classList.add('cards-dragging');
    }
    moveTowards(e.clientY);
    e.preventDefault();
}
```

**Plain English:** "On every cursor movement: if no row is being
dragged, ignore. If the cursor has moved less than 5 pixels and we
haven't started dragging yet, ignore. Otherwise mark drag as active
(adding CSS classes for the visual lift effect) and reorder."

The `MOVE_THRESHOLD` check prevents a click from being mistaken for
a drag. The user often clicks the burger handle without intending
to drag.

```js
function onPointerUp() {
    if (!dragRow) return;
    const wasDragging = isDragging;
    if (dragRow) dragRow.classList.remove('dragging');
    document.body.classList.remove('cards-dragging');
    dragRow = null;
    pointerStart = null;
    isDragging = false;
    if (wasDragging) persistOrder();
}
```

**Plain English:** "Cursor released. Clean up the visual state, reset
the tracking variables. If we actually dragged (vs. just a click),
save the new order to the server."

```js
list.querySelectorAll('.todo-drag-handle').forEach((handle) => {
    handle.addEventListener('pointerdown', (e) => {
        if (e.button !== undefined && e.button !== 0) return;
        const row = handle.closest('.todo-row');
        if (!row) return;
        dragRow = row;
        pointerStart = { x: e.clientX, y: e.clientY };
        isDragging = false;
        e.preventDefault();
    });
});
document.addEventListener('pointermove', onPointerMove);
document.addEventListener('pointerup', onPointerUp);
document.addEventListener('pointercancel', onPointerUp);
```

**Plain English:** "Wire up the drag-handle on each row to start a
drag. The move/up/cancel listeners go on the document — once a drag
starts, the cursor can leave the row, leave the list, even leave the
window, and we still want to track it."

`e.button !== 0` — only respond to left mouse button. Right-click
shouldn't start a drag.

`pointerdown` / `pointermove` / `pointerup` / `pointercancel` are
modern unified events that cover mouse, touch, and pen with one API.
`pointercancel` fires when the system intervenes (e.g., a browser
gesture); we treat it like `pointerup` to clean up state.

## Add task — submit the add-task form

```js
function bindAddTaskForm(form) {
    if (form.dataset.bound === '1') return;
    form.dataset.bound = '1';
    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        const titleInput = form.querySelector('input[name="title"]');
        const dueInput = form.querySelector('input[name="due_at"]');
        const classSelect = form.querySelector('[data-add-task-class]');
        const title = (titleInput.value || '').trim();
        if (!title) return;
        const classId = classSelect ? classSelect.value : null;
        const url = classId
            ? `/classes/${classId}/tasks`
            : form.action;
        const fd = new FormData();
        fd.append('title', title);
        if (dueInput && dueInput.value) {
            fd.append('due_at', dueInput.value + 'T23:59:00');
        }
        try {
            const r = await fetch(url, {
                method: 'POST',
                headers: { 'Accept': 'application/json' },
                body: fd,
            });
            if (!r.ok) { ... }
            const dueValue = dueInput ? dueInput.value : '';
            titleInput.value = '';
            if (dueInput) dueInput.value = '';
            if (dueValue) {
                const t = new Date();
                const todayStr = t.getFullYear() + '-' +
                    String(t.getMonth() + 1).padStart(2, '0') + '-' +
                    String(t.getDate()).padStart(2, '0');
                if (dueValue > todayStr) {
                    alert(`Task added — due ${dueValue}. It won't appear on the Today list until that date; find it on the class page or Week view.`);
                }
            }
            window.location.reload();
        } catch (err) {
            console.error('add-task failed:', err);
            alert('Could not add task: ' + err.message);
        }
    });
}
```

**Plain English:** "When the user submits the *Add task* form, build
a payload with the title, due date, and class. POST to the right
class's task endpoint. On success, clear the form, warn the user if
their due date is in the future (because the new task won't appear
on the Today list), then reload the page so the new row shows up."

A few details:

`form.action` is `/classes/0/tasks` — a stub. The form template was
written for a fallback no-JS path, but JS rewrites the URL to the
selected class's id from the dropdown:

```js
const url = classId
    ? `/classes/${classId}/tasks`
    : form.action;
```

The future-due warning:

```js
if (dueValue > todayStr) {
    alert(`Task added — due ${dueValue}. It won't appear on the Today list...`);
}
```

The Today list filters server-side — only items due today, overdue,
or with no date show up. If the user adds a task due next Tuesday,
the server saves it correctly, but the list won't show it. Without
this alert, the user thinks the add silently failed.

`dueValue > todayStr` works because both are `YYYY-MM-DD` strings —
ISO-format dates string-compare correctly.

`window.location.reload()` — unlike edit, add does a full reload
because the new row needs to appear in the right class block in the
right sort order, and patching that into the DOM by hand is more
trouble than it's worth.

## Wire-up at page load

```js
function bindAll() {
    document.querySelectorAll('.todo-toggle').forEach(bindToggle);
    document.querySelectorAll('.todo-del').forEach(bindDelete);
    document.querySelectorAll('.todo-edit').forEach(bindEditButton);
    document.querySelectorAll('.todo-list-draggable').forEach(bindDrag);
    document.querySelectorAll('form[data-add-task]').forEach(bindAddTaskForm);
    document.querySelectorAll('form[data-edit-task]').forEach(bindEditTaskForm);
}
bindAll();
window.bindTodoToggles = bindAll;
```

**Plain English:** "Run through the page once, find every kind of
clickable element we know how to handle, and wire each one up. Also
expose this as a global function so other code can call it later
after injecting new rows."

Six selectors, six binders. Each binder is idempotent (the
"bind-once" pattern), so calling `bindAll()` again only wires up new
elements.

`window.bindTodoToggles = bindAll` puts the function on the global
namespace so any other code on the page can call it. (Currently
nothing does, but it's there as an extension point.)

Note this script does not wait for `DOMContentLoaded` — it runs
whenever it executes. Because `<script defer>` is set in the
template, it runs after the DOM is parsed, so this works.

## How `todo.js` connects to the server

| User action       | JS hits                       | Server route in `main.py` |
|-------------------|-------------------------------|---------------------------|
| Toggle a task     | `POST /tasks/{id}/toggle`     | `toggle_task`             |
| Toggle an event   | `POST /events/{id}/toggle`    | `toggle_event`            |
| Delete a task     | `POST /tasks/{id}/delete`     | `delete_task`             |
| Edit a task       | `POST /tasks/{id}/edit`       | `edit_task`               |
| Reorder tasks     | `POST /tasks/reorder`         | `reorder_tasks`           |
| Add a task        | `POST /classes/{id}/tasks`    | `create_task`             |

These are the next set of routes worth walking — they're the server
side of every behavior in this file. That'll be the next note (or
notes) in the series.

## End-to-end flow

```
Page loads, todo.js runs bindAll().
        │
        ▼
Listeners attached to every toggle / delete / edit / drag handle / form on the page.
        │
        ▼
User does something:
   • Click circle  → bindToggle: optimistic flip + POST /tasks/{id}/toggle
   • Click X       → bindDelete: confirm + POST /tasks/{id}/delete + animate out
   • Click ✎       → bindEditButton: copy values into edit popup, open it
   • Submit edit   → bindEditTaskForm: POST /tasks/{id}/edit + patch DOM
   • Drag handle   → bindDrag: FLIP-animate reorder + POST /tasks/reorder
   • Submit add    → bindAddTaskForm: POST /classes/{id}/tasks + reload
        │
        ▼
Server saves the change in the SQLite DB.
        │
        ▼
Next page load reads the saved state and renders.
```

The browser side is now covered end to end: page loads, templates
render (`02`–`04`), `modal.js` handles popups (`05`), `upload.js`
handles drag-drop and theme (`06`), `todo.js` handles task
interactions (this note). Everything from here forward is on the
server side — the routes that handle the POSTs above.
