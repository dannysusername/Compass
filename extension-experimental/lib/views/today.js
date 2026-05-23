// Today list. /today.json returns one bucket per class (with overdue
// merged in), already deduped + sorted by the server. We just render.
//
// Offline: the server does the date/rrule/overdue bucketing, so we cache
// the last computed /today.json and replay it when the network is down —
// the user still sees their tasks. A background syncNow() keeps the raw
// local mirror warm for the next slice (offline writes / client render).

import { api, NotAuthenticated } from "../api.js";
import { showLogin } from "../nav.js";
import { formatHeaderDate } from "../util.js";
import { renderBucket } from "./row.js";
import { showClassDetail } from "./classes.js";
import { syncNow, cacheComputedView, getComputedView } from "../sync.js";

const $ = (sel) => document.querySelector(sel);

export async function loadToday() {
    const target = $("#content");
    target.innerHTML = '<p class="muted loading">Loading…</p>';
    try {
        const data = await api.today();
        renderTodayData(target, data, { offline: false });
        // Save last-known-good for offline, and keep the mirror warm.
        cacheComputedView("today", data).catch(() => {});
        syncNow().catch(() => {});
    } catch (err) {
        if (err instanceof NotAuthenticated) { showLogin(); return; }
        // Network/server error → replay the last cached view if we have one.
        const cached = await getComputedView("today").catch(() => null);
        if (cached) {
            renderTodayData(target, cached, { offline: true });
        } else {
            renderError(target, "Couldn't load: " + err.message);
        }
    }
}

function renderTodayData(target, data, { offline }) {
    $("#today-date").textContent = formatHeaderDate(data.today);
    target.innerHTML = "";
    if (offline) target.appendChild(offlineBanner());
    if (!data.buckets || data.buckets.length === 0) {
        renderEmptyInto(target);
        return;
    }
    data.buckets.forEach((b) => {
        target.appendChild(renderBucket(b, { onClassClick: showClassDetail }));
    });
}

function offlineBanner() {
    const div = document.createElement("div");
    div.className = "offline-banner";
    div.textContent = "Offline — showing your last synced tasks.";
    return div;
}

function renderEmptyInto(target) {
    const p = document.createElement("p");
    p.className = "muted empty";
    p.textContent = "Nothing for today. Use Quick Add to capture something.";
    target.appendChild(p);
}

function renderError(target, msg) {
    target.innerHTML = "";
    const p = document.createElement("p");
    p.className = "muted error";
    p.textContent = msg;
    target.appendChild(p);
}
