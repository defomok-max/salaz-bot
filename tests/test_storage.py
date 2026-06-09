"""Tests for the SQLite-backed history & user store."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pytest

from instagram_dork_bot.bot import _bootstrap_user
from instagram_dork_bot.config import Settings
from instagram_dork_bot.extractors import Contacts
from instagram_dork_bot.pipeline import Listing, PipelineStats
from instagram_dork_bot.storage import HistoryStore, UserStatus


@dataclass
class _FakeTgUser:
    id: int
    username: str | None = None
    first_name: str | None = None
    last_name: str | None = None


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "telegram_bot_token": "fake-token",
        "serper_api_key": "fake-key",
    }
    base.update(overrides)
    return cast(Settings, Settings(**base))  # type: ignore[arg-type]


def _listing(link: str, *, emails: tuple[str, ...] = ("shop@brand.io",)) -> Listing:
    return Listing(
        title="Brand Shop",
        link=link,
        snippet="snippet",
        contacts=Contacts(
            emails=emails,
            whatsapp_numbers=("+15551234567",),
            phones=("+447700900123",),
        ),
    )


@pytest.mark.asyncio
async def test_save_and_get_search(tmp_path: Path) -> None:
    db_path = tmp_path / "history.db"
    async with HistoryStore(db_path) as store:
        listings = [_listing("https://www.instagram.com/p/abc")]
        stats = PipelineStats(queries_run=2, raw_results=10, accepted=1)
        search_id = await store.save_search(
            user_id=42,
            keyword="iphone",
            listings=listings,
            stats=stats,
        )
        record = await store.get_search(user_id=42, search_id=search_id)
        assert record is not None
        search, stored = record
        assert search.keyword == "iphone"
        assert search.accepted == 1
        assert len(stored) == 1
        assert stored[0].link == "https://www.instagram.com/p/abc"
        assert stored[0].contacts.emails == ("shop@brand.io",)
        assert stored[0].contacts.whatsapp_numbers == ("+15551234567",)
        assert stored[0].contacts.phones == ("+447700900123",)


@pytest.mark.asyncio
async def test_list_searches_pagination_and_isolation(tmp_path: Path) -> None:
    db_path = tmp_path / "history.db"
    async with HistoryStore(db_path) as store:
        for i in range(3):
            await store.save_search(
                user_id=1,
                keyword=f"kw{i}",
                listings=[_listing(f"https://www.instagram.com/p/{i}")],
                stats=PipelineStats(queries_run=1, raw_results=1, accepted=1),
            )
        await store.save_search(
            user_id=2,
            keyword="other",
            listings=[],
            stats=PipelineStats(queries_run=0, raw_results=0, accepted=0),
        )

        assert await store.count_searches(user_id=1) == 3
        assert await store.count_searches(user_id=2) == 1

        page0 = await store.list_searches(user_id=1, limit=2, offset=0)
        page1 = await store.list_searches(user_id=1, limit=2, offset=2)
        assert [s.keyword for s in page0] == ["kw2", "kw1"]
        assert [s.keyword for s in page1] == ["kw0"]


@pytest.mark.asyncio
async def test_latest_keyword_returns_most_recent(tmp_path: Path) -> None:
    """Used by the main-menu 'repeat last search' shortcut."""
    db_path = tmp_path / "history.db"
    async with HistoryStore(db_path) as store:
        await store.save_search(
            user_id=1,
            keyword="iphone 14",
            listings=[_listing("https://www.instagram.com/p/abc")],
            stats=PipelineStats(queries_run=2, raw_results=4, accepted=1),
        )
        await store.save_search(
            user_id=1,
            keyword="macbook air",
            listings=[],
            stats=PipelineStats(queries_run=1, raw_results=0, accepted=0),
        )
        await store.save_search(
            user_id=1,
            keyword="python telegram",
            listings=[],
            stats=PipelineStats(queries_run=1, raw_results=0, accepted=0),
            source="freelance",
        )

        assert await store.latest_keyword(user_id=1, source="instagram") == "macbook air"
        assert await store.latest_keyword(user_id=1, source="freelance") == "python telegram"
        assert await store.latest_keyword(user_id=1) == "python telegram"

        assert await store.latest_keyword(user_id=999) is None
        assert await store.latest_keyword(user_id=999, source="instagram") is None


@pytest.mark.asyncio
async def test_get_search_other_user_returns_none(tmp_path: Path) -> None:
    db_path = tmp_path / "history.db"
    async with HistoryStore(db_path) as store:
        sid = await store.save_search(
            user_id=1,
            keyword="kw",
            listings=[_listing("https://www.instagram.com/p/x")],
            stats=PipelineStats(queries_run=1, raw_results=1, accepted=1),
        )
        # Different user must not be able to read it.
        assert await store.get_search(user_id=2, search_id=sid) is None


@pytest.mark.asyncio
async def test_save_search_with_no_listings(tmp_path: Path) -> None:
    db_path = tmp_path / "history.db"
    async with HistoryStore(db_path) as store:
        sid = await store.save_search(
            user_id=99,
            keyword="empty",
            listings=[],
            stats=PipelineStats(queries_run=3, raw_results=20, accepted=0),
        )
        record = await store.get_search(user_id=99, search_id=sid)
        assert record is not None
        search, stored = record
        assert search.accepted == 0
        assert stored == []


# --- user / admin lifecycle -------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_user_request_creates_pending_user(tmp_path: Path) -> None:
    db_path = tmp_path / "history.db"
    async with HistoryStore(db_path) as store:
        user = await store.upsert_user_request(
            user_id=42,
            username="johndoe",
            first_name="John",
            last_name="Doe",
        )
        assert user.id == 42
        assert user.status == UserStatus.PENDING
        assert user.is_admin is False
        assert user.username == "johndoe"
        assert user.requested_at is not None


@pytest.mark.asyncio
async def test_upsert_user_request_refreshes_profile(tmp_path: Path) -> None:
    db_path = tmp_path / "history.db"
    async with HistoryStore(db_path) as store:
        await store.upsert_user_request(
            user_id=42,
            username="oldname",
            first_name=None,
            last_name=None,
        )
        await store.set_user_status(user_id=42, status=UserStatus.APPROVED, decided_by=1)
        # Subsequent /start with new username must NOT reset the status.
        refreshed = await store.upsert_user_request(
            user_id=42,
            username="newname",
            first_name="John",
            last_name=None,
        )
        assert refreshed.username == "newname"
        assert refreshed.status == UserStatus.APPROVED


@pytest.mark.asyncio
async def test_set_user_status_transitions(tmp_path: Path) -> None:
    db_path = tmp_path / "history.db"
    async with HistoryStore(db_path) as store:
        await store.upsert_user_request(user_id=42, username="u", first_name=None, last_name=None)
        approved = await store.set_user_status(user_id=42, status=UserStatus.APPROVED, decided_by=1)
        assert approved is not None
        assert approved.status == UserStatus.APPROVED
        assert approved.decided_by == 1
        assert approved.decided_at is not None

        blocked = await store.set_user_status(user_id=42, status=UserStatus.BLOCKED, decided_by=1)
        assert blocked is not None
        assert blocked.status == UserStatus.BLOCKED


@pytest.mark.asyncio
async def test_set_user_admin_promotes_and_approves(tmp_path: Path) -> None:
    db_path = tmp_path / "history.db"
    async with HistoryStore(db_path) as store:
        await store.upsert_user_request(user_id=7, username="boss", first_name=None, last_name=None)
        promoted = await store.set_user_admin(user_id=7, is_admin=True)
        assert promoted is not None
        assert promoted.is_admin is True
        # Admins are implicitly approved.
        assert promoted.status == UserStatus.APPROVED
        assert await store.has_any_admin() is True


@pytest.mark.asyncio
async def test_list_admins_returns_admin_users(tmp_path: Path) -> None:
    db_path = tmp_path / "history.db"
    async with HistoryStore(db_path) as store:
        for uid, name in ((1, "alice"), (2, "bob"), (3, "carol")):
            await store.upsert_user_request(
                user_id=uid, username=name, first_name=None, last_name=None
            )
        await store.set_user_admin(user_id=2, is_admin=True)
        admins = await store.list_admins()
        assert [a.id for a in admins] == [2]
        await store.set_user_admin(user_id=3, is_admin=True)
        admins = await store.list_admins()
        assert [a.id for a in admins] == [2, 3]


@pytest.mark.asyncio
async def test_has_any_admin_false_initially(tmp_path: Path) -> None:
    db_path = tmp_path / "history.db"
    async with HistoryStore(db_path) as store:
        assert await store.has_any_admin() is False


@pytest.mark.asyncio
async def test_list_users_filtered_by_status(tmp_path: Path) -> None:
    db_path = tmp_path / "history.db"
    async with HistoryStore(db_path) as store:
        for uid in (1, 2, 3, 4):
            await store.upsert_user_request(
                user_id=uid, username=f"u{uid}", first_name=None, last_name=None
            )
        await store.set_user_status(user_id=2, status=UserStatus.APPROVED, decided_by=1)
        await store.set_user_status(user_id=3, status=UserStatus.BLOCKED, decided_by=1)

        pending = await store.list_users(status=UserStatus.PENDING, limit=20)
        approved = await store.list_users(status=UserStatus.APPROVED, limit=20)
        blocked = await store.list_users(status=UserStatus.BLOCKED, limit=20)

        assert {u.id for u in pending} == {1, 4}
        assert {u.id for u in approved} == {2}
        assert {u.id for u in blocked} == {3}

        assert await store.count_users(status=UserStatus.PENDING) == 2
        assert await store.count_users() == 4


@pytest.mark.asyncio
async def test_bootstrap_user_promotes_username_admin(tmp_path: Path) -> None:
    """First /start from a username in admin_usernames must auto-promote.

    This is the integration smoke test that catches kwarg/positional
    mismatches between bot.py and storage.py: any wiring break here
    bubbles up as a TypeError instead of being silently logged.
    """
    db_path = tmp_path / "history.db"
    settings = _settings(admin_usernames="andryshayoyo")
    async with HistoryStore(db_path) as store:
        tg_user = _FakeTgUser(id=42, username="andryshayoyo", first_name="A")
        record = await _bootstrap_user(
            bot=cast("object", None),  # type: ignore[arg-type]
            history=store,
            settings=settings,
            tg_user=tg_user,
        )
        assert record.id == 42
        assert record.is_admin is True
        assert record.status == UserStatus.APPROVED


@pytest.mark.asyncio
async def test_bootstrap_user_no_admin_no_env_does_not_promote(tmp_path: Path) -> None:
    """If no admin exists in the DB AND ADMIN_USER_IDS is empty, refuse to
    auto-promote. This is the security fix for persistent-disk-less plans
    (e.g. Render Free) where the DB is wiped on every redeploy — auto-promote
    would otherwise turn the bot into an open relay on every restart.
    """
    db_path = tmp_path / "history.db"
    settings = _settings()  # empty admin_user_ids / admin_usernames
    async with HistoryStore(db_path) as store:
        record = await _bootstrap_user(
            bot=cast("object", None),  # type: ignore[arg-type]
            history=store,
            settings=settings,
            tg_user=_FakeTgUser(id=1, username="alice"),
        )
        assert record.is_admin is False
        assert record.status == UserStatus.PENDING

        # Subsequent users without privileges also stay pending.
        second = await _bootstrap_user(
            bot=cast("object", None),  # type: ignore[arg-type]
            history=store,
            settings=settings,
            tg_user=_FakeTgUser(id=2, username="bob"),
        )
        assert second.is_admin is False
        assert second.status == UserStatus.PENDING


@pytest.mark.asyncio
async def test_bootstrap_user_env_admin_takes_precedence_over_empty_db(
    tmp_path: Path,
) -> None:
    """If ADMIN_USER_IDS is configured, that user is promoted even on an
    otherwise empty DB. The previous auto-promote branch is gone; only
    explicit env config can create the first admin.
    """
    db_path = tmp_path / "history.db"
    settings = _settings(admin_user_ids="42")
    async with HistoryStore(db_path) as store:
        record = await _bootstrap_user(
            bot=cast("object", None),  # type: ignore[arg-type]
            history=store,
            settings=settings,
            tg_user=_FakeTgUser(id=42, username="owner"),
        )
        assert record.is_admin is True
        assert record.status == UserStatus.APPROVED

        # Strangers still get nothing even though there is now an admin.
        stranger = await _bootstrap_user(
            bot=cast("object", None),  # type: ignore[arg-type]
            history=store,
            settings=settings,
            tg_user=_FakeTgUser(id=99, username="stranger"),
        )
        assert stranger.is_admin is False
        assert stranger.status == UserStatus.PENDING


@pytest.mark.asyncio
async def test_update_user_prefs_round_trip(tmp_path: Path) -> None:
    db_path = tmp_path / "history.db"
    async with HistoryStore(db_path) as store:
        await store.upsert_user_request(user_id=42, username="u", first_name=None, last_name=None)
        updated = await store.update_user_prefs(
            user_id=42,
            countries=("US", "AE"),
            max_results=25,
        )
        assert updated is not None
        assert updated.pref_countries == ("US", "AE")
        assert updated.pref_max_results == 25

        cleared = await store.update_user_prefs(user_id=42, clear_countries=True)
        assert cleared is not None
        assert cleared.pref_countries is None
        assert cleared.pref_max_results == 25  # left as-is


@pytest.mark.asyncio
async def test_migrations_are_idempotent(tmp_path: Path) -> None:
    """Running the migration loop twice must not raise (duplicate columns
    are expected on the second pass). This guards against the previous
    ``except Exception: pass`` blanket-swallower that masked real bugs.
    """
    db_path = tmp_path / "history.db"
    async with HistoryStore(db_path) as store:
        await store.connect()
    # Second open() runs migrations on an already-migrated schema.
    async with HistoryStore(db_path) as store2:
        await store2.connect()
    assert db_path.exists()


@pytest.mark.asyncio
async def test_migrations_raise_on_real_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-duplicate-column OperationalError must propagate, not be
    silently swallowed. This pins the new behaviour in place.
    """
    from instagram_dork_bot import storage as storage_mod

    db_path = tmp_path / "history.db"
    async with HistoryStore(db_path) as store:
        await store.connect()
        # Force the next migration to fail with a non-idempotent error.
        monkeypatch.setattr(
            storage_mod,
            "_MIGRATIONS",
            ["ALTER TABLE nonexistent_table ADD COLUMN foo INTEGER"],
        )
        with pytest.raises(Exception):
            await store._run_migrations()
