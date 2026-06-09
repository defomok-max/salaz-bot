"""Inline keyboard builders for the Telegram bot UI.

The bot uses a single "control message" that is edited in-place as the user
navigates. All actions are inline-button callbacks; there is no persistent
reply keyboard.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from aiogram.types import (
    CopyTextButton,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from .countries import GROUPS, Country, selectable_countries
from .extractors import Contacts
from .freelance.platforms import PLATFORMS, get_platform
from .storage import StoredUser, UserStatus

# --- Callback data ----------------------------------------------------------

CB_MENU = "menu"
CB_SEARCH = "search"
CB_FREELANCE = "freelance"
CB_CANCEL = "cancel"
CB_CANCEL_SEARCH = "cancel_search"
CB_HISTORY = "history"
CB_HIST_FREELANCE = "hfreelance"
CB_HELP = "help"
CB_REPEAT = "repeat"  # repeat:<source>:<search_id>
CB_SETTINGS = "settings"
CB_NOOP = "noop"

# Freelance tab.
CB_FREELANCE_PROMPTS = "fr_prompt"
CB_FREELANCE_PLATFORMS = "fr_plat"
CB_FREELANCE_PLATFORM_TOGGLE_PREFIX = "fr_t"  # fr_t:<code>
CB_FREELANCE_PLATFORM_ALL = "fr_all"
CB_FREELANCE_PLATFORM_NONE = "fr_none"
CB_FREELANCE_RUN = "fr_run"

# Access-gate callbacks.
CB_ACCESS_REQUEST = "ax_req"
CB_ACCESS_PENDING = "ax_pending"

# Admin panel.
CB_ADMIN_PANEL = "ap"
CB_ADMIN_LIST_PREFIX = "ap_list"  # ap_list:<status>:<page>
CB_ADMIN_USER_PREFIX = "ap_user"  # ap_user:<user_id>
CB_ADMIN_ACTION_PREFIX = "ap_act"  # ap_act:<user_id>:<action>
CB_ADMIN_LIMIT_PREFIX = "ap_lim"  # ap_lim:<user_id>:<limit>
CB_ADMIN_LIMIT_PICKER_PREFIX = "ap_limp"  # ap_limp:<user_id>
# Inline admin actions sent in the *notification* DM.
CB_ADMIN_NOTIF_PREFIX = "ap_n"  # ap_n:<user_id>:<action>

# Settings.
CB_SET_COUNTRIES = "s_cnt"
CB_SET_RESULTS = "s_res"
CB_SET_PLATFORMS = "s_plat"  # shortcut from settings → freelance platforms picker
CB_COUNTRY_TOGGLE_PREFIX = "c_t"  # c_t:<page>:<code>
CB_COUNTRY_PAGE_PREFIX = "c_p"  # c_p:<page>
CB_COUNTRY_GROUP_PREFIX = "c_g"  # c_g:<group_code>
CB_COUNTRY_ALL = "c_all"
CB_COUNTRY_NONE = "c_none"
CB_RESULTS_PICK_PREFIX = "r_p"  # r_p:<count>
CB_RESULTS_PICK_RESET = "r_pr"  # r_pr - reset to default

# Pagination of past results / history list.
CB_PAGE_PREFIX = "page"
CB_HIST_LIST_PREFIX = "hpage"  # hpage:<source>:<page>
CB_HIST_OPEN_PREFIX = "hopen"
CB_HIST_PAGE_PREFIX = "hres"
CB_HIST_SOURCE_PREFIX = "hsrc"  # hsrc:<source>:<page>

# History source codes. "instagram" | "freelance"
SOURCE_INSTAGRAM = "instagram"
SOURCE_FREELANCE = "freelance"


def cb_page(page: int) -> str:
    return f"{CB_PAGE_PREFIX}:{page}"


def cb_history_list_page(source: str, page: int) -> str:
    return f"{CB_HIST_LIST_PREFIX}:{source}:{page}"


def cb_history_source(source: str, page: int = 0) -> str:
    """Switch history tab (Instagram ↔ Freelance)."""
    return f"{CB_HIST_SOURCE_PREFIX}:{source}:{page}"


def cb_repeat(source: str, search_id: int | None = None) -> str:
    """Re-run a previous search by id. If search_id is None, the bot
    uses the most recent search of that source for the user."""
    sid = search_id if search_id is not None else 0
    return f"{CB_REPEAT}:{source}:{sid}"


def cb_history_open(search_id: int, page: int = 0) -> str:
    return f"{CB_HIST_OPEN_PREFIX}:{search_id}:{page}"


def cb_history_results_page(search_id: int, page: int) -> str:
    return f"{CB_HIST_PAGE_PREFIX}:{search_id}:{page}"


def cb_admin_list(status: str, page: int) -> str:
    return f"{CB_ADMIN_LIST_PREFIX}:{status}:{page}"


def cb_admin_user(user_id: int) -> str:
    return f"{CB_ADMIN_USER_PREFIX}:{user_id}"


def cb_admin_action(user_id: int, action: str) -> str:
    return f"{CB_ADMIN_ACTION_PREFIX}:{user_id}:{action}"


def cb_admin_notif(user_id: int, action: str) -> str:
    return f"{CB_ADMIN_NOTIF_PREFIX}:{user_id}:{action}"


def cb_admin_limit_picker(user_id: int) -> str:
    return f"{CB_ADMIN_LIMIT_PICKER_PREFIX}:{user_id}"


def cb_admin_limit(user_id: int, limit: int) -> str:
    return f"{CB_ADMIN_LIMIT_PREFIX}:{user_id}:{limit}"


def cb_country_toggle(page: int, code: str) -> str:
    return f"{CB_COUNTRY_TOGGLE_PREFIX}:{page}:{code}"


def cb_country_page(page: int) -> str:
    return f"{CB_COUNTRY_PAGE_PREFIX}:{page}"


def cb_country_group(group_code: str) -> str:
    return f"{CB_COUNTRY_GROUP_PREFIX}:{group_code}"


def cb_results_pick(count: int) -> str:
    return f"{CB_RESULTS_PICK_PREFIX}:{count}"


def cb_results_pick_reset() -> str:
    return CB_RESULTS_PICK_RESET


# --- Keyboard builders ------------------------------------------------------


def main_menu_keyboard(
    *,
    is_admin: bool = False,
    last_instagram_keyword: str | None = None,
    last_freelance_keyword: str | None = None,
) -> InlineKeyboardMarkup:
    """Main menu: 2-column grid of primary actions + recent-search row.

    When the user has at least one search in history, a quick-repeat
    row appears under the primary grid so the most common action —
    "run that same keyword again" — is one tap away.
    """
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(text="🔍 Instagram", callback_data=CB_SEARCH),
            InlineKeyboardButton(text="💼 Фриланс", callback_data=CB_FREELANCE),
        ],
        [
            InlineKeyboardButton(text="📜 История", callback_data=CB_HISTORY),
            InlineKeyboardButton(text="⚙️ Настройки", callback_data=CB_SETTINGS),
        ],
    ]

    repeat_row: list[InlineKeyboardButton] = []
    if last_instagram_keyword:
        kw = _truncate(last_instagram_keyword, 22)
        repeat_row.append(
            InlineKeyboardButton(
                text=f"🔁 Instagram: {kw}",
                callback_data=cb_repeat(SOURCE_INSTAGRAM),
            )
        )
    if last_freelance_keyword:
        kw = _truncate(last_freelance_keyword, 22)
        repeat_row.append(
            InlineKeyboardButton(
                text=f"🔁 Фриланс: {kw}",
                callback_data=cb_repeat(SOURCE_FREELANCE),
            )
        )
    if repeat_row:
        rows.append(repeat_row)

    rows.append([InlineKeyboardButton(text="ℹ️ Помощь", callback_data=CB_HELP)])

    if is_admin:
        rows.append([InlineKeyboardButton(text="🛡 Админ-панель", callback_data=CB_ADMIN_PANEL)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def help_keyboard() -> InlineKeyboardMarkup:
    """Keyboard shown on the /help screen — quick links back into the bot."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🔍 Instagram", callback_data=CB_SEARCH),
                InlineKeyboardButton(text="💼 Фриланс", callback_data=CB_FREELANCE),
            ],
            [
                InlineKeyboardButton(text="📜 История", callback_data=CB_HISTORY),
                InlineKeyboardButton(text="⚙️ Настройки", callback_data=CB_SETTINGS),
            ],
            [InlineKeyboardButton(text="🏠 Меню", callback_data=CB_MENU)],
        ]
    )


def access_request_keyboard(*, requested: bool = False) -> InlineKeyboardMarkup:
    """Keyboard for users who don't have access yet."""
    if requested:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⏳ Запрос отправлен",
                        callback_data=CB_ACCESS_PENDING,
                    )
                ]
            ]
        )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📨 Запросить доступ",
                    callback_data=CB_ACCESS_REQUEST,
                )
            ]
        ]
    )


def cancel_keyboard() -> InlineKeyboardMarkup:
    """Cancel + menu pair used while waiting for user keyword input.

    The menu button is wired to the same CB_MENU callback as the rest of
    the bot so users have a consistent escape route even if the cancel
    text is hidden in the chat scrollback.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="❌ Отмена", callback_data=CB_CANCEL),
                InlineKeyboardButton(text="🏠 Меню", callback_data=CB_MENU),
            ]
        ]
    )


def progress_keyboard() -> InlineKeyboardMarkup:
    """Buttons shown on the in-flight progress message.

    Tapping "🏠 Меню" aborts the in-flight search and returns to the
    main menu — it's the same behaviour as pressing ❌ Отмена, just
    labelled in a way that matches the rest of the bot.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="❌ Отменить поиск", callback_data=CB_CANCEL_SEARCH),
                InlineKeyboardButton(text="🏠 В меню", callback_data=CB_MENU),
            ]
        ]
    )


def _pagination_row(
    *,
    current: int,
    total: int,
    builder: Callable[[int], str],
) -> list[InlineKeyboardButton]:
    """Build a ``◀ N/M ▶`` row of pagination buttons."""
    if total <= 1:
        return []
    prev_page = (current - 1) % total
    next_page = (current + 1) % total
    return [
        InlineKeyboardButton(text="◀", callback_data=builder(prev_page)),
        InlineKeyboardButton(text=f"{current + 1}/{total}", callback_data=CB_NOOP),
        InlineKeyboardButton(text="▶", callback_data=builder(next_page)),
    ]


def results_keyboard(
    *,
    page: int,
    total_pages: int,
    show_back_to_history: bool = False,
    search_id: int | None = None,
) -> InlineKeyboardMarkup:
    """Keyboard for paginated search results.

    Layout: pagination row, primary actions row (new search / settings
    shortcut), and a back-or-menu row.  In history mode the pagination
    callback uses the search-id-aware variant so paging through stored
    results preserves context.
    """
    rows: list[list[InlineKeyboardButton]] = []

    if show_back_to_history and search_id is not None:
        nav = _pagination_row(
            current=page,
            total=total_pages,
            builder=lambda p, sid=search_id: cb_history_results_page(sid, p),  # type: ignore[misc]
        )
    else:
        nav = _pagination_row(current=page, total=total_pages, builder=cb_page)
    if nav:
        rows.append(nav)

    primary = [
        InlineKeyboardButton(text="🔁 Новый поиск", callback_data=CB_SEARCH),
        InlineKeyboardButton(text="⚙️ Фильтры", callback_data=CB_SETTINGS),
    ]
    rows.append(primary)

    if show_back_to_history:
        rows.append(
            [
                InlineKeyboardButton(text="↩️ К истории", callback_data=CB_HISTORY),
                InlineKeyboardButton(text="🏠 Меню", callback_data=CB_MENU),
            ]
        )
    else:
        rows.append(
            [
                InlineKeyboardButton(text="📜 История", callback_data=CB_HISTORY),
                InlineKeyboardButton(text="🏠 Меню", callback_data=CB_MENU),
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def history_list_keyboard(
    *,
    items: list[tuple[int, str]],
    page: int,
    total_pages: int,
    source: str = SOURCE_INSTAGRAM,
    counts: dict[str, int] | None = None,
) -> InlineKeyboardMarkup:
    """History list with an Instagram ↔ Freelance tab toggle at the top.

    ``counts`` is an optional ``{source: total}`` dict used to render
    the tab labels ("● Instagram (8)").  If omitted, no counts are
    shown.
    """
    counts = counts or {}
    ig_count = counts.get(SOURCE_INSTAGRAM)
    fr_count = counts.get(SOURCE_FREELANCE)
    ig_label = _tab_label("🔍 Instagram", source == SOURCE_INSTAGRAM, ig_count)
    fr_label = _tab_label("💼 Фриланс", source == SOURCE_FREELANCE, fr_count)

    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text=ig_label,
                callback_data=cb_history_source(SOURCE_INSTAGRAM, 0),
            ),
            InlineKeyboardButton(
                text=fr_label,
                callback_data=cb_history_source(SOURCE_FREELANCE, 0),
            ),
        ]
    ]

    rows.extend(
        [InlineKeyboardButton(text=label, callback_data=cb_history_open(sid))]
        for sid, label in items
    )

    nav = _pagination_row(
        current=page,
        total=total_pages,
        builder=lambda p, s=source: cb_history_list_page(s, p),  # type: ignore[misc]
    )
    if nav:
        rows.append(nav)

    rows.append(
        [
            InlineKeyboardButton(text="🔍 Новый поиск", callback_data=CB_SEARCH),
            InlineKeyboardButton(text="🏠 Меню", callback_data=CB_MENU),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def empty_history_keyboard(
    *,
    source: str = SOURCE_INSTAGRAM,
    counts: dict[str, int] | None = None,
) -> InlineKeyboardMarkup:
    """History screen with no items — still shows the tab toggle."""
    counts = counts or {}
    ig_count = counts.get(SOURCE_INSTAGRAM)
    fr_count = counts.get(SOURCE_FREELANCE)
    ig_label = _tab_label("🔍 Instagram", source == SOURCE_INSTAGRAM, ig_count)
    fr_label = _tab_label("💼 Фриланс", source == SOURCE_FREELANCE, fr_count)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=ig_label,
                    callback_data=cb_history_source(SOURCE_INSTAGRAM, 0),
                ),
                InlineKeyboardButton(
                    text=fr_label,
                    callback_data=cb_history_source(SOURCE_FREELANCE, 0),
                ),
            ],
            [InlineKeyboardButton(text="🔍 Новый поиск", callback_data=CB_SEARCH)],
            [InlineKeyboardButton(text="🏠 Меню", callback_data=CB_MENU)],
        ]
    )


def back_to_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🏠 Меню", callback_data=CB_MENU)],
        ]
    )


def copy_link_keyboard(url: str) -> InlineKeyboardMarkup:
    """Per-result inline keyboard with a single 'copy URL' button."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📋 Скопировать ссылку",
                    copy_text=CopyTextButton(text=url),
                ),
                InlineKeyboardButton(text="↗️ Открыть", url=url),
            ]
        ]
    )


# --- Settings keyboards -----------------------------------------------------


def settings_menu_keyboard(
    *,
    selected_countries: Sequence[str],
    max_results: int,
    total_countries: int,
    selected_platforms: Sequence[str] | None = None,
    total_platforms: int = 0,
) -> InlineKeyboardMarkup:
    """Settings hub: countries, results-per-search, and freelance platforms.

    All three are user-level preferences — the platform picker is wired
    through a dedicated shortcut button so users don't have to go
    through the freelance tab to tweak it.
    """
    cn = len(selected_countries)
    if cn == 0:
        countries_label = "🌐 Страны: не выбрано"
    elif cn == total_countries:
        countries_label = "🌐 Страны: все"
    else:
        countries_label = f"🌐 Страны: {cn} из {total_countries}"

    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text=countries_label, callback_data=CB_SET_COUNTRIES)],
        [
            InlineKeyboardButton(
                text=f"🔢 Лимит ссылок: {max_results}",
                callback_data=CB_SET_RESULTS,
            )
        ],
    ]
    if selected_platforms is not None and total_platforms:
        pn = len(selected_platforms)
        if pn == 0:
            platforms_label = "💼 Платформы: не выбрано"
        elif pn == total_platforms:
            platforms_label = "💼 Платформы: все"
        else:
            platforms_label = f"💼 Платформы: {pn} из {total_platforms}"
        rows.append([InlineKeyboardButton(text=platforms_label, callback_data=CB_SET_PLATFORMS)])
    rows.append([InlineKeyboardButton(text="🏠 Меню", callback_data=CB_MENU)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


COUNTRIES_PER_PAGE = 8


def country_picker_keyboard(
    *,
    selected: Sequence[str],
    page: int,
) -> InlineKeyboardMarkup:
    """Multi-select country picker, paginated. Row layout: 2 columns."""
    selected_set = set(selected)
    countries = selectable_countries()
    total_pages = max(1, (len(countries) + COUNTRIES_PER_PAGE - 1) // COUNTRIES_PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    start = page * COUNTRIES_PER_PAGE
    chunk: list[Country] = list(countries[start : start + COUNTRIES_PER_PAGE])

    rows: list[list[InlineKeyboardButton]] = []
    pair: list[InlineKeyboardButton] = []
    for country in chunk:
        check = "☑️" if country.code in selected_set else "▫️"
        pair.append(
            InlineKeyboardButton(
                text=f"{check} {country.flag} {country.name_ru}",
                callback_data=cb_country_toggle(page, country.code),
            )
        )
        if len(pair) == 2:
            rows.append(pair)
            pair = []
    if pair:
        rows.append(pair)

    nav = _pagination_row(
        current=page,
        total=total_pages,
        builder=cb_country_page,
    )
    if nav:
        rows.append(nav)

    # Group presets row(s).
    group_row: list[InlineKeyboardButton] = []
    for grp in GROUPS:
        group_row.append(
            InlineKeyboardButton(text=grp.name_ru, callback_data=cb_country_group(grp.code))
        )
        if len(group_row) == 2:
            rows.append(group_row)
            group_row = []
    if group_row:
        rows.append(group_row)

    rows.append(
        [
            InlineKeyboardButton(text="✅ Все", callback_data=CB_COUNTRY_ALL),
            InlineKeyboardButton(text="🚫 Снять все", callback_data=CB_COUNTRY_NONE),
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(text="⚙️ Назад", callback_data=CB_SETTINGS),
            InlineKeyboardButton(text="🏠 Меню", callback_data=CB_MENU),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def results_picker_keyboard(
    *,
    current: int | None,
    choices: Sequence[int],
    default: int,
) -> InlineKeyboardMarkup:
    """Picker for the per-search result cap.

    ``current`` may be ``None`` if the user has not picked a value yet
    (the default is in effect).
    """
    rows: list[list[InlineKeyboardButton]] = []
    pair: list[InlineKeyboardButton] = []
    for choice in choices:
        marker = "🟢" if current is not None and choice == current else "⚪️"
        pair.append(
            InlineKeyboardButton(
                text=f"{marker} {choice}",
                callback_data=cb_results_pick(choice),
            )
        )
        if len(pair) == 2:
            rows.append(pair)
            pair = []
    if pair:
        rows.append(pair)
    default_marker = "🟢" if current is None else "⚪️"
    rows.append(
        [
            InlineKeyboardButton(
                text=f"{default_marker} ↩️ Сбросить ({default})",
                callback_data=cb_results_pick_reset(),
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(text="⚙️ Назад", callback_data=CB_SETTINGS),
            InlineKeyboardButton(text="🏠 Меню", callback_data=CB_MENU),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


# --- Admin panel keyboards --------------------------------------------------


_ADMIN_STATUS_TABS: tuple[tuple[str, str], ...] = (
    ("pending", "⏳ Ожидают"),
    ("approved", "✅ Активные"),
    ("blocked", "⛔ Блок"),
    ("denied", "❌ Отклонены"),
)


def admin_panel_keyboard(
    *,
    users: list[StoredUser],
    status: str,
    page: int,
    total_pages: int,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []

    # Status tabs (as buttons that re-load the panel for that tab).
    tab_row: list[InlineKeyboardButton] = []
    for tab_status, tab_label in _ADMIN_STATUS_TABS:
        prefix = "● " if tab_status == status else ""
        tab_row.append(
            InlineKeyboardButton(
                text=f"{prefix}{tab_label}",
                callback_data=cb_admin_list(tab_status, 0),
            )
        )
        if len(tab_row) == 2:
            rows.append(tab_row)
            tab_row = []
    if tab_row:
        rows.append(tab_row)

    for user in users:
        rows.append(
            [
                InlineKeyboardButton(
                    text=_admin_user_label(user),
                    callback_data=cb_admin_user(user.id),
                )
            ]
        )

    nav = _pagination_row(
        current=page,
        total=total_pages,
        builder=lambda p, st=status: cb_admin_list(st, p),  # type: ignore[misc]
    )
    if nav:
        rows.append(nav)

    rows.append([InlineKeyboardButton(text="🏠 Меню", callback_data=CB_MENU)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _admin_user_label(user: StoredUser) -> str:
    parts: list[str] = []
    if user.username:
        parts.append(f"@{user.username}")
    elif user.first_name or user.last_name:
        parts.append(" ".join(p for p in (user.first_name, user.last_name) if p))
    else:
        parts.append(f"id {user.id}")
    if user.is_admin:
        parts.append("👑")
    return " ".join(parts)


def admin_user_actions_keyboard(user: StoredUser) -> InlineKeyboardMarkup:
    """Per-user action buttons inside the admin panel."""
    rows: list[list[InlineKeyboardButton]] = []
    if user.status != UserStatus.APPROVED:
        rows.append(
            [
                InlineKeyboardButton(
                    text="✅ Одобрить",
                    callback_data=cb_admin_action(user.id, "approve"),
                )
            ]
        )
    if user.status != UserStatus.BLOCKED:
        rows.append(
            [
                InlineKeyboardButton(
                    text="⛔ Заблокировать",
                    callback_data=cb_admin_action(user.id, "block"),
                )
            ]
        )
    if user.status not in (UserStatus.DENIED, UserStatus.BLOCKED):
        rows.append(
            [
                InlineKeyboardButton(
                    text="❌ Отклонить",
                    callback_data=cb_admin_action(user.id, "deny"),
                )
            ]
        )
    if user.status in (UserStatus.BLOCKED, UserStatus.DENIED):
        rows.append(
            [
                InlineKeyboardButton(
                    text="↩️ Разблокировать",
                    callback_data=cb_admin_action(user.id, "approve"),
                )
            ]
        )
    limit_label = f"🔗 Лимит ссылок: {user.link_limit}" if user.link_limit else "🔗 Лимит ссылок: ∞"
    rows.append(
        [
            InlineKeyboardButton(
                text=limit_label,
                callback_data=cb_admin_limit_picker(user.id),
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="◀ К списку",
                callback_data=cb_admin_list(user.status.value, 0),
            ),
            InlineKeyboardButton(text="🏠 Меню", callback_data=CB_MENU),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_limit_picker_keyboard(user_id: int, current_limit: int | None) -> InlineKeyboardMarkup:
    choices = [10, 25, 50, 100, 250, 500]
    rows: list[list[InlineKeyboardButton]] = []
    pair: list[InlineKeyboardButton] = []
    for choice in choices:
        marker = "🟢" if choice == current_limit else "⚪️"
        pair.append(
            InlineKeyboardButton(
                text=f"{marker} {choice}",
                callback_data=cb_admin_limit(user_id, choice),
            )
        )
        if len(pair) == 2:
            rows.append(pair)
            pair = []
    if pair:
        rows.append(pair)
    marker_inf = "🟢" if current_limit is None else "⚪️"
    rows.append(
        [
            InlineKeyboardButton(
                text=f"{marker_inf} ∞ Без лимита",
                callback_data=cb_admin_limit(user_id, 0),
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="◀ К пользователю",
                callback_data=cb_admin_user(user_id),
            ),
            InlineKeyboardButton(text="🏠 Меню", callback_data=CB_MENU),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_notification_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Inline keyboard for the DM that arrives to admins on access requests."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Одобрить",
                    callback_data=cb_admin_notif(user_id, "approve"),
                ),
                InlineKeyboardButton(
                    text="❌ Отклонить",
                    callback_data=cb_admin_notif(user_id, "deny"),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="⛔ Заблокировать",
                    callback_data=cb_admin_notif(user_id, "block"),
                )
            ],
        ]
    )


# --- Freelance keyboards ----------------------------------------------------


def freelance_menu_keyboard(
    *,
    last_keyword: str | None = None,
) -> InlineKeyboardMarkup:
    """Entry keyboard for the freelance tab.

    The primary actions are placed in a 2-column grid (search /
    platforms) and a repeat row appears when the user has at least one
    freelance search in history.
    """
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(text="🔎 Запустить поиск", callback_data=CB_FREELANCE_PROMPTS),
            InlineKeyboardButton(text="🌐 Платформы", callback_data=CB_FREELANCE_PLATFORMS),
        ],
        [
            InlineKeyboardButton(text="📜 История фриланса", callback_data=CB_HIST_FREELANCE),
            InlineKeyboardButton(text="🏠 Меню", callback_data=CB_MENU),
        ],
    ]
    if last_keyword:
        kw = _truncate(last_keyword, 26)
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"🔁 Повторить: {kw}",
                    callback_data=cb_repeat(SOURCE_FREELANCE),
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def freelance_platforms_keyboard(
    *,
    selected: Sequence[str],
    back_callback: str = CB_FREELANCE,
    back_label: str = "◀ Назад",
) -> InlineKeyboardMarkup:
    """Multi-select platform picker.

    The back button target is configurable so the same picker can be
    reached from the freelance tab (``back=CB_FREELANCE``) or from
    the settings menu (``back=CB_SETTINGS``).
    """
    selected_set = {s.lower() for s in selected}
    rows: list[list[InlineKeyboardButton]] = []
    pair: list[InlineKeyboardButton] = []
    for plat in PLATFORMS:
        check = "🟢" if plat.code in selected_set else "⚪️"
        pair.append(
            InlineKeyboardButton(
                text=f"{check} {plat.label}",
                callback_data=f"{CB_FREELANCE_PLATFORM_TOGGLE_PREFIX}:{plat.code}",
            )
        )
        if len(pair) == 2:
            rows.append(pair)
            pair = []
    if pair:
        rows.append(pair)
    rows.append(
        [
            InlineKeyboardButton(text="✅ Все", callback_data=CB_FREELANCE_PLATFORM_ALL),
            InlineKeyboardButton(text="🚫 Снять все", callback_data=CB_FREELANCE_PLATFORM_NONE),
        ]
    )
    rows.append(
        [InlineKeyboardButton(text="🔎 Запустить поиск", callback_data=CB_FREELANCE_PROMPTS)]
    )
    rows.append(
        [
            InlineKeyboardButton(text=back_label, callback_data=back_callback),
            InlineKeyboardButton(text="🏠 Меню", callback_data=CB_MENU),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def freelance_cancel_keyboard() -> InlineKeyboardMarkup:
    """Cancel button shown on the in-flight freelance progress message."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data=CB_CANCEL_SEARCH)],
        ]
    )


def _format_freelance_listing_entry(
    idx: int,
    listing: object,
) -> str:
    """Head + contacts + platform/budget line for a freelance listing."""
    title = getattr(listing, "title", "")
    link = getattr(listing, "link", "")
    contacts: Contacts = getattr(listing, "contacts", None) or Contacts()
    platform = getattr(listing, "platform", "") or ""
    listing_type = getattr(listing, "listing_type", "") or ""
    budget = getattr(listing, "budget", "") or ""

    import html as _html

    head = f"<b>{idx}.</b> <code>{_html.escape(str(link))}</code>"
    blocks: list[str] = []
    if contacts.emails:
        blocks.append(
            "   📧 <code>" + ", ".join(_html.escape(e) for e in contacts.emails) + "</code>"
        )
    if contacts.whatsapp_numbers:
        blocks.append(
            "   💬 <code>"
            + ", ".join(_html.escape(p) for p in contacts.whatsapp_numbers)
            + "</code>"
        )
    if contacts.phones:
        blocks.append(
            "   📞 <code>" + ", ".join(_html.escape(p) for p in contacts.phones) + "</code>"
        )
    if contacts.telegram_handles:
        blocks.append(
            "   ✈️ <code>@"
            + ", @".join(_html.escape(h) for h in contacts.telegram_handles)
            + "</code>"
        )
    meta_bits: list[str] = []
    plat = get_platform(platform) if platform else None
    if plat is not None:
        meta_bits.append(plat.label)
    if listing_type:
        meta_bits.append(listing_type)
    if budget:
        meta_bits.append(budget)
    if meta_bits:
        blocks.append("   🏷 " + _html.escape(" · ".join(meta_bits)))
    if not title:
        title = link
    body = head + "\n   <i>" + _html.escape(str(title)) + "</i>"
    if blocks:
        body += "\n" + "\n".join(blocks)
    return body


def render_freelance_results_page(
    *,
    listings: Sequence[object],
    page: int,
    per_page: int,
    keyword: str,
    per_platform: dict[str, int] | None = None,
) -> tuple[str, int, int]:
    """Render one page of freelance results; return (text, total_pages, total)."""
    import html as _html

    total = len(listings)
    total_pages = max(1, (total + per_page - 1) // per_page) if total else 1
    page = max(0, min(page, total_pages - 1))
    start = page * per_page
    chunk = listings[start : start + per_page]
    if not chunk:
        body = "🤷 Ничего подходящего не нашлось. Попробуй другой ключевик или менее редкий товар."
        if per_platform:
            bits = ", ".join(f"{k}: {v}" for k, v in per_platform.items())
            body += f"\n\nПлатформы опрошены: {_html.escape(bits)}."
        return body, total_pages, total
    header = (
        f"💼 <b>{total}</b> листингов по запросу "
        f"<b>{_html.escape(keyword)}</b>\n"
        f"Страница <b>{page + 1}</b>/<b>{total_pages}</b>\n"
    )
    lines = [
        _format_freelance_listing_entry(start + i + 1, listing) for i, listing in enumerate(chunk)
    ]
    return header + "\n\n" + "\n\n".join(lines), total_pages, total


# --- Small text helpers -----------------------------------------------------


def _truncate(text: str, limit: int) -> str:
    """Trim ``text`` to ``limit`` chars; add an ellipsis when shortened."""
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _tab_label(base: str, active: bool, count: int | None) -> str:
    """Build a tab button label: '● Instagram (8)' / '💼 Фриланс (4)' / '🔍 Instagram'."""
    prefix = "● " if active else ""
    if count is None:
        return f"{prefix}{base}"
    return f"{prefix}{base} ({count})"
