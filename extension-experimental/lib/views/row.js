// Apple-minimal task/event row. Drag handle, toggle circle, title, time
// label, × delete. Used by Today, Month day-cards, and Class-detail —
// keep the visual vocabulary identical so the row "looks the same"
// everywhere (same rule the web app follows; see CLAUDE.md "Row drawer UX").

import { whenLabel } from "../util.js";
import { showEditor } from "../forms/edit-task.js";
import { showEventEditor } from "../forms/event-editor.js";
import { onToggle, onDelete } from "../behaviors/row-actions.js";

// Overdue = due_at is strictly in the past AND not completed. Used to
// auto-paint past-due rows red regardless of which view is rendering
// them — Today view's overdue bucket sets isOverdue explicitly, but
// Month / Class-detail / class-block views rendered the past task in
// black (silent). User decision: any past-due task is overdue everywhere.
export function isItemOverdue(it) {
    if (!it || it.completed) return false;
    if (!it.due_at) return false;
    return new Date(it.due_at) < new Date();
}

export function renderRow(it, isOverdue) {
    // Promote auto-detected overdue → red styling in every view.
    if (!isOverdue && isItemOverdue(it)) isOverdue = true;
    const li = document.createElement("li");
    li.className = "todo-row";
    li.dataset.kind = it.kind;
    li.dataset.id = String(it.id);
    li.dataset.classId = it.class_id == null ? "0" : String(it.class_id);
    li.dataset.title = it.title;
    li.dataset.dueAt = it.due_at || "";
    li.dataset.startsAt = it.starts_at || "";
    li.dataset.tagId = it.tag_id == null ? "" : String(it.tag_id);
    li.dataset.isAllDay = it.is_all_day ? "1" : "";
    li.dataset.rrule = it.rrule || "";
    li.dataset.notes = it.notes || "";
    if (it.kind === "event") li.dataset.subKind = it.sub_kind || "";
    if (it.completed) li.classList.add("done");
    if (isOverdue) li.classList.add("is-overdue");
    if (it.tag_color || it.sub_kind_color) {
        li.classList.add("has-tag");
        li.style.setProperty("--tag-color", it.tag_color || it.sub_kind_color);
    }

    const main = document.createElement("div");
    main.className = "todo-row-main";
    if (it.kind === "task" || it.kind === "event") {
        main.classList.add("clickable");
        main.addEventListener("click", (e) => {
            // Defensive: ignore clicks bubbling from inner buttons (toggle, ×).
            if (e.target.closest("button")) return;
            // Drag handles begin the gesture on pointerdown — by the time
            // a real click bubbles, the drag binder marks the row
            // 'just-dragged' and we skip the editor open.
            if (li.classList.contains("just-dragged")) {
                li.classList.remove("just-dragged");
                return;
            }
            if (it.kind === "task") showEditor(li);
            else showEventEditor(li);
        });
    }

    const handle = document.createElement("span");
    handle.className = "todo-drag-handle";
    handle.setAttribute("aria-label", "Drag to reorder");
    handle.title = "Drag to reorder";
    main.appendChild(handle);

    const circle = document.createElement("button");
    circle.type = "button";
    circle.className = "todo-circle";
    circle.setAttribute("aria-label", it.completed ? "Mark not done" : "Mark done");
    circle.setAttribute("aria-pressed", it.completed ? "true" : "false");
    circle.addEventListener("click", (e) => {
        e.stopPropagation();
        onToggle(li);
    });
    main.appendChild(circle);

    const title = document.createElement("span");
    title.className = "todo-title";
    title.textContent = it.title;
    main.appendChild(title);

    const when = whenLabel(it);
    if (when) {
        const w = document.createElement("span");
        w.className = "todo-when";
        w.textContent = when;
        main.appendChild(w);
    }

    const del = document.createElement("button");
    del.type = "button";
    del.className = "todo-del";
    del.setAttribute("aria-label", `Delete ${it.title}`);
    del.textContent = "×";
    del.addEventListener("click", (e) => {
        e.stopPropagation();
        onDelete(li);
    });
    main.appendChild(del);

    li.appendChild(main);
    return li;
}

// Class-bucketed group: header (drag handle + clickable code/name) + UL of rows.
// `class_id == 0` is the synthetic Personal bucket — rendered as a non-
// clickable label since there's no /classes/0 endpoint.
export function renderBucket(bucket, options = {}) {
    const { onClassClick } = options;
    const block = document.createElement("section");
    block.className = "class-block";
    block.dataset.classId = String(bucket.class_id ?? 0);

    const head = document.createElement("div");
    head.className = "class-block-head";
    if (bucket.is_personal) head.classList.add("is-personal");
    const dragHandle = document.createElement("span");
    dragHandle.className = "class-block-drag";
    dragHandle.setAttribute("aria-label", "Drag to reorder this class");
    dragHandle.title = "Drag to reorder";
    head.appendChild(dragHandle);
    const isReal = !bucket.is_personal && bucket.class_id > 0;
    const labelTag = isReal ? "button" : "span";
    const label = document.createElement(labelTag);
    label.className = "class-block-label";
    if (isReal) {
        label.type = "button";
        label.setAttribute("aria-label", `Open ${bucket.code} detail`);
        label.addEventListener("click", () => {
            if (onClassClick) onClassClick(bucket.class_id);
        });
    }
    const code = document.createElement("span");
    code.className = "class-code";
    code.textContent = bucket.code;
    label.appendChild(code);
    if (!bucket.is_personal && bucket.name) {
        const name = document.createElement("span");
        name.className = "class-name";
        name.textContent = bucket.name;
        label.appendChild(name);
    }
    head.appendChild(label);
    block.appendChild(head);

    const list = document.createElement("ul");
    list.className = "todo-list";
    (bucket.items || []).forEach((it) => list.appendChild(renderRow(it, false)));
    // Today endpoint emits overdue_items per bucket; month/week omit it,
    // so an empty default keeps the same renderer working everywhere.
    (bucket.overdue_items || []).forEach((it) => list.appendChild(renderRow(it, true)));
    block.appendChild(list);
    return block;
}
