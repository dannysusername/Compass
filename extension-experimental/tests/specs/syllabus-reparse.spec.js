// Reparse affordance on the class-detail surface. The "↻ Reparse syllabus"
// button appears only when a syllabus exists AND the account can still parse
// (same canParseNow gate as "+ Upload syllabus"). Clicking confirms, then
// re-runs the parse via the upload view's parse-stage poll (covered server-
// side); here we assert the gating + presence, mirroring syllabus-entitlement.
//
// CommonJS module — matches the existing specs.

const { test, expect } = require("@playwright/test");
const { launchPanel } = require("../fixtures/extension.js");

const CLASS_WITH_SYLLABUS = {
    class: { id: 1, code: "CS 101", name: "Intro", is_personal: false },
    tasks: [],
    events: [],
    syllabus: { id: 7, filename: "syl.pdf", parsed_at: "2026-01-01T00:00:00" },
    documents: [],
};

const ME = {
    ownKey: { xai_api_key_set: true, server_key_available: true,
              free_parses_used: 0, free_parse_limit: 5, free_parses_remaining: null },
    exhausted: { xai_api_key_set: false, server_key_available: true,
                 free_parses_used: 5, free_parse_limit: 5, free_parses_remaining: 0 },
};

async function showDetailWith(context, sidePanel, mePatch) {
    await context.route("**/classes/1.json", (route) =>
        route.fulfill({ status: 200, contentType: "application/json",
            body: JSON.stringify(CLASS_WITH_SYLLABUS) }));
    await sidePanel.evaluate(async (patch) => {
        const { state } = await import("./lib/state.js");
        state.me = Object.assign(
            { id: 1, email: "t@e.com", timezone: "America/New_York",
              calendar_token: "tok", calendar_urls: { webcal_url: "", https_url: "" } },
            patch);
        const cls = await import("./lib/views/classes.js");
        await cls.showClassDetail(1);
    }, mePatch);
    await sidePanel.waitForSelector("#class-detail-syllabus-section:not([hidden])");
}

test.describe("Syllabus reparse affordance", () => {
    test("shows the Reparse button when a syllabus exists and parsing is allowed", async () => {
        const { context, sidePanel } = await launchPanel();
        try {
            await showDetailWith(context, sidePanel, ME.ownKey);
            await expect(sidePanel.locator("#class-detail-reparse")).toBeVisible();
        } finally {
            await context.close();
        }
    });

    test("hides the Reparse button when the account is out of parses", async () => {
        const { context, sidePanel } = await launchPanel();
        try {
            await showDetailWith(context, sidePanel, ME.exhausted);
            await expect(sidePanel.locator("#class-detail-reparse")).toBeHidden();
        } finally {
            await context.close();
        }
    });
});
