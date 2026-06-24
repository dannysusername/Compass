import { useEffect, useRef, useState } from "react";
import ModalPortal from "./ModalPortal.jsx";

// Manage existing tags: rename / recolor any tag, delete your own (system tags
// can be renamed/recolored but not deleted). Opened from the "Manage tags" link
// in the task form. Changes flow back up so the task form's tag dropdown stays
// in sync. Uses its own overlay class so modal.js doesn't interfere.

function TagRow({ tag, onUpdated, onDeleted }) {
  const [name, setName] = useState(tag.name);
  const [color, setColor] = useState(tag.color);
  const [busy, setBusy] = useState(false);
  const dirty = name.trim() !== tag.name || color !== tag.color;

  async function save() {
    if (!name.trim() || busy) return;
    setBusy(true);
    const body = new FormData();
    body.append("name", name.trim());
    body.append("color", color);
    try {
      const r = await fetch(`/tags/${tag.id}/edit`, {
        method: "POST",
        body,
        headers: { Accept: "application/json" },
      });
      if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
      const updated = await r.json();
      onUpdated({ ...tag, ...updated });
    } catch (err) {
      alert("Could not save tag: " + err.message);
    } finally {
      setBusy(false);
    }
  }

  async function del() {
    if (!window.confirm(`Delete tag "${tag.name}"? It'll be removed from any tasks using it.`)) return;
    try {
      const r = await fetch(`/tags/${tag.id}/delete`, {
        method: "POST",
        headers: { Accept: "application/json" },
      });
      if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
      onDeleted(tag.id);
    } catch (err) {
      alert("Could not delete tag: " + err.message);
    }
  }

  return (
    <li className={`tag-manage-row ${tag.is_system ? "is-system" : ""}`}>
      <input type="color" value={color} onChange={(e) => setColor(e.target.value)} aria-label="Tag color" />
      <input type="text" value={name} onChange={(e) => setName(e.target.value)} aria-label="Tag name" />
      {tag.is_system && <span className="tag-system-badge">system</span>}
      <div className="tag-manage-actions">
        <button type="button" className="small" disabled={!dirty || busy} onClick={save}>
          Save
        </button>
        {!tag.is_system && (
          <button type="button" className="link-btn danger" onClick={del}>
            Delete
          </button>
        )}
      </div>
    </li>
  );
}

export default function ManageTagsModal({ tags, onClose, onTagUpdated, onTagDeleted }) {
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

  // System tags first, then yours.
  const ordered = [...tags.filter((t) => t.is_system), ...tags.filter((t) => !t.is_system)];

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
      <div className="modal-dialog" role="dialog" aria-label="Manage tags">
        <div className="modal-head">
          <h2>Manage tags</h2>
          <button type="button" className="modal-close" onClick={onClose} aria-label="Close">
            ×
          </button>
        </div>
        <div className="modal-body">
          <p className="subtle">
            System tags can be renamed and recolored but not deleted. Deleting a tag removes it from
            any tasks using it.
          </p>
          <ul className="tag-manage-list">
            {ordered.length === 0 ? (
              <li className="subtle">No tags yet.</li>
            ) : (
              ordered.map((t) => (
                <TagRow key={t.id} tag={t} onUpdated={onTagUpdated} onDeleted={onTagDeleted} />
              ))
            )}
          </ul>
        </div>
      </div>
    </div>
  );
}
