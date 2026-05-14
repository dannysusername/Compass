// Edit-task surface. Full website parity: title, class, due/starts, all-day,
// repeat (+ end date), tag, reminders (alerts chips), attachments
// (existing list with × delete + new file picker), notes.
//
// Partial-update protocol: the server only touches columns whose form
// fields are PRESENT in the request. The full edit modal sends every
// field it owns so explicit clears (notes='', tag_id='', alerts='') work.
// Fields the modal doesn't expose (e.g. completed_at) are absent and
// therefore preserved.
//
// Attachments are split:
//   - DELETE existing attachment → fires immediately via /attachments/{id}/delete
//     (destructive, confirmed inline; no pending state to track).
//   - ADD new attachment → buffered locally; uploaded after a successful
//     save via POST /tasks/{id}/attachments. Matches the add-task pattern.

import { api, NotAuthenticated } from "../api.js";
import { state } from "../state.js";
import { showLogin, showSecondary, returnToList, showToast } from "../nav.js";
import { ensureLookups } from "../lookups.js";
import { alertLabel, formatLocal, addFilesToBuffer } from "../util.js";
import { isItemOverdue } from "../views/row.js";
import { load } from "../views/index.js";

const $ = (sel) => document.querySelector(sel);

// Per-form transient state:
//   - editAlerts: array of integer minutes-before, mirrored to the hidden
//     `alerts` input on every chip change so submit is one form-data call.
//   - editPendingFiles: File objects picked but not uploaded yet.
//   - editReturnToClass: classId to drill back into after save (when the
//     user opened the editor from a class-detail row).
let editAlerts = [];
let editPendingFiles = [];
let editPendingDeletes = [];   // attachment IDs the user × clicked; applied on Save
let editFailedUploads = [];    // files whose upload failed last save; Retry button uses these
let editReturnToClass = null;
// True once /tasks/{id}/details.json has resolved. Submit only sends the
// `alerts` form field when this is true — otherwise an early/failed load
// would let us send alerts="" and silently clear the user's reminders.
let editDetailsLoaded = false;

export async function showEditor(rowEl) {
    showSecondary("#editor");
    // Class + tag dropdowns must be populated BEFORE setting their value —
    // otherwise `select.value = "5"` for a missing option silently drops
    // to "" and the form would save with no class/tag. Force-refresh so
    // a class created/deleted in another tab shows up here.
    await ensureLookups({ force: true });
    // Decide where to go after save: if the row lived inside class-detail,
    // come back to it; otherwise the regular list reload.
    editReturnToClass = state.currentClassId || null;
    // Disable the Save button until /tasks/{id}/details.json finishes —
    // otherwise an early submit would send alerts="" and rrule_until=""
    // (clearing both on the server) before we'd loaded the existing values.
    setSaveEnabled(false);
    populateEditor(rowEl);
    const t = $("#edit-form").querySelector("input[name='title']");
    if (t) { t.focus(); t.select(); }
}

function setSaveEnabled(enabled) {
    const btn = $("#edit-form button[type='submit']");
    if (!btn) return;
    btn.disabled = !enabled;
    btn.textContent = enabled ? "Save" : "Loading…";
}

export function hideEditor() {
    setEditStatus("", "");
    if (editReturnToClass) {
        // Re-open class detail rather than dump back to the list view.
        // Imported lazily to avoid a circular import at module init.
        import("../views/classes.js").then(({ showClassDetail }) => {
            showClassDetail(editReturnToClass);
        });
    } else {
        returnToList();
    }
}

function populateEditor(rowEl) {
    const editForm = $("#edit-form");
    const id = rowEl.dataset.id;
    editForm.task_id.value = id;
    editForm.title.value = rowEl.dataset.title || "";
    // Overdue cap: visible when the row's due_at is in the past AND the
    // task isn't completed. The row data attributes are the same shape
    // isItemOverdue expects (it.completed isn't on the row dataset, but
    // overdue rows already carry the .done class — check that instead).
    const cap = $("#edit-overdue-cap");
    if (cap) {
        const overdue = isItemOverdue({
            due_at: rowEl.dataset.dueAt || null,
            completed: rowEl.classList.contains("done"),
        });
        cap.hidden = !overdue;
    }
    const isAllDay = rowEl.dataset.isAllDay === "1";
    editForm.is_all_day.checked = isAllDay;
    syncAllDay();
    setDateInput(editForm.due_at, rowEl.dataset.dueAt, isAllDay);
    setDateInput(editForm.starts_at, rowEl.dataset.startsAt, false);
    editForm.class_id.value = rowEl.dataset.classId || "0";
    editForm.tag_id.value = rowEl.dataset.tagId || "";
    editForm.rrule.value = rowEl.dataset.rrule || "";
    editForm.notes.value = rowEl.dataset.notes || "";
    syncRruleVisibility();

    // Reset deferred state.
    editAlerts = [];
    editPendingFiles = [];
    editPendingDeletes = [];
    editFailedUploads = [];
    editDetailsLoaded = false;
    renderEditAlertsChips();
    renderEditAttachmentsList();
    editForm.rrule_until.value = "";

    // Async pull of rrule_until + alerts + attachments. Save stays
    // disabled until this resolves so an early submit can't clear them.
    api.taskDetails(id).then((d) => {
        if (!d) { setSaveEnabled(true); return; }
        editForm.rrule_until.value = d.rrule_until || "";
        editAlerts = (d.alerts || []).map((n) => parseInt(n, 10)).filter((n) => !Number.isNaN(n));
        renderEditAlertsChips();
        renderExistingAttachments(d.attachments || [], id);
        editDetailsLoaded = true;
        setSaveEnabled(true);
    }).catch((err) => {
        if (err instanceof NotAuthenticated) { showLogin(); return; }
        // Re-enable Save so the user can still edit basics. editDetailsLoaded
        // stays false → submit omits the alerts field → server preserves
        // the existing alerts (partial-update semantics).
        setSaveEnabled(true);
    });
}

function setDateInput(input, isoOrEmpty, isAllDay) {
    if (!input) return;
    if (!isoOrEmpty) { input.value = ""; return; }
    const len = isAllDay && input.type === "date" ? 10 : 16;
    input.value = isoOrEmpty.slice(0, len);
}

// All-day + Repeat both want to control starts_at: All-day clears+disables
// it (anchored to its due date); Repeat does the same (rrule + range is
// mutually exclusive — see CLAUDE.md). They OR their disable conditions
// so toggling one off doesn't re-enable the field while the other is on.
function syncAllDay() {
    const editForm = $("#edit-form");
    const on = editForm.is_all_day.checked;
    const due = editForm.due_at;
    const starts = editForm.starts_at;
    if (on) {
        if (due.value && due.type === "datetime-local") {
            due.dataset.lastTime = due.value.slice(11, 16) || "17:00";
            due.value = due.value.slice(0, 10);
        }
        due.type = "date";
        if (starts) {
            if (starts.value && starts.type === "datetime-local") {
                starts.dataset.lastTime = starts.value.slice(11, 16) || "09:00";
            }
            starts.value = "";
            starts.type = "date";
        }
    } else {
        if (due.value && due.type === "date") {
            const lastTime = due.dataset.lastTime || "17:00";
            due.value = due.value + "T" + lastTime;
        }
        due.type = "datetime-local";
        if (starts) {
            if (starts.value && starts.type === "date") {
                const lastTime = starts.dataset.lastTime || "09:00";
                starts.value = starts.value + "T" + lastTime;
            }
            starts.type = "datetime-local";
        }
    }
    syncStartsDisabled();
}

function syncRruleVisibility() {
    const editForm = $("#edit-form");
    const showing = !!editForm.rrule.value;
    $("#edit-until-label").hidden = !showing;
    if (!showing) editForm.rrule_until.value = "";
    else updateRruleUntilMin();
    syncStartsDisabled();
}

// Constrain the rrule_until picker so the user can't pick a date on or
// before Due. Same UX trap protection as add-task.
function updateRruleUntilMin() {
    const editForm = $("#edit-form");
    const due = editForm.due_at;
    const until = editForm.rrule_until;
    if (!due || !until) return;
    const dueVal = due.value;
    if (!dueVal) {
        until.removeAttribute("min");
        return;
    }
    let minVal;
    if (due.type === "date") {
        const d = new Date(dueVal + "T00:00:00");
        d.setDate(d.getDate() + 1);
        minVal = formatLocal(d);
    } else {
        const d = new Date(dueVal);
        d.setMinutes(d.getMinutes() + 1);
        minVal = formatLocal(d);
    }
    until.min = minVal;
    if (until.value && until.value < minVal) until.value = "";
}

function syncStartsDisabled() {
    const editForm = $("#edit-form");
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

// ---- Alerts (chips) ----

function renderEditAlertsChips() {
    const chips = $("#edit-alerts-chips");
    const hidden = $("#edit-alerts-value");
    if (!chips || !hidden) return;
    chips.innerHTML = "";
    const cleaned = [...new Set(editAlerts.map((n) => parseInt(n, 10)))]
        .filter((n) => !Number.isNaN(n))
        .sort((a, b) => b - a);
    editAlerts = cleaned;
    hidden.value = cleaned.join(",");
    cleaned.forEach((m) => {
        const chip = document.createElement("span");
        chip.className = "alert-chip";
        chip.dataset.minutes = String(m);
        const lab = document.createElement("span");
        lab.textContent = alertLabel(m);
        const rm = document.createElement("button");
        rm.type = "button";
        rm.className = "alert-chip-remove";
        rm.setAttribute("aria-label", `Remove ${lab.textContent}`);
        rm.textContent = "×";
        rm.addEventListener("click", () => {
            editAlerts = editAlerts.filter((x) => x !== m);
            renderEditAlertsChips();
        });
        chip.appendChild(lab);
        chip.appendChild(rm);
        chips.appendChild(chip);
    });
}

// ---- Attachments ----

// Existing attachments — × deletes immediately (destructive, confirmed).
function renderExistingAttachments(list, taskId) {
    const ul = $("#edit-attachments-list");
    if (!ul) return;
    ul.innerHTML = "";
    list.forEach((a) => ul.appendChild(renderExistingAttachmentRow(a, taskId)));
    // Re-render any pending files appended by the user during the same
    // editor session.
    renderEditAttachmentsList(true);
}

function renderExistingAttachmentRow(a, taskId) {
    const li = document.createElement("li");
    li.className = "attachment-row attachment-existing";
    li.dataset.attachmentId = String(a.id);
    const link = document.createElement("a");
    link.className = "attachment-link";
    link.href = "#";
    link.textContent = a.original_name || a.filename;
    link.title = a.filename;
    link.addEventListener("click", async (e) => {
        e.preventDefault();
        // If this row is marked for delete, treat the link as a no-op
        // (visually it's struck through; clicking shouldn't open).
        if (li.classList.contains("attachment-pending-delete")) return;
        const url = await api.fileUrl(a.filename);
        chrome.tabs.create({ url });
    });
    const rm = document.createElement("button");
    rm.type = "button";
    rm.className = "alert-chip-remove";
    setRemoveBtnState(rm, a, false);
    rm.addEventListener("click", () => {
        const isPending = editPendingDeletes.includes(a.id);
        if (isPending) {
            // Undo: remove from pending list, restore visual state.
            editPendingDeletes = editPendingDeletes.filter((x) => x !== a.id);
            li.classList.remove("attachment-pending-delete");
            setRemoveBtnState(rm, a, false);
        } else {
            // Mark for delete on next Save. No confirm dialog (the
            // visual strikethrough is the affordance; Cancel = undo).
            editPendingDeletes.push(a.id);
            li.classList.add("attachment-pending-delete");
            setRemoveBtnState(rm, a, true);
        }
    });
    li.appendChild(link);
    li.appendChild(rm);
    return li;
}

function setRemoveBtnState(btn, a, isPending) {
    if (isPending) {
        btn.textContent = "↺";
        btn.title = "Undo delete";
        btn.setAttribute("aria-label", `Undo delete of ${a.original_name || a.filename}`);
    } else {
        btn.textContent = "×";
        btn.title = "Mark for delete";
        btn.setAttribute("aria-label", `Delete ${a.original_name || a.filename}`);
    }
}

// Pending files — buffered until save, uploaded after edit returns OK.
function renderEditAttachmentsList(append = false) {
    const ul = $("#edit-attachments-list");
    if (!ul) return;
    if (!append) {
        // Wipe pending rows; keep existing rows in place.
        ul.querySelectorAll(".attachment-pending").forEach((el) => el.remove());
    } else {
        ul.querySelectorAll(".attachment-pending").forEach((el) => el.remove());
    }
    editPendingFiles.forEach((f, i) => {
        const li = document.createElement("li");
        li.className = "attachment-row attachment-pending";
        const name = document.createElement("span");
        name.textContent = f.name + " (new)";
        const rm = document.createElement("button");
        rm.type = "button";
        rm.className = "alert-chip-remove";
        rm.setAttribute("aria-label", `Remove ${f.name}`);
        rm.textContent = "×";
        rm.addEventListener("click", () => {
            editPendingFiles.splice(i, 1);
            renderEditAttachmentsList(true);
        });
        li.appendChild(name);
        li.appendChild(rm);
        ul.appendChild(li);
    });
}

// ---- Bind once at boot ----

export function bindEditTask() {
    const editForm = $("#edit-form");

    editForm.is_all_day.addEventListener("change", () => {
        syncAllDay();
        updateRruleUntilMin();
    });
    editForm.rrule.addEventListener("change", syncRruleVisibility);
    editForm.due_at.addEventListener("input", updateRruleUntilMin);

    // Reminders picker → push onto editAlerts, re-render chips.
    const adder = $("#edit-alerts-add");
    if (adder) {
        adder.addEventListener("change", () => {
            const v = adder.value;
            if (!v) return;
            editAlerts.push(parseInt(v, 10));
            renderEditAlertsChips();
            adder.value = "";
        });
    }

    // Attachment file picker → buffer with size + dup checks.
    const fileInput = $("#edit-attachments-input");
    if (fileInput) {
        fileInput.addEventListener("change", (e) => {
            const result = addFilesToBuffer(e.target.files, editPendingFiles);
            fileInput.value = "";
            renderEditAttachmentsList(true);
            if (result.error) setEditStatus(result.error, "error");
            else if (result.replaced) setEditStatus(`Replaced ${result.replaced} file(s).`, "success");
            else setEditStatus("", "");
        });
    }

    editForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const submitBtn = editForm.querySelector("button[type='submit']");
        // Re-entrancy guard — same as add-task. Save is also disabled
        // while details fetch is in flight (see setSaveEnabled), so
        // submit can't fire before alerts/rrule_until are loaded either.
        if (submitBtn && submitBtn.disabled) return;
        const id = editForm.task_id.value;
        if (!id) return;
        const title = (editForm.title.value || "").trim();
        if (!title) {
            setEditStatus("Title required.", "error");
            editForm.title.focus();
            return;
        }
        const due = editForm.due_at.value;
        const starts = editForm.starts_at.value;
        if (starts && due && starts > due) {
            setEditStatus("Cannot save, the start date must be before the end date.", "error");
            return;
        }
        if (editForm.tag_id.value === "__new__") {
            setEditStatus("Pick a tag (or finish creating the new one).", "error");
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
        // rrule_until is meaningful ONLY when the task has an rrule. Two
        // protections layered:
        //   1. Don't send until details have loaded (otherwise we'd send
        //      "" and clear the user's existing end-date prematurely).
        //   2. Always send when rrule is set OR when we're explicitly
        //      clearing rrule (the form's rrule="" branch — server's
        //      wipe path needs to see rrule_until="" to clean state).
        //      But when neither rrule nor rrule_until is set, omit so
        //      the server doesn't accept an orphan rrule_until.
        if (editDetailsLoaded) {
            const hasRrule = !!editForm.rrule.value;
            const untilVal = editForm.rrule_until.value;
            if (hasRrule) {
                fd.append("rrule_until", untilVal || "");
            } else {
                // No rrule: clear any stale rrule_until on the server too.
                fd.append("rrule_until", "");
            }
        }
        // alerts: only send the field when details have loaded —
        // otherwise we'd be saying "user explicitly chose no reminders"
        // when really they just hit Save before we knew the existing set.
        // Server's partial-update preserves rows for fields it doesn't see.
        if (editDetailsLoaded) {
            fd.append("alerts", $("#edit-alerts-value").value || "");
        }

        setEditStatus("Saving…", "pending");
        if (submitBtn) submitBtn.disabled = true;
        try {
            await api.editTask(id, fd);
            // Apply pending attachment deletes BEFORE uploads — keeps
            // the user's intent ordered (× then save means the file is
            // gone). Failures are tracked separately and surfaced.
            const failedDeletes = [];
            for (const attachmentId of editPendingDeletes) {
                try { await api.deleteAttachment(attachmentId); }
                catch (err) {
                    console.error("attachment delete failed:", err);
                    failedDeletes.push(attachmentId);
                }
            }
            editPendingDeletes = failedDeletes;
            // Upload any pending new attachments. Track failures so the
            // user can Retry without re-picking the same files.
            const failedUploads = [];
            for (const f of editPendingFiles) {
                try { await api.addAttachment(id, f); }
                catch (err) {
                    console.error("attachment upload failed:", err);
                    failedUploads.push(f);
                }
            }
            editPendingFiles = [];
            editFailedUploads = failedUploads;
            if (editFailedUploads.length || editPendingDeletes.length) {
                // Partial success: keep the form open so the user can Retry.
                renderEditAttachmentsList(true);
                renderUploadFailureBanner(id);
                return;
            }
            await load();
            hideEditor();
            showToast("Task saved ✓", "success");
        } catch (err) {
            if (err instanceof NotAuthenticated) {
                showLogin();
                hideEditor();
                return;
            }
            setEditStatus("Couldn't save: " + err.message, "error");
        } finally {
            if (submitBtn) submitBtn.disabled = false;
        }
    });

    $("#editor-back").addEventListener("click", hideEditor);
    $("#editor-cancel").addEventListener("click", hideEditor);
}

function setEditStatus(text, kind) {
    const el = $("#edit-status");
    if (!text) { el.hidden = true; return; }
    el.textContent = text;
    el.className = "status " + (kind || "");
    el.hidden = false;
}

// Yellow warning + Retry button shown after a partial save (some
// attachment uploads or deletes failed). Re-attempts ONLY the failures
// so we don't re-upload files that already succeeded.
function renderUploadFailureBanner(taskId) {
    const el = $("#edit-status");
    el.innerHTML = "";
    el.className = "status warn";
    el.hidden = false;
    const failedCount = editFailedUploads.length + editPendingDeletes.length;
    const msg = document.createElement("span");
    msg.textContent = `Saved, but ${failedCount} attachment change(s) failed. `;
    el.appendChild(msg);
    const retry = document.createElement("button");
    retry.type = "button";
    retry.className = "muted";
    retry.textContent = "Retry";
    retry.addEventListener("click", async () => {
        setEditStatus("Retrying…", "pending");
        const stillFailedDeletes = [];
        for (const attId of editPendingDeletes) {
            try { await api.deleteAttachment(attId); }
            catch (err) { stillFailedDeletes.push(attId); }
        }
        editPendingDeletes = stillFailedDeletes;
        const stillFailedUploads = [];
        for (const f of editFailedUploads) {
            try { await api.addAttachment(taskId, f); }
            catch (err) { stillFailedUploads.push(f); }
        }
        editFailedUploads = stillFailedUploads;
        if (editFailedUploads.length || editPendingDeletes.length) {
            renderUploadFailureBanner(taskId);
        } else {
            setEditStatus("All changes saved ✓", "success");
            setTimeout(async () => { await load(); hideEditor(); }, 600);
        }
    });
    el.appendChild(retry);
}
