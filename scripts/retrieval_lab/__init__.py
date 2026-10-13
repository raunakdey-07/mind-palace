"""Scoring retrieval alone, with no language model in the loop.

    python scripts/retrieval_lab/run.py --arms A,B,C,D,E,F --out results.json

## Why this exists

Every number this project has about retrieval is an *end-to-end* number: a
question is asked, an answer comes back, and the answer is either right or
wrong. That measures the product, which is what an integrator buys, and it
cannot tell you **why** it was wrong. A miss could be the embedder, the ranker,
the acceptance gate, the question's vocabulary, or the fact that the answer was
never a claim.

This lab scores the *ranking* instead, against gold that was written before the
retriever existed, so each of those causes can be told apart.

## The three rules that keep it honest

**Gold is authored, not derived from a run.** A label that came from "what the
system returned" measures nothing.

**Arm A imports the shipped scorer.** `api.services.memory_relevance` is used
directly -- `plan`, `terms`, `claim_terms` -- and claims are embedded through
the same `representation_of` the product caches vectors for. Arm A is therefore
not a reimplementation that can drift; it is the shipped logic with the
database and the response envelope removed. Arms B-F are new code and are
labelled as such.

**Nothing here is a headline.** Every result is reported per slice. A single
number across slices hides exactly the failures worth knowing about: a system
can be strong on `current` and actively harmful on `absent`, and the average
stays respectable.
"""

from __future__ import annotations

from .corpus import Corpus, Claim, Passage, Question, load_dev, load_heldout_v1
from .metrics import (
    answerable_separation_auroc,
    confident_wrong_rate,
    metrics_for_run,
    risk_coverage,
)

__all__ = [
    "Corpus",
    "Claim",
    "Passage",
    "Question",
    "load_dev",
    "load_heldout_v1",
    "answerable_separation_auroc",
    "confident_wrong_rate",
    "metrics_for_run",
    "risk_coverage",
]
