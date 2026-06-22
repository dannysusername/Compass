import { useCallback, useEffect, useState } from "react";
import DayModal from "./DayModal.jsx";
import RecurringDeleteDialog from "../shared/RecurringDeleteDialog.jsx";
import TaskModal from "../shared/TaskModal.jsx";
import ManageTagsModal from "../shared/ManageTagsModal.jsx";
import { useTaskMutations } from "../shared/useTaskMutations.js";

// The React Week view. Renders the month grid (layer 1), opens a day-detail
// modal (layer 2), and now toggles-done / deletes tasks (layer 3). The grid
// cells and the modal both derive from one `data` state, so a single
// optimistic update is reflected everywhere at once — no manual DOM syncing
// like the legacy todo.js needed.

const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

// --- pure helpers that produce a new `data` with one item changed/removed ---
// React state is immutable: we never mutate `data`, we build a new object so
// React notices the change and re-renders. These walk days → buckets → items.
function sameItem(it, kind, id) {
  return it.kind === kind && String(it.id) === String(id);
}
function mapItems(data, fn) {
  return {
    ...data,
    days: data.days.map((day) => ({
      ...day,
      buckets: day.buckets.map((b) => ({ ...b, items: b.items.map(fn) })),
    })),
  };
}
function withItemDone(data, kind, id, done) {
  return mapItems(data, (it) => (sameItem(it, kind, id) ? { ...it, completed: done } : it));
}
function withoutItem(data, kind, id) {
  return {
    ...data,
    days: data.days.map((day) => ({
      ...day,
      // Drop the item, then drop any class-bucket left empty by the removal.
      buckets: day.buckets
        .map((b) => ({ ...b, items: b.items.filter((it) => !sameItem(it, kind, id)) }))
        .filter((b) => b.items.length > 0),
    })),
  };
}

function cellItems(day) {
  const out = [];
  for (const bucket of day.buckets) {
    for (const item of bucket.items) {
      out.push({ ...item, code: bucket.code });
    }
  }
  return out;
}

function DayCell({ day, index, onOpen }) {
  const dayNum = parseInt(day.date.slice(8, 10), 10);
  const items = cellItems(day);
  const classes = [
    "day-cell",
    day.is_today ? "is-today" : "",
    day.in_month ? "" : "is-out-of-month",
  ].filter(Boolean).join(" ");

  return (
    <section
      className={classes}
      role="button"
      tabIndex={0}
      onClick={() => onOpen(day.date)}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onOpen(day.date);
        }
      }}
    >
      <header className="day-cell-head">
        <span className="day-cell-dayname">
          <span className="day-cell-weekday">{WEEKDAYS[index % 7]}</span>
          <span className="day-cell-date">{dayNum}</span>
        </span>
        {day.is_today && <span className="day-today-tag">today</span>}
      </header>
      {items.length > 0 && (
        <ul className="day-cell-items">
          {items.map((it) => {
            const color = it.tag_color || it.sub_kind_color;
            const itemClasses = [
              "day-cell-item",
              it.completed ? "done" : "",
              color ? "has-tag" : "",
              it.is_range_day ? "is-range-day" : "",
              it.actionable === false ? "is-context" : "",
            ].filter(Boolean).join(" ");
            return (
              <li
                key={`${it.kind}-${it.id}`}
                className={itemClasses}
                style={color ? { "--tag-color": color } : undefined}
              >
                <span className="day-cell-code">{it.code}</span>
                <span className="day-cell-title">{it.title}</span>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

export default function WeekApp({ month }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  // Which day's modal is open, by date string. We look the day up from `data`
  // (below) rather than storing a snapshot, so mutations to `data` flow into
  // the open modal automatically.
  const [openDate, setOpenDate] = useState(null);
  // The user's classes (for the add-task class picker), and the date we're
  // currently adding a task for (null = add modal closed).
  const [classes, setClasses] = useState([]);
  const [tags, setTags] = useState([]);
  const [addDate, setAddDate] = useState(null);
  // The task currently being edited, or null. Edit is offered for
  // non-recurring tasks only (see DayTaskList).
  const [editTask, setEditTask] = useState(null);
  // Whether the Manage Tags modal is open (reached from the task form).
  const [showManageTags, setShowManageTags] = useState(false);

  const qs = month ? `?grid=1&month=${month}` : "?grid=1";

  // Re-pull the month after a server mutation we don't optimistically model
  // (recurring exclude/end-after change which dates render).
  const reload = useCallback(async () => {
    const r = await fetch(`/month.json${qs}`, { credentials: "same-origin" });
    if (r.ok) setData(await r.json());
  }, [qs]);

  useEffect(() => {
    let cancelled = false;
    fetch(`/month.json${qs}`, { credentials: "same-origin" })
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((json) => !cancelled && setData(json))
      .catch((err) => !cancelled && setError(err.message));
    return () => {
      cancelled = true;
    };
  }, [qs]);

  // The class + tag lists for the task form — fetched once.
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

  // A tag created inline in the form joins the list (non-system by definition).
  const handleTagCreated = (tag) => setTags((prev) => [...prev, { ...tag, is_system: false }]);
  // Manage-tags edits/deletes keep the shared list (and thus the form's
  // dropdown) in sync.
  const handleTagUpdated = (tag) =>
    setTags((prev) => prev.map((t) => (t.id === tag.id ? { ...t, ...tag } : t)));
  const handleTagDeleted = (id) => setTags((prev) => prev.filter((t) => t.id !== id));

  // --- mutations ----------------------------------------------------------
  const { handleToggle, handleDelete, recurring, resolveRecurring } = useTaskMutations({
    patchItemDone: (kind, id, done) => setData((d) => withItemDone(d, kind, id, done)),
    removeItem: (kind, id) => setData((d) => withoutItem(d, kind, id)),
    reload,
  });

  // Drag persistence happens inside DayTaskList; this just syncs the reordered
  // (and possibly re-classed) buckets back into the day so the grid cell and a
  // reopened modal agree.
  function handleReorderDay(dayDate, newBuckets) {
    setData((d) => ({
      ...d,
      days: d.days.map((day) =>
        day.date === dayDate ? { ...day, buckets: newBuckets } : day
      ),
    }));
  }

  // Closing the modal is also when we prune any class block left empty by a
  // cross-class drag — keeping it visible while open lets the user change their
  // mind, but it shouldn't linger once they're done with the day.
  function closeModal() {
    setData((d) =>
      openDate
        ? {
            ...d,
            days: d.days.map((day) =>
              day.date === openDate
                ? { ...day, buckets: day.buckets.filter((b) => b.items.length > 0) }
                : day
            ),
          }
        : d
    );
    setOpenDate(null);
  }

  if (error) {
    return <div className="month-grid-status">Couldn’t load the calendar ({error}).</div>;
  }
  if (!data) {
    return <div className="month-grid-status">Loading…</div>;
  }

  const openDay = openDate ? data.days.find((d) => d.date === openDate) || null : null;

  return (
    <>
      <div className="month-weekdays">
        {WEEKDAYS.map((d) => (
          <div key={d}>{d}</div>
        ))}
      </div>
      <div className="month-grid">
        {data.days.map((day, i) => (
          <DayCell key={day.date} day={day} index={i} onOpen={setOpenDate} />
        ))}
      </div>
      {openDay && (
        <DayModal
          day={openDay}
          onClose={closeModal}
          onToggle={handleToggle}
          onDelete={handleDelete}
          onReorder={handleReorderDay}
          onAddTask={setAddDate}
          onEdit={setEditTask}
          onReload={reload}
        />
      )}
      {addDate && (
        <TaskModal
          mode="add"
          date={addDate}
          classes={classes}
          tags={tags}
          onTagCreated={handleTagCreated}
          onManageTags={() => setShowManageTags(true)}
          onClose={() => setAddDate(null)}
          onSaved={async () => {
            setAddDate(null);
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
      {recurring && (
        <RecurringDeleteDialog label={recurring.label} onPick={resolveRecurring} />
      )}
      {showManageTags && (
        <ManageTagsModal
          tags={tags}
          onClose={() => setShowManageTags(false)}
          onTagUpdated={handleTagUpdated}
          onTagDeleted={handleTagDeleted}
        />
      )}
    </>
  );
}
