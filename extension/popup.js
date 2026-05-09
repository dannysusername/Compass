// Popup controller. Two states:
//   - Logged-out: show "Log in to Compass" CTA that opens the main site
//     in a tab. After login, the cookie is set on the Compass origin
//     and the next popup open succeeds.
//   - Logged-in: minimal add-task form. Submit posts to /tasks (or
//     /classes/{id}/tasks for class-scoped) and closes the popup.
//
// Everything routes through lib/api.js, which handles cookies + base URL
// + 401 detection.

import { api, NotAuthenticated } from "./lib/api.js";

const $ = (sel) => document.querySelector(sel);

// ---- View toggles ----
function showLoggedOut() {
    $("#logged-in").hidden = true;
    $("#logged-out").hidden = false;
    api.base().then((url) => { $("#server-url-readout").textContent = url; });
}
function showLoggedIn() {
    $("#logged-out").hidden = true;
    $("#logged-in").hidden = false;
    primeForm();
}

// ---- Wiring (always-on, idempotent) ----
$("#open-login").addEventListener("click", async () => {
    const url = await api.base();
    chrome.tabs.create({ url: url + "/login" });
    window.close();
});
$("#open-options").addEventListener("click", (e) => {
    e.preventDefault();
    chrome.runtime.openOptionsPage();
});
$("#open-app").addEventListener("click", async (e) => {
    e.preventDefault();
    const url = await api.base();
    chrome.tabs.create({ url });
    window.close();
});
$("#open-sidepanel").addEventListener("click", async (e) => {
    e.preventDefault();
    // chrome.sidePanel.open() requires a user gesture. Call it directly
    // from the popup (the click is the gesture); routing through the
    // service worker via sendMessage can lose the gesture token between
    // hops in some Chrome builds. The worker is a fallback if the popup
    // path errors (e.g., older Chrome that doesn't expose .open() to
    // popup contexts).
    const win = await chrome.windows.getCurrent();
    try {
        await chrome.sidePanel.open({ windowId: win.id });
    } catch (_) {
        try { await chrome.runtime.sendMessage({ type: "open_side_panel" }); }
        catch (_) { /* both failed — silent; user can try again */ }
    }
    window.close();
});

// All-day checkbox flips the due input between datetime and date types,
// matching the web app's modal behaviour. Skipped here for simplicity in
// V1 — the server accepts both datetime-local and date strings.
$("#all-day").addEventListener("change", (e) => {
    const due = $("#due-input");
    if (e.target.checked) {
        if (due.value && due.type === "datetime-local") due.value = due.value.slice(0, 10);
        due.type = "date";
    } else {
        if (due.value && due.type === "date") due.value = due.value + "T17:00";
        due.type = "datetime-local";
    }
});

// ---- Form prime ----
async function primeForm() {
    // Default due: today at 5pm, in user's local time.
    const due = $("#due-input");
    if (!due.value) {
        const d = new Date();
        d.setHours(17, 0, 0, 0);
        const pad = (n) => String(n).padStart(2, "0");
        due.value = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T17:00`;
    }
    // Populate class + tag pickers from the server.
    try {
        const [classes, tags] = await Promise.all([api.classes(), api.tags()]);
        const classSel = $("#class-select");
        classes.forEach((c) => {
            const opt = document.createElement("option");
            opt.value = String(c.id);
            opt.textContent = `${c.code} — ${c.name}`;
            classSel.appendChild(opt);
        });
        const tagSel = $("#tag-select");
        tags.forEach((t) => {
            const opt = document.createElement("option");
            opt.value = String(t.id);
            opt.textContent = t.name + (t.is_system ? "" : "");
            tagSel.appendChild(opt);
        });
    } catch (err) {
        if (err instanceof NotAuthenticated) showLoggedOut();
    }
}

// ---- Submit ----
$("#add-task-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const form = e.target;
    const title = form.title.value.trim();
    if (!title) return;
    const classId = form.class_id.value;
    const due = form.due_at.value;
    const fd = new FormData();
    fd.append("title", title);
    if (due) fd.append("due_at", due);
    if (form.is_all_day.checked) fd.append("is_all_day", "1");
    if (form.tag_id.value) fd.append("tag_id", form.tag_id.value);
    if (form.rrule.value) fd.append("rrule", form.rrule.value);
    if (form.notes.value.trim()) fd.append("notes", form.notes.value);

    setStatus("Adding…", "pending");
    try {
        if (classId) await api.addClassTask(classId, fd);
        else await api.addPersonalTask(fd);
        setStatus("Added ✓", "success");
        // Brief flash so user sees it, then close.
        setTimeout(() => window.close(), 350);
    } catch (err) {
        if (err instanceof NotAuthenticated) {
            showLoggedOut();
            return;
        }
        setStatus("Couldn't add: " + err.message, "error");
    }
});

function setStatus(text, kind) {
    const el = $("#status");
    el.textContent = text;
    el.className = "status " + (kind || "");
    el.hidden = false;
}

// ---- Boot ----
(async () => {
    try {
        await api.me();
        showLoggedIn();
    } catch (err) {
        if (err instanceof NotAuthenticated) {
            showLoggedOut();
        } else {
            // Server unreachable — most likely Compass isn't running, or
            // the configured URL is wrong. Treat as logged-out so the
            // user can hit the URL via "Log in" / "Change".
            showLoggedOut();
            $("#logged-out p.muted").textContent =
                "Can't reach Compass. Is the server running?";
        }
    }
})();
