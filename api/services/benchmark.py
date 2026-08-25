"""Retrieval strategy benchmark: compares vector/hybrid/RRF/reranked retrieval.

Requires a live PostgreSQL + pgvector database with an ingested corpus.
This is an integration instrument, deliberately separate from the unit suite.
"""

from __future__ import annotations

import statistics
import time
from dataclasses import dataclass, field
from typing import Callable

from api.services.db import session_scope
from api.services.embedder import Embedder
from api.services.evaluation import (
    EvaluationService,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
)
from api.services.retrieval import RetrievalResult, RetrievalService

K_VALUES = (1, 3, 5, 10)


@dataclass
class StrategyResult:
    """Aggregate metrics and latency for one strategy over the benchmark."""

    name: str
    recall: dict[int, float] = field(default_factory=dict)
    precision: dict[int, float] = field(default_factory=dict)
    mrr: float = 0.0
    ndcg: dict[int, float] = field(default_factory=dict)
    latencies_ms: list[float] = field(default_factory=list)

    @property
    def avg_latency_ms(self) -> float:
        return statistics.mean(self.latencies_ms) if self.latencies_ms else 0.0

    @property
    def p50_latency_ms(self) -> float:
        return statistics.median(self.latencies_ms) if self.latencies_ms else 0.0


def _make_strategy(service: RetrievalService, query_vector: list[float], **kwargs) -> Callable:
    async def run(k: int) -> tuple[list[str], list[RetrievalResult]]:
        results = await service.search(query_vector, k=k, **kwargs)
        return [r.source_title for r in results], results

    return run


def build_strategies(
    service: RetrievalService,
    embedder: Embedder,
    query: str,
    candidate_sizes: tuple[int, ...] = (20,),
) -> dict[str, Callable]:
    """Return ordered strategy runners for a query. Same queries for every strategy.

    When multiple candidate sizes are given, one rerank variant per size is added
    (e.g. ``hybrid+rrf+rerank@c10``).
    """
    vector = embedder.embed_single(query)
    strategies: dict[str, Callable] = {
        "vector": _make_strategy(service, vector, hybrid=False),
        "hybrid": _make_strategy(service, vector, hybrid=True, rrf=False, query_text=query),
        "hybrid+rrf": _make_strategy(service, vector, hybrid=True, rrf=True, query_text=query),
    }
    for c in candidate_sizes:
        name = f"hybrid+rrf+rerank@c{c}"
        strategies[name] = _make_strategy(
            service, vector, hybrid=True, rrf=True, query_text=query, rerank=True, candidate_k=c
        )
    return strategies


async def run_benchmark(
    benchmark_file: str = "eval/retrieval_benchmarks.yaml",
    k_values: tuple[int, ...] = K_VALUES,
    candidate_sizes: tuple[int, ...] = (20,),
) -> tuple[dict[str, StrategyResult], list[dict]]:
    """Run all strategies over the benchmark.

    Returns per-strategy aggregates plus detailed failure records.
    """
    benchmarks = EvaluationService.load_benchmarks(benchmark_file)
    embedder = Embedder()

    results: dict[str, StrategyResult] = {}
    failures: list[dict] = []

    # Warm up models once so first-query load time doesn't skew latency.
    embedder.embed_single("warmup")

    async with session_scope() as db:
        service = RetrievalService(db)

        for bm in benchmarks:
            query = bm["query"]
            expected = bm["expected"]
            runners = build_strategies(service, embedder, query, candidate_sizes)

            for name in results_or_default(runners):
                if name not in results:
                    results[name] = StrategyResult(name=name)
                runner = runners[name]

                start = time.perf_counter()
                retrieved_titles, retrieved_results = await runner(k=max(k_values))
                elapsed_ms = (time.perf_counter() - start) * 1000

                sr = results[name]
                sr.latencies_ms.append(elapsed_ms)
                for k in k_values:
                    sr.recall[k] = sr.recall.get(k, 0.0) + recall_at_k(
                        expected, retrieved_titles, k
                    )
                    sr.precision[k] = sr.precision.get(k, 0.0) + precision_at_k(
                        expected, retrieved_titles, k
                    )
                    sr.ndcg[k] = sr.ndcg.get(k, 0.0) + ndcg_at_k(expected, retrieved_titles, k)
                sr.mrr += reciprocal_rank(expected, retrieved_titles)

                if not set(expected) & set(retrieved_titles[: max(k_values)]):
                    failures.append(_failure_record(bm, name, expected, retrieved_results))

    n = len(benchmarks)
    for sr in results.values():
        for k in k_values:
            sr.recall[k] /= n
            sr.precision[k] /= n
            sr.ndcg[k] /= n
        sr.mrr /= n

    return results, failures


def results_or_default(runners: dict[str, Callable]) -> list[str]:
    """Stable ordering helper for strategy names."""
    order = ["vector", "hybrid", "hybrid+rrf"]
    rerank = sorted(n for n in runners if n.startswith("hybrid+rrf+rerank"))
    return [n for n in order if n in runners] + rerank


def _failure_record(
    bm: dict, strategy: str, expected: list[str], results: list[RetrievalResult]
) -> dict:
    """Capture full diagnostic detail for a failed retrieval."""
    return {
        "query": bm["query"],
        "category": bm["category"],
        "strategy": strategy,
        "expected": expected,
        "candidates": [
            {
                "rank": i + 1,
                "title": r.source_title,
                "heading_path": r.heading_path,
                "vector_score": r.vector_score,
                "keyword_score": r.keyword_score,
                "rrf_score": r.rrf_score,
                "rerank_score": r.rerank_score,
                "text_preview": r.text[:200],
            }
            for i, r in enumerate(results)
        ],
    }


def format_report(
    results: dict[str, StrategyResult],
    k_values: tuple[int, ...] = K_VALUES,
    failures: list[dict] | None = None,
) -> str:
    """Render a readable comparison table."""
    lines = []
    header = (
        "Strategy".ljust(24)
        + "".join(f"R@{k}".rjust(6 if k == 1 else 7) for k in k_values)
        + "P@3".rjust(7)
        + "MRR".rjust(7)
        + "nDCG@5".rjust(8)
        + "avg ms".rjust(9)
    )
    lines.append(header)
    lines.append("-" * len(header))

    ordered = results_or_default(results)  # type: ignore[arg-type]
    for name in ordered:
        sr = results[name]
        row = name.ljust(24)
        row += "".join(f"{sr.recall[k]:.2f}".rjust(6 if k == 1 else 7) for k in k_values)
        row += f"{sr.precision.get(3, 0.0):.2f}".rjust(7)
        row += f"{sr.mrr:.2f}".rjust(7)
        row += f"{sr.ndcg.get(5, 0.0):.2f}".rjust(8)
        row += f"{sr.avg_latency_ms:.0f}".rjust(9)
        lines.append(row)

    if failures:
        lines.append("")
        lines.append(f"Total misses (no expected doc in top-{max(k_values)}): {len(failures)}")
        for f in failures:
            lines.append(f"  [{f['strategy']}] ({f['category']}) {f['query']}")
            lines.append(f"      expected: {', '.join(f['expected'])}")
            top = f["candidates"][:5]
            got = ", ".join(c["title"] for c in top) or "nothing"
            lines.append(f"      got:      {got}")

    return "\n".join(lines)


def format_failure_details(failures: list[dict]) -> str:
    """Render verbose failure diagnostics with scores and chunk text."""
    lines = []
    for f in failures:
        lines.append(f"[{f['strategy']}] ({f['category']}) {f['query']}")
        lines.append(f"  Expected: {', '.join(f['expected'])}")
        for c in f["candidates"]:
            scores = []
            if c["vector_score"] is not None:
                scores.append(f"vec={c['vector_score']:.3f}")
            if c["keyword_score"] is not None:
                scores.append(f"kw={c['keyword_score']:.3f}")
            if c["rrf_score"] is not None:
                scores.append(f"rrf={c['rrf_score']:.3f}")
            if c["rerank_score"] is not None:
                scores.append(f"ce={c['rerank_score']:.3f}")
            lines.append(f"  #{c['rank']} {c['title']} | {c['heading_path']} | {' '.join(scores)}")
            lines.append(f"      text: {c['text_preview'][:120]}...")
        lines.append("")
    return "\n".join(lines)
