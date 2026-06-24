import { useEffect, useRef, useState } from "react";
import { toLocalInput, alertLabel, normalizeAlerts, ALERT_PRESETS } from "./format.js";
import ModalPortal from "./ModalPortal.jsx";

// The add/edit-task modal (full field set). One component serves both modes:
//   - add  (mode="add"):  POST /tasks or /classes/{id}/tasks, then reload.
//   - edit (mode="edit"): POST /tasks/{id}/edit (partial update), then reload.
// Fields: title, start/due dates, all-day, class, tags (+ inline new tag),
// repeat (+ end date), reminders, attachments, notes. Shared by the Week and
// Today islands.

export default function TaskModal({
  mode,
  date,
  task,
  classes,
  tags,
  onTagCreated,
  onManageTags,
  onClose,
  onSaved,
  defaultClassId,
}) {
  const editing = mode === "edit";
  const [title, setTitle] = useState(editing ? task.title || "" : "");
  const [tagId, setTagId] = useState(editing && task.tag_id ? String(task.tag_id) : "");
  // Inline "new tag" sub-form state.
  const [showNewTag, setShowNewTag] = useState(false);
  const [newTagName, setNewTagName] = useState("");
  const [newTagColor, setNewTagColor] = useState("#a83232");
  const [startsAt, setStartsAt] = useState(
    editing ? toLocalInput(task.starts_at) : `${date}T09:00`
  );
  const [dueAt, setDueAt] = useState(editing ? toLocalInput(task.due_at) : `${date}T10:00`);
  const [isAllDay, setIsAllDay] = useState(editing ? !!task.is_all_day : false);
  const [classId, setClassId] = useState(
    editing
      ? task.class_id
        ? String(task.class_id)
        : "0"
      : defaultClassId
        ? String(defaultClassId)
        : "0"
  );
  const [notes, setNotes] = useState(editing ? task.notes || "" : "");
  // Repeat rule + its optional end date. rrule is on the item; rrule_until is
  // not, so for edits we pull both from /tasks/{id}/details.json below.
  const [rrule, setRrule] = useState(editing ? task.rrule || "" : "");
  const [rruleUntil, setRruleUntil] = useState("");
  // Reminder offsets (minutes before). `touched` gates whether ADD sends them
  // at all — an untouched add lets the server apply smart defaults by tag.
  const [alerts, setAlerts] = useState([]);
  const [alertsTouched, setAlertsTouched] = useState(false);
  // Attachments. In edit mode `attachments` is the live server list (uploads/
  // deletes hit the server immediately). In add mode there's no task id yet, so
  // files are buffered in `pendingFiles` and uploaded after the task is created.
  const [attachments, setAttachments] = useState([]);
  const [pendingFiles, setPendingFiles] = useState([]);
  const [busy, setBusy] = useState(false);
  const downOnBackdrop = useRef(false);

  // On edit, fetch the detail bundle (authoritative rrule + rrule_until from
  // the base task, kept off the lightweight grid item).
  useEffect(() => {
    if (!editing) return;
    let cancelled = false;
    fetch(`/tasks/${task.id}/details.json`, { headers: { Accept: "application/json" } })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (cancelled || !d) return;
        setRrule(d.rrule || "");
        setRruleUntil(d.rrule_until || "");
        setAlerts(normalizeAlerts(d.alerts || []));
        setAttachments(d.attachments || []);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [editing, task]);

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

  async function handleSubmit(e) {
    e.preventDefault();
    if (!title.trim() || busy) return;

    // Reject a backwards range before hitting the server (parity with the
    // legacy todo.js form). Compare the displayed values: all-day uses the
    // date-only portion so a same-day all-day task isn't falsely flagged.
    const sCmp = isAllDay ? startsAt.slice(0, 10) : startsAt;
    const dCmp = isAllDay ? dueAt.slice(0, 10) : dueAt;
    if (sCmp && dCmp && sCmp > dCmp) {
      alert("Cannot save event, the start date must be before the end date");
      return;
    }

    setBusy(true);

    // Field-sending rules mirror the legacy form: dates only when set; the
    // rest always-send in EDIT (so the user can clear them via the partial-
    // update endpoint) but only-when-set in ADD (let create-time defaults run).
    const body = new FormData();
    body.append("title", title.trim());
    if (startsAt) body.append("starts_at", startsAt);
    if (dueAt) body.append("due_at", dueAt);

    if (editing) body.append("notes", notes);
    else if (notes.trim()) body.append("notes", notes.trim());

    if (editing) body.append("is_all_day", isAllDay ? "1" : "");
    else if (isAllDay) body.append("is_all_day", "1");

    if (editing) body.append("class_id", classId); // "0" = Personal

    if (editing) body.append("tag_id", tagId); // "" clears
    else if (tagId) body.append("tag_id", tagId);

    if (editing) body.append("rrule", rrule); // "" = doesn't repeat
    else if (rrule) body.append("rrule", rrule);

    // End date only matters with a repeat rule; send "" otherwise so an edit
    // clears any stale UNTIL.
    const untilToSend = rrule ? rruleUntil : "";
    if (editing) body.append("rrule_until", untilToSend);
    else if (untilToSend) body.append("rrule_until", untilToSend);

    // Reminders: always in edit (empty = no alerts); in add only if the user
    // touched them, so an untouched add gets the server's smart defaults.
    if (editing) body.append("alerts", alerts.join(","));
    else if (alertsTouched) body.append("alerts", alerts.join(","));

    const url = editing
      ? `/tasks/${task.id}/edit`
      : classId === "0"
        ? "/tasks"
        : `/classes/${classId}/tasks`;

    try {
      const r = await fetch(url, { method: "POST", body, headers: { Accept: "application/json" } });
      if (!r.ok) {
        let detail = `${r.status} ${r.statusText}`;
        try {
          const j = await r.json();
          if (j.detail) detail = j.detail;
        } catch {
          /* non-JSON error body */
        }
        throw new Error(detail);
      }
      // Add mode: now that the task exists, upload any buffered files.
      if (!editing && pendingFiles.length) {
        const created = await r.json().catch(() => null);
        if (created && created.id) {
          for (const f of pendingFiles) {
            const afd = new FormData();
            afd.append("file", f);
            try {
              await fetch(`/tasks/${created.id}/attachments`, {
                method: "POST",
                body: afd,
                headers: { Accept: "application/json" },
              });
            } catch {
              /* one failure shouldn't block the rest */
            }
          }
        }
      }
      await onSaved(); // parent reloads the month, then closes us
    } catch (err) {
      if (window.CompassSync && window.CompassSync.isOffline(err)) {
        await window.CompassSync.queueUpsert("tasks", {
          ...(editing ? { id: task.id } : {}),
          title: title.trim(),
          due_at: dueAt || null,
          starts_at: startsAt || null,
          is_all_day: isAllDay,
          notes: notes.trim() || null,
          class_id: classId === "0" ? null : Number(classId),
          rrule: rrule || "",
          tag_id: tagId || null,
        });
        alert("Saved offline — it'll sync when you reconnect.");
        onClose();
      } else {
        alert(`Could not ${editing ? "save" : "add"} task: ${err.message}`);
        setBusy(false);
      }
    }
  }

  async function handleCreateTag() {
    const name = newTagName.trim();
    if (!name) return;
    if (!/^#[0-9a-fA-F]{6}$/.test(newTagColor)) {
      alert("Color must be a #rrggbb hex value.");
      return;
    }
    const body = new FormData();
    body.append("name", name);
    body.append("color", newTagColor);
    try {
      const r = await fetch("/tags", { method: "POST", body, headers: { Accept: "application/json" } });
      if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
      const tag = await r.json();
      onTagCreated(tag); // joins the parent's tag list
      setTagId(String(tag.id)); // ...and select it
      setShowNewTag(false);
      setNewTagName("");
    } catch (err) {
      alert("Could not create tag: " + err.message);
    }
  }

  async function handleFileChange(e) {
    const f = e.target.files && e.target.files[0];
    e.target.value = ""; // allow re-selecting the same file
    if (!f) return;
    if (editing) {
      const fd = new FormData();
      fd.append("file", f);
      try {
        const r = await fetch(`/tasks/${task.id}/attachments`, {
          method: "POST",
          body: fd,
          headers: { Accept: "application/json" },
        });
        if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
        const att = await r.json();
        setAttachments((cur) => [...cur, att]);
      } catch (err) {
        alert("Could not upload: " + err.message);
      }
    } else {
      setPendingFiles((cur) => [...cur, f]);
    }
  }

  async function handleDeleteAttachment(att) {
    const label = att.original_name || att.filename;
    if (!window.confirm(`Remove "${label}"?`)) return;
    try {
      const r = await fetch(`/attachments/${att.id}/delete`, {
        method: "POST",
        headers: { Accept: "application/json" },
      });
      if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
      setAttachments((cur) => cur.filter((x) => x.id !== att.id));
    } catch (err) {
      alert("Could not remove: " + err.message);
    }
  }

  function handleTagSelect(e) {
    const v = e.target.value;
    if (v === "__new__") {
      setShowNewTag(true);
    } else {
      setShowNewTag(false);
      setTagId(v);
    }
  }

  const systemTags = tags.filter((t) => t.is_system);
  const yourTags = tags.filter((t) => !t.is_system);
  const startsValue = isAllDay ? startsAt.slice(0, 10) : startsAt;
  const dueValue = isAllDay ? dueAt.slice(0, 10) : dueAt;

  return (
    <ModalPortal>
    <div
      className="react-modal-overlay"
      onMouseDown={(e) => {
        downOnBackdrop.current = e.target === e.currentTarget;
      }}
      onClick={(e) => {
        if (e.target === e.currentTarget && downOnBackdrop.current) onClose();
      }}
    >
      <div className="modal-dialog" role="dialog" aria-label={editing ? "Edit task" : "Add a task"}>
        <div className="modal-head">
          <h2>{editing ? "Edit task" : "Add a task"}</h2>
          <button type="button" className="modal-close" onClick={onClose} aria-label="Close">
            ×
          </button>
        </div>
        <form className="add-task-form" onSubmit={handleSubmit}>
          <label className="add-task-title">
            <span className="add-task-label">Task</span>
            {/* eslint-disable-next-line jsx-a11y/no-autofocus */}
            <input type="text" value={title} onChange={(e) => setTitle(e.target.value)} required autoFocus />
          </label>
          <label className={`add-task-starts${rrule ? " disabled" : ""}`}>
            <span className="add-task-label">Starts on (optional)</span>
            <input
              type={isAllDay ? "date" : "datetime-local"}
              value={startsValue}
              onChange={(e) => setStartsAt(e.target.value)}
              disabled={!!rrule}
            />
          </label>
          <label className="add-task-due">
            <span className="add-task-label">Due (optional)</span>
            <input
              type={isAllDay ? "date" : "datetime-local"}
              value={dueValue}
              onChange={(e) => setDueAt(e.target.value)}
            />
          </label>
          <label className="add-task-allday">
            <input type="checkbox" checked={isAllDay} onChange={(e) => setIsAllDay(e.target.checked)} />
            <span>All day</span>
          </label>
          <label className="add-task-class">
            <span className="add-task-label">Class</span>
            <select value={classId} onChange={(e) => setClassId(e.target.value)}>
              <option value="0">Personal (no class)</option>
              {classes.length > 0 && <option disabled>──────────</option>}
              {classes.map((c) => (
                <option key={c.id} value={String(c.id)}>
                  {c.code} — {c.name}
                </option>
              ))}
            </select>
          </label>
          <div className="add-task-tag">
            <span className="add-task-label">
              Tag (optional){" "}
              {onManageTags && (
                <button type="button" className="link-btn" onClick={onManageTags}>
                  Manage tags
                </button>
              )}
            </span>
            <select value={showNewTag ? "__new__" : tagId} onChange={handleTagSelect}>
              <option value="">No tag</option>
              {systemTags.length > 0 && (
                <optgroup label="System">
                  {systemTags.map((t) => (
                    <option key={t.id} value={String(t.id)}>
                      {t.name}
                    </option>
                  ))}
                </optgroup>
              )}
              {yourTags.length > 0 && (
                <optgroup label="Yours">
                  {yourTags.map((t) => (
                    <option key={t.id} value={String(t.id)}>
                      {t.name}
                    </option>
                  ))}
                </optgroup>
              )}
              <option value="__new__">+ New tag…</option>
            </select>
            {showNewTag && (
              <div className="new-tag-form">
                <input
                  type="text"
                  placeholder="Tag name (e.g. Reading)"
                  value={newTagName}
                  onChange={(e) => setNewTagName(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault(); // don't submit the outer task form
                      handleCreateTag();
                    }
                  }}
                />
                <input
                  type="color"
                  value={newTagColor}
                  onChange={(e) => setNewTagColor(e.target.value)}
                />
                <button type="button" className="small" onClick={handleCreateTag}>
                  Create
                </button>
                <button
                  type="button"
                  className="secondary small"
                  onClick={() => setShowNewTag(false)}
                >
                  Cancel
                </button>
              </div>
            )}
          </div>
          <label className="add-task-rrule">
            <span className="add-task-label">Repeat</span>
            <select
              value={rrule}
              onChange={(e) => {
                const v = e.target.value;
                setRrule(v);
                // rrule and a start/range are mutually exclusive: choosing a
                // repeat clears (and below, disables) Starts-on. Parity with
                // the legacy form's bindRruleVisibility.
                if (v) setStartsAt("");
              }}
            >
              <option value="">Doesn&rsquo;t repeat</option>
              <option value="FREQ=DAILY">Daily</option>
              <option value="FREQ=WEEKLY">Weekly</option>
              <option value="FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR">Every weekday (Mon–Fri)</option>
              <option value="FREQ=MONTHLY">Monthly</option>
            </select>
          </label>
          {rrule && (
            <label className="add-task-rrule-until">
              <span className="add-task-label">End date (optional)</span>
              <input
                type="datetime-local"
                value={rruleUntil}
                onChange={(e) => setRruleUntil(e.target.value)}
              />
            </label>
          )}
          <div className="add-task-alerts">
            <span className="add-task-label">Reminders</span>
            <div className="alerts-chips">
              {alerts.map((m) => (
                <span className="alert-chip" key={m}>
                  <span>{alertLabel(m)}</span>
                  <button
                    type="button"
                    className="alert-chip-remove"
                    aria-label={`Remove ${alertLabel(m)} reminder`}
                    onClick={() => {
                      setAlerts((cur) => cur.filter((x) => x !== m));
                      setAlertsTouched(true);
                    }}
                  >
                    ×
                  </button>
                </span>
              ))}
            </div>
            <div className="alerts-add">
              <select
                value=""
                onChange={(e) => {
                  if (!e.target.value) return;
                  setAlerts((cur) => normalizeAlerts([...cur, parseInt(e.target.value, 10)]));
                  setAlertsTouched(true);
                }}
              >
                <option value="">+ Add reminder…</option>
                {ALERT_PRESETS.map(([m, label]) => (
                  <option key={m} value={m}>
                    {label}
                  </option>
                ))}
              </select>
            </div>
          </div>
          <div className="add-task-attachments">
            <span className="add-task-label">Attachments</span>
            <ul className="attachments-list">
              {editing
                ? attachments.map((att) => (
                    <li className="attachment-row" key={att.id}>
                      <span className="attachment-name">{att.original_name || att.filename}</span>
                      <button
                        type="button"
                        className="attachment-remove"
                        aria-label={`Remove ${att.original_name || att.filename}`}
                        onClick={() => handleDeleteAttachment(att)}
                      >
                        ×
                      </button>
                    </li>
                  ))
                : pendingFiles.map((f, idx) => (
                    <li className="attachment-row" key={idx}>
                      <span className="attachment-name">{f.name}</span>
                      <button
                        type="button"
                        className="attachment-remove"
                        aria-label={`Remove ${f.name}`}
                        onClick={() => setPendingFiles((cur) => cur.filter((_, i) => i !== idx))}
                      >
                        ×
                      </button>
                    </li>
                  ))}
            </ul>
            <label className="attachments-upload">
              <input type="file" onChange={handleFileChange} />
              <span className="link-btn">+ Add file</span>
            </label>
          </div>
          <label className="add-task-notes">
            <span className="add-task-label">Notes (optional)</span>
            <textarea
              rows="4"
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="Anything extra — links, reminders…"
            />
          </label>
          <div className="modal-actions">
            <button type="submit" disabled={busy}>
              {busy ? "Saving…" : editing ? "Save" : "Add task"}
            </button>
            <button type="button" className="secondary" onClick={onClose}>
              Cancel
            </button>
          </div>
        </form>
      </div>
    </div>
    </ModalPortal>
  );
}
