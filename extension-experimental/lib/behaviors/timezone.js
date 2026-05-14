// One-shot timezone auto-save. The web app does this on every page load
// via base.html JS. The side panel is long-lived (one open per tab life),
// so we fire once on boot — fine for the realistic case where a user's tz
// changes when they travel (panel is reopened).
//
// Server short-circuits when the value matches what's stored, so calling
// this on every boot costs at most one cheap DB read.

import { api, NotAuthenticated } from "../api.js";

export async function autoSaveTimezone() {
    let tz = "";
    try {
        tz = Intl.DateTimeFormat().resolvedOptions().timeZone || "";
    } catch (_) { /* old Chromium without resolvedOptions — skip */ }
    if (!tz) return;
    try {
        await api.saveTimezone(tz);
    } catch (err) {
        // 401 means session expired; the next protected fetch will surface
        // it and route the user to login. Quietly swallow other errors —
        // tz is a nicety, not load-bearing.
        if (err instanceof NotAuthenticated) return;
    }
}
