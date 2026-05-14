// Side panel: full Today list rendered narrow-form-factor. Read-only in
// this iteration — toggle/edit/delete come next. Keeps the same Apple-
// minimal row vocabulary as the web app: one row per task, drag-handle
// circle title time tag — no inline indicators.
//
// Data comes from /today.json (server-rendered shape that matches the
// web app's `today_buckets` template variable). The endpoint already
// merges today + overdue and dedupes — we just render what comes back.

import { api, NotAuthenticated } from "./lib/api.js";

const $ = (sel) => document.querySelector(sel);

// ---- Render ----
function renderRow(it, isOverdue) {
    const li = document.createElement("li");
    li.className = "todo-row";
    li.dataset.kind = it.kind;
    li.dataset.id = String(it.id);
    li.dataset.classId = it.class_id == null ? "0" : String(it.class_id);
    li.dataset.title = it.title;
    li.dataset.dueAt = it.due_at || "";
    li.dataset.startsAt = it.starts_at || "";
    li.dataset.tagId = it.tag_id == null ? "" : String(it.tag_id);
    li.dataset.isAllDay = it.is_all_day ? "1" : "";
    li.dataset.rrule = it.rrule || "";  // empty → non-recurring path
    li.dataset.notes = it.notes || "";
    if (it.completed) li.classList.add("done");
    if (isOverdue) li.classList.add("is-overdue");
    if (it.tag_color || it.sub_kind_color) {
        li.classList.add("has-tag");
        li.style.setProperty("--tag-color", it.tag_color || it.sub_kind_color);
    }

    const main = document.createElement("div");
    main.className = "todo-row-main";
    // Row body click → open editor. Tasks → showEditor; events →
    // showEventEditor. Both flows ignore button clicks (× delete,
    // toggle) that bubble up.
    if (it.kind === "task" || it.kind === "event") {
        // Stash the event sub_kind on the row's dataset so the event
        // editor can pre-fill the kind field on open.
        if (it.kind === "event") {
            li.dataset.subKind = it.sub_kind || "";
        }
        main.classList.add("clickable");
        main.addEventListener("click", (e) => {
            // Defensive: ignore clicks that bubbled from inner buttons.
            if (e.target.closest("button")) return;
            // Drag handles also start on pointerdown — by the time a
            // click bubbles up, the drag binder will have set the row
            // as 'dragging' if the user actually dragged. Skip the
            // editor open in that case.
            if (li.classList.contains("just-dragged")) {
                li.classList.remove("just-dragged");
                return;
            }
            if (it.kind === "task") showEditor(li);
            else showEventEditor(li);
        });
    }

    // Drag handle (≡) on the left — pointerdown starts a drag, see
    // bindDrag() below.
    const handle = document.createElement("span");
    handle.className = "todo-drag-handle";
    handle.setAttribute("aria-label", "Drag to reorder");
    handle.title = "Drag to reorder";
    main.appendChild(handle);

    const circle = document.createElement("button");
    circle.type = "button";
    circle.className = "todo-circle";
    circle.setAttribute("aria-label", it.completed ? "Mark not done" : "Mark done");
    circle.setAttribute("aria-pressed", it.completed ? "true" : "false");
    circle.addEventListener("click", (e) => {
        e.stopPropagation();
        onToggle(li);
    });
    main.appendChild(circle);

    const title = document.createElement("span");
    title.className = "todo-title";
    title.textContent = it.title;
    main.appendChild(title);

    // Time / range / "All day" — only one of these renders, mirroring
    // the web row macro's elif chain.
    const when = whenLabel(it);
    if (when) {
        const w = document.createElement("span");
        w.className = "todo-when";
        w.textContent = when;
        main.appendChild(w);
    }

    // Tag is rendered as a 3px left-edge stripe on the row itself (see
    // .todo-row's border-left in sidepanel.css), reading from the
    // --tag-color CSS variable that's already set on the row above. The
    // pill text was eating ~40-60px the title needed more — name still
    // surfaces in the row drawer + edit modal, so detail isn't lost.

    // Delete button — visible on row hover, plain × glyph. Recurring
    // tasks open the bottom-sheet picker; everything else uses a
    // confirm() prompt.
    const del = document.createElement("button");
    del.type = "button";
    del.className = "todo-del";
    del.setAttribute("aria-label", `Delete ${it.title}`);
    del.textContent = "×";
    del.addEventListener("click", (e) => {
        e.stopPropagation();
        onDelete(li);
    });
    main.appendChild(del);

    li.appendChild(main);
    return li;
}

function whenLabel(it) {
    // Compact 12h format so the time costs ~20-30px instead of the 50px
    // a "17:00" + "All day" string would take. Title gets the slack.
    // All-day collapses to a small bullet — same visual rhythm without
    // the four-letter label competing with the title for space.
    if (it.is_all_day) return "•";
    if (!it.due_at) return "";
    const m = it.due_at.match(/T(\d{2}):(\d{2})/);
    if (!m) return "";
    if (m[1] === "00" && m[2] === "00") return ""; // midnight = "no specific time"
    let h = parseInt(m[1], 10);
    const min = m[2];
    const ap = h >= 12 ? "p" : "a";
    if (h === 0) h = 12; else if (h > 12) h -= 12;
    return min === "00" ? `${h}${ap}` : `${h}:${min}${ap}`;
}

function renderBucket(bucket) {
    const block = document.createElement("section");
    block.className = "class-block";
    // bucket.class_id can be 0 for the Personal bucket (PERSONAL_BUCKET
    // sentinel). Stringified so cross-class drag can read it via
    // dataset; '0' is the convention the server's edit_task interprets
    // as Personal alongside ''.
    block.dataset.classId = String(bucket.class_id ?? 0);

    const head = document.createElement("div");
    head.className = "class-block-head";
    if (bucket.is_personal) head.classList.add("is-personal");
    // Drag handle (≡) for reordering the entire class block. Only
    // meaningful in Today view; CSS hides it inside .day-block.
    const dragHandle = document.createElement("span");
    dragHandle.className = "class-block-drag";
    dragHandle.setAttribute("aria-label", "Drag to reorder this class");
    dragHandle.title = "Drag to reorder";
    head.appendChild(dragHandle);
    // The code + name are wrapped in a button so tapping the class
    // header drills into the detail surface. Personal has no detail
    // endpoint (no /classes/0), so it stays a plain label.
    const isReal = !bucket.is_personal && bucket.class_id > 0;
    const labelTag = isReal ? "button" : "span";
    const label = document.createElement(labelTag);
    label.className = "class-block-label";
    if (isReal) {
        label.type = "button";
        label.setAttribute("aria-label", `Open ${bucket.code} detail`);
        label.addEventListener("click", () => showClassDetail(bucket.class_id));
    }
    const code = document.createElement("span");
    code.className = "class-code";
    code.textContent = bucket.code;
    label.appendChild(code);
    if (!bucket.is_personal && bucket.name) {
        const name = document.createElement("span");
        name.className = "class-name";
        name.textContent = bucket.name;
        label.appendChild(name);
    }
    head.appendChild(label);
    block.appendChild(head);

    const list = document.createElement("ul");
    list.className = "todo-list";
    (bucket.items || []).forEach((it) => list.appendChild(renderRow(it, false)));
    // Only the today endpoint emits overdue_items; week endpoint omits
    // the key entirely — default to [] so the same renderBucket works
    // for both views.
    (bucket.overdue_items || []).forEach((it) => list.appendChild(renderRow(it, true)));
    block.appendChild(list);

    return block;
}

function renderEmpty(target) {
    target.innerHTML = "";
    const p = document.createElement("p");
    p.className = "muted empty";
    p.textContent = "Nothing for today. Use Quick Add to capture something.";
    target.appendChild(p);
}

// Show the inline login section. Hides every other surface so the
// password form is the only thing the user can interact with — login is
// a hard gate, not a competing affordance.
function showLogin() {
    const loggedOut = document.getElementById("logged-out");
    const signup = document.getElementById("signup-view");
    const loggedIn = document.getElementById("logged-in");
    if (loggedOut) loggedOut.hidden = false;
    if (signup) signup.hidden = true;
    if (loggedIn) loggedIn.hidden = true;
    // Surface the configured server URL so a user pointing at the wrong
    // host can fix it before guessing why their password "doesn't work."
    api.base().then((url) => {
        const el = document.getElementById("login-server-url");
        if (el) el.textContent = url;
    });
    const emailInput = document.querySelector("#login-form input[name='email']");
    if (emailInput) emailInput.focus();
}

function showSignup() {
    const loggedOut = document.getElementById("logged-out");
    const signup = document.getElementById("signup-view");
    const loggedIn = document.getElementById("logged-in");
    if (loggedOut) loggedOut.hidden = true;
    if (signup) signup.hidden = false;
    if (loggedIn) loggedIn.hidden = true;
    const emailInput = document.querySelector("#signup-form input[name='email']");
    if (emailInput) emailInput.focus();
}

function showApp() {
    const loggedOut = document.getElementById("logged-out");
    const signup = document.getElementById("signup-view");
    const loggedIn = document.getElementById("logged-in");
    if (loggedOut) loggedOut.hidden = true;
    if (signup) signup.hidden = true;
    if (loggedIn) loggedIn.hidden = false;
}

function renderError(target, msg) {
    target.innerHTML = "";
    const p = document.createElement("p");
    p.className = "muted error";
    p.textContent = msg;
    target.appendChild(p);
}

// ---- Toggle ----
async function onToggle(rowEl) {
    const kind = rowEl.dataset.kind;
    const id = rowEl.dataset.id;
    if (!id) return;
    const wasDone = rowEl.classList.contains("done");
    // Optimistic flip — feels snappy, the next /today.json refresh will
    // either confirm (server agrees) or naturally drop the row (the
    // server's hide_completed window means a still-overdue, now-complete
    // task should disappear on tomorrow's render but stay visible today).
    setRowDone(rowEl, !wasDone);
    try {
        if (kind === "event") await api.toggleEvent(id);
        else await api.toggleTask(id);
    } catch (err) {
        setRowDone(rowEl, wasDone);
        if (err instanceof NotAuthenticated) {
            showLogin();
        }
        // Other errors: revert handled, no need to alert — the UI rollback
        // is the signal.
    }
}

function setRowDone(rowEl, done) {
    rowEl.classList.toggle("done", done);
    const circle = rowEl.querySelector(".todo-circle");
    if (circle) {
        circle.setAttribute("aria-pressed", done ? "true" : "false");
        circle.setAttribute("aria-label", done ? "Mark not done" : "Mark done");
    }
}

// ---- Delete ----
// Tracks which row the bottom-sheet recurring picker is acting on.
// Cleared when the sheet closes either way.
let pendingDeleteRow = null;

async function onDelete(rowEl) {
    const kind = rowEl.dataset.kind;
    const isRecurring = kind === "task" && !!rowEl.dataset.rrule;
    if (isRecurring) {
        pendingDeleteRow = rowEl;
        showRecurringSheet(rowEl);
        return;
    }
    const label = kind === "event" ? "event" : "task";
    if (!confirm(`Delete this ${label}?`)) return;
    await runDelete(rowEl, "all");
}

function showRecurringSheet(rowEl) {
    const sheet = $("#rec-delete");
    const prompt = $("#rec-delete-prompt");
    const dueAt = rowEl.dataset.dueAt || "";
    const dateLabel = dueAt
        ? new Date(dueAt).toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" })
        : "this occurrence";
    prompt.textContent = `Starting ${dateLabel}, what do you want removed?`;
    sheet.hidden = false;
}

function hideRecurringSheet() {
    $("#rec-delete").hidden = true;
    pendingDeleteRow = null;
}

// Wire sheet buttons once at boot.
$("#rec-delete").querySelectorAll("button[data-mode]").forEach((btn) => {
    btn.addEventListener("click", async () => {
        const mode = btn.dataset.mode;
        const rowEl = pendingDeleteRow;
        hideRecurringSheet();
        if (!rowEl || mode === "cancel") return;
        await runDelete(rowEl, mode);
    });
});

// `mode` is one of: 'this' (exclude one occurrence), 'future' (cap rrule
// at this occurrence), 'all' (hard-delete the whole task/event row).
async function runDelete(rowEl, mode) {
    const kind = rowEl.dataset.kind;
    const id = rowEl.dataset.id;
    const dueAt = rowEl.dataset.dueAt || "";
    // Optimistically detach the row so the UI feels snappy. We re-load
    // /today.json afterwards anyway, which is the source of truth — but
    // also rebinds 'this date' / 'future' results that touch other rows.
    const parent = rowEl.parentNode;
    const next = rowEl.nextSibling;
    rowEl.remove();
    try {
        const isRecurring = !!rowEl.dataset.rrule;
        if (mode === "this") {
            await api.excludeTaskOccurrence(id, dueAt);
        } else if (mode === "future") {
            await api.endTaskAfter(id, dueAt);
        } else {
            // 'all' — non-recurring tasks land here too, with mode='all'.
            if (kind === "event") await api.deleteEvent(id);
            else await api.deleteTask(id);
        }
        // Re-pull when the change might have touched other rows: any
        // recurring delete (sibling occurrences) OR any 'this'/'future'
        // mode (which only run on recurring tasks anyway). Skip the
        // reload for the common case of deleting one non-recurring row,
        // where we already removed the only DOM element that matters.
        if (mode !== "all" || isRecurring) {
            await load();
        }
    } catch (err) {
        // Put the row back where it was so the user can retry.
        if (parent) parent.insertBefore(rowEl, next);
        if (err instanceof NotAuthenticated) {
            showLogin();
        } else {
            alert("Couldn't delete: " + err.message);
        }
    }
}

// ---- Editor ----
// View swap rather than overlay modal: side panel is too narrow to host
// a comfortable modal-on-list. Click a task row's body → side panel
// becomes the edit form. Save / Back → list view returns.
//
// Alerts and attachments aren't part of this surface — managed in the
// main Compass app (linked at the bottom of the form). The /tasks/{id}/
// edit endpoint does partial updates based on field presence, so the
// fields we DON'T submit stay untouched.

const editForm = $("#edit-form");
const editorView = $("#editor");
const listView = $("#content");

async function showEditor(rowEl) {
    listView.hidden = true;
    if (classDetailView) classDetailView.hidden = true;
    editorView.hidden = false;
    // FAB doesn't belong on a non-list surface — hide while editing.
    const fab = document.getElementById("add-task-fab");
    if (fab) fab.hidden = true;
    // Class + tag dropdowns must be populated BEFORE setting their value
    // — otherwise `select.value = "5"` for a missing option silently
    // drops to "" and the form saves with no class/tag. Cached promise
    // means subsequent clicks pay nothing.
    await ensureEditorLists();
    populateEditor(rowEl);
    const t = editForm.querySelector("input[name='title']");
    if (t) { t.focus(); t.select(); }
}

function hideEditor() {
    editorView.hidden = true;
    listView.hidden = false;
    if (classDetailView) classDetailView.hidden = true;
    const fab = document.getElementById("add-task-fab");
    if (fab) fab.hidden = false;
    setEditStatus("", "");
}

function populateEditor(rowEl) {
    const id = rowEl.dataset.id;
    editForm.task_id.value = id;
    editForm.title.value = rowEl.dataset.title || "";
    // due_at on a row is full ISO ("2026-05-08T17:00:00-04:00") but the
    // datetime-local input expects "YYYY-MM-DDTHH:MM" (no seconds, no tz).
    // For all-day, the input is type=date so trim to the YYYY-MM-DD prefix.
    const isAllDay = rowEl.dataset.isAllDay === "1";
    editForm.is_all_day.checked = isAllDay;
    syncAllDay();  // flips due_at type to date if needed
    setDateInput(editForm.due_at, rowEl.dataset.dueAt, isAllDay);
    setDateInput(editForm.starts_at, rowEl.dataset.startsAt, false);
    editForm.class_id.value = rowEl.dataset.classId || "0";
    editForm.tag_id.value = rowEl.dataset.tagId || "";
    editForm.rrule.value = rowEl.dataset.rrule || "";
    editForm.notes.value = rowEl.dataset.notes || "";
    syncRruleVisibility();  // hides/shows until field, disables starts_at
    // Pull rrule_until (not stored on the row) from /tasks/{id}/details.json.
    editForm.rrule_until.value = "";
    api.taskDetails(id).then((d) => {
        editForm.rrule_until.value = d && d.rrule_until ? d.rrule_until.slice(0, 16) : "";
    }).catch(() => { /* no rrule_until is fine */ });
}

function setDateInput(input, isoOrEmpty, isAllDay) {
    if (!input) return;
    if (!isoOrEmpty) { input.value = ""; return; }
    // ISO from server has tz offset; the datetime-local input wants the
    // wall-clock part only ("YYYY-MM-DDTHH:MM"). Slice to length 16 for
    // datetime-local; 10 for date-only (all-day).
    const len = isAllDay && input.type === "date" ? 10 : 16;
    input.value = isoOrEmpty.slice(0, len);
}

// Class + tag pickers: populate options from server. Cached across edits
// so we don't re-fetch every time a form opens — classes and tags rarely
// change mid-session, and this side panel is long-lived. Both the editor
// AND the FAB add-task form get populated from the same data so they
// stay in sync without refetching.
// /me.json payload, populated in boot() and after login/signup. Settings
// surface keys off this — email, masked xAI key, calendar URLs.
let cachedMe = null;
let classesPromise = null;
let tagsPromise = null;
async function ensureEditorLists() {
    if (!classesPromise) classesPromise = api.classes().catch(() => []);
    if (!tagsPromise) tagsPromise = api.tags().catch(() => []);
    const [classes, tags] = await Promise.all([classesPromise, tagsPromise]);
    // Editor's class dropdown.
    fillClassSelect(editForm.class_id, classes);
    // Add-task form's class dropdown.
    const addClassSel = $("#add-class");
    if (addClassSel) fillClassSelect(addClassSel, classes);
    // Editor's tag dropdown.
    fillTagSelect(editForm.tag_id, tags);
    // Add-task form's tag dropdown.
    const addTagSel = $("#add-tag");
    if (addTagSel) fillTagSelect(addTagSel, tags);
    // Inline "+ New tag" mini-form on both selects (idempotent — guarded
    // by a dataset flag).
    bindInlineNewTag(editForm.tag_id, editForm);
    if (addTagSel) bindInlineNewTag(addTagSel, $("#add-task-form"));
}

// Tag selects: System tags grouped above user tags (matches the website's
// add-task modal). Keep the leading "No tag" option, drop and re-add the
// rest from the supplied list.
function fillTagSelect(sel, tags) {
    while (sel.options.length > 1) sel.remove(1);
    const sys = tags.filter((t) => t.is_system);
    const own = tags.filter((t) => !t.is_system);
    if (sys.length) {
        const g = document.createElement("optgroup");
        g.label = "System";
        sys.forEach((t) => {
            const o = document.createElement("option");
            o.value = String(t.id);
            o.textContent = t.name;
            g.appendChild(o);
        });
        sel.appendChild(g);
    }
    if (own.length) {
        const g = document.createElement("optgroup");
        g.label = "Yours";
        own.forEach((t) => {
            const o = document.createElement("option");
            o.value = String(t.id);
            o.textContent = t.name;
            o.dataset.color = t.color || "";
            g.appendChild(o);
        });
        sel.appendChild(g);
    }
    // "+ New tag…" sentinel — picking it reveals an inline create form
    // adjacent to the select. Mirrors the website's add-task modal.
    const newOpt = document.createElement("option");
    newOpt.value = "__new__";
    newOpt.textContent = "+ New tag…";
    sel.appendChild(newOpt);
}

// Inline tag-create flow attached to a tag <select>. When the user picks
// "__new__", a small {name, color, Create} form appears below; on
// success the new tag is added to the select, selected, and the form
// hides again. Bind once per <select>.
function bindInlineNewTag(sel, formContainer) {
    if (!sel || !formContainer) return;
    if (sel.dataset.inlineTagBound === "1") return;
    sel.dataset.inlineTagBound = "1";
    // Build the inline mini-form lazily and inject it after the select's
    // parent <label> so it lives in the form's normal flow.
    const wrap = document.createElement("div");
    wrap.className = "inline-new-tag hidden";
    wrap.innerHTML = `
        <div class="new-tag-row">
            <input type="text" class="inline-new-tag-name" placeholder="Tag name" maxlength="60">
            <input type="color" class="inline-new-tag-color" value="#A04528">
            <button type="button" class="primary inline-new-tag-create">Create</button>
        </div>
        <div class="status inline-new-tag-status" hidden></div>
    `;
    sel.parentElement.parentElement.insertBefore(wrap, sel.parentElement.nextSibling);

    const nameInput = wrap.querySelector(".inline-new-tag-name");
    const colorInput = wrap.querySelector(".inline-new-tag-color");
    const createBtn = wrap.querySelector(".inline-new-tag-create");
    const statusEl = wrap.querySelector(".inline-new-tag-status");

    sel.addEventListener("change", () => {
        if (sel.value === "__new__") {
            wrap.classList.remove("hidden");
            nameInput.value = "";
            colorInput.value = "#A04528";
            statusEl.hidden = true;
            nameInput.focus();
        } else {
            wrap.classList.add("hidden");
        }
    });

    createBtn.addEventListener("click", async () => {
        const name = (nameInput.value || "").trim();
        if (!name) { nameInput.focus(); return; }
        statusEl.textContent = "Creating…";
        statusEl.className = "status pending";
        statusEl.hidden = false;
        try {
            const tag = await api.createTag({ name, color: colorInput.value });
            // Bust cache + refresh ALL tag selects in the doc so the
            // new tag is immediately pickable elsewhere too.
            tagsPromise = null;
            const fresh = await api.tags();
            document.querySelectorAll("select[name='tag_id']").forEach((s) => {
                fillTagSelect(s, fresh);
            });
            sel.value = String(tag.id);
            wrap.classList.add("hidden");
            statusEl.hidden = true;
        } catch (err) {
            if (err instanceof NotAuthenticated) { showLogin(); return; }
            statusEl.textContent = err.message || "Couldn't create.";
            statusEl.className = "status error";
        }
    });
}

function fillClassSelect(sel, classes) {
    // Keep the leading "Personal" option, drop and re-add the rest.
    while (sel.options.length > 1) sel.remove(1);
    classes.forEach((c) => {
        const o = document.createElement("option");
        o.value = String(c.id);
        o.textContent = `${c.code} — ${c.name}`;
        sel.appendChild(o);
    });
}

// All-day + Repeat both want to control starts_at: All-day clears+disables
// it (an all-day task is anchored to its due date), Repeat does the same
// (rrule + range is mutually exclusive — see CLAUDE.md). They OR their
// disable conditions so toggling one off doesn't re-enable the field
// while the other is still on.
function syncAllDay() {
    const on = editForm.is_all_day.checked;
    const due = editForm.due_at;
    const starts = editForm.starts_at;
    if (on) {
        if (due.value && due.type === "datetime-local") due.value = due.value.slice(0, 10);
        due.type = "date";
        if (starts) {
            starts.value = "";
            starts.type = "date";
        }
    } else {
        if (due.value && due.type === "date") due.value = due.value + "T17:00";
        due.type = "datetime-local";
        if (starts) {
            if (starts.value && starts.type === "date") starts.value = starts.value + "T09:00";
            starts.type = "datetime-local";
        }
    }
    syncStartsDisabled();
}

function syncRruleVisibility() {
    const showing = !!editForm.rrule.value;
    $("#edit-until-label").hidden = !showing;
    if (!showing) editForm.rrule_until.value = "";
    syncStartsDisabled();
}

function syncStartsDisabled() {
    const allDay = editForm.is_all_day.checked;
    const hasRrule = !!editForm.rrule.value;
    const disabled = allDay || hasRrule;
    const starts = editForm.starts_at;
    const label = $("#edit-starts-label");
    if (starts) {
        starts.disabled = disabled;
        if (disabled) starts.value = "";
    }
    if (label) label.classList.toggle("disabled", disabled);
}

editForm.is_all_day.addEventListener("change", syncAllDay);
editForm.rrule.addEventListener("change", syncRruleVisibility);

// Submit: every field the modal exposes is sent so the server's partial-
// update logic clears empties (notes='', tag_id='') correctly. Fields the
// modal doesn't expose (alerts, attachments) are absent and therefore
// preserved.
editForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const id = editForm.task_id.value;
    const title = (editForm.title.value || "").trim();
    if (!id || !title) return;
    const due = editForm.due_at.value;
    const starts = editForm.starts_at.value;
    if (starts && due && starts > due) {
        setEditStatus("Cannot save event, the start date must be before the end date", "error");
        return;
    }
    const fd = new FormData();
    fd.append("title", title);
    if (due) fd.append("due_at", due);
    if (starts) fd.append("starts_at", starts);
    if (editForm.tag_id.value === "__new__") {
        setEditStatus("Pick a tag (or finish creating the new one).", "error");
        return;
    }
    fd.append("tag_id", editForm.tag_id.value || "");
    fd.append("notes", editForm.notes.value || "");
    fd.append("class_id", editForm.class_id.value);
    fd.append("rrule", editForm.rrule.value || "");
    fd.append("is_all_day", editForm.is_all_day.checked ? "1" : "");
    fd.append("rrule_until", editForm.rrule_until.value || "");
    setEditStatus("Saving…", "pending");
    try {
        await api.editTask(id, fd);
        await load();
        hideEditor();
    } catch (err) {
        if (err instanceof NotAuthenticated) {
            showLogin();
            hideEditor();
            return;
        }
        setEditStatus("Couldn't save: " + err.message, "error");
    }
});

function setEditStatus(text, kind) {
    const el = $("#edit-status");
    if (!text) { el.hidden = true; return; }
    el.textContent = text;
    el.className = "status " + (kind || "");
    el.hidden = false;
}

// ---- Class detail view --------------------------------------------------
// Drill-down: click a class header → show that class's events + tasks.
// Same view-swap pattern as the editor; both hide #content and #class-
// detail's only-one-visible-at-a-time, never stacked.
const classDetailView = $("#class-detail");

// Cached class id of the currently-open detail surface — re-render
// (after upload / delete) and the doc-upload form's submit handler both
// need to know which class they're acting on.
let currentClassId = null;

async function showClassDetail(classId) {
    listView.hidden = true;
    editorView.hidden = true;
    classDetailView.hidden = false;
    currentClassId = classId;
    const fab = document.getElementById("add-task-fab");
    if (fab) fab.hidden = true;
    const tasksUl = $("#class-detail-tasks");
    const eventsUl = $("#class-detail-events");
    const docsUl = $("#class-detail-docs");
    tasksUl.innerHTML = "";
    eventsUl.innerHTML = "";
    docsUl.innerHTML = "";
    $("#class-detail-tasks-empty").hidden = true;
    $("#class-detail-events-empty").hidden = true;
    $("#class-detail-docs-empty").hidden = true;
    $("#class-detail-syllabus-section").hidden = true;
    $("#class-detail-code").textContent = "Loading…";
    $("#class-detail-name").textContent = "";
    try {
        const data = await api.classDetail(classId);
        $("#class-detail-code").textContent = data.class.code;
        $("#class-detail-name").textContent = data.class.name || "";

        // Syllabus section: inline iframe + open-in-tab + download links.
        // Hide the section entirely when there's no syllabus rather than
        // showing an empty-stuffed frame.
        if (data.syllabus && data.syllabus.filename) {
            const url = await api.fileUrl(data.syllabus.filename);
            $("#class-detail-pdf").src = url;
            const openTab = $("#class-pdf-open-tab");
            const dl = $("#class-pdf-download");
            openTab.href = url;
            // Anchor click in chrome-extension:// origin won't actually
            // navigate the panel — open in a real tab instead.
            openTab.onclick = (e) => {
                e.preventDefault();
                chrome.tabs.create({ url });
            };
            dl.href = url;
            dl.setAttribute("download", data.syllabus.filename);
            $("#class-detail-syllabus-section").hidden = false;
        }

        // Documents list. Title is a clickable text — opens the file in
        // a new tab (chrome.tabs.create, since the side panel can't
        // navigate cross-origin). × deletes after confirm.
        if (data.documents && data.documents.length) {
            for (const d of data.documents) {
                docsUl.appendChild(await renderDocRow(d));
            }
        } else {
            $("#class-detail-docs-empty").hidden = false;
        }

        if (data.tasks.length === 0) {
            $("#class-detail-tasks-empty").hidden = false;
        } else {
            data.tasks.forEach((t) => tasksUl.appendChild(renderRow(t, false)));
        }
        if (data.events.length === 0) {
            $("#class-detail-events-empty").hidden = false;
        } else {
            data.events.forEach((ev) => eventsUl.appendChild(renderRow(ev, false)));
        }
    } catch (err) {
        if (err instanceof NotAuthenticated) {
            showLogin();
            hideClassDetail();
            return;
        }
        $("#class-detail-code").textContent = "Couldn't load";
        $("#class-detail-name").textContent = err.message;
    }
}

async function renderDocRow(d) {
    const li = document.createElement("li");
    li.dataset.docId = String(d.id);
    const url = await api.fileUrl(d.filename);
    const link = document.createElement("a");
    link.className = "doc-link";
    link.href = url;
    link.textContent = d.title || d.filename;
    link.title = d.filename;
    link.addEventListener("click", (e) => {
        // Open in a real tab so Chrome's PDF viewer / native handler kicks
        // in. Anchor click inside chrome-extension:// origin doesn't
        // navigate the panel anyway.
        e.preventDefault();
        chrome.tabs.create({ url });
    });
    const del = document.createElement("button");
    del.type = "button";
    del.className = "doc-del";
    del.setAttribute("aria-label", `Delete ${d.title || d.filename}`);
    del.textContent = "×";
    del.addEventListener("click", async (e) => {
        e.stopPropagation();
        if (!confirm(`Delete "${d.title || d.filename}"?`)) return;
        try {
            await api.deleteDoc(d.id);
            li.remove();
            const remaining = $("#class-detail-docs").querySelectorAll("li").length;
            if (remaining === 0) $("#class-detail-docs-empty").hidden = false;
        } catch (err) {
            if (err instanceof NotAuthenticated) { showLogin(); return; }
            alert("Couldn't delete: " + err.message);
        }
    });
    li.appendChild(link);
    li.appendChild(del);
    return li;
}

function hideClassDetail() {
    classDetailView.hidden = true;
    listView.hidden = false;
    currentClassId = null;
    // Stop the PDF stream when the surface closes — nothing's reading it.
    const pdf = document.getElementById("class-detail-pdf");
    if (pdf) pdf.src = "";
    const fab = document.getElementById("add-task-fab");
    if (fab) fab.hidden = false;
}

// Doc upload form submit. Re-renders the whole class-detail surface so
// the new doc lands at the top of the list (server returns it sorted
// newest-first) and the empty-state copy goes away if needed.
const docUploadForm = document.getElementById("class-detail-doc-upload");
function setDocUploadStatus(text, kind) {
    const el = $("#class-detail-doc-status");
    if (!text) { el.hidden = true; return; }
    el.textContent = text;
    el.className = "status " + (kind || "");
    el.hidden = false;
}
docUploadForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!currentClassId) return;
    const fileInput = $("#class-detail-doc-file");
    const file = fileInput.files[0];
    if (!file) return;
    const title = (docUploadForm.title.value || "").trim();
    setDocUploadStatus("Uploading…", "pending");
    try {
        await api.uploadDoc(currentClassId, file, title);
        docUploadForm.reset();
        setDocUploadStatus("Uploaded ✓", "success");
        setTimeout(() => setDocUploadStatus("", ""), 800);
        await showClassDetail(currentClassId);
    } catch (err) {
        if (err instanceof NotAuthenticated) {
            showLogin();
            hideClassDetail();
            return;
        }
        setDocUploadStatus("Couldn't upload: " + err.message, "error");
    }
});

$("#class-detail-back").addEventListener("click", hideClassDetail);
$("#editor-back").addEventListener("click", hideEditor);
$("#editor-cancel").addEventListener("click", hideEditor);
$("#edit-open-app").addEventListener("click", async (e) => {
    e.preventDefault();
    const url = await api.base();
    chrome.tabs.create({ url });
});

// Class + tag dropdowns are populated by boot() after login is verified —
// see the bottom of this file. Doing it earlier would race the auth check
// and cache an empty result on a logged-out boot, leaving the dropdowns
// permanently blank for the rest of the session.

// ---- Drag-to-reorder -----------------------------------------------------
// Pointer-based, delegated on #content so it survives every load() that
// rebuilds the bucket DOM. Two operations:
//   1. Within-class reorder — pure DOM move + POST /tasks/reorder.
//   2. Cross-class move — drop into a different .class-block UL → also
//      PATCH the task's class_id via /tasks/{id}/edit before reordering.
// FLIP animation skipped for the V1 — direct DOM manipulation reads as
// "snappy" enough for the side panel's narrow column.
let dragRow = null;
let dragSourceClassId = null;
let dragScope = null;
let pointerStart = null;
let isDragging = false;
const DRAG_THRESHOLD = 5;

function classIdOfList(list) {
    const block = list.closest(".class-block");
    return block ? (block.dataset.classId || "0") : "0";
}

// Drag scope = the DOM element that defines the universe a row can move
// inside of. Rows can't cross this boundary. Determines which reorder
// endpoint runs on drop.
//   - month: scope = the .month-day-card the row started in. Per-day
//     reorder via /tasks/reorder-day, with cross-class allowed inside the
//     same day (one day-card hosts multiple class-blocks).
//   - classes drill-down: scope = the source UL itself (#class-detail-tasks
//     or #class-detail-events). /tasks/reorder for the rows inside.
//   - today: scope = listView (#content). Cross-class allowed; /tasks/reorder.
function dragScopeFor(row) {
    const monthCard = row.closest(".month-day-card");
    if (monthCard) return { kind: "month", el: monthCard };
    const classDetail = row.closest("#class-detail");
    if (classDetail) {
        // Tasks and events are separate ULs in the class-detail surface;
        // scope drag to whichever list the row started in so events
        // can't end up in the tasks UL or vice versa.
        return { kind: "classes", el: row.parentNode };
    }
    return { kind: "today", el: listView };
}

// Single delegated pointerdown listener so drag works across all three
// surfaces (today list, month day-cards, class-detail) without binding
// per-surface. The scope check happens only after we know we have a
// real drag handle to grab.
document.addEventListener("pointerdown", (e) => {
    const handle = e.target.closest(".todo-drag-handle");
    if (!handle) return;
    const row = handle.closest(".todo-row");
    if (!row) return;
    dragRow = row;
    dragSourceClassId = classIdOfList(row.parentNode);
    dragScope = dragScopeFor(row);
    pointerStart = { x: e.clientX, y: e.clientY };
    isDragging = false;
    e.preventDefault();
});

document.addEventListener("pointermove", (e) => {
    if (!dragRow || !pointerStart) return;
    const dx = e.clientX - pointerStart.x;
    const dy = e.clientY - pointerStart.y;
    if (!isDragging && Math.hypot(dx, dy) < DRAG_THRESHOLD) return;
    if (!isDragging) {
        isDragging = true;
        dragRow.classList.add("dragging");
        document.body.classList.add("cards-dragging");
    }
    // Drop-target candidates are constrained to the drag scope. In
    // month: every UL inside this day-card. In classes: the source UL
    // only (single-list scope). In today: every class-block UL in
    // listView.
    let lists;
    if (dragScope.kind === "classes") {
        lists = [dragScope.el];
    } else {
        lists = Array.from(dragScope.el.querySelectorAll("ul.todo-list"));
    }
    if (lists.length === 0) return;
    let bestList = null;
    let bestDist = Infinity;
    for (const list of lists) {
        const r = list.getBoundingClientRect();
        const dyOut = e.clientY < r.top ? r.top - e.clientY
                    : (e.clientY > r.bottom ? e.clientY - r.bottom : 0);
        if (dyOut < bestDist) { bestDist = dyOut; bestList = list; }
    }
    if (!bestList) return;
    // Find the row to insert before, by cursor's vertical position.
    const rows = Array.from(bestList.querySelectorAll(".todo-row:not(.dragging)"));
    let insertBefore = null;
    for (const target of rows) {
        const rect = target.getBoundingClientRect();
        if (e.clientY < rect.top + rect.height / 2) {
            insertBefore = target;
            break;
        }
    }
    if (insertBefore) bestList.insertBefore(dragRow, insertBefore);
    else bestList.appendChild(dragRow);
    e.preventDefault();
});

document.addEventListener("pointerup", async () => {
    if (!dragRow) return;
    const droppedRow = dragRow;
    const wasDragging = isDragging;
    const sourceClassId = dragSourceClassId;
    const scope = dragScope;
    // Clear state synchronously so a stray pointermove between drop and
    // the await below can't re-trigger reordering on a row the user
    // already let go of.
    dragRow = null;
    dragSourceClassId = null;
    dragScope = null;
    pointerStart = null;
    isDragging = false;
    droppedRow.classList.remove("dragging");
    document.body.classList.remove("cards-dragging");
    if (!wasDragging) return;
    // Mark this row 'just-dragged' so the row-body click handler that
    // fires on the same pointer gesture doesn't open the editor.
    droppedRow.classList.add("just-dragged");
    await persistDragDrop(droppedRow, sourceClassId, scope);
});

async function persistDragDrop(row, sourceClassId, scope) {
    // Step 1: cross-class move (tasks only — events stay tied to their
    // class). Allowed in today + month (same day, different class-block);
    // skipped in classes drill-down where there's only one class anyway.
    if (scope.kind !== "classes" && row.dataset.kind === "task") {
        const newClassId = classIdOfList(row.parentNode);
        if (newClassId !== sourceClassId) {
            const fd = new FormData();
            fd.append("class_id", newClassId === "0" ? "" : newClassId);
            try {
                await api.editTask(row.dataset.id, fd);
                row.dataset.classId = newClassId;
            } catch (err) {
                console.error("cross-class move failed:", err);
            }
        }
    }
    // Step 2: persist order via the right endpoint for the scope.
    if (scope.kind === "month") {
        // Per-day position override — only the day's rows go in the
        // payload. Day date comes from the .month-day-card data
        // attribute; without it we can't address the right day.
        const day = scope.el.dataset.dayDate;
        if (!day) return;
        const items = Array.from(scope.el.querySelectorAll(".todo-row"))
            .map((el) => ({
                kind: el.dataset.kind,
                id: parseInt(el.dataset.id, 10),
            }))
            .filter((it) => (it.kind === "task" || it.kind === "event") && !Number.isNaN(it.id));
        try {
            await api.reorderTasksDay(day, items);
        } catch (err) {
            console.error("reorder-day failed:", err);
        }
        return;
    }
    // today + classes: global Task/Event.position via /tasks/reorder.
    // Items come from the scope element so classes drill-down only
    // includes its class's rows, not tasks from other classes.
    const items = Array.from(scope.el.querySelectorAll(".todo-row"))
        .map((el) => ({
            kind: el.dataset.kind,
            id: parseInt(el.dataset.id, 10),
        }))
        .filter((it) => (it.kind === "task" || it.kind === "event") && !Number.isNaN(it.id));
    if (items.length === 0) return;
    try {
        await api.reorderTasks(items);
    } catch (err) {
        console.error("reorder failed:", err);
    }
}

// ---- Class-block drag (Today view only) ---------------------------------
// Drag the class header to reorder entire class blocks. Persists via
// /classes/reorder, which writes User.class_order_json — the same key
// the home/today/week views all read for display order. In Week view
// the affordance is hidden + the gesture skipped because reordering
// classes per-day isn't a thing the server supports.
let dragBlock = null;
let blockPointerStart = null;
let isDraggingBlock = false;

listView.addEventListener("pointerdown", (e) => {
    if (currentView !== "today") return;
    const handle = e.target.closest(".class-block-drag");
    if (!handle) return;
    const block = handle.closest(".class-block");
    if (!block) return;
    dragBlock = block;
    blockPointerStart = { x: e.clientX, y: e.clientY };
    isDraggingBlock = false;
    e.preventDefault();
});

document.addEventListener("pointermove", (e) => {
    if (!dragBlock || !blockPointerStart) return;
    const dy = e.clientY - blockPointerStart.y;
    if (!isDraggingBlock && Math.abs(dy) < DRAG_THRESHOLD) return;
    if (!isDraggingBlock) {
        isDraggingBlock = true;
        dragBlock.classList.add("dragging");
        document.body.classList.add("cards-dragging");
    }
    // Find which other class-block to slot before, by cursor's y.
    const blocks = Array.from(
        listView.querySelectorAll(".class-block:not(.dragging)")
    );
    let insertBefore = null;
    for (const target of blocks) {
        const rect = target.getBoundingClientRect();
        if (e.clientY < rect.top + rect.height / 2) {
            insertBefore = target;
            break;
        }
    }
    const parent = dragBlock.parentNode;
    if (insertBefore) parent.insertBefore(dragBlock, insertBefore);
    else parent.appendChild(dragBlock);
    e.preventDefault();
});

document.addEventListener("pointerup", async () => {
    if (!dragBlock) return;
    const wasDragging = isDraggingBlock;
    const droppedBlock = dragBlock;
    dragBlock = null;
    blockPointerStart = null;
    isDraggingBlock = false;
    droppedBlock.classList.remove("dragging");
    document.body.classList.remove("cards-dragging");
    if (!wasDragging) return;
    // Collect new order — bucket keys (class_id, "0" = Personal).
    const order = Array.from(
        droppedBlock.parentNode.querySelectorAll(":scope > .class-block")
    )
        .map((b) => b.dataset.classId)
        .filter((k) => k != null);
    try {
        await api.reorderClasses(order);
    } catch (err) {
        console.error("class reorder failed:", err);
    }
});

// ---- Add-task view (FAB) -----------------------------------------------
// Opens via the floating + button bottom-right. Same view-swap pattern
// as #editor: hides #content while the form is up, restores on cancel
// or save. Full website-parity field set; smart defaults pre-fill
// starts_at + due_at on open so the form is submittable immediately.
const addForm = $("#add-task-form");
const addView = $("#add-task-view");
const addFab = $("#add-task-fab");

function setAddStatus(text, kind) {
    const el = $("#add-task-status");
    if (!text) { el.hidden = true; return; }
    el.textContent = text;
    el.className = "status " + (kind || "");
    el.hidden = false;
}

// Smart default datetimes — port of static/todo.js:_smartDefaultStart /
// _smartDefaultDue / _formatLocal so the extension's add-task surface
// produces the same starts/due values the website's modal does.
function _formatLocal(d) {
    const pad = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function _smartDefaultStart() {
    // Round CURRENT time UP to the next 30-min mark so events default to
    // a slot that's actually in the future. Mirrors Apple Calendar's
    // "next half hour" default.
    const d = new Date();
    d.setSeconds(0, 0);
    const m = d.getMinutes();
    if (m === 0 || m === 30) {
        d.setMinutes(m + 30);
    } else if (m < 30) {
        d.setMinutes(30);
    } else {
        d.setMinutes(0);
        d.setHours(d.getHours() + 1);
    }
    return _formatLocal(d);
}

function _smartDefaultDue(startStr) {
    // Apple-style: due defaults to one hour after start.
    const d = new Date(startStr);
    d.setHours(d.getHours() + 1);
    return _formatLocal(d);
}

function populateAddTaskDefaults() {
    // Only fill empties — if the user typed something then cancelled, we
    // shouldn't blow it away the next time they reopen the form.
    if (!addForm.starts_at.value && !addForm.is_all_day.checked) {
        addForm.starts_at.value = _smartDefaultStart();
    }
    if (!addForm.due_at.value && !addForm.is_all_day.checked) {
        const startBase = addForm.starts_at.value || _smartDefaultStart();
        addForm.due_at.value = _smartDefaultDue(startBase);
    }
}

function alertLabel(minutes) {
    const m = parseInt(minutes, 10);
    if (m === 0) return "At time";
    if (m < 60) return `${m} min before`;
    if (m < 1440) return `${(m / 60).toFixed(0)}h before`;
    if (m < 10080) return `${(m / 1440).toFixed(0)}d before`;
    return `${(m / 10080).toFixed(0)}w before`;
}

// Alerts are tracked as an array of integer minutes-before. The hidden
// `alerts` input carries the comma-separated value at submit time.
let addAlerts = [];

function renderAddAlertsChips() {
    const chips = $("#add-alerts-chips");
    const hidden = $("#add-alerts-value");
    chips.innerHTML = "";
    const cleaned = [...new Set(addAlerts.map((n) => parseInt(n, 10)))]
        .filter((n) => !Number.isNaN(n))
        .sort((a, b) => b - a);
    addAlerts = cleaned;
    hidden.value = cleaned.join(",");
    cleaned.forEach((m) => {
        const chip = document.createElement("span");
        chip.className = "alert-chip";
        chip.dataset.minutes = String(m);
        const label = document.createElement("span");
        label.textContent = alertLabel(m);
        const rm = document.createElement("button");
        rm.type = "button";
        rm.className = "alert-chip-remove";
        rm.setAttribute("aria-label", `Remove ${label.textContent}`);
        rm.textContent = "×";
        rm.addEventListener("click", () => {
            addAlerts = addAlerts.filter((x) => x !== m);
            renderAddAlertsChips();
        });
        chip.appendChild(label);
        chip.appendChild(rm);
        chips.appendChild(chip);
    });
}

const addAlertsAdd = $("#add-alerts-add");
addAlertsAdd.addEventListener("change", () => {
    const v = addAlertsAdd.value;
    if (!v) return;
    addAlerts.push(parseInt(v, 10));
    renderAddAlertsChips();
    addAlertsAdd.value = "";
});

// Pending attachments — files the user picked before the task exists yet.
// Buffered here, then POSTed to /tasks/{id}/attachments after the create
// call returns the new id.
let addPendingFiles = [];

function renderAddAttachmentsList() {
    const ul = $("#add-attachments-list");
    ul.innerHTML = "";
    addPendingFiles.forEach((f, i) => {
        const li = document.createElement("li");
        li.className = "attachment-row";
        const name = document.createElement("span");
        name.textContent = f.name;
        const rm = document.createElement("button");
        rm.type = "button";
        rm.className = "alert-chip-remove";
        rm.setAttribute("aria-label", `Remove ${f.name}`);
        rm.textContent = "×";
        rm.addEventListener("click", () => {
            addPendingFiles.splice(i, 1);
            renderAddAttachmentsList();
        });
        li.appendChild(name);
        li.appendChild(rm);
        ul.appendChild(li);
    });
}

const addAttachmentsInput = $("#add-attachments-input");
addAttachmentsInput.addEventListener("change", (e) => {
    for (const f of e.target.files) addPendingFiles.push(f);
    addAttachmentsInput.value = ""; // allow re-picking the same file
    renderAddAttachmentsList();
});

// Repeat dropdown reveals the End-date input. Both rrule + all-day disable
// starts_at (mutually exclusive with a range — see CLAUDE.md).
function syncAddRruleVisibility() {
    const showing = !!addForm.rrule.value;
    $("#add-until-label").hidden = !showing;
    if (!showing) addForm.rrule_until.value = "";
    syncAddStartsDisabled();
}

function syncAddAllDay() {
    const on = addForm.is_all_day.checked;
    const due = addForm.due_at;
    const starts = addForm.starts_at;
    if (on) {
        if (due.value && due.type === "datetime-local") due.value = due.value.slice(0, 10);
        due.type = "date";
        if (starts) {
            starts.value = "";
            starts.type = "date";
        }
    } else {
        if (due.value && due.type === "date") due.value = due.value + "T17:00";
        due.type = "datetime-local";
        if (starts) {
            if (starts.value && starts.type === "date") starts.value = starts.value + "T09:00";
            starts.type = "datetime-local";
        }
    }
    syncAddStartsDisabled();
}

function syncAddStartsDisabled() {
    const allDay = addForm.is_all_day.checked;
    const hasRrule = !!addForm.rrule.value;
    const disabled = allDay || hasRrule;
    const starts = addForm.starts_at;
    const label = $("#add-starts-label");
    if (starts) {
        starts.disabled = disabled;
        if (disabled) starts.value = "";
    }
    if (label) label.classList.toggle("disabled", disabled);
}

addForm.is_all_day.addEventListener("change", syncAddAllDay);
addForm.rrule.addEventListener("change", syncAddRruleVisibility);

// View-swap: open the add-task surface in place of the list. Hides the
// FAB itself so the + glyph isn't competing with the form. The list
// view's tab choice is preserved — cancel/save returns the user to
// whichever tab (Today / Month) they came from.
function showAddTask() {
    listView.hidden = true;
    if (editorView) editorView.hidden = true;
    if (classDetailView) classDetailView.hidden = true;
    addView.hidden = false;
    if (addFab) addFab.hidden = true;
    populateAddTaskDefaults();
    setAddStatus("", "");
    const t = addForm.querySelector("input[name='title']");
    if (t) { t.focus(); t.select(); }
}

function hideAddTask() {
    addView.hidden = true;
    listView.hidden = false;
    if (addFab) addFab.hidden = false;
}

if (addFab) addFab.addEventListener("click", showAddTask);
$("#add-task-back").addEventListener("click", hideAddTask);
$("#add-task-cancel").addEventListener("click", hideAddTask);

addForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const title = (addForm.title.value || "").trim();
    if (!title) return;
    const due = addForm.due_at.value;
    const starts = addForm.starts_at.value;
    if (starts && due && starts > due) {
        setAddStatus("Cannot save, the start date must be before the end date", "error");
        return;
    }
    const fd = new FormData();
    fd.append("title", title);
    if (due) fd.append("due_at", due);
    if (starts) fd.append("starts_at", starts);
    if (addForm.is_all_day.checked) fd.append("is_all_day", "1");
    if (addForm.rrule.value) fd.append("rrule", addForm.rrule.value);
    if (addForm.rrule_until.value) fd.append("rrule_until", addForm.rrule_until.value);
    if (addForm.tag_id.value && addForm.tag_id.value !== "__new__") {
        fd.append("tag_id", addForm.tag_id.value);
    } else if (addForm.tag_id.value === "__new__") {
        setAddStatus("Pick a tag (or finish creating the new one).", "error");
        return;
    }
    // Alerts: send the field even when empty so the server treats it as
    // "user explicitly chose no reminders" instead of falling back to
    // smart defaults. Matches `_create_task_for_user`'s alerts handling.
    fd.append("alerts", $("#add-alerts-value").value || "");
    if (addForm.notes.value.trim()) fd.append("notes", addForm.notes.value);

    const classId = addForm.class_id.value;
    setAddStatus("Adding…", "pending");
    try {
        const created = classId
            ? await api.addClassTask(classId, fd)
            : await api.addPersonalTask(fd);
        // POST any pending attachments now that we have the task id.
        // Best-effort — a failed upload doesn't roll back the task.
        if (created && created.id && addPendingFiles.length) {
            for (const f of addPendingFiles) {
                try { await api.addAttachment(created.id, f); }
                catch (err) { console.error("attachment upload failed:", err); }
            }
        }
        // Clear the form so the next FAB tap starts fresh, except for the
        // class selection (most rapid-fire adds stay in the same class).
        const stickyClass = addForm.class_id.value;
        addForm.reset();
        addForm.class_id.value = stickyClass;
        addAlerts = [];
        addPendingFiles = [];
        renderAddAlertsChips();
        renderAddAttachmentsList();
        syncAddRruleVisibility();
        syncAddAllDay();
        await load();
        hideAddTask();
    } catch (err) {
        if (err instanceof NotAuthenticated) {
            showLogin();
            hideAddTask();
            return;
        }
        setAddStatus("Couldn't add: " + err.message, "error");
    }
});

// ---- View management ----
// Two views share #content: 'today' (today + overdue, class-bucketed)
// and 'month' (full calendar month as day-cards, /month.json). The tab
// buttons swap which fetcher + renderer runs. `currentView` is the
// source of truth so load() always renders the correct shape.
// `currentMonth` is the YYYY-MM the user is paging through; null means
// the server's "current month" default.
let currentView = "today";
let currentMonth = null;

// Hide every sub-view (editor / add-task / add-class / event editor /
// class detail / settings / syllabus upload) and show the list +
// floating + button. Tab clicks call this so a half-open sub-view
// doesn't trap the user. Cancel buttons still work — they're just an
// alternate route back to the same place.
function returnToList() {
    listView.hidden = false;
    if (typeof editorView !== "undefined" && editorView) editorView.hidden = true;
    if (typeof classDetailView !== "undefined" && classDetailView) classDetailView.hidden = true;
    if (typeof addView !== "undefined" && addView) addView.hidden = true;
    if (typeof addClassView !== "undefined" && addClassView) addClassView.hidden = true;
    if (typeof eventEditorView !== "undefined" && eventEditorView) eventEditorView.hidden = true;
    if (typeof settingsView !== "undefined" && settingsView) settingsView.hidden = true;
    if (typeof syllabusView !== "undefined" && syllabusView) syllabusView.hidden = true;
    // Stop any in-flight syllabus poll if the user navigates away mid-parse.
    if (typeof syllabusPollTimer !== "undefined" && syllabusPollTimer) {
        clearTimeout(syllabusPollTimer);
        syllabusPollTimer = null;
    }
    // Clear PDF iframe to stop streaming if the class-detail surface was up.
    const pdf = document.getElementById("class-detail-pdf");
    if (pdf) pdf.src = "";
    const fab = document.getElementById("add-task-fab");
    if (fab) fab.hidden = false;
    currentClassId = null;
}

function setView(view) {
    if (view !== "today" && view !== "month" && view !== "classes") return;
    currentView = view;
    document.querySelectorAll(".view-tab").forEach((btn) => {
        btn.classList.toggle("active", btn.dataset.view === view);
    });
    returnToList();
    load();
}

document.querySelectorAll(".view-tab").forEach((btn) => {
    btn.addEventListener("click", () => setView(btn.dataset.view));
});

// ---- Month-day-card renderer (month view) ----
// Each day in the requested month gets its own card — header (weekday +
// date), then the same class-bucketed list shape Today uses. Empty days
// still render so the month feels complete; the user always sees the
// scaffolding even when nothing's scheduled.
function renderMonthDay(day) {
    const card = document.createElement("li");
    card.className = "month-day-card" + (day.is_today ? " is-today" : "");
    // Drag-to-reorder reads this to scope the per-day reorder POST.
    card.dataset.dayDate = day.date;
    const head = document.createElement("header");
    head.className = "month-day-head";
    const d = new Date(day.date + "T00:00:00");
    const dow = document.createElement("span");
    dow.className = "month-day-dow";
    dow.textContent = d.toLocaleDateString(undefined, { weekday: "short" });
    const num = document.createElement("span");
    num.className = "month-day-num";
    num.textContent = String(d.getDate());
    head.appendChild(dow);
    head.appendChild(num);
    if (day.is_today) {
        const tag = document.createElement("span");
        tag.className = "month-day-today";
        tag.textContent = "today";
        head.appendChild(tag);
    }
    card.appendChild(head);
    if (!day.buckets || day.buckets.length === 0) {
        const empty = document.createElement("p");
        empty.className = "muted month-day-empty";
        empty.textContent = "No tasks";
        card.appendChild(empty);
        return card;
    }
    day.buckets.forEach((b) => card.appendChild(renderBucket(b)));
    return card;
}

// Month-nav header: ‹ Month YYYY › lives at the top of the list. Built
// fresh on each render so prev/next handlers always reference the
// latest data.
function renderMonthNav(data) {
    const nav = document.createElement("div");
    nav.className = "month-nav";
    const prev = document.createElement("button");
    prev.type = "button";
    prev.className = "muted-btn month-nav-btn";
    prev.setAttribute("aria-label", "Previous month");
    prev.textContent = "‹";
    prev.addEventListener("click", () => {
        currentMonth = data.prev_month;
        load();
    });
    const label = document.createElement("span");
    label.className = "month-nav-label";
    label.textContent = data.label;
    const next = document.createElement("button");
    next.type = "button";
    next.className = "muted-btn month-nav-btn";
    next.setAttribute("aria-label", "Next month");
    next.textContent = "›";
    next.addEventListener("click", () => {
        currentMonth = data.next_month;
        load();
    });
    nav.appendChild(prev);
    nav.appendChild(label);
    nav.appendChild(next);
    return nav;
}

// ---- Load ----
// ---- Classes list renderer ---------------------------------------------
// Vertical list of class cards. Each card → showClassDetail(c.id) which
// already exists for the today-list class-header tap path. Empty state
// punches through to the website where users add classes today.
function renderClassesList(target, classes) {
    target.innerHTML = "";
    // "+ Add class" / "+ Upload syllabus" buttons sit above the list. They
    // appear even on the empty state so users can take action right away.
    const actions = document.createElement("div");
    actions.className = "classes-actions";
    const addBtn = document.createElement("button");
    addBtn.type = "button";
    addBtn.textContent = "+ Add class";
    addBtn.addEventListener("click", showAddClass);
    const uploadBtn = document.createElement("button");
    uploadBtn.type = "button";
    uploadBtn.textContent = "+ Upload syllabus";
    if (cachedMe && !cachedMe.xai_api_key_set) {
        uploadBtn.classList.add("is-disabled");
        uploadBtn.title = "Set your xAI API key in Settings first";
    }
    uploadBtn.addEventListener("click", () => {
        if (cachedMe && !cachedMe.xai_api_key_set) {
            showSettings();
            setXaiStatus("Set your xAI API key first to parse syllabi.", "error");
            return;
        }
        showSyllabusUpload();
    });
    actions.appendChild(addBtn);
    actions.appendChild(uploadBtn);
    target.appendChild(actions);

    if (!classes || classes.length === 0) {
        const p = document.createElement("p");
        p.className = "muted empty";
        p.textContent = "No classes yet. Tap + Add class above to start.";
        target.appendChild(p);
        return;
    }
    const ul = document.createElement("ul");
    ul.className = "classes-list";
    classes.forEach((c) => {
        const li = document.createElement("li");
        li.className = "class-card";
        const btn = document.createElement("button");
        btn.type = "button";
        btn.setAttribute("aria-label", `Open ${c.code}`);
        const code = document.createElement("span");
        code.className = "class-card-code";
        code.textContent = c.code;
        btn.appendChild(code);
        if (c.name) {
            const name = document.createElement("span");
            name.className = "class-card-name";
            name.textContent = c.name;
            btn.appendChild(name);
        }
        btn.addEventListener("click", () => showClassDetail(c.id));
        li.appendChild(btn);
        ul.appendChild(li);
    });
    target.appendChild(ul);
}

async function load() {
    const target = $("#content");
    target.innerHTML = '<p class="muted loading">Loading…</p>';
    try {
        if (currentView === "classes") {
            const classes = await api.classes();
            $("#today-date").textContent = "Classes";
            renderClassesList(target, classes);
            return;
        }
        if (currentView === "month") {
            const data = await api.month(currentMonth);
            // Server may have normalized currentMonth (e.g., null → "2026-05").
            // Cache it so prev/next nav stays anchored even after a refresh.
            currentMonth = data.month;
            $("#today-date").textContent = data.label;
            target.innerHTML = "";
            target.appendChild(renderMonthNav(data));
            const list = document.createElement("ol");
            list.className = "month-day-list";
            data.days.forEach((d) => list.appendChild(renderMonthDay(d)));
            target.appendChild(list);
            return;
        }
        const data = await api.today();
        $("#today-date").textContent = formatDate(data.today);
        if (!data.buckets || data.buckets.length === 0) {
            renderEmpty(target);
            return;
        }
        target.innerHTML = "";
        data.buckets.forEach((b) => target.appendChild(renderBucket(b)));
    } catch (err) {
        if (err instanceof NotAuthenticated) {
            showLogin();
        } else {
            renderError(target, "Couldn't load: " + err.message);
        }
    }
}

function formatDate(iso) {
    // iso is "YYYY-MM-DD"; render as "Today · Fri May 08".
    const d = new Date(iso + "T00:00:00");
    const opts = { weekday: "short", month: "short", day: "2-digit" };
    return "Today · " + d.toLocaleDateString(undefined, opts);
}

// ---- Inline login -------------------------------------------------------
// Posts directly to /login with the existing session cookie semantics.
// The Compass server returns 303 on success (we set redirect: 'manual'
// so the response surfaces as opaqueredirect, which we treat as success)
// or 401 on bad credentials. After a successful POST, /me.json confirms
// the session and we swap into the app.
const loginForm = $("#login-form");

function setLoginStatus(text, kind) {
    const el = $("#login-status");
    if (!text) { el.hidden = true; return; }
    el.textContent = text;
    el.className = "status " + (kind || "");
    el.hidden = false;
}

loginForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const email = (loginForm.email.value || "").trim();
    const password = loginForm.password.value || "";
    if (!email || !password) return;
    setLoginStatus("Signing in…", "pending");
    try {
        const base = await api.base();
        const fd = new FormData();
        fd.append("email", email);
        fd.append("password", password);
        const r = await fetch(base + "/login", {
            method: "POST",
            body: fd,
            credentials: "include",
            redirect: "manual",
        });
        // 401 → bad credentials. opaqueredirect (status 0) or any 2xx → success.
        if (r.status === 401) {
            setLoginStatus("Wrong email or password.", "error");
            return;
        }
        // Verify the cookie actually landed by hitting /me.json.
        const me = await api.me().catch(() => null);
        if (!me) {
            setLoginStatus("Couldn't sign in. Double-check the server URL.", "error");
            return;
        }
        cachedMe = me;
        setLoginStatus("", "");
        loginForm.reset();
        showApp();
        await ensureEditorLists();
        await load();
    } catch (err) {
        setLoginStatus("Couldn't sign in: " + err.message, "error");
    }
});

$("#login-open-options").addEventListener("click", (e) => {
    e.preventDefault();
    chrome.runtime.openOptionsPage();
});
$("#login-open-signup").addEventListener("click", (e) => {
    e.preventDefault();
    showSignup();
});

// ---- Signup ----------------------------------------------------------
// POSTs /signup with Accept: application/json so the server returns
// {id, email} on success or {error: "..."} on validation failure. After
// success, the session cookie is already set — we just verify via
// /me.json and fall through into the app.
const signupForm = $("#signup-form");

function setSignupStatus(text, kind) {
    const el = $("#signup-status");
    if (!text) { el.hidden = true; return; }
    el.textContent = text;
    el.className = "status " + (kind || "");
    el.hidden = false;
}

signupForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const email = (signupForm.email.value || "").trim();
    const pw = signupForm.password.value || "";
    const confirm = signupForm.password_confirm.value || "";
    if (!email || !pw) return;
    if (pw !== confirm) {
        setSignupStatus("Passwords don't match.", "error");
        return;
    }
    setSignupStatus("Creating account…", "pending");
    try {
        await api.signup(email, pw);
        const me = await api.me().catch(() => null);
        if (!me) {
            setSignupStatus("Account created but couldn't sign in. Try logging in.", "error");
            return;
        }
        cachedMe = me;
        setSignupStatus("", "");
        signupForm.reset();
        showApp();
        await ensureEditorLists();
        await load();
    } catch (err) {
        setSignupStatus(err.message || "Couldn't sign up.", "error");
    }
});

$("#signup-back-to-login").addEventListener("click", (e) => {
    e.preventDefault();
    showLogin();
});

// ---- Add-class surface --------------------------------------------------
// Opens via "+ Add class" on the Classes tab. View-swap pattern.
const addClassView = $("#add-class-view");
const addClassForm = $("#add-class-form");

function setAddClassStatus(text, kind) {
    const el = $("#add-class-status");
    if (!text) { el.hidden = true; return; }
    el.textContent = text;
    el.className = "status " + (kind || "");
    el.hidden = false;
}

function showAddClass() {
    listView.hidden = true;
    if (editorView) editorView.hidden = true;
    if (classDetailView) classDetailView.hidden = true;
    addClassView.hidden = false;
    const fab = document.getElementById("add-task-fab");
    if (fab) fab.hidden = true;
    setAddClassStatus("", "");
    addClassForm.reset();
    addClassForm.code.focus();
}

function hideAddClass() {
    addClassView.hidden = true;
    listView.hidden = false;
    const fab = document.getElementById("add-task-fab");
    if (fab) fab.hidden = false;
}

addClassForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const code = (addClassForm.code.value || "").trim();
    const name = (addClassForm.name.value || "").trim();
    if (!code || !name) return;
    setAddClassStatus("Adding…", "pending");
    try {
        await api.createClass({ code, name });
        // Bust the classes-promise cache so the dropdowns + Classes tab
        // re-fetch and pick up the new class.
        classesPromise = null;
        addClassForm.reset();
        setAddClassStatus("", "");
        hideAddClass();
        // Re-render whatever view we came from. If the user is on Classes,
        // load() refreshes the list. Otherwise just refresh dropdowns.
        await ensureEditorLists();
        await load();
    } catch (err) {
        if (err instanceof NotAuthenticated) {
            showLogin();
            hideAddClass();
            return;
        }
        setAddClassStatus("Couldn't add: " + err.message, "error");
    }
});

$("#add-class-back").addEventListener("click", hideAddClass);
$("#add-class-cancel").addEventListener("click", hideAddClass);

// "Delete class" button at the bottom of #class-detail.
$("#class-detail-delete").addEventListener("click", async () => {
    if (!currentClassId) return;
    const code = $("#class-detail-code").textContent || "this class";
    if (!confirm(`Delete ${code} and everything in it?`)) return;
    try {
        await api.deleteClass(currentClassId);
        classesPromise = null;
        hideClassDetail();
        await ensureEditorLists();
        await load();
    } catch (err) {
        if (err instanceof NotAuthenticated) {
            showLogin();
            hideClassDetail();
            return;
        }
        alert("Couldn't delete class: " + err.message);
    }
});

// ---- Event editor surface -----------------------------------------------
// Click an event row's body → open the editor. Mirrors showEditor for
// tasks but talks to /events/{id}/edit + /events/{id}/clone.
const eventEditorView = $("#event-editor-view");
const eventEditForm = $("#event-edit-form");
let eventEditReturnClass = null; // class id to drill back into on save

function setEventEditStatus(text, kind) {
    const el = $("#event-edit-status");
    if (!text) { el.hidden = true; return; }
    el.textContent = text;
    el.className = "status " + (kind || "");
    el.hidden = false;
}

function showEventEditor(rowEl) {
    listView.hidden = true;
    if (editorView) editorView.hidden = true;
    if (classDetailView) classDetailView.hidden = true;
    eventEditorView.hidden = false;
    const fab = document.getElementById("add-task-fab");
    if (fab) fab.hidden = true;
    eventEditForm.event_id.value = rowEl.dataset.id;
    eventEditForm.class_id.value = rowEl.dataset.classId || "";
    eventEditReturnClass = rowEl.dataset.classId
        ? parseInt(rowEl.dataset.classId, 10)
        : null;
    eventEditForm.title.value = rowEl.dataset.title || "";
    eventEditForm.kind.value = rowEl.dataset.kind === "event"
        ? (rowEl.dataset.subKind || "milestone")
        : "milestone";
    // Server stores starts_at on events as the event's "due_at" in row
    // dataset (renderRow normalizes both into data-due-at). Same wall-
    // clock-prefix slice as the task editor.
    eventEditForm.starts_at.value = (rowEl.dataset.dueAt || "").slice(0, 16);
    eventEditForm.ends_at.value = "";  // not surfaced on rows; user re-enters if known
    setEventEditStatus("", "");
    eventEditForm.title.focus();
    eventEditForm.title.select();
}

function hideEventEditor() {
    eventEditorView.hidden = true;
    // If we came from class-detail, re-open it; otherwise back to list.
    if (eventEditReturnClass) {
        showClassDetail(eventEditReturnClass);
    } else {
        listView.hidden = false;
        const fab = document.getElementById("add-task-fab");
        if (fab) fab.hidden = false;
    }
}

eventEditForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const id = eventEditForm.event_id.value;
    if (!id) return;
    const fd = new FormData();
    fd.append("title", eventEditForm.title.value || "");
    fd.append("kind", eventEditForm.kind.value || "milestone");
    if (eventEditForm.starts_at.value) fd.append("starts_at", eventEditForm.starts_at.value);
    if (eventEditForm.ends_at.value) fd.append("ends_at", eventEditForm.ends_at.value);
    setEventEditStatus("Saving…", "pending");
    try {
        await api.editEvent(id, fd);
        hideEventEditor();
    } catch (err) {
        if (err instanceof NotAuthenticated) {
            showLogin();
            return;
        }
        setEventEditStatus("Couldn't save: " + err.message, "error");
    }
});

$("#event-clone-btn").addEventListener("click", async () => {
    const id = eventEditForm.event_id.value;
    if (!id) return;
    if (!confirm("Duplicate this event?")) return;
    setEventEditStatus("Duplicating…", "pending");
    try {
        await api.cloneEvent(id);
        hideEventEditor();
    } catch (err) {
        if (err instanceof NotAuthenticated) {
            showLogin();
            return;
        }
        setEventEditStatus("Couldn't duplicate: " + err.message, "error");
    }
});

$("#event-editor-back").addEventListener("click", hideEventEditor);
$("#event-editor-cancel").addEventListener("click", hideEventEditor);

// ---- Settings surface ---------------------------------------------------
// Account / Timezone / xAI key / Calendar / Manage tags. Footer ⚙ button
// opens this. Logout lives inside the Account section.
const settingsView = $("#settings-view");

function showSettings() {
    listView.hidden = true;
    if (editorView) editorView.hidden = true;
    if (classDetailView) classDetailView.hidden = true;
    if (addView) addView.hidden = true;
    if (addClassView) addClassView.hidden = true;
    if (eventEditorView) eventEditorView.hidden = true;
    settingsView.hidden = false;
    const fab = document.getElementById("add-task-fab");
    if (fab) fab.hidden = true;
    populateSettings();
}

function hideSettings() {
    settingsView.hidden = true;
    listView.hidden = false;
    const fab = document.getElementById("add-task-fab");
    if (fab) fab.hidden = false;
}

function populateSettings() {
    // Source of truth = cachedMe; refreshed in boot() and after any
    // settings mutation that changes its fields.
    if (!cachedMe) return;
    $("#settings-email").textContent = cachedMe.email || "";
    $("#settings-tz").textContent = cachedMe.timezone || "—";
    const xaiSet = $("#settings-xai-status");
    if (cachedMe.xai_api_key_set) {
        xaiSet.textContent = "Key set: " + (cachedMe.xai_api_key_masked || "");
    } else {
        xaiSet.textContent = "No key set. Syllabus upload requires one.";
    }
    const urls = cachedMe.calendar_urls || {};
    const webcal = $("#settings-cal-webcal");
    webcal.href = urls.webcal_url || "#";
    $("#settings-cal-url").textContent = urls.https_url || "";
    populateManageTags();
}

$("#settings-back").addEventListener("click", hideSettings);
$("#open-settings").addEventListener("click", showSettings);

$("#settings-logout").addEventListener("click", async () => {
    try {
        await api.logout();
    } catch (_) { /* even on failure, clear local state */ }
    cachedMe = null;
    classesPromise = null;
    tagsPromise = null;
    hideSettings();
    showLogin();
});

const xaiForm = $("#settings-xai-form");
function setXaiStatus(text, kind) {
    const el = $("#settings-xai-status-line");
    if (!text) { el.hidden = true; return; }
    el.textContent = text;
    el.className = "status " + (kind || "");
    el.hidden = false;
}
xaiForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const key = (xaiForm.xai_api_key.value || "").trim();
    setXaiStatus("Saving…", "pending");
    try {
        const r = await api.saveXaiKey(key);
        if (cachedMe) {
            cachedMe.xai_api_key_set = !!(r && r.xai_api_key_set);
            cachedMe.xai_api_key_masked = r && r.xai_api_key_masked;
        }
        xaiForm.reset();
        setXaiStatus("Saved ✓", "success");
        setTimeout(() => setXaiStatus("", ""), 800);
        populateSettings();
    } catch (err) {
        if (err instanceof NotAuthenticated) { showLogin(); return; }
        setXaiStatus(err.message || "Couldn't save.", "error");
    }
});
$("#settings-xai-clear").addEventListener("click", async () => {
    setXaiStatus("Clearing…", "pending");
    try {
        const r = await api.saveXaiKey("");
        if (cachedMe) {
            cachedMe.xai_api_key_set = !!(r && r.xai_api_key_set);
            cachedMe.xai_api_key_masked = null;
        }
        xaiForm.reset();
        setXaiStatus("Cleared", "success");
        setTimeout(() => setXaiStatus("", ""), 800);
        populateSettings();
    } catch (err) {
        if (err instanceof NotAuthenticated) { showLogin(); return; }
        setXaiStatus(err.message || "Couldn't clear.", "error");
    }
});

$("#settings-cal-regen").addEventListener("click", async () => {
    if (!confirm("Regenerate your calendar token? Existing subscriptions will stop working until you re-subscribe with the new URL.")) return;
    try {
        const r = await api.regenerateCalendarToken();
        if (cachedMe) {
            cachedMe.calendar_token = r.calendar_token;
            cachedMe.calendar_urls = r.calendar_urls;
        }
        populateSettings();
    } catch (err) {
        if (err instanceof NotAuthenticated) { showLogin(); return; }
        alert("Couldn't regenerate: " + err.message);
    }
});

// ---- Manage tags --------------------------------------------------------
// List inside Settings: name (rename inline), color swatch (recolor),
// × delete (system tags can rename but not delete; × hidden via
// .is-system class).
async function populateManageTags() {
    const ul = $("#settings-tags-list");
    ul.innerHTML = "";
    let tags;
    try {
        tagsPromise = api.tags();
        tags = await tagsPromise;
    } catch (err) {
        if (err instanceof NotAuthenticated) { showLogin(); return; }
        return;
    }
    tags.forEach((t) => ul.appendChild(renderManageTagRow(t)));
}

function renderManageTagRow(t) {
    const li = document.createElement("li");
    li.dataset.tagId = String(t.id);
    const swatch = document.createElement("input");
    swatch.type = "color";
    swatch.className = "manage-tag-swatch";
    swatch.value = t.color || "#A04528";
    swatch.title = "Click to change color";
    swatch.addEventListener("input", async () => {
        try {
            await api.editTag(t.id, { name: t.name, color: swatch.value });
            t.color = swatch.value;
            tagsPromise = null;  // bust cache so dropdowns refresh
        } catch (err) {
            if (err instanceof NotAuthenticated) { showLogin(); return; }
            alert("Couldn't recolor: " + err.message);
        }
    });
    if (t.is_system) {
        const sys = document.createElement("span");
        sys.className = "manage-tag-system";
        sys.textContent = "sys";
        li.appendChild(sys);
    }
    const name = document.createElement("input");
    name.type = "text";
    name.className = "manage-tag-name";
    name.value = t.name;
    name.addEventListener("blur", async () => {
        const newName = (name.value || "").trim();
        if (!newName || newName === t.name) {
            name.value = t.name;
            return;
        }
        try {
            await api.editTag(t.id, { name: newName, color: swatch.value });
            t.name = newName;
            tagsPromise = null;
        } catch (err) {
            name.value = t.name;
            if (err instanceof NotAuthenticated) { showLogin(); return; }
            alert("Couldn't rename: " + err.message);
        }
    });
    name.addEventListener("keydown", (e) => {
        if (e.key === "Enter") { e.preventDefault(); name.blur(); }
        if (e.key === "Escape") { name.value = t.name; name.blur(); }
    });
    const del = document.createElement("button");
    del.type = "button";
    del.className = "manage-tag-del" + (t.is_system ? " is-system" : "");
    del.setAttribute("aria-label", `Delete ${t.name}`);
    del.textContent = "×";
    del.addEventListener("click", async () => {
        if (t.is_system) return;
        if (!confirm(`Delete tag "${t.name}"? Tasks using it will lose the tag.`)) return;
        try {
            await api.deleteTag(t.id);
            li.remove();
            tagsPromise = null;
        } catch (err) {
            if (err instanceof NotAuthenticated) { showLogin(); return; }
            alert("Couldn't delete: " + err.message);
        }
    });
    li.appendChild(swatch);
    li.appendChild(name);
    li.appendChild(del);
    return li;
}

const newTagForm = $("#settings-new-tag-form");
function setManageTagsStatus(text, kind) {
    const el = $("#settings-tags-status");
    if (!text) { el.hidden = true; return; }
    el.textContent = text;
    el.className = "status " + (kind || "");
    el.hidden = false;
}
newTagForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const name = (newTagForm.name.value || "").trim();
    const color = newTagForm.color.value || "#A04528";
    if (!name) return;
    setManageTagsStatus("Creating…", "pending");
    try {
        await api.createTag({ name, color });
        newTagForm.reset();
        newTagForm.color.value = "#A04528";
        tagsPromise = null;
        setManageTagsStatus("Added ✓", "success");
        setTimeout(() => setManageTagsStatus("", ""), 800);
        populateManageTags();
    } catch (err) {
        if (err instanceof NotAuthenticated) { showLogin(); return; }
        setManageTagsStatus(err.message || "Couldn't add.", "error");
    }
});

// ---- Syllabus upload + parse polling -----------------------------------
const syllabusView = $("#syllabus-upload-view");
const syllabusDrop = $("#syllabus-drop");
const syllabusFileInput = $("#syllabus-file-input");
const syllabusPickedEl = $("#syllabus-picked");
const syllabusUploadBtn = $("#syllabus-upload-btn");
let syllabusFile = null;
let syllabusPollTimer = null;

function showSyllabusUpload() {
    listView.hidden = true;
    if (editorView) editorView.hidden = true;
    if (classDetailView) classDetailView.hidden = true;
    if (settingsView) settingsView.hidden = true;
    syllabusView.hidden = false;
    const fab = document.getElementById("add-task-fab");
    if (fab) fab.hidden = true;
    // Reset state
    syllabusFile = null;
    syllabusFileInput.value = "";
    syllabusPickedEl.hidden = true;
    syllabusPickedEl.textContent = "";
    syllabusUploadBtn.disabled = true;
    setSyllabusStatus("", "");
    $("#syllabus-upload-stage").hidden = false;
    $("#syllabus-parse-stage").hidden = true;
    $("#syllabus-parse-actions").hidden = true;
}

function hideSyllabusUpload() {
    syllabusView.hidden = true;
    listView.hidden = false;
    const fab = document.getElementById("add-task-fab");
    if (fab) fab.hidden = false;
    if (syllabusPollTimer) {
        clearTimeout(syllabusPollTimer);
        syllabusPollTimer = null;
    }
}

function setSyllabusStatus(text, kind) {
    const el = $("#syllabus-upload-status");
    if (!text) { el.hidden = true; return; }
    el.textContent = text;
    el.className = "status " + (kind || "");
    el.hidden = false;
}

function pickSyllabusFile(file) {
    if (!file) return;
    if (!/\.pdf$/i.test(file.name) && file.type !== "application/pdf") {
        setSyllabusStatus("Only PDF files are accepted.", "error");
        return;
    }
    if (file.size > 25 * 1024 * 1024) {
        setSyllabusStatus("File is too big (25 MB max).", "error");
        return;
    }
    syllabusFile = file;
    syllabusPickedEl.textContent = file.name + " · " + Math.round(file.size / 1024) + " KB";
    syllabusPickedEl.hidden = false;
    syllabusUploadBtn.disabled = false;
    setSyllabusStatus("", "");
}

["dragenter", "dragover"].forEach((evName) =>
    syllabusDrop.addEventListener(evName, (e) => {
        e.preventDefault();
        syllabusDrop.classList.add("drop-active");
    })
);
["dragleave", "drop"].forEach((evName) =>
    syllabusDrop.addEventListener(evName, (e) => {
        e.preventDefault();
        syllabusDrop.classList.remove("drop-active");
    })
);
syllabusDrop.addEventListener("drop", (e) => {
    const f = e.dataTransfer.files && e.dataTransfer.files[0];
    if (f) pickSyllabusFile(f);
});
syllabusDrop.addEventListener("click", () => syllabusFileInput.click());
syllabusFileInput.addEventListener("change", (e) => {
    pickSyllabusFile(e.target.files[0]);
});

syllabusUploadBtn.addEventListener("click", async () => {
    if (!syllabusFile) return;
    setSyllabusStatus("Uploading…", "pending");
    try {
        const r = await api.uploadSyllabus(syllabusFile);
        // Server returns { syllabus_id, class_id }. Now poll the JSON
        // status endpoint every 2s until done / error / missing.
        $("#syllabus-upload-stage").hidden = true;
        $("#syllabus-parse-stage").hidden = false;
        $("#syllabus-parse-actions").hidden = true;
        $("#syllabus-parse-status").textContent = "Parsing…";
        pollSyllabusStatus(r.syllabus_id, r.class_id);
    } catch (err) {
        if (err instanceof NotAuthenticated) {
            showLogin();
            hideSyllabusUpload();
            return;
        }
        if ((err.message || "").includes("need_key")) {
            // No xAI key set — punt to settings.
            hideSyllabusUpload();
            showSettings();
            setXaiStatus("Set your xAI API key first to parse syllabi.", "error");
            return;
        }
        setSyllabusStatus(err.message || "Couldn't upload.", "error");
    }
});

function pollSyllabusStatus(syllabusId, classId) {
    api.syllabusStatus(syllabusId).then((s) => {
        const status = s && s.status;
        const statusEl = $("#syllabus-parse-status");
        if (status === "done") {
            statusEl.textContent = "Done ✓";
            classesPromise = null;
            ensureEditorLists().then(() => {
                hideSyllabusUpload();
                showClassDetail(classId);
            });
            return;
        }
        if (status && status.startsWith("error")) {
            statusEl.textContent = status;
            $("#syllabus-parse-actions").hidden = false;
            return;
        }
        if (status === "missing") {
            statusEl.textContent = "Syllabus disappeared. Try again.";
            $("#syllabus-parse-actions").hidden = false;
            return;
        }
        // pending / running / unknown — keep polling.
        statusEl.textContent = "Parsing… (" + (status || "pending") + ")";
        syllabusPollTimer = setTimeout(() => pollSyllabusStatus(syllabusId, classId), 2000);
    }).catch((err) => {
        if (err instanceof NotAuthenticated) {
            showLogin();
            hideSyllabusUpload();
            return;
        }
        $("#syllabus-parse-status").textContent = "Status fetch failed: " + err.message;
        $("#syllabus-parse-actions").hidden = false;
    });
}

$("#syllabus-back").addEventListener("click", hideSyllabusUpload);
$("#syllabus-retry").addEventListener("click", () => showSyllabusUpload());

// ---- Wiring ----

// ---- Boot ----
// Decide login vs app once at startup. /me.json is the source of truth —
// any other state (cached, optimistic) is a lie. On success, populate the
// dropdowns once (cheaper now than on first row click) then load Today.
async function boot() {
    try {
        const me = await api.me();
        if (!me) { showLogin(); return; }
        cachedMe = me;
        showApp();
        const footer = $("#logged-in-as");
        if (footer && me.email) footer.textContent = me.email;
        await ensureEditorLists();
        await load();
    } catch (err) {
        if (err instanceof NotAuthenticated) {
            showLogin();
            return;
        }
        // Server unreachable / wrong URL — show the login surface so the
        // user can fix the server URL via the options link, rather than
        // a stuck "Loading…" spinner.
        showLogin();
        setLoginStatus("Couldn't reach Compass: " + err.message, "error");
    }
}

boot();
