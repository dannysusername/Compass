// Top-level dispatcher. setView() switches the active tab and reloads;
// load() reads state.currentView and routes to the right per-view loader.

import { state } from "../state.js";
import { returnToList, resetSavedScroll } from "../nav.js";
import { loadToday } from "./today.js";
import { loadMonth } from "./month.js";
import { loadClasses } from "./classes.js";

export function setView(view) {
    if (view !== "today" && view !== "month" && view !== "classes") return;
    state.currentView = view;
    document.querySelectorAll(".view-tab").forEach((btn) => {
        btn.classList.toggle("active", btn.dataset.view === view);
    });
    // Different tab = different content; the saved scroll from the prior
    // tab no longer applies. Reset before returnToList runs.
    resetSavedScroll();
    returnToList();
    load();
}

export async function load() {
    if (state.currentView === "month") return loadMonth();
    if (state.currentView === "classes") return loadClasses();
    return loadToday();
}

export function bindViewTabs() {
    document.querySelectorAll(".view-tab").forEach((btn) => {
        btn.addEventListener("click", () => setView(btn.dataset.view));
    });
}
