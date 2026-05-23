// Web (PWA) offline write-queue — local-first step 2 for the website.
//
// The site is server-rendered: the service worker serves the last cached
// HTML when offline (offline VIEWING). This adds offline EDITING — task
// writes that fail because we're offline are queued in IndexedDB, replayed
// via POST /sync when the connection returns, and re-applied to the page on
// load so an offline edit survives a reload (the cached HTML doesn't know
// about it). Tasks only, matching the server's /sync push.
//
// Exposes window.CompassSync. todo.js calls into it from its toggle / delete
// / add handlers when a write hits a network error.
(function (global) {
    "use strict";
    const DB_NAME = "compass-web-sync";
    const DB_VERSION = 1;

    let _dbp = null;
    function db() {
        if (_dbp) return _dbp;
        _dbp = new Promise((res, rej) => {
            const r = indexedDB.open(DB_NAME, DB_VERSION);
            r.onupgradeneeded = () => {
                const d = r.result;
                if (!d.objectStoreNames.contains("queue"))
                    d.createObjectStore("queue", { keyPath: "uid", autoIncrement: true });
            };
            r.onsuccess = () => res(r.result);
            r.onerror = () => rej(r.error);
        });
        return _dbp;
    }
    function reqProm(r) { return new Promise((res, rej) => { r.onsuccess = () => res(r.result); r.onerror = () => rej(r.error); }); }
    function tx(mode, fn) {
        return db().then((d) => new Promise((resolve, reject) => {
            const t = d.transaction("queue", mode);
            let out;
            Promise.resolve(fn(t.objectStore("queue"))).then((v) => { out = v; });
            t.oncomplete = () => resolve(out);
            t.onerror = () => reject(t.error);
        }));
    }
    const getQueue = () => tx("readonly", (s) => reqProm(s.getAll()));
    const enqueue = (entry) => tx("readwrite", (s) => s.put(entry));
    const removeMany = (uids) => tx("readwrite", (s) => uids.forEach((u) => s.delete(u)));

    // A network failure (offline) vs a 401 / server HTTP error.
    function isOffline(err) {
        return !navigator.onLine || (err && err.name === "TypeError");
    }

    let _tmp = 0;
    const tempId = () => `tmp-${Date.now()}-${_tmp++}`;

    // Queue an upsert. For a new task pass no id → returns a temp id the
    // caller stamps on its optimistic row so replay can reconcile it.
    async function queueTaskUpsert(data) {
        const isNew = data.id == null;
        const id = isNew ? tempId() : data.id;
        const change = Object.assign({}, data, { updated_at: new Date().toISOString() });
        if (isNew) { delete change.id; change.client_id = id; }
        await enqueue({ op: "upsert", data: change, localId: id });
        return id;
    }
    async function queueTaskDelete(id) {
        if (typeof id === "string" && id.startsWith("tmp-")) {
            // Never synced — just drop its queued upsert(s).
            const all = await getQueue();
            await removeMany(all.filter((e) => e.localId === id).map((e) => e.uid));
            return;
        }
        await enqueue({ op: "delete", id: Number(id) });
    }

    // Replay the queue to the server. Returns the id_map (temp → server id).
    async function replay() {
        const pending = await getQueue();
        if (!pending.length) return { id_map: {} };
        const changes = { tasks: [] };
        const deletes = { tasks: [] };
        for (const e of pending) {
            if (e.op === "delete") deletes.tasks.push(e.id);
            else changes.tasks.push(e.data);
        }
        const r = await fetch("/sync", {
            method: "POST",
            credentials: "same-origin",
            headers: { "Content-Type": "application/json", "Accept": "application/json" },
            body: JSON.stringify({ changes, deletes }),
        });
        if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
        const res = await r.json();
        // Reconcile temp ids on any optimistic rows still in the DOM.
        for (const [clientId, serverId] of Object.entries(res.id_map || {})) {
            document.querySelectorAll(`.todo-row[data-id="${clientId}"]`).forEach((row) => {
                row.dataset.id = String(serverId);
            });
        }
        await removeMany(pending.map((e) => e.uid));
        return res;
    }

    // Re-apply pending toggles/deletes to the freshly-loaded page so an
    // offline edit survives a reload (added rows are left to the next
    // online refresh — injecting a server-styled row offline is brittle).
    async function applyToDom() {
        let pending;
        try { pending = await getQueue(); } catch (_) { return; }
        for (const e of pending) {
            if (e.op === "delete") {
                document.querySelectorAll(`.todo-row[data-id="${e.id}"]`).forEach((r) => r.remove());
            } else if (e.op === "upsert" && e.data && e.data.id != null && "completed_at" in e.data) {
                const done = !!e.data.completed_at;
                document.querySelectorAll(`.todo-row[data-id="${e.data.id}"]`).forEach((row) => {
                    row.classList.toggle("done", done);
                    const c = row.querySelector(".todo-toggle");
                    if (c) c.setAttribute("aria-pressed", done ? "true" : "false");
                });
            }
        }
    }

    // Flush the queue when the connection returns. No reload needed — the
    // optimistic DOM already reflects the edits, and replay() reconciles any
    // temp ids → server ids in place.
    global.addEventListener("online", () => { replay().catch(() => {}); });
    // On load, replay anything left over (online) or re-apply it (offline).
    global.addEventListener("DOMContentLoaded", () => {
        if (navigator.onLine) replay().catch(() => {});
        applyToDom();
    });

    global.CompassSync = { queueTaskUpsert, queueTaskDelete, replay, applyToDom, isOffline, getQueue };
})(window);
