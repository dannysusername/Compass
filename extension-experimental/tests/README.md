# Compass extension tests — state-machine + Playwright

Property-based browser tests. Drives a real Chromium with the unpacked
extension loaded; for every reachable state of a form, asserts that
the rules from `POSSIBILITIES.md` hold against the rendered DOM.

## Run

```
cd extension-experimental/tests
npm install                # one-time
npx playwright install chromium   # one-time, downloads browser
npm test                   # runs all specs
npm run test:headed        # same, but you see the Chrome window
```

> **Note:** scripts call `node node_modules/@playwright/test/cli.js test`
> directly. `npx playwright test` is broken on Windows + Git Bash (loader
> hook doesn't install) — direct invocation is the workaround.

## Adding a test for a new form / feature

The pattern is in `specs/add-task-state-machine.spec.js`. Three parts:

1. **List every input** the feature touches (toggles, dropdowns, inputs).
2. **Compute the state space** — for N booleans, that's 2^N reachable
   states. Iterate them with a nested loop. (For larger spaces — say a
   dropdown with 4 options — switch the loop to `fast-check`'s
   `fc.assert(fc.property(...))` for randomized coverage.)
3. **Write the invariants** — the rules that must hold in every state.
   One `expect(...)` per rule. Pass the state object as the assertion
   message so failures point at the exact bad combo.

```js
const states = [];
for (const a of [false, true]) {
    for (const b of [false, true]) {
        states.push({ a, b });
    }
}
for (const state of states) {
    await setFormState(sidePanel, state);
    await assertInvariants(sidePanel, state);
}
```

When the spec evolves (you add a new input, or change a rule), update
`POSSIBILITIES-<feature>.md`, then mirror the change here.

## Why this catches bugs the "I'll click through it manually" pass missed

The CSS bug we hit (End-date stayed visible because `.field { display: flex }`
overrode `[hidden]`) lives across two layers — JavaScript thinks the
field is hidden, CSS renders it visible. Static code review can't catch
this; only a real browser checking computed style can.

`expect(el).toBeHidden()` checks computed display, not just the `hidden`
attribute. So the moment a CSS rule outranks `[hidden]`, this test
goes red.

## Structure

```
tests/
├── README.md                  this file
├── package.json               npm scripts + deps
├── playwright.config.js       single-worker, headed, longer timeout
├── fixtures/
│   └── extension.js           launches Chromium with the extension
│                              loaded; mocks every server endpoint the
│                              panel pings on boot (so tests don't need
│                              a real Compass server running)
└── specs/
    └── add-task-state-machine.spec.js   the example test; copy + adapt for new forms
```
