# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Raunak Dey

"""One code path for building a context pack, shared by REST, SDK and MCP.

Order matters, and it encodes the product rule: authority decides what is true
and what is absent, retrieval only adds raw source material.

    1. Does this corpus have an archive?
    2. If yes, resolve the question against the archive. That answer is the pack.
    3. If it resolves nothing, stop. Do not pad with retrieval output.
    4. If no, retrieval is the only source and the pack says so via ``status``.
    5. Retrieval runs as an optimization and is skipped when the model is gone.

Because step 5 is optional, a missing embedding model downgrades the pack from
semantic to lexical to authoritative-only. It never removes the answer.
"""

from __future__ import annotations

import logging
import time

from api.models.memory import MemoryRequest
from api.services.context_packer import ContextPack, pack_context
from api.services.corpora import get_corpus_by_name
from api.services.memory import enabled as archive_present

logger = logging.getLogger(__name__)

VALID_STRATEGIES = {"vector", "hybrid", "hybrid_rrf"}


class ContextError(Exception):
    """Public context failure carrying an HTTP status."""

    def __init__(self, code: str, message: str, status_code: int):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


NO_RELEVANT_MEMORY = "NO_RELEVANT_MEMORY"


def _has_relevant_memory(memory) -> bool:
    """Did the archive actually answer the question?

    Absence is the marker's job, not the length of the result. A pack that
    resolved real claims but dropped them to fit the budget is truncated, not
    empty, and must not be padded with retrieval output.
    """
    if memory is None:
        return False
    if NO_RELEVANT_MEMORY in memory.constraints:
        return False
    return bool(
        memory.current_memories
        or memory.historical_memories
        or memory.uncertain_memories
        or memory.changes
        or memory.conflicts
        or memory.evidence
    )


async def build_context(
    db,
    corpus: str,
    query: str,
    *,
    budget_tokens: int = 4096,
    k: int = 8,
    strategy: str = "hybrid_rrf",
    as_of=None,
    intent: str | None = None,
) -> tuple[ContextPack, dict]:
    """Return the pack plus a timing/decision trace for observability.

    ``as_of`` is optional; when set the archive resolves that instant instead of
    now, so "what was true then" and "what is true" share one code path.
    """
    if strategy not in VALID_STRATEGIES:
        raise ContextError(
            "invalid_strategy",
            f"invalid strategy '{strategy}'; expected one of {sorted(VALID_STRATEGIES)}",
            422,
        )

    trace = {
        "operation": "context",
        "corpus": corpus,
        "strategy": strategy,
        "archived": False,
        "semantic": False,
        "intent": None,
        "status": None,
    }

    corpus_row = await get_corpus_by_name(db, corpus)
    if corpus_row is None:
        raise ContextError("corpus_not_found", f"corpus '{corpus}' not found", 404)
    corpus_id = corpus_row["id"]

    t0 = time.perf_counter()
    try:
        archived = await archive_present(db, corpus_id)
    except Exception:  # noqa: BLE001 - absence of an archive is not fatal
        logger.warning("context_archive_probe_failed", exc_info=True)
        archived = False
    trace["archived"] = archived

    memory = None
    if archived:
        from api.services.memory_public import MemoryError, execute_in_session

        # The envelope is far more verbose than the text it renders, so it gets a
        # floor. The token budget still governs the delivered text.
        request = MemoryRequest(
            corpus=corpus,
            query=query,
            as_of=as_of,
            budget=max(4096, budget_tokens * 4),
            intent=intent or "auto",
        )
        try:
            memory = await execute_in_session(db, "query", request)
            trace["intent"] = memory.state.as_of.isoformat() if memory.state.as_of else None
        except MemoryError as exc:
            # An authoritative failure is reported, never papered over with chunks.
            raise ContextError(exc.code, exc.message, exc.status_code) from exc
    trace["authoritative_ms"] = round((time.perf_counter() - t0) * 1000, 3)

    # Retrieval is the optional rung. Without it the pack still answers, and it is
    # skipped entirely once the archive has said there is nothing relevant.
    results = []
    if not (memory is not None and not _has_relevant_memory(memory)):
        t1 = time.perf_counter()
        results = await _retrieve(db, corpus_id, query, k, strategy)
        trace["retrieval_ms"] = round((time.perf_counter() - t1) * 1000, 3)
        trace["candidate_count"] = len(results)
        trace["semantic"] = True

    pack = pack_context(
        query, results, budget_tokens=budget_tokens, strategy=strategy, memory=memory
    )
    trace["status"] = pack.status
    trace["returned_count"] = len(pack.memories) + len(pack.conflicts)
    return pack, trace


async def _retrieve(db, corpus_id: str, query: str, k: int, strategy: str) -> list:
    """Rank live chunks. A missing model downgrades to no retrieval, not an error."""
    from api.services.embedder import SEMANTIC_DEPENDENCY_ERRORS, Embedder
    from api.services.retrieval import RetrievalService

    try:
        vector = Embedder().embed_single(query)
    except SEMANTIC_DEPENDENCY_ERRORS:
        logger.info("context_semantic_unavailable", extra={"strategy": strategy})
        return []
    try:
        return await RetrievalService(db).search(
            vector,
            k=k,
            hybrid=(strategy != "vector"),
            rrf=(strategy == "hybrid_rrf"),
            query_text=query if strategy != "vector" else None,
            corpus_id=corpus_id,
        )
    except SEMANTIC_DEPENDENCY_ERRORS:
        logger.info("context_retrieval_unavailable", extra={"strategy": strategy})
        return []


__all__ = ["ContextError", "build_context", "VALID_STRATEGIES"]
