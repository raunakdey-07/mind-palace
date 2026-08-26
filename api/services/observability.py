# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Raunak Dey

"""Lightweight observability for retrieval/context operations.

Structured log lines with stable field names — enough to diagnose latency
and behavior, no dashboard. Every context/search operation records:

    stage timings (embedding, retrieval, packing), corpus, strategy,
    candidate count, returned count, token estimate, truncated flag.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger("mindpalace.ops")


@dataclass
class OperationTrace:
    """Accumulates stage timings for one operation."""

    operation: str
    corpus: str | None = None
    strategy: str | None = None
    started: float = field(default_factory=time.perf_counter)
    embedding_ms: float | None = None
    retrieval_ms: float | None = None
    rerank_ms: float | None = None
    pack_ms: float | None = None
    candidate_count: int | None = None
    returned_count: int | None = None
    token_estimate: int | None = None
    truncated: bool | None = None

    def mark_embedding(self) -> None:
        self.embedding_ms = (time.perf_counter() - self.started) * 1000

    def mark_retrieval(self, candidates: int, returned: int) -> None:
        now = time.perf_counter()
        prev = self.embedding_ms and self.started + self.embedding_ms / 1000 or self.started
        self.retrieval_ms = (now - prev) * 1000
        self.candidate_count = candidates
        self.returned_count = returned

    def mark_pack(self, token_estimate: int, truncated: bool) -> None:
        self.pack_ms = (time.perf_counter() - self.started) * 1000 - (
            (self.retrieval_ms or 0) + (self.embedding_ms or 0)
        )
        self.token_estimate = token_estimate
        self.truncated = truncated

    def emit(self) -> None:
        total_ms = (time.perf_counter() - self.started) * 1000
        logger.info(
            "op=%s corpus=%s strategy=%s embed_ms=%.1f retrieve_ms=%.1f pack_ms=%.1f "
            "candidates=%s returned=%s tokens=%s truncated=%s total_ms=%.1f",
            self.operation,
            self.corpus or "-",
            self.strategy or "-",
            self.embedding_ms or 0,
            self.retrieval_ms or 0,
            self.pack_ms or 0,
            self.candidate_count if self.candidate_count is not None else "-",
            self.returned_count if self.returned_count is not None else "-",
            self.token_estimate if self.token_estimate is not None else "-",
            self.truncated if self.truncated is not None else "-",
            total_ms,
        )
