// Classes tab + class-detail drill-down. The list is a vertical card
// stack; each card opens the detail surface (syllabus iframe, documents,
// tasks, events, delete-class). "+ Add class" and "+ Upload syllabus"
// sit above the list and stay visible on the empty state so a fresh
// user can take action immediately.

import { api, NotAuthenticated } from "../api.js";
import { state, resetCaches } from "../state.js";
import { showLogin, showSecondary, returnToList, setFabHidden } from "../nav.js";
import { renderRow } from "./row.js";
import { ensureLookups } from "../lookups.js";
import { showAddClass } from "../forms/add-class.js";
import { showSyllabusUpload, startReparse } from "../forms/syllabus.js";
import { showSettings, setXaiStatus } from "../forms/settings.js";
import { load } from "./index.js";
import { isOfflineError, queueUpsert, queueDelete } from "../sync.js";

const $ = (sel) => document.querySelector(sel);

// Parsing is allowed when the user has their own key (uncapped) OR the
// server's free pool is configured and this account still has parses left.
// Shared by the "+ Upload syllabus" button and the class-detail "Reparse"
// button — mirrors the server's _parse_gate_response (docs/SYLLABUS.md).
// free_parses_remaining === null ⇒ uncapped (own key OR admin grant).
export function canParseNow() {
    const me = state.me || {};
    return !!me.xai_api_key_set
        || me.free_parses_remaining === null
        || (!!me.server_key_available && (me.free_parses_remaining || 0) > 0);
}
function parseBlockMsg() {
    const me = state.me || {};
    return me.server_key_available
        ? "You've used all your free syllabus parses — add your own xAI key in Settings for unlimited."
        : "Set your xAI API key in Settings first to parse syllabi.";
}

export async function loadClasses() {
    const target = $("#content");
    target.innerHTML = '<p class="muted loading">Loading…</p>';
    try {
        const classes = await api.classes();
        $("#today-date").textContent = "Classes";
        renderClassesList(target, classes);
    } catch (err) {
        if (err instanceof NotAuthenticated) showLogin();
        else {
            target.innerHTML = "";
            const p = document.createElement("p");
            p.className = "muted error";
            p.textContent = "Couldn't load: " + err.message;
            target.appendChild(p);
        }
    }
}

function renderClassesList(target, classes) {
    target.innerHTML = "";
    const actions = document.createElement("div");
    actions.className = "classes-actions";
    const addBtn = document.createElement("button");
    addBtn.type = "button";
    addBtn.textContent = "+ Add class";
    addBtn.addEventListener("click", showAddClass);
    const uploadBtn = document.createElement("button");
    uploadBtn.type = "button";
    uploadBtn.textContent = "+ Upload syllabus";
    // Out of free parses (or no key path at all) → bounce to Settings.
    const canParse = canParseNow();
    const blockMsg = parseBlockMsg();
    if (state.me && !canParse) {
        uploadBtn.classList.add("is-disabled");
        uploadBtn.title = blockMsg;
    }
    uploadBtn.addEventListener("click", () => {
        if (state.me && !canParse) {
            showSettings();
            setXaiStatus(blockMsg, "error");
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

// ---- Class detail ----

export async function showClassDetail(classId) {
    showSecondary("#class-detail");
    state.currentClassId = classId;

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
        state.lastClassDetail = data;  // offline fallback for refreshEventsOnly
        $("#class-detail-code").textContent = data.class.code;
        $("#class-detail-name").textContent = data.class.name || "";

        state.currentSyllabusId = data.syllabus ? data.syllabus.id : null;
        if (data.syllabus && data.syllabus.filename) {
            const url = await api.fileUrl(data.syllabus.filename);
            $("#class-detail-pdf").src = url;
            const openTab = $("#class-pdf-open-tab");
            const dl = $("#class-pdf-download");
            openTab.href = url;
            // Anchor click in a chrome-extension:// origin doesn't actually
            // navigate the panel — open in a real tab instead.
            openTab.onclick = (e) => {
                e.preventDefault();
                chrome.tabs.create({ url });
            };
            dl.href = url;
            dl.setAttribute("download", data.syllabus.filename);
            // Reparse is only meaningful with a syllabus present, and is gated
            // by the same entitlement as a fresh upload.
            const reparseBtn = $("#class-detail-reparse");
            reparseBtn.hidden = !canParseNow();
            $("#class-detail-syllabus-section").hidden = false;
        }

        if (data.documents && data.documents.length) {
            for (const d of data.documents) {
                docsUl.appendChild(await renderDocRow(d));
            }
        } else {
            $("#class-detail-docs-empty").hidden = false;
        }

        if (data.tasks.length === 0) $("#class-detail-tasks-empty").hidden = false;
        else data.tasks.forEach((t) => tasksUl.appendChild(renderRow(t, false)));

        renderEventsForClass(classId, data.events || []);
    } catch (err) {
        if (err instanceof NotAuthenticated) {
            showLogin();
            return;
        }
        $("#class-detail-code").textContent = "Couldn't load";
        $("#class-detail-name").textContent = err.message;
    }
}

// ---- Events review/calendar UI (parity with web class.html) ----
// Replaces the legacy flat #class-detail-events list with a banner +
// "Pending review" / "On calendar" subsections + bulk action buttons.
// Offline-aware: a network error queues the toggle/delete + leaves the
// optimistic DOM change in place so the user sees their edit hold.

function renderEventsForClass(classId, events) {
    const ul = $("#class-detail-events");
    const empty = $("#class-detail-events-empty");
    // Wipe any prior render — including the banner/toolbar nodes injected
    // outside the UL on the previous open.
    document.querySelectorAll("[data-events-injected]").forEach((n) => n.remove());
    ul.innerHTML = "";
    empty.hidden = true;

    if (!events.length) { empty.hidden = false; return; }
    const pending = events.filter((e) => e.added_to_calendar === false);
    const added = events.filter((e) => e.added_to_calendar !== false);

    const parent = ul.parentElement;
    const total = events.length;

    // Banner — only when pending events exist. Carries "Add all" + "Delete all"
    // side-by-side so the user doesn't scroll.
    if (pending.length) {
        const banner = document.createElement("div");
        banner.className = "events-review-banner";
        banner.setAttribute("data-events-injected", "1");
        const text = document.createElement("div");
        text.className = "events-review-banner-text";
        text.innerHTML = `<strong>${pending.length} event${pending.length === 1 ? "" : "s"} found</strong> in your syllabus. Add them to your calendar?`;
        const actions = document.createElement("div");
        actions.className = "events-review-banner-actions";
        const addAll = makeBulkBtn("Add all to calendar", "primary",
            () => doBulkAddAll(classId));
        const delAll = makeBulkBtn("Delete all events", "danger-btn",
            () => doBulkDeleteAll(classId, total));
        actions.appendChild(addAll); actions.appendChild(delAll);
        banner.appendChild(text); banner.appendChild(actions);
        parent.insertBefore(banner, ul);
    } else {
        // No pending → top toolbar still surfaces Delete all so it's
        // reachable without scrolling (matches the web's behavior).
        const toolbar = document.createElement("div");
        toolbar.className = "events-section-toolbar";
        toolbar.setAttribute("data-events-injected", "1");
        toolbar.appendChild(makeBulkBtn("Delete all events", "danger-btn small",
            () => doBulkDeleteAll(classId, total)));
        parent.insertBefore(toolbar, ul);
    }

    if (pending.length) {
        appendSubsection(parent, ul, "Pending review",
            makeBulkBtn("Add all to calendar", "small",
                () => doBulkAddAll(classId)));
        pending.forEach((ev) => ul.appendChild(renderEventRow(ev, false)));
    }

    if (added.length) {
        // Visual divider between the two groups — only when both exist.
        if (pending.length) {
            const sep = document.createElement("div");
            sep.className = "events-subsection-separator";
            sep.setAttribute("data-events-injected", "1");
            parent.insertBefore(sep, ul);
        }
        appendSubsection(parent, ul, "On calendar",
            makeBulkBtn("Remove all from calendar", "small",
                () => doBulkRemoveAll(classId, added.length)));
        added.forEach((ev) => ul.appendChild(renderEventRow(ev, true)));
    }
}

function makeBulkBtn(label, cls, onClick) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = cls;
    b.textContent = label;
    b.addEventListener("click", onClick);
    return b;
}

function appendSubsection(parent, ul, heading, btn) {
    const head = document.createElement("div");
    head.className = "events-subsection-head";
    head.setAttribute("data-events-injected", "1");
    const h = document.createElement("h3");
    h.textContent = heading;
    head.appendChild(h);
    head.appendChild(btn);
    parent.insertBefore(head, ul);
}

// Per-event row with an Add/Remove button. We don't use renderRow's
// generic editor wiring here because events on the class-detail surface
// are about calendar membership, not edit-task flow. Click on the title
// still opens the event editor (same as the legacy row).
function renderEventRow(ev, onCalendar) {
    const row = renderRow(ev, false);
    const main = row.querySelector(".todo-row-main");
    const del = main.querySelector(".todo-del");
    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = onCalendar ? "event-cal-remove" : "event-cal-add";
    toggle.textContent = onCalendar ? "− Remove" : "+ Add";
    toggle.title = onCalendar
        ? "Move this event back to pending review"
        : "Add this event to your calendar";
    toggle.addEventListener("click", async (e) => {
        e.stopPropagation();
        await doEventToggle(ev.id, !onCalendar, row);
    });
    main.insertBefore(toggle, del);
    return row;
}

async function doEventToggle(eventId, addToCal, rowEl) {
    // Optimistic: swap the row visually right now. Reconciliation comes
    // on the next class-detail open.
    rowEl.classList.add("toggling");
    try {
        if (addToCal) await api.addEventToCalendar(eventId);
        else await api.removeEventFromCalendar(eventId);
        // Re-render the whole events section so the row moves to the
        // correct subsection.
        await refreshEventsOnly();
    } catch (err) {
        if (err instanceof NotAuthenticated) { showLogin(); return; }
        if (isOfflineError(err)) {
            // Queue + leave the optimistic DOM change (we'll repaint on
            // next reconnect). Sync engine merges newest-wins server-side.
            await queueUpsert("events",
                { id: eventId, added_to_calendar: addToCal });
            rowEl.classList.add("queued-offline");
            return;
        }
        alert("Couldn't update: " + err.message);
        rowEl.classList.remove("toggling");
    }
}

async function doBulkAddAll(classId) {
    try {
        await api.addAllClassEvents(classId);
        await refreshEventsOnly();
    } catch (err) {
        if (err instanceof NotAuthenticated) { showLogin(); return; }
        if (isOfflineError(err)) {
            // Per-event queue so each event's flag lands correctly under
            // the newest-wins merge. The mirror is updated via queueUpsert.
            const pending = state.lastClassDetail?.events?.filter(
                (e) => e.added_to_calendar === false) || [];
            for (const ev of pending) {
                await queueUpsert("events",
                    { id: ev.id, added_to_calendar: true });
            }
            await refreshEventsOnly({ optimisticAddAll: true });
            return;
        }
        alert("Couldn't add: " + err.message);
    }
}

async function doBulkRemoveAll(classId, count) {
    if (!confirm(`Move all ${count} event${count === 1 ? "" : "s"} back to pending review? (Not permanent — you can re-add them.)`)) return;
    try {
        await api.removeAllClassEvents(classId);
        await refreshEventsOnly();
    } catch (err) {
        if (err instanceof NotAuthenticated) { showLogin(); return; }
        if (isOfflineError(err)) {
            const onCal = state.lastClassDetail?.events?.filter(
                (e) => e.added_to_calendar !== false) || [];
            for (const ev of onCal) {
                await queueUpsert("events",
                    { id: ev.id, added_to_calendar: false });
            }
            await refreshEventsOnly({ optimisticRemoveAll: true });
            return;
        }
        alert("Couldn't remove: " + err.message);
    }
}

async function doBulkDeleteAll(classId, count) {
    if (!confirm(`Permanently delete ALL ${count} event${count === 1 ? "" : "s"} for this class? This cannot be undone.`)) return;
    try {
        await api.deleteAllClassEvents(classId);
        await refreshEventsOnly();
    } catch (err) {
        if (err instanceof NotAuthenticated) { showLogin(); return; }
        if (isOfflineError(err)) {
            const all = state.lastClassDetail?.events || [];
            for (const ev of all) await queueDelete("events", ev.id);
            await refreshEventsOnly({ optimisticDeleteAll: true });
            return;
        }
        alert("Couldn't delete: " + err.message);
    }
}

// Re-fetch the class detail (or replay from cached state offline) and
// re-render only the events section. Keeps the syllabus iframe + tasks
// intact (no flicker on the rest of the surface).
async function refreshEventsOnly(opts = {}) {
    const classId = state.currentClassId;
    if (!classId) return;
    let data;
    try {
        data = await api.classDetail(classId);
        state.lastClassDetail = data;
    } catch (err) {
        if (isOfflineError(err) && state.lastClassDetail) {
            // Offline: mutate the cached events to match the queued action.
            data = state.lastClassDetail;
            if (opts.optimisticAddAll) {
                data.events = data.events.map((e) =>
                    ({ ...e, added_to_calendar: true }));
            } else if (opts.optimisticRemoveAll) {
                data.events = data.events.map((e) =>
                    ({ ...e, added_to_calendar: false }));
            } else if (opts.optimisticDeleteAll) {
                data.events = [];
            }
        } else { throw err; }
    }
    renderEventsForClass(classId, data.events || []);
}

export function hideClassDetail() {
    returnToList();
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

// Wire one-shot at boot — class-detail back button, doc upload form,
// delete-class button.
export function bindClassDetail() {
    $("#class-detail-back").addEventListener("click", hideClassDetail);

    $("#class-detail-reparse").addEventListener("click", () => {
        const syllabusId = state.currentSyllabusId;
        const classId = state.currentClassId;
        if (!syllabusId || !classId) return;
        if (!canParseNow()) {
            showSettings();
            setXaiStatus(parseBlockMsg(), "error");
            return;
        }
        if (!confirm("Reparsing re-runs the AI parse on this syllabus and ERASES the events previously found from it, replacing them with a fresh set. Continue?")) return;
        startReparse(syllabusId, classId);
    });

    const docForm = $("#class-detail-doc-upload");
    const fileInput = $("#class-detail-doc-file");
    const chooseBtn = $("#class-detail-doc-choose");
    const fileName = $("#class-detail-doc-filename");
    // The file input is hidden; only the "Choose file" button opens the
    // picker. Clicking elsewhere on the row does nothing (the bare native
    // input used to open Finder from anywhere on the row).
    chooseBtn.addEventListener("click", () => fileInput.click());
    fileInput.addEventListener("change", () => {
        const f = fileInput.files[0];
        fileName.textContent = f ? f.name : "No file chosen";
        fileName.classList.toggle("has-file", !!f);
    });

    docForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        if (!state.currentClassId) return;
        const file = fileInput.files[0];
        if (!file) { setDocStatus("Choose a file first.", "error"); return; }
        const title = (docForm.title.value || "").trim();
        setDocStatus("Uploading…", "pending");
        try {
            await api.uploadDoc(state.currentClassId, file, title);
            docForm.reset();
            fileName.textContent = "No file chosen";
            fileName.classList.remove("has-file");
            setDocStatus("Uploaded ✓", "success");
            setTimeout(() => setDocStatus("", ""), 800);
            await showClassDetail(state.currentClassId);
        } catch (err) {
            if (err instanceof NotAuthenticated) { showLogin(); return; }
            setDocStatus("Couldn't upload: " + err.message, "error");
        }
    });

    $("#class-detail-delete").addEventListener("click", async () => {
        if (!state.currentClassId) return;
        const id = state.currentClassId;
        const code = $("#class-detail-code").textContent || "this class";
        if (!confirm(`Delete ${code} and everything in it?`)) return;
        try {
            await api.deleteClass(id);
            resetCaches();
            hideClassDetail();
            await ensureLookups();
            await load();
        } catch (err) {
            if (err instanceof NotAuthenticated) { showLogin(); return; }
            if (isOfflineError(err)) {
                // Queue the delete (mirror + tombstone-on-push) so it removes
                // on reconnect; leave the detail surface. Mirrors web parity.
                await queueDelete("classes", id);
                resetCaches();
                hideClassDetail();
                return;
            }
            alert("Couldn't delete class: " + err.message);
        }
    });
}

function setDocStatus(text, kind) {
    const el = $("#class-detail-doc-status");
    if (!text) { el.hidden = true; return; }
    el.textContent = text;
    el.className = "status " + (kind || "");
    el.hidden = false;
}
