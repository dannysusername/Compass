import { useCallback, useEffect, useState } from "react";
import TaskListDnd from "../shared/TaskListDnd.jsx";
import RecurringDeleteDialog from "../shared/RecurringDeleteDialog.jsx";
import { useTaskMutations } from "../shared/useTaskMutations.js";
import TaskModal from "../shared/TaskModal.jsx";
import ManageTagsModal from "../shared/ManageTagsModal.jsx";
import { dateOnly } from "../shared/format.js";

// Today list, React island.
//   Layer 1: read-only render from /today.json (today's items + overdue).
//   Layer 2: toggle-done + delete (incl. recurring this/future/all).
//   Layer 3: drag-to-reorder (global /tasks/reorder) + move across classes,
//            via the shared TaskListDnd.
// Add/edit (layer 4, reusing the Week TaskModal) follows.
//
// /today.json gives each bucket `items` (today) + `overdue_items`. We merge
// them into ONE list per bucket (overdue tagged with `_overdue`) so the list is
// a single sortable, matching the legacy single-<ul> layout.

function unify(rawBuckets) {
  return (rawBuckets || []).map((b) => ({
    ...b,
    items: [
      ...b.items.map((it) => ({ ...it, _overdue: false })),
      ...b.overdue_items.map((it) => ({ ...it, _overdue: true })),
    ],
  }));
}

function sameItem(it, kind, id) {
  return it.kind === kind && String(it.id) === String(id);
}
function mapItems(buckets, fn) {
  return buckets.map((b) => ({ ...b, items: b.items.map(fn) }));
}
function withItemDone(buckets, kind, id, done) {
  return mapItems(buckets, (it) => (sameItem(it, kind, id) ? { ...it, completed: done } : it));
}
function withoutItem(buckets, kind, id) {
  return buckets
    .map((b) => ({ ...b, items: b.items.filter((it) => !sameItem(it, kind, id)) }))
    .filter((b) => b.items.length > 0);
}
function isOffline(err) {
  return !!(window.CompassSync && window.CompassSync.isOffline(err));
}

function todayLabel(dateStr) {
  if (!dateStr) return "";
  return dateOnly(dateStr).toLocaleDateString(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
  });
}

export default function TodayApp({ defaultClassId } = {}) {
  // `data` = { today, buckets } with buckets already unified (single items list).
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  // Add/edit form + tag management (mirrors WeekApp).
  const [classes, setClasses] = useState([]);
  const [tags, setTags] = useState([]);
  const [addOpen, setAddOpen] = useState(false);
  const [editTask, setEditTask] = useState(null);
  const [showManageTags, setShowManageTags] = useState(false);

  const load = useCallback(async () => {
    const r = await fetch("/today.json", { credentials: "same-origin" });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const json = await r.json();
    return { today: json.today, buckets: unify(json.buckets) };
  }, []);

  const reload = useCallback(async () => {
    try {
      setData(await load());
    } catch (err) {
      console.error("reload failed:", err);
    }
  }, [load]);

  useEffect(() => {
    let cancelled = false;
    load()
      .then((d) => !cancelled && setData(d))
      .catch((err) => !cancelled && setError(err.message));
    return () => {
      cancelled = true;
    };
  }, [load]);

  // Class + tag lists for the add/edit form — fetched once.
  useEffect(() => {
    let cancelled = false;
    fetch("/classes.json", { credentials: "same-origin" })
      .then((r) => (r.ok ? r.json() : []))
      .then((list) => !cancelled && setClasses(list))
      .catch(() => {});
    fetch("/tags.json", { credentials: "same-origin" })
      .then((r) => (r.ok ? r.json() : []))
      .then((list) => !cancelled && setTags(list))
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  const handleTagCreated = (tag) => setTags((prev) => [...prev, { ...tag, is_system: false }]);
  const handleTagUpdated = (tag) =>
    setTags((prev) => prev.map((t) => (t.id === tag.id ? { ...t, ...tag } : t)));
  const handleTagDeleted = (id) => setTags((prev) => prev.filter((t) => t.id !== id));

  function setBuckets(updater) {
    setData((d) => (d ? { ...d, buckets: updater(d.buckets) } : d));
  }

  const { handleToggle, handleDelete, recurring, resolveRecurring } = useTaskMutations({
    patchItemDone: (kind, id, done) => setBuckets((b) => withItemDone(b, kind, id, done)),
    removeItem: (kind, id) => setBuckets((b) => withoutItem(b, kind, id)),
    reload,
  });

  // Drag: lift the reordered buckets, and persist global task order.
  function handleReorderBuckets(newBuckets) {
    setData((d) => (d ? { ...d, buckets: newBuckets } : d));
  }
  async function persistOrder(items) {
    const body = { items };
    try {
      const r = await fetch("/tasks/reorder", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify(body),
      });
      if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
    } catch (err) {
      if (isOffline(err)) {
        await window.CompassSync.queueRequest("/tasks/reorder", body, { json: true });
      } else {
        console.error("reorder failed:", err);
      }
    }
  }

  if (error) {
    return <div className="today-list-status">Couldn’t load today’s list ({error}).</div>;
  }
  if (!data) {
    return <div className="today-list-status">Loading…</div>;
  }

  const buckets = data.buckets || [];

  return (
    <section className="today-list-block">
      <div className="today-list-head">
        <h2> 
          Today <span className="subtle">{todayLabel(data.today)}</span>
        </h2>
      </div>
      <button type="button" className="add-task-btn" onClick={() => setAddOpen(true)}>
        + Add task
      </button>
      {buckets.length === 0 ? (
        <p className="empty">
          Nothing for today. Click <strong>+ Add task</strong> to add one.
        </p>
      ) : (
        <TaskListDnd
          buckets={buckets}
          onToggle={handleToggle}
          onDelete={handleDelete}
          onEdit={setEditTask}
          onReorderBuckets={handleReorderBuckets}
          onPersistOrder={persistOrder}
          onReload={reload}
        />
      )}
      {recurring && <RecurringDeleteDialog label={recurring.label} onPick={resolveRecurring} />}
      {addOpen && (
        <TaskModal
          mode="add"
          date={data.today}
          defaultClassId={defaultClassId}
          classes={classes}
          tags={tags}
          onTagCreated={handleTagCreated}
          onManageTags={() => setShowManageTags(true)}
          onClose={() => setAddOpen(false)}
          onSaved={async () => {
            setAddOpen(false);
            await reload();
          }}
        />
      )}
      {editTask && (
        <TaskModal
          mode="edit"
          task={editTask}
          classes={classes}
          tags={tags}
          onTagCreated={handleTagCreated}
          onManageTags={() => setShowManageTags(true)}
          onClose={() => setEditTask(null)}
          onSaved={async () => {
            setEditTask(null);
            await reload();
          }}
        />
      )}
      {showManageTags && (
        <ManageTagsModal
          tags={tags}
          onClose={() => setShowManageTags(false)}
          onTagUpdated={handleTagUpdated}
          onTagDeleted={handleTagDeleted}
        />
      )}
    </section>
  );
}
