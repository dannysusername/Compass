// Unit tests for the local-first sync engine (lib/sync.js): IndexedDB
// mirror, PULL (apply rows + tombstone deletions), local write queue, and
// PUSH (send queued task changes, map temp client ids -> server ids, clear
// the queue). The server is mocked via context.route so no live backend is
// needed. CommonJS to match the sibling specs.
//
// The Today view runs a background syncNow() on boot, so each test settles
// that, registers its own /sync route, then resets the mirror to a clean
// state (resetSync) before exercising — keeps assertions deterministic.

const { test, expect } = require("@playwright/test");
const { launchPanel } = require("../fixtures/extension.js");

const EMPTY = {
    server_time: "2026-01-01T00:00:00+00:00",
    classes: [], tags: [], tasks: [], events: [], deletions: [],
};

async function resetSync(sidePanel) {
    await sidePanel.evaluate(async () => {
        const s = await import("./lib/sync.js");
        await s._resetForTests();
    });
}

// Settle the boot syncNow (handled by the fixture's /sync mock) before the
// test registers its own /sync route, so boot can't pollute captures.
async function settled(sidePanel) {
    await sidePanel.waitForTimeout(400);
}

test.describe("sync engine (lib/sync.js)", () => {
    test("pull applies server rows and tombstone deletions", async () => {
        const { context, sidePanel } = await launchPanel();
        await settled(sidePanel);
        await context.route(/\/sync(\?|$)/, (route) => {
            if (route.request().method() !== "GET") return route.fallback();
            route.fulfill({ status: 200, contentType: "application/json",
                body: JSON.stringify({ ...EMPTY,
                    tasks: [{ id: 1, title: "Keep me" }, { id: 2, title: "Delete me" }],
                    deletions: [{ kind: "task", id: 2 }] }) });
        });
        await resetSync(sidePanel);
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
        await settled(sidePanel);
        const seenSince = [];
        await context.route(/\/sync(\?|$)/, (route) => {
            if (route.request().method() !== "GET") return route.fallback();
            seenSince.push(new URL(route.request().url()).searchParams.get("since"));
            route.fulfill({ status: 200, contentType: "application/json",
                body: JSON.stringify({ ...EMPTY, server_time: "2026-07-07T07:07:07+00:00" }) });
        });
        await resetSync(sidePanel);
        try {
            await sidePanel.evaluate(async () => {
                const sync = await import("./lib/sync.js");
                await sync.pull();
                await sync.pull();
            });
            expect(seenSince[0]).toBeNull();                          // first: no cursor
            expect(seenSince[1]).toBe("2026-07-07T07:07:07+00:00");   // second: stored cursor
        } finally {
            await context.close();
        }
    });

    test("queue a new task then push: sends client_id, maps to server id, clears queue", async () => {
        const { context, sidePanel } = await launchPanel();
        await settled(sidePanel);
        let pushedBody = null;
        await context.route(/\/sync(\?|$)/, (route) => {
            const req = route.request();
            if (req.method() === "GET")
                return route.fulfill({ status: 200, contentType: "application/json",
                    body: JSON.stringify(EMPTY) });
            pushedBody = JSON.parse(req.postData() || "{}");
            const cid = pushedBody.changes.tasks[0].client_id;
            route.fulfill({ status: 200, contentType: "application/json",
                body: JSON.stringify({ server_time: "2026-01-02T00:00:00+00:00", id_map: { [cid]: 500 } }) });
        });
        await resetSync(sidePanel);
        try {
            const r = await sidePanel.evaluate(async () => {
                const sync = await import("./lib/sync.js");
                await sync.queueTaskUpsert({ title: "Offline new", due_at: "2026-05-23T10:00" });
                const beforeTasks = (await sync.local.tasks()).length;
                const beforePending = (await sync.local.pending()).length;
                await sync.push();
                return { beforeTasks, beforePending, afterPending: (await sync.local.pending()).length };
            });
            expect(r.beforeTasks).toBe(1);
            expect(r.beforePending).toBe(1);
            expect(r.afterPending).toBe(0);
            expect(pushedBody.changes.tasks[0].title).toBe("Offline new");
            expect(pushedBody.changes.tasks[0].client_id).toBeTruthy();
        } finally {
            await context.close();
        }
    });

    test("queue a delete of an existing task: push sends it in deletes", async () => {
        const { context, sidePanel } = await launchPanel();
        await settled(sidePanel);
        let pushedBody = null;
        await context.route(/\/sync(\?|$)/, (route) => {
            const req = route.request();
            if (req.method() === "GET")
                return route.fulfill({ status: 200, contentType: "application/json",
                    body: JSON.stringify({ ...EMPTY, tasks: [{ id: 7, title: "Doomed" }] }) });
            pushedBody = JSON.parse(req.postData() || "{}");
            route.fulfill({ status: 200, contentType: "application/json",
                body: JSON.stringify({ server_time: "x", id_map: {} }) });
        });
        await resetSync(sidePanel);
        try {
            const remaining = await sidePanel.evaluate(async () => {
                const sync = await import("./lib/sync.js");
                await sync.pull();             // brings task id 7 into the mirror
                await sync.queueTaskDelete(7);
                await sync.push();
                return (await sync.local.tasks()).length;
            });
            expect(remaining).toBe(0);
            expect(pushedBody.deletes.tasks).toContain(7);
        } finally {
            await context.close();
        }
    });
});
