import { useEffect, useRef, useState } from "react";
import {
  DndContext,
  DragOverlay,
  KeyboardSensor,
  PointerSensor,
  closestCorners,
  useDroppable,
  useSensor,
  useSensors,
} from "@dnd-kit/core";
import {
  SortableContext,
  arrayMove,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import TodoRow from "./TodoRow.jsx";

// Shared drag-to-reorder list (dnd-kit), used by the Week day modal and the
// Today list. Tasks reorder within a class and move across classes; events stay
// in their own list and nothing drops into an imported-calendar bucket. Class
// blocks are themselves sortable (drag the grip).
//
// Persistence is split so each caller can target the right endpoint:
//   - onReorderBuckets(newBuckets)  → lift the new order to parent state
//   - onPersistOrder(flatItems)     → caller persists item order (reorder-day
//                                     for Week, reorder for Today)
//   - onReload()                    → re-pull after a class reorder (global)
// Cross-class moves (PATCH class_id) are handled here since they're identical.
// Items may carry `_overdue` (Today) to render the overdue style.

function ClassBlockHead({ bucket }) {
  if (bucket.is_imported) {
    return (
      <div className="class-block-head class-block-head-imported">
        <span className="cal-swatch" style={{ "--cal-color": bucket.color }} />
        <span className="class-code">{bucket.code}</span>
      </div>
    );
  }
  if (bucket.is_personal) {
    return (
      <div className="class-block-head class-block-head-personal">
        <span className="class-code">{bucket.code}</span>
      </div>
    );
  }
  return (
    <a href={`/classes/${bucket.class_id}`} className="class-block-head">
      <span className="class-code">{bucket.code}</span>
      <span className="class-name">{bucket.name}</span>
    </a>
  );
}

function SortableTodoRow({ item, onToggle, onDelete, onEdit }) {
  const id = `${item.kind}-${item.id}`;
  const { attributes, listeners, setNodeRef, setActivatorNodeRef, transform, transition, isDragging } =
    useSortable({ id });
  const style = {
    transform: CSS.Translate.toString(transform),
    transition,
    opacity: isDragging ? 0.4 : undefined,
  };
  return (
    <TodoRow
      item={item}
      onToggle={onToggle}
      onDelete={onDelete}
      onEdit={onEdit}
      isOverdue={!!item._overdue}
      innerRef={setNodeRef}
      style={style}
      dragHandleProps={{ ref: setActivatorNodeRef, ...attributes, ...listeners }}
    />
  );
}

function ClassBlock({ bucket, onToggle, onDelete, onEdit }) {
  const containerId = String(bucket.class_id);
  const { setNodeRef: setDropRef } = useDroppable({ id: containerId });
  const {
    attributes,
    listeners,
    setNodeRef: setSortRef,
    setActivatorNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: `block-${bucket.class_id}` });
  const style = {
    transform: CSS.Translate.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : undefined,
  };
  return (
    <div className="class-block" ref={setSortRef} style={style}>
      <div className="class-block-head-row">
        <span
          className="class-block-drag"
          title="Drag to reorder this class"
          aria-label="Drag class"
          ref={setActivatorNodeRef}
          {...attributes}
          {...listeners}
        >
          <span className="class-block-drag-grip" aria-hidden="true" />
        </span>
        <ClassBlockHead bucket={bucket} />
      </div>
      <SortableContext
        items={bucket.items.map((it) => `${it.kind}-${it.id}`)}
        strategy={verticalListSortingStrategy}
      >
        <ul className="todo-list" ref={setDropRef}>
          {bucket.items.map((it) => (
            <SortableTodoRow
              key={`${it.kind}-${it.id}`}
              item={it}
              onToggle={onToggle}
              onDelete={onDelete}
              onEdit={onEdit}
            />
          ))}
        </ul>
      </SortableContext>
    </div>
  );
}

const itemKey = (it) => `${it.kind}-${it.id}`;
const blockId = (bucket) => `block-${bucket.class_id}`;
function isOffline(err) {
  return !!(window.CompassSync && window.CompassSync.isOffline(err));
}

export default function TaskListDnd({
  buckets: bucketsProp,
  onToggle,
  onDelete,
  onEdit,
  onReorderBuckets,
  onPersistOrder,
  onReload,
}) {
  const [buckets, setBuckets] = useState(bucketsProp);
  useEffect(() => {
    setBuckets(bucketsProp);
  }, [bucketsProp]);

  const [activeItem, setActiveItem] = useState(null);
  const [activeBlock, setActiveBlock] = useState(null);
  const dragSource = useRef(null);

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates })
  );

  function findContainer(id) {
    const s = String(id);
    if (s.startsWith("block-")) return s.slice(6);
    if (buckets.some((b) => String(b.class_id) === s)) return s;
    const b = buckets.find((bk) => bk.items.some((it) => itemKey(it) === s));
    return b ? String(b.class_id) : null;
  }
  const isBlockId = (id) => String(id).startsWith("block-");
  function getItem(id) {
    for (const b of buckets) {
      const it = b.items.find((x) => itemKey(x) === id);
      if (it) return it;
    }
    return null;
  }

  function handleDragStart(event) {
    if (isBlockId(event.active.id)) {
      setActiveBlock(buckets.find((b) => blockId(b) === event.active.id) || null);
      return;
    }
    setActiveItem(getItem(event.active.id));
    dragSource.current = findContainer(event.active.id);
  }

  function handleDragOver(event) {
    const { active, over } = event;
    if (!over || isBlockId(active.id)) return;
    const from = findContainer(active.id);
    const to = findContainer(over.id);
    if (!from || !to || from === to) return;
    const item = getItem(active.id);
    if (!item) return;
    if (item.kind === "event") return;
    if (to.startsWith("imp-")) return;

    setBuckets((prev) => {
      const fromB = prev.find((b) => String(b.class_id) === from);
      const toB = prev.find((b) => String(b.class_id) === to);
      if (!fromB || !toB) return prev;
      const moving = fromB.items.find((it) => itemKey(it) === active.id);
      if (!moving) return prev;
      const overIdx = toB.items.findIndex((it) => itemKey(it) === over.id);
      const insertAt = overIdx === -1 ? toB.items.length : overIdx;
      return prev.map((b) => {
        if (b === fromB) return { ...b, items: b.items.filter((it) => itemKey(it) !== active.id) };
        if (b === toB) {
          const next = [...b.items];
          next.splice(insertAt, 0, moving);
          return { ...b, items: next };
        }
        return b;
      });
    });
  }

  function handleDragEnd(event) {
    const { active, over } = event;
    const wasBlock = isBlockId(active.id);
    setActiveItem(null);
    setActiveBlock(null);
    if (!over) return;

    if (wasBlock) {
      const fromIdx = buckets.findIndex((b) => blockId(b) === active.id);
      const toContainer = findContainer(over.id);
      const toIdx = buckets.findIndex((b) => String(b.class_id) === toContainer);
      if (fromIdx === -1 || toIdx === -1 || fromIdx === toIdx) return;
      const next = arrayMove(buckets, fromIdx, toIdx);
      setBuckets(next);
      persistClassOrder(next);
      return;
    }

    const finalContainer = findContainer(active.id);
    let next = buckets;
    const bIdx = buckets.findIndex((b) => String(b.class_id) === finalContainer);
    if (bIdx !== -1) {
      const items = buckets[bIdx].items;
      const oldIndex = items.findIndex((it) => itemKey(it) === active.id);
      const overIndex = items.findIndex((it) => itemKey(it) === over.id);
      const newIndex = overIndex === -1 ? items.length - 1 : overIndex;
      if (oldIndex !== -1 && oldIndex !== newIndex) {
        next = buckets.map((b, i) =>
          i === bIdx ? { ...b, items: arrayMove(items, oldIndex, newIndex) } : b
        );
      }
    }
    setBuckets(next);

    const movedItem = getItem(active.id);
    const classChanged =
      dragSource.current && finalContainer && dragSource.current !== finalContainer;
    persist(next, movedItem, finalContainer, classChanged);
  }

  async function persist(newBuckets, movedItem, toContainer, classChanged) {
    onReorderBuckets(newBuckets);

    // Cross-class move (tasks only): PATCH just the class_id.
    if (classChanged && movedItem && movedItem.kind === "task") {
      const classId = toContainer === "0" ? "" : toContainer;
      const body = new FormData();
      body.append("class_id", classId);
      try {
        const r = await fetch(`/tasks/${movedItem.id}/edit`, {
          method: "POST",
          body,
          headers: { Accept: "application/json" },
        });
        if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
      } catch (err) {
        if (isOffline(err) && !String(movedItem.id).startsWith("tmp-")) {
          await window.CompassSync.queueUpsert("tasks", {
            id: movedItem.id,
            class_id: toContainer === "0" ? null : toContainer,
          });
        } else {
          console.error("cross-class move failed:", err);
        }
      }
    }

    // Item order — caller persists (Week: reorder-day; Today: reorder).
    const items = newBuckets
      .flatMap((b) => b.items)
      .filter((it) => it.kind === "task" || it.kind === "event")
      .map((it) => ({ kind: it.kind, id: it.id }));
    await onPersistOrder(items);
  }

  // Class order is GLOBAL (User.class_order_json); after persisting, reload so
  // it re-applies everywhere. Imported buckets ("imp-N") are sent too but the
  // server drops them (non-int keys).
  async function persistClassOrder(newBuckets) {
    const order = newBuckets.map((b) => String(b.class_id));
    try {
      const r = await fetch("/classes/reorder", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ order }),
      });
      if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
      if (onReload) await onReload();
    } catch (err) {
      if (isOffline(err)) {
        await window.CompassSync.queueRequest("/classes/reorder", { order }, { json: true });
      } else {
        console.error("class reorder failed:", err);
      }
    }
  }

  return (
    <DndContext
      sensors={sensors}
      collisionDetection={closestCorners}
      onDragStart={handleDragStart}
      onDragOver={handleDragOver}
      onDragEnd={handleDragEnd}
    >
      <SortableContext
        items={buckets.map((b) => `block-${b.class_id}`)}
        strategy={verticalListSortingStrategy}
      >
        <div className="class-block-list">
          {buckets.map((bucket) => (
            <ClassBlock
              key={bucket.class_id}
              bucket={bucket}
              onToggle={onToggle}
              onDelete={onDelete}
              onEdit={onEdit}
            />
          ))}
        </div>
      </SortableContext>
      <DragOverlay>
        {activeItem ? (
          <li className="todo-row" style={activeItem.tag_color ? { "--tag-color": activeItem.tag_color } : undefined}>
            <div className="todo-row-main">
              <span className="todo-drag-handle"><span className="todo-burger" /></span>
              <span className="todo-toggle"><span className="todo-circle" /></span>
              <span className="todo-title">{activeItem.title}</span>
            </div>
          </li>
        ) : activeBlock ? (
          <div className="class-block" style={{ opacity: 0.9 }}>
            <div className="class-block-head-row">
              <span className="class-block-drag">
                <span className="class-block-drag-grip" aria-hidden="true" />
              </span>
              <ClassBlockHead bucket={activeBlock} />
            </div>
          </div>
        ) : null}
      </DragOverlay>
    </DndContext>
  );
}
