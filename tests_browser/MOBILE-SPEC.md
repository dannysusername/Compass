# Mobile layout spec

Objective definition of "looks good on mobile" for the Compass web app,
so correctness is a passing test, not a judgment call. `test_mobile_sweep.py`
enforces every rule below across the full page × width matrix.

This mirrors the `extension-experimental/` workflow: spec the behaviour
first, encode it as a state/assertion test, *then* make the code pass.

## Criteria (each is an assertion in the sweep)

- **S1 — No horizontal overflow.** `document.documentElement.scrollWidth`
  must be ≤ viewport width + 1px. A page that scrolls sideways is broken.
  (This is the rule the old `/admin` table violated — its 5-column table
  forced the body wider than the screen, pushing content "off the side".)

- **S2 — Primary container in-bounds.** The main content wrapper
  (`main` / `.setup-card` / `.home-layout` / `.month-grid`) must have a
  bounding box fully inside the viewport: `left ≥ -1` and
  `right ≤ innerWidth + 1`. Catches content shoved off-screen even when
  the body itself didn't grow.

- **S3 — Reachable primary actions.** On a touch (coarse-pointer) context,
  the always-present primary controls — the nav hamburger, and any
  visible form submit `button` — render at least 40px tall so they're
  tappable.

- **S4 — Modals fit.** With a modal open, `.modal-dialog` width must be
  ≤ innerWidth and its close button fully within the viewport, so the
  user can always dismiss it.

## Page × width matrix

Widths: **320** (small phone / iPhone SE), **390** (modern iPhone),
**768** (tablet portrait / large phone landscape).

| Page                  | Auth        | Notes                                  |
|-----------------------|-------------|----------------------------------------|
| `/login`              | logged out  | S1, S2, S3                             |
| `/signup`             | logged out  | S1, S2, S3                             |
| `/forgot`             | logged out  | S1, S2, S3                             |
| `/reset/{token}`      | logged out  | S1, S2, S3 (real token via test hook)  |
| `/`                   | regular     | seeded with 1 class + 1 task           |
| `/today`              | regular     | seeded                                 |
| `/week`               | regular     | stacked-agenda calendar                |
| `/settings`           | regular     | long iCal URL must wrap, not overflow  |
| `/classes/{id}`       | regular     | the seeded class detail page           |
| `/admin`              | admin       | requires `ADMIN_EMAILS` + admin user   |
| `/` + add-task modal  | regular     | S4                                     |

## Out of scope

- Native apps and the Chromium extension side panel (separate surface,
  covered by `extension-experimental/tests/specs/`).
- Pixel-perfect visual polish — these are structural/layout guarantees.
  Aesthetic review is the screenshot pass, not an assertion.
