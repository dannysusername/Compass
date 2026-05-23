// The Today view must survive offline: it caches the last computed
// /today.json and, when the network is down, replays it (with an offline
// banner) instead of erroring. CommonJS to match the sibling specs.

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

async function showToday(sidePanel) {
    await sidePanel.evaluate(async () => {
        const { setView } = await import("./lib/views/index.js");
        setView("today");
    });
}

test.describe("Today view offline fallback", () => {
    test("caches the computed view, then replays it offline with a banner", async () => {
        const { context, sidePanel } = await launchPanel();
        // Background syncNow() pulls /sync — keep it happy.
        await context.route(/\/sync(\?|$)/, (route) =>
            route.fulfill({ status: 200, contentType: "application/json",
                body: JSON.stringify({ server_time: "2026-01-01T00:00:00+00:00",
                    classes: [], tags: [], tasks: [], events: [], deletions: [] }) }));
        // Online: serve a today view with one task (overrides the fixture's empty one).
        await context.route("**/today.json", (route) =>
            route.fulfill({ status: 200, contentType: "application/json",
                body: JSON.stringify(TODAY_WITH_TASK) }));

        try {
            await showToday(sidePanel);
            await expect(sidePanel.locator("#content")).toContainText("Offline visible");
            await expect(sidePanel.locator(".offline-banner")).toHaveCount(0);  // online: no banner

            // Go offline: the today fetch now fails.
            await context.route("**/today.json", (route) => route.abort());
            await showToday(sidePanel);

            // Cached view replays: task still shows, plus the offline banner.
            await expect(sidePanel.locator(".offline-banner")).toBeVisible();
            await expect(sidePanel.locator("#content")).toContainText("Offline visible");
        } finally {
            await context.close();
        }
    });

    test("offline degrades gracefully (banner), never a crash", async () => {
        // Boot loads today once (the fixture's empty view), which caches it.
        // Going offline then replays that cache with the banner — not an error.
        const { context, sidePanel } = await launchPanel();
        await context.route(/\/sync(\?|$)/, (route) => route.abort());
        await context.route("**/today.json", (route) => route.abort());
        try {
            await showToday(sidePanel);
            await expect(sidePanel.locator(".offline-banner")).toBeVisible();
        } finally {
            await context.close();
        }
    });
});
