// Test fixture: launches Chromium with the unpacked extension loaded,
// mocks every server endpoint the side panel pings on boot, returns the
// extension's side-panel page ready to interact with.
//
// Why mocks instead of a real Compass server? The form's state-machine
// rules (visibility / disabled / required) are pure UI behavior. They
// don't depend on what the server actually stores. Mocking removes the
// test's coupling to the server's life-cycle.
//
// CommonJS module — Playwright on Windows + Git Bash has loader issues
// with ESM that broke test discovery (see package.json scripts comment).

const { chromium } = require("@playwright/test");
const { resolve } = require("node:path");
const { mkdtempSync } = require("node:fs");
const { tmpdir } = require("node:os");
const { join } = require("node:path");

// extension-experimental/ — the unpacked extension we want Chrome to load.
const EXTENSION_PATH = resolve(__dirname, "..", "..");

// Minimal /me.json payload that satisfies the panel's boot sequence.
const FAKE_ME = {
    id: 1,
    email: "test@example.com",
    timezone: "America/New_York",
    xai_api_key_set: false,
    xai_api_key_masked: null,
    calendar_token: "test-token-123",
    calendar_urls: { webcal_url: "", https_url: "" },
};

// Two pre-baked classes + a couple tags. The form's add-class dropdown
// reads from /classes.json; tag dropdown from /tags.json. Real data so
// dropdowns render with real options.
const FAKE_CLASSES = [
    { id: 1, code: "MATH 250", name: "Calculus II" },
    { id: 2, code: "PHYS 101", name: "Intro Physics" },
];
const FAKE_TAGS = [
    { id: 100, name: "exam", color: "#B23A3A", is_system: true, system_key: "exam" },
    { id: 101, name: "quiz", color: "#5C8A3A", is_system: true, system_key: "quiz" },
];
const FAKE_TODAY = { today: "2026-05-10", buckets: [] };

// Wire all mocks on a context. Every fetch the panel makes is captured
// here — no real network leaves the test.
async function installMocks(context) {
    await context.route("**/me.json", (route) => {
        route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify(FAKE_ME),
        });
    });
    await context.route("**/classes.json", (route) => {
        route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify(FAKE_CLASSES),
        });
    });
    await context.route("**/tags.json", (route) => {
        route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify(FAKE_TAGS),
        });
    });
    await context.route("**/today.json", (route) => {
        route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify(FAKE_TODAY),
        });
    });
    // Timezone auto-save fires on boot; just return success.
    await context.route("**/settings/timezone", (route) => {
        route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({ saved: true, unchanged: true }),
        });
    });
}

// Launch a new browser context with the extension loaded. Returns
// { context, sidePanel, extensionId } — the side-panel page is already
// navigated and in the logged-in app surface (boot has resolved against
// mocks).
async function launchPanel() {
    const userDataDir = mkdtempSync(join(tmpdir(), "compass-ext-test-"));
    const context = await chromium.launchPersistentContext(userDataDir, {
        headless: false,  // Manifest V3 service workers don't run in headless.
        args: [
            `--disable-extensions-except=${EXTENSION_PATH}`,
            `--load-extension=${EXTENSION_PATH}`,
            "--no-first-run",
            "--no-default-browser-check",
        ],
    });
    await installMocks(context);

    // Wait for the extension's service worker so we can read its id.
    let [worker] = context.serviceWorkers();
    if (!worker) worker = await context.waitForEvent("serviceworker");
    const extensionId = worker.url().split("/")[2];

    const sidePanel = await context.newPage();
    await sidePanel.goto(`chrome-extension://${extensionId}/sidepanel.html`);
    // Wait for the panel to finish booting — the FAB only appears once
    // /me.json resolves and showApp() fires.
    await sidePanel.waitForSelector("#add-task-fab:not([hidden])", { timeout: 10_000 });

    return { context, sidePanel, extensionId };
}

module.exports = { launchPanel };
