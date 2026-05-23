// Offline EDITING + offline BOOT (local-first step 2). When a write fails
// because the network is down, the extension keeps the optimistic change,
// queues it for the next sync, and patches the cached Today view so it
// survives a reload. And if /me.json is unreachable on boot, a cached
// identity lets the app start OFFLINE instead of dumping to login.
//
// route.abort() makes fetch() reject with a TypeError — the same signal a
// real offline write produces — so we don't need context.set_offline.

const { test, expect } = require("@playwright/test");
const { launchPanel } = require("../fixtures/extension.js");

const TODAY_WITH_TASK = {
    today: "2026-05-10",
    buckets: [{
        cls: { id: 0, code: "Personal", name: "", is_personal: true },
        items: [{
            kind: "task", id: 1, title: "Offline visible",
            due_at: "2026-05-10T10:00:00", completed: false,
            class_id: 0, tag_id: null, notes: "", is_all_day: false, rrule: "",
        }],
        overdue_items: [],
    }],
};

async function showTodayWithTask(context, sidePanel) {
    await context.route("**/today.json", (route) =>
        route.fulfill({ status: 200, contentType: "application/json",
            body: JSON.stringify(TODAY_WITH_TASK) }));
    await sidePanel.evaluate(async () => {
        const { setView } = await import("./lib/views/index.js");
        setView("today");
    });
    await expect(sidePanel.locator("#content")).toContainText("Offline visible");
}

async function syncState(sidePanel) {
    return sidePanel.evaluate(async () => {
        const s = await import("./lib/sync.js");
        const pending = await s.local.pending();
        const view = await s.getComputedView("today");
        return { pending, buckets: view ? view.buckets : [] };
    });
}

test.describe("offline editing + boot", () => {
    test("offline toggle keeps the row done and queues it", async () => {
        const { context, sidePanel } = await launchPanel();
        await showTodayWithTask(context, sidePanel);
        await context.route("**/tasks/*/toggle", (route) => route.abort());

        const row = sidePanel.locator('li.todo-row[data-id="1"]');
        await row.locator(".todo-circle").click();
        await expect(row).toHaveClass(/done/);  // optimistic state kept (not rolled back)

        // The queue write happens async in the failed-toggle catch — poll.
        await expect.poll(async () => (await syncState(sidePanel)).pending.length).toBe(1);
        const s = await syncState(sidePanel);
        expect(s.pending[0].op).toBe("upsert");
        expect(s.buckets[0].items[0].completed).toBe(true);  // cached view patched
        await context.close();
    });

    test("offline delete keeps the row removed and queues it", async () => {
        const { context, sidePanel } = await launchPanel();
        await showTodayWithTask(context, sidePanel);
        await context.route("**/tasks/*/delete", (route) => route.abort());
        sidePanel.on("dialog", (d) => d.accept());  // confirm("Delete this task?")

        await sidePanel.locator('li.todo-row[data-id="1"] .todo-del').click();
        await expect(sidePanel.locator('li.todo-row[data-id="1"]')).toHaveCount(0);

        await expect.poll(async () => (await syncState(sidePanel)).pending.length).toBe(1);
        const s = await syncState(sidePanel);
        expect(s.pending[0].op).toBe("delete");
        expect(s.pending[0].id).toBe(1);
        expect(s.buckets[0].items.length).toBe(0);  // gone from the cached view too
        await context.close();
    });

    test("offline boot shows the app from cache, not the login screen", async () => {
        const { context, sidePanel } = await launchPanel();  // online boot caches `me`
        await sidePanel.waitForTimeout(400);                 // let the cache write land
        await context.route("**/me.json", (route) => route.abort());  // now offline
        await sidePanel.reload();                            // re-runs boot offline

        // Cached identity → app comes up (FAB visible), NOT stranded on login.
        await expect(sidePanel.locator("#add-task-fab")).toBeVisible({ timeout: 8000 });
        await context.close();
    });
});
