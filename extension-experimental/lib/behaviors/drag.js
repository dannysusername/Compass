// Drag-to-reorder. Two affordances:
//   1. Row drag (every surface): drag handle on each row. Cross-class
//      allowed in today + month; classes drill-down keeps drag inside the
//      source UL (tasks / events stay separated).
//   2. Class-block drag (Today only): drag handle on the class header
//      reorders entire class blocks.
//
// Pointer-based, delegated on document so it survives every load() that
// rebuilds the DOM. No FLIP animation — direct DOM moves are snappy
// enough for the panel's narrow column.

import { api } from "../api.js";
import { state } from "../state.js";
import { isOfflineError, queueRequest, queueUpsert } from "../sync.js";

const DRAG_THRESHOLD = 5;

let dragRow = null;
let dragSourceClassId = null;
let dragScope = null;
let pointerStart = null;
let isDraggingRow = false;

let dragBlock = null;
let blockPointerStart = null;
let isDraggingBlock = false;

function classIdOfList(list) {
    const block = list.closest(".class-block");
    return block ? (block.dataset.classId || "0") : "0";
}

// Drag scope = the DOM element bounding the universe a row can move
// inside of. Determines which reorder endpoint runs on drop.
//   - month: scope = the .month-day-card the row started in. Per-day
//     reorder via /tasks/reorder-day, with cross-class allowed inside the
//     same day (one day-card hosts multiple class-blocks).
//   - classes drill-down: scope = the source UL itself. /tasks/reorder.
//   - today: scope = #content. Cross-class allowed; /tasks/reorder.
function dragScopeFor(row) {
    const monthCard = row.closest(".month-day-card");
    if (monthCard) return { kind: "month", el: monthCard };
    const classDetail = row.closest("#class-detail");
    if (classDetail) return { kind: "classes", el: row.parentNode };
    return { kind: "today", el: document.getElementById("content") };
}

export function bindDrag() {
    document.addEventListener("pointerdown", (e) => {
        const handle = e.target.closest(".todo-drag-handle");
        if (!handle) return;
        const row = handle.closest(".todo-row");
        if (!row) return;
        dragRow = row;
        dragSourceClassId = classIdOfList(row.parentNode);
        dragScope = dragScopeFor(row);
        pointerStart = { x: e.clientX, y: e.clientY };
        isDraggingRow = false;
        e.preventDefault();
    });

    document.addEventListener("pointermove", (e) => {
        if (!dragRow || !pointerStart) return;
        const dx = e.clientX - pointerStart.x;
        const dy = e.clientY - pointerStart.y;
        if (!isDraggingRow && Math.hypot(dx, dy) < DRAG_THRESHOLD) return;
        if (!isDraggingRow) {
            isDraggingRow = true;
            dragRow.classList.add("dragging");
            document.body.classList.add("cards-dragging");
        }
        const lists = dragScope.kind === "classes"
            ? [dragScope.el]
            : Array.from(dragScope.el.querySelectorAll("ul.todo-list"));
        if (lists.length === 0) return;
        let bestList = null;
        let bestDist = Infinity;
        for (const list of lists) {
            const r = list.getBoundingClientRect();
            const dyOut = e.clientY < r.top
                ? r.top - e.clientY
                : (e.clientY > r.bottom ? e.clientY - r.bottom : 0);
            if (dyOut < bestDist) { bestDist = dyOut; bestList = list; }
        }
        if (!bestList) return;
        const rows = Array.from(bestList.querySelectorAll(".todo-row:not(.dragging)"));
        let insertBefore = null;
        for (const target of rows) {
            const rect = target.getBoundingClientRect();
            if (e.clientY < rect.top + rect.height / 2) {
                insertBefore = target;
                break;
            }
        }
        if (insertBefore) bestList.insertBefore(dragRow, insertBefore);
        else bestList.appendChild(dragRow);
        e.preventDefault();
    });

    document.addEventListener("pointerup", async () => {
        if (!dragRow) return;
        const droppedRow = dragRow;
        const wasDragging = isDraggingRow;
        const sourceClassId = dragSourceClassId;
        const scope = dragScope;
        // Clear synchronously so a stray pointermove between drop and the
        // await below can't re-trigger reordering on a row the user
        // already let go of.
        dragRow = null;
        dragSourceClassId = null;
        dragScope = null;
        pointerStart = null;
        isDraggingRow = false;
        droppedRow.classList.remove("dragging");
        document.body.classList.remove("cards-dragging");
        if (!wasDragging) return;
        // Mark this row 'just-dragged' so the row-body click handler that
        // fires on the same pointer gesture doesn't open the editor.
        droppedRow.classList.add("just-dragged");
        await persistDragDrop(droppedRow, sourceClassId, scope);
    });

    // Class-block drag (Today only) — uses the same pointermove/up flow
    // but a separate state machine so the row + block drags don't collide.
    document.addEventListener("pointerdown", (e) => {
        if (state.currentView !== "today") return;
        const handle = e.target.closest(".class-block-drag");
        if (!handle) return;
        const block = handle.closest(".class-block");
        if (!block) return;
        dragBlock = block;
        blockPointerStart = { x: e.clientX, y: e.clientY };
        isDraggingBlock = false;
        e.preventDefault();
    });

    document.addEventListener("pointermove", (e) => {
        if (!dragBlock || !blockPointerStart) return;
        const dy = e.clientY - blockPointerStart.y;
        if (!isDraggingBlock && Math.abs(dy) < DRAG_THRESHOLD) return;
        if (!isDraggingBlock) {
            isDraggingBlock = true;
            dragBlock.classList.add("dragging");
            document.body.classList.add("cards-dragging");
        }
        const blocks = Array.from(
            document.getElementById("content").querySelectorAll(".class-block:not(.dragging)")
        );
        let insertBefore = null;
        for (const target of blocks) {
            const rect = target.getBoundingClientRect();
            if (e.clientY < rect.top + rect.height / 2) {
                insertBefore = target;
                break;
            }
        }
        const parent = dragBlock.parentNode;
        if (insertBefore) parent.insertBefore(dragBlock, insertBefore);
        else parent.appendChild(dragBlock);
        e.preventDefault();
    });

    document.addEventListener("pointerup", async () => {
        if (!dragBlock) return;
        const wasDragging = isDraggingBlock;
        const droppedBlock = dragBlock;
        dragBlock = null;
        blockPointerStart = null;
        isDraggingBlock = false;
        droppedBlock.classList.remove("dragging");
        document.body.classList.remove("cards-dragging");
        if (!wasDragging) return;
        const order = Array.from(
            droppedBlock.parentNode.querySelectorAll(":scope > .class-block")
        ).map((b) => b.dataset.classId).filter((k) => k != null);
        try {
            await api.reorderClasses(order);
        } catch (err) {
            // Offline: class order is a User field (not a push field) — replay
            // the exact request on reconnect.
            if (isOfflineError(err)) await queueRequest("/classes/reorder", { order }, { json: true });
            else console.error("class reorder failed:", err);
        }
    });
}

async function persistDragDrop(row, sourceClassId, scope) {
    // Cross-class move (tasks only — events stay tied to their class).
    if (scope.kind !== "classes" && row.dataset.kind === "task") {
        const newClassId = classIdOfList(row.parentNode);
        if (newClassId !== sourceClassId) {
            const fd = new FormData();
            fd.append("class_id", newClassId === "0" ? "" : newClassId);
            try {
                await api.editTask(row.dataset.id, fd);
                row.dataset.classId = newClassId;
            } catch (err) {
                if (isOfflineError(err) && !String(row.dataset.id).startsWith("tmp-")) {
                    await queueUpsert("tasks",
                        { id: row.dataset.id, class_id: newClassId === "0" ? null : newClassId });
                    row.dataset.classId = newClassId;
                } else {
                    console.error("cross-class move failed:", err);
                }
            }
        }
    }
    // Per-day position override (month) vs global Task.position (today/classes).
    if (scope.kind === "month") {
        const day = scope.el.dataset.dayDate;
        if (!day) return;
        const items = Array.from(scope.el.querySelectorAll(".todo-row"))
            .map((el) => ({ kind: el.dataset.kind, id: parseInt(el.dataset.id, 10) }))
            .filter((it) => (it.kind === "task" || it.kind === "event") && !Number.isNaN(it.id));
        try {
            await api.reorderTasksDay(day, items);
        } catch (err) {
            if (isOfflineError(err)) await queueRequest("/tasks/reorder-day", { day, items }, { json: true });
            else console.error("reorder-day failed:", err);
        }
        return;
    }
    const items = Array.from(scope.el.querySelectorAll(".todo-row"))
        .map((el) => ({ kind: el.dataset.kind, id: parseInt(el.dataset.id, 10) }))
        .filter((it) => (it.kind === "task" || it.kind === "event") && !Number.isNaN(it.id));
    if (items.length === 0) return;
    try {
        await api.reorderTasks(items);
    } catch (err) {
        if (isOfflineError(err)) await queueRequest("/tasks/reorder", { items }, { json: true });
        else console.error("reorder failed:", err);
    }
}
