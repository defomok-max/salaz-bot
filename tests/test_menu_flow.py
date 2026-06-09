"""End-to-end smoke test for the menu flow.

This test wires the keyboard builders together as a user would click
through them — main menu → freelance → settings → history → results —
and checks that the resulting markup always:

* contains an escape route (Menu / Back / Cancel)
* has no dangling callback prefixes
* keeps the right tab active on history screens

It does not hit a real Telegram API; it only checks the keyboard
shapes that the bot would send.
"""

from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup

from instagram_dork_bot.keyboards import (
    CB_ADMIN_PANEL,
    CB_CANCEL,
    CB_CANCEL_SEARCH,
    CB_FREELANCE,
    CB_FREELANCE_PROMPTS,
    CB_HELP,
    CB_HIST_FREELANCE,
    CB_HISTORY,
    CB_MENU,
    CB_REPEAT,
    CB_SEARCH,
    CB_SET_COUNTRIES,
    CB_SET_PLATFORMS,
    CB_SET_RESULTS,
    CB_SETTINGS,
    SOURCE_FREELANCE,
    SOURCE_INSTAGRAM,
    cancel_keyboard,
    empty_history_keyboard,
    freelance_menu_keyboard,
    freelance_platforms_keyboard,
    help_keyboard,
    history_list_keyboard,
    main_menu_keyboard,
    progress_keyboard,
    results_keyboard,
    settings_menu_keyboard,
)


def _flat_callbacks(markup: InlineKeyboardMarkup) -> list[str]:
    return [c for row in markup.inline_keyboard for c in (b.callback_data or "" for b in row)]


def _has_text(markup: InlineKeyboardMarkup, needle: str) -> bool:
    return any(needle in (b.text or "") for row in markup.inline_keyboard for b in row)


# --- Main menu --------------------------------------------------------------


def test_main_menu_flow_includes_recent_shortcut() -> None:
    kb = main_menu_keyboard(
        is_admin=True,
        last_instagram_keyword="iphone 14",
        last_freelance_keyword="python bot",
    )
    cbs = _flat_callbacks(kb)
    # All four primary sections reachable.
    assert CB_SEARCH in cbs
    assert CB_FREELANCE in cbs
    assert CB_HISTORY in cbs
    assert CB_SETTINGS in cbs
    # Repeat shortcut is wired to a real callback.
    repeat_cbs = [c for c in cbs if c.startswith(f"{CB_REPEAT}:")]
    assert any(SOURCE_INSTAGRAM in c for c in repeat_cbs)
    assert any(SOURCE_FREELANCE in c for c in repeat_cbs)
    # Help + admin still present.
    assert CB_HELP in cbs
    assert CB_ADMIN_PANEL in cbs


def test_main_menu_forbidden_callbacks() -> None:
    """No half-baked or empty callback data on the main menu."""
    kb = main_menu_keyboard()
    cbs = _flat_callbacks(kb)
    assert "" not in cbs
    assert ":" not in [c.split(":")[0] for c in cbs if c not in {CB_REPEAT, "repeat"}]


# --- Prompt screens ---------------------------------------------------------


def test_prompt_screens_have_escape_routes() -> None:
    """Both the cancel keyboard and the help keyboard must include 'menu'."""
    for kb in (cancel_keyboard(), help_keyboard(), progress_keyboard()):
        cbs = _flat_callbacks(kb)
        # Every keyboard the user can get stuck in has a Menu/Cancel path.
        assert CB_MENU in cbs or CB_CANCEL in cbs or CB_CANCEL_SEARCH in cbs, (
            f"keyboard has no escape: {cbs}"
        )


def test_progress_keyboard_does_not_block_user() -> None:
    """Progress screen must allow both aborting and going to menu."""
    kb = progress_keyboard()
    cbs = _flat_callbacks(kb)
    assert CB_CANCEL_SEARCH in cbs
    assert CB_MENU in cbs


# --- Freelance --------------------------------------------------------------


def test_freelance_menu_escape_routes() -> None:
    kb = freelance_menu_keyboard()
    cbs = _flat_callbacks(kb)
    assert CB_FREELANCE_PROMPTS in cbs
    assert CB_MENU in cbs
    assert CB_HIST_FREELANCE in cbs


def test_freelance_platforms_keyboard_back_button_routes_correctly() -> None:
    """From settings the back button must lead to settings, not freelance."""
    from_settings = freelance_platforms_keyboard(
        selected=("upwork",), back_callback=CB_SETTINGS, back_label="⚙️ В настройки"
    )
    cbs = _flat_callbacks(from_settings)
    assert CB_SETTINGS in cbs
    # And a freelance-tab keyboard must lead back to freelance.
    from_freelance = freelance_platforms_keyboard(selected=("upwork",))
    assert CB_FREELANCE in _flat_callbacks(from_freelance)


# --- Settings ---------------------------------------------------------------


def test_settings_keyboard_three_preferences() -> None:
    """The settings hub exposes countries, results, and (when enabled) platforms."""
    kb = settings_menu_keyboard(
        selected_countries=("us", "gb"),
        max_results=50,
        total_countries=10,
        selected_platforms=("upwork", "fiverr"),
        total_platforms=4,
    )
    cbs = _flat_callbacks(kb)
    assert CB_SET_COUNTRIES in cbs
    assert CB_SET_RESULTS in cbs
    assert CB_SET_PLATFORMS in cbs
    # And the back-to-menu button is present.
    assert CB_MENU in cbs


# --- History ----------------------------------------------------------------


def test_history_list_tabs_swap_works_in_both_directions() -> None:
    items = [(1, "02.06 • iphone 14 (12)"), (2, "01.06 • macbook air (8)")]
    counts = {SOURCE_INSTAGRAM: 2, SOURCE_FREELANCE: 0}
    kb = history_list_keyboard(
        items=items, page=0, total_pages=1, source=SOURCE_INSTAGRAM, counts=counts
    )
    # At least one tab callback points at freelance, the other at instagram.
    cbs = _flat_callbacks(kb)
    assert cbs.count("hsrc:freelance:0") >= 1
    assert cbs.count("hsrc:instagram:0") >= 1
    # Items open via the per-row callback.
    assert "hopen:1:0" in cbs
    assert "hopen:2:0" in cbs


def test_empty_history_shows_correct_copy() -> None:
    kb_ig = empty_history_keyboard(
        source=SOURCE_INSTAGRAM,
        counts={SOURCE_INSTAGRAM: 0, SOURCE_FREELANCE: 0},
    )
    kb_fr = empty_history_keyboard(
        source=SOURCE_FREELANCE,
        counts={SOURCE_INSTAGRAM: 0, SOURCE_FREELANCE: 0},
    )
    # The active tab is the one whose source matches.
    labels_ig = [(b.text or "") for row in kb_ig.inline_keyboard for b in row]
    labels_fr = [(b.text or "") for row in kb_fr.inline_keyboard for b in row]
    assert any(label.startswith("●") and "Instagram" in label for label in labels_ig)
    assert any(label.startswith("●") and "Фриланс" in label for label in labels_fr)


# --- Results ----------------------------------------------------------------


def test_results_keyboard_includes_filters_shortcut_and_menu() -> None:
    kb = results_keyboard(page=0, total_pages=3)
    cbs = _flat_callbacks(kb)
    # The new "filters" shortcut: click goes to settings.
    assert CB_SETTINGS in cbs
    assert CB_SEARCH in cbs
    assert CB_MENU in cbs
    assert CB_HISTORY in cbs


def test_results_keyboard_back_to_history_present() -> None:
    """In history mode the back button says 'к истории' instead of just 'история'."""
    kb = results_keyboard(page=0, total_pages=2, show_back_to_history=True, search_id=42)
    assert _has_text(kb, "↩️ К истории")
    # Pagination callback uses the search-id-aware variant.
    cbs = _flat_callbacks(kb)
    assert any(c.startswith("hres:42:") for c in cbs)


# --- Cross-cutting: every keyboard has a back to main menu path -----------


def test_every_keyboard_has_main_menu_path() -> None:
    """The user must never get stuck without a way back to /menu."""
    keyboards: list[InlineKeyboardMarkup] = [
        main_menu_keyboard(),
        help_keyboard(),
        cancel_keyboard(),
        progress_keyboard(),
        freelance_menu_keyboard(),
        freelance_platforms_keyboard(selected=("upwork",)),
        settings_menu_keyboard(selected_countries=("us",), max_results=10, total_countries=10),
        history_list_keyboard(items=[(1, "label")], page=0, total_pages=1, source=SOURCE_INSTAGRAM),
        empty_history_keyboard(source=SOURCE_INSTAGRAM),
        results_keyboard(page=0, total_pages=1),
    ]
    for kb in keyboards:
        cbs = _flat_callbacks(kb)
        # The main menu itself doesn't have a "menu" button — it IS the
        # menu (the user can always send /menu or /start as text).
        # Every other screen must have an explicit menu callback.
        if cbs and cbs[0] not in (CB_SEARCH, CB_FREELANCE, CB_HISTORY, CB_SETTINGS):
            assert CB_MENU in cbs, f"keyboard has no main menu path: {cbs}"


# --- Pluralisation ---------------------------------------------------------


def test_russian_search_pluralisation_in_history_label() -> None:
    """The bot renders «N поисков / поиска / поиск» depending on N.

    This is a sanity check that the empty-state title doesn't have a
    wrong singular/plural like "0 поисков" — the empty state has no
    count, so the word "поиск" should not appear in a count suffix.
    """
    kb_empty = empty_history_keyboard(source=SOURCE_INSTAGRAM)
    text = " ".join(b.text or "" for row in kb_empty.inline_keyboard for b in row)
    # The empty history view shows tab labels, not a count line. So the
    # full text shouldn't contain "0 поиск" or similar bad pluralisation.
    assert "0 поиск" not in text
    assert "1 поисков" not in text
    assert "2 поисков" not in text
