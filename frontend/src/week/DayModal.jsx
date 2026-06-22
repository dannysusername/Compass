import { useEffect, useRef } from "react";
import DayTaskList from "./DayTaskList.jsx";

// The day-detail modal shell. It owns the overlay/dialog, close behavior
// (close button, backdrop click, Escape) and scroll-lock; the task list inside
// — display, toggle/delete (layer 3), and drag (layer 4) — lives in
// DayTaskList. Uses its OWN overlay class so global modal.js leaves it alone.

function dayLabel(dateStr) {
  const s = dateStr.slice(0, 10);
  const d = new Date(+s.slice(0, 4), +s.slice(5, 7) - 1, +s.slice(8, 10));
  return d.toLocaleDateString(undefined, { weekday: "long", month: "short", day: "numeric" });
}

export default function DayModal({ day, onClose, onToggle, onDelete, onReorder, onAddTask, onEdit, onReload }) {
  const downOnBackdrop = useRef(false);

  useEffect(() => {
    document.body.classList.add("modal-open");
    const onKey = (e) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.body.classList.remove("modal-open");
      document.removeEventListener("keydown", onKey);
    };
  }, [onClose]);

  const buckets = day.buckets || [];

  return (
    <div
      className="react-modal-overlay"
      onMouseDown={(e) => {
        downOnBackdrop.current = e.target === e.currentTarget;
      }}
      onClick={(e) => {
        if (e.target === e.currentTarget && downOnBackdrop.current) onClose();
      }}
    >
      <div className="modal-dialog" role="dialog" aria-label={dayLabel(day.date)}>
        <div className="modal-head">
          <h2>{dayLabel(day.date)}</h2>
          <button type="button" className="modal-close" onClick={onClose} aria-label="Close">
            ×
          </button>
        </div>
        <div className="modal-body">
          <button
            type="button"
            className="add-task-btn"
            onClick={() => onAddTask(day.date)}
          >
            + Add task for this day
          </button>
          {buckets.length === 0 ? (
            <p className="empty">Nothing scheduled this day.</p>
          ) : (
            <DayTaskList
              day={day}
              onToggle={onToggle}
              onDelete={onDelete}
              onReorder={onReorder}
              onEdit={onEdit}
              onReload={onReload}
            />
          )}
        </div>
      </div>
    </div>
  );
}
