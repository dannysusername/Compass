// Add-class surface — small form for code + name. View-swap from the
// Classes tab's "+ Add class" button.

import { api, NotAuthenticated } from "../api.js";
import { isOfflineError, queueUpsert } from "../sync.js";
import { resetCaches } from "../state.js";
import { showLogin, showSecondary, returnToList } from "../nav.js";
import { ensureLookups } from "../lookups.js";
import { load } from "../views/index.js";

const $ = (sel) => document.querySelector(sel);

export function showAddClass() {
    showSecondary("#add-class-view");
    setStatus("", "");
    const f = $("#add-class-form");
    f.reset();
    f.code.focus();
}

function hideAddClass() { returnToList(); }

function setStatus(text, kind) {
    const el = $("#add-class-status");
    if (!text) { el.hidden = true; return; }
    el.textContent = text;
    el.className = "status " + (kind || "");
    el.hidden = false;
}

export function bindAddClass() {
    const f = $("#add-class-form");
    f.addEventListener("submit", async (e) => {
        e.preventDefault();
        const code = (f.code.value || "").trim();
        const name = (f.name.value || "").trim();
        if (!code || !name) return;
        setStatus("Adding…", "pending");
        try {
            await api.createClass({ code, name });
            // Bust caches so dropdowns + Classes list re-fetch.
            resetCaches();
            f.reset();
            setStatus("", "");
            hideAddClass();
            await ensureLookups();
            await load();
        } catch (err) {
            if (err instanceof NotAuthenticated) { showLogin(); hideAddClass(); return; }
            if (isOfflineError(err)) {
                // Queue the create (mirror + pending) so it syncs on reconnect;
                // stay on the surface with a confirmation. Mirrors the web
                // class-actions.js offline path.
                await queueUpsert("classes", { code: code.toUpperCase(), name });
                resetCaches();
                f.reset();
                setStatus("Saved offline — will sync", "");
                return;
            }
            setStatus("Couldn't add: " + err.message, "error");
        }
    });
    $("#add-class-back").addEventListener("click", hideAddClass);
    $("#add-class-cancel").addEventListener("click", hideAddClass);
}
