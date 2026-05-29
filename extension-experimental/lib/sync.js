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
const isTempId = (v) => typeof v === "string" && v.startsWith("tmp-");

// Create/update a row locally (optimistic) + queue it for the next push.
// `kind` is the server/store plural key: tasks|classes|tags|events. A row
// with no id is new: gets a temp id locally + a client_id in the queued
// change so push() can map it to the real server id.
//
// Repeated writes to the SAME row — including editing a task that was created
// offline and only has a temp id — MERGE into one pending change (keyed by
// localId) + merge onto the mirror row. Without this, creating a task offline
// then editing it replayed as several brand-new tasks on reconnect (the
// duplicate-rows bug): each edit coerced the temp id to NaN and stacked a
// fresh create.
export async function queueUpsert(kind, data) {
    const raw = data.id;
    const isNew = raw == null;
    const isTemp = isTempId(raw);
    const localId = isNew ? tempId() : (isTemp ? raw : Number(raw));
    const updated_at = new Date().toISOString();
    // Mirror: merge onto the existing row so a partial edit keeps other fields.
    const rows = await getAll(kind);
    const prev = rows.find((r) => String(r.id) === String(localId)) || {};
    const row = { ...prev, ...data, id: localId, updated_at };
    await putAll(kind, [row]);
    // Pending: collapse create + later edits/toggles into ONE change per row.
    const change = { ...data, updated_at };
    if (isNew || isTemp) { delete change.id; change.client_id = localId; }
    else { change.id = localId; }
    const pending = await getAll("pending");
    const existing = pending.find((p) => p.op === "upsert" && String(p.localId) === String(localId));
    if (existing) {
        existing.data = { ...existing.data, ...change };
        if (existing.data.client_id) delete existing.data.id;  // stays a create
        await tx("pending", "readwrite", (s) => s.put(existing));
    } else {
        await tx("pending", "readwrite", (s) =>
            s.put({ op: "upsert", kind, data: change, localId }));
    }
    return row;
}

export async function queueDelete(kind, id) {
    if (typeof id === "string" && id.startsWith("tmp-")) {
        // never synced to the server — just drop its queued upsert(s).
        await del(kind, id);
        const all = await getAll("pending");
        const uids = all.filter((p) => p.localId === id).map((p) => p.uid);
        return tx("pending", "readwrite", (s) => { uids.forEach((u) => s.delete(u)); });
    }
    const nid = Number(id);
    await del(kind, nid);
    return tx("pending", "readwrite", (s) => s.put({ op: "delete", kind, id: nid }));
}

// Back-compat task helpers.
export const queueTaskUpsert = (data) => queueUpsert("tasks", data);
export const queueTaskDelete = (id) => queueDelete("tasks", id);

// tag_id for the offline queue: '' / '__new__' → null; a temp id stays a
// string (so the server resolves a just-created-offline tag in the same push);
// a real numeric id is coerced to a number. Keeps a temp tag id from becoming
// NaN (the same corruption that caused the historical duplicate-task bug).
export function tagIdForQueue(v) {
    if (!v || v === "__new__") return null;
    return /^tmp-/.test(v) ? v : Number(v);
}

// ---- Push ----
export async function push() {
    const pending = await getAll("pending");
    if (!pending.length) return { id_map: {} };
    const requests = pending.filter((p) => p.op === "request");
    const syncable = pending.filter((p) => p.op !== "request");
    const changes = {};
    const deletes = {};
    for (const p of syncable) {
        const k = p.kind === "task" ? "tasks" : p.kind;  // tolerate legacy singular
        if (p.op === "delete") (deletes[k] = deletes[k] || []).push(p.id);
        else (changes[k] = changes[k] || []).push(p.data);
    }
    const res = syncable.length ? await api.syncPush({ changes, deletes }) : { id_map: {} };
    // Replay raw form POSTs (recurring exclude / end-after) verbatim — the
    // server does the date math identically to the online path.
    for (const p of requests) {
        if (p.json) await api.postJson(p.url, p.body || {});
        else await api.postForm(p.url, p.body || {});
    }
    // Drop temp rows whose server id we now know — the next pull brings the
    // canonical server row. A temp id could live in any store.
    for (const clientId of Object.keys(res.id_map || {})) {
        for (const st of DATA_STORES) await del(st, clientId);
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

// A network failure (offline) vs a 401 (auth) or HTTP error (server
// rejected): fetch() rejects with a TypeError when there's no connection.
export function isOfflineError(err) {
    return !navigator.onLine || (err && err.name === "TypeError");
}

async function _patchToday(mutate) {
    const view = await getComputedView("today");
    if (!view) return;
    mutate(view);
    await cacheComputedView("today", view);
}

const _isTask = (it, id) => it.kind === "task" && String(it.id) === String(id);
const _isItem = (it, domKind, id) => it.kind === domKind && String(it.id) === String(id);
const _serverKind = (domKind) => (domKind === "event" ? "events" : "tasks");

// Kind-aware toggle/delete (works for task AND event rows). domKind is the
// row's singular kind ("task"/"event"); the queue uses the plural server key.
export async function offlineToggleItem(domKind, id, completed) {
    await queueUpsert(_serverKind(domKind),
        { id, completed_at: completed ? new Date().toISOString() : null });
    await _patchToday((view) => {
        for (const b of (view.buckets || [])) {
            for (const it of [...(b.items || []), ...(b.overdue_items || [])]) {
                if (_isItem(it, domKind, id)) it.completed = completed;
            }
        }
    });
}

export async function offlineDeleteItem(domKind, id) {
    await queueDelete(_serverKind(domKind), id);
    await _patchToday((view) => {
        for (const b of (view.buckets || [])) {
            b.items = (b.items || []).filter((it) => !_isItem(it, domKind, id));
            b.overdue_items = (b.overdue_items || []).filter((it) => !_isItem(it, domKind, id));
        }
    });
}

// Queue a raw form POST to replay verbatim on reconnect (server-authoritative
// ops whose date math we don't replicate client-side).
export async function queueRequest(url, body, opts) {
    return tx("pending", "readwrite", (s) =>
        s.put({ op: "request", url, body: body || {}, json: !!(opts && opts.json) }));
}

// Offline recurring delete: 'this' (exclude one occurrence) / 'future'
// (end-after). Queues the exact /exclude or /end-after request and drops the
// occurrence from the cached Today view. The server appends the exdate / sets
// UNTIL on reconnect — identical to the online path, no tz/format replication.
export async function offlineRecurringDelete(mode, id, occurrenceAt) {
    const url = mode === "future" ? `/tasks/${id}/end-after` : `/tasks/${id}/exclude`;
    await queueRequest(url, { occurrence_at: occurrenceAt });
    await _patchToday((view) => {
        for (const b of (view.buckets || [])) {
            b.items = (b.items || []).filter((it) => !_isTask(it, id));
            b.overdue_items = (b.overdue_items || []).filter((it) => !_isTask(it, id));
        }
    });
}

const _bucketKey = (classId) =>
    (classId == null || classId === "" || String(classId) === "0") ? 0 : Number(classId);

export async function offlineAddTask(taskData) {
    const row = await queueTaskUpsert(taskData);   // temp id + queued + mirror
    const key = _bucketKey(taskData.class_id);
    const classes = await local.classes();
    const cls = classes.find((c) => Number(c.id) === key);
    const item = {
        kind: "task", id: row.id, title: taskData.title,
        due_at: taskData.due_at || null, starts_at: taskData.starts_at || null,
        completed: false, class_id: key,
        tag_id: taskData.tag_id ?? null, is_all_day: !!taskData.is_all_day,
        rrule: taskData.rrule || "", notes: taskData.notes || "",
    };
    // Optimistically place it in the Today view so the add is visible. A
    // not-actually-today task reconciles away on the next online pull.
    await _patchToday((view) => {
        view.buckets = view.buckets || [];
        let bucket = view.buckets.find((b) => Number(b.class_id ?? 0) === key);
        if (!bucket) {
            bucket = key === 0
                ? { class_id: 0, code: "Personal", name: "", is_personal: true, items: [], overdue_items: [] }
                : { class_id: key, code: cls ? cls.code : "Class", name: cls ? cls.name : "", is_personal: false, items: [], overdue_items: [] };
            view.buckets.push(bucket);
        }
        bucket.items = bucket.items || [];
        bucket.items.push(item);
    });
    return row;
}

export async function offlineMarkTask(id, completed) {
    await queueTaskUpsert({ id, completed_at: completed ? new Date().toISOString() : null });
    await _patchToday((view) => {
        for (const b of (view.buckets || [])) {
            for (const it of [...(b.items || []), ...(b.overdue_items || [])]) {
                if (_isTask(it, id)) it.completed = completed;
            }
        }
    });
}

// Full offline edit of an existing task: queue the changed fields + patch
// the cached Today item so the edit shows + survives a reload. A class_id
// change updates the field in place; bucket placement reconciles on the
// next online pull. (Reminders/alerts are separate rows — not synced here.)
export async function offlineEditTask(id, fields) {
    await queueTaskUpsert({ id, ...fields });
    const keys = ["title", "due_at", "starts_at", "tag_id", "notes",
                  "is_all_day", "rrule", "rrule_until", "class_id"];
    await _patchToday((view) => {
        for (const b of (view.buckets || [])) {
            for (const it of [...(b.items || []), ...(b.overdue_items || [])]) {
                if (_isTask(it, id)) {
                    for (const k of keys) if (k in fields) it[k] = fields[k];
                }
            }
        }
    });
}

export async function offlineDeleteTask(id) {
    await queueTaskDelete(id);  // raw — queueDelete drops the pending create for temp ids
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
