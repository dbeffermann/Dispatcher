"""
Regression test: unified input entrypoint (click / Enter / Space)
==================================================================
Verifies that all three primary-action inputs (real click, Enter, Space) produce
identical beat-advance behaviour in normal scenes and while a vignette overlay is
active.  Also statically asserts that no CONTINUAR button calls advance() directly.

Architecture invariant under test
----------------------------------
  triggerPrimaryAction(source)  <-- ONLY entrypoint for advancing the experience
      |
      v
  handleBlockedContinue()  <-- state router
      |
      +-- vignetteOpen  --> vgnContinue()
      +-- inputMode     --> submitInput()
      +-- (default)     --> advance()

Neither #btn-continue nor #vgn-continue-btn may call advance(), vgnContinue(),
or submitInput() directly.  Any new "continue" button or keyboard shortcut MUST
route through triggerPrimaryAction() so the 90 ms debounce and state gate apply
uniformly.

Usage
-----
    python scripts/test_input_regression.py           # headless (default)
    python scripts/test_input_regression.py --headed  # show browser window

Requirements
------------
    pip install playwright

    Browser selection (first match wins):
      1. Installed Chromium  (playwright install chromium)
      2. System Edge         (msedge — present on all modern Windows installs)
      3. System Chrome       (--channel=chrome flag)

    For option 1:  python -m playwright install chromium
    For options 2/3 no extra download is needed.
"""

import re
import sys
import argparse
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright, Page
except ImportError:
    sys.exit(
        "Playwright not installed.\n"
        "Run:  pip install playwright && python -m playwright install chromium"
    )

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
HTML_PATH = Path(__file__).resolve().parents[1] / "dispatcher_console.html"
URL = HTML_PATH.as_uri()

PASS_SYM = "[OK]"
FAIL_SYM = "[FAIL]"

# Scene IDs used by the regression suite. Switch if the story data changes.
VIGNETTE_TEST_SCENE = "supervisor"
NORMAL_TEST_SCENE   = "test_authflow"   # 3 narration beats, no vignettes


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def setup(page: Page) -> None:
    """Load the game and bootstrap the global G state via INICIAR."""
    page.set_viewport_size({"width": 1280, "height": 720})
    page.goto(URL, wait_until="load")
    page.wait_for_timeout(800)
    page.click('button:has-text("INICIAR")')
    page.wait_for_timeout(2000)
    # Guarantee the game is fully started regardless of animation timing
    page.evaluate("G.gameStarted = true")


def enter_scene(page: Page, scene_id: str) -> None:
    """Enter a scene with all rate-limiters cleared and scene-cut fade suppressed.

    ``enterScene()`` always sets ``G.sceneCutPending = true``, which causes
    the first ``advance()`` call to trigger a 360 ms CSS fade before processing
    any beat.  For regression tests we don't need the visual fade; suppressing it
    makes every advance() call deterministic and avoids timing races.
    """
    page.evaluate(
        f"G.gameStarted = true;"
        f"G.primaryActionAt = 0;"
        f"G.advanceLastAt = 0;"
        f"G.advanceInFlight = false;"
        f"enterScene('{scene_id}');"
        f"G.sceneCutPending = false;"
    )
    page.wait_for_timeout(200)


def click_continue(page: Page) -> None:
    """Click #btn-continue and blur it afterward to avoid focus-trap side effects."""
    page.click("#btn-continue")
    page.evaluate("document.getElementById('btn-continue').blur()")


def beat_index(page: Page) -> int:
    return page.evaluate("G.beatIndex")


def open_vignette(page: Page) -> None:
    """Open the vignette overlay in VIGNETTE_TEST_SCENE.

    In the ``supervisor`` scene beat 0 is ``{kind: vignette, action: open}``,
    so one advance() opens the overlay and leaves G.beatIndex=1.  Beat 1 is a
    plain narration beat, so the *next* CONTINUAR click will close the overlay
    (vgnContinue peeks at beat 1, sees it is not a vignette-sequence beat, and
    immediately sets G.vignetteOpen=false).
    """
    enter_scene(page, VIGNETTE_TEST_SCENE)
    page.evaluate("advance()")        # beat 0: vignette open → G.vignetteOpen=true
    page.wait_for_timeout(600)        # allow overlay fade-in CSS transition (~300 ms)
    # Reset debounce so the test's click/key is not gated by the advance() above
    page.evaluate("G.primaryActionAt = 0; G.advanceLastAt = 0;")
    assert page.evaluate("G.vignetteOpen"), \
        f"Expected vignetteOpen=true after advancing into {VIGNETTE_TEST_SCENE}"


def run_test(name: str, fn) -> bool:
    try:
        fn()
        print(f"  {PASS_SYM} {name}")
        return True
    except AssertionError as e:
        print(f"  {FAIL_SYM} {name}: {e}")
        return False
    except Exception as e:
        print(f"  {FAIL_SYM} {name}: unexpected error: {e}")
        return False


# ---------------------------------------------------------------------------
# Test functions
# ---------------------------------------------------------------------------
def test_normal_click(page: Page) -> None:
    """Click on #btn-continue advances the beat by 1."""
    enter_scene(page, NORMAL_TEST_SCENE)
    b0 = beat_index(page)
    click_continue(page)
    page.wait_for_timeout(300)
    b1 = beat_index(page)
    assert b1 == b0 + 1, f"expected beat {b0+1}, got {b1}"


def test_normal_enter(page: Page) -> None:
    """Enter advances the beat by 1 in a normal scene."""
    enter_scene(page, NORMAL_TEST_SCENE)
    b0 = beat_index(page)
    page.keyboard.press("Enter")
    page.wait_for_timeout(300)
    b1 = beat_index(page)
    assert b1 == b0 + 1, f"expected beat {b0+1}, got {b1}"


def test_normal_space(page: Page) -> None:
    """Space advances the beat by 1 in a normal scene."""
    enter_scene(page, NORMAL_TEST_SCENE)
    b0 = beat_index(page)
    page.keyboard.press("Space")
    page.wait_for_timeout(300)
    b1 = beat_index(page)
    assert b1 == b0 + 1, f"expected beat {b0+1}, got {b1}"


def test_click_enter_space_identical(page: Page) -> None:
    """Click, Enter and Space each advance exactly 1 beat (parity check)."""
    results = {}

    # Click — use click_continue() which resets debounce and blurs focus after
    enter_scene(page, NORMAL_TEST_SCENE)
    b0 = beat_index(page)
    click_continue(page)
    page.wait_for_timeout(300)
    results["click"] = beat_index(page) - b0

    # Enter — enter_scene() resets G.primaryActionAt so debounce cannot bleed over
    enter_scene(page, NORMAL_TEST_SCENE)
    b0 = beat_index(page)
    page.keyboard.press("Enter")
    page.wait_for_timeout(300)
    results["Enter"] = beat_index(page) - b0

    # Space
    enter_scene(page, NORMAL_TEST_SCENE)
    b0 = beat_index(page)
    page.keyboard.press("Space")
    page.wait_for_timeout(300)
    results["Space"] = beat_index(page) - b0

    assert all(v == 1 for v in results.values()), (
        "All inputs must advance exactly 1 beat: " +
        ", ".join(f"{k}={v}" for k, v in results.items())
    )


def test_vignette_click(page: Page) -> None:
    """Click on #btn-continue closes an active vignette overlay."""
    open_vignette(page)
    click_continue(page)
    page.wait_for_timeout(500)
    assert not page.evaluate("G.vignetteOpen"), "vignetteOpen should be false after click"


def test_vignette_enter(page: Page) -> None:
    """Enter closes an active vignette overlay."""
    open_vignette(page)
    page.keyboard.press("Enter")
    page.wait_for_timeout(500)
    assert not page.evaluate("G.vignetteOpen"), "vignetteOpen should be false after Enter"


def test_vignette_space(page: Page) -> None:
    """Space closes an active vignette overlay."""
    open_vignette(page)
    page.keyboard.press("Space")
    page.wait_for_timeout(500)
    assert not page.evaluate("G.vignetteOpen"), "vignetteOpen should be false after Space"


def test_vignette_enter_space_identical(page: Page) -> None:
    """Click, Enter and Space on an open vignette all close it (parity check)."""
    results = {}
    for method in ("click", "enter", "space"):
        open_vignette(page)
        if method == "click":
            click_continue(page)
        elif method == "enter":
            page.keyboard.press("Enter")
        else:
            page.keyboard.press("Space")
        page.wait_for_timeout(500)
        results[method] = not page.evaluate("G.vignetteOpen")   # True = closed OK

    assert all(results.values()), (
        "All three inputs must close the vignette: " +
        ", ".join(f"{k}={'closed' if v else 'STILL OPEN'}" for k, v in results.items())
    )


def test_overlay_pointer_events_none(page: Page) -> None:
    """#vignette-overlay must have pointer-events:none when vgn-visible, so clicks
    pass through to #btn-continue instead of being swallowed by the overlay."""
    open_vignette(page)
    pe = page.evaluate(
        "window.getComputedStyle(document.getElementById('vignette-overlay')).pointerEvents"
    )
    assert pe == "none", (
        f"overlay pointer-events should be 'none' while visible (got {pe!r}).\n"
        "  Fix: remove 'pointer-events:auto' from #vignette-overlay.vgn-visible CSS rule."
    )


def test_vgn_btn_pointer_events_auto(page: Page) -> None:
    """#vgn-continue-btn must retain pointer-events:auto (child overrides parent none)."""
    open_vignette(page)
    pe = page.evaluate(
        "window.getComputedStyle(document.getElementById('vgn-continue-btn')).pointerEvents"
    )
    assert pe == "auto", (
        f"#vgn-continue-btn pointer-events should be 'auto' (got {pe!r}).\n"
        "  Fix: ensure '#vgn-continue-btn {{ pointer-events:auto; }}' CSS rule is present."
    )


def test_buttons_use_trigger_not_advance(page: Page) -> None:
    """Static HTML check: both CONTINUAR buttons must call triggerPrimaryAction(),
    never advance() / vgnContinue() / submitInput() directly."""
    html = page.content()

    btn_main = re.search(r'id="btn-continue"[^>]*>', html)
    btn_vgn  = re.search(r'id="vgn-continue-btn"[^>]*>', html)
    assert btn_main, "#btn-continue not found in rendered HTML"
    assert btn_vgn,  "#vgn-continue-btn not found in rendered HTML"

    FORBIDDEN = ("advance()", "vgnContinue()", "submitInput()")
    for tag, label in ((btn_main.group(), "#btn-continue"),
                       (btn_vgn.group(),  "#vgn-continue-btn")):
        for bad in FORBIDDEN:
            assert bad not in tag, (
                f"{label} must not call {bad} directly — "
                f"route through triggerPrimaryAction() instead.\n  Tag: {tag}"
            )
        assert "triggerPrimaryAction" in tag, (
            f"{label} must call triggerPrimaryAction(). Tag: {tag}"
        )


def test_debounce_blocks_double_click(page: Page) -> None:
    """Two synchronous JS clicks must advance only one beat (90 ms debounce guard)."""
    enter_scene(page, NORMAL_TEST_SCENE)
    b0 = beat_index(page)
    page.evaluate("""
        () => {
            triggerPrimaryAction('click');
            triggerPrimaryAction('click');
        }
    """)
    page.wait_for_timeout(300)
    b1 = beat_index(page)
    assert b1 == b0 + 1, (
        f"double-call should advance exactly 1 beat (debounce), got {b1 - b0}"
    )


def test_vgn_btn_disabled_during_close(page: Page) -> None:
    """#vgn-continue-btn must be disabled for the ~580 ms fade-out after close."""
    open_vignette(page)
    # Trigger close
    page.evaluate("_vgnClose()")
    page.wait_for_timeout(60)   # shortly after close starts (well within 580 ms window)
    disabled = page.evaluate("document.getElementById('vgn-continue-btn').disabled")
    assert disabled, (
        "#vgn-continue-btn should be disabled during the 580 ms fade-out to prevent "
        "stale clicks registering a second action."
    )


# ---------------------------------------------------------------------------
# Test registry
# ---------------------------------------------------------------------------
TESTS = [
    # --- Normal scene: three inputs ---
    ("Normal scene — click advances beat",              test_normal_click),
    ("Normal scene — Enter advances beat",              test_normal_enter),
    ("Normal scene — Space advances beat",              test_normal_space),
    ("Normal scene — click / Enter / Space identical",  test_click_enter_space_identical),
    # --- Vignette: three inputs ---
    ("Vignette — click closes overlay",                 test_vignette_click),
    ("Vignette — Enter closes overlay",                 test_vignette_enter),
    ("Vignette — Space closes overlay",                 test_vignette_space),
    ("Vignette — click / Enter / Space identical",      test_vignette_enter_space_identical),
    # --- CSS / pointer-events invariants ---
    ("Overlay pointer-events:none when visible",        test_overlay_pointer_events_none),
    ("#vgn-continue-btn pointer-events:auto",           test_vgn_btn_pointer_events_auto),
    # --- Architecture invariants ---
    ("Both buttons route through triggerPrimaryAction", test_buttons_use_trigger_not_advance),
    ("Debounce blocks rapid double-click",              test_debounce_blocks_double_click),
    ("Vgn button disabled during 580 ms fade-out",      test_vgn_btn_disabled_during_close),
]


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------
def _launch_browser(playwright, headed: bool):
    """Launch a browser, preferring an already-installed one over a download.

    Resolution order:
      1. System Microsoft Edge  (channel='msedge' — no executable_path needed)
      2. System Google Chrome   (channel='chrome')
      3. Playwright's own Chromium (requires: python -m playwright install chromium)
    """
    base_args = ["--allow-file-access-from-files"]

    # 1. System Edge via Playwright channel (recommended — Edge is on all modern Windows)
    try:
        return playwright.chromium.launch(
            headless=not headed, args=base_args, channel="msedge"
        )
    except Exception:
        pass

    # 2. System Chrome via Playwright channel
    try:
        return playwright.chromium.launch(
            headless=not headed, args=base_args, channel="chrome"
        )
    except Exception:
        pass

    # 3. Playwright's own downloaded Chromium
    try:
        return playwright.chromium.launch(headless=not headed, args=base_args)
    except Exception:
        pass

    sys.exit(
        "No usable browser found.\n"
        "Install one of:\n"
        "  * python -m playwright install chromium\n"
        "  * Microsoft Edge  (already on most Windows 10/11 systems)\n"
        "  * Google Chrome"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Input regression test suite")
    parser.add_argument("--headed", action="store_true", help="Show browser window")
    args = parser.parse_args()

    passed = failed = 0

    with sync_playwright() as p:
        browser = _launch_browser(p, args.headed)
        page = browser.new_page()

        print(f"\nInput Regression Tests — dispatcher_console.html")
        print(f"{'='*54}")
        print(f"File : {URL}")
        print(f"Tests: {len(TESTS)}\n")

        setup(page)

        for name, fn in TESTS:
            ok = run_test(name, lambda f=fn: f(page))
            if ok:
                passed += 1
            else:
                failed += 1

        browser.close()

    print(f"\n{'='*54}")
    print(f"PASSED {passed}/{len(TESTS)}    FAILED {failed}/{len(TESTS)}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
