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

    test("offline add queues a new task and shows it", async () => {
        const { context, sidePanel } = await launchPanel();
        await showTodayWithTask(context, sidePanel);  // caches the base today view
        // Now fully offline: both the create AND the today refetch fail, so
        // load() falls back to the cache we patched with the new task.
        await context.route("**/today.json", (route) => route.abort());
        await context.route("**/tasks", (route) =>
            route.request().method() === "POST" ? route.abort() : route.fallback());

        await sidePanel.locator("#add-task-fab").click();
        await sidePanel.locator("#add-task-form input[name='title']").fill("Made offline");
        await sidePanel.locator("#add-task-form button[type='submit']").click();

        await expect(sidePanel.locator("#content")).toContainText("Made offline");
        await expect.poll(async () =>
            (await syncState(sidePanel)).pending.filter((p) => p.op === "upsert").length
        ).toBeGreaterThan(0);
        const s = await syncState(sidePanel);
        const up = s.pending.find((p) => p.op === "upsert");
        expect(up.data.client_id).toBeTruthy();   // new task → has a client_id for the push
        expect(up.data.title).toBe("Made offline");
        await context.close();
    });

    test("offline full edit queues the changed fields", async () => {
        const { context, sidePanel } = await launchPanel();
        await showTodayWithTask(context, sidePanel);
        await context.route("**/tasks/*/details.json", (route) =>
            route.fulfill({ status: 200, contentType: "application/json",
                body: JSON.stringify({ rrule_until: "", alerts: [], attachments: [] }) }));
        await context.route("**/tasks/*/edit", (route) => route.abort());  // offline

        // open the editor for the task row, change the title, save
        await sidePanel.evaluate(async () => {
            const row = document.querySelector('li.todo-row[data-id="1"]');
            const mod = await import("./lib/forms/edit-task.js");
            await mod.showEditor(row);
        });
        await expect(sidePanel.locator("#editor")).toBeVisible();
        await sidePanel.locator("#edit-form input[name='title']").fill("Edited offline");
        await sidePanel.locator("#edit-form button[type='submit']").click();

        await expect.poll(async () => {
            const s = await syncState(sidePanel);
            const up = s.pending.find((p) => p.op === "upsert" && p.data && p.data.id === 1);
            return up && up.data.title;
        }).toBe("Edited offline");
        await context.close();
    });

    test("create offline then edit several times → ONE pending create (no duplicates)", async () => {
        // Regression for the duplicate-task bug: editing a still-offline task
        // (temp id) must MERGE into the queued create, not stack new changes.
        const { context, sidePanel } = await launchPanel();
        const result = await sidePanel.evaluate(async () => {
            const s = await import("./lib/sync.js");
            await s._resetForTests();
            const row = await s.queueTaskUpsert({ title: "Multi", due_at: "2026-05-10T09:00" });
            const tmp = row.id;                              // temp id, never synced
            await s.offlineEditTask(tmp, { due_at: "2026-05-10T10:00" });
            await s.offlineEditTask(tmp, { due_at: "2026-05-10T14:00" });
            await s.offlineEditTask(tmp, { due_at: "2026-05-10T16:00" });
            return { pending: await s.local.pending(), tmp };
        });
        const ups = result.pending.filter((p) => p.op === "upsert");
        expect(ups.length).toBe(1);                          // collapsed into ONE
        expect(ups[0].data.client_id).toBe(result.tmp);      // still a create
        expect(ups[0].data.id).toBeUndefined();              // not an update
        expect(ups[0].data.title).toBe("Multi");             // create field preserved
        expect(ups[0].data.due_at).toBe("2026-05-10T16:00"); // final date wins
        await context.close();
    });

    test("offline add-class queues a classes create", async () => {
        const { context, sidePanel } = await launchPanel();
        // POST /classes fails (offline); other /classes* requests pass through.
        await context.route("**/classes", (route) =>
            route.request().method() === "POST" ? route.abort() : route.fallback());

        await sidePanel.evaluate(async () => {
            const m = await import("./lib/forms/add-class.js");
            m.showAddClass();
        });
        await sidePanel.locator("#add-class-form input[name='code']").fill("MATH 250");
        await sidePanel.locator("#add-class-form input[name='name']").fill("Calculus II");
        await sidePanel.locator("#add-class-form button[type='submit']").click();

        await expect.poll(async () =>
            (await syncState(sidePanel)).pending.filter(
                (p) => p.kind === "classes" && p.op === "upsert").length
        ).toBe(1);
        const up = (await syncState(sidePanel)).pending.find((p) => p.kind === "classes");
        expect(up.data.client_id).toBeTruthy();   // new → client_id for the push
        expect(up.data.code).toBe("MATH 250");
        expect(up.data.name).toBe("Calculus II");
        await context.close();
    });

    test("offline delete-class queues a classes delete", async () => {
        const { context, sidePanel } = await launchPanel();
        await context.route("**/classes/1/delete", (route) => route.abort());
        sidePanel.on("dialog", (d) => d.accept());  // confirm("Delete ... ?")

        // Land on the class-detail surface for class 1, then delete.
        await sidePanel.evaluate(async () => {
            const { state } = await import("./lib/state.js");
            const { showSecondary } = await import("./lib/nav.js");
            state.currentClassId = 1;
            document.querySelector("#class-detail-code").textContent = "CS101";
            showSecondary("#class-detail");
        });
        await sidePanel.locator("#class-detail-delete").click();

        await expect.poll(async () =>
            (await syncState(sidePanel)).pending.filter(
                (p) => p.kind === "classes" && p.op === "delete").length
        ).toBe(1);
        const del = (await syncState(sidePanel)).pending.find(
            (p) => p.kind === "classes" && p.op === "delete");
        expect(del.id).toBe(1);
        await context.close();
    });

    test("offline new tag (settings) queues a tags create", async () => {
        const { context, sidePanel } = await launchPanel();
        await context.route("**/tags", (route) =>
            route.request().method() === "POST" ? route.abort() : route.fallback());

        await sidePanel.locator("#open-settings").click();
        await expect(sidePanel.locator("#settings-new-tag-form")).toBeVisible();
        await sidePanel.locator("#settings-new-tag-form input[name='name']").fill("OffTag");
        await sidePanel.locator("#settings-new-tag-form button[type='submit']").click();

        await expect.poll(async () =>
            (await syncState(sidePanel)).pending.filter(
                (p) => p.kind === "tags" && p.op === "upsert").length
        ).toBe(1);
        const up = (await syncState(sidePanel)).pending.find((p) => p.kind === "tags");
        expect(up.data.client_id).toBeTruthy();
        expect(up.data.name).toBe("OffTag");
        await context.close();
    });

    test("offline tag rename (settings) queues a tags upsert", async () => {
        const { context, sidePanel } = await launchPanel();
        await context.route("**/tags.json", (route) =>
            route.fulfill({ status: 200, contentType: "application/json",
                body: JSON.stringify([{ id: 5, name: "Old", color: "#ff0000", is_system: false }]) }));
        await context.route("**/tags/5/edit", (route) => route.abort());

        await sidePanel.locator("#open-settings").click();
        const nameInput = sidePanel.locator('#settings-tags-list li[data-tag-id="5"] input.manage-tag-name');
        await expect(nameInput).toBeVisible();
        await nameInput.fill("Renamed");
        await nameInput.blur();

        await expect.poll(async () =>
            (await syncState(sidePanel)).pending.filter(
                (p) => p.kind === "tags" && p.op === "upsert").length
        ).toBe(1);
        const up = (await syncState(sidePanel)).pending.find((p) => p.kind === "tags" && p.op === "upsert");
        expect(up.data.id).toBe(5);
        expect(up.data.name).toBe("Renamed");
        await context.close();
    });

    test("offline tag delete (settings) queues a tags delete", async () => {
        const { context, sidePanel } = await launchPanel();
        await context.route("**/tags.json", (route) =>
            route.fulfill({ status: 200, contentType: "application/json",
                body: JSON.stringify([{ id: 5, name: "DelTag", color: "#ff0000", is_system: false }]) }));
        await context.route("**/tags/5/delete", (route) => route.abort());
        sidePanel.on("dialog", (d) => d.accept());  // confirm("Delete tag ...?")

        await sidePanel.locator("#open-settings").click();
        const row = sidePanel.locator('#settings-tags-list li[data-tag-id="5"]');
        await expect(row).toBeVisible();
        await row.locator("button.manage-tag-del").click();

        await expect.poll(async () =>
            (await syncState(sidePanel)).pending.filter(
                (p) => p.kind === "tags" && p.op === "delete").length
        ).toBe(1);
        expect((await syncState(sidePanel)).pending.find(
            (p) => p.kind === "tags" && p.op === "delete").id).toBe(5);
        await context.close();
    });

    test("offline recurring this/future queues a replayable request", async () => {
        const { context, sidePanel } = await launchPanel();
        const result = await sidePanel.evaluate(async () => {
            const s = await import("./lib/sync.js");
            await s._resetForTests();
            await s.offlineRecurringDelete("this", 7, "2026-09-15T09:00:00");
            await s.offlineRecurringDelete("future", 8, "2026-10-01T09:00:00");
            return await s.local.pending();
        });
        const reqs = result.filter((p) => p.op === "request");
        expect(reqs.length).toBe(2);
        const excl = reqs.find((r) => r.url === "/tasks/7/exclude");
        const end = reqs.find((r) => r.url === "/tasks/8/end-after");
        expect(excl.body.occurrence_at).toBe("2026-09-15T09:00:00");
        expect(end.body.occurrence_at).toBe("2026-10-01T09:00:00");
        await context.close();
    });

    test("push replays a queued recurring request to the server", async () => {
        const { context, sidePanel } = await launchPanel();
        let hit = null;
        await context.route("**/tasks/7/exclude", (route) => {
            hit = route.request().method();
            route.fulfill({ status: 200, contentType: "application/json", body: "{}" });
        });
        await context.route("**/sync", (route) =>
            route.request().method() === "POST"
                ? route.fulfill({ status: 200, contentType: "application/json",
                    body: JSON.stringify({ id_map: {} }) })
                : route.fallback());
        await sidePanel.evaluate(async () => {
            const s = await import("./lib/sync.js");
            await s._resetForTests();
            await s.offlineRecurringDelete("this", 7, "2026-09-15T09:00:00");
            await s.push();
        });
        expect(hit).toBe("POST");  // the /exclude request was replayed
        const left = await syncState(sidePanel);
        expect(left.pending.length).toBe(0);  // queue cleared after replay
        await context.close();
    });

    test("push replays a queued JSON request (reorder) to the server", async () => {
        const { context, sidePanel } = await launchPanel();
        let body = null, ctype = null;
        await context.route("**/tasks/reorder", (route) => {
            body = route.request().postData();
            ctype = route.request().headers()["content-type"] || "";
            route.fulfill({ status: 200, contentType: "application/json", body: "{}" });
        });
        await context.route("**/sync", (route) =>
            route.request().method() === "POST"
                ? route.fulfill({ status: 200, contentType: "application/json",
                    body: JSON.stringify({ id_map: {} }) })
                : route.fallback());
        await sidePanel.evaluate(async () => {
            const s = await import("./lib/sync.js");
            await s._resetForTests();
            await s.queueRequest("/tasks/reorder", { items: [{ kind: "task", id: 1 }] }, { json: true });
            await s.push();
        });
        expect(ctype).toContain("application/json");  // replayed as JSON, not form
        expect(JSON.parse(body).items[0].id).toBe(1);
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
