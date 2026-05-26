"""
E2E player-perspective tests — dispatcher_console.html
=======================================================
Simulate a real player session from INICIAR through the first few beats of
'bienvenida'.

Design constraints
------------------
  * No G.* state manipulation
  * No direct engine function calls (advance, enterScene, vgnContinue, etc.)
  * Inputs come only from real browser interactions: mouse click, keyboard
  * Assertions observe the DOM only: element visibility, CSS classes, child counts

The bienvenida scene begins with a password input beat (kind=input).
Correct answer: "clavesecreta"
After password submission:
  beat 1  — vignette open (overlay visible, requires CONTINUAR / Enter / Space)
  beat 2  — sfx           (no overlay; click/Enter/Space adds an entry to narr-feed)
  beat 3  — vignette open
  ...

Usage
-----
    python scripts/test_e2e_player.py           # headless
    python scripts/test_e2e_player.py --headed  # show browser window

Requirements
------------
    pip install playwright
    python -m playwright install chromium   # or use system Edge / Chrome
"""

import argparse, sys
from pathlib import Path
from playwright.sync_api import sync_playwright, Page

# ─────────────────────────────────────────────────────────────────────────────
URL      = (Path(__file__).resolve().parent.parent / "dispatcher_console.html").as_uri()
PASSWORD = "clavesecreta"

PASS_SYM = "OK"
FAIL_SYM = "FAIL"


# ─────────────────────────────────────────────────────────────────────────────
# Player-perspective helpers  (DOM-only, no G.* reads or writes)
# ─────────────────────────────────────────────────────────────────────────────

def start_session(page: Page) -> None:
    """Open the game and click INICIAR, then click CONTINUAR once to advance past
    the scene-cut fade and land on the first beat (password input).

    Timing notes:
      - startGame() fires enterScene() after 900 ms
      - enterScene() sets G.sceneCutPending=true but does NOT call advance()
      - First player click on CONTINUAR triggers advance(), which detects
        G.sceneCutPending and calls doFade(() => advance()) — 360 ms fade
      - After the fade, advance() processes beat 0 (kind=input) and
        refreshControls() creates #inp-answer in the DOM
    """
    page.set_viewport_size({"width": 1280, "height": 720})
    page.goto(URL)
    page.wait_for_timeout(800)           # page initialisation
    page.click("button.cinema-btn")      # click INICIAR
    page.wait_for_timeout(2200)          # 900 ms startGame timer + buffer
    # One player click to cross the scene-cut fade and reach beat 0 (input)
    page.click("#btn-continue")
    page.wait_for_timeout(700)           # 360 ms doFade + buffer


def submit_password(page: Page) -> None:
    """Fill in the password and press Enter — exactly as a player would."""
    page.fill("#inp-answer", PASSWORD)
    page.keyboard.press("Enter")
    page.wait_for_timeout(700)           # allow advance() + vignette fade-in (beat 1)


def vignette_visible(page: Page) -> bool:
    """True when the vignette overlay carries the .vgn-visible CSS class."""
    return page.evaluate(
        "document.getElementById('vignette-overlay').classList.contains('vgn-visible')"
    )


def narr_count(page: Page) -> int:
    """Number of rendered beat entries in #narr-feed."""
    return page.evaluate("document.getElementById('narr-feed').children.length")


def click_until_vignette_closes(page: Page, method: str = "click", max_clicks: int = 10) -> int:
    """Press click / Enter / Space repeatedly until the vignette overlay closes.

    In bienvenida the vignette chain is:
        beat 1: vignette open   ← overlay opens
        beat 2: sfx             ← audio side-effect: vgnContinue calls advance(), stays open
        beat 3: vignette open   ← vignette seq: vgnContinue calls advance(), stays open
        beat 4: ambient         ← audio side-effect: vgnContinue calls advance(), stays open
        beat 5: line (ERIC)     ← NOT vignette seq / audio: vgnContinue closes overlay

    Four player inputs are therefore needed to close the overlay in this scene.
    The loop handles this without hard-coding the exact count.

    Returns the number of inputs that were needed.
    """
    for i in range(max_clicks):
        if not vignette_visible(page):
            return i
        if method == "click":
            page.click("#btn-continue")
        elif method == "Enter":
            page.keyboard.press("Enter")
        else:
            page.keyboard.press("Space")
        page.wait_for_timeout(250)   # > 90 ms debounce; advance() is synchronous
    assert not vignette_visible(page), (
        f"Vignette still visible after {max_clicks} {method!r} inputs.\n"
        f"The vignette sequence may have changed — check bienvenida beats in "
        f"data/dispatcher_story.json and update max_clicks if needed."
    )
    return max_clicks


# ─────────────────────────────────────────────────────────────────────────────
# Test utilities
# ─────────────────────────────────────────────────────────────────────────────

def run_test(name: str, fn) -> bool:
    try:
        fn()
        print(f"  {PASS_SYM}  {name}")
        return True
    except AssertionError as e:
        print(f"  {FAIL_SYM} {name}: {e}")
        return False
    except Exception as e:
        print(f"  {FAIL_SYM} {name}: unexpected error: {e}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────

def test_game_starts_with_input(page: Page) -> None:
    """After clicking INICIAR the game automatically shows the password prompt."""
    start_session(page)
    assert page.is_visible("#inp-answer"), (
        "#inp-answer not visible after INICIAR.\n"
        "Expected: game enters the first beat (password input) on its own."
    )


def test_password_enter_submits(page: Page) -> None:
    """Typing the correct password + Enter advances past the input beat and opens
    the first vignette."""
    start_session(page)
    submit_password(page)
    assert vignette_visible(page), (
        "Vignette not visible after correct password submission.\n"
        "Expected: beat 1 (vignette open) loads automatically after input."
    )


def test_click_closes_vignette(page: Page) -> None:
    """Clicking CONTINUAR repeatedly navigates through the bienvenida vignette
    chain and eventually closes the overlay."""
    start_session(page)
    submit_password(page)
    assert vignette_visible(page), "Precondition: vignette must be open"
    click_until_vignette_closes(page, "click")
    assert not vignette_visible(page), (
        "Vignette still visible after click chain.\n"
        "Expected: repeated clicks drive through the vignette sequence and close it."
    )


def test_enter_closes_vignette(page: Page) -> None:
    """Pressing Enter repeatedly navigates through the vignette chain (parity with click)."""
    start_session(page)
    submit_password(page)
    assert vignette_visible(page), "Precondition: vignette must be open"
    click_until_vignette_closes(page, "Enter")
    assert not vignette_visible(page), (
        "Vignette still visible after Enter chain.\n"
        "Expected: Enter parity with click through the vignette sequence."
    )


def test_space_closes_vignette(page: Page) -> None:
    """Pressing Space repeatedly navigates through the vignette chain (parity with click)."""
    start_session(page)
    submit_password(page)
    assert vignette_visible(page), "Precondition: vignette must be open"
    click_until_vignette_closes(page, "Space")
    assert not vignette_visible(page), (
        "Vignette still visible after Space chain.\n"
        "Expected: Space parity with click through the vignette sequence."
    )


def _reach_narration_mode(page: Page) -> None:
    """Start game, submit password, navigate through the vignette chain, land in narration."""
    start_session(page)
    submit_password(page)
    assert vignette_visible(page), "Precondition: opening vignette must be visible"
    click_until_vignette_closes(page, "click")
    assert not vignette_visible(page), "Precondition: vignette must be closed now"
    # Wait until the game's close-animation flag clears (G.vignetteClosing=true
    # disables #btn-continue for 620 ms).  page.click() auto-waits for the DOM
    # state, but page.keyboard.press() fires immediately, so we gate on the
    # real DOM state instead of a fixed sleep.
    page.wait_for_selector("#btn-continue:not([disabled])", timeout=10000)


def test_click_advances_narration(page: Page) -> None:
    """In narration mode, clicking CONTINUAR appends at least one new entry to
    #narr-feed."""
    _reach_narration_mode(page)
    b0 = narr_count(page)
    page.click("#btn-continue")
    page.wait_for_timeout(400)
    b1 = narr_count(page)
    assert b1 > b0, f"narr-feed unchanged after click ({b0} -> {b1})"


def test_enter_advances_narration(page: Page) -> None:
    """In narration mode, pressing Enter appends at least one new entry to
    #narr-feed."""
    _reach_narration_mode(page)
    b0 = narr_count(page)
    page.keyboard.press("Enter")
    page.wait_for_timeout(400)
    b1 = narr_count(page)
    assert b1 > b0, f"narr-feed unchanged after Enter ({b0} -> {b1})"


def test_space_advances_narration(page: Page) -> None:
    """In narration mode, pressing Space appends at least one new entry to
    #narr-feed."""
    _reach_narration_mode(page)
    b0 = narr_count(page)
    page.keyboard.press("Space")
    page.wait_for_timeout(400)
    b1 = narr_count(page)
    assert b1 > b0, f"narr-feed unchanged after Space ({b0} -> {b1})"


# ─────────────────────────────────────────────────────────────────────────────
TESTS = [
    ("E2E — INICIAR shows password prompt",       test_game_starts_with_input),
    ("E2E — password + Enter opens vignette",      test_password_enter_submits),
    ("E2E — click closes vignette (player UX)",    test_click_closes_vignette),
    ("E2E — Enter closes vignette (parity)",       test_enter_closes_vignette),
    ("E2E — Space closes vignette (parity)",       test_space_closes_vignette),
    ("E2E — click advances narration beat",        test_click_advances_narration),
    ("E2E — Enter advances narration beat",        test_enter_advances_narration),
    ("E2E — Space advances narration beat",        test_space_advances_narration),
]


# ─────────────────────────────────────────────────────────────────────────────
def _launch_browser(playwright, headed: bool):
    base_args = ["--allow-file-access-from-files"]
    for channel in ("msedge", "chrome"):
        try:
            return playwright.chromium.launch(
                headless=not headed, args=base_args, channel=channel
            )
        except Exception:
            pass
    try:
        return playwright.chromium.launch(headless=not headed, args=base_args)
    except Exception:
        pass
    sys.exit(
        "No usable browser found.\n"
        "Install one of:\n"
        "  * python -m playwright install chromium\n"
        "  * Microsoft Edge  (pre-installed on Windows 10/11)\n"
        "  * Google Chrome"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="E2E player-perspective test suite")
    parser.add_argument("--headed", action="store_true", help="Show browser window")
    args = parser.parse_args()

    passed = failed = 0

    with sync_playwright() as p:
        browser = _launch_browser(p, args.headed)

        print(f"\nE2E Player Tests — dispatcher_console.html")
        print(f"{'=' * 54}")
        print(f"File : {URL}")
        print(f"Tests: {len(TESTS)}")
        print(f"Note : Each test opens a fresh browser page (no shared state).\n")

        for name, fn in TESTS:
            page = browser.new_page()
            ok = run_test(name, lambda p=page, f=fn: f(p))
            page.close()
            if ok:
                passed += 1
            else:
                failed += 1

        browser.close()

    print(f"\n{'=' * 54}")
    print(f"PASSED {passed}/{len(TESTS)}    FAILED {failed}/{len(TESTS)}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
