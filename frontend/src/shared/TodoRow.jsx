import { useState } from "react";
import { timeOf, isClockTime, weekdayAbbr } from "./format.js";

// Shared task/event row. Presentational + the row-level interactions (toggle
// circle, expandable drawer with Edit/Delete). Drag wiring is optional (passed
// in by a sortable wrapper) so the same row works in read-only and draggable
// lists. `isOverdue` adds the overdue styling + an "Overdue" cap in the drawer.

function When({ item }) {
  if (item.is_all_day) return <span className="todo-when">All day</span>;
  if (item.is_range) {
    return (
      <span className="todo-range">
        {weekdayAbbr(item.starts_at)} {timeOf(item.starts_at)} →{" "}
        {weekdayAbbr(item.due_at)} {timeOf(item.due_at)}
      </span>
    );
  }
  if (item.due_at && isClockTime(item.due_at)) {
    return <span className="todo-when">{timeOf(item.due_at)}</span>;
  }
  return null;
}

export default function TodoRow({
  item,
  onToggle,
  onDelete,
  onEdit,
  isOverdue = false,
  innerRef,
  style,
  dragHandleProps,
}) {
  const [open, setOpen] = useState(false);
  // Edit is offered for tasks only (events aren't editable). onEdit may be
  // absent in read-only contexts.
  const canEdit = onEdit && item.kind === "task";
  const color = item.tag_color || item.sub_kind_color;
  const classes = [
    "todo-row",
    item.completed ? "done" : "",
    color ? "has-tag" : "",
    item.is_range_day ? "is-range-day" : "",
    item.actionable === false ? "is-context" : "",
    isOverdue ? "is-overdue" : "",
  ].filter(Boolean).join(" ");
  const mergedStyle = { ...(color ? { "--tag-color": color } : {}), ...style };

  return (
    <li ref={innerRef} className={classes} style={mergedStyle}>
      <div
        className="todo-row-main"
        role="button"
        tabIndex={0}
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            setOpen((o) => !o);
          }
        }}
      >
        <span className="todo-drag-handle" title="Drag to reorder" {...dragHandleProps}>
          <span className="todo-burger" />
        </span>
        <button
          type="button"
          className="todo-toggle"
          aria-pressed={item.completed ? "true" : "false"}
          aria-label="Toggle done"
          onClick={(e) => {
            e.stopPropagation();
            onToggle(item);
          }}
        >
          <span className="todo-circle" />
        </button>
        <span className="todo-title">{item.title}</span>
        <When item={item} />
        {item.tag_name && (
          <span className="todo-tag" style={{ "--tag-color": item.tag_color }}>
            {item.tag_name}
          </span>
        )}
        {item.sub_kind && (
          <span
            className="todo-tag"
            style={item.sub_kind_color ? { "--tag-color": item.sub_kind_color } : undefined}
          >
            {item.sub_kind}
          </span>
        )}
      </div>
      {open && (
        <div className="todo-drawer">
          {isOverdue && <div className="todo-drawer-overdue">Overdue</div>}
          {item.notes && item.notes.trim() && <div className="todo-notes">{item.notes}</div>}
          <div className="todo-drawer-actions">
            {canEdit && (
              <button type="button" className="todo-edit" onClick={() => onEdit(item)}>
                ✎ Edit
              </button>
            )}
            <button type="button" className="todo-del" onClick={() => onDelete(item)}>
              × Delete
            </button>
          </div>
        </div>
      )}
    </li>
  );
}
