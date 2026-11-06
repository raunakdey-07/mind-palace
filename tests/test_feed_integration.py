from __future__ import annotations

import os
from collections.abc import Generator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from api.services.memory_feed import feed
from api.services.memory_public import MemoryError
from scripts import m009_release_validation as validation

pytestmark = pytest.mark.asyncio

TRAVERSAL_CORPUS = "m009-integration-traversal"
DURABLE_CORPUS = "m009-integration-durable"
CURSOR_CORPUS = "m009-integration-cursor"
OTHER_CORPUS = "m009-integration-other"
EMPTY_CORPUS = "m009-integration-empty"
BOUNDARY_CORPUS = "m009-integration-boundary"
CONCURRENT_CORPUS = "m009-integration-concurrent"
CONCURRENT_OTHER_CORPUS = "m009-integration-concurrent-other"
BASE_TIME = datetime(2026, 1, 1, tzinfo=UTC)
CONCURRENT_BASE_TIME = datetime(2026, 2, 1, tzinfo=UTC)
CURSOR_SECRET = "m009-feed-integration-" + "x" * 48


def _postgres_is_available(url: str) -> bool:
    engine = None
    try:
        engine = sa.create_engine(
            validation.sync_url(url),
            poolclass=NullPool,
            connect_args={"connect_timeout": 2},
        )
        with engine.connect() as connection:
            connection.execute(sa.text("SELECT 1"))
    except (SQLAlchemyError, OSError, TimeoutError):
        return False
    finally:
        if engine is not None:
            engine.dispose()
    return True


@pytest.fixture(scope="module")
def disposable_postgres_url() -> Generator[str, None, None]:
    source_url = validation.source_database_url()
    if not _postgres_is_available(source_url):
        pytest.skip("PostgreSQL is unavailable")

    database = validation.DisposableDatabase(source_url)
    try:
        database.create()
        cursor_secret = os.environ.setdefault("MIND_PALACE_CURSOR_SECRET", CURSOR_SECRET)
        validation.run_migrations(database.url, cursor_secret)
        yield database.url
    finally:
        database.drop()


@asynccontextmanager
async def _sessions(database_url):
    engine = create_async_engine(validation.async_url(database_url), poolclass=NullPool)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield sessions
    finally:
        await engine.dispose()


def _seed(database_url, corpus, specs):
    engine = sa.create_engine(validation.sync_url(database_url), poolclass=NullPool)
    try:
        return validation.seed_fixture(engine, corpus, specs)
    finally:
        engine.dispose()


def _archive_ids(database_url, corpus):
    engine = sa.create_engine(validation.sync_url(database_url), poolclass=NullPool)
    try:
        return [str(row["id"]).strip() for row in validation.fetch_rows(engine, corpus)]
    finally:
        engine.dispose()


def _item_key(item):
    return item.observed_at, item.version_id


def _assert_ordered_unique(items, corpus):
    assert items
    ids = [item.version_id for item in items]
    assert len(ids) == len(set(ids))
    assert all(item.corpus == corpus for item in items)
    keys = [_item_key(item) for item in items]
    assert keys == sorted(keys)


async def _traverse(sessions, corpus, cursor, page_size):
    items = []
    seen_cursors = set()
    for page_count in range(1, 101):
        async with sessions() as db:
            page = await feed(db, corpus, cursor, page_size)
        assert page.corpus == corpus
        assert len(page.items) <= page_size
        items.extend(page.items)
        if not page.has_more:
            assert page.next_cursor is None
            return items, page_count
        assert page.next_cursor is not None
        assert page.next_cursor not in seen_cursors
        seen_cursors.add(page.next_cursor)
        cursor = page.next_cursor
    raise AssertionError("feed traversal exceeded the page guard")


async def test_feed_traverses_multiple_pages_without_duplicates(disposable_postgres_url):
    specs = validation.make_specs(TRAVERSAL_CORPUS, 12, BASE_TIME)
    _seed(disposable_postgres_url, TRAVERSAL_CORPUS, specs)

    async with _sessions(disposable_postgres_url) as sessions:
        items, page_count = await _traverse(sessions, TRAVERSAL_CORPUS, None, 5)

    assert page_count > 1
    assert page_count == 3
    assert len(items) == len(specs)
    _assert_ordered_unique(items, TRAVERSAL_CORPUS)
    assert [item.version_id for item in items] == _archive_ids(
        disposable_postgres_url, TRAVERSAL_CORPUS
    )


async def test_feed_cursor_is_durable_across_independent_sessions(disposable_postgres_url):
    specs = validation.make_specs(DURABLE_CORPUS, 12, BASE_TIME)
    _seed(disposable_postgres_url, DURABLE_CORPUS, specs)

    async with _sessions(disposable_postgres_url) as sessions:
        async with sessions() as first_db:
            first = await feed(first_db, DURABLE_CORPUS, None, 3)
        assert first.has_more
        assert first.next_cursor is not None

        async with sessions() as second_db:
            second = await feed(second_db, DURABLE_CORPUS, first.next_cursor, 3)

    first_ids = {item.version_id for item in first.items}
    second_ids = {item.version_id for item in second.items}
    assert first_ids.isdisjoint(second_ids)
    assert _item_key(second.items[0]) > _item_key(first.items[-1])
    assert all(item.corpus == DURABLE_CORPUS for item in first.items + second.items)


async def test_feed_rejects_cursor_from_another_corpus(disposable_postgres_url):
    target_specs = validation.make_specs(CURSOR_CORPUS, 6, BASE_TIME)
    other_specs = validation.make_specs(OTHER_CORPUS, 1, BASE_TIME)
    _seed(disposable_postgres_url, CURSOR_CORPUS, target_specs)
    _seed(disposable_postgres_url, OTHER_CORPUS, other_specs)

    async with _sessions(disposable_postgres_url) as sessions:
        async with sessions() as db:
            first = await feed(db, CURSOR_CORPUS, None, 2)
        assert first.next_cursor is not None

        with pytest.raises(MemoryError) as error:
            async with sessions() as db:
                await feed(db, OTHER_CORPUS, first.next_cursor, 2)

    assert error.value.code == "cursor_corpus_mismatch"


async def test_feed_handles_empty_corpus_and_page_size_boundaries(disposable_postgres_url):
    _seed(disposable_postgres_url, EMPTY_CORPUS, ())
    boundary_specs = validation.make_specs(BOUNDARY_CORPUS, 1, BASE_TIME)
    _seed(disposable_postgres_url, BOUNDARY_CORPUS, boundary_specs)

    async with _sessions(disposable_postgres_url) as sessions:
        async with sessions() as db:
            empty = await feed(db, EMPTY_CORPUS, None, 1)
        assert empty.items == []
        assert not empty.has_more
        assert empty.next_cursor is None

        async with sessions() as db:
            minimum = await feed(db, BOUNDARY_CORPUS, None, 1)
            maximum = await feed(db, BOUNDARY_CORPUS, None, 500)
        assert len(minimum.items) == 1
        assert len(maximum.items) == 1
        assert not minimum.has_more
        assert not maximum.has_more

        for invalid_page_size in (0, 501, True):
            with pytest.raises(MemoryError) as error:
                async with sessions() as db:
                    await feed(db, EMPTY_CORPUS, None, invalid_page_size)
            assert error.value.code == "invalid_page_size"


def _next_ids(start: str, used: set[str], count: int) -> list[str]:
    value = int(start, 16)
    result = []
    while len(result) < count:
        value += 1
        candidate = f"{value:064x}"
        if candidate not in used:
            result.append(candidate)
            used.add(candidate)
    return result


async def test_feed_concurrent_insertion_preserves_live_keyset_semantics(
    disposable_postgres_url,
):
    initial_specs = validation.make_specs(
        CONCURRENT_CORPUS,
        30,
        CONCURRENT_BASE_TIME,
        equal_groups=True,
    )
    sync_engine = sa.create_engine(
        validation.sync_url(disposable_postgres_url),
        poolclass=NullPool,
    )
    try:
        target = validation.seed_fixture(sync_engine, CONCURRENT_CORPUS, initial_specs)

        async with _sessions(disposable_postgres_url) as sessions:
            async with sessions() as db:
                first = await feed(db, CONCURRENT_CORPUS, None, 5)

            assert len(first.items) == 5
            assert first.has_more
            assert first.next_cursor is not None
            boundary = first.items[-1]
            boundary_time = boundary.observed_at
            boundary_id = boundary.version_id
            used_ids = {spec.version_id for spec in initial_specs}
            equal_ids = _next_ids(boundary_id, used_ids, 2)
            target_late = [
                validation.late_spec(
                    CONCURRENT_CORPUS,
                    "equal-one",
                    boundary_time,
                    equal_ids[0],
                    "m009-late-equal-one",
                ),
                validation.late_spec(
                    CONCURRENT_CORPUS,
                    "equal-two",
                    boundary_time,
                    equal_ids[1],
                    "m009-late-equal-two",
                ),
                validation.late_spec(
                    CONCURRENT_CORPUS,
                    "later",
                    boundary_time + timedelta(seconds=10),
                    validation.version_id_for(CONCURRENT_CORPUS, "later", 1),
                    "m009-late-later",
                ),
                validation.late_spec(
                    CONCURRENT_CORPUS,
                    "before-cursor",
                    boundary_time - timedelta(microseconds=1),
                    validation.version_id_for(CONCURRENT_CORPUS, "before-cursor", 1),
                    "m009-late-before",
                ),
            ]
            other_specs = validation.make_specs(
                CONCURRENT_OTHER_CORPUS,
                4,
                boundary_time + timedelta(seconds=20),
            )
            other_fixture = validation.insert_late_rows(
                sync_engine,
                target,
                target_late,
                CONCURRENT_OTHER_CORPUS,
                other_specs,
            )

            continuation, _ = await _traverse(sessions, CONCURRENT_CORPUS, first.next_cursor, 5)
            fresh, _ = await _traverse(sessions, CONCURRENT_CORPUS, None, 5)
            other_items, _ = await _traverse(sessions, CONCURRENT_OTHER_CORPUS, None, 5)

        all_rows = validation.fetch_rows(sync_engine, CONCURRENT_CORPUS)
        all_ids = [str(row["id"]).strip() for row in all_rows]
        expected_continuation = [
            str(row["id"]).strip()
            for row in all_rows
            if (row["observed_at"], str(row["id"]).strip()) > (boundary_time, boundary_id)
        ]
        first_and_continuation = list(first.items) + continuation
        continuation_ids = {item.version_id for item in continuation}
        fresh_ids = {item.version_id for item in fresh}
        other_ids = {item.version_id for item in other_items}

        assert len(initial_specs) >= 30
        assert len(first_and_continuation) == len(
            {item.version_id for item in first_and_continuation}
        )
        _assert_ordered_unique(continuation, CONCURRENT_CORPUS)
        _assert_ordered_unique(fresh, CONCURRENT_CORPUS)
        _assert_ordered_unique(other_items, CONCURRENT_OTHER_CORPUS)
        assert [item.version_id for item in continuation] == expected_continuation
        assert [item.version_id for item in fresh] == all_ids
        assert {item.version_id for item in other_items} == {
            spec.version_id for spec in other_fixture.specs
        }
        assert other_ids.isdisjoint(fresh_ids)
        assert all(item.corpus == CONCURRENT_CORPUS for item in first_and_continuation)
        assert all(item.corpus == CONCURRENT_OTHER_CORPUS for item in other_items)

        equal_items = [item for item in fresh if item.version_id in set(equal_ids)]
        assert [item.version_id for item in equal_items] == equal_ids
        assert len(equal_items) == 2
        assert equal_items[0].observed_at == boundary_time
        assert equal_items[1].observed_at == boundary_time
        assert set(equal_ids).issubset(continuation_ids)

        timestamp_groups = {}
        for item in fresh:
            timestamp_groups.setdefault(item.observed_at, []).append(item.version_id)
        assert any(len(ids) >= 2 for ids in timestamp_groups.values())
        assert all(ids == sorted(ids) for ids in timestamp_groups.values())

        before_id = target_late[3].version_id
        later_id = target_late[2].version_id
        assert later_id in continuation_ids
        assert before_id in fresh_ids
        assert before_id not in continuation_ids
    finally:
        sync_engine.dispose()
