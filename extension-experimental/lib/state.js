// Shared module-scope state. Imported by every surface so they all read
// from one source of truth. Mutators export setters so callers don't have
// to know which key to bust on a refresh.

export const state = {
    // Last /me.json payload — email, timezone, xAI status, calendar URLs.
    // Settings, login, signup all key off this.
    me: null,

    // Cached fetch promises. Busted (set null) on any mutation that would
    // change them so the next read re-fetches.
    classesPromise: null,
    tagsPromise: null,

    // Current navigation state. setView() in nav.js mutates these; load()
    // in views/index.js reads them to pick the right fetcher.
    currentView: "today",       // "today" | "month" | "classes"
    currentMonth: null,         // YYYY-MM the user is paging through; null = server default
    currentClassId: null,       // class drill-down id; null when not on class-detail
};

export function resetCaches() {
    state.classesPromise = null;
    state.tagsPromise = null;
}

export function clearForLogout() {
    state.me = null;
    state.classesPromise = null;
    state.tagsPromise = null;
    state.currentView = "today";
    state.currentMonth = null;
    state.currentClassId = null;
}
