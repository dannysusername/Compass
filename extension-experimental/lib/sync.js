// Local-first sync engine (step 2). Mirrors the user's data in IndexedDB so
// the panel can READ offline, and queues task WRITES to push when back
// online. PULL applies the server's deltas (+ tombstone deletions); PUSH
// sends queued task changes (the server resolves conflicts newest-wins) and
// reconciles temp client ids -> real server ids. Wiring the views to read
// from here / write through queueTask* is the following slice — this module
// is the engine + is unit-tested on its own.

import { api } from "./api.js";

const DB_NAME = "compass-sync";
const DB_VERSION = 1;
const DATA_STORES = ["tasks", "classes", "tags", "events"];
const STORE_FOR = { task: "tasks", class: "classes", tag: "tags", event: "events" };

let _dbPromise = null;
function db() {
    if (_dbPromise) return _dbPromise;
    _dbPromise = new Promise((resolve, reject) => {
        const req = indexedDB.open(DB_NAME, DB_VERSION);
        req.onupgradeneeded = () => {
            const d = req.result;
            for (const s of DATA_STORES) {
                if (!d.objectStoreNames.contains(s)) d.createObjectStore(s, { keyPath: "id" });
            }
            if (!d.objectStoreNames.contains("pending")) {
                d.createObjectStore("pending", { keyPath: "uid", autoIncrement: true });
            }
            if (!d.objectStoreNames.contains("meta")) {
                d.createObjectStore("meta", { keyPath: "key" });
            }
        };
        req.onsuccess = () => resolve(req.result);
        req.onerror = () => reject(req.error);
    });
    return _dbPromise;
}

function reqProm(r) {
    return new Promise((res, rej) => {
        r.onsuccess = () => res(r.result);
        r.onerror = () => rej(r.error);
    });
}

// Run fn(store) inside a transaction; resolve with whatever fn resolves to.
// fn must only issue IDB requests (no awaiting non-IDB promises mid-tx, or
// the tx auto-closes).
function tx(store, mode, fn) {
    return db().then((d) => new Promise((resolve, reject) => {
        const t = d.transaction(store, mode);
        const s = t.objectStore(store);
        let result;
        Promise.resolve(fn(s)).then((r) => { result = r; });
        t.oncomplete = () => resolve(result);
        t.onerror = () => reject(t.error);
        t.onabort = () => reject(t.error);
    }));
}

const getAll = (store) => tx(store, "readonly", (s) => reqProm(s.getAll()));
const putAll = (store, rows) => tx(store, "readwrite", (s) => { (rows || []).forEach((r) => s.put(r)); });
const del = (store, id) => tx(store, "readwrite", (s) => s.delete(id));
const metaGet = (key) => tx("meta", "readonly", (s) => reqProm(s.get(key)).then((r) => (r ? r.value : null)));
const metaSet = (key, value) => tx("meta", "readwrite", (s) => s.put({ key, value }));

// ---- Pull ----
export async function pull() {
    const cursor = await metaGet("cursor");
    const data = await api.syncPull(cursor);
    await putAll("classes", data.classes);
    await putAll("tags", data.tags);
    await putAll("tasks", data.tasks);
    await putAll("events", data.events);
    for (const d of (data.deletions || [])) {
        const store = STORE_FOR[d.kind];
        if (store) await del(store, d.id);
    }
    if (data.server_time) await metaSet("cursor", data.server_time);
    return data;
}

// ---- Local writes (queued) ----
let _tmp = 0;
const tempId = () => `tmp-${Date.now()}-${_tmp++}`;

// Create/update a task locally (optimistic) + queue it for the next push.
// A task with no id is a new one: gets a temp id locally and a client_id in
// the queued change so push() can map it to the server id.
export async function queueTaskUpsert(taskData) {
    const isNew = taskData.id == null;
    const id = isNew ? tempId() : taskData.id;
    const updated_at = new Date().toISOString();
    const row = { ...taskData, id, updated_at };
    await putAll("tasks", [row]);
    const change = { ...taskData, updated_at };
    if (isNew) { delete change.id; change.client_id = id; }
    await tx("pending", "readwrite", (s) =>
        s.put({ op: "upsert", kind: "task", data: change, localId: id }));
    return row;
}

export async function queueTaskDelete(id) {
    await del("tasks", id);
    if (typeof id === "string" && id.startsWith("tmp-")) {
        // never synced to the server — just drop its queued upsert(s).
        const all = await getAll("pending");
        const uids = all.filter((p) => p.localId === id).map((p) => p.uid);
        return tx("pending", "readwrite", (s) => { uids.forEach((u) => s.delete(u)); });
    }
    return tx("pending", "readwrite", (s) => s.put({ op: "delete", kind: "task", id }));
}

// ---- Push ----
export async function push() {
    const pending = await getAll("pending");
    if (!pending.length) return { id_map: {} };
    const changes = { tasks: [] };
    const deletes = { tasks: [] };
    for (const p of pending) {
        if (p.kind !== "task") continue;
        if (p.op === "delete") deletes.tasks.push(p.id);
        else changes.tasks.push(p.data);
    }
    const res = await api.syncPush({ changes, deletes });
    // Drop temp rows whose server id we now know — the next pull brings the
    // canonical server row (its updated_at > our cursor).
    for (const clientId of Object.keys(res.id_map || {})) {
        await del("tasks", clientId);
    }
    await tx("pending", "readwrite", (s) => { pending.forEach((p) => s.delete(p.uid)); });
    return res;
}

// Push local changes first (so the server merges newest-wins), then pull the
// merged result. Throws if offline — the queue is preserved for next time.
export async function syncNow() {
    await push();
    await pull();
}

// Offline-capable reads for the views (next slice wires these in).
export const local = {
    tasks: () => getAll("tasks"),
    classes: () => getAll("classes"),
    tags: () => getAll("tags"),
    events: () => getAll("events"),
    cursor: () => metaGet("cursor"),
    pending: () => getAll("pending"),
};

// Last-known-good cache of a server-COMPUTED view payload (e.g. /today.json),
// keyed by name. The server does the date/rrule/overdue bucketing, so until
// the client can recompute views from the raw mirror, we stash the computed
// response and replay it when offline. Stored in the meta store.
export const cacheComputedView = (key, data) => metaSet("view:" + key, data);
export const getComputedView = (key) => metaGet("view:" + key);

// ---- Offline task writes (optimistic) ----
// Queue the change AND patch the cached Today view so the edit survives a
// reload while still offline. The next online syncNow() pushes the queue
// (newest-wins) and a fresh /today.json reconciles the view.

async function _patchToday(mutate) {
    const view = await getComputedView("today");
    if (!view) return;
    mutate(view);
    await cacheComputedView("today", view);
}

const _isTask = (it, id) => it.kind === "task" && String(it.id) === String(id);

export async function offlineMarkTask(id, completed) {
    await queueTaskUpsert({ id: Number(id), completed_at: completed ? new Date().toISOString() : null });
    await _patchToday((view) => {
        for (const b of (view.buckets || [])) {
            for (const it of [...(b.items || []), ...(b.overdue_items || [])]) {
                if (_isTask(it, id)) it.completed = completed;
            }
        }
    });
}

export async function offlineDeleteTask(id) {
    await queueTaskDelete(Number(id));
    await _patchToday((view) => {
        for (const b of (view.buckets || [])) {
            b.items = (b.items || []).filter((it) => !_isTask(it, id));
            b.overdue_items = (b.overdue_items || []).filter((it) => !_isTask(it, id));
        }
    });
}

// Test-only: wipe every store for a deterministic starting state. Uses the
// module's own (correctly-versioned) connection so it can't race the schema.
export async function _resetForTests() {
    const d = await db();
    const names = [...DATA_STORES, "pending", "meta"];
    return new Promise((resolve, reject) => {
        const t = d.transaction(names, "readwrite");
        names.forEach((n) => t.objectStore(n).clear());
        t.oncomplete = () => resolve();
        t.onerror = () => reject(t.error);
    });
}
