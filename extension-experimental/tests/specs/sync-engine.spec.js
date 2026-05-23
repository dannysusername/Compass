// Unit tests for the local-first sync engine (lib/sync.js): IndexedDB
// mirror, PULL (apply rows + tombstone deletions), local write queue, and
// PUSH (send queued task changes, map temp client ids -> server ids, clear
// the queue). The server is mocked via context.route so no live backend is
// needed. CommonJS to match the sibling specs.

const { test, expect } = require("@playwright/test");
const { launchPanel } = require("../fixtures/extension.js");

function emptyPull(extra = {}) {
    return {
        server_time: "2026-01-01T00:00:00+00:00",
        classes: [], tags: [], tasks: [], events: [], deletions: [],
        ...extra,
    };
}

test.describe("sync engine (lib/sync.js)", () => {
    test("pull applies server rows and tombstone deletions", async () => {
        const { context, sidePanel } = await launchPanel();
        await context.route("**/sync", (route) => {
            if (route.request().method() !== "GET") return route.fallback();
            route.fulfill({
                status: 200, contentType: "application/json",
                body: JSON.stringify(emptyPull({
                    tasks: [{ id: 1, title: "Keep me" }, { id: 2, title: "Delete me" }],
                    deletions: [{ kind: "task", id: 2 }],
                })),
            });
        });
        try {
            const titles = await sidePanel.evaluate(async () => {
                const sync = await import("./lib/sync.js");
                await sync.pull();
                return (await sync.local.tasks()).map((t) => t.title);
            });
            expect(titles).toEqual(["Keep me"]);  // id 2 was tombstoned
        } finally {
            await context.close();
        }
    });

    test("pull stores the server_time cursor and sends it back next pull", async () => {
        const { context, sidePanel } = await launchPanel();
        const seenSince = [];
        // Regex (not glob) so it matches /sync and /sync?since=… but NOT the
        // lib/sync.js module import (a "**/sync*" glob would catch that).
        await context.route(/\/sync(\?|$)/, (route) => {
            if (route.request().method() !== "GET") return route.fallback();
            const u = new URL(route.request().url());
            seenSince.push(u.searchParams.get("since"));
            route.fulfill({ status: 200, contentType: "application/json",
                body: JSON.stringify(emptyPull()) });
        });
        try {
            await sidePanel.evaluate(async () => {
                const sync = await import("./lib/sync.js");
                await sync.pull();
                await sync.pull();
            });
            expect(seenSince[0]).toBeNull();                         // first pull: no cursor
            expect(seenSince[1]).toBe("2026-01-01T00:00:00+00:00");  // second: stored cursor
        } finally {
            await context.close();
        }
    });

    test("queue a new task then push: sends client_id, maps to server id, clears queue", async () => {
        const { context, sidePanel } = await launchPanel();
        let pushedBody = null;
        await context.route("**/sync", (route) => {
            const req = route.request();
            if (req.method() === "GET") {
                return route.fulfill({ status: 200, contentType: "application/json",
                    body: JSON.stringify(emptyPull()) });
            }
            // POST: echo a server id for the pushed client_id
            pushedBody = JSON.parse(req.postData() || "{}");
            const cid = pushedBody.changes.tasks[0].client_id;
            route.fulfill({ status: 200, contentType: "application/json",
                body: JSON.stringify({ server_time: "2026-01-02T00:00:00+00:00",
                                       id_map: { [cid]: 500 } }) });
        });
        try {
            const r = await sidePanel.evaluate(async () => {
                const sync = await import("./lib/sync.js");
                await sync.queueTaskUpsert({ title: "Offline new", due_at: "2026-05-23T10:00" });
                const beforeTasks = (await sync.local.tasks()).length;
                const beforePending = (await sync.local.pending()).length;
                await sync.push();
                return {
                    beforeTasks, beforePending,
                    afterPending: (await sync.local.pending()).length,
                };
            });
            expect(r.beforeTasks).toBe(1);     // optimistic local row exists
            expect(r.beforePending).toBe(1);   // queued
            expect(r.afterPending).toBe(0);    // queue cleared after push
            expect(pushedBody.changes.tasks[0].title).toBe("Offline new");
            expect(pushedBody.changes.tasks[0].client_id).toBeTruthy();
        } finally {
            await context.close();
        }
    });

    test("queue a delete of an existing task: push sends it in deletes", async () => {
        const { context, sidePanel } = await launchPanel();
        let pushedBody = null;
        await context.route("**/sync", (route) => {
            const req = route.request();
            if (req.method() === "GET") {
                return route.fulfill({ status: 200, contentType: "application/json",
                    body: JSON.stringify(emptyPull({ tasks: [{ id: 7, title: "Doomed" }] })) });
            }
            pushedBody = JSON.parse(req.postData() || "{}");
            route.fulfill({ status: 200, contentType: "application/json",
                body: JSON.stringify({ server_time: "x", id_map: {} }) });
        });
        try {
            const remaining = await sidePanel.evaluate(async () => {
                const sync = await import("./lib/sync.js");
                await sync.pull();                  // brings task id 7 into the mirror
                await sync.queueTaskDelete(7);
                await sync.push();
                return (await sync.local.tasks()).length;
            });
            expect(remaining).toBe(0);                       // gone locally
            expect(pushedBody.deletes.tasks).toContain(7);   // and pushed as a delete
        } finally {
            await context.close();
        }
    });
});
