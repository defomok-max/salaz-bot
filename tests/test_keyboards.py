"""Tests for the menu / inline-keyboard builders.

The goal is to keep the user-facing menu shape under test so a
regression (e.g. accidentally dropping the back button) is caught
without spinning up a real bot.
"""

from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup

from instagram_dork_bot.keyboards import (
    CB_HELP,
    CB_HIST_SOURCE_PREFIX,
    CB_MENU,
    CB_REPEAT,
    CB_SEARCH,
    CB_SET_PLATFORMS,
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


def _labels(markup: InlineKeyboardMarkup) -> list[list[str]]:
    return [[btn.text for btn in row] for row in markup.inline_keyboard]


def _callbacks(markup: InlineKeyboardMarkup) -> list[list[str]]:
    return [[btn.callback_data or "" for btn in row] for row in markup.inline_keyboard]


def test_main_menu_is_two_column_grid() -> None:
    kb = main_menu_keyboard()
    rows = _labels(kb)
    assert rows[0] == ["🔍 Instagram", "💼 Фриланс"]
    assert rows[1] == ["📜 История", "⚙️ Настройки"]


def test_main_menu_help_button_present() -> None:
    kb = main_menu_keyboard()
    cbs = [c for row in _callbacks(kb) for c in row]
    assert CB_HELP in cbs


def test_main_menu_admin_button_only_for_admin() -> None:
    kb_user = main_menu_keyboard(is_admin=False)
    kb_admin = main_menu_keyboard(is_admin=True)
    flat_user = [b.text for row in kb_user.inline_keyboard for b in row]
    flat_admin = [b.text for row in kb_admin.inline_keyboard for b in row]
    assert "🛡 Админ-панель" not in flat_user
    assert "🛡 Админ-панель" in flat_admin


def test_main_menu_repeat_row_appears_for_recent_searches() -> None:
    kb_empty = main_menu_keyboard()
    kb_with_ig = main_menu_keyboard(last_instagram_keyword="iphone 14")
    kb_with_both = main_menu_keyboard(
        last_instagram_keyword="iphone 14", last_freelance_keyword="python bot"
    )

    empty_texts = [b.text for row in kb_empty.inline_keyboard for b in row]
    assert not any(t.startswith("🔁") for t in empty_texts)

    ig_texts = [b.text for row in kb_with_ig.inline_keyboard for b in row]
    assert any("🔁 Instagram" in t and "iphone 14" in t for t in ig_texts)

    both_texts = [b.text for row in kb_with_both.inline_keyboard for b in row]
    assert any("🔁 Instagram" in t for t in both_texts)
    assert any("🔁 Фриланс" in t for t in both_texts)


def test_main_menu_repeat_truncates_long_keyword() -> None:
    long_kw = "x" * 200
    kb = main_menu_keyboard(last_instagram_keyword=long_kw)
    texts = [b.text for row in kb.inline_keyboard for b in row]
    repeat_btn = next(t for t in texts if t.startswith("🔁"))
    # The truncation suffix is "…" and the prefix is "🔁 Instagram: " (14 chars).
    assert len(repeat_btn) <= 14 + 22 + 1


def test_main_menu_repeat_callback_format() -> None:
    kb = main_menu_keyboard(last_freelance_keyword="python")
    cbs = [c for row in _callbacks(kb) for c in row]
    repeat = next(c for c in cbs if c.startswith(f"{CB_REPEAT}:"))
    assert repeat == f"{CB_REPEAT}:{SOURCE_FREELANCE}:0"


def test_cancel_keyboard_has_both_cancel_and_menu() -> None:
    kb = cancel_keyboard()
    texts = [b.text for row in kb.inline_keyboard for b in row]
    cbs = [c for row in _callbacks(kb) for c in row]
    assert "❌ Отмена" in texts
    assert "🏠 Меню" in texts
    assert CB_MENU in cbs


def test_progress_keyboard_explains_cancel_and_menu() -> None:
    kb = progress_keyboard()
    texts = [b.text for row in kb.inline_keyboard for b in row]
    assert "❌ Отменить поиск" in texts
    assert "🏠 В меню" in texts


def test_help_keyboard_links_back_into_sections() -> None:
    kb = help_keyboard()
    cbs = [c for row in _callbacks(kb) for c in row]
    assert CB_SEARCH in cbs
    assert CB_MENU in cbs


def test_history_list_keyboard_has_tab_toggle() -> None:
    kb = history_list_keyboard(
        items=[(1, "02.06 • iphone 14 (12)")],
        page=0,
        total_pages=1,
        source=SOURCE_INSTAGRAM,
        counts={SOURCE_INSTAGRAM: 1, SOURCE_FREELANCE: 0},
    )
    rows = _labels(kb)
    # First row is the tab toggle.
    assert "●" in rows[0][0]
    assert "Instagram" in rows[0][0]
    # And the inactive tab should still appear.
    assert "Фриланс" in rows[0][1]


def test_history_list_keyboard_inactive_tab_callable() -> None:
    kb = history_list_keyboard(
        items=[],
        page=0,
        total_pages=1,
        source=SOURCE_INSTAGRAM,
    )
    cbs = [c for row in _callbacks(kb) for c in row]
    # At least one callback targets the freelance tab.
    freelance_targets = [
        c for c in cbs if c.startswith(f"{CB_HIST_SOURCE_PREFIX}:{SOURCE_FREELANCE}")
    ]
    assert freelance_targets, f"expected a freelance tab callback, got: {cbs}"


def test_empty_history_keyboard_still_has_tabs() -> None:
    kb = empty_history_keyboard(
        source=SOURCE_INSTAGRAM,
        counts={SOURCE_INSTAGRAM: 0, SOURCE_FREELANCE: 0},
    )
    rows = _labels(kb)
    assert any("●" in t for t in rows[0])
    assert any("💼" in t for t in rows[0])


def test_settings_keyboard_includes_platforms_row() -> None:
    kb = settings_menu_keyboard(
        selected_countries=("us",),
        max_results=50,
        total_countries=10,
        selected_platforms=("upwork", "fiverr"),
        total_platforms=4,
    )
    cbs = [c for row in _callbacks(kb) for c in row]
    assert CB_SET_PLATFORMS in cbs


def test_settings_keyboard_omits_platforms_when_none() -> None:
    kb = settings_menu_keyboard(
        selected_countries=("us",),
        max_results=50,
        total_countries=10,
        selected_platforms=None,
    )
    cbs = [c for row in _callbacks(kb) for c in row]
    assert CB_SET_PLATFORMS not in cbs


def test_freelance_menu_uses_two_column_grid() -> None:
    kb = freelance_menu_keyboard()
    rows = _labels(kb)
    assert rows[0] == ["🔎 Запустить поиск", "🌐 Платформы"]
    assert rows[1] == ["📜 История фриланса", "🏠 Меню"]


def test_freelance_menu_repeat_row_optional() -> None:
    kb_empty = freelance_menu_keyboard()
    kb_repeat = freelance_menu_keyboard(last_keyword="python bot")
    flat_empty = [b.text for row in kb_empty.inline_keyboard for b in row]
    flat_repeat = [b.text for row in kb_repeat.inline_keyboard for b in row]
    assert not any("🔁" in t for t in flat_empty)
    assert any("🔁" in t and "python bot" in t for t in flat_repeat)


def test_freelance_platforms_keyboard_back_target_configurable() -> None:
    kb_from_freelance = freelance_platforms_keyboard(selected=("upwork",))
    kb_from_settings = freelance_platforms_keyboard(
        selected=("upwork",),
        back_callback=CB_SET_PLATFORMS,  # any sentinel is fine
    )
    rows_default = _callbacks(kb_from_freelance)
    rows_settings = _callbacks(kb_from_settings)
    # The last row contains [back, menu] — the first element is the
    # configurable back callback, the second is always the menu shortcut.
    assert rows_default[-1][0] == "freelance"
    assert rows_default[-1][1] == CB_MENU
    assert rows_settings[-1][0] == CB_SET_PLATFORMS
    assert rows_settings[-1][1] == CB_MENU


def test_results_keyboard_includes_filters_shortcut() -> None:
    kb = results_keyboard(page=0, total_pages=3)
    cbs = [c for row in _callbacks(kb) for c in row]
    # "settings" is the callback for the filters shortcut button.
    assert "settings" in cbs
