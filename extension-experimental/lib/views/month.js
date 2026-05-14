// Month view — vertical scrollable day list. The redesign:
//   - Busy days render as full cards with class-bucketed items.
//   - Empty days collapse to a thin 1-line dim row (still visible).
//   - Today always renders as a busy card even when empty.
//   - Every day (busy or empty) is tappable: opens Add-task pre-filled
//     with that date.
//   - Top nav: ‹ Month YYYY › + a "Today" button that jumps back to the
//     current month.

import { api, NotAuthenticated } from "../api.js";
import { state } from "../state.js";
import { showLogin } from "../nav.js";
import { renderBucket } from "./row.js";
import { showClassDetail } from "./classes.js";
import { showAddTaskForDay } from "../forms/add-task.js";

const $ = (sel) => document.querySelector(sel);

export async function loadMonth() {
    const target = $("#content");
    target.innerHTML = '<p class="muted loading">Loading…</p>';
    try {
        const data = await api.month(state.currentMonth);
        // Server normalises currentMonth (null → "2026-05"); cache the
        // normalised value so prev/next nav stays anchored.
        state.currentMonth = data.month;
        $("#today-date").textContent = data.label;
        target.innerHTML = "";
        target.appendChild(renderMonthNav(data));
        const list = document.createElement("ol");
        list.className = "month-day-list";
        data.days.forEach((day) => list.appendChild(renderMonthDay(day)));
        target.appendChild(list);
    } catch (err) {
        if (err instanceof NotAuthenticated) showLogin();
        else {
            target.innerHTML = "";
            const p = document.createElement("p");
            p.className = "muted error";
            p.textContent = "Couldn't load: " + err.message;
            target.appendChild(p);
        }
    }
}

function renderMonthNav(data) {
    const nav = document.createElement("div");
    nav.className = "month-nav";

    const prev = document.createElement("button");
    prev.type = "button";
    prev.className = "muted-btn month-nav-btn";
    prev.setAttribute("aria-label", "Previous month");
    prev.textContent = "‹";
    prev.addEventListener("click", () => {
        state.currentMonth = data.prev_month;
        loadMonth();
    });

    const label = document.createElement("span");
    label.className = "month-nav-label";
    label.textContent = data.label;

    const next = document.createElement("button");
    next.type = "button";
    next.className = "muted-btn month-nav-btn";
    next.setAttribute("aria-label", "Next month");
    next.textContent = "›";
    next.addEventListener("click", () => {
        state.currentMonth = data.next_month;
        loadMonth();
    });

    // "Today" button — jumps the nav back to whatever month contains
    // today. Hidden when we're already on the current month so it doesn't
    // sit there as visual clutter on the common case.
    const todayBtn = document.createElement("button");
    todayBtn.type = "button";
    todayBtn.className = "muted-btn month-nav-today";
    todayBtn.textContent = "Today";
    const currentYM = data.today ? data.today.slice(0, 7) : null;
    if (currentYM && data.month === currentYM) todayBtn.hidden = true;
    todayBtn.addEventListener("click", () => {
        state.currentMonth = currentYM;
        loadMonth();
    });

    nav.appendChild(prev);
    nav.appendChild(label);
    nav.appendChild(next);
    nav.appendChild(todayBtn);
    return nav;
}

function renderMonthDay(day) {
    // Busy day = at least one class bucket has items. Today is always
    // rendered as a busy card to anchor the user even if their day is empty.
    const hasItems = !!(day.buckets && day.buckets.length > 0);
    const renderAsCard = hasItems || day.is_today;
    return renderAsCard ? renderBusyCard(day) : renderEmptyStrip(day);
}

function renderBusyCard(day) {
    const li = document.createElement("li");
    li.className = "month-day-card" + (day.is_today ? " is-today" : "");
    li.dataset.dayDate = day.date;

    const head = document.createElement("header");
    head.className = "month-day-head";
    const d = new Date(day.date + "T00:00:00");
    const dow = document.createElement("span");
    dow.className = "month-day-dow";
    dow.textContent = d.toLocaleDateString(undefined, { weekday: "short" });
    const num = document.createElement("span");
    num.className = "month-day-num";
    num.textContent = String(d.getDate());
    head.appendChild(dow);
    head.appendChild(num);
    if (day.is_today) {
        const tag = document.createElement("span");
        tag.className = "month-day-today";
        tag.textContent = "today";
        head.appendChild(tag);
    }
    // "+ on this day" affordance — same gesture as tapping an empty
    // strip, but inside a busy card. Sits at the right edge of the head.
    const add = document.createElement("button");
    add.type = "button";
    add.className = "month-day-add";
    add.setAttribute("aria-label", `Add task on ${day.date}`);
    add.title = "Add task on this day";
    add.textContent = "+";
    add.addEventListener("click", (e) => {
        e.stopPropagation();
        showAddTaskForDay(day.date);
    });
    head.appendChild(add);
    li.appendChild(head);

    if (!day.buckets || day.buckets.length === 0) {
        // Today, but nothing scheduled. Friendly empty state (still
        // tappable via the + above).
        const empty = document.createElement("p");
        empty.className = "muted month-day-empty";
        empty.textContent = "Nothing scheduled.";
        li.appendChild(empty);
    } else {
        day.buckets.forEach((b) => {
            li.appendChild(renderBucket(b, { onClassClick: showClassDetail }));
        });
    }
    return li;
}

function renderEmptyStrip(day) {
    const li = document.createElement("li");
    li.className = "month-day-strip";
    li.dataset.dayDate = day.date;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "month-day-strip-btn";
    btn.setAttribute("aria-label", `Add task on ${day.date}`);
    const d = new Date(day.date + "T00:00:00");
    const dow = document.createElement("span");
    dow.className = "month-day-strip-dow";
    dow.textContent = d.toLocaleDateString(undefined, { weekday: "short" });
    const num = document.createElement("span");
    num.className = "month-day-strip-num";
    num.textContent = String(d.getDate());
    const hint = document.createElement("span");
    hint.className = "month-day-strip-hint";
    hint.textContent = "+ add";
    btn.appendChild(dow);
    btn.appendChild(num);
    btn.appendChild(hint);
    btn.addEventListener("click", () => showAddTaskForDay(day.date));
    li.appendChild(btn);
    return li;
}
