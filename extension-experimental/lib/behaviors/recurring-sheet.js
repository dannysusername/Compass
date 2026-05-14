// Bottom-sheet recurring-delete picker. Shown when the user X's a row
// whose dataset.rrule is non-empty. Three options match the web app's
// delete dialog: this date / this and future / entire task. Cancel does
// nothing.

const $ = (sel) => document.querySelector(sel);

let pendingRow = null;
let pendingCallback = null;

export function showRecurringSheet(rowEl, callback) {
    pendingRow = rowEl;
    pendingCallback = callback;
    const sheet = $("#rec-delete");
    const prompt = $("#rec-delete-prompt");
    const dueAt = rowEl.dataset.dueAt || "";
    const dateLabel = dueAt
        ? new Date(dueAt).toLocaleDateString(undefined, {
            weekday: "short", month: "short", day: "numeric",
        })
        : "this occurrence";
    if (prompt) prompt.textContent = `Starting ${dateLabel}, what do you want removed?`;
    if (sheet) sheet.hidden = false;
}

export function hideRecurringSheet() {
    const sheet = $("#rec-delete");
    if (sheet) sheet.hidden = true;
    pendingRow = null;
    pendingCallback = null;
}

// Wire sheet buttons once at boot.
export function bindRecurringSheet() {
    const sheet = $("#rec-delete");
    if (!sheet) return;
    sheet.querySelectorAll("button[data-mode]").forEach((btn) => {
        btn.addEventListener("click", async () => {
            const mode = btn.dataset.mode;
            const row = pendingRow;
            const cb = pendingCallback;
            hideRecurringSheet();
            if (!row || mode === "cancel" || !cb) return;
            await cb(row, mode);
        });
    });
}
