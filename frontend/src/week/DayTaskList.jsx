import TaskListDnd from "../shared/TaskListDnd.jsx";

// Week day-modal task list. Thin wrapper over the shared TaskListDnd that
// persists per-day item order to /tasks/reorder-day (so reordering a multi-day
// task on one day doesn't disturb the others). Cross-class moves and class
// reordering are handled by the shared component.
function isOffline(err) {
  return !!(window.CompassSync && window.CompassSync.isOffline(err));
}

export default function DayTaskList({ day, onToggle, onDelete, onReorder, onEdit, onReload }) {
  async function persistOrder(items) {
    const body = { day: day.date, items };
    try {
      const r = await fetch("/tasks/reorder-day", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify(body),
      });
      if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
    } catch (err) {
      if (isOffline(err)) {
        await window.CompassSync.queueRequest("/tasks/reorder-day", body, { json: true });
      } else {
        console.error("reorder failed:", err);
      }
    }
  }

  return (
    <TaskListDnd
      buckets={day.buckets}
      onToggle={onToggle}
      onDelete={onDelete}
      onEdit={onEdit}
      onReorderBuckets={(newBuckets) => onReorder(day.date, newBuckets)}
      onPersistOrder={persistOrder}
      onReload={onReload}
    />
  );
}
