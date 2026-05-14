// Today list. /today.json returns one bucket per class (with overdue
// merged in), already deduped + sorted by the server. We just render.

import { api, NotAuthenticated } from "../api.js";
import { showLogin } from "../nav.js";
import { formatHeaderDate } from "../util.js";
import { renderBucket } from "./row.js";
import { showClassDetail } from "./classes.js";

const $ = (sel) => document.querySelector(sel);

export async function loadToday() {
    const target = $("#content");
    target.innerHTML = '<p class="muted loading">Loading…</p>';
    try {
        const data = await api.today();
        $("#today-date").textContent = formatHeaderDate(data.today);
        if (!data.buckets || data.buckets.length === 0) {
            renderEmpty(target);
            return;
        }
        target.innerHTML = "";
        data.buckets.forEach((b) => {
            target.appendChild(renderBucket(b, { onClassClick: showClassDetail }));
        });
    } catch (err) {
        if (err instanceof NotAuthenticated) showLogin();
        else renderError(target, "Couldn't load: " + err.message);
    }
}

function renderEmpty(target) {
    target.innerHTML = "";
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
