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
    // Row body click → open editor (tasks only — events have no edit
    // route in the web app, so we mirror that). Circle and × buttons
    // already stopPropagation so they don't trigger this.
    if (it.kind === "task") {
        main.classList.add("clickable");
        main.addEventListener("click", (e) => {
            // Defensive: ignore clicks that bubbled from inner buttons.
            if (e.target.closest("button")) return;
            showEditor(li);
        });
    }

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

    const tagName = it.tag_name || it.sub_kind;
    if (tagName) {
        const tag = document.createElement("span");
        tag.className = "todo-tag";
        tag.style.setProperty("--tag-color", it.tag_color || it.sub_kind_color || "");
        tag.textContent = tagName;
        main.appendChild(tag);
    }

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
    if (it.is_all_day) return "All day";
    if (!it.due_at) return "";
    // due_at comes through as ISO with offset (e.g. "2026-05-08T17:00:00-04:00").
    // Render the local-clock time portion — strip date + tz, use HH:MM.
    const m = it.due_at.match(/T(\d{2}):(\d{2})/);
    if (!m) return "";
    if (m[1] === "00" && m[2] === "00") return ""; // midnight = "no specific time"
    return `${m[1]}:${m[2]}`;
}

function renderBucket(bucket) {
    const block = document.createElement("section");
    block.className = "class-block";

    const head = document.createElement("div");
    head.className = "class-block-head";
    if (bucket.is_personal) head.classList.add("is-personal");
    const code = document.createElement("span");
    code.className = "class-code";
    code.textContent = bucket.code;
    head.appendChild(code);
    if (!bucket.is_personal && bucket.name) {
        const name = document.createElement("span");
        name.className = "class-name";
        name.textContent = bucket.name;
        head.appendChild(name);
    }
    block.appendChild(head);

    const list = document.createElement("ul");
    list.className = "todo-list";
    bucket.items.forEach((it) => list.appendChild(renderRow(it, false)));
    bucket.overdue_items.forEach((it) => list.appendChild(renderRow(it, true)));
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

function renderLoggedOut(target) {
    target.innerHTML = "";
    const p = document.createElement("p");
    p.className = "muted";
    p.textContent = "Sign in to Compass to see your tasks.";
    target.appendChild(p);
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "primary";
    btn.textContent = "Log in to Compass";
    btn.addEventListener("click", async () => {
        const url = await api.base();
        chrome.tabs.create({ url: url + "/login" });
    });
    target.appendChild(btn);
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
            renderLoggedOut($("#content"));
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
            renderLoggedOut($("#content"));
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
    editorView.hidden = false;
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
// so we don't re-fetch every time the modal opens — classes and tags
// rarely change mid-session, and this side panel is long-lived.
let classesPromise = null;
let tagsPromise = null;
async function ensureEditorLists() {
    if (!classesPromise) classesPromise = api.classes().catch(() => []);
    if (!tagsPromise) tagsPromise = api.tags().catch(() => []);
    const [classes, tags] = await Promise.all([classesPromise, tagsPromise]);
    const classSel = editForm.class_id;
    // Keep the leading "Personal (no class)" option, drop and re-add the rest.
    while (classSel.options.length > 1) classSel.remove(1);
    classes.forEach((c) => {
        const o = document.createElement("option");
        o.value = String(c.id);
        o.textContent = `${c.code} — ${c.name}`;
        classSel.appendChild(o);
    });
    const tagSel = editForm.tag_id;
    while (tagSel.options.length > 1) tagSel.remove(1);
    tags.forEach((t) => {
        const o = document.createElement("option");
        o.value = String(t.id);
        o.textContent = t.name;
        tagSel.appendChild(o);
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
            renderLoggedOut(listView);
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

$("#editor-back").addEventListener("click", hideEditor);
$("#editor-cancel").addEventListener("click", hideEditor);
$("#edit-open-app").addEventListener("click", async (e) => {
    e.preventDefault();
    const url = await api.base();
    chrome.tabs.create({ url });
});

// Pre-load class + tag dropdowns the first time the side panel renders
// (cheaper than waiting until the user clicks a row). Cached promises
// mean repeat calls are no-ops.
ensureEditorLists();

// ---- Load ----
async function load() {
    const target = $("#content");
    target.innerHTML = '<p class="muted loading">Loading…</p>';
    try {
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
            renderLoggedOut(target);
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

// ---- Wiring ----
$("#refresh-btn").addEventListener("click", load);
$("#open-app").addEventListener("click", async (e) => {
    e.preventDefault();
    const url = await api.base();
    chrome.tabs.create({ url });
});

load();
