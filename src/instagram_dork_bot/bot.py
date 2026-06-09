"""Telegram bot entrypoint and conversation flow.

The bot keeps a single "control message" per chat and edits it in place as
the user navigates between menu, search, results, history, settings and
admin screens. There is no persistent reply keyboard — every action is an
inline-button callback.

Access is gated by an explicit approval workflow:

* unknown users see "Доступ закрыт" with a "Запросить доступ" button;
* admins receive a DM with one-tap approve / deny / block buttons;
* admins also have an in-bot panel that lists users by status.
"""

from __future__ import annotations

import asyncio
import contextlib
import html
import logging
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatAction, ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramUnauthorizedError
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    ReplyKeyboardRemove,
)
from aiogram.utils.chat_action import ChatActionSender

from .config import Settings
from .countries import (
    COUNTRIES,
    GROUPS,
    all_country_codes,
    default_country_codes,
    get_country,
    selectable_countries,
)
from .extractors import Contacts
from .freelance.pipeline import (
    FreelanceListing,
    FreelancePipeline,
    FreelancePipelineStats,
    FreelanceProgressEvent,
)
from .freelance.platforms import PLATFORMS, all_platform_codes
from .keyboards import (
    CB_ACCESS_PENDING,
    CB_ACCESS_REQUEST,
    CB_ADMIN_ACTION_PREFIX,
    CB_ADMIN_LIMIT_PICKER_PREFIX,
    CB_ADMIN_LIMIT_PREFIX,
    CB_ADMIN_LIST_PREFIX,
    CB_ADMIN_NOTIF_PREFIX,
    CB_ADMIN_PANEL,
    CB_ADMIN_USER_PREFIX,
    CB_CANCEL,
    CB_CANCEL_SEARCH,
    CB_COUNTRY_ALL,
    CB_COUNTRY_GROUP_PREFIX,
    CB_COUNTRY_NONE,
    CB_COUNTRY_PAGE_PREFIX,
    CB_COUNTRY_TOGGLE_PREFIX,
    CB_FREELANCE,
    CB_FREELANCE_PLATFORM_ALL,
    CB_FREELANCE_PLATFORM_NONE,
    CB_FREELANCE_PLATFORM_TOGGLE_PREFIX,
    CB_FREELANCE_PLATFORMS,
    CB_FREELANCE_PROMPTS,
    CB_HELP,
    CB_HIST_FREELANCE,
    CB_HIST_LIST_PREFIX,
    CB_HIST_OPEN_PREFIX,
    CB_HIST_PAGE_PREFIX,
    CB_HIST_SOURCE_PREFIX,
    CB_HISTORY,
    CB_MENU,
    CB_NOOP,
    CB_PAGE_PREFIX,
    CB_REPEAT,
    CB_RESULTS_PICK_PREFIX,
    CB_RESULTS_PICK_RESET,
    CB_SEARCH,
    CB_SET_COUNTRIES,
    CB_SET_PLATFORMS,
    CB_SET_RESULTS,
    CB_SETTINGS,
    SOURCE_FREELANCE,
    SOURCE_INSTAGRAM,
    access_request_keyboard,
    admin_limit_picker_keyboard,
    admin_notification_keyboard,
    admin_panel_keyboard,
    admin_user_actions_keyboard,
    back_to_menu_keyboard,
    cancel_keyboard,
    country_picker_keyboard,
    empty_history_keyboard,
    freelance_cancel_keyboard,
    freelance_menu_keyboard,
    freelance_platforms_keyboard,
    help_keyboard,
    history_list_keyboard,
    main_menu_keyboard,
    progress_keyboard,
    render_freelance_results_page,
    results_keyboard,
    results_picker_keyboard,
    settings_menu_keyboard,
)
from .oembed import InstagramOEmbedClient
from .pipeline import Listing, PipelineStats, ProgressEvent, SearchPipeline
from .search import SerperClient
from .storage import (
    HistoryStore,
    StoredFreelanceListing,
    StoredListing,
    StoredSearch,
    StoredUser,
    UserStatus,
)

logger = logging.getLogger(__name__)

TELEGRAM_MAX = 3800
PROGRESS_EDIT_INTERVAL_SEC = 1.2
PROGRESS_BAR_WIDTH = 14
ADMIN_USERS_PER_PAGE = 8


WELCOME_TEXT = (
    "<b>👋 Salaz-bot</b>\n\n"
    "Ищу посты в <b>Instagram</b> с контактами "
    "(email / WhatsApp / Telegram) и заказы на <b>фриланс-платформах</b> "
    "с прямыми контактами клиента.\n\n"
    "Можно просто прислать ключевик — например <code>iphone 14</code> — "
    "и я запущу поиск."
)

HELP_TEXT = (
    "<b>ℹ️ Помощь</b>\n\n"
    "<b>Что я умею:</b>\n"
    "• <b>🔍 Instagram</b> — посты в открытом доступе, где указан "
    "<i>email</i> или <i>WhatsApp / телефон</i>.\n"
    "• <b>💼 Фриланс</b> — заказы на Upwork, Fiverr, Freelancer.com, "
    "Kwork с прямым контактом клиента (email / Telegram / WhatsApp).\n\n"
    "<b>Команды:</b>\n"
    "<code>/start</code>, <code>/menu</code> — главное меню\n"
    "<code>/help</code> — эта справка\n"
    "<code>/cancel</code> — отменить ввод ключевика\n\n"
    "<b>Советы:</b>\n"
    "• Чем конкретнее ключевик, тем точнее выдача.\n"
    "• В <b>⚙️ Настройках</b> выбери страны и лимит — это сужает выборку.\n"
    "• Если результатов мало — добавь платформы в настройках фриланса."
)


def _format_active_filters_text(
    user: StoredUser,
    *,
    settings: Settings,
) -> str:
    """Render the 'active filters' hint shown in prompt screens."""
    countries = _user_country_codes(user)
    max_results = _user_max_results(user, settings)
    total = len(selectable_countries())
    if not countries:
        countries_line = "🌍 Страны: <b>не выбрано</b> (будут все)"
    elif len(countries) >= total:
        countries_line = "🌍 Страны: <b>все</b>"
    else:
        countries_line = f"🌍 Страны: <b>{len(countries)}</b> из {total}"
    return f"{countries_line}\n🔢 Лимит: <b>{max_results}</b> ссылок"


def _format_prompt_keyword_text(
    user: StoredUser,
    *,
    settings: Settings,
) -> str:
    """Build the Instagram keyword prompt with current filter summary."""
    return (
        "✍️ <b>Поиск Instagram</b>\n"
        "━━━━━━━━━━━━━━━\n"
        f"{_format_active_filters_text(user, settings=settings)}\n"
        "━━━━━━━━━━━━━━━\n"
        "Пришли ключевик, например:\n"
        "<code>iphone 14</code> · <code>macbook air</code> · <code>used sofa</code>\n\n"
        "<i>Чем конкретнее запрос — тем точнее выдача.</i>"
    )


PROMPT_KEYWORD_TEXT = (
    "✍️ <b>Поиск Instagram</b>\n\n"
    "Пришли ключевик, например:\n"
    "<code>iphone 14</code> · <code>macbook air</code> · <code>used sofa</code>"
)


def _format_freelance_prompt_text(
    user: StoredUser,
    *,
    settings: Settings,
) -> str:
    """Build the freelance keyword prompt with current platform + filter summary."""
    platforms = _user_platforms(user)
    total_platforms = len(PLATFORMS)
    if not platforms:
        plat_line = "💼 Платформы: <b>не выбрано</b>"
    elif len(platforms) == total_platforms:
        plat_line = "💼 Платформы: <b>все</b>"
    else:
        plat_line = f"💼 Платформы: <b>{len(platforms)}</b> из {total_platforms}"
    max_results = _user_max_results(user, settings)
    return (
        "💼 <b>Фриланс-поиск</b>\n"
        "━━━━━━━━━━━━━━━\n"
        f"{plat_line}\n"
        f"🔢 Лимит: <b>{max_results}</b> ссылок\n"
        "━━━━━━━━━━━━━━━\n"
        "Пришли ключевик, например:\n"
        "<code>python telegram bot</code> · <code>figma landing</code>\n\n"
        "<i>Ищу листинги, где клиент оставил "
        "email / Telegram / WhatsApp.</i>"
    )


FREELANCE_PROMPT_KEYWORD_TEXT = (
    "💼 <b>Фриланс-поиск</b>\n\n"
    "Пришли ключевик, например:\n"
    "<code>python telegram bot</code> · <code>figma landing</code>"
)

ACCESS_DENIED_TEXT = (
    "🚫 <b>Доступ закрыт.</b>\n\n"
    "Этот бот работает только по приглашению. Жми кнопку ниже, чтобы "
    "отправить запрос администратору."
)

ACCESS_PENDING_TEXT = (
    "⏳ <b>Запрос отправлен.</b>\n\n"
    "Подожди, пока администратор его рассмотрит. Когда тебя одобрят — "
    "пришлю уведомление сюда."
)

ACCESS_BLOCKED_TEXT = (
    "⛔ <b>Доступ заблокирован.</b>\n\nЕсли считаешь это ошибкой — напиши администратору."
)

ACCESS_DENIED_DECISION_TEXT = (
    "❌ <b>Заявка отклонена.</b>\n\nЕсли что-то изменилось — попробуй позже."
)


class SearchForm(StatesGroup):
    """FSM states for the conversational search flow."""

    waiting_for_keyword = State()
    waiting_for_freelance_keyword = State()


@dataclass(frozen=True)
class _ResolvedQuery:
    keyword: str


# Active search tasks keyed by (chat_id, user_id).
_ACTIVE_SEARCHES: dict[tuple[int, int], asyncio.Task[object]] = {}


# --- Rendering helpers ------------------------------------------------------


def _render_progress_bar(done: int, total: int, width: int = PROGRESS_BAR_WIDTH) -> str:
    if total <= 0:
        return f"[{'░' * width}] —"
    ratio = max(0.0, min(1.0, done / total))
    filled = round(ratio * width)
    bar = "▓" * filled + "░" * (width - filled)
    pct = round(ratio * 100)
    return f"[{bar}] {pct}%"


def _render_progress_text(keyword: str, event: ProgressEvent) -> str:
    bar = _render_progress_bar(event.queries_done, event.queries_total)
    stage_human = {
        "init": "Готовлю запросы…",
        "dork_start": f"Ищу: <i>{html.escape(event.label or '...')}</i>",
        "dork_done": f"Готов запрос: <i>{html.escape(event.label or '...')}</i>",
        "dork_failed": f"Запрос упал: <i>{html.escape(event.label or '...')}</i>",
        "completed": "Поиск завершён.",
    }.get(event.stage, "Работаю…")

    counter = (
        f"Запросы: <b>{event.queries_done}</b>/{event.queries_total or '?'}"
        if event.queries_total
        else f"Запросы: <b>{event.queries_done}</b>"
    )
    accepted = f"Найдено: <b>{event.accepted}</b>/{event.max_results}"

    return (
        f"🔎 Поиск: <b>{html.escape(keyword)}</b>\n\n"
        f"<code>{bar}</code>\n"
        f"{counter} • {accepted}\n"
        f"{stage_human}"
    )


def _format_stats(stats: PipelineStats, accepted: int) -> str:
    lines = [
        f"Запросов выполнено: {stats.queries_run}",
        f"Сырых результатов: {stats.raw_results}",
        f"Принято после фильтров: {accepted}",
    ]
    if stats.rejected_reasons:
        lines.append("Отброшено по причинам:")
        for reason, count in sorted(stats.rejected_reasons.items(), key=lambda kv: -kv[1]):
            lines.append(f"  • {reason}: {count}")
    return "\n".join(lines)


def _parse_search_command(text: str | None) -> _ResolvedQuery | None:
    if not text:
        return None
    stripped = text.strip()
    if not stripped:
        return None
    if stripped.startswith("/"):
        parts = stripped.split(maxsplit=1)
        if len(parts) < 2:
            return None
        keyword = parts[1].strip()
    else:
        keyword = stripped
    if not keyword:
        return None
    return _ResolvedQuery(keyword=keyword)


def _format_contacts_block(contacts: Contacts) -> str:
    rows: list[str] = []
    if contacts.emails:
        emails = ", ".join(html.escape(e) for e in contacts.emails)
        rows.append(f"   📧 <code>{emails}</code>")
    if contacts.whatsapp_numbers:
        wa = ", ".join(html.escape(p) for p in contacts.whatsapp_numbers)
        rows.append(f"   💬 <code>{wa}</code>")
    if contacts.phones:
        phones = ", ".join(html.escape(p) for p in contacts.phones)
        rows.append(f"   📞 <code>{phones}</code>")
    return "\n".join(rows)


def _format_listing_entry(idx: int, listing: Listing | StoredListing) -> str:
    head = f"<b>{idx}.</b> <code>{html.escape(listing.link)}</code>"
    block = _format_contacts_block(listing.contacts)
    return f"{head}\n{block}" if block else head


def _paginate_listings(
    listings: list[Listing] | list[StoredListing],
    *,
    per_page: int,
) -> list[list[Listing] | list[StoredListing]]:
    if not listings:
        return [[]]
    return [listings[i : i + per_page] for i in range(0, len(listings), per_page)]


def _render_results_page(
    *,
    keyword: str,
    page: int,
    total_pages: int,
    page_listings: list[Listing] | list[StoredListing],
    total_listings: int,
    stats: PipelineStats | None,
    created_at: datetime | None,
    per_page: int,
) -> str:
    if total_listings == 0:
        body = "🤷 Ничего подходящего не нашлось. Попробуй другой ключевик или менее редкий товар."
        if stats is not None:
            body += f"\n\n<pre>{html.escape(_format_stats(stats, 0))}</pre>"
        return body

    when = ""
    if created_at is not None:
        when = f" • <i>{html.escape(created_at.astimezone().strftime('%d.%m.%Y %H:%M'))}</i>"

    header = (
        f"✅ <b>{total_listings}</b> постов по запросу "
        f"<b>{html.escape(keyword)}</b>{when}\n"
        f"Страница <b>{page + 1}</b>/<b>{total_pages}</b>\n"
    )

    start_idx = page * per_page

    lines = [
        _format_listing_entry(start_idx + i + 1, listing) for i, listing in enumerate(page_listings)
    ]
    body = "\n\n".join(lines)
    text = f"{header}\n{body}"

    if stats is not None and page == total_pages - 1:
        text += f"\n\n<pre>{html.escape(_format_stats(stats, total_listings))}</pre>"
    if len(text) > TELEGRAM_MAX:
        text = text[: TELEGRAM_MAX - 1] + "…"
    return text


def _format_history_label(item: StoredSearch) -> str:
    when = item.created_at.astimezone().strftime("%d.%m %H:%M")
    keyword = item.keyword
    if len(keyword) > 28:
        keyword = keyword[:25] + "…"
    return f"{when} • {keyword} ({item.accepted})"


# --- Telegram message helpers ----------------------------------------------


async def _safe_edit(
    bot: Bot,
    chat_id: int,
    message_id: int,
    text: str,
    *,
    reply_markup: object | None = None,
    disable_web_page_preview: bool = True,
) -> None:
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            reply_markup=reply_markup,
            disable_web_page_preview=disable_web_page_preview,
        )
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc):
            logger.debug("ignored edit error: %s", exc)
    except Exception as exc:
        logger.debug("ignored edit error: %s", exc)


async def _ensure_control_message(
    bot: Bot,
    chat_id: int,
    state: FSMContext,
    *,
    text: str,
    reply_markup: object | None,
) -> int:
    """Return the id of the chat's control message, creating one if missing."""
    data = await state.get_data()
    msg_id = data.get("control_message_id")
    if isinstance(msg_id, int):
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=msg_id,
                text=text,
                reply_markup=reply_markup,
                disable_web_page_preview=True,
            )
            return msg_id
        except TelegramBadRequest as exc:
            if "message is not modified" in str(exc):
                return msg_id
            logger.debug("control message edit failed, recreating: %s", exc)
    sent = await bot.send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=reply_markup,
        disable_web_page_preview=True,
    )
    await state.update_data(control_message_id=sent.message_id)
    return sent.message_id


async def _delete_message_silently(bot: Bot, chat_id: int, message_id: int) -> None:
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception as exc:
        logger.debug("could not delete message: %s", exc)


async def _force_clear_reply_keyboard(bot: Bot, chat_id: int) -> None:
    """Send a tiny throw-away message with ``ReplyKeyboardRemove`` and delete it.

    This guarantees the legacy reply keyboard from earlier bot versions is
    wiped on the client side. The throw-away message is removed immediately
    so the chat stays clean.
    """
    try:
        sent = await bot.send_message(
            chat_id=chat_id,
            text="…",
            reply_markup=ReplyKeyboardRemove(),
        )
    except Exception as exc:
        logger.debug("ReplyKeyboardRemove send failed: %s", exc)
        return
    with contextlib.suppress(Exception):
        await bot.delete_message(chat_id=chat_id, message_id=sent.message_id)


# --- Settings helpers -------------------------------------------------------


def _user_country_codes(user: StoredUser) -> tuple[str, ...]:
    """Resolve the user's effective country whitelist."""
    if user.pref_countries is not None:
        return user.pref_countries
    return default_country_codes()


def _user_max_results(user: StoredUser, settings: Settings) -> int:
    if user.pref_max_results is not None:
        return user.pref_max_results
    return settings.max_results


def _user_platforms(user: StoredUser) -> tuple[str, ...]:
    """Resolve the user's effective freelance-platform whitelist."""
    if user.pref_platforms is not None:
        return user.pref_platforms
    return all_platform_codes()


# --- Access gate helpers ----------------------------------------------------


def _is_bootstrap_admin(settings: Settings, *, user_id: int, username: str | None) -> bool:
    """Return True if this user should be auto-promoted to admin on first start."""
    if user_id in settings.admin_user_ids:
        return True
    return bool(username and username.lower() in settings.admin_usernames)


def _is_preapproved(settings: Settings, user_id: int) -> bool:
    return user_id in settings.allowed_user_ids


async def _bootstrap_user(
    *,
    bot: Bot,
    history: HistoryStore,
    settings: Settings,
    tg_user: object,
) -> StoredUser:
    """Upsert a Telegram user and apply bootstrap promotions.

    On first contact the user lands as ``pending``. If they match any of the
    bootstrap admin lists in env they are promoted on the spot.
    """
    user_id = getattr(tg_user, "id", 0)
    username = getattr(tg_user, "username", None)
    first_name = getattr(tg_user, "first_name", None)
    last_name = getattr(tg_user, "last_name", None)

    record = await history.upsert_user_request(
        user_id=user_id,
        username=username,
        first_name=first_name,
        last_name=last_name,
    )

    promote = False
    if _is_bootstrap_admin(settings, user_id=user_id, username=username):
        promote = True
    elif not record.is_admin and not await history.has_any_admin():
        # No admin in the system yet AND ADMIN_USER_IDS / ADMIN_USERNAMES are
        # not configured: refuse to auto-promote. On plans without persistent
        # disk (e.g. Render Free) the SQLite history is wiped on every redeploy
        # which would otherwise promote the very first chatter to admin on
        # every restart — turning the bot into an open relay.
        logger.warning(
            "no admin in DB and ADMIN_USER_IDS / ADMIN_USERNAMES empty — "
            "refusing to auto-promote user %s (@%s). Set ADMIN_USER_IDS in env.",
            user_id,
            username,
        )
        promote = False

    if promote and not record.is_admin:
        await history.set_user_admin(user_id=user_id, is_admin=True)
        await history.set_user_status(
            user_id=user_id, status=UserStatus.APPROVED, decided_by=user_id
        )
        record = await history.get_user(user_id) or record
        logger.info("bootstrap-promoted user %s (@%s) to admin", user_id, username)
    elif record.status == UserStatus.PENDING and not promote and _is_preapproved(settings, user_id):
        await history.set_user_status(
            user_id=user_id, status=UserStatus.APPROVED, decided_by=user_id
        )
        record = await history.get_user(user_id) or record

    return record


async def _show_access_gate(bot: Bot, chat_id: int, state: FSMContext, *, user: StoredUser) -> None:
    if user.status == UserStatus.PENDING:
        text = ACCESS_DENIED_TEXT
        markup = access_request_keyboard(requested=False)
    elif user.status == UserStatus.BLOCKED:
        text = ACCESS_BLOCKED_TEXT
        markup = back_to_menu_keyboard()
    elif user.status == UserStatus.DENIED:
        text = ACCESS_DENIED_DECISION_TEXT
        markup = access_request_keyboard(requested=False)
    else:
        return
    await _ensure_control_message(bot, chat_id, state, text=text, reply_markup=markup)


async def _notify_admins_of_request(bot: Bot, history: HistoryStore, user: StoredUser) -> None:
    """Send a DM to every admin announcing a new pending request."""
    admins = await history.list_admins()
    if not admins:
        logger.warning("no admins found, cannot notify about user %s", user.id)
        return
    label = user.username and f"@{user.username}"
    name = " ".join(p for p in (user.first_name, user.last_name) if p)
    headline = label or name or f"id {user.id}"
    text = (
        "📨 <b>Новый запрос на доступ</b>\n\n"
        f"Пользователь: <b>{html.escape(headline)}</b>\n"
        f"ID: <code>{user.id}</code>"
    )
    if name and label:
        text += f"\nИмя: {html.escape(name)}"
    for admin in admins:
        if admin.id == user.id:
            continue
        try:
            await bot.send_message(
                chat_id=admin.id,
                text=text,
                reply_markup=admin_notification_keyboard(user.id),
            )
        except Exception as exc:
            logger.warning(
                "failed to notify admin %s (%s) about user %s: %s",
                admin.id,
                admin.username or "no-username",
                user.id,
                exc,
            )


# --- Action helpers (admin) -------------------------------------------------


_VALID_ADMIN_ACTIONS = ("approve", "deny", "block")


def _status_for_action(action: str) -> UserStatus:
    return {
        "approve": UserStatus.APPROVED,
        "deny": UserStatus.DENIED,
        "block": UserStatus.BLOCKED,
    }[action]


async def _apply_admin_decision(
    *,
    bot: Bot,
    history: HistoryStore,
    target_user_id: int,
    action: str,
    decided_by: int,
) -> StoredUser | None:
    if action not in _VALID_ADMIN_ACTIONS:
        return None
    if target_user_id == decided_by and action in ("deny", "block"):
        return None
    target = await history.get_user(target_user_id)
    if target is None:
        return None
    new_status = _status_for_action(action)
    await history.set_user_status(user_id=target_user_id, status=new_status, decided_by=decided_by)
    target = await history.get_user(target_user_id) or target

    # Notify the affected user.
    notice = {
        "approve": "✅ Тебя одобрили. Возвращайся в бота — отправь /start.",
        "deny": "❌ Заявка на доступ к боту отклонена.",
        "block": "⛔ Доступ к боту заблокирован.",
    }[action]
    with contextlib.suppress(Exception):
        await bot.send_message(chat_id=target_user_id, text=notice)

    return target


# --- Search flow ------------------------------------------------------------


async def _show_menu(
    bot: Bot,
    chat_id: int,
    state: FSMContext,
    *,
    user: StoredUser,
    history: HistoryStore,
) -> None:
    """Render the main menu with a 'repeat last search' shortcut row."""
    await state.set_state(None)
    last_ig = await history.latest_keyword(user_id=user.id, source=SOURCE_INSTAGRAM)
    last_fr = await history.latest_keyword(user_id=user.id, source=SOURCE_FREELANCE)
    await _ensure_control_message(
        bot,
        chat_id,
        state,
        text=WELCOME_TEXT,
        reply_markup=main_menu_keyboard(
            is_admin=user.is_admin,
            last_instagram_keyword=last_ig,
            last_freelance_keyword=last_fr,
        ),
    )


async def _prompt_for_keyword(
    bot: Bot,
    chat_id: int,
    state: FSMContext,
    *,
    user: StoredUser,
    settings: Settings,
) -> None:
    """Render the Instagram keyword prompt with current filter summary."""
    await state.set_state(SearchForm.waiting_for_keyword)
    await _ensure_control_message(
        bot,
        chat_id,
        state,
        text=_format_prompt_keyword_text(user, settings=settings),
        reply_markup=cancel_keyboard(),
    )


async def _run_search(
    *,
    bot: Bot,
    chat_id: int,
    user: StoredUser,
    keyword: str,
    settings: Settings,
    history: HistoryStore,
    state: FSMContext,
) -> None:
    """Execute a search and stream progress + results into the control message."""
    await state.set_state(None)
    user_id = user.id

    if user.link_limit is not None:
        used = await history.count_user_total_listings(user_id=user_id)
        if used >= user.link_limit:
            await _ensure_control_message(
                bot,
                chat_id,
                state,
                text=(
                    f"🚫 Лимит ссылок исчерпан ({used}/{user.link_limit}).\n"
                    "Обратись к админу для увеличения лимита."
                ),
                reply_markup=back_to_menu_keyboard(),
            )
            return

    max_results = _user_max_results(user, settings)
    countries = _user_country_codes(user)

    msg_id = await _ensure_control_message(
        bot,
        chat_id,
        state,
        text=_render_progress_text(
            keyword,
            ProgressEvent(stage="init", max_results=max_results),
        ),
        reply_markup=progress_keyboard(),
    )
    await state.update_data(active_keyword=keyword)

    # Cancel any in-flight search for this chat+user so the new one wins.
    _cancel_active_search(chat_id, user_id)

    last_edit = 0.0
    last_text = ""

    async def _progress(event: ProgressEvent) -> None:
        nonlocal last_edit, last_text
        text = _render_progress_text(keyword, event)
        now = time.monotonic()
        is_terminal = event.stage in {"completed", "dork_failed"}
        if not is_terminal and (now - last_edit) < PROGRESS_EDIT_INTERVAL_SEC:
            return
        if text == last_text:
            return
        last_edit = now
        last_text = text
        await _safe_edit(bot, chat_id, msg_id, text, reply_markup=progress_keyboard())

    listings: list[Listing] = []
    stats: PipelineStats | None = None
    error: str | None = None
    cancelled = False

    async def _oembed_factory() -> InstagramOEmbedClient:
        return InstagramOEmbedClient(timeout=settings.oembed_timeout_seconds)

    async def _do_search() -> tuple[list[Listing], PipelineStats]:
        async with (
            ChatActionSender(bot=bot, chat_id=chat_id, action=ChatAction.TYPING),
            SerperClient(
                api_keys=settings.serper_api_keys,
                timeout=settings.search_timeout_seconds,
            ) as client,
        ):
            pipeline = SearchPipeline(
                client,
                max_results=max_results,
                results_per_query=settings.results_per_query,
                max_search_calls=settings.max_search_calls,
                allowed_countries=countries,
                oembed_factory=_oembed_factory,
                oembed_concurrency=settings.verification_concurrency,
            )
            return await pipeline.run(keyword, progress=_progress)

    task = asyncio.create_task(_do_search())
    await state.update_data(search_task_id=id(task))
    _ACTIVE_SEARCHES[(chat_id, user_id)] = task
    try:
        try:
            listings, stats = await task
        except asyncio.CancelledError:
            cancelled = True
        except Exception as exc:
            logger.exception("pipeline failed")
            error = str(exc)
    finally:
        # Only delete if WE are still the active task; another search
        # may have already replaced us.
        if _ACTIVE_SEARCHES.get((chat_id, user_id)) is task:
            _ACTIVE_SEARCHES.pop((chat_id, user_id), None)
        await state.update_data(search_task_id=None)

    if cancelled:
        await _safe_edit(
            bot,
            chat_id,
            msg_id,
            "🛑 Поиск отменён.",
            reply_markup=back_to_menu_keyboard(),
        )
        return

    if error is not None:
        await _safe_edit(
            bot,
            chat_id,
            msg_id,
            f"❌ Ошибка во время поиска: {html.escape(error)}",
            reply_markup=back_to_menu_keyboard(),
        )
        return

    assert stats is not None
    search_id = await history.save_search(
        user_id=user_id,
        keyword=keyword,
        listings=listings,
        stats=stats,
        source="instagram",
    )
    await state.update_data(active_search_id=search_id)
    await _show_results_page(
        bot=bot,
        chat_id=chat_id,
        state=state,
        keyword=keyword,
        listings=listings,
        stats=stats,
        page=0,
        per_page=settings.results_per_page,
        from_history=False,
        created_at=datetime.now(UTC),
    )


def _cancel_active_search(chat_id: int, user_id: int) -> None:
    """Cancel the currently running search for this chat+user, if any."""
    existing = _ACTIVE_SEARCHES.get((chat_id, user_id))
    if existing is not None and not existing.done():
        existing.cancel()


async def _run_freelance_search(
    *,
    bot: Bot,
    chat_id: int,
    user: StoredUser,
    keyword: str,
    settings: Settings,
    history: HistoryStore,
    state: FSMContext,
) -> None:
    """Run the freelance-platform pipeline and persist the results."""
    await state.set_state(None)
    user_id = user.id
    if user.link_limit is not None:
        used = await history.count_user_total_listings(user_id=user_id)
        if used >= user.link_limit:
            await _ensure_control_message(
                bot,
                chat_id,
                state,
                text=(
                    f"🚫 Лимит ссылок исчерпан ({used}/{user.link_limit}).\n"
                    "Обратись к админу для увеличения лимита."
                ),
                reply_markup=back_to_menu_keyboard(),
            )
            return

    max_results = _user_max_results(user, settings)
    platforms = _user_platforms(user)

    msg_id = await _ensure_control_message(
        bot,
        chat_id,
        state,
        text=_render_freelance_progress_text(
            keyword,
            FreelanceProgressEvent(stage="init", max_results=max_results),
        ),
        reply_markup=freelance_cancel_keyboard(),
    )
    _cancel_active_search(chat_id, user_id)

    last_edit = 0.0
    last_text = ""

    async def _progress(event: FreelanceProgressEvent) -> None:
        nonlocal last_edit, last_text
        text = _render_freelance_progress_text(keyword, event)
        now = time.monotonic()
        is_terminal = event.stage in {"completed", "dork_failed"}
        if not is_terminal and (now - last_edit) < PROGRESS_EDIT_INTERVAL_SEC:
            return
        if text == last_text:
            return
        last_edit = now
        last_text = text
        await _safe_edit(bot, chat_id, msg_id, text, reply_markup=freelance_cancel_keyboard())

    listings: list[FreelanceListing] = []
    stats: FreelancePipelineStats | None = None
    error: str | None = None
    cancelled = False

    async def _do_search() -> tuple[list[FreelanceListing], FreelancePipelineStats]:
        async with (
            ChatActionSender(bot=bot, chat_id=chat_id, action=ChatAction.TYPING),
            SerperClient(
                api_keys=settings.serper_api_keys,
                timeout=settings.search_timeout_seconds,
            ) as client,
        ):
            pipeline = FreelancePipeline(
                client,
                max_results=max_results,
                results_per_query=settings.results_per_query,
                max_search_calls=settings.max_search_calls * 2,
                allowed_platforms=platforms,
            )
            return await pipeline.run(keyword, progress=_progress)

    task = asyncio.create_task(_do_search())
    _ACTIVE_SEARCHES[(chat_id, user_id)] = task
    try:
        try:
            listings, stats = await task
        except asyncio.CancelledError:
            cancelled = True
        except Exception as exc:
            logger.exception("freelance pipeline failed")
            error = str(exc)
    finally:
        if _ACTIVE_SEARCHES.get((chat_id, user_id)) is task:
            _ACTIVE_SEARCHES.pop((chat_id, user_id), None)

    if cancelled:
        await _safe_edit(
            bot,
            chat_id,
            msg_id,
            "🛑 Поиск отменён.",
            reply_markup=back_to_menu_keyboard(),
        )
        return
    if error is not None:
        await _safe_edit(
            bot,
            chat_id,
            msg_id,
            f"❌ Ошибка во время поиска: {html.escape(error)}",
            reply_markup=back_to_menu_keyboard(),
        )
        return

    assert stats is not None

    # We persist as Instagram-shaped Listing rows so the existing listings
    # table works, and stash the platform/budget/listing_type in the
    # separate freelance_listings table via the factory.
    converted = [
        Listing(
            title=lst.title,
            link=lst.link,
            snippet=lst.snippet,
            contacts=lst.contacts,
        )
        for lst in listings
    ]

    def _factory(lst: Listing) -> dict[str, object]:
        match = next(
            (orig for orig in listings if orig.link == lst.link),
            None,
        )
        return {
            "platform": match.platform if match else "",
            "budget": match.budget if match else "",
            "listing_type": match.listing_type if match else "",
        }

    search_id = await history.save_search(
        user_id=user_id,
        keyword=keyword,
        listings=converted,
        stats=stats,  # type: ignore[arg-type]
        source="freelance",
        freelance_listing_factory=_factory,
    )
    await state.update_data(active_search_id=search_id, active_source="freelance")
    await _show_freelance_results_page(
        bot=bot,
        chat_id=chat_id,
        state=state,
        keyword=keyword,
        listings=listings,
        stats=stats,
        page=0,
        per_page=settings.results_per_page,
        from_history=False,
    )


def _render_freelance_progress_text(keyword: str, event: FreelanceProgressEvent) -> str:
    bar = _render_progress_bar(event.queries_done, event.queries_total)
    stage_human = {
        "init": "Готовлю запросы…",
        "dork_start": f"Ищу: <i>{html.escape(event.label or '...')}</i>",
        "dork_done": f"Готов запрос: <i>{html.escape(event.label or '...')}</i>",
        "dork_failed": f"Запрос упал: <i>{html.escape(event.label or '...')}</i>",
        "completed": "Поиск завершён.",
    }.get(event.stage, "Работаю…")

    counter = (
        f"Запросы: <b>{event.queries_done}</b>/{event.queries_total or '?'}"
        if event.queries_total
        else f"Запросы: <b>{event.queries_done}</b>"
    )
    accepted = f"Найдено: <b>{event.accepted}</b>/{event.max_results}"
    return (
        f"💼 Фриланс-поиск: <b>{html.escape(keyword)}</b>\n\n"
        f"<code>{bar}</code>\n"
        f"{counter} • {accepted}\n"
        f"{stage_human}"
    )


async def _show_freelance_results_page(
    *,
    bot: Bot,
    chat_id: int,
    state: FSMContext,
    keyword: str,
    listings: list[FreelanceListing] | list[StoredFreelanceListing],
    stats: FreelancePipelineStats | None,
    page: int,
    per_page: int,
    from_history: bool,
    search_id: int | None = None,
) -> None:
    text, total_pages, _total = render_freelance_results_page(
        listings=list(listings),
        page=page,
        per_page=per_page,
        keyword=keyword,
        per_platform=stats.per_platform if stats else None,
    )
    # Telegram limit guard.
    if len(text) > TELEGRAM_MAX:
        text = text[: TELEGRAM_MAX - 1] + "…"
    rows: list[list[InlineKeyboardButton]] = []
    if total_pages > 1:
        from .keyboards import (  # local import: avoid cycles at module load
            CB_FREELANCE,
            CB_FREELANCE_PROMPTS,
            CB_HIST_FREELANCE,
            CB_HIST_PAGE_PREFIX,
            CB_NOOP,
        )

        def _cb(p: int) -> str:
            if from_history and search_id is not None:
                return f"{CB_HIST_PAGE_PREFIX}:{search_id}:{p}"
            return f"{CB_FREELANCE_PROMPTS}:p{p}"

        prev_p = (page - 1) % total_pages
        next_p = (page + 1) % total_pages
        rows.append(
            [
                InlineKeyboardButton(text="◀", callback_data=_cb(prev_p)),
                InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data=CB_NOOP),
                InlineKeyboardButton(text="▶", callback_data=_cb(next_p)),
            ]
        )
    nav_row: list[InlineKeyboardButton] = [
        InlineKeyboardButton(text="🔁 Новый поиск", callback_data=CB_FREELANCE_PROMPTS)
    ]
    if from_history:
        nav_row.append(InlineKeyboardButton(text="📜 К истории", callback_data=CB_HIST_FREELANCE))
    else:
        nav_row.append(InlineKeyboardButton(text="💼 К фрилансу", callback_data=CB_FREELANCE))
    rows.append(nav_row)
    rows.append([InlineKeyboardButton(text="🏠 Меню", callback_data=CB_MENU)])
    await _ensure_control_message(
        bot, chat_id, state, text=text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
    )


async def _show_results_page(
    *,
    bot: Bot,
    chat_id: int,
    state: FSMContext,
    keyword: str,
    listings: list[Listing] | list[StoredListing],
    stats: PipelineStats | None,
    page: int,
    per_page: int,
    from_history: bool,
    created_at: datetime | None,
    search_id: int | None = None,
) -> None:
    pages = _paginate_listings(listings, per_page=per_page)
    total_pages = max(1, len(pages))
    page = max(0, min(page, total_pages - 1))
    text = _render_results_page(
        keyword=keyword,
        page=page,
        total_pages=total_pages,
        page_listings=pages[page] if pages else [],
        total_listings=len(listings),
        stats=stats,
        created_at=created_at,
        per_page=per_page,
    )
    keyboard = results_keyboard(
        page=page,
        total_pages=total_pages,
        show_back_to_history=from_history,
        search_id=search_id,
    )
    await _ensure_control_message(bot, chat_id, state, text=text, reply_markup=keyboard)


async def _show_history_list(
    *,
    bot: Bot,
    chat_id: int,
    user_id: int,
    state: FSMContext,
    history: HistoryStore,
    page: int,
    per_page: int,
    source: str = SOURCE_INSTAGRAM,
) -> None:
    """Render the history list with Instagram / Freelance tab toggle.

    The tab labels always show the total counts of *both* sources, so
    the user can see at a glance which side of the bot has more saved
    searches.
    """
    counts = {
        SOURCE_INSTAGRAM: await history.count_searches(user_id=user_id, source=SOURCE_INSTAGRAM),
        SOURCE_FREELANCE: await history.count_searches(user_id=user_id, source=SOURCE_FREELANCE),
    }
    total = counts[source]
    if total == 0:
        empty_text = _empty_history_text(source)
        await _ensure_control_message(
            bot,
            chat_id,
            state,
            text=empty_text,
            reply_markup=empty_history_keyboard(source=source, counts=counts),
        )
        return
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))
    items = await history.list_searches(
        user_id=user_id, limit=per_page, offset=page * per_page, source=source
    )
    labels = [(it.id, _format_history_label(it)) for it in items]
    text = (
        f"<b>📜 {_history_title(source)}</b> — "
        f"<b>{total}</b> "
        f"{_pluralize_searches(total)}.\n"
        f"Страница <b>{page + 1}</b>/<b>{total_pages}</b>.\n\n"
        "Жми по строке, чтобы открыть результаты."
    )
    await _ensure_control_message(
        bot,
        chat_id,
        state,
        text=text,
        reply_markup=history_list_keyboard(
            items=labels,
            page=page,
            total_pages=total_pages,
            source=source,
            counts=counts,
        ),
    )


def _history_title(source: str) -> str:
    return "Instagram-поиски" if source == SOURCE_INSTAGRAM else "Фриланс-поиски"


def _empty_history_text(source: str) -> str:
    if source == SOURCE_INSTAGRAM:
        return (
            "<b>📜 Instagram-поиски</b>\n\n"
            "Здесь пока пусто. Запусти поиск — и я сохраню результаты."
        )
    return (
        "<b>📜 Фриланс-поиски</b>\n\n"
        "Здесь пока пусто. Запусти фриланс-поиск — и я сохраню результаты."
    )


def _pluralize_searches(n: int) -> str:
    """Russian pluralisation for «поиск / поиска / поисков»."""
    n_abs = abs(n) % 100
    n_last = n_abs % 10
    if 11 <= n_abs <= 14:
        return "поисков"
    if n_last == 1:
        return "поиск"
    if 2 <= n_last <= 4:
        return "поиска"
    return "поисков"


async def _show_freelance_history_list(
    *,
    bot: Bot,
    chat_id: int,
    user_id: int,
    state: FSMContext,
    history: HistoryStore,
    page: int,
    per_page: int,
) -> None:
    """Backward-compat alias: route to the unified history list with
    source='freelance' so existing handlers keep working."""
    await _show_history_list(
        bot=bot,
        chat_id=chat_id,
        user_id=user_id,
        state=state,
        history=history,
        page=page,
        per_page=per_page,
        source=SOURCE_FREELANCE,
    )


async def _show_history_results(
    *,
    bot: Bot,
    chat_id: int,
    user_id: int,
    state: FSMContext,
    history: HistoryStore,
    search_id: int,
    page: int,
    per_page: int,
) -> None:
    record = await history.get_search(user_id=user_id, search_id=search_id)
    if record is None:
        await _ensure_control_message(
            bot,
            chat_id,
            state,
            text="🤷 Этот поиск не найден в истории.",
            reply_markup=empty_history_keyboard(),
        )
        return
    search, listings = record
    if search.source == "freelance":
        record_fr = await history.get_freelance_search(user_id=user_id, search_id=search_id)
        if record_fr is None:
            await _ensure_control_message(
                bot,
                chat_id,
                state,
                text="🤷 Этот поиск не найден в истории.",
                reply_markup=empty_history_keyboard(),
            )
            return
        _, fr_listings = record_fr
        await _show_freelance_results_page(
            bot=bot,
            chat_id=chat_id,
            state=state,
            keyword=search.keyword,
            listings=fr_listings,
            stats=None,
            page=page,
            per_page=per_page,
            from_history=True,
            search_id=search.id,
        )
        return
    await _show_results_page(
        bot=bot,
        chat_id=chat_id,
        state=state,
        keyword=search.keyword,
        listings=listings,
        stats=None,
        page=page,
        per_page=per_page,
        from_history=True,
        created_at=search.created_at,
        search_id=search.id,
    )


# --- Settings UI ------------------------------------------------------------


async def _show_settings(
    *,
    bot: Bot,
    chat_id: int,
    state: FSMContext,
    user: StoredUser,
    settings: Settings,
) -> None:
    selected = _user_country_codes(user)
    max_results = _user_max_results(user, settings)
    selected_platforms = _user_platforms(user)
    text = (
        "<b>⚙️ Настройки</b>\n\n"
        f"🌐 Страны: <b>{len(selected)}</b> из {len(selectable_countries())}\n"
        f"🔢 Кол-во ссылок за поиск: <b>{max_results}</b>\n"
        f"💼 Платформы: <b>{len(selected_platforms)}</b> из {len(PLATFORMS)}"
    )
    await _ensure_control_message(
        bot,
        chat_id,
        state,
        text=text,
        reply_markup=settings_menu_keyboard(
            selected_countries=selected,
            max_results=max_results,
            total_countries=len(selectable_countries()),
            selected_platforms=selected_platforms,
            total_platforms=len(PLATFORMS),
        ),
    )


async def _show_country_picker(
    *,
    bot: Bot,
    chat_id: int,
    state: FSMContext,
    user: StoredUser,
    page: int,
) -> None:
    selected = _user_country_codes(user)
    n = len(selected)
    summary: str
    if n == 0:
        summary = "пока ничего"
    elif n >= len(selectable_countries()):
        summary = "все"
    else:
        names = []
        for code in selected[:6]:
            country = get_country(code)
            if country:
                names.append(f"{country.flag} {country.name_ru}")
        more = "" if n <= 6 else f", и ещё {n - 6}"
        summary = ", ".join(names) + more
    text = (
        "<b>🌐 Страны для поиска</b>\n\n"
        "Тыкай страны, чтобы включать / выключать. Можно выбрать одну, "
        "несколько, через пресеты внизу или «Все».\n\n"
        f"Сейчас: <b>{n}</b> из {len(selectable_countries())} — {html.escape(summary)}."
    )
    await _ensure_control_message(
        bot,
        chat_id,
        state,
        text=text,
        reply_markup=country_picker_keyboard(selected=selected, page=page),
    )


async def _show_results_picker(
    *,
    bot: Bot,
    chat_id: int,
    state: FSMContext,
    user: StoredUser,
    settings: Settings,
) -> None:
    current = _user_max_results(user, settings)
    text = (
        "<b>🔢 Кол-во ссылок за поиск</b>\n\n"
        f"Сейчас: <b>{current}</b>.\n"
        "Выбирай — больше = дольше, но шире покрытие."
    )
    await _ensure_control_message(
        bot,
        chat_id,
        state,
        text=text,
        reply_markup=results_picker_keyboard(
            current=user.pref_max_results,
            choices=settings.result_count_choices,
            default=settings.max_results,
        ),
    )


# --- Admin panel UI ---------------------------------------------------------


async def _show_admin_panel(
    *,
    bot: Bot,
    chat_id: int,
    state: FSMContext,
    history: HistoryStore,
    status: str,
    page: int,
) -> None:
    try:
        status_enum = UserStatus(status)
    except ValueError:
        status_enum = UserStatus.PENDING
    total = await history.count_users(status=status_enum)
    total_pages = max(1, (total + ADMIN_USERS_PER_PAGE - 1) // ADMIN_USERS_PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    users = await history.list_users(
        status=status_enum,
        limit=ADMIN_USERS_PER_PAGE,
        offset=page * ADMIN_USERS_PER_PAGE,
    )
    text = (
        "<b>🛡 Админ-панель</b>\n\n"
        f"Статус: <b>{_human_status(status_enum)}</b> — <b>{total}</b> юзеров.\n"
        f"Страница <b>{page + 1}</b>/<b>{total_pages}</b>."
    )
    if total == 0:
        text += "\n\nЗдесь пусто."
    await _ensure_control_message(
        bot,
        chat_id,
        state,
        text=text,
        reply_markup=admin_panel_keyboard(
            users=users,
            status=status_enum.value,
            page=page,
            total_pages=total_pages,
        ),
    )


async def _show_admin_user_detail(
    *,
    bot: Bot,
    chat_id: int,
    state: FSMContext,
    history: HistoryStore,
    target_user_id: int,
) -> None:
    user = await history.get_user(target_user_id)
    if user is None:
        await _ensure_control_message(
            bot,
            chat_id,
            state,
            text="🤷 Пользователь не найден.",
            reply_markup=back_to_menu_keyboard(),
        )
        return

    label = user.username and f"@{user.username}"
    name = " ".join(p for p in (user.first_name, user.last_name) if p)
    headline = label or name or f"id {user.id}"
    when_req = (
        user.requested_at.astimezone().strftime("%d.%m.%Y %H:%M") if user.requested_at else "—"
    )
    when_dec = user.decided_at.astimezone().strftime("%d.%m.%Y %H:%M") if user.decided_at else "—"
    used = await history.count_user_total_listings(user_id=user.id)
    limit_str = f"{used}/{user.link_limit}" if user.link_limit else f"{used}/∞"
    text = (
        f"<b>{html.escape(headline)}</b>\n\n"
        f"ID: <code>{user.id}</code>\n"
        f"Статус: <b>{_human_status(user.status)}</b>\n"
        f"Админ: {'да' if user.is_admin else 'нет'}\n"
        f"Ссылки: <b>{limit_str}</b>\n"
        f"Запрошен: {when_req}\n"
        f"Решение: {when_dec}"
    )
    if name and label:
        text += f"\nИмя: {html.escape(name)}"
    await _ensure_control_message(
        bot,
        chat_id,
        state,
        text=text,
        reply_markup=admin_user_actions_keyboard(user),
    )


def _human_status(status: UserStatus) -> str:
    return {
        UserStatus.PENDING: "ожидание",
        UserStatus.APPROVED: "одобрен",
        UserStatus.BLOCKED: "заблокирован",
        UserStatus.DENIED: "отклонён",
    }[status]


# --- Resolution helpers -----------------------------------------------------


async def _resolve_user(
    *,
    bot: Bot,
    history: HistoryStore,
    settings: Settings,
    tg_user: object,
) -> StoredUser:
    """Get the StoredUser for an incoming Telegram user, bootstrapping if needed."""
    return await _bootstrap_user(bot=bot, history=history, settings=settings, tg_user=tg_user)


async def _gate(
    *,
    bot: Bot,
    chat_id: int,
    state: FSMContext,
    history: HistoryStore,
    settings: Settings,
    tg_user: object,
) -> StoredUser | None:
    """Resolve the user and either return them (approved) or render the gate screen.

    Returns ``None`` when the caller should bail out because the user cannot
    proceed.
    """
    user = await _resolve_user(bot=bot, history=history, settings=settings, tg_user=tg_user)
    if user.status == UserStatus.APPROVED:
        return user
    await _show_access_gate(bot, chat_id, state, user=user)
    return None


# --- Dispatcher -------------------------------------------------------------


def build_dispatcher(settings: Settings, history: HistoryStore) -> Dispatcher:
    """Build an aiogram dispatcher wired to the search pipeline."""
    dp = Dispatcher(storage=MemoryStorage())
    per_page = settings.results_per_page
    history_per_page = settings.history_per_page

    async def _gated(
        message_or_cb: Message | CallbackQuery,
        state: FSMContext,
    ) -> StoredUser | None:
        chat_id = _chat_id_of(message_or_cb)
        tg_user = message_or_cb.from_user
        if tg_user is None or chat_id is None:
            return None
        bot = message_or_cb.bot
        if bot is None:
            return None
        return await _gate(
            bot=bot,
            chat_id=chat_id,
            state=state,
            history=history,
            settings=settings,
            tg_user=tg_user,
        )

    @dp.message(CommandStart())
    async def on_start(message: Message, state: FSMContext) -> None:
        if message.bot is None or message.from_user is None:
            return
        # Reset the control message so the next render lives in a fresh
        # bubble after /start.
        await state.update_data(control_message_id=None)
        await _force_clear_reply_keyboard(message.bot, message.chat.id)

        user = await _resolve_user(
            bot=message.bot,
            history=history,
            settings=settings,
            tg_user=message.from_user,
        )
        if user.status != UserStatus.APPROVED:
            await _show_access_gate(message.bot, message.chat.id, state, user=user)
            return
        await _show_menu(message.bot, message.chat.id, state, user=user, history=history)

    @dp.message(Command("cancel"))
    async def on_cancel(message: Message, state: FSMContext) -> None:
        if message.bot is None or message.from_user is None:
            return
        user = await _gated(message, state)
        if user is None:
            return
        await state.set_state(None)
        await _show_menu(message.bot, message.chat.id, state, user=user, history=history)

    @dp.message(Command("admin"))
    async def on_admin(message: Message, state: FSMContext) -> None:
        if message.bot is None or message.from_user is None:
            return
        user = await _gated(message, state)
        if user is None or not user.is_admin:
            return
        await state.update_data(control_message_id=None)
        await _show_admin_panel(
            bot=message.bot,
            chat_id=message.chat.id,
            state=state,
            history=history,
            status=UserStatus.PENDING.value,
            page=0,
        )

    @dp.message(Command("search"))
    async def on_search_cmd(message: Message, state: FSMContext) -> None:
        if message.bot is None or message.from_user is None:
            return
        user = await _gated(message, state)
        if user is None:
            return
        parsed = _parse_search_command(message.text)
        await _delete_message_silently(message.bot, message.chat.id, message.message_id)
        if parsed is None:
            await _prompt_for_keyword(
                message.bot, message.chat.id, state, user=user, settings=settings
            )
            return
        await _run_search(
            bot=message.bot,
            chat_id=message.chat.id,
            user=user,
            keyword=parsed.keyword,
            settings=settings,
            history=history,
            state=state,
        )

    @dp.message(Command("help"))
    async def on_help_cmd(message: Message, state: FSMContext) -> None:
        if message.bot is None or message.from_user is None:
            return
        user = await _gated(message, state)
        if user is None:
            return
        await _delete_message_silently(message.bot, message.chat.id, message.message_id)
        await _ensure_control_message(
            message.bot, message.chat.id, state, text=HELP_TEXT, reply_markup=help_keyboard()
        )

    @dp.message(Command("menu"))
    async def on_menu_cmd(message: Message, state: FSMContext) -> None:
        if message.bot is None or message.from_user is None:
            return
        user = await _gated(message, state)
        if user is None:
            return
        await _delete_message_silently(message.bot, message.chat.id, message.message_id)
        await state.update_data(active_search_id=None)
        await _show_menu(message.bot, message.chat.id, state, user=user, history=history)

    @dp.message(SearchForm.waiting_for_keyword, F.text & ~F.text.startswith("/"))
    async def on_keyword_input(message: Message, state: FSMContext) -> None:
        if message.bot is None or message.from_user is None:
            return
        user = await _gated(message, state)
        if user is None:
            return
        text = (message.text or "").strip()
        await _delete_message_silently(message.bot, message.chat.id, message.message_id)
        if not text:
            await _prompt_for_keyword(
                message.bot, message.chat.id, state, user=user, settings=settings
            )
            return
        await _run_search(
            bot=message.bot,
            chat_id=message.chat.id,
            user=user,
            keyword=text,
            settings=settings,
            history=history,
            state=state,
        )

    # Loose-text fallback: any non-slash text outside an explicit "waiting
    # for keyword" state. We explicitly exclude both keyword-input states
    # so the freelance handler below gets first dibs.
    @dp.message(
        F.text & ~F.text.startswith("/"),
        ~StateFilter(SearchForm.waiting_for_keyword),
        ~StateFilter(SearchForm.waiting_for_freelance_keyword),
    )
    async def on_loose_text(message: Message, state: FSMContext) -> None:
        if message.bot is None or message.from_user is None:
            return
        user = await _gated(message, state)
        if user is None:
            return
        text = (message.text or "").strip()
        if not text:
            return
        await _delete_message_silently(message.bot, message.chat.id, message.message_id)
        await _run_search(
            bot=message.bot,
            chat_id=message.chat.id,
            user=user,
            keyword=text,
            settings=settings,
            history=history,
            state=state,
        )

    # --- Callback handlers -------------------------------------------------

    @dp.callback_query(F.data == CB_NOOP)
    async def cb_noop(callback: CallbackQuery) -> None:
        await callback.answer()

    @dp.callback_query(F.data == CB_ACCESS_PENDING)
    async def cb_access_pending(callback: CallbackQuery) -> None:
        await callback.answer("Запрос уже отправлен. Жди ответа админа.", show_alert=True)

    @dp.callback_query(F.data == CB_ACCESS_REQUEST)
    async def cb_access_request(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer("Отправил админу запрос.")
        if callback.bot is None or callback.message is None or callback.from_user is None:
            return
        # Refresh user record (ensures DB has the latest profile).
        user = await _resolve_user(
            bot=callback.bot,
            history=history,
            settings=settings,
            tg_user=callback.from_user,
        )
        await state.update_data(control_message_id=callback.message.message_id)
        if user.status == UserStatus.BLOCKED:
            await _show_access_gate(callback.bot, callback.message.chat.id, state, user=user)
            return
        if user.status == UserStatus.APPROVED:
            await _show_menu(
                callback.bot, callback.message.chat.id, state, user=user, history=history
            )
            return
        already_pending = user.status == UserStatus.PENDING and user.requested_at is not None
        await _ensure_control_message(
            callback.bot,
            callback.message.chat.id,
            state,
            text=ACCESS_PENDING_TEXT,
            reply_markup=access_request_keyboard(requested=True),
        )
        if not already_pending:
            await _notify_admins_of_request(callback.bot, history, user)

    async def _wrap_cb(
        callback: CallbackQuery,
        state: FSMContext,
        action: Callable[[StoredUser], Awaitable[None]],
    ) -> None:
        if callback.bot is None or callback.message is None or callback.from_user is None:
            await callback.answer()
            return
        user = await _resolve_user(
            bot=callback.bot,
            history=history,
            settings=settings,
            tg_user=callback.from_user,
        )
        if user.status != UserStatus.APPROVED:
            await callback.answer()
            await state.update_data(control_message_id=callback.message.message_id)
            await _show_access_gate(callback.bot, callback.message.chat.id, state, user=user)
            return
        await callback.answer()
        await state.update_data(control_message_id=callback.message.message_id)
        await action(user)

    @dp.callback_query(F.data == CB_MENU)
    async def cb_menu(callback: CallbackQuery, state: FSMContext) -> None:
        async def _do(user: StoredUser) -> None:
            assert callback.bot is not None and callback.message is not None
            await state.update_data(active_search_id=None)
            await _show_menu(
                callback.bot, callback.message.chat.id, state, user=user, history=history
            )

        await _wrap_cb(callback, state, _do)

    @dp.callback_query(F.data == CB_SEARCH)
    async def cb_search(callback: CallbackQuery, state: FSMContext) -> None:
        async def _do(user: StoredUser) -> None:
            assert callback.bot is not None and callback.message is not None
            await _prompt_for_keyword(
                callback.bot, callback.message.chat.id, state, user=user, settings=settings
            )

        await _wrap_cb(callback, state, _do)

    @dp.callback_query(F.data == CB_CANCEL)
    async def cb_cancel(callback: CallbackQuery, state: FSMContext) -> None:
        async def _do(user: StoredUser) -> None:
            assert callback.bot is not None and callback.message is not None
            await _show_menu(
                callback.bot, callback.message.chat.id, state, user=user, history=history
            )

        await _wrap_cb(callback, state, _do)

    @dp.callback_query(F.data == CB_CANCEL_SEARCH)
    async def cb_cancel_search(callback: CallbackQuery) -> None:
        if callback.message is None or callback.from_user is None:
            await callback.answer()
            return
        chat_id = callback.message.chat.id
        user_id = callback.from_user.id
        task = _ACTIVE_SEARCHES.get((chat_id, user_id))
        if task and not task.done():
            task.cancel()
            await callback.answer("Останавливаю…")
        else:
            await callback.answer("Поиск уже завершён.")

    @dp.callback_query(F.data.startswith(f"{CB_PAGE_PREFIX}:"))
    async def cb_page(callback: CallbackQuery, state: FSMContext) -> None:
        async def _do(user: StoredUser) -> None:
            assert callback.bot is not None and callback.message is not None
            assert callback.data is not None
            try:
                page = int(callback.data.split(":", 1)[1])
            except (ValueError, IndexError):
                return
            data = await state.get_data()
            search_id = data.get("active_search_id")
            if not isinstance(search_id, int):
                await _show_menu(
                    callback.bot, callback.message.chat.id, state, user=user, history=history
                )
                return
            await _show_history_results(
                bot=callback.bot,
                chat_id=callback.message.chat.id,
                user_id=user.id,
                state=state,
                history=history,
                search_id=search_id,
                page=page,
                per_page=per_page,
            )

        await _wrap_cb(callback, state, _do)

    @dp.callback_query(F.data == CB_HISTORY)
    async def cb_history(callback: CallbackQuery, state: FSMContext) -> None:
        async def _do(user: StoredUser) -> None:
            assert callback.bot is not None and callback.message is not None
            await state.update_data(active_search_id=None)
            await _show_history_list(
                bot=callback.bot,
                chat_id=callback.message.chat.id,
                user_id=user.id,
                state=state,
                history=history,
                page=0,
                per_page=history_per_page,
                source=SOURCE_INSTAGRAM,
            )

        await _wrap_cb(callback, state, _do)

    @dp.callback_query(F.data.startswith(f"{CB_HIST_LIST_PREFIX}:"))
    async def cb_history_page(callback: CallbackQuery, state: FSMContext) -> None:
        async def _do(user: StoredUser) -> None:
            assert callback.bot is not None and callback.message is not None
            assert callback.data is not None
            parts = callback.data.split(":", 2)
            if len(parts) < 3:
                return
            source_raw, page_raw = parts[1], parts[2]
            source = (
                source_raw
                if source_raw in (SOURCE_INSTAGRAM, SOURCE_FREELANCE)
                else SOURCE_INSTAGRAM
            )
            try:
                page = int(page_raw)
            except ValueError:
                return
            await _show_history_list(
                bot=callback.bot,
                chat_id=callback.message.chat.id,
                user_id=user.id,
                state=state,
                history=history,
                page=page,
                per_page=history_per_page,
                source=source,
            )

        await _wrap_cb(callback, state, _do)

    @dp.callback_query(F.data.startswith(f"{CB_HIST_SOURCE_PREFIX}:"))
    async def cb_history_source(callback: CallbackQuery, state: FSMContext) -> None:
        """Switch history tab (Instagram ↔ Freelance)."""

        async def _do(user: StoredUser) -> None:
            assert callback.bot is not None and callback.message is not None
            assert callback.data is not None
            parts = callback.data.split(":", 2)
            if len(parts) < 3:
                return
            source_raw, page_raw = parts[1], parts[2]
            source = (
                source_raw
                if source_raw in (SOURCE_INSTAGRAM, SOURCE_FREELANCE)
                else SOURCE_INSTAGRAM
            )
            try:
                page = int(page_raw)
            except ValueError:
                page = 0
            await _show_history_list(
                bot=callback.bot,
                chat_id=callback.message.chat.id,
                user_id=user.id,
                state=state,
                history=history,
                page=page,
                per_page=history_per_page,
                source=source,
            )

        await _wrap_cb(callback, state, _do)

    @dp.callback_query(F.data.startswith(f"{CB_REPEAT}:"))
    async def cb_repeat_search(callback: CallbackQuery, state: FSMContext) -> None:
        """Re-run a previous search (or the latest one for the source)."""

        async def _do(user: StoredUser) -> None:
            assert callback.bot is not None and callback.message is not None
            assert callback.data is not None
            parts = callback.data.split(":", 2)
            if len(parts) < 3:
                return
            source_raw, sid_raw = parts[1], parts[2]
            source = (
                source_raw
                if source_raw in (SOURCE_INSTAGRAM, SOURCE_FREELANCE)
                else SOURCE_INSTAGRAM
            )
            try:
                search_id = int(sid_raw)
            except ValueError:
                search_id = 0
            keyword: str | None = None
            if search_id > 0:
                stored = await history.get_search(user_id=user.id, search_id=search_id)
                if stored is not None:
                    keyword = stored[0].keyword
            if keyword is None:
                keyword = await history.latest_keyword(user_id=user.id, source=source)
            if not keyword:
                # Nothing to repeat — bounce back to the appropriate tab.
                if source == SOURCE_FREELANCE:
                    await _show_freelance_menu(
                        callback.bot, callback.message.chat.id, state, user=user, history=history
                    )
                else:
                    await _show_menu(
                        callback.bot, callback.message.chat.id, state, user=user, history=history
                    )
                return
            if source == SOURCE_FREELANCE:
                await _run_freelance_search(
                    bot=callback.bot,
                    chat_id=callback.message.chat.id,
                    user=user,
                    keyword=keyword,
                    settings=settings,
                    history=history,
                    state=state,
                )
            else:
                await _run_search(
                    bot=callback.bot,
                    chat_id=callback.message.chat.id,
                    user=user,
                    keyword=keyword,
                    settings=settings,
                    history=history,
                    state=state,
                )

        await _wrap_cb(callback, state, _do)

    @dp.callback_query(F.data == CB_HELP)
    async def cb_help(callback: CallbackQuery, state: FSMContext) -> None:
        async def _do(user: StoredUser) -> None:
            assert callback.bot is not None and callback.message is not None
            await _ensure_control_message(
                callback.bot,
                callback.message.chat.id,
                state,
                text=HELP_TEXT,
                reply_markup=help_keyboard(),
            )

        await _wrap_cb(callback, state, _do)

    @dp.callback_query(F.data.startswith(f"{CB_HIST_OPEN_PREFIX}:"))
    async def cb_history_open(callback: CallbackQuery, state: FSMContext) -> None:
        async def _do(user: StoredUser) -> None:
            assert callback.bot is not None and callback.message is not None
            assert callback.data is not None
            parts = callback.data.split(":")
            if len(parts) < 2:
                return
            try:
                search_id = int(parts[1])
                page = int(parts[2]) if len(parts) >= 3 else 0
            except ValueError:
                return
            await state.update_data(active_search_id=search_id)
            await _show_history_results(
                bot=callback.bot,
                chat_id=callback.message.chat.id,
                user_id=user.id,
                state=state,
                history=history,
                search_id=search_id,
                page=page,
                per_page=per_page,
            )

        await _wrap_cb(callback, state, _do)

    @dp.callback_query(F.data.startswith(f"{CB_HIST_PAGE_PREFIX}:"))
    async def cb_history_results_page(callback: CallbackQuery, state: FSMContext) -> None:
        async def _do(user: StoredUser) -> None:
            assert callback.bot is not None and callback.message is not None
            assert callback.data is not None
            parts = callback.data.split(":")
            if len(parts) != 3:
                return
            try:
                search_id = int(parts[1])
                page = int(parts[2])
            except ValueError:
                return
            await state.update_data(active_search_id=search_id)
            await _show_history_results(
                bot=callback.bot,
                chat_id=callback.message.chat.id,
                user_id=user.id,
                state=state,
                history=history,
                search_id=search_id,
                page=page,
                per_page=per_page,
            )

        await _wrap_cb(callback, state, _do)

    # --- Freelance tab callbacks -------------------------------------------

    async def _show_freelance_menu(
        bot: Bot,
        chat_id: int,
        st: FSMContext,
        *,
        user: StoredUser,
        history: HistoryStore,
    ) -> None:
        await st.set_state(None)
        last = await history.latest_keyword(user_id=user.id, source=SOURCE_FREELANCE)
        await _ensure_control_message(
            bot,
            chat_id,
            st,
            text=(
                "<b>💼 Фриланс-поиск</b>\n\n"
                "Ищу заказы/проекты на Upwork, Fiverr, Freelancer.com, Kwork, "
                "где клиент оставил контакт (email / Telegram / WhatsApp).\n\n"
                "Жми <b>🔎 Запустить поиск</b> или сначала выбери платформы."
            ),
            reply_markup=freelance_menu_keyboard(last_keyword=last),
        )

    @dp.callback_query(F.data == CB_FREELANCE)
    async def cb_freelance(callback: CallbackQuery, state: FSMContext) -> None:
        async def _do(user: StoredUser) -> None:
            assert callback.bot is not None and callback.message is not None
            await _show_freelance_menu(
                callback.bot, callback.message.chat.id, state, user=user, history=history
            )

        await _wrap_cb(callback, state, _do)

    @dp.callback_query(F.data == CB_FREELANCE_PROMPTS)
    async def cb_freelance_prompts(callback: CallbackQuery, state: FSMContext) -> None:
        async def _do(user: StoredUser) -> None:
            assert callback.bot is not None and callback.message is not None
            await state.set_state(SearchForm.waiting_for_freelance_keyword)
            await _ensure_control_message(
                callback.bot,
                callback.message.chat.id,
                state,
                text=_format_freelance_prompt_text(user, settings=settings),
                reply_markup=cancel_keyboard(),
            )

        await _wrap_cb(callback, state, _do)

    @dp.callback_query(F.data == CB_FREELANCE_PLATFORMS)
    async def cb_freelance_platforms(callback: CallbackQuery, state: FSMContext) -> None:
        async def _do(user: StoredUser) -> None:
            assert callback.bot is not None and callback.message is not None
            selected = list(_user_platforms(user))
            await _ensure_control_message(
                callback.bot,
                callback.message.chat.id,
                state,
                text=(
                    "<b>🌐 Платформы для фриланс-поиска</b>\n\nОтметь платформы, по которым искать."
                ),
                reply_markup=freelance_platforms_keyboard(selected=selected),
            )

        await _wrap_cb(callback, state, _do)

    @dp.callback_query(F.data.startswith(f"{CB_FREELANCE_PLATFORM_TOGGLE_PREFIX}:"))
    async def cb_freelance_platform_toggle(callback: CallbackQuery, state: FSMContext) -> None:
        async def _do(user: StoredUser) -> None:
            assert callback.bot is not None and callback.message is not None
            assert callback.data is not None
            code = callback.data.split(":", 1)[1].lower()
            from .freelance.platforms import get_platform as _get_plat

            if _get_plat(code) is None:
                return
            current = list(_user_platforms(user))
            if code in current:
                current.remove(code)
            else:
                current.append(code)
            # Persist in canonical registry order.
            from .freelance.platforms import PLATFORMS as _PLATFORMS

            ordered = tuple(p.code for p in _PLATFORMS if p.code in current)
            await history.update_user_prefs(user_id=user.id, platforms=ordered)
            refreshed = await history.get_user(user.id) or user
            await _ensure_control_message(
                callback.bot,
                callback.message.chat.id,
                state,
                text=(
                    "<b>🌐 Платформы для фриланс-поиска</b>\n\nОтметь платформы, по которым искать."
                ),
                reply_markup=freelance_platforms_keyboard(
                    selected=list(_user_platforms(refreshed))
                ),
            )

        await _wrap_cb(callback, state, _do)

    @dp.callback_query(F.data == CB_FREELANCE_PLATFORM_ALL)
    async def cb_freelance_platform_all(callback: CallbackQuery, state: FSMContext) -> None:
        async def _do(user: StoredUser) -> None:
            assert callback.bot is not None and callback.message is not None
            from .freelance.platforms import all_platform_codes as _all_codes

            await history.update_user_prefs(user_id=user.id, platforms=_all_codes())
            refreshed = await history.get_user(user.id) or user
            await _ensure_control_message(
                callback.bot,
                callback.message.chat.id,
                state,
                text=(
                    "<b>🌐 Платформы для фриланс-поиска</b>\n\nОтметь платформы, по которым искать."
                ),
                reply_markup=freelance_platforms_keyboard(
                    selected=list(_user_platforms(refreshed))
                ),
            )

        await _wrap_cb(callback, state, _do)

    @dp.callback_query(F.data == CB_FREELANCE_PLATFORM_NONE)
    async def cb_freelance_platform_none(callback: CallbackQuery, state: FSMContext) -> None:
        async def _do(user: StoredUser) -> None:
            assert callback.bot is not None and callback.message is not None
            await history.update_user_prefs(user_id=user.id, platforms=(), clear_platforms=True)
            refreshed = await history.get_user(user.id) or user
            await _ensure_control_message(
                callback.bot,
                callback.message.chat.id,
                state,
                text=(
                    "<b>🌐 Платформы для фриланс-поиска</b>\n\nОтметь платформы, по которым искать."
                ),
                reply_markup=freelance_platforms_keyboard(
                    selected=list(_user_platforms(refreshed))
                ),
            )

        await _wrap_cb(callback, state, _do)

    @dp.callback_query(F.data == CB_HIST_FREELANCE)
    async def cb_hist_freelance(callback: CallbackQuery, state: FSMContext) -> None:
        async def _do(user: StoredUser) -> None:
            assert callback.bot is not None and callback.message is not None
            await _show_freelance_history_list(
                bot=callback.bot,
                chat_id=callback.message.chat.id,
                user_id=user.id,
                state=state,
                history=history,
                page=0,
                per_page=history_per_page,
            )

        await _wrap_cb(callback, state, _do)

    @dp.message(SearchForm.waiting_for_freelance_keyword, F.text & ~F.text.startswith("/"))
    async def on_freelance_keyword_input(message: Message, state: FSMContext) -> None:
        if message.bot is None or message.from_user is None:
            return
        user = await _gated(message, state)
        if user is None:
            return
        text = (message.text or "").strip()
        await _delete_message_silently(message.bot, message.chat.id, message.message_id)
        if not text:
            await state.set_state(SearchForm.waiting_for_freelance_keyword)
            await _ensure_control_message(
                message.bot,
                message.chat.id,
                state,
                text=_format_freelance_prompt_text(user, settings=settings),
                reply_markup=cancel_keyboard(),
            )
            return
        await _run_freelance_search(
            bot=message.bot,
            chat_id=message.chat.id,
            user=user,
            keyword=text,
            settings=settings,
            history=history,
            state=state,
        )

    # --- Settings callbacks ------------------------------------------------

    @dp.callback_query(F.data == CB_SETTINGS)
    async def cb_settings(callback: CallbackQuery, state: FSMContext) -> None:
        async def _do(user: StoredUser) -> None:
            assert callback.bot is not None and callback.message is not None
            await _show_settings(
                bot=callback.bot,
                chat_id=callback.message.chat.id,
                state=state,
                user=user,
                settings=settings,
            )

        await _wrap_cb(callback, state, _do)

    @dp.callback_query(F.data == CB_SET_COUNTRIES)
    async def cb_set_countries(callback: CallbackQuery, state: FSMContext) -> None:
        async def _do(user: StoredUser) -> None:
            assert callback.bot is not None and callback.message is not None
            await _show_country_picker(
                bot=callback.bot,
                chat_id=callback.message.chat.id,
                state=state,
                user=user,
                page=0,
            )

        await _wrap_cb(callback, state, _do)

    @dp.callback_query(F.data == CB_SET_RESULTS)
    async def cb_set_results(callback: CallbackQuery, state: FSMContext) -> None:
        async def _do(user: StoredUser) -> None:
            assert callback.bot is not None and callback.message is not None
            await _show_results_picker(
                bot=callback.bot,
                chat_id=callback.message.chat.id,
                state=state,
                user=user,
                settings=settings,
            )

        await _wrap_cb(callback, state, _do)

    @dp.callback_query(F.data == CB_SET_PLATFORMS)
    async def cb_set_platforms(callback: CallbackQuery, state: FSMContext) -> None:
        """Settings → freelance platforms shortcut.

        Reuses the freelance platform picker so the same UX flows
        regardless of where the user came from.
        """

        async def _do(user: StoredUser) -> None:
            assert callback.bot is not None and callback.message is not None
            selected = list(_user_platforms(user))
            await _ensure_control_message(
                callback.bot,
                callback.message.chat.id,
                state,
                text=(
                    "<b>💼 Платформы для фриланс-поиска</b>\n\n"
                    "Отметь платформы, по которым искать. "
                    "«Снять все» отключит фриланс-поиск."
                ),
                reply_markup=freelance_platforms_keyboard(
                    selected=selected,
                    back_callback=CB_SETTINGS,
                    back_label="⚙️ В настройки",
                ),
            )

        await _wrap_cb(callback, state, _do)

    @dp.callback_query(F.data.startswith(f"{CB_COUNTRY_PAGE_PREFIX}:"))
    async def cb_country_page(callback: CallbackQuery, state: FSMContext) -> None:
        async def _do(user: StoredUser) -> None:
            assert callback.bot is not None and callback.message is not None
            assert callback.data is not None
            try:
                page = int(callback.data.split(":", 1)[1])
            except (ValueError, IndexError):
                return
            await _show_country_picker(
                bot=callback.bot,
                chat_id=callback.message.chat.id,
                state=state,
                user=user,
                page=page,
            )

        await _wrap_cb(callback, state, _do)

    @dp.callback_query(F.data.startswith(f"{CB_COUNTRY_TOGGLE_PREFIX}:"))
    async def cb_country_toggle(callback: CallbackQuery, state: FSMContext) -> None:
        async def _do(user: StoredUser) -> None:
            assert callback.bot is not None and callback.message is not None
            assert callback.data is not None
            parts = callback.data.split(":")
            if len(parts) != 3:
                return
            try:
                page = int(parts[1])
            except ValueError:
                return
            code = parts[2].upper()
            if get_country(code) is None:
                return
            current = list(_user_country_codes(user))
            if code in current:
                current.remove(code)
            else:
                current.append(code)
            # Persist by canonical order.
            ordered = tuple(c.code for c in COUNTRIES if c.code in current)
            await history.update_user_prefs(user_id=user.id, countries=ordered)
            refreshed = await history.get_user(user.id) or user
            await _show_country_picker(
                bot=callback.bot,
                chat_id=callback.message.chat.id,
                state=state,
                user=refreshed,
                page=page,
            )

        await _wrap_cb(callback, state, _do)

    @dp.callback_query(F.data == CB_COUNTRY_ALL)
    async def cb_country_all(callback: CallbackQuery, state: FSMContext) -> None:
        async def _do(user: StoredUser) -> None:
            assert callback.bot is not None and callback.message is not None
            await history.update_user_prefs(user_id=user.id, countries=all_country_codes())
            refreshed = await history.get_user(user.id) or user
            await _show_country_picker(
                bot=callback.bot,
                chat_id=callback.message.chat.id,
                state=state,
                user=refreshed,
                page=0,
            )

        await _wrap_cb(callback, state, _do)

    @dp.callback_query(F.data == CB_COUNTRY_NONE)
    async def cb_country_none(callback: CallbackQuery, state: FSMContext) -> None:
        async def _do(user: StoredUser) -> None:
            assert callback.bot is not None and callback.message is not None
            await history.update_user_prefs(user_id=user.id, countries=())
            refreshed = await history.get_user(user.id) or user
            await _show_country_picker(
                bot=callback.bot,
                chat_id=callback.message.chat.id,
                state=state,
                user=refreshed,
                page=0,
            )

        await _wrap_cb(callback, state, _do)

    @dp.callback_query(F.data.startswith(f"{CB_COUNTRY_GROUP_PREFIX}:"))
    async def cb_country_group(callback: CallbackQuery, state: FSMContext) -> None:
        async def _do(user: StoredUser) -> None:
            assert callback.bot is not None and callback.message is not None
            assert callback.data is not None
            group_code = callback.data.split(":", 1)[1]
            target = next((g for g in GROUPS if g.code == group_code), None)
            if target is None:
                return
            await history.update_user_prefs(user_id=user.id, countries=target.members)
            refreshed = await history.get_user(user.id) or user
            await _show_country_picker(
                bot=callback.bot,
                chat_id=callback.message.chat.id,
                state=state,
                user=refreshed,
                page=0,
            )

        await _wrap_cb(callback, state, _do)

    @dp.callback_query(F.data.startswith(f"{CB_RESULTS_PICK_PREFIX}:"))
    async def cb_results_pick(callback: CallbackQuery, state: FSMContext) -> None:
        async def _do(user: StoredUser) -> None:
            assert callback.bot is not None and callback.message is not None
            assert callback.data is not None
            try:
                count = int(callback.data.split(":", 1)[1])
            except (ValueError, IndexError):
                return
            if count not in settings.result_count_choices:
                return
            await history.update_user_prefs(user_id=user.id, max_results=count)
            refreshed = await history.get_user(user.id) or user
            await _show_results_picker(
                bot=callback.bot,
                chat_id=callback.message.chat.id,
                state=state,
                user=refreshed,
                settings=settings,
            )

        await _wrap_cb(callback, state, _do)

    @dp.callback_query(F.data == CB_RESULTS_PICK_RESET)
    async def cb_results_pick_reset(callback: CallbackQuery, state: FSMContext) -> None:
        async def _do(user: StoredUser) -> None:
            assert callback.bot is not None and callback.message is not None
            await history.update_user_prefs(user_id=user.id, clear_max_results=True)
            refreshed = await history.get_user(user.id) or user
            await _show_results_picker(
                bot=callback.bot,
                chat_id=callback.message.chat.id,
                state=state,
                user=refreshed,
                settings=settings,
            )

        await _wrap_cb(callback, state, _do)

    # --- Admin panel callbacks --------------------------------------------

    @dp.callback_query(F.data == CB_ADMIN_PANEL)
    async def cb_admin_panel(callback: CallbackQuery, state: FSMContext) -> None:
        async def _do(user: StoredUser) -> None:
            if not user.is_admin:
                return
            assert callback.bot is not None and callback.message is not None
            await _show_admin_panel(
                bot=callback.bot,
                chat_id=callback.message.chat.id,
                state=state,
                history=history,
                status=UserStatus.PENDING.value,
                page=0,
            )

        await _wrap_cb(callback, state, _do)

    @dp.callback_query(F.data.startswith(f"{CB_ADMIN_LIST_PREFIX}:"))
    async def cb_admin_list(callback: CallbackQuery, state: FSMContext) -> None:
        async def _do(user: StoredUser) -> None:
            if not user.is_admin:
                return
            assert callback.bot is not None and callback.message is not None
            assert callback.data is not None
            parts = callback.data.split(":")
            if len(parts) != 3:
                return
            status = parts[1]
            try:
                page = int(parts[2])
            except ValueError:
                return
            await _show_admin_panel(
                bot=callback.bot,
                chat_id=callback.message.chat.id,
                state=state,
                history=history,
                status=status,
                page=page,
            )

        await _wrap_cb(callback, state, _do)

    @dp.callback_query(F.data.startswith(f"{CB_ADMIN_USER_PREFIX}:"))
    async def cb_admin_user(callback: CallbackQuery, state: FSMContext) -> None:
        async def _do(user: StoredUser) -> None:
            if not user.is_admin:
                return
            assert callback.bot is not None and callback.message is not None
            assert callback.data is not None
            try:
                target_id = int(callback.data.split(":", 1)[1])
            except (ValueError, IndexError):
                return
            await _show_admin_user_detail(
                bot=callback.bot,
                chat_id=callback.message.chat.id,
                state=state,
                history=history,
                target_user_id=target_id,
            )

        await _wrap_cb(callback, state, _do)

    @dp.callback_query(F.data.startswith(f"{CB_ADMIN_ACTION_PREFIX}:"))
    async def cb_admin_action(callback: CallbackQuery, state: FSMContext) -> None:
        async def _do(user: StoredUser) -> None:
            if not user.is_admin:
                return
            assert callback.bot is not None and callback.message is not None
            assert callback.data is not None
            parts = callback.data.split(":")
            if len(parts) != 3:
                return
            try:
                target_id = int(parts[1])
            except ValueError:
                return
            action = parts[2]
            updated = await _apply_admin_decision(
                bot=callback.bot,
                history=history,
                target_user_id=target_id,
                action=action,
                decided_by=user.id,
            )
            if updated is None:
                await callback.answer("Не удалось.", show_alert=True)
                return
            await _show_admin_user_detail(
                bot=callback.bot,
                chat_id=callback.message.chat.id,
                state=state,
                history=history,
                target_user_id=target_id,
            )

        await _wrap_cb(callback, state, _do)

    @dp.callback_query(F.data.startswith(f"{CB_ADMIN_LIMIT_PICKER_PREFIX}:"))
    async def cb_admin_limit_picker(callback: CallbackQuery, state: FSMContext) -> None:
        async def _do(user: StoredUser) -> None:
            if not user.is_admin:
                return
            assert callback.bot is not None and callback.message is not None
            assert callback.data is not None
            parts = callback.data.split(":")
            if len(parts) != 2:
                return
            try:
                target_id = int(parts[1])
            except ValueError:
                return
            target = await history.get_user(target_id)
            if target is None:
                await callback.answer("Пользователь не найден.", show_alert=True)
                return
            await _ensure_control_message(
                callback.bot,
                callback.message.chat.id,
                state,
                text=f"🔗 Лимит ссылок для <b>{html.escape(target.display_name)}</b>:",
                reply_markup=admin_limit_picker_keyboard(target_id, target.link_limit),
            )

        await _wrap_cb(callback, state, _do)

    @dp.callback_query(F.data.startswith(f"{CB_ADMIN_LIMIT_PREFIX}:"))
    async def cb_admin_limit_set(callback: CallbackQuery, state: FSMContext) -> None:
        async def _do(user: StoredUser) -> None:
            if not user.is_admin:
                return
            assert callback.bot is not None and callback.message is not None
            assert callback.data is not None
            parts = callback.data.split(":")
            if len(parts) != 3:
                return
            try:
                target_id = int(parts[1])
                limit_val = int(parts[2])
            except ValueError:
                return
            new_limit = None if limit_val == 0 else limit_val
            await history.set_user_link_limit(user_id=target_id, link_limit=new_limit)
            label = "∞" if new_limit is None else str(new_limit)
            await callback.answer(f"Лимит: {label}", show_alert=False)
            await _show_admin_user_detail(
                bot=callback.bot,
                chat_id=callback.message.chat.id,
                state=state,
                history=history,
                target_user_id=target_id,
            )

        await _wrap_cb(callback, state, _do)

    @dp.callback_query(F.data.startswith(f"{CB_ADMIN_NOTIF_PREFIX}:"))
    async def cb_admin_notif(callback: CallbackQuery) -> None:
        """Inline buttons attached to the admin's incoming notification DM."""
        if callback.from_user is None or callback.bot is None or callback.message is None:
            await callback.answer()
            return
        admin = await history.get_user(callback.from_user.id)
        if admin is None or not admin.is_admin:
            await callback.answer("Только для админов.", show_alert=True)
            return
        if callback.data is None:
            await callback.answer()
            return
        parts = callback.data.split(":")
        if len(parts) != 3:
            await callback.answer()
            return
        try:
            target_id = int(parts[1])
        except ValueError:
            await callback.answer()
            return
        action = parts[2]
        updated = await _apply_admin_decision(
            bot=callback.bot,
            history=history,
            target_user_id=target_id,
            action=action,
            decided_by=admin.id,
        )
        if updated is None:
            await callback.answer("Пользователь не найден.", show_alert=True)
            return
        await callback.answer(
            {
                "approve": "Одобрено.",
                "deny": "Отклонено.",
                "block": "Заблокировано.",
            }.get(action, "Готово.")
        )
        # Replace the notification DM with a plain status confirmation.
        text_label = (
            (updated.username and f"@{updated.username}")
            or " ".join(p for p in (updated.first_name, updated.last_name) if p)
            or f"id {updated.id}"
        )
        with contextlib.suppress(Exception):
            await callback.bot.edit_message_text(
                chat_id=callback.message.chat.id,
                message_id=callback.message.message_id,
                text=(
                    f"📨 Запрос от <b>{html.escape(text_label)}</b>\n"
                    f"Решение: <b>{_human_status(updated.status)}</b>"
                ),
                reply_markup=None,
            )

    return dp


def _chat_id_of(payload: Message | CallbackQuery) -> int | None:
    if isinstance(payload, Message):
        return payload.chat.id
    if payload.message is not None:
        return payload.message.chat.id
    return None


async def _run_health_server() -> None:
    """Minimal HTTP server so Render / ping services can reach us."""
    from aiohttp import web

    async def _health(_request: web.Request) -> web.Response:
        return web.Response(text="ok")

    app = web.Application()
    app.router.add_get("/", _health)
    app.router.add_get("/health", _health)
    port = int(os.environ.get("PORT", "10000"))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info("health-check server listening on :%s", port)


async def run_bot(settings: Settings) -> None:
    """Start polling Telegram for updates."""
    bot = Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    history = HistoryStore(settings.history_db_path)
    await history.connect()
    dp = build_dispatcher(settings, history)
    await _run_health_server()
    logger.info("starting bot polling")
    try:
        try:
            await dp.start_polling(bot)
        except TelegramUnauthorizedError:
            # Token is revoked / wrong — no point in retrying every few
            # seconds and spamming the log. Fail fast; Render will mark
            # the deploy as crashed and surface the error in the dashboard.
            logger.critical(
                "telegram bot token is unauthorized — refusing to continue. "
                "Set TELEGRAM_BOT_TOKEN in env and redeploy."
            )
            raise
    finally:
        with contextlib.suppress(Exception):
            await history.close()
        await bot.session.close()


def main() -> None:
    """CLI entrypoint."""
    from .config import load_settings

    settings = load_settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    asyncio.run(run_bot(settings))


if __name__ == "__main__":
    main()
