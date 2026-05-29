// Document-upload file picker. The class-detail "Add a document" row used
// to be a bare native <input type="file"> stretched across the row, so a
// click ANYWHERE on the row opened the OS file chooser. The fix hides the
// real input and adds an explicit "Choose file" button — the picker must
// open ONLY from that button, never from the row / filename text.
//
// We detect the picker via Playwright's `filechooser` event (clicking a
// control that calls input.click() fires it). bindClassDetail() wires the
// button at boot; we just reveal the #class-detail surface so the row is
// actionable.
//
// CommonJS module — matches the existing specs.

const { test, expect } = require("@playwright/test");
const { launchPanel } = require("../fixtures/extension.js");

// Reveal the class-detail surface without a full data load — the doc-upload
// row is static markup wired at boot, so showing the view is enough.
async function revealClassDetail(sidePanel) {
    await sidePanel.evaluate(async () => {
        const { showSecondary } = await import("./lib/nav.js");
        showSecondary("#class-detail");
    });
    await expect(sidePanel.locator("#class-detail-doc-choose")).toBeVisible();
}

test.describe("Document-upload file picker", () => {
    test("the Choose file button opens the picker", async () => {
        const { context, sidePanel } = await launchPanel();
        try {
            await revealClassDetail(sidePanel);
            const [chooser] = await Promise.all([
                sidePanel.waitForEvent("filechooser", { timeout: 3000 }),
                sidePanel.locator("#class-detail-doc-choose").click(),
            ]);
            expect(chooser, "Choose file → OS picker opens").toBeTruthy();
        } finally {
            await context.close();
        }
    });

    test("clicking the row / filename text does NOT open the picker", async () => {
        const { context, sidePanel } = await launchPanel();
        try {
            await revealClassDetail(sidePanel);
            let opened = false;
            sidePanel.on("filechooser", () => { opened = true; });

            // The filename label (what replaced the native input's text area)
            // and the surrounding row must be inert.
            await sidePanel.locator("#class-detail-doc-filename").click();
            await sidePanel.waitForTimeout(400);
            expect(opened, "Clicking the filename text must NOT open a picker")
                .toBe(false);
        } finally {
            await context.close();
        }
    });

    test("choosing a file shows its name; the real input stays hidden", async () => {
        const { context, sidePanel } = await launchPanel();
        try {
            await revealClassDetail(sidePanel);
            // The actual <input type=file> is hidden — never directly clickable.
            await expect(sidePanel.locator("#class-detail-doc-file"))
                .toBeHidden();

            // Set a file programmatically and fire change → filename appears.
            await sidePanel.locator("#class-detail-doc-file").setInputFiles({
                name: "lecture-3.pdf",
                mimeType: "application/pdf",
                buffer: Buffer.from("%PDF-1.4 fake"),
            });
            const label = sidePanel.locator("#class-detail-doc-filename");
            await expect(label).toHaveText("lecture-3.pdf");
            await expect(label).toHaveClass(/has-file/);
        } finally {
            await context.close();
        }
    });
});
