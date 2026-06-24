// Thin fetch wrapper for the Compass server. Centralises three things:
//   1. The base URL — the single SERVER_URL constant from config.js. There is
//      no user-facing override: the extension targets one server so it "just
//      works" with no setup. (Change SERVER_URL in config.js for local dev.)
//   2. Auth — a per-user bearer token (see below); credentials:'include' is
//      kept only so a same-site localhost-dev cookie still works.
//   3. Auth detection — a 401 from the server means "not logged in"; callers
//      catch the NotAuthenticated error and render the "Log in" CTA.

import { SERVER_URL } from "./config.js";

export class NotAuthenticated extends Error {
    constructor() { super("not_authenticated"); }
}

async function getBaseUrl() {
    return SERVER_URL.replace(/\/+$/, "");
}

// Per-user bearer token, captured at login/signup and stored in
// chrome.storage.local. The session cookie is SameSite=Lax and won't ride
// cross-origin to this extension, so the token is how we authenticate.
const TOKEN_KEY = "compass_token";

async function getToken() {
    const o = await chrome.storage.local.get(TOKEN_KEY);
    return o[TOKEN_KEY] || null;
}
async function setToken(token) {
    if (token) await chrome.storage.local.set({ [TOKEN_KEY]: token });
}
async function clearToken() {
    await chrome.storage.local.remove(TOKEN_KEY);
}

async function request(path, { method = "GET", body, headers = {} } = {}) {
    const base = await getBaseUrl();
    const token = await getToken();
    const init = {
        method,
        // credentials stays for the localhost-dev case (same-site cookie);
        // in prod the Bearer header below does the real authentication.
        credentials: "include",
        headers: {
            Accept: "application/json",
            ...(token ? { Authorization: "Bearer " + token } : {}),
            ...headers,
        },
    };
    if (body instanceof FormData) {
        init.body = body;
    } else if (body !== undefined) {
        init.body = body;
        if (!("Content-Type" in init.headers)) {
            init.headers["Content-Type"] = "application/json";
        }
    }
    const r = await fetch(base + path, init);
    if (r.status === 401) throw new NotAuthenticated();
    if (!r.ok) {
        let detail = `${r.status} ${r.statusText}`;
        try {
            const j = await r.json();
            // FastAPI raises with .detail, the new JSON branches use .error;
            // either is treated as the human-readable message.
            if (j.detail) detail = j.detail;
            else if (j.error) detail = j.error;
        } catch (_) { /* non-JSON error body */ }
        throw new Error(detail);
    }
    // Some endpoints (toggle/delete) return JSON; some return empty 204.
    const ct = r.headers.get("content-type") || "";
    if (ct.includes("application/json")) return r.json();
    return null;
}

export const api = {
    base: getBaseUrl,
    // Token plumbing exposed so views can capture/clear it explicitly.
    getToken,
    setToken,
    clearToken,
    me: async () => {
        const me = await request("/me.json");
        // Refresh the stored token whenever the server hands one back.
        if (me && me.extension_token) await setToken(me.extension_token);
        return me;
    },
    login: async (email, password) => {
        const fd = new FormData();
        fd.append("email", email);
        fd.append("password", password);
        const r = await request("/login", { method: "POST", body: fd });
        if (r && r.extension_token) await setToken(r.extension_token);
        return r;
    },
    signup: async (email, password) => {
        const fd = new FormData();
        fd.append("email", email);
        fd.append("password", password);
        const r = await request("/signup", { method: "POST", body: fd });
        if (r && r.extension_token) await setToken(r.extension_token);
        return r;
    },
    logout: async () => {
        try {
            return await request("/logout", { method: "POST" });
        } finally {
            // Drop the token regardless of how the server responds so a
            // failed/offline logout still ends the local extension session.
            await clearToken();
        }
    },
    classes: () => request("/classes.json"),
    createClass: ({ code, name }) => {
        const fd = new FormData();
        fd.append("code", code);
        fd.append("name", name);
        return request("/classes", { method: "POST", body: fd });
    },
    deleteClass: (id) =>
        request(`/classes/${id}/delete`, { method: "POST" }),
    tags: () => request("/tags.json"),
    createTag: ({ name, color }) => {
        const fd = new FormData();
        fd.append("name", name);
        fd.append("color", color);
        return request("/tags", { method: "POST", body: fd });
    },
    editTag: (id, { name, color }) => {
        const fd = new FormData();
        fd.append("name", name);
        fd.append("color", color);
        return request(`/tags/${id}/edit`, { method: "POST", body: fd });
    },
    deleteTag: (id) =>
        request(`/tags/${id}/delete`, { method: "POST" }),
    editEvent: (id, form) =>
        request(`/events/${id}/edit`, { method: "POST", body: form }),
    cloneEvent: (id) =>
        request(`/events/${id}/clone`, { method: "POST" }),
    addEventToCalendar: (id) =>
        request(`/events/${id}/add-to-calendar`, { method: "POST" }),
    removeEventFromCalendar: (id) =>
        request(`/events/${id}/remove-from-calendar`, { method: "POST" }),
    addAllClassEvents: (classId) =>
        request(`/classes/${classId}/events/add-all`, { method: "POST" }),
    removeAllClassEvents: (classId) =>
        request(`/classes/${classId}/events/remove-all`, { method: "POST" }),
    deleteAllClassEvents: (classId) =>
        request(`/classes/${classId}/events/delete-all`, { method: "POST" }),
    saveXaiKey: (key) => {
        const fd = new FormData();
        fd.append("xai_api_key", key);
        return request("/settings", { method: "POST", body: fd });
    },
    regenerateCalendarToken: () =>
        request("/settings/calendar/regenerate", { method: "POST" }),
    uploadSyllabus: (file) => {
        const fd = new FormData();
        fd.append("file", file);
        return request("/syllabus", { method: "POST", body: fd });
    },
    // Attach a syllabus to an EXISTING class (vs uploadSyllabus, which makes a
    // new one). Lets a manually-created class get a syllabus.
    uploadSyllabusToClass: (classId, file) => {
        const fd = new FormData();
        fd.append("file", file);
        return request(`/classes/${classId}/syllabus`, { method: "POST", body: fd });
    },
    syllabusStatus: (id) =>
        request(`/syllabus/${id}/status.json`),
    reparseSyllabus: (id) =>
        request(`/syllabus/${id}/reparse`, { method: "POST" }),
    deleteSyllabus: (id) =>
        request(`/syllabus/${id}/delete`, { method: "POST" }),
    today: () => request("/today.json"),
    week: (days = 7) => request(`/week.json?days=${days}`),
    month: (m) => request("/month.json" + (m ? `?month=${m}` : "")),
    classDetail: (id) => request(`/classes/${id}.json`),
    uploadDoc: (classId, file, title) => {
        const fd = new FormData();
        fd.append("file", file);
        if (title) fd.append("title", title);
        return request(`/classes/${classId}/docs`, { method: "POST", body: fd });
    },
    deleteDoc: (docId) =>
        request(`/docs/${docId}/delete`, { method: "POST" }),
    fileUrl: async (filename) => {
        // Build the cookie-authed /uploads URL the iframe + new-tab links
        // point at. The user's session cookie rides cross-origin via the
        // host_permission grant in manifest.json.
        const base = await getBaseUrl();
        return `${base}/uploads/${encodeURIComponent(filename)}`;
    },
    addPersonalTask: (form) =>
        request("/tasks", { method: "POST", body: form }),
    addClassTask: (classId, form) =>
        request(`/classes/${classId}/tasks`, { method: "POST", body: form }),
    addAttachment: (taskId, file) => {
        const fd = new FormData();
        fd.append("file", file);
        return request(`/tasks/${taskId}/attachments`, { method: "POST", body: fd });
    },
    deleteAttachment: (attachmentId) =>
        request(`/attachments/${attachmentId}/delete`, { method: "POST" }),
    saveTimezone: (tz) => {
        const fd = new FormData();
        fd.append("tz", tz);
        return request("/settings/timezone", { method: "POST", body: fd });
    },
    taskDetails: (id) =>
        request(`/tasks/${id}/details.json`),
    editTask: (id, form) =>
        request(`/tasks/${id}/edit`, { method: "POST", body: form }),
    reorderTasks: (items) =>
        request("/tasks/reorder", {
            method: "POST",
            body: JSON.stringify({ items }),
            headers: { "Content-Type": "application/json" },
        }),
    reorderTasksDay: (day, items) =>
        request("/tasks/reorder-day", {
            method: "POST",
            body: JSON.stringify({ day, items }),
            headers: { "Content-Type": "application/json" },
        }),
    reorderClasses: (order) =>
        request("/classes/reorder", {
            method: "POST",
            body: JSON.stringify({ order }),
            headers: { "Content-Type": "application/json" },
        }),
    toggleTask: (id) =>
        request(`/tasks/${id}/toggle`, { method: "POST" }),
    toggleEvent: (id) =>
        request(`/events/${id}/toggle`, { method: "POST" }),
    deleteTask: (id) =>
        request(`/tasks/${id}/delete`, { method: "POST" }),
    deleteEvent: (id) =>
        request(`/events/${id}/delete`, { method: "POST" }),
    excludeTaskOccurrence: (id, occurrenceAt) => {
        const fd = new FormData();
        fd.append("occurrence_at", occurrenceAt);
        return request(`/tasks/${id}/exclude`, { method: "POST", body: fd });
    },
    endTaskAfter: (id, occurrenceAt) => {
        const fd = new FormData();
        fd.append("occurrence_at", occurrenceAt);
        return request(`/tasks/${id}/end-after`, { method: "POST", body: fd });
    },
    // Generic form POST — used by the offline queue to replay raw requests
    // (recurring exclude / end-after) verbatim on reconnect.
    postForm: (path, fields) => {
        const fd = new FormData();
        for (const [k, v] of Object.entries(fields || {})) fd.append(k, v);
        return request(path, { method: "POST", body: fd });
    },
    postJson: (path, obj) =>
        request(path, {
            method: "POST",
            body: JSON.stringify(obj || {}),
            headers: { "Content-Type": "application/json" },
        }),
    // ---- Local-first sync (step 2) ----
    syncPull: (since) =>
        request("/sync" + (since ? `?since=${encodeURIComponent(since)}` : "")),
    syncPush: (payload) =>
        request("/sync", { method: "POST", body: JSON.stringify(payload) }),
};
