"""Bounded, deterministic question variants and relevance gates (never state rules).

All vocabulary comes from the query/archive. No generated facts or domain alias
map. Config is process-level, so adapters cannot bypass the relevance policy.
"""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass

from api.models.memory import MemoryRequest, MemoryResponse

# Function/temporal words are not independent evidence of a matching subject.
STOP = frozenset("""a an the of for to in on at by from with without and or as well
what which who where when why how is are was were be been do does did we our it
its them they their this that these those use uses used currently current now
previous previously before after then than rather during once still already
have has had can could would should will may might selected decision decisions
architecture project system application documented fact contract assertion
source sources document documents claim claims evidence exact show tell list
about regarding both also same all any each one two know known knowledge
stage checkpoint pilot cutover expansion federation clarification restored
restoration deleted deletion historical history formerly earlier later old
looking back today tomorrow setting aside ignore regardless between until
""".split())


def terms(text: str) -> set[str]:
    words = re.findall(r"[a-z][a-z0-9]+", text.casefold())
    result = set()
    for word in words:
        if word in STOP:
            continue
        for suffix in ("ation", "ing", "ed", "s"):
            if word.endswith(suffix) and len(word) > len(suffix) + 3:
                word = word[: -len(suffix)]
                break
        result.add(word)
    return result


@dataclass(frozen=True)
class RelevancePolicy:
    approach: str = "E"
    minimum: float = 0.30
    strong: float = 0.65
    relative: float = 0.90
    max_topics: int = 4

    def __post_init__(self):
        if self.approach not in "ABCDE" or len(self.approach) != 1:
            raise ValueError("Unknown relevance approach")
        if not all(
            math.isfinite(v) and 0 <= v <= 1 for v in (self.minimum, self.strong, self.relative)
        ):
            raise ValueError("Relevance thresholds must be finite values in [0,1]")
        if self.strong < self.minimum or not 1 <= self.max_topics <= 4:
            raise ValueError("Invalid relevance configuration")

    @classmethod
    def configured(cls):
        return cls(
            minimum=float(os.getenv("MEMORY_QUERY_MINIMUM", "0.30")),
            strong=float(os.getenv("MEMORY_QUERY_STRONG", "0.65")),
            relative=float(os.getenv("MEMORY_QUERY_RELATIVE", "0.90")),
        )


@dataclass(frozen=True)
class QueryPlan:
    original_query: str
    topics: tuple[str, ...]
    intent: str
    temporal_selector: str | None
    path: str | None


def subject(question: str) -> str:
    """Remove bounded temporal framing, never interpret source text as instructions."""
    text = question
    # Introductory framing is not the requested subject.
    text = re.sub(
        r"^(?:looking back|viewed|setting aside|ignore|regardless|without|"
        r"instead of|rather than|as distinct from|as opposed to)[^,;]*[,;]\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"^(?:at|before|after|prior to|following|during|once)\s+[^,;]+[,;]\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"^(?:looking back|viewed|setting aside|ignore|regardless|without|"
        r"instead of|rather than|as distinct from|as opposed to)[^,;]*[,;]\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\b(?:at|in|on|during)\s+(?:the\s+)?stage\s+[a-z0-9]+\b",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\b\d{4}-\d{2}-\d{2}(?:t[^\s?]+)?\b", "", text, flags=re.IGNORECASE)
    text = re.sub(
        r"\b(?:before|after|at|in|during|once)\s+(?:the\s+)?"
        r"(?:pilot|cutover|expansion|federation|ownership-clarification|"
        r"restored-runbook|capacity expansion|restoration)(?:\s+checkpoint)?\b",
        "",
        text,
        flags=re.IGNORECASE,
    )
    return " ".join(text.split()).strip(" ,;") or question


def plan(request: MemoryRequest, intent: str, policy: RelevancePolicy) -> QueryPlan:
    text = subject(request.query) if policy.approach == "E" else request.query
    if policy.approach == "E":
        text = re.split(
            r",?\s+(?:rather than|as distinct from|as opposed to)\b",
            text,
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0]
    variants = [text]
    if policy.approach in {"D", "E"}:
        parts = re.split(r"\s+(?:and|or|as well as)\s+|[,;]", text, maxsplit=policy.max_topics - 1)
        meaningful = [part.strip(" ,;") for part in parts if terms(part)]
        # Do not turn alternatives inside a question into independent requests.
        if re.search(r"\bor\b", text) or any(len(terms(p)) < 2 for p in meaningful):
            meaningful = [text]
        if len(meaningful) > 1:
            variants = meaningful[: policy.max_topics]
    return QueryPlan(
        request.query,
        tuple(variants),
        intent,
        str(request.as_of or request.snapshot_id) if request.as_of or request.snapshot_id else None,
        request.path,
    )


def relevance(
    full: MemoryResponse, request: MemoryRequest, intent: str, embedder, policy: RelevancePolicy
) -> tuple[dict[str, float], QueryPlan, dict]:
    from api.services.memory_public import _claims

    interpreted = plan(request, intent, policy)
    claims = {c.id: c for c in _claims(full)}
    ids = sorted(claims)
    if not ids:
        return {}, interpreted, {"candidates": 0, "embedding_calls": 0, "embedding_texts": 0}
    representations = [f"{claims[c].claim} {claims[c].key} {claims[c].path}" for c in ids]
    # One batch across all topics and claims, not one full archive encoding per topic.
    texts = list(interpreted.topics) + representations
    vectors = embedder.embed(texts)
    if len(vectors) != len(texts) or not vectors or not vectors[0]:
        raise ValueError("Embedding output count/dimension mismatch")
    dimension = len(vectors[0])
    if any(len(v) != dimension or not all(math.isfinite(x) for x in v) for v in vectors):
        raise ValueError("Invalid embedding vectors")
    documents = [terms(text) for text in representations]
    # Terms occurring in every candidate (e.g. project name) cannot support relevance.
    common = set.intersection(*documents) if len(documents) > 1 else set()
    chosen = {}
    for i, topic in enumerate(interpreted.topics):
        query_terms = terms(topic) - common
        by_key = {}
        for cid, vector, words in zip(ids, vectors[len(interpreted.topics) :], documents):
            claim = claims[cid]
            if request.path and claim.path != request.path:
                continue
            score = sum(a * b for a, b in zip(vectors[i], vector))
            overlap = bool(query_terms & (words - common))
            if policy.approach == "A":
                accepted = score >= 0.30
            elif policy.approach == "B":
                accepted = score >= 0.30 and overlap
            elif policy.approach == "C":
                accepted = score >= policy.minimum
            else:
                accepted = score >= policy.minimum and (
                    overlap or score >= policy.strong or math.isclose(score, policy.minimum)
                )
            if accepted:
                by_key[claim.key] = max(by_key.get(claim.key, -1), score)
        top = max(by_key.values(), default=0)
        for key, score in by_key.items():
            if score >= top * policy.relative:
                chosen[key] = max(chosen.get(key, -1), score)
    return (
        chosen,
        interpreted,
        {
            "candidates": len(ids),
            "embedding_calls": 1,
            "embedding_texts": len(texts),
            "topics": len(interpreted.topics),
        },
    )
