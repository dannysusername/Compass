// State-machine tests for the Delete button on the Edit-task surface.
//
// The button has two reachable code paths:
//   - isRecurring=true   → opens the recurring-sheet picker (this/future/all)
//   - isRecurring=false  → fires window.confirm() then hard-deletes
//
// Plus the load-bearing invariant from POSSIBILITIES P3/P7: when the user
// edits `due_at` in the form before clicking Delete, the API call for
// recurring "this date" / "this+future" must still use the ROW's ORIGINAL
// dueAt — not the form's edited value. Skipping this assertion is how the
// "I deleted the wrong occurrence" class of bug would land.
//
// We bypass the today-view → row-click chain by importing edit-task.js
// inside the page context and calling showEditor() with a synthetic row.
// That keeps the test focused on the editor + delete behavior; the view
// rendering pipeline is verified separately (or by manual QA).
//
// CommonJS module — matches the existing spec; Windows + Git Bash loader
// issues with ESM are well-documented in the package.json scripts comment.

const { test, expect } = require("@playwright/test");
const { launchPanel } = require("../fixtures/extension.js");

// One recurring + one non-recurring fixture row. Dataset attrs mirror
// what the today-view's renderItem macro stamps on a real <li>.
const RECURRING_ROW = {
    kind: "task",
    id: "42",
    title: "Math homework",
    dueAt: "2026-06-15T17:00:00",
    startsAt: "",
    classId: "1",
    tagId: "",
    notes: "",
    isAllDay: "",
    rrule: "FREQ=WEEKLY",
};
const NON_RECURRING_ROW = { ...RECURRING_ROW, id: "43", rrule: "" };

// Empty details payload — Save stays disabled until this resolves, but
// Delete is not gated on details (P8). We still need a 200 so the editor
// renders without console errors.
const EMPTY_DETAILS = { rrule_until: "", alerts: [], attachments: [] };

// Helper: install the editor-related mocks (details + delete endpoints).
// `capture` is an out-param the test reads after the API call fires.
async function installEditorMocks(context, capture) {
    await context.route("**/tasks/*/details.json", (route) => {
        route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify(EMPTY_DETAILS),
        });
    });
    await context.route("**/tasks/*/delete", (route) => {
        capture.deleteUrl = route.request().url();
        route.fulfill({ status: 200, contentType: "application/json", body: "{}" });
    });
    await context.route("**/tasks/*/exclude", async (route) => {
        capture.excludeUrl = route.request().url();
        capture.excludeBody = route.request().postData();
        route.fulfill({ status: 200, contentType: "application/json", body: "{}" });
    });
    await context.route("**/tasks/*/end-after", async (route) => {
        capture.endAfterUrl = route.request().url();
        capture.endAfterBody = route.request().postData();
        route.fulfill({ status: 200, contentType: "application/json", body: "{}" });
    });
}

// Helper: synthesize a row in the DOM with the given dataset attrs and
// hand it to showEditor(). Returns once the editor surface is visible.
async function openEditorWith(sidePanel, rowSpec) {
    await sidePanel.evaluate(async (data) => {
        const row = document.createElement("li");
        for (const [k, v] of Object.entries(data)) {
            // dataset uses camelCase keys, which become data-kebab-case attrs.
            row.dataset[k] = v;
        }
        // Mount the row so it's in the DOM tree (avoids any
        // "detached element" edge cases in showEditor/populateEditor).
        document.body.appendChild(row);
        const mod = await import("./lib/forms/edit-task.js");
        await mod.showEditor(row);
    }, rowSpec);
    await sidePanel.waitForSelector("#editor:not([hidden])");
}

test.describe("Edit-task Delete button", () => {
    test("Delete button is present in the editor action row", async () => {
        const { context, sidePanel } = await launchPanel();
        const capture = {};
        await installEditorMocks(context, capture);
        try {
            await openEditorWith(sidePanel, NON_RECURRING_ROW);
            const del = sidePanel.locator("#editor-delete");
            await expect(del, "Delete button must exist + be visible").toBeVisible();
            await expect(del, "Delete button must be enabled at rest").toBeEnabled();
        } finally {
            await context.close();
        }
    });

    test("Recurring task → click Delete opens the bottom-sheet picker (not a confirm)", async () => {
        const { context, sidePanel } = await launchPanel();
        const capture = {};
        await installEditorMocks(context, capture);
        // Confirm dialogs would auto-fail the test here — we want NO confirm
        // for recurring; the sheet is the affordance. Catch any dialog and
        // mark it as a failure signal.
        let confirmFired = false;
        sidePanel.on("dialog", async (dialog) => {
            confirmFired = true;
            await dialog.dismiss();
        });
        try {
            await openEditorWith(sidePanel, RECURRING_ROW);
            await sidePanel.locator("#editor-delete").click();
            const sheet = sidePanel.locator("#rec-delete");
            await expect(sheet, "Recurring sheet must be visible after Delete").toBeVisible();
            expect(confirmFired, "No confirm() should fire for recurring tasks").toBe(false);
        } finally {
            await context.close();
        }
    });

    test("Non-recurring task → click Delete fires window.confirm() (and no recurring sheet)", async () => {
        const { context, sidePanel } = await launchPanel();
        const capture = {};
        await installEditorMocks(context, capture);
        let confirmMessage = null;
        sidePanel.on("dialog", async (dialog) => {
            confirmMessage = dialog.message();
            // Dismiss so the test can verify the dialog appeared without
            // actually triggering the delete API call here.
            await dialog.dismiss();
        });
        try {
            await openEditorWith(sidePanel, NON_RECURRING_ROW);
            await sidePanel.locator("#editor-delete").click();
            // The dialog event is async; wait briefly for it.
            await sidePanel.waitForTimeout(150);
            expect(confirmMessage, "confirm() must fire with a message").toBeTruthy();
            const sheet = sidePanel.locator("#rec-delete");
            await expect(sheet, "Recurring sheet must stay hidden for non-recurring").toBeHidden();
        } finally {
            await context.close();
        }
    });

    test("Recurring 'this date' picker → POST /exclude carries the ROW's original dueAt, not the form's edited value", async () => {
        const { context, sidePanel } = await launchPanel();
        const capture = {};
        await installEditorMocks(context, capture);
        try {
            await openEditorWith(sidePanel, RECURRING_ROW);
            // Edit the form's due_at to a DIFFERENT value than the row carries.
            // The Delete button must ignore this and act on the original.
            await sidePanel.locator("#edit-form input[name='due_at']")
                .fill("2026-07-20T09:00");
            await sidePanel.locator("#editor-delete").click();
            await sidePanel.waitForSelector("#rec-delete:not([hidden])");
            await sidePanel.locator("#rec-delete button[data-mode='this']").click();
            // Wait for the captured request to land.
            await expect.poll(() => capture.excludeUrl, { timeout: 3_000 })
                .toMatch(/\/tasks\/42\/exclude$/);
            // FormData posts as multipart; the original dueAt should appear
            // in the body. The edited form value (2026-07-20T09:00) must NOT.
            expect(capture.excludeBody,
                "P3/P7: occurrence_at must match the row's ORIGINAL dueAt")
                .toContain(RECURRING_ROW.dueAt);
            expect(capture.excludeBody,
                "Form's edited due_at must NOT leak into occurrence_at")
                .not.toContain("2026-07-20T09:00");
        } finally {
            await context.close();
        }
    });

    test("Recurring 'this and future' picker → POST /end-after with original dueAt", async () => {
        const { context, sidePanel } = await launchPanel();
        const capture = {};
        await installEditorMocks(context, capture);
        try {
            await openEditorWith(sidePanel, RECURRING_ROW);
            await sidePanel.locator("#editor-delete").click();
            await sidePanel.waitForSelector("#rec-delete:not([hidden])");
            await sidePanel.locator("#rec-delete button[data-mode='future']").click();
            await expect.poll(() => capture.endAfterUrl, { timeout: 3_000 })
                .toMatch(/\/tasks\/42\/end-after$/);
            expect(capture.endAfterBody).toContain(RECURRING_ROW.dueAt);
        } finally {
            await context.close();
        }
    });

    test("Recurring 'entire task' picker → POST /tasks/{id}/delete", async () => {
        const { context, sidePanel } = await launchPanel();
        const capture = {};
        await installEditorMocks(context, capture);
        try {
            await openEditorWith(sidePanel, RECURRING_ROW);
            await sidePanel.locator("#editor-delete").click();
            await sidePanel.waitForSelector("#rec-delete:not([hidden])");
            await sidePanel.locator("#rec-delete button[data-mode='all']").click();
            await expect.poll(() => capture.deleteUrl, { timeout: 3_000 })
                .toMatch(/\/tasks\/42\/delete$/);
        } finally {
            await context.close();
        }
    });

    test("Non-recurring accept confirm → POST /tasks/{id}/delete", async () => {
        const { context, sidePanel } = await launchPanel();
        const capture = {};
        await installEditorMocks(context, capture);
        sidePanel.on("dialog", async (dialog) => { await dialog.accept(); });
        try {
            await openEditorWith(sidePanel, NON_RECURRING_ROW);
            await sidePanel.locator("#editor-delete").click();
            await expect.poll(() => capture.deleteUrl, { timeout: 3_000 })
                .toMatch(/\/tasks\/43\/delete$/);
        } finally {
            await context.close();
        }
    });

    test("Successful delete closes the editor (P9: same return routing as Save)", async () => {
        const { context, sidePanel } = await launchPanel();
        const capture = {};
        await installEditorMocks(context, capture);
        sidePanel.on("dialog", async (dialog) => { await dialog.accept(); });
        try {
            await openEditorWith(sidePanel, NON_RECURRING_ROW);
            await sidePanel.locator("#editor-delete").click();
            // After accept, the handler awaits the delete API + load(),
            // then calls hideEditor(). Editor surface must hide.
            await expect(sidePanel.locator("#editor"),
                "Editor must close after successful delete")
                .toBeHidden({ timeout: 3_000 });
        } finally {
            await context.close();
        }
    });

    test("Delete failure → editor stays open with red status, buttons re-enabled (P10)", async () => {
        const { context, sidePanel } = await launchPanel();
        const capture = {};
        await installEditorMocks(context, capture);
        // Override the delete route to fail. Playwright's matcher uses
        // last-registered-wins, so this MUST run AFTER installEditorMocks.
        await context.route("**/tasks/*/delete", (route) => {
            route.fulfill({
                status: 500,
                contentType: "application/json",
                body: JSON.stringify({ detail: "boom" }),
            });
        });
        sidePanel.on("dialog", async (dialog) => { await dialog.accept(); });
        try {
            await openEditorWith(sidePanel, NON_RECURRING_ROW);
            await sidePanel.locator("#editor-delete").click();
            // Editor must stay open with an error status visible.
            await expect(sidePanel.locator("#editor"),
                "Editor must stay open on delete failure").toBeVisible();
            const status = sidePanel.locator("#edit-status");
            await expect(status, "Status line must surface the error")
                .toContainText(/Couldn't delete/i);
            // Delete + Save must be re-enabled so the user can retry or cancel.
            await expect(sidePanel.locator("#editor-delete")).toBeEnabled();
        } finally {
            await context.close();
        }
    });
});
