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

    // Broadcast sync phase changes so the header status pill can reflect them
    // (queued = a write is waiting, syncing = pushing, synced = all caught up,
    // error = push failed). The pill reads the live queue length for counts.
    function emit(phase) {
        try { global.dispatchEvent(new CustomEvent("compass-sync", { detail: { phase } })); }
        catch (_) { /* CustomEvent unsupported — pill just won't update */ }
    }

    let _tmp = 0;
    const tempId = () => `tmp-${Date.now()}-${_tmp++}`;
    const isTempId = (v) => typeof v === "string" && v.startsWith("tmp-");

    // Queue an upsert and return the row's local id. A row with no id is new:
    // it gets a temp id + a client_id so replay can map it to the server id.
    //
    // CRUCIAL: repeated writes to the SAME row — including editing a task that
    // was created offline and only has a temp id — MERGE into one pending
    // change keyed by localId, instead of stacking up. Without this, creating a
    // task offline and then editing its date replayed as several brand-new
    // tasks on reconnect (the duplicate-rows-with-weird-dates bug): each edit
    // coerced the temp id to NaN and enqueued a fresh create.
    // `kind` is the server plural key: "tasks" | "classes" | "tags" | "events".
    async function queueUpsert(kind, data) {
        const raw = data.id;
        const isNew = raw == null;
        const isTemp = isTempId(raw);
        const localId = isNew ? tempId() : (isTemp ? raw : Number(raw));
        const change = Object.assign({}, data, { updated_at: new Date().toISOString() });
        if (isNew || isTemp) { delete change.id; change.client_id = localId; }
        else { change.id = localId; }
        const all = await getQueue();
        const existing = all.find((e) => e.op === "upsert" && e.localId === localId);
        if (existing) {
            existing.data = Object.assign({}, existing.data, change);
            if (existing.data.client_id) delete existing.data.id;  // stays a create
            await enqueue(existing);                                // same uid → replace
        } else {
            await enqueue({ op: "upsert", kind, data: change, localId });
        }
        emit("queued");
        return localId;
    }
    async function queueDelete(kind, id) {
        if (typeof id === "string" && id.startsWith("tmp-")) {
            // Never synced — just drop its queued upsert(s).
            const all = await getQueue();
            await removeMany(all.filter((e) => e.localId === id).map((e) => e.uid));
            emit("queued");
            return;
        }
        await enqueue({ op: "delete", kind, id: Number(id) });
        emit("queued");
    }
    // Back-compat task helpers.
    const queueTaskUpsert = (data) => queueUpsert("tasks", data);
    const queueTaskDelete = (id) => queueDelete("tasks", id);

    // Replay the queue to the server. Returns the id_map (temp → server id).
    async function replay() {
        const pending = await getQueue();
        if (!pending.length) { emit("synced"); return { id_map: {} }; }
        emit("syncing");
        const changes = {};
        const deletes = {};
        for (const e of pending) {
            const kind = e.kind || "tasks";
            if (e.op === "delete") (deletes[kind] = deletes[kind] || []).push(e.id);
            else (changes[kind] = changes[kind] || []).push(e.data);
        }
        try {
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
            emit("synced");
            return res;
        } catch (err) {
            emit("error");
            throw err;
        }
    }

    // Manual "Sync now": flush the queue then refresh the list to canonical
    // server rows (the header pill's click handler calls this).
    async function syncNow() {
        await replay();
        if (typeof window.compassSoftRefresh === "function") await window.compassSoftRefresh();
    }

    // ---- Optimistic row rendering (offline ADD) ----
    // The site is server-rendered, so an offline-added task has no row until a
    // reload reaches the server. These build a row matching templates/_today_list.html
    // (render_item) in JS so the task shows the instant it's added offline, and
    // survives an offline reload (applyToDom re-injects it from the queue). On
    // reconnect a softRefresh swaps the whole list for the canonical server HTML.
    const _bucketKey = (classId) =>
        (classId == null || classId === "" || String(classId) === "0") ? "0" : String(classId);

    function _classLabel(classId) {
        if (_bucketKey(classId) === "0") return { code: "Personal", name: "", isPersonal: true };
        const opt = document.querySelector(`select[name="class_id"] option[value="${classId}"]`);
        if (opt && opt.textContent.includes("—")) {
            const parts = opt.textContent.split("—");
            return { code: parts[0].trim(), name: parts.slice(1).join("—").trim(), isPersonal: false };
        }
        return { code: "Class", name: "", isPersonal: false };
    }

    function _makeClassBlock(classId) {
        const key = _bucketKey(classId);
        const label = _classLabel(classId);
        const block = document.createElement("div");
        block.className = "class-block";
        block.setAttribute("data-bucket-key", key);
        const headRow = document.createElement("div");
        headRow.className = "class-block-head-row";
        const grip = document.createElement("span");
        grip.className = "class-block-drag";
        grip.title = "Drag to reorder this class";
        grip.innerHTML = '<span class="class-block-drag-grip" aria-hidden="true"></span>';
        headRow.appendChild(grip);
        let head;
        if (label.isPersonal) {
            head = document.createElement("div");
            head.className = "class-block-head class-block-head-personal";
            const code = document.createElement("span");
            code.className = "class-code"; code.textContent = label.code;
            head.appendChild(code);
        } else {
            head = document.createElement("a");
            head.href = "/classes/" + key;
            head.className = "class-block-head";
            const code = document.createElement("span");
            code.className = "class-code"; code.textContent = label.code;
            const name = document.createElement("span");
            name.className = "class-name"; name.textContent = label.name;
            head.appendChild(code); head.appendChild(name);
        }
        headRow.appendChild(head);
        block.appendChild(headRow);
        const ul = document.createElement("ul");
        ul.className = "todo-list todo-list-draggable";
        block.appendChild(ul);
        return block;
    }

    function buildTaskRow(data) {
        const li = document.createElement("li");
        li.className = "todo-row";
        li.setAttribute("data-kind", "task");
        li.setAttribute("data-id", String(data.id));
        li.setAttribute("data-class-id", _bucketKey(data.class_id) === "0" ? "" : String(data.class_id));
        li.setAttribute("data-title", data.title || "");
        li.setAttribute("data-due-at", data.due_at || "");
        li.setAttribute("data-starts-at", data.starts_at || "");
        li.setAttribute("data-tag-id", data.tag_id != null ? String(data.tag_id) : "");
        li.setAttribute("data-sub-kind-id", "");
        li.setAttribute("data-rrule", data.rrule || "");
        li.setAttribute("data-is-all-day", data.is_all_day ? "1" : "");
        li.setAttribute("data-notes", data.notes || "");

        const main = document.createElement("div");
        main.className = "todo-row-main";
        main.setAttribute("data-row-toggle", "");
        main.setAttribute("aria-expanded", "false");
        main.setAttribute("tabindex", "0");
        main.setAttribute("role", "button");
        main.setAttribute("aria-label", (data.title || "") + " — tap for actions");

        const handle = document.createElement("span");
        handle.className = "todo-drag-handle";
        handle.title = "Drag to reorder priority";
        handle.innerHTML = '<span class="todo-burger" aria-hidden="true"></span>';
        main.appendChild(handle);

        const toggle = document.createElement("button");
        toggle.type = "button";
        toggle.className = "todo-toggle";
        toggle.setAttribute("aria-pressed", "false");
        toggle.setAttribute("aria-label", "Toggle done");
        toggle.innerHTML = '<span class="todo-circle"></span>';
        main.appendChild(toggle);

        const titleEl = document.createElement("span");
        titleEl.className = "todo-title";
        titleEl.textContent = data.title || "";
        main.appendChild(titleEl);

        if (data.is_all_day) {
            const w = document.createElement("span");
            w.className = "todo-when"; w.textContent = "All day";
            main.appendChild(w);
        } else if (data.due_at && data.due_at.length >= 16) {
            const hm = data.due_at.slice(11, 16);
            if (hm && hm !== "00:00") {
                const w = document.createElement("span");
                w.className = "todo-when"; w.textContent = hm;
                main.appendChild(w);
            }
        }
        if (data.tag_id != null && data.tag_id !== "") {
            const opt = document.querySelector(`select[name="tag_id"] option[value="${data.tag_id}"]`);
            if (opt) {
                const pill = document.createElement("span");
                pill.className = "todo-tag";
                const color = opt.getAttribute("data-color");
                if (color) pill.style.setProperty("--tag-color", color);
                pill.textContent = opt.textContent;
                main.appendChild(pill);
            }
        }
        li.appendChild(main);

        const drawer = document.createElement("div");
        drawer.className = "todo-drawer";
        drawer.hidden = true;
        if (data.notes && String(data.notes).trim()) {
            const n = document.createElement("div");
            n.className = "todo-notes"; n.textContent = data.notes;
            drawer.appendChild(n);
        }
        const actions = document.createElement("div");
        actions.className = "todo-drawer-actions";
        const edit = document.createElement("button");
        edit.type = "button"; edit.className = "todo-edit";
        edit.setAttribute("data-id", String(data.id)); edit.textContent = "✎ Edit";
        const del = document.createElement("button");
        del.type = "button"; del.className = "todo-del";
        del.setAttribute("data-id", String(data.id)); del.setAttribute("data-kind", "task");
        del.textContent = "× Delete";
        actions.appendChild(edit); actions.appendChild(del);
        drawer.appendChild(actions);
        li.appendChild(drawer);
        return li;
    }

    // Place an optimistic task row in the right class bucket on the today list,
    // creating the bucket (and the container, replacing the "Nothing for today"
    // empty state) when needed. Returns the row, or null if not on a list page
    // or the row is already present. Caller re-binds interactions afterward.
    function injectTaskRow(data) {
        const section = document.querySelector(".today-list-block");
        if (!section) return null;
        if (data.id != null && document.querySelector(`.todo-row[data-id="${data.id}"]`)) return null;
        let container = section.querySelector("[data-class-block-list]");
        if (!container) {
            container = document.createElement("div");
            container.className = "class-block-list";
            container.setAttribute("data-class-block-list", "");
            const empty = section.querySelector("p.empty");
            if (empty) empty.replaceWith(container); else section.appendChild(container);
        }
        const key = _bucketKey(data.class_id);
        let block = container.querySelector(`.class-block[data-bucket-key="${key}"]`);
        if (!block) { block = _makeClassBlock(data.class_id); container.appendChild(block); }
        const ul = block.querySelector("ul.todo-list");
        if (!ul) return null;
        const row = buildTaskRow(data);
        ul.appendChild(row);
        return row;
    }

    // Re-apply pending edits to the freshly-loaded page so an offline change
    // survives a reload behind the service worker: toggles re-marked, deletes
    // re-removed, and offline-added tasks re-injected (matched on client_id so
    // the next replay reconciles them to their server id).
    async function applyToDom() {
        let pending;
        try { pending = await getQueue(); } catch (_) { return; }
        let injected = false;
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
            } else if (e.op === "upsert" && e.kind === "tasks" && e.data
                       && e.data.id == null && e.data.client_id && e.data.title) {
                if (injectTaskRow({ ...e.data, id: e.data.client_id })) injected = true;
            }
        }
        if (injected && typeof window.bindTodoToggles === "function") window.bindTodoToggles();
    }

    // Flush the queue when the connection returns, then swap the today list
    // for the server's canonical HTML so offline-added rows (which carried a
    // temp id + minimal optimistic markup) become real, fully-styled rows
    // without a manual refresh. Only refresh if something actually flushed.
    global.addEventListener("online", () => {
        getQueue().then((q) => {
            const had = q.length;
            return replay().then(() => {
                if (had && typeof window.compassSoftRefresh === "function") {
                    window.compassSoftRefresh();
                }
            });
        }).catch(() => {});
    });
    // On load, replay anything left over (online) or re-apply it (offline).
    global.addEventListener("DOMContentLoaded", () => {
        if (navigator.onLine) replay().catch(() => {});
        applyToDom();
    });

    global.CompassSync = {
        queueUpsert, queueDelete, queueTaskUpsert, queueTaskDelete,
        replay, syncNow, applyToDom, isOffline, getQueue, injectTaskRow, buildTaskRow,
    };
})(window);
