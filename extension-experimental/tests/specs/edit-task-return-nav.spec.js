// Regression tests for the Edit-task surface's return-to-origin navigation
// and overdue-task save.
//
// THE BUG: showEditor() used `editReturnToClass = state.currentClassId`,
// but currentClassId stays set after a tab switch (only returnToList()
// clears it). So editing a task from the Today / Month list while
// currentClassId was stale would, on Back, wrongly drill into that class
// ("editing an overdue task takes me to the classes tab"). Fix: key the
// return target off the LIVE visibility of the #class-detail surface at
// open time, not the possibly-stale currentClassId.
//
// CommonJS to match the sibling specs (ESM loader issues on Win+Git Bash).

const { test, expect } = require("@playwright/test");
const { launchPanel } = require("../fixtures/extension.js");

const ROW = {
    kind: "task",
    id: "77",
    title: "Overdue thing",
    dueAt: "2026-05-01T10:00:00",   // in the past relative to FAKE_TODAY (2026-05-10)
    startsAt: "",
    classId: "1",
    tagId: "",
    notes: "",
    isAllDay: "",
    rrule: "",
};
const EMPTY_DETAILS = { rrule_until: "", alerts: [], attachments: [] };

async function mockDetails(context) {
    await context.route("**/tasks/*/details.json", (route) =>
        route.fulfill({ status: 200, contentType: "application/json",
                        body: JSON.stringify(EMPTY_DETAILS) }));
}

// Open the editor with a synthetic row. `prep` runs in the page BEFORE
// showEditor and can set up state / surfaces (e.g. simulate class-detail).
async function openEditorWith(sidePanel, rowSpec, prepKey) {
    await sidePanel.evaluate(async ({ data, prepKey }) => {
        const { state } = await import("./lib/state.js");
        if (prepKey === "staleClassId") {
            // User visited a class earlier, then tab-switched to Today.
            // currentClassId is stale; #class-detail is hidden.
            state.currentClassId = 1;
        } else if (prepKey === "onClassDetail") {
            const { showSecondary } = await import("./lib/nav.js");
            state.currentClassId = 1;
            showSecondary("#class-detail");   // makes #class-detail visible
        }
        const row = document.createElement("li");
        for (const [k, v] of Object.entries(data)) row.dataset[k] = v;
        document.body.appendChild(row);
        const mod = await import("./lib/forms/edit-task.js");
        await mod.showEditor(row);
    }, { data: rowSpec, prepKey });
    await sidePanel.waitForSelector("#editor:not([hidden])");
}

test.describe("Edit-task return-to-origin navigation", () => {
    test("Edit from the list (stale currentClassId) → Back returns to the list, NOT a class", async () => {
        const { context, sidePanel } = await launchPanel();
        await mockDetails(context);
        try {
            await openEditorWith(sidePanel, ROW, "staleClassId");
            await sidePanel.locator("#editor-back").click();
            await expect(sidePanel.locator("#editor"),
                "editor closes").toBeHidden();
            await expect(sidePanel.locator("#class-detail"),
                "must NOT drill into a class on back").toBeHidden();
            await expect(sidePanel.locator("#content"),
                "returns to the list").toBeVisible();
        } finally {
            await context.close();
        }
    });

    test("Edit from class-detail → Back returns to class-detail (behavior preserved)", async () => {
        const { context, sidePanel } = await launchPanel();
        await mockDetails(context);
        await context.route("**/classes/1.json", (route) =>
            route.fulfill({ status: 200, contentType: "application/json",
                body: JSON.stringify({
                    class: { id: 1, code: "CS101", name: "Intro" },
                    tasks: [], events: [], syllabus: null, documents: [],
                }) }));
        try {
            await openEditorWith(sidePanel, ROW, "onClassDetail");
            await sidePanel.locator("#editor-back").click();
            await expect(sidePanel.locator("#class-detail"),
                "returns to class-detail").toBeVisible({ timeout: 3000 });
            await expect(sidePanel.locator("#editor"),
                "editor closes").toBeHidden();
        } finally {
            await context.close();
        }
    });

    test("Editing an OVERDUE task saves (POST /tasks/{id}/edit fires)", async () => {
        const { context, sidePanel } = await launchPanel();
        await mockDetails(context);
        const capture = {};
        await context.route("**/tasks/*/edit", (route) => {
            capture.editUrl = route.request().url();
            route.fulfill({ status: 200, contentType: "application/json", body: "{}" });
        });
        try {
            await openEditorWith(sidePanel, ROW, "staleClassId");
            // Save is gated on details.json; wait for it to enable.
            await expect(sidePanel.locator("#edit-form button[type='submit']")).toBeEnabled();
            await sidePanel.locator("#edit-form input[name='title']").fill("Overdue thing RENAMED");
            await sidePanel.locator("#edit-form button[type='submit']").click();
            await expect.poll(() => capture.editUrl, { timeout: 3000 })
                .toMatch(/\/tasks\/77\/edit$/);
        } finally {
            await context.close();
        }
    });
});
