import { useState } from "react";

function isOffline(err) {
  return !!(window.CompassSync && window.CompassSync.isOffline(err));
}

// Shared toggle-done / delete orchestration for the task lists (Week + Today).
// The state shapes differ per page, so the caller injects how to mutate state:
//   - patchItemDone(kind, id, done): optimistically set an item's completed flag
//   - removeItem(kind, id):          optimistically drop an item
//   - reload():                      re-pull from the server
// Returns the two handlers plus the recurring-delete prompt state to render.
export function useTaskMutations({ patchItemDone, removeItem, reload }) {
  const [recurring, setRecurring] = useState(null); // { label, resolve } while asking

  function askRecurring(label) {
    return new Promise((resolve) => setRecurring({ label, resolve }));
  }
  function resolveRecurring(mode) {
    setRecurring((cur) => {
      if (cur) cur.resolve(mode);
      return null;
    });
  }

  async function handleToggle(item) {
    const { kind, id } = item;
    const done = !item.completed;
    patchItemDone(kind, id, done); // optimistic
    const url = kind === "event" ? `/events/${id}/toggle` : `/tasks/${id}/toggle`;
    try {
      const r = await fetch(url, { method: "POST", headers: { Accept: "application/json" } });
      if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
    } catch (err) {
      if (isOffline(err)) {
        await window.CompassSync.queueUpsert(kind === "event" ? "events" : "tasks", {
          id,
          completed_at: done ? new Date().toISOString() : null,
        });
      } else {
        patchItemDone(kind, id, !done); // revert
        console.error("toggle failed:", err);
      }
    }
  }

  async function handleDelete(item) {
    const { kind, id } = item;
    const isRecurring = kind === "task" && !!item.rrule;

    if (isRecurring) {
      const occ = item.due_at || "";
      const label = occ ? new Date(occ).toLocaleDateString() : "";
      const mode = await askRecurring(label);
      if (!mode) return;
      if (mode === "this" || mode === "future") {
        const path = mode === "this" ? "exclude" : "end-after";
        const body = new FormData();
        body.append("occurrence_at", occ);
        try {
          const r = await fetch(`/tasks/${id}/${path}`, {
            method: "POST",
            body,
            headers: { Accept: "application/json" },
          });
          if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
          await reload(); // server changed which dates render
        } catch (err) {
          if (isOffline(err) && !String(id).startsWith("tmp-")) {
            await window.CompassSync.queueRequest(`/tasks/${id}/${path}`, { occurrence_at: occ });
            removeItem(kind, id);
          } else {
            alert("Could not delete: " + err.message);
          }
        }
        return;
      }
      // mode === "all" → standard whole-series delete below.
    } else if (!window.confirm(`Delete this ${kind === "event" ? "event" : "task"}?`)) {
      return;
    }

    const url = kind === "event" ? `/events/${id}/delete` : `/tasks/${id}/delete`;
    removeItem(kind, id); // optimistic
    try {
      const r = await fetch(url, { method: "POST", headers: { Accept: "application/json" } });
      if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
    } catch (err) {
      if (!isRecurring && isOffline(err)) {
        await window.CompassSync.queueDelete(kind === "event" ? "events" : "tasks", id);
      } else {
        alert("Could not delete: " + err.message);
        reload(); // restore the true state
      }
    }
  }

  return { handleToggle, handleDelete, recurring, resolveRecurring };
}
