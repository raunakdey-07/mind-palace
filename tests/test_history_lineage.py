"""Regression coverage for history claim-lineage filtering on multi-claim docs."""

from api.models.memory import MemoryRequest
from api.services import ingestion as ingestion_service
from api.services import memory_public

ADR_001 = """# ADR 001

## Decision

- Primary job queue broker: Redis.
- Queue message retention window: 7 days.
"""

ADR_002 = """---
title: "ADR 002"
supersedes: adr-001.md
---

# ADR 002

## Decision

- Primary job queue broker: NATS.
- Queue message retention window: 7 days.
"""

ADR_003_REVERSED = """---
title: "ADR 003"
supersedes: adr-002.md
---

# ADR 003

## Decision

- Queue message retention window: 30 days.
- Primary job queue broker: Kafka.
"""


async def _ingest(store, body, path):
    await ingestion_service.IngestionService(
        memory_enabled=True, require_embeddings=False
    ).ingest_file(content=body, path=path, corpus_id=store.corpus)


async def test_history_filters_intermediate_transitions_to_matched_lineage(store):
    await _ingest(store, ADR_001, "adr-001.md")
    await _ingest(store, ADR_002, "adr-002.md")
    await _ingest(store, ADR_003_REVERSED, "adr-003.md")

    response = await memory_public.execute_in_session(
        store.db,
        "history",
        MemoryRequest(corpus=store.name, query="What is the primary job queue broker?"),
    )

    from cli.render import render_history

    rendered = render_history(response)
    assert "superseded by  Primary job queue broker: NATS." in rendered
    assert "superseded by  Primary job queue broker: Kafka." in rendered
    assert "current now: Primary job queue broker: Kafka." in rendered
    assert "Queue message retention window" not in rendered
