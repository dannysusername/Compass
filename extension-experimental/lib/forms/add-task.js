// Add-task surface (FAB). Same view-swap pattern as #editor: hides
// #content while up, restores on cancel/save. Smart defaults pre-fill
// starts_at + due_at so submitting immediately produces a valid hour-
// long event today. Mirrors the website's add-task modal field-for-field.
//
// showAddTaskForDay(yyyymmdd) is the entry point for month-view's
// per-day "+" — pre-fills the picked day at 9–10am.

import { api, NotAuthenticated } from "../api.js";
import { state } from "../state.js";
import { showLogin, showSecondary, returnToList, showToast } from "../nav.js";
import { ensureLookups, defaultClassId } from "../lookups.js";
import {
    smartDefaultStart, smartDefaultDue, smartDefaultsForDay, alertLabel,
    formatLocal, formatLocalDate, addFilesToBuffer,
} from "../util.js";
import { load } from "../views/index.js";

const $ = (sel) => document.querySelector(sel);

let addAlerts = [];
let addPendingFiles = [];
let addFailedUploads = [];     // files whose upload failed after a successful create; Retry uses these
let addCreatedTaskId = null;   // set after successful create when uploads remain to retry

function setAddStatus(text, kind) {
    const el = $("#add-task-status");
    if (!text) { el.hidden = true; return; }
    el.textContent = text;
    el.className = "status " + (kind || "");
    el.hidden = false;
}

// Yellow warning + Retry shown after a partial save (the task itself was
// created, but some attachments didn't upload). Re-attempts ONLY the
// failures so we don't double-upload anything that succeeded.
function renderAddUploadFailureBanner() {
    const el = $("#add-task-status");
    el.innerHTML = "";
    el.className = "status warn";
    el.hidden = false;
    const msg = document.createElement("span");
    msg.textContent = `Task added, but ${addFailedUploads.length} attachment(s) failed. `;
    el.appendChild(msg);
    const retry = document.createElement("button");
    retry.type = "button";
    retry.className = "muted";
    retry.textContent = "Retry";
    retry.addEventListener("click", async () => {
        if (!addCreatedTaskId) return;
        setAddStatus("Retrying…", "pending");
        const stillFailed = [];
        for (const f of addFailedUploads) {
            try { await api.addAttachment(addCreatedTaskId, f); }
            catch (err) { stillFailed.push(f); }
        }
        addFailedUploads = stillFailed;
        if (stillFailed.length) {
            renderAddUploadFailureBanner();
        } else {
            setAddStatus("All uploads complete ✓", "success");
            setTimeout(() => {
                addCreatedTaskId = null;
                hideAddTask();
            }, 600);
        }
    });
    el.appendChild(retry);
    const dismiss = document.createElement("button");
    dismiss.type = "button";
    dismiss.className = "muted";
    dismiss.textContent = "Dismiss";
    dismiss.style.marginLeft = "6px";
    dismiss.addEventListener("click", () => {
        addFailedUploads = [];
        addCreatedTaskId = null;
        hideAddTask();
    });
    el.appendChild(dismiss);
}

function populateDefaults() {
    const addForm = $("#add-task-form");
    if (!addForm.starts_at.value && !addForm.is_all_day.checked) {
        addForm.starts_at.value = smartDefaultStart();
    }
    if (!addForm.due_at.value && !addForm.is_all_day.checked) {
        const startBase = addForm.starts_at.value || smartDefaultStart();
        addForm.due_at.value = smartDefaultDue(startBase);
    }
}

// Reset the volatile fields between opens so a cancel-then-reopen lands
// in a clean state. Sticky fields (class) are preserved on purpose;
// title is left alone (always empty after reset).
function resetVolatileFields() {
    const addForm = $("#add-task-form");
    addForm.title.value = "";
    addForm.is_all_day.checked = false;
    addForm.rrule.value = "";
    addForm.rrule_until.value = "";
    addForm.starts_at.value = "";
    addForm.due_at.value = "";
    addForm.tag_id.value = "";
    addForm.notes.value = "";
    syncRruleVisibility();
    syncAllDay();
}

async function applyDefaultClass() {
    const addForm = $("#add-task-form");
    // Only set when user hasn't already picked / a sticky value isn't set.
    if (addForm.class_id.value) return;
    const def = await defaultClassId();
    // Some users have no classes (Personal-only) — leave the default ""
    // (which means Personal in the add-task form).
    if (def) addForm.class_id.value = def;
}

export async function showAddTask() {
    showSecondary("#add-task-view");
    await ensureLookups({ force: true });
    resetVolatileFields();
    addAlerts = [];
    addPendingFiles = [];
    addFailedUploads = [];
    addCreatedTaskId = null;
    renderAlertsChips();
    renderAttachmentsList();
    await applyDefaultClass();
    populateDefaults();
    setAddStatus("", "");
    const t = $("#add-task-form").querySelector("input[name='title']");
    if (t) { t.focus(); t.select(); }
}

// Entry point from month-view day cards / empty strips. Pre-fills both
// dates with the picked day. starts_at = 9am, due_at = 10am — same
// "default block" the web app uses for new events on a non-today day.
export async function showAddTaskForDay(yyyymmdd) {
    showSecondary("#add-task-view");
    await ensureLookups({ force: true });
    resetVolatileFields();
    addAlerts = [];
    addPendingFiles = [];
    addFailedUploads = [];
    addCreatedTaskId = null;
    renderAlertsChips();
    renderAttachmentsList();
    await applyDefaultClass();
    const addForm = $("#add-task-form");
    const { start, due } = smartDefaultsForDay(yyyymmdd);
    addForm.starts_at.value = start;
    addForm.due_at.value = due;
    setAddStatus("", "");
    const t = addForm.querySelector("input[name='title']");
    if (t) { t.focus(); t.select(); }
}

export function hideAddTask() {
    returnToList();
}

function renderAlertsChips() {
    const chips = $("#add-alerts-chips");
    const hidden = $("#add-alerts-value");
    if (!chips || !hidden) return;
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
        const lab = document.createElement("span");
        lab.textContent = alertLabel(m);
        const rm = document.createElement("button");
        rm.type = "button";
        rm.className = "alert-chip-remove";
        rm.setAttribute("aria-label", `Remove ${lab.textContent}`);
        rm.textContent = "×";
        rm.addEventListener("click", () => {
            addAlerts = addAlerts.filter((x) => x !== m);
            renderAlertsChips();
        });
        chip.appendChild(lab);
        chip.appendChild(rm);
        chips.appendChild(chip);
    });
}

function renderAttachmentsList() {
    const ul = $("#add-attachments-list");
    if (!ul) return;
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
            renderAttachmentsList();
        });
        li.appendChild(name);
        li.appendChild(rm);
        ul.appendChild(li);
    });
}

function syncAllDay() {
    const addForm = $("#add-task-form");
    const on = addForm.is_all_day.checked;
    const due = addForm.due_at;
    const starts = addForm.starts_at;
    if (on) {
        // Stash the time portion BEFORE slicing it off so a later
        // toggle-off can restore the user's original time instead of
        // defaulting to 17:00.
        if (due.value && due.type === "datetime-local") {
            due.dataset.lastTime = due.value.slice(11, 16) || "17:00";
            due.value = due.value.slice(0, 10);
        }
        due.type = "date";
        // An all-day task must have a date — default Due to today when
        // empty so we never create a date-less, hard-to-manage task.
        if (!due.value) due.value = formatLocalDate(new Date());
        if (starts) {
            if (starts.value && starts.type === "datetime-local") {
                starts.dataset.lastTime = starts.value.slice(11, 16) || "09:00";
            }
            // Keep the start value — an all-day task may span multiple days
            // (start date → due date). Only a Repeat clears it (see
            // syncStartsDisabled). Just drop the time component.
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
    const addForm = $("#add-task-form");
    const showing = !!addForm.rrule.value;
    $("#add-until-label").hidden = !showing;
    if (!showing) addForm.rrule_until.value = "";
    else updateRruleUntilMin();
    syncStartsDisabled();
}

// Constrain the rrule_until picker so the user can't pick a date on or
// before Due. Recurrence with End ≤ Due renders zero occurrences and
// silently disappears — that's a UX trap, so we make it impossible at
// the picker level. Called whenever Due changes, all-day toggles, or
// rrule shows.
function updateRruleUntilMin() {
    const addForm = $("#add-task-form");
    const due = addForm.due_at;
    const until = addForm.rrule_until;
    if (!due || !until) return;
    const dueVal = due.value;
    if (!dueVal) {
        // No Due → no constraint on End-date. Edge case (smart defaults
        // pre-fill Due, but be defensive).
        until.removeAttribute("min");
        return;
    }
    let minVal;
    if (due.type === "date") {
        // All-day: Due is YYYY-MM-DD. Earliest valid End = the next day
        // at 00:00. rrule_until is always datetime-local.
        const d = new Date(dueVal + "T00:00:00");
        d.setDate(d.getDate() + 1);
        minVal = formatLocal(d);
    } else {
        // datetime-local: Due is YYYY-MM-DDTHH:MM. Earliest valid End =
        // Due + 1 minute (datetime-local granularity).
        const d = new Date(dueVal);
        d.setMinutes(d.getMinutes() + 1);
        minVal = formatLocal(d);
    }
    until.min = minVal;
    // If the current value is now below the new min, clear it — keeps
    // form state legal.
    if (until.value && until.value < minVal) until.value = "";
}

function syncStartsDisabled() {
    const addForm = $("#add-task-form");
    // Only a Repeat disables Starts-on (rrule + range are mutually
    // exclusive). All-day does NOT — an all-day task can span a date range.
    const hasRrule = !!addForm.rrule.value;
    const disabled = hasRrule;
    const starts = addForm.starts_at;
    const label = $("#add-starts-label");
    if (starts) {
        starts.disabled = disabled;
        if (disabled) starts.value = "";
    }
    if (label) label.classList.toggle("disabled", disabled);
}

export function bindAddTask() {
    const addForm = $("#add-task-form");
    const fab = $("#add-task-fab");
    if (fab) fab.addEventListener("click", showAddTask);

    addForm.is_all_day.addEventListener("change", () => {
        syncAllDay();
        updateRruleUntilMin();
    });
    addForm.rrule.addEventListener("change", syncRruleVisibility);
    // Due changes → re-tighten the End-date floor so the picker can't
    // offer a date <= Due. `input` event fires on every keystroke /
    // picker pick, not just blur.
    addForm.due_at.addEventListener("input", updateRruleUntilMin);

    const adder = $("#add-alerts-add");
    if (adder) {
        adder.addEventListener("change", () => {
            const v = adder.value;
            if (!v) return;
            addAlerts.push(parseInt(v, 10));
            renderAlertsChips();
            adder.value = "";
        });
    }

    const fileInput = $("#add-attachments-input");
    if (fileInput) {
        fileInput.addEventListener("change", (e) => {
            const result = addFilesToBuffer(e.target.files, addPendingFiles);
            fileInput.value = "";  // allow re-picking same file
            renderAttachmentsList();
            if (result.error) setAddStatus(result.error, "error");
            else if (result.replaced) setAddStatus(`Replaced ${result.replaced} file(s).`, "success");
            else setAddStatus("", "");
        });
    }

    $("#add-task-back").addEventListener("click", hideAddTask);
    $("#add-task-cancel").addEventListener("click", hideAddTask);

    addForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const submitBtn = addForm.querySelector("button[type='submit']");
        // Re-entrancy guard: if the previous submit is still in flight,
        // a double-tap or fast Enter would create the task twice.
        if (submitBtn && submitBtn.disabled) return;
        const title = (addForm.title.value || "").trim();
        if (!title) {
            setAddStatus("Title required.", "error");
            addForm.title.focus();
            return;
        }
        // Smart-default fallback: if the user manually cleared Due (or
        // somehow opened with no Due), backfill at submit-time so a task
        // always has a date. Picks the next 30-min mark for time, or
        // Starts + 1h when Starts is set.
        if (!addForm.due_at.value && !addForm.is_all_day.checked && !addForm.rrule.value) {
            const startBase = addForm.starts_at.value || smartDefaultStart();
            addForm.due_at.value = smartDefaultDue(startBase);
        }
        const due = addForm.due_at.value;
        const starts = addForm.starts_at.value;
        if (starts && due && starts > due) {
            setAddStatus("Cannot save, the start date must be before the end date.", "error");
            return;
        }
        if (addForm.tag_id.value === "__new__") {
            setAddStatus("Pick a tag (or finish creating the new one).", "error");
            return;
        }
        const fd = new FormData();
        fd.append("title", title);
        if (due) fd.append("due_at", due);
        if (starts) fd.append("starts_at", starts);
        if (addForm.is_all_day.checked) fd.append("is_all_day", "1");
        if (addForm.rrule.value) fd.append("rrule", addForm.rrule.value);
        // rrule_until is meaningful ONLY when paired with an rrule. Even
        // if a stale value lingers in the (hidden) input, never send it
        // without rrule — the server would silently store nonsense state.
        if (addForm.rrule.value && addForm.rrule_until.value) {
            fd.append("rrule_until", addForm.rrule_until.value);
        }
        if (addForm.tag_id.value && addForm.tag_id.value !== "__new__") {
            fd.append("tag_id", addForm.tag_id.value);
        }
        // Always send alerts (even empty) so the server treats blank as
        // "user picked no reminders" instead of falling back to smart
        // defaults — matches `_create_task_for_user`'s alerts handling.
        fd.append("alerts", $("#add-alerts-value").value || "");
        if (addForm.notes.value.trim()) fd.append("notes", addForm.notes.value);

        const classId = addForm.class_id.value;
        setAddStatus("Adding…", "pending");
        if (submitBtn) submitBtn.disabled = true;
        try {
            const created = classId
                ? await api.addClassTask(classId, fd)
                : await api.addPersonalTask(fd);
            // Upload any pending attachments now that we have the new id.
            // Track failures so the user can Retry without re-picking.
            const failed = [];
            if (created && created.id && addPendingFiles.length) {
                for (const f of addPendingFiles) {
                    try { await api.addAttachment(created.id, f); }
                    catch (err) {
                        console.error("attachment upload failed:", err);
                        failed.push(f);
                    }
                }
            }
            if (failed.length && created && created.id) {
                addFailedUploads = failed;
                addCreatedTaskId = created.id;
                addPendingFiles = [];
                renderAttachmentsList();
                renderAddUploadFailureBanner();
                await load();
                return;
            }
            // Clear the form for next FAB tap, but keep the class
            // selection sticky — most rapid-fire adds stay in the same class.
            const stickyClass = addForm.class_id.value;
            addForm.reset();
            addForm.class_id.value = stickyClass;
            addAlerts = [];
            addPendingFiles = [];
            renderAlertsChips();
            renderAttachmentsList();
            syncRruleVisibility();
            syncAllDay();
            await load();
            hideAddTask();
            showToast("Task added ✓", "success");
        } catch (err) {
            if (err instanceof NotAuthenticated) {
                showLogin();
                hideAddTask();
                return;
            }
            setAddStatus("Couldn't add: " + err.message, "error");
        } finally {
            if (submitBtn) submitBtn.disabled = false;
        }
    });
}
