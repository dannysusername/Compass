"""Mobile-viewport regression tests. Drives real Chromium at an iPhone-
class viewport (390x844) and asserts the responsive pass holds:

  - no page scrolls horizontally (the old header row overflowed),
  - the nav collapses to a hamburger and opens on tap,
  - the month calendar stacks to a single column instead of crushing
    seven columns into ~45px each.

Also drops full-page screenshots into ./mobile-screenshots/ so the
fix can be eyeballed without re-running the suite. That dir is
gitignored — it's evidence, not source.
"""
import pathlib

from playwright.sync_api import expect

MOBILE = {"width": 390, "height": 844}  # iPhone 12/13/14 logical px

_SHOTS = pathlib.Path(__file__).resolve().parent.parent / "mobile-screenshots"
_SHOTS.mkdir(exist_ok=True)


def _scroll_width(page) -> int:
    return page.evaluate("() => document.documentElement.scrollWidth")


def _inner_width(page) -> int:
    return page.evaluate("() => window.innerWidth")


def test_no_horizontal_overflow(signed_in_page, server_url):
    """Every top-level page must fit the viewport width. The pre-fix
    header (logo + 4 nav links + 2 buttons in a non-wrapping flex row)
    overflowed a 390px screen and forced a horizontal scrollbar."""
    page = signed_in_page
    page.set_viewport_size(MOBILE)
    for path in ("/", "/today", "/week"):
        page.goto(server_url + path)
        page.wait_for_load_state("networkidle")
        sw, iw = _scroll_width(page), _inner_width(page)
        # 1px tolerance for sub-pixel rounding.
        assert sw <= iw + 1, f"{path} overflows: scrollWidth={sw} innerWidth={iw}"
        name = "home" if path == "/" else path.strip("/")
        page.screenshot(path=str(_SHOTS / f"{name}.png"), full_page=True)


def test_hamburger_toggles_nav(signed_in_page, server_url):
    page = signed_in_page
    page.set_viewport_size(MOBILE)
    page.goto(server_url + "/")
    page.wait_for_load_state("networkidle")

    nav = page.locator("#top-nav")
    toggle = page.locator(".nav-toggle")

    expect(toggle).to_be_visible()
    expect(nav).to_be_hidden()  # collapsed by default on mobile

    toggle.click()
    expect(nav).to_be_visible()
    expect(nav.get_by_role("link", name="Week")).to_be_visible()
    page.screenshot(path=str(_SHOTS / "nav-open.png"))

    toggle.click()  # closes again
    expect(nav).to_be_hidden()


def test_month_grid_stacks(signed_in_page, server_url):
    page = signed_in_page
    page.set_viewport_size(MOBILE)
    page.goto(server_url + "/week")
    page.wait_for_load_state("networkidle")

    # Weekday strip is meaningless in a single column — hidden on mobile.
    expect(page.locator(".month-weekdays")).to_be_hidden()

    # In-month cells stack vertically: same left edge, increasing top.
    cells = page.locator(".day-cell:not(.is-out-of-month)")
    assert cells.count() >= 2
    b0 = cells.nth(0).bounding_box()
    b1 = cells.nth(1).bounding_box()
    assert abs(b0["x"] - b1["x"]) < 2, "day cells not left-aligned (not stacked)"
    assert b1["y"] > b0["y"], "second day cell is not below the first (not stacked)"
    # A stacked cell spans most of the viewport rather than ~1/7th.
    assert b0["width"] > MOBILE["width"] * 0.7, "day cell too narrow — still a 7-col grid"
