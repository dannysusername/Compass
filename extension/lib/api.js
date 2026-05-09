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
            if (j.detail) detail = j.detail;
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
    classes: () => request("/classes.json"),
    tags: () => request("/tags.json"),
    today: () => request("/today.json"),
    week: (days = 7) => request(`/week.json?days=${days}`),
    month: (m) => request("/month.json" + (m ? `?month=${m}` : "")),
    classDetail: (id) => request(`/classes/${id}.json`),
    addPersonalTask: (form) =>
        request("/tasks", { method: "POST", body: form }),
    addClassTask: (classId, form) =>
        request(`/classes/${classId}/tasks`, { method: "POST", body: form }),
    addAttachment: (taskId, file) => {
        const fd = new FormData();
        fd.append("file", file);
        return request(`/tasks/${taskId}/attachments`, { method: "POST", body: fd });
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
};
