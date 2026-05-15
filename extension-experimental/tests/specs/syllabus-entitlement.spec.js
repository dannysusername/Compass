// State-machine tests for syllabus-parse entitlement (the hybrid
// free-pool-vs-own-key model). The single source of truth on the client
// is `state.me` (projected from the server's /me.json `_parse_usage`):
//
//   xai_api_key_set        → user brought their own key (uncapped)
//   server_key_available   → the shared/free pool is configured
//   free_parses_remaining  → parses left on the free pool (null = unlimited)
//
// Four reachable states drive two affordances — the Classes-view
// "+ Upload syllabus" button (enabled/disabled + tooltip) and the
// Settings "Syllabus parsing" usage line:
//
//   S1 own key                        → enabled,  "unlimited"
//   S2 free pool, parses left         → enabled,  "N of M used · K left"
//   S3 free pool, exhausted           → disabled, "out of free parses"
//   S4 no own key + no pool (dev)     → disabled, "set your xAI key"
//
// Like edit-task-delete.spec.js we drive the modules directly: set
// `state.me` in the page context, then call `loadClasses()` /
// `showSettings()` so the test stays focused on the gating logic and not
// on the tab-navigation pipeline (covered elsewhere / by manual QA).
//
// CommonJS module — matches the existing specs (Windows + Git Bash ESM
// loader issues are documented in the tests' package.json).

const { test, expect } = require("@playwright/test");
const { launchPanel } = require("../fixtures/extension.js");

// The four /me.json projections. Only the entitlement fields matter here;
// the rest mirror the fixture's FAKE_ME shape so the panel stays happy.
const ME = {
    ownKey: {
        xai_api_key_set: true,
        xai_api_key_masked: "xai-12…cdef",
        server_key_available: true,
        free_parses_used: 0,
        free_parse_limit: 5,
        free_parses_remaining: null,
    },
    poolLeft: {
        xai_api_key_set: false,
        xai_api_key_masked: null,
        server_key_available: true,
        free_parses_used: 2,
        free_parse_limit: 5,
        free_parses_remaining: 3,
    },
    poolExhausted: {
        xai_api_key_set: false,
        xai_api_key_masked: null,
        server_key_available: true,
        free_parses_used: 5,
        free_parse_limit: 5,
        free_parses_remaining: 0,
    },
    noPool: {
        xai_api_key_set: false,
        xai_api_key_masked: null,
        server_key_available: false,
        free_parses_used: 0,
        free_parse_limit: 5,
        free_parses_remaining: 0,
    },
    // Admin-granted unlimited: no own key, but uncapped on the shared key.
    granted: {
        xai_api_key_set: false,
        xai_api_key_masked: null,
        unlimited_grant: true,
        server_key_available: true,
        free_parses_used: 9,
        free_parse_limit: 5,
        free_parses_remaining: null,
    },
};

// Merge an entitlement projection into state.me, then (re)render the
// Classes view so the Upload-syllabus button reflects it.
async function renderClassesWith(sidePanel, mePatch) {
    await sidePanel.evaluate(async (patch) => {
        const { state } = await import("./lib/state.js");
        state.me = Object.assign(
            { id: 1, email: "t@e.com", timezone: "America/New_York",
              calendar_token: "tok", calendar_urls: { webcal_url: "", https_url: "" } },
            patch,
        );
        const cls = await import("./lib/views/classes.js");
        await cls.loadClasses();
    }, mePatch);
    await sidePanel.waitForSelector(".classes-actions");
}

function uploadBtn(sidePanel) {
    return sidePanel.locator(".classes-actions button", { hasText: "Upload syllabus" });
}

test.describe("Syllabus-parse entitlement", () => {
    test("S1 own key → Upload enabled, Settings says unlimited", async () => {
        const { context, sidePanel } = await launchPanel();
        try {
            await renderClassesWith(sidePanel, ME.ownKey);
            await expect(uploadBtn(sidePanel),
                "Own key → Upload must NOT be disabled").not.toHaveClass(/is-disabled/);

            await sidePanel.evaluate(async () => {
                const s = await import("./lib/forms/settings.js");
                s.showSettings();
            });
            await expect(sidePanel.locator("#settings-parse-usage"))
                .toContainText(/unlimited/i);
        } finally {
            await context.close();
        }
    });

    test("S2 free pool with parses left → Upload enabled, Settings shows the count", async () => {
        const { context, sidePanel } = await launchPanel();
        try {
            await renderClassesWith(sidePanel, ME.poolLeft);
            await expect(uploadBtn(sidePanel),
                "Parses left → Upload enabled").not.toHaveClass(/is-disabled/);

            await sidePanel.evaluate(async () => {
                const s = await import("./lib/forms/settings.js");
                s.showSettings();
            });
            const line = sidePanel.locator("#settings-parse-usage");
            await expect(line).toContainText("2 of 5 used");
            await expect(line).toContainText("3 left");
        } finally {
            await context.close();
        }
    });

    test("S3 free pool exhausted → Upload disabled; click bounces to Settings with the message", async () => {
        const { context, sidePanel } = await launchPanel();
        try {
            await renderClassesWith(sidePanel, ME.poolExhausted);
            const btn = uploadBtn(sidePanel);
            await expect(btn, "Exhausted → Upload disabled").toHaveClass(/is-disabled/);
            await expect(btn).toHaveAttribute("title", /used all your free/i);

            await btn.click();
            await expect(sidePanel.locator("#settings-view"),
                "Click while exhausted → Settings surface opens").toBeVisible();
            await expect(sidePanel.locator("#settings-xai-status-line"))
                .toContainText(/used all your free/i);
            await expect(sidePanel.locator("#settings-parse-usage"))
                .toContainText(/add your own xAI key/i);
        } finally {
            await context.close();
        }
    });

    test("S5 admin-granted unlimited (no own key) → Upload enabled, Settings says granted by admin", async () => {
        const { context, sidePanel } = await launchPanel();
        try {
            await renderClassesWith(sidePanel, ME.granted);
            await expect(uploadBtn(sidePanel),
                "Admin-granted → Upload enabled even with no own key")
                .not.toHaveClass(/is-disabled/);

            await sidePanel.evaluate(async () => {
                const s = await import("./lib/forms/settings.js");
                s.showSettings();
            });
            await expect(sidePanel.locator("#settings-parse-usage"))
                .toContainText(/granted by an admin/i);
        } finally {
            await context.close();
        }
    });

    test("S4 no own key + no server pool → Upload disabled with set-your-key tooltip", async () => {
        const { context, sidePanel } = await launchPanel();
        try {
            await renderClassesWith(sidePanel, ME.noPool);
            const btn = uploadBtn(sidePanel);
            await expect(btn, "No pool + no key → Upload disabled").toHaveClass(/is-disabled/);
            await expect(btn).toHaveAttribute("title", /Set your xAI API key/i);

            await sidePanel.evaluate(async () => {
                const s = await import("./lib/forms/settings.js");
                s.showSettings();
            });
            await expect(sidePanel.locator("#settings-parse-usage"))
                .toContainText(/isn't configured on this server/i);
        } finally {
            await context.close();
        }
    });
});
