// Thin fetch wrapper for the Compass server. Centralises three things:
//   1. The base URL — read from chrome.storage so the user can point at
//      localhost during dev and at Heroku in prod without rebuilding.
//   2. Credentials — every call sends cookies so the user's existing
//      session rides along (cookie was set when they logged in via the
//      main site in a tab).
//   3. Auth detection — a 401 from the server means "redirect to login";
//      callers throw a NotAuthenticated error the popup can catch and
//      render the "Log in" CTA.
//
// Default Compass URL is http://localhost:8000 (matches CLAUDE.md run
// command). Override via the options page.

const DEFAULT_URL = "http://localhost:8000";

export class NotAuthenticated extends Error {
    constructor() { super("not_authenticated"); }
}

async function getBaseUrl() {
    const { compass_url } = await chrome.storage.local.get("compass_url");
    return (compass_url || DEFAULT_URL).replace(/\/+$/, "");
}

async function request(path, { method = "GET", body, headers = {} } = {}) {
    const base = await getBaseUrl();
    const init = {
        method,
        credentials: "include",
        headers: { Accept: "application/json", ...headers },
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
    me: () => request("/me.json"),
    signup: (email, password) => {
        const fd = new FormData();
        fd.append("email", email);
        fd.append("password", password);
        return request("/signup", { method: "POST", body: fd });
    },
    logout: () => request("/logout", { method: "POST" }),
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
    syllabusStatus: (id) =>
        request(`/syllabus/${id}/status.json`),
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
    // ---- Local-first sync (step 2) ----
    syncPull: (since) =>
        request("/sync" + (since ? `?since=${encodeURIComponent(since)}` : "")),
    syncPush: (payload) =>
        request("/sync", { method: "POST", body: JSON.stringify(payload) }),
};
