// State-machine test for the Add-task form.
//
// The form has three boolean-shaped inputs that drive UI rules:
//   - is_all_day:  on / off
//   - rrule:       set ("FREQ=DAILY") / unset ("")
//   - hasDue:      Due field has a value / cleared
//
// 2 × 2 × 2 = 8 reachable states. We enumerate ALL of them deterministically
// and assert the invariants we agreed on (the rules from POSSIBILITIES.md):
//
//   I-1: rrule unset                  → End-date field is HIDDEN  (the bug that bit us)
//   I-2: rrule set                    → End-date field is VISIBLE
//   I-3: all_day OR rrule             → Starts is DISABLED
//   I-4: all_day off AND rrule unset  → Starts is ENABLED
//
// Adding a new field later? Add it to the loop variables + add an
// invariant. The full state space gets re-tested automatically.
//
// fast-check is loaded but not strictly needed for 3 booleans — when the
// state space grows (e.g., a 4th boolean = 16 states, or a select with
// N options), switch the explicit loop to fc.assert + fc.record.

const { test, expect } = require("@playwright/test");
const { launchPanel } = require("../fixtures/extension.js");

// Helper: set the form to a target state. Each call resets to baseline
// then applies the requested toggles, so per-state runs are independent.
async function setFormState(sidePanel, state) {
    const allDayBox = sidePanel.locator("#add-all-day");
    const rruleSel = sidePanel.locator("#add-rrule");
    const dueInput = sidePanel.locator("#add-task-form input[name='due_at']");

    // Reset toggles to a known baseline.
    if (await allDayBox.isChecked()) await allDayBox.click();
    await rruleSel.selectOption("");
    await dueInput.fill("");

    // Apply the target state.
    if (state.isAllDay) await allDayBox.click();
    if (state.hasRrule) await rruleSel.selectOption("FREQ=DAILY");
    if (state.hasDue) {
        if (state.isAllDay) await dueInput.fill("2026-06-15");
        else await dueInput.fill("2026-06-15T10:00");
    }
}

// Helper: assert the four invariants on the current form DOM.
async function assertInvariants(sidePanel, state) {
    const endDateLabel = sidePanel.locator("#add-until-label");
    const startsInput = sidePanel.locator("#add-task-form input[name='starts_at']");
    const stateStr = JSON.stringify(state);

    // I-1 / I-2: End-date visibility tracks rrule presence.
    if (state.hasRrule) {
        await expect(endDateLabel, `${stateStr} I-2 (end-date should be visible)`).toBeVisible();
    } else {
        await expect(endDateLabel, `${stateStr} I-1 (end-date should be hidden)`).toBeHidden();
    }

    // I-3 / I-4: Starts disabled iff all_day OR rrule.
    const shouldDisable = state.isAllDay || state.hasRrule;
    if (shouldDisable) {
        await expect(startsInput, `${stateStr} I-3 (starts should be disabled)`).toBeDisabled();
    } else {
        await expect(startsInput, `${stateStr} I-4 (starts should be enabled)`).toBeEnabled();
    }
}

test("Add-task form invariants hold across every reachable state", async () => {
    const { context, sidePanel } = await launchPanel();
    try {
        // Open the FAB once for all states.
        await sidePanel.locator("#add-task-fab").click();
        await sidePanel.waitForSelector("#add-task-view:not([hidden])");

        // Enumerate the full 2³ = 8 state space.
        const states = [];
        for (const isAllDay of [false, true]) {
            for (const hasRrule of [false, true]) {
                for (const hasDue of [false, true]) {
                    states.push({ isAllDay, hasRrule, hasDue });
                }
            }
        }

        for (const state of states) {
            await setFormState(sidePanel, state);
            await assertInvariants(sidePanel, state);
        }
    } finally {
        await context.close();
    }
});
