// Side-panel entrypoint. Boots the panel, decides login-vs-app once via
// /me.json, wires every surface's one-shot listeners, then hands off to
// the per-view modules under lib/.
//
// Module map (see SPEC.md for the full layout):
//   lib/api.js              fetch wrapper + auth detection
//   lib/state.js            shared module-scope state
//   lib/nav.js              view-swap helpers
//   lib/util.js             formatters + datetime helpers
//   lib/lookups.js          class + tag dropdown population
//   lib/views/*             today / month / classes / login / index dispatcher
//   lib/forms/*             add-task / edit-task / event-editor / add-class / settings / syllabus
//   lib/behaviors/*         drag / recurring-sheet / tag-inline / row-actions / timezone

import { api, NotAuthenticated } from "./lib/api.js";
import { state } from "./lib/state.js";
import { showApp, showLogin, bindEscape } from "./lib/nav.js";
import { ensureLookups } from "./lib/lookups.js";
import { autoSaveTimezone } from "./lib/behaviors/timezone.js";
import { bindDrag } from "./lib/behaviors/drag.js";
import { bindRecurringSheet } from "./lib/behaviors/recurring-sheet.js";
import { bindLogin, setLoginStatus } from "./lib/views/login.js";
import { bindViewTabs, load } from "./lib/views/index.js";
import { bindClassDetail } from "./lib/views/classes.js";
import { bindAddTask } from "./lib/forms/add-task.js";
import { bindEditTask } from "./lib/forms/edit-task.js";
import { bindEventEditor } from "./lib/forms/event-editor.js";
import { bindAddClass } from "./lib/forms/add-class.js";
import { bindSettings } from "./lib/forms/settings.js";
import { bindSyllabus } from "./lib/forms/syllabus.js";
import { syncNow, cacheComputedView, getComputedView } from "./lib/sync.js";

const $ = (sel) => document.querySelector(sel);

// ---- One-shot wires that don't fit any per-surface module --------------

function wireHeader() {
    // Refresh button: re-runs whatever load() the current view fires.
    // Was wired in the HTML but had no JS handler — the audit-fix.
    const refreshBtn = $("#refresh-btn");
    if (refreshBtn) refreshBtn.addEventListener("click", () => load());
}

function wireFooter() {
    // "Open Compass →" link: opens the configured base URL in a new tab.
    // Was wired in HTML but had no JS handler.
    const openApp = $("#open-app");
    if (openApp) {
        openApp.addEventListener("click", async (e) => {
            e.preventDefault();
            const url = await api.base();
            chrome.tabs.create({ url });
        });
    }
    // "Edit in Compass" link inside the editor (kept as a fallback even
    // though the editor now handles alerts + attachments natively).
    const editOpenApp = $("#edit-open-app");
    if (editOpenApp) {
        editOpenApp.addEventListener("click", async (e) => {
            e.preventDefault();
            const url = await api.base();
            chrome.tabs.create({ url });
        });
    }
}

function bindAll() {
    bindLogin();
    bindViewTabs();
    bindAddTask();
    bindEditTask();
    bindEventEditor();
    bindAddClass();
    bindSettings();
    bindSyllabus();
    bindClassDetail();
    bindRecurringSheet();
    bindDrag();
    bindEscape();
    wireHeader();
    wireFooter();
}

// ---- Boot --------------------------------------------------------------

async function boot() {
    // Wire every surface's listeners ONCE — they're idempotent (one
    // form, one submit handler) so it's safe to call before /me.json
    // resolves.
    bindAll();
    // When the connection comes back, flush any queued offline edits and
    // re-render with fresh server data.
    window.addEventListener("online", () => {
        syncNow().then(() => load()).catch(() => {});
    });
    try {
        const me = await api.me();
        if (!me) { showLogin(); return; }
        state.me = me;
        cacheComputedView("me", me).catch(() => {});  // for offline boot
        showApp();
        const footer = $("#logged-in-as");
        if (footer && me.email) footer.textContent = me.email;
        // Lookups + timezone fire in parallel so the panel comes up fast.
        await Promise.all([ensureLookups(), autoSaveTimezone()]);
        await load();
    } catch (err) {
        if (err instanceof NotAuthenticated) { showLogin(); return; }
        // Offline / unreachable: a real 401 is handled above. If we have a
        // cached identity from a prior online boot, run the app OFFLINE
        // against the local mirror instead of stranding the user on login.
        const cachedMe = await getComputedView("me").catch(() => null);
        if (cachedMe) {
            state.me = cachedMe;
            showApp();
            const footer = $("#logged-in-as");
            if (footer && cachedMe.email) footer.textContent = cachedMe.email;
            ensureLookups().catch(() => {});
            await load();  // Today falls back to its cached view
            return;
        }
        // Never synced — can't do anything offline; let the user fix the URL.
        showLogin();
        setLoginStatus("Couldn't reach Compass: " + err.message, "error");
    }
}

boot();
