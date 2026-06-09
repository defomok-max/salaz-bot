"""SQLite-backed persistence for users, preferences, and search history.

Tables:

* ``users`` — one row per Telegram user (status, admin flag, prefs, audit).
* ``searches`` — one row per executed search (per user).
* ``listings`` — one row per accepted result, foreign-keyed to a search.

Contact fields are stored as comma-joined strings (cheap and good enough for
the Telegram UI, which renders them straight back to text).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

import aiosqlite

from .extractors import Contacts
from .pipeline import Listing, PipelineStats

logger = logging.getLogger(__name__)


class UserStatus(StrEnum):
    """Lifecycle state of a Telegram user with respect to bot access."""

    PENDING = "pending"
    APPROVED = "approved"
    BLOCKED = "blocked"
    DENIED = "denied"

    @property
    def label_ru(self) -> str:
        return {
            UserStatus.PENDING: "ожидает",
            UserStatus.APPROVED: "одобрен",
            UserStatus.BLOCKED: "заблокирован",
            UserStatus.DENIED: "отклонён",
        }[self]


@dataclass(frozen=True)
class StoredUser:
    """A Telegram user as known to the bot."""

    id: int
    status: UserStatus
    is_admin: bool
    username: str | None
    first_name: str | None
    last_name: str | None
    requested_at: datetime | None
    decided_at: datetime | None
    decided_by: int | None
    pref_countries: tuple[str, ...] | None
    pref_max_results: int | None
    link_limit: int | None
    pref_platforms: tuple[str, ...] | None = None

    @property
    def display_name(self) -> str:
        for candidate in (self.username and f"@{self.username}", self.first_name, str(self.id)):
            if candidate:
                return candidate
        return str(self.id)


@dataclass(frozen=True)
class StoredSearch:
    """A single past search as listed on the history screen."""

    id: int
    user_id: int
    keyword: str
    created_at: datetime
    queries_run: int
    raw_results: int
    accepted: int
    source: str = "instagram"


@dataclass(frozen=True)
class StoredFreelanceListing:
    """A past freelance listing with platform-specific metadata."""

    title: str
    link: str
    snippet: str
    contacts: Contacts
    platform: str
    budget: str
    listing_type: str


@dataclass(frozen=True)
class StoredListing:
    """A single past listing pulled out of the history."""

    title: str
    link: str
    snippet: str
    contacts: Contacts


@dataclass(frozen=True)
class UserPrefs:
    """Per-user preferences for a search."""

    countries: tuple[str, ...] | None = None
    max_results: int | None = None
    extras: dict[str, str] = field(default_factory=dict)


class HistoryStore:
    """Async wrapper around an aiosqlite connection."""

    def __init__(self, db_path: Path | str) -> None:
        self._db_path = str(db_path)
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        if self._conn is not None:
            return
        parent = Path(self._db_path).resolve().parent
        parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self._db_path)
        await self._conn.execute("PRAGMA foreign_keys = ON")
        await self._conn.executescript(_SCHEMA)
        await self._conn.commit()
        await self._run_migrations()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def __aenter__(self) -> HistoryStore:
        await self.connect()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    @property
    def _required_conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("HistoryStore is not connected; call .connect() first.")
        return self._conn

    async def _run_migrations(self) -> None:
        """Apply schema migrations idempotently.

        Each ``ALTER TABLE ADD COLUMN`` raises ``OperationalError`` on
        subsequent runs once the column already exists; we treat that as
        the no-op signal and re-raise anything else (typos, missing
        tables, syntax errors) so a broken migration doesn't get masked.
        """
        conn = self._required_conn
        for stmt in _MIGRATIONS:
            try:
                await conn.execute(stmt)
                await conn.commit()
            except aiosqlite.OperationalError as exc:
                msg = str(exc).lower()
                if "duplicate column" in msg or "already exists" in msg:
                    continue
                logger.exception("migration failed: %s", stmt)
                raise
            except Exception:
                logger.exception("migration failed: %s", stmt)
                raise

    # ----- users -----------------------------------------------------------

    async def get_user(self, user_id: int) -> StoredUser | None:
        conn = self._required_conn
        async with conn.execute(_SELECT_USER + " WHERE id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
        return _row_to_user(row) if row else None

    async def has_any_admin(self) -> bool:
        conn = self._required_conn
        async with conn.execute("SELECT 1 FROM users WHERE is_admin = 1 LIMIT 1") as cur:
            return (await cur.fetchone()) is not None

    async def upsert_user_request(
        self,
        *,
        user_id: int,
        username: str | None,
        first_name: str | None,
        last_name: str | None,
    ) -> StoredUser:
        """Create the user as ``pending`` if unknown, otherwise refresh the profile.

        Existing users keep their status; this is a no-op if they're already
        approved or blocked.
        """
        conn = self._required_conn
        existing = await self.get_user(user_id)
        now = datetime.now(UTC).isoformat()
        if existing is None:
            await conn.execute(
                "INSERT INTO users "
                "(id, status, is_admin, username, first_name, last_name, requested_at) "
                "VALUES (?, ?, 0, ?, ?, ?, ?)",
                (user_id, UserStatus.PENDING.value, username, first_name, last_name, now),
            )
            await conn.commit()
        else:
            await conn.execute(
                "UPDATE users SET username = ?, first_name = ?, last_name = ? WHERE id = ?",
                (username, first_name, last_name, user_id),
            )
            await conn.commit()
        record = await self.get_user(user_id)
        assert record is not None
        return record

    async def set_user_status(
        self,
        *,
        user_id: int,
        status: UserStatus,
        decided_by: int,
    ) -> StoredUser | None:
        conn = self._required_conn
        now = datetime.now(UTC).isoformat()
        await conn.execute(
            "UPDATE users SET status = ?, decided_at = ?, decided_by = ? WHERE id = ?",
            (status.value, now, decided_by, user_id),
        )
        await conn.commit()
        return await self.get_user(user_id)

    async def set_user_admin(self, *, user_id: int, is_admin: bool) -> StoredUser | None:
        conn = self._required_conn
        await conn.execute(
            "UPDATE users SET is_admin = ?, status = ? WHERE id = ?",
            (
                1 if is_admin else 0,
                UserStatus.APPROVED.value if is_admin else UserStatus.PENDING.value,
                user_id,
            ),
        )
        await conn.commit()
        return await self.get_user(user_id)

    async def list_users(
        self,
        *,
        status: UserStatus | None = None,
        limit: int,
        offset: int = 0,
    ) -> list[StoredUser]:
        conn = self._required_conn
        if status is None:
            query = _SELECT_USER + " ORDER BY requested_at DESC, id DESC LIMIT ? OFFSET ?"
            params: tuple[object, ...] = (limit, offset)
        else:
            query = (
                _SELECT_USER
                + " WHERE status = ? ORDER BY requested_at DESC, id DESC LIMIT ? OFFSET ?"
            )
            params = (status.value, limit, offset)
        async with conn.execute(query, params) as cur:
            rows = await cur.fetchall()
        return [_row_to_user(r) for r in rows]

    async def count_users(self, *, status: UserStatus | None = None) -> int:
        conn = self._required_conn
        if status is None:
            sql = "SELECT COUNT(1) FROM users"
            params: tuple[object, ...] = ()
        else:
            sql = "SELECT COUNT(1) FROM users WHERE status = ?"
            params = (status.value,)
        async with conn.execute(sql, params) as cur:
            row = await cur.fetchone()
        return int(row[0]) if row else 0

    async def list_admins(self) -> list[StoredUser]:
        """Return every user with the admin flag set."""
        conn = self._required_conn
        async with conn.execute(_SELECT_USER + " WHERE is_admin = 1 ORDER BY id ASC") as cur:
            rows = await cur.fetchall()
        return [_row_to_user(r) for r in rows]

    async def set_user_link_limit(
        self, *, user_id: int, link_limit: int | None
    ) -> StoredUser | None:
        conn = self._required_conn
        await conn.execute(
            "UPDATE users SET link_limit = ? WHERE id = ?",
            (link_limit, user_id),
        )
        await conn.commit()
        return await self.get_user(user_id)

    async def count_user_total_listings(self, *, user_id: int) -> int:
        conn = self._required_conn
        async with conn.execute(
            "SELECT COALESCE(SUM(accepted), 0) FROM searches WHERE user_id = ?",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
        return int(row[0]) if row else 0

    async def update_user_prefs(
        self,
        *,
        user_id: int,
        countries: tuple[str, ...] | None = None,
        max_results: int | None = None,
        platforms: tuple[str, ...] | None = None,
        clear_countries: bool = False,
        clear_max_results: bool = False,
        clear_platforms: bool = False,
    ) -> StoredUser | None:
        """Update preference columns.

        Pass ``clear_*=True`` to reset a column to its default. If a value
        is left ``None`` *and* the corresponding ``clear_*`` flag is also
        ``False``, the column is left as-is.
        """
        conn = self._required_conn
        sets: list[str] = []
        params: list[object] = []
        if clear_countries:
            sets.append("pref_countries = NULL")
        elif countries is not None:
            sets.append("pref_countries = ?")
            params.append(",".join(countries))
        if clear_max_results:
            sets.append("pref_max_results = NULL")
        elif max_results is not None:
            sets.append("pref_max_results = ?")
            params.append(int(max_results))
        if clear_platforms:
            sets.append("pref_platforms = NULL")
        elif platforms is not None:
            sets.append("pref_platforms = ?")
            params.append(",".join(platforms))
        if not sets:
            return await self.get_user(user_id)
        params.append(user_id)
        await conn.execute(
            f"UPDATE users SET {', '.join(sets)} WHERE id = ?",
            params,
        )
        await conn.commit()
        return await self.get_user(user_id)

    # ----- searches --------------------------------------------------------

    async def save_search(
        self,
        *,
        user_id: int,
        keyword: str,
        listings: Iterable[Listing],
        stats: PipelineStats,
        source: str = "instagram",
        freelance_listing_factory: Callable[[Listing], dict[str, object]] | None = None,
    ) -> int:
        """Persist a completed search and its listings. Returns the search_id.

        ``source`` distinguishes Instagram from freelance-platform searches
        in the history view. ``freelance_listing_factory`` (if provided) is
        called for each listing to produce extra columns for the
        ``freelance_listings`` table — Instagram searches leave it ``None``.
        """
        conn = self._required_conn
        now = datetime.now(UTC).isoformat()

        # Wrap the whole write in a single transaction so a failure in
        # the middle leaves no orphan rows.
        await conn.execute("BEGIN IMMEDIATE")
        try:
            cursor = await conn.execute(
                "INSERT INTO searches "
                "(user_id, keyword, created_at, queries_run, raw_results, "
                "accepted, source) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    user_id,
                    keyword,
                    now,
                    stats.queries_run,
                    stats.raw_results,
                    stats.accepted,
                    source,
                ),
            )
            search_id = cursor.lastrowid
            if search_id is None:
                raise RuntimeError("failed to insert search row")

            listing_rows = [
                (
                    search_id,
                    pos,
                    listing.title,
                    listing.link,
                    listing.snippet,
                    ",".join(listing.contacts.emails),
                    ",".join(listing.contacts.whatsapp_numbers),
                    ",".join(listing.contacts.phones),
                    ",".join(listing.contacts.telegram_handles),
                )
                for pos, listing in enumerate(listings)
            ]
            if listing_rows:
                await conn.executemany(
                    "INSERT INTO listings "
                    "(search_id, position, title, link, snippet, "
                    "emails, whatsapp_numbers, phones, telegram_handles) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    listing_rows,
                )

            rejected_json = json.dumps(stats.rejected_reasons, ensure_ascii=False, sort_keys=True)
            await conn.execute(
                "UPDATE searches SET rejected_reasons = ? WHERE id = ?",
                (rejected_json, search_id),
            )

            if source == "freelance" and freelance_listing_factory is not None:
                await self._save_freelance_listings(
                    search_id=search_id, listings=listings, factory=freelance_listing_factory
                )

            await conn.commit()
        except Exception:
            await conn.rollback()
            raise
        return int(search_id)

    async def _save_freelance_listings(
        self,
        *,
        search_id: int,
        listings: Iterable[Listing],
        factory: Callable[[Listing], dict[str, object]],
    ) -> None:
        """Insert per-listing platform-specific metadata for freelance searches."""
        conn = self._required_conn
        rows: list[tuple[object, ...]] = []
        for pos, listing in enumerate(listings):
            meta = factory(listing)
            rows.append(
                (
                    search_id,
                    pos,
                    str(meta.get("platform", "")),
                    str(meta.get("budget", "")),
                    str(meta.get("listing_type", "")),
                )
            )
        if rows:
            await conn.executemany(
                "INSERT INTO freelance_listings "
                "(search_id, position, platform, budget, listing_type) "
                "VALUES (?, ?, ?, ?, ?)",
                rows,
            )

    async def list_searches(
        self,
        *,
        user_id: int,
        limit: int,
        offset: int = 0,
        source: str | None = None,
    ) -> list[StoredSearch]:
        conn = self._required_conn
        if source is None:
            async with conn.execute(
                "SELECT id, user_id, keyword, created_at, queries_run, raw_results, "
                "accepted, source "
                "FROM searches WHERE user_id = ? "
                "ORDER BY id DESC LIMIT ? OFFSET ?",
                (user_id, limit, offset),
            ) as cur:
                rows = await cur.fetchall()
        else:
            async with conn.execute(
                "SELECT id, user_id, keyword, created_at, queries_run, raw_results, "
                "accepted, source "
                "FROM searches WHERE user_id = ? AND source = ? "
                "ORDER BY id DESC LIMIT ? OFFSET ?",
                (user_id, source, limit, offset),
            ) as cur:
                rows = await cur.fetchall()
        return [_row_to_search(r) for r in rows]

    async def latest_keyword(
        self,
        *,
        user_id: int,
        source: str | None = None,
    ) -> str | None:
        """Return the most recent keyword for a user (optionally per-source).

        Used by the main-menu "repeat last search" shortcut. Returns
        ``None`` when the user has no history.
        """
        conn = self._required_conn
        if source is None:
            async with conn.execute(
                "SELECT keyword FROM searches WHERE user_id = ? ORDER BY id DESC LIMIT 1",
                (user_id,),
            ) as cur:
                row = await cur.fetchone()
        else:
            async with conn.execute(
                "SELECT keyword FROM searches WHERE user_id = ? AND source = ? "
                "ORDER BY id DESC LIMIT 1",
                (user_id, source),
            ) as cur:
                row = await cur.fetchone()
        if row is None:
            return None
        return str(row[0]) if row[0] is not None else None

    async def count_searches(
        self,
        *,
        user_id: int,
        source: str | None = None,
    ) -> int:
        conn = self._required_conn
        if source is None:
            async with conn.execute(
                "SELECT COUNT(1) FROM searches WHERE user_id = ?", (user_id,)
            ) as cur:
                row = await cur.fetchone()
        else:
            async with conn.execute(
                "SELECT COUNT(1) FROM searches WHERE user_id = ? AND source = ?",
                (user_id, source),
            ) as cur:
                row = await cur.fetchone()
        return int(row[0]) if row else 0

    async def get_search(
        self, *, user_id: int, search_id: int
    ) -> tuple[StoredSearch, list[StoredListing]] | None:
        conn = self._required_conn
        async with conn.execute(
            "SELECT id, user_id, keyword, created_at, queries_run, raw_results, "
            "accepted, source "
            "FROM searches WHERE id = ? AND user_id = ?",
            (search_id, user_id),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        search = _row_to_search(row)
        async with conn.execute(
            "SELECT title, link, snippet, emails, whatsapp_numbers, phones "
            "FROM listings WHERE search_id = ? ORDER BY position ASC",
            (search_id,),
        ) as cur:
            listing_rows = await cur.fetchall()
        listings = [_row_to_listing(lr) for lr in listing_rows]
        return search, listings

    async def get_freelance_search(
        self, *, user_id: int, search_id: int
    ) -> tuple[StoredSearch, list[StoredFreelanceListing]] | None:
        """Like :meth:`get_search` but joins ``freelance_listings`` for extra fields."""
        conn = self._required_conn
        async with conn.execute(
            "SELECT id, user_id, keyword, created_at, queries_run, raw_results, "
            "accepted, source "
            "FROM searches WHERE id = ? AND user_id = ?",
            (search_id, user_id),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        search = _row_to_search(row)
        async with conn.execute(
            "SELECT l.title, l.link, l.snippet, l.emails, l.whatsapp_numbers, "
            "l.phones, f.platform, f.budget, f.listing_type "
            "FROM listings l LEFT JOIN freelance_listings f "
            "ON f.search_id = l.search_id AND f.position = l.position "
            "WHERE l.search_id = ? ORDER BY l.position ASC",
            (search_id,),
        ) as cur:
            listing_rows = await cur.fetchall()
        listings = [_row_to_freelance_listing(r) for r in listing_rows]
        return search, listings


def _row_to_user(row: tuple[object, ...] | None) -> StoredUser:
    assert row is not None
    countries_raw = row[8]
    countries: tuple[str, ...] | None
    if countries_raw is None:
        countries = None
    else:
        countries = tuple(part for part in str(countries_raw).split(",") if part)
    platforms: tuple[str, ...] | None = None
    if len(row) > 12 and row[12]:
        platforms = tuple(part.strip() for part in str(row[12]).split(",") if part.strip())
    return StoredUser(
        id=int(row[0]),  # type: ignore[arg-type]
        status=UserStatus(str(row[1])),
        is_admin=bool(int(row[2])),  # type: ignore[arg-type]
        username=_opt_str(row[3]),
        first_name=_opt_str(row[4]),
        last_name=_opt_str(row[5]),
        requested_at=_opt_dt(row[6]),
        decided_at=_opt_dt(row[7]),
        pref_countries=countries,
        pref_max_results=_opt_int(row[9]),
        decided_by=_opt_int(row[10]),
        link_limit=_opt_int(row[11]) if len(row) > 11 else None,
        pref_platforms=platforms,
    )


def _opt_str(value: object) -> str | None:
    if value is None or value == "":
        return None
    return str(value)


def _opt_int(value: object) -> int | None:
    if value is None:
        return None
    return int(value)  # type: ignore[arg-type]


def _opt_dt(value: object) -> datetime | None:
    if not value:
        return None
    return _parse_dt(str(value))


def _row_to_search(row: tuple[object, ...]) -> StoredSearch:
    return StoredSearch(
        id=int(row[0]),  # type: ignore[arg-type]
        user_id=int(row[1]),  # type: ignore[arg-type]
        keyword=str(row[2]),
        created_at=_parse_dt(str(row[3])),
        queries_run=int(row[4]),  # type: ignore[arg-type]
        raw_results=int(row[5]),  # type: ignore[arg-type]
        accepted=int(row[6]),  # type: ignore[arg-type]
        source=str(row[7]) if len(row) > 7 and row[7] is not None else "instagram",
    )


def _row_to_listing(row: tuple[object, ...]) -> StoredListing:
    contacts = Contacts(
        emails=tuple(_split_csv(str(row[3]))),
        whatsapp_numbers=tuple(_split_csv(str(row[4]))),
        phones=tuple(_split_csv(str(row[5]))),
        telegram_handles=tuple(_split_csv(str(row[6]))) if len(row) > 6 else (),
    )
    return StoredListing(
        title=str(row[0]),
        link=str(row[1]),
        snippet=str(row[2]),
        contacts=contacts,
    )


def _row_to_freelance_listing(row: tuple[object, ...]) -> StoredFreelanceListing:
    contacts = Contacts(
        emails=tuple(_split_csv(str(row[3]))),
        whatsapp_numbers=tuple(_split_csv(str(row[4]))),
        phones=tuple(_split_csv(str(row[5]))),
        telegram_handles=tuple(_split_csv(str(row[6]))) if len(row) > 6 else (),
    )
    return StoredFreelanceListing(
        title=str(row[0]),
        link=str(row[1]),
        snippet=str(row[2]),
        contacts=contacts,
        platform=str(row[7]) if len(row) > 7 else "",
        budget=str(row[8]) if len(row) > 8 else "",
        listing_type=str(row[9]) if len(row) > 9 else "",
    )


def _split_csv(value: str) -> list[str]:
    if not value:
        return []
    return [p for p in value.split(",") if p]


def _parse_dt(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return datetime.now(UTC)


_SELECT_USER = (
    "SELECT id, status, is_admin, username, first_name, last_name, "
    "requested_at, decided_at, pref_countries, pref_max_results, decided_by, "
    "link_limit, pref_platforms "
    "FROM users"
)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'pending',
    is_admin INTEGER NOT NULL DEFAULT 0,
    username TEXT,
    first_name TEXT,
    last_name TEXT,
    requested_at TEXT,
    decided_at TEXT,
    decided_by INTEGER,
    pref_countries TEXT,
    pref_max_results INTEGER,
    link_limit INTEGER,
    pref_platforms TEXT
);

CREATE INDEX IF NOT EXISTS idx_users_status ON users(status, requested_at DESC);

CREATE TABLE IF NOT EXISTS searches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    keyword TEXT NOT NULL,
    created_at TEXT NOT NULL,
    queries_run INTEGER NOT NULL DEFAULT 0,
    raw_results INTEGER NOT NULL DEFAULT 0,
    accepted INTEGER NOT NULL DEFAULT 0,
    rejected_reasons TEXT NOT NULL DEFAULT '{}',
    source TEXT NOT NULL DEFAULT 'instagram'
);

CREATE INDEX IF NOT EXISTS idx_searches_user ON searches(user_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_searches_user_source ON searches(user_id, source, id DESC);

CREATE TABLE IF NOT EXISTS listings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    search_id INTEGER NOT NULL REFERENCES searches(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    link TEXT NOT NULL,
    snippet TEXT NOT NULL DEFAULT '',
    emails TEXT NOT NULL DEFAULT '',
    whatsapp_numbers TEXT NOT NULL DEFAULT '',
    phones TEXT NOT NULL DEFAULT '',
    telegram_handles TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_listings_search ON listings(search_id, position);

CREATE TABLE IF NOT EXISTS freelance_listings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    search_id INTEGER NOT NULL REFERENCES searches(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    platform TEXT NOT NULL DEFAULT '',
    budget TEXT NOT NULL DEFAULT '',
    listing_type TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_freelance_listings_search
    ON freelance_listings(search_id, position);
"""

_MIGRATIONS = [
    "ALTER TABLE users ADD COLUMN link_limit INTEGER",
    "ALTER TABLE users ADD COLUMN pref_platforms TEXT",
    "ALTER TABLE searches ADD COLUMN source TEXT NOT NULL DEFAULT 'instagram'",
    "ALTER TABLE listings ADD COLUMN telegram_handles TEXT NOT NULL DEFAULT ''",
]
