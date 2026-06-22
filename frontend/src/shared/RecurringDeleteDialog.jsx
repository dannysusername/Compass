import { useEffect } from "react";

// Asks which instances of a recurring task to delete. `onPick` is called with
// "this" | "future" | "all" | null (cancel). Mirrors the legacy
// #delete-recurring-modal but is React-owned (its own overlay class so
// modal.js leaves it alone).
export default function RecurringDeleteDialog({ label, onPick }) {
  useEffect(() => {
    document.body.classList.add("modal-open");
    const onKey = (e) => {
      if (e.key === "Escape") onPick(null);
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.body.classList.remove("modal-open");
      document.removeEventListener("keydown", onKey);
    };
  }, [onPick]);

  return (
    <div
      className="react-modal-overlay"
      onClick={(e) => e.target === e.currentTarget && onPick(null)}
    >
      <div className="modal-dialog" role="dialog" aria-label="Delete recurring task">
        <div className="modal-head">
          <h2>Delete recurring task</h2>
          <button type="button" className="modal-close" onClick={() => onPick(null)} aria-label="Close">
            ×
          </button>
        </div>
        <div className="modal-body">
          <p>
            This task repeats. Pick which instances to delete
            {label ? ` starting ${label}` : ""}.
          </p>
          <div className="modal-actions modal-actions-stack">
            <button type="button" onClick={() => onPick("this")}>
              Just this one
            </button>
            <button type="button" onClick={() => onPick("future")}>
              This and all future
            </button>
            <button type="button" onClick={() => onPick("all")}>
              All occurrences
            </button>
            <button type="button" className="secondary" onClick={() => onPick(null)}>
              Cancel
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
