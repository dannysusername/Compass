// View-swap helpers. The side panel is too narrow to host overlay modals,
// so every "secondary" surface (editor, add-task, settings, syllabus, etc.)
// hides the list and takes its place. returnToList() is the reset point —
// every tab click and "back" button funnels through it.

import { state } from "./state.js";

const $ = (sel) => document.querySelector(sel);

// Every surface that can be toggled hidden. Listed once so returnToList
// can flip them all without ten if-typeofs.
const SECONDARY_SURFACES = [
    "#editor",
    "#class-detail",
    "#add-task-view",
    "#add-class-view",
    "#event-editor-view",
    "#settings-view",
    "#syllabus-upload-view",
];

// Scroll position of the list view captured at the moment we transitioned
// AWAY from it. Restored on returnToList so the user lands back where
// they were after editing / adding / drilling into class-detail. Only
// captured on the FIRST list→secondary hop (secondary→secondary keeps
// the original anchor) so a deep nav stack still returns cleanly.
let savedListScrollY = 0;

export function showApp() {
    const out = $("#logged-out");
    const su = $("#signup-view");
    const inn = $("#logged-in");
    if (out) out.hidden = true;
    if (su) su.hidden = true;
    if (inn) inn.hidden = false;
}

export function showLogin() {
    const out = $("#logged-out");
    const su = $("#signup-view");
    const inn = $("#logged-in");
    if (out) out.hidden = false;
    if (su) su.hidden = true;
    if (inn) inn.hidden = true;
    const email = document.querySelector("#login-form input[name='email']");
    if (email) email.focus();
}

export function showSignup() {
    const out = $("#logged-out");
    const su = $("#signup-view");
    const inn = $("#logged-in");
    if (out) out.hidden = true;
    if (su) su.hidden = false;
    if (inn) inn.hidden = true;
    const email = document.querySelector("#signup-form input[name='email']");
    if (email) email.focus();
}

export function setFabHidden(hidden) {
    const fab = $("#add-task-fab");
    if (fab) fab.hidden = hidden;
}

// Hide every secondary surface and show the list + FAB. Tab clicks and
// back/cancel buttons go through this. Also clears the class-detail PDF
// iframe so the stream stops.
export function returnToList() {
    const list = $("#content");
    if (list) list.hidden = false;
    SECONDARY_SURFACES.forEach((sel) => {
        const el = $(sel);
        if (el) el.hidden = true;
    });
    const pdf = $("#class-detail-pdf");
    if (pdf) pdf.src = "";
    setFabHidden(false);
    state.currentClassId = null;
    // Restore scroll position the user had when they left the list.
    // Two RAFs because the list went from hidden to visible — the
    // browser needs the layout pass to land before scrollTo has a real
    // page height to scroll within.
    const target = savedListScrollY;
    requestAnimationFrame(() => {
        requestAnimationFrame(() => {
            window.scrollTo({ top: target, left: 0, behavior: "instant" });
        });
    });
}

// Show one secondary surface, hide all others, hide the list, hide the FAB.
// Caller is responsible for any per-surface focus / pre-fill.
export function showSecondary(selector) {
    const list = $("#content");
    // Capture scroll only when the list was actually visible — i.e., this
    // is a list→secondary hop. Secondary→secondary (e.g., class-detail →
    // editor) leaves savedListScrollY pointing at the original list-leave
    // moment, so the eventual returnToList lands at the right place.
    if (list && !list.hidden) {
        savedListScrollY = window.scrollY || document.documentElement.scrollTop || 0;
    }
    if (list) list.hidden = true;
    SECONDARY_SURFACES.forEach((sel) => {
        const el = $(sel);
        if (el) el.hidden = sel !== selector;
    });
    setFabHidden(true);
    // Secondary surfaces should open at the top, not at the previous
    // list's scroll position.
    window.scrollTo({ top: 0, left: 0, behavior: "instant" });
}

// Reset the saved scroll — called by setView() so a tab switch starts
// from the top instead of restoring the previous tab's anchor.
export function resetSavedScroll() {
    savedListScrollY = 0;
}

// Escape key while a secondary surface is open → click the surface's
// back/cancel button to dismiss. Forms handle their own state cleanup
// in their cancel handlers, so we don't have to know which is open.
export function bindEscape() {
    document.addEventListener("keydown", (e) => {
        if (e.key !== "Escape") return;
        // Skip when typing in a textarea-like field — Escape there often
        // means "discard autocomplete" not "close the form". Single-line
        // inputs are fine to escape from.
        const t = e.target;
        if (t && t.tagName === "TEXTAREA") return;
        for (const sel of SECONDARY_SURFACES) {
            const el = $(sel);
            if (el && !el.hidden) {
                const cancel = el.querySelector("button[id$='-back'], button[id$='-cancel']");
                if (cancel) { cancel.click(); e.preventDefault(); }
                return;
            }
        }
    });
}

// Toast notification banner — fixed at the top of the panel for ~3s.
// Used after a successful save (especially when the user has tabbed
// away from the form, so the inline status line is invisible).
let toastTimer = null;
export function showToast(text, kind = "success") {
    let toast = $("#toast");
    if (!toast) {
        toast = document.createElement("div");
        toast.id = "toast";
        toast.className = "toast";
        document.body.appendChild(toast);
    }
    toast.textContent = text;
    toast.className = "toast toast-" + kind;
    toast.hidden = false;
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
        toast.hidden = true;
        toastTimer = null;
    }, 3000);
}
