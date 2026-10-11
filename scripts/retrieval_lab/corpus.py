"""The corpus, the questions, and the gold labels -- loaded, never re-authored.

Everything here comes from `scripts/benchmark/m0115_dataset.py`, which renders a
platform specification into documents *and* authors the questions against that
same specification. The specification is the ground truth; the documents are
rendered from it. That ordering is the point: a question cannot drift away from
the corpus because both were written from one source.

Three kinds of label, because three kinds of question exist:

* **claim-level gold** -- the authored key, e.g. `architecture.datastore`. A
  retriever that ranks the right key first has solved the question.
* **passage-level gold** -- the passages whose text contains that key's claim.
  A retriever can find the right *sentence* inside the wrong *document*, and
  claim-level gold alone cannot see that.
* **ABSENT** -- `key is None`. The corpus does not contain the answer, and a
  good retriever must rank nothing highly enough to be believed.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from api.services.chunker import chunk_document  # noqa: E402
from api.services.parser import parse_markdown  # noqa: E402


@dataclass(frozen=True)
class Claim:
    """One authored claim: the unit the product ranks and the unit gold names."""

    key: str
    claim: str
    value: str
    path: str
    order: int
    valid_from: str | None = None
    valid_until: str | None = None

    @property
    def claim_id(self) -> str:
        return self.key


@dataclass(frozen=True)
class Passage:
    """One chunk of a document, whether or not any claim lives in it."""

    passage_id: str
    path: str
    text: str
    order: int
    #: Keys of claims whose text occurs in this passage. Empty means the passage
    #: carries NO authority, which arm F must preserve rather than paper over.
    claim_keys: tuple[str, ...] = ()


@dataclass(frozen=True)
class Question:
    qid: str
    text: str
    slice: str
    #: Authored key, or None for an ABSENT question.
    key: str | None
    expect_state: str
    expect_values: tuple[str, ...] = ()
    forbid_values: tuple[str, ...] = ()

    @property
    def answerable(self) -> bool:
        return self.key is not None


@dataclass
class Corpus:
    claims: list[Claim]
    passages: list[Passage]
    dataset_hash: str = ""
    notes: dict = field(default_factory=dict)

    def claim_by_key(self, key: str) -> Claim | None:
        for claim in self.claims:
            if claim.key == key:
                return claim
        return None

    def passages_for(self, key: str) -> list[Passage]:
        return [p for p in self.passages if key in p.claim_keys]


def _parse_documents(documents) -> tuple[list[Claim], list[Passage]]:
    """Turn rendered documents into claims and passages.

    The corpus has repeated document paths across stages: a later stage
    supersedes an earlier one. Superseded text is kept, because "what did we
    used to think" is one of the slices and it can only be answered if the old
    version is retrievable. The gold key alone does not say which version.
    """
    claims: list[Claim] = []
    passages: list[Passage] = []
    seen_claim_texts: set[tuple[str, str]] = set()

    for doc in documents:
        metadata, body = parse_markdown(doc.content)
        raw_claims = metadata.get("claims") or []
        for order, entry in enumerate(raw_claims):
            key = str(entry.get("key", "")).strip()
            claim_text = str(entry.get("claim", "")).strip()
            if not key or not claim_text:
                continue
            marker = (key, claim_text)
            if marker in seen_claim_texts:
                # The same authored claim re-rendered in a later stage. Ranking
                # it twice would make recall@5 look better than it is.
                continue
            seen_claim_texts.add(marker)
            claims.append(
                Claim(
                    key=key,
                    claim=claim_text,
                    value=str(entry.get("value", "")),
                    path=doc.path,
                    order=order,
                    valid_from=entry.get("valid_from"),
                    valid_until=entry.get("valid_until"),
                )
            )

        for index, chunk in enumerate(chunk_document(body)):
            text = chunk if isinstance(chunk, str) else chunk.get("text", "")
            if not text.strip():
                continue
            located = tuple(
                c.key for c in claims if c.path == doc.path and c.claim and c.claim in text
            )
            passages.append(
                Passage(
                    passage_id=f"{doc.path}#{index}",
                    path=doc.path,
                    text=text,
                    order=index,
                    claim_keys=located,
                )
            )
    return claims, passages


#: The slices the report is broken down by. Every number in the lab is reported
#: per slice; none is pooled.
SLICES = (
    "current",
    "historical",
    "temporal",
    "conflict",
    "provenance",
    "abstention",
    "multi-topic",
    "relationship",
    "adversarial",
)


def load_dev() -> tuple[Corpus, list[Question]]:
    """The 202-question development set. **Dev** -- tuning is allowed here."""
    from benchmark.m0115_dataset import build_corpus, build_questions, dataset_hash

    documents = build_corpus()
    claims, passages = _parse_documents(documents)
    questions = [
        Question(
            qid=q.qid,
            text=q.text,
            slice=q.category,
            key=q.key,
            expect_state=q.expect_state,
            expect_values=tuple(q.expect_values),
            forbid_values=tuple(q.forbid_values),
        )
        for q in build_questions()
    ]
    corpus = Corpus(
        claims=claims,
        passages=passages,
        dataset_hash=dataset_hash(),
        notes={
            "kind": "dev",
            "documents": len(documents),
            "independent_of_retriever": True,
        },
    )
    return corpus, questions


def load_heldout_v1() -> tuple[Corpus, list[Question]]:
    """The 157-question held-out set, used for **diagnosis only**.

    It has been scored repeatedly and its failures are known to the author of
    this repository, which is precisely what makes it useless as evidence of
    generalisation. It is loaded here so a regression is visible, and it is
    labelled `burned` in every artifact so nobody mistakes it for a sealed set.
    """
    import json

    path = ROOT / "docs/performance/m0130-heldout.json"
    data = json.loads(path.read_text())
    documents = __import__("benchmark.m0130_heldout", fromlist=["build_corpus"]).build_corpus()
    claims, passages = _parse_documents(documents)
    by_qid = {r["qid"]: r for r in data["results"]}
    questions = [
        Question(
            qid=q.qid,
            text=q.text,
            slice=q.category,
            key=q.key,
            expect_state=q.expect_state,
        )
        for q in __import__(
            "benchmark.m0130_heldout", fromlist=["build_questions"]
        ).build_questions()
        if q.qid in by_qid
    ]
    corpus = Corpus(
        claims=claims,
        passages=passages,
        dataset_hash=data["meta"]["dataset_hash"],
        notes={
            "kind": "burned",
            "scored_before": True,
            "may_be_used_for": "diagnosis only; never as evidence of generalisation",
        },
    )
    return corpus, questions
