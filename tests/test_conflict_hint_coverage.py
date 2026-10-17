"""The conflict hint must not stop looking because a corpus got large.

The defect this covers: `related_active` read the **200 most recently observed**
active claims and tokenised them. Above 200 claims, a fact written early was
invisible to the hint, and the receipt said nothing about the truncation. A
caller reading "no related claims" would conclude there was nothing nearby, which
is the one thing the hint exists to prevent.

Now the vector channel is an HNSW nearest-neighbour lookup over every cached
claim vector. `LIMIT` bounds how many come back, not which rows were searched,
and the lexical channel that remains is unbounded.
"""

import hashlib
import inspect
import uuid

from sqlalchemy import text

from api.services import claim_embeddings, memory
from api.services.claim_embeddings import representation_hash, representation_of
from api.services.corpora import get_or_create_corpus
from api.services.db import session_scope
from api.services.embedder import Embedder
from api.services.remember import related_active


async def _corpus() -> tuple[str, str]:
    name = "ann-" + uuid.uuid4().hex[:8]
    async with session_scope() as db:
        return (await get_or_create_corpus(db, name))["id"], name


async def _seed(corpus_id: str, n: int, embedder) -> list[str]:
    """n claims with cached vectors, oldest first. Returns the claim ids."""
    ids = []
    async with session_scope() as db:
        for i in range(n):
            path = f"bulk/{i // 50:03d}/doc{i:05d}.md"
            statement = f"Service {1000000 + i} datastore runs on PostgreSQL 16 with {i} replicas."
            key = f"svc.{1000000 + i}.datastore"
            body = f"# Service {1000000 + i}\n\n- {statement}\n"
            metadata = {
                "title": f"Service {1000000 + i}",
                "claims": [
                    {"key": key, "claim": statement, "value": statement, "evidence": statement}
                ],
            }
            chunks = [{"text": body, "heading_path": f"Service {1000000 + i}", "order_index": 0}]
            version = await memory.record_version(
                db,
                corpus_id,
                path,
                hashlib.sha256(path.encode()).hexdigest()[:32],
                body,
                metadata,
                chunks,
            )
            claim_id = (
                await db.execute(
                    text("SELECT id FROM memory_claims WHERE version_id = :v"),
                    {"v": version["id"]},
                )
            ).scalar_one()
            ids.append(claim_id)
        # `session_scope` does not commit and the evidence trigger is DEFERRABLE,
        # so without this every claim above would be rolled back with the session.
        await db.commit()

    async with session_scope() as db:
        wanted, texts = {}, []
        for i, claim_id in enumerate(ids):
            representation = representation_of(
                f"Service {1000000 + i} datastore runs on PostgreSQL 16 with {i} replicas.",
                f"svc.{1000000 + i}.datastore",
                f"bulk/{i // 50:03d}/doc{i:05d}.md",
            )
            wanted[claim_id] = representation_hash(representation)
            texts.append(representation)
        for start in range(0, len(texts), 256):
            batch = texts[start : start + 256]
            encoded = embedder.embed(batch)
            keys = list(wanted)[start : start + 256]
            await claim_embeddings.store(
                db,
                corpus_id,
                {k: wanted[k] for k in keys},
                dict(zip(keys, encoded)),
                embedder.model_name,
                embedder.dimension,
                "test",
                commit=True,
            )
    return ids


async def test_the_hint_finds_a_claim_written_first_in_a_large_corpus():
    """The exact failure: claim #0, written before 2,000 others existed."""
    corpus_id, name = await _corpus()
    embedder = Embedder()
    ids = await _seed(corpus_id, 300, embedder)

    async with session_scope() as db:
        first = await memory._active_claims(db, corpus_id, limit=None)
    assert len(first) == 300
    oldest = min(first, key=lambda row: row["observed_at"])
    assert oldest["id"] == ids[0], "the oldest claim must be the one written first"

    found = await related_active(oldest["claim"], corpus_id)
    assert any(
        row["claim_id"] == oldest["id"] for row in found
    ), "a claim written before the 200 most recent must still be reachable"


async def test_the_vector_channel_is_used_when_vectors_exist():
    corpus_id, _name = await _corpus()
    embedder = Embedder()
    await _seed(corpus_id, 20, embedder)
    async with session_scope() as db:
        active = await memory._active_claims(db, corpus_id, limit=None)
    found = await related_active(active[0]["claim"], corpus_id)
    assert found, "a self-match must always be found"
    assert {row["channel"] for row in found} == {"vector"}


async def test_the_lexical_channel_still_answers_with_no_vectors():
    """A corpus written entirely through `remember` has no cached vectors at all.

    The hint has to exist there, because that is where a first write is most
    likely to collide with something already recorded.
    """
    corpus_id, _name = await _corpus()
    async with session_scope() as db:
        for i in range(5):
            path = f"nv/doc{i}.md"
            statement = f"The failover region for tier {i} is eu-west-{i}."
            key = f"failover.tier{i}"
            body = f"# Tier {i}\n\n- {statement}\n"
            metadata = {
                "title": f"Tier {i}",
                "claims": [
                    {"key": key, "claim": statement, "value": statement, "evidence": statement}
                ],
            }
            chunks = [{"text": body, "heading_path": f"Tier {i}", "order_index": 0}]
            await memory.record_version(
                db,
                corpus_id,
                path,
                hashlib.sha256(path.encode()).hexdigest()[:32],
                body,
                metadata,
                chunks,
            )
        await db.commit()

    async with session_scope() as db:
        rows = await db.execute(
            text("SELECT count(*) FROM memory_claim_embeddings WHERE corpus_id = :c"),
            {"c": corpus_id},
        )
        assert rows.scalar_one() == 0, "no vectors, by construction"
        active = await memory._active_claims(db, corpus_id, limit=None)

    # Same sentence as claim 0, so token overlap is 1.0.
    found = await related_active(active[0]["claim"], corpus_id)
    assert any(row["claim_id"] == active[0]["id"] for row in found)


async def test_the_neighbour_search_does_not_bound_the_candidate_pool():
    """`LIMIT` is a result count. A claim outside it is far, not old."""
    corpus_id, _name = await _corpus()
    embedder = Embedder()
    await _seed(corpus_id, 60, embedder)
    async with session_scope() as db:
        active = await memory._active_claims(db, corpus_id, limit=None)
    oldest = min(active, key=lambda row: row["observed_at"])
    query = embedder.embed([oldest["claim"]])[0]
    async with session_scope() as db:
        rows = await memory._nearest_active_claims(
            db,
            corpus_id,
            query,
            model=embedder.model_name,
            dimension=embedder.dimension,
            limit=3,
        )
    assert oldest["id"] in {row["id"] for row in rows}


async def test_the_hnsw_index_exists():
    """Migration 012's guarantee, checked against the catalog.

    Deliberately not a plan assertion. At the corpus sizes a test can afford to
    build, the planner legitimately prefers the existing
    `(corpus_id, embedding_model, embedding_dimension)` btree and sorts: at forty
    claims that really is the cheaper plan, so asserting an index scan there would
    be asserting the planner is wrong.

    The plan at a size where it matters was measured instead, over 2,000 cached
    vectors: `Index Scan using idx_claim_embeddings_hnsw on memory_claim_embeddings`,
    3.8 ms for the whole statement. That figure is in the migration docstring,
    which is where someone deciding whether the index is worth having will look.
    """
    async with session_scope() as db:
        row = (
            await db.execute(
                text(
                    "SELECT indexdef FROM pg_indexes WHERE indexname = 'idx_claim_embeddings_hnsw'"
                )
            )
        ).scalar_one_or_none()
    assert row is not None, "migration 012 did not create the index"
    assert "hnsw" in row and "vector_cosine_ops" in row, row
    assert "embedding IS NOT NULL" in row, "the partial predicate must match the query"


def test_the_neighbour_query_is_shaped_for_the_index():
    """An index is only usable if the query orders by the operator it indexes."""
    sql = memory.NEIGHBOUR_SQL
    assert "ORDER BY embedding <=> CAST(:q AS vector)" in sql
    assert "embedding IS NOT NULL" in sql, "the partial index predicate must match"
    assert sql.index("ORDER BY") < sql.index("LIMIT :fetch")


async def test_a_stale_cached_vector_is_not_reported():
    """A score computed from text the claim no longer has is not evidence."""
    from api.services.remember import _vector_channel

    corpus_id, _name = await _corpus()
    embedder = Embedder()
    await _seed(corpus_id, 5, embedder)
    async with session_scope() as db:
        row = (
            await db.execute(
                text(
                    "SELECT claim_id FROM memory_claim_embeddings " "WHERE corpus_id = :c LIMIT 1"
                ),
                {"c": corpus_id},
            )
        ).scalar_one()
        # Rewrite the cached row's hash so it no longer describes the text it was
        # made from, exactly as an edited claim would.
        await db.execute(
            text(
                "UPDATE memory_claim_embeddings SET representation_hash = :h "
                "WHERE corpus_id = :c AND claim_id = :i"
            ),
            {"h": "0" * 64, "c": corpus_id, "i": row},
        )
        await db.commit()

    async with session_scope() as db:
        active = await memory._active_claims(db, corpus_id, limit=None)
    target = next(c for c in active if c["id"] == row)
    async with session_scope() as db:
        out = await _vector_channel(memory, db, target["claim"], corpus_id, None)
    assert row not in {r["claim_id"] for r in out.values()}


async def test_the_hint_never_raises_on_an_unknown_corpus():
    """The write it annotates already succeeded; a hint must not fail it."""
    assert await related_active("anything at all", "0" * 64) == []


def test_the_unbounded_reader_is_the_default():
    """`_active_claims` answers "which claims are live", completely.

    It used to default to the 200 most recent. A caller looking for a
    near-duplicate must not be handed a slice of the live set with nothing saying
    it is one, so the default is everything, and the bounded alternative is
    `_nearest_active_claims`, which is bounded by similarity rather than by age.
    """
    signature = inspect.signature(memory._active_claims)
    assert signature.parameters["limit"].default is None
    assert memory.NEIGHBOUR_FETCH > 5, "the fetch must exceed the reported count"
    assert "ORDER BY embedding <=> " in memory.NEIGHBOUR_SQL
