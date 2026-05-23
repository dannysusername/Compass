// Toggle (complete/uncomplete) and delete behaviors for task/event rows.
// Optimistic updates with rollback on failure — feels snappy, the next
// /today.json refresh is the source of truth. Recurring tasks delegate
// the delete to the bottom-sheet picker (this/future/all).

import { api, NotAuthenticated } from "../api.js";
import { showLogin } from "../nav.js";
import { showRecurringSheet } from "./recurring-sheet.js";
import { load } from "../views/index.js";
import { offlineMarkTask, offlineDeleteTask, isOfflineError } from "../sync.js";

export async function onToggle(rowEl) {
    const kind = rowEl.dataset.kind;
    const id = rowEl.dataset.id;
    if (!id) return;
    const wasDone = rowEl.classList.contains("done");
    setRowDone(rowEl, !wasDone);
    try {
        if (kind === "event") await api.toggleEvent(id);
        else await api.toggleTask(id);
    } catch (err) {
        if (err instanceof NotAuthenticated) { setRowDone(rowEl, wasDone); showLogin(); return; }
        // Offline: keep the optimistic state, queue it for the next sync.
        // (Events are server-generated — no offline write, so roll back.)
        if (kind === "task" && isOfflineError(err)) {
            await offlineMarkTask(id, !wasDone);
        } else {
            setRowDone(rowEl, wasDone);  // genuine server error → revert
        }
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

export async function onDelete(rowEl) {
    const kind = rowEl.dataset.kind;
    const isRecurring = kind === "task" && !!rowEl.dataset.rrule;
    if (isRecurring) {
        showRecurringSheet(rowEl, runDelete);
        return;
    }
    const label = kind === "event" ? "event" : "task";
    if (!confirm(`Delete this ${label}?`)) return;
    await runDelete(rowEl, "all");
}

// `mode` is one of: 'this' (exclude one occurrence), 'future' (cap rrule
// at this occurrence), 'all' (hard-delete the whole task/event row).
export async function runDelete(rowEl, mode) {
    const kind = rowEl.dataset.kind;
    const id = rowEl.dataset.id;
    const dueAt = rowEl.dataset.dueAt || "";
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
            if (kind === "event") await api.deleteEvent(id);
            else await api.deleteTask(id);
        }
        // Re-pull when other rows might be affected: any recurring
        // delete (sibling occurrences) OR any 'this'/'future' mode
        // (recurring-only). Skip the reload for the common single-row
        // case, where we already removed the only DOM element that matters.
        if (mode !== "all" || isRecurring) await load();
    } catch (err) {
        if (err instanceof NotAuthenticated) {
            if (parent) parent.insertBefore(rowEl, next);
            showLogin();
            return;
        }
        // Offline delete of a plain task: keep it removed + queue the
        // delete for the next sync. (Recurring this/future and events
        // need the server, so those revert.)
        if (kind === "task" && mode === "all" && !rowEl.dataset.rrule && isOfflineError(err)) {
            await offlineDeleteTask(id);
            return;
        }
        if (parent) parent.insertBefore(rowEl, next);  // revert
        alert("Couldn't delete: " + err.message);
    }
}
