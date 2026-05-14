// Event editor. Click an event row's body → swap to this surface. Server's
// /events/{id}/edit accepts title, kind, starts_at, ends_at; "Duplicate"
// calls /events/{id}/clone.
//
// Like the task editor, save returns to the source: class-detail when the
// row was opened from there, otherwise the regular list.

import { api, NotAuthenticated } from "../api.js";
import { state } from "../state.js";
import { showLogin, showSecondary, returnToList } from "../nav.js";
import { load } from "../views/index.js";

const $ = (sel) => document.querySelector(sel);

let returnToClass = null;

export function showEventEditor(rowEl) {
    showSecondary("#event-editor-view");
    const f = $("#event-edit-form");
    f.event_id.value = rowEl.dataset.id;
    f.class_id.value = rowEl.dataset.classId || "";
    returnToClass = state.currentClassId
        || (rowEl.dataset.classId ? parseInt(rowEl.dataset.classId, 10) : null);
    f.title.value = rowEl.dataset.title || "";
    f.kind.value = rowEl.dataset.subKind || "milestone";
    // Server stores starts_at on events as the row's due_at (renderRow
    // normalizes both into data-due-at). Wall-clock-prefix slice.
    f.starts_at.value = (rowEl.dataset.dueAt || "").slice(0, 16);
    f.ends_at.value = "";
    setStatus("", "");
    f.title.focus();
    f.title.select();
}

function hideEventEditor() {
    if (returnToClass) {
        import("../views/classes.js").then(({ showClassDetail }) => {
            showClassDetail(returnToClass);
        });
    } else {
        returnToList();
    }
}

function setStatus(text, kind) {
    const el = $("#event-edit-status");
    if (!text) { el.hidden = true; return; }
    el.textContent = text;
    el.className = "status " + (kind || "");
    el.hidden = false;
}

export function bindEventEditor() {
    const f = $("#event-edit-form");
    f.addEventListener("submit", async (e) => {
        e.preventDefault();
        const id = f.event_id.value;
        if (!id) return;
        const fd = new FormData();
        fd.append("title", f.title.value || "");
        fd.append("kind", f.kind.value || "milestone");
        if (f.starts_at.value) fd.append("starts_at", f.starts_at.value);
        if (f.ends_at.value) fd.append("ends_at", f.ends_at.value);
        setStatus("Saving…", "pending");
        try {
            await api.editEvent(id, fd);
            await load();
            hideEventEditor();
        } catch (err) {
            if (err instanceof NotAuthenticated) { showLogin(); return; }
            setStatus("Couldn't save: " + err.message, "error");
        }
    });
    $("#event-clone-btn").addEventListener("click", async () => {
        const id = f.event_id.value;
        if (!id) return;
        if (!confirm("Duplicate this event?")) return;
        setStatus("Duplicating…", "pending");
        try {
            await api.cloneEvent(id);
            await load();
            hideEventEditor();
        } catch (err) {
            if (err instanceof NotAuthenticated) { showLogin(); return; }
            setStatus("Couldn't duplicate: " + err.message, "error");
        }
    });
    $("#event-editor-back").addEventListener("click", hideEventEditor);
    $("#event-editor-cancel").addEventListener("click", hideEventEditor);
}
