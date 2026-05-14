// Playwright config tuned for testing a Manifest V3 Chromium extension.
// Headless can't load extensions, so we use a persistent context launched
// from each test. Single-worker because Chromium extension contexts
// don't parallelize cleanly. CommonJS to avoid Windows + ESM loader
// quirks in Playwright's test runner.

const { defineConfig } = require("@playwright/test");

module.exports = defineConfig({
    testDir: "./specs",
    timeout: 180_000,
    fullyParallel: false,
    workers: 1,
    reporter: [["list"]],
});
