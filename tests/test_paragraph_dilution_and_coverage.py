"""The three defects the first dogfood round left behind.

**Paragraph dilution.** A statement the writer wrapped over three lines is one
sentence, and the mean-pooled vector of the whole thing is the mean of all
three lines' words. The continuation clauses are detail, and averaging them in
dilutes the claim's own topic: the row-level-security claim in
`docs/security.md` scored 0.530 on "How are tenants isolated in the database?"
as the line the writer wrapped it in, and 0.465 as the whole paragraph. A
statement is now embedded as its lead.

**Terse fragment bias.** "embedding suite: `7 passed`;" shares one word with
"How large is the default install and which embedding backend does it use?"
and scored higher than the runtime.md claim that answers it, which carries two
of the question's five concepts. The order now counts how much of the question
a claim answers, not only how alike it is.

**Out-of-corpus subject abstention.** "How is Kafka configured in Mind Palace?"
matched the project's own name and returned claims about the project's
portability. A question whose subject the corpus has never used abstains, and
a question whose subject the corpus's best answer is not about abstains too.
"""

from __future__ import annotations

from api.models.memory import Claim, MemoryRequest
from api.services import claim_embeddings, memory_relevance

CLOCK = __import__("datetime").datetime(2024, 6, 15, 12, tzinfo=__import__("datetime").timezone.utc)

WRAPPED = (
    "**Row-level security — migration `008`.** Every tenant-owned table carries a\n"
    "   policy keyed on `corpus_id`, with `ENABLE` and `FORCE`."
)


def _claim(identifier, key, text, *, path="docs/security.md"):
    return Claim(
        id=identifier,
        key=key,
        value=text,
        claim=text,
        status="CURRENT",
        version_id="a" * 64,
        path=path,
        observed_at=CLOCK,
        evidence_ids=["e-" + identifier],
    )


def test_the_representation_is_the_lead_clause_and_the_heading_context():
    """The continuation clauses are detail, and detail does not get a vote.

    The whole paragraph averages to 0.465 against the tenant question and the
    line the writer wrapped it in averages to 0.530. The representation is the
    line the topic is in, plus the heading the claim sits under.
    """
    claim = _claim("rls", "md:security-the-boundary:2", WRAPPED)
    assert claim_embeddings.representation(claim) == (
        "**Row-level security — migration `008`.** Every tenant-owned table carries a"
        " md:security-the-boundary:2 docs/security.md"
    )


def test_a_claim_that_fits_on_one_line_is_its_own_lead():
    """An authored claim is a sentence, and a sentence is one line."""
    claim = _claim("one", "architecture.delivery", "Dispatch delivers order events at least once.")
    assert claim_embeddings.representation(claim) == (
        "Dispatch delivers order events at least once. architecture.delivery docs/security.md"
    )


def test_the_lead_is_the_first_line_whatever_the_claim_says():
    assert claim_embeddings.lead_clause("one line") == "one line"
    assert claim_embeddings.lead_clause("first\nsecond") == "first"
    assert claim_embeddings.lead_clause("  first  \nsecond") == "first"


class FakeEmbedder:
    """A prescribed score per text, and the rest of them at `default`."""

    def __init__(self, scores=None, default=0.5):
        self.scores = scores or {}
        self.default = default
        self.calls = []

    def embed(self, texts):
        self.calls.append(list(texts))
        out = []
        for text in texts:
            score = self.scores.get(text, self.default)
            out.append([score, (1.0 - score * score) ** 0.5])
        return out


def _response(claims):
    from api.models.memory import Evidence, MemoryResponse, State

    response = MemoryResponse(query="", corpus="test", state=State(valid_at=CLOCK))
    response.current_memories = list(claims)
    for claim in claims:
        response.evidence.append(
            Evidence(
                id=claim.evidence_ids[0],
                claim_id=claim.id,
                version_id=claim.version_id,
                document_id="d" * 64,
                path=claim.path,
                source_hash="f" * 64,
                chunk_id="c" * 64,
                heading=None,
                text=claim.claim,
                start_offset=0,
                end_offset=len(claim.claim),
                observed_at=CLOCK,
            )
        )
    return response


def _order(question, claims, embedder):
    scores, _plan, _counters = memory_relevance.relevance(
        _response(claims),
        MemoryRequest(corpus="test", query=question),
        "current",
        embedder,
        memory_relevance.RelevancePolicy.configured(),
    )
    return scores


def test_a_claim_that_answers_more_of_the_question_ranks_above_a_fragment():
    """The fragment shares one word with the question and answers none of it.

    "embedding suite: `7 passed`;" is a four-word log line about a test run.
    The runtime.md claim carries "default" and "install". The log line scores
    higher on the embedding, and it used to win.
    """
    question = "How large is the default install and which embedding backend does it use?"
    fragment = _claim("log", "report.embedding", "embedding suite: `7 passed`;")
    answer = _claim(
        "install",
        "runtime.install",
        "A machine that will not take a model at all. The default install is 216 MB and a",
    )
    embedder = FakeEmbedder(
        scores={
            "embedding suite: `7 passed`; report.embedding docs/security.md": 0.51,
            (
                "A machine that will not take a model at all. The default install is 216 MB and a"
                " runtime.install docs/security.md"
            ): 0.42,
        },
        default=0.42,
    )
    order = _order(question, [fragment, answer], embedder)
    assert list(order) == [
        "runtime.install",
        "report.embedding",
    ], "the claim that answers two of the question's concepts comes first"


def _corpus(count, text, key="corpus.own"):
    """`count` claims that all say the same thing.

    A word in this many claims is the corpus's own vocabulary, and a question
    that names only that has not found a subject.
    """
    return [_claim(f"own{n}", f"{key}.{n}", text) for n in range(count)]


def test_a_question_about_a_subject_the_corpus_never_used_abstains():
    """Nothing in the corpus uses the word, so nothing can answer the question."""
    claims = _corpus(14, "Mind Palace is the memory system this corpus describes")
    claims.append(_claim("other", "corpus.other", "The runtime keeps its own state directory"))
    embedder = FakeEmbedder(default=0.55)
    scores = _order("How does Mind Palace use Kubernetes?", claims, embedder)
    assert scores == {}


def test_a_question_about_a_subject_the_corpus_has_still_answers():
    """The gate is about a subject the corpus has never used."""
    claims = _corpus(14, "Mind Palace is the memory system this corpus describes")
    claims.append(
        _claim(
            "kube",
            "corpus.kube",
            "Mind Palace uses Kubernetes for the batch scheduler and nothing else",
        )
    )
    embedder = FakeEmbedder(default=0.55)
    scores = _order("How does Mind Palace use Kubernetes?", claims, embedder)
    assert "corpus.kube" in scores


def test_a_question_whose_subject_is_the_corpus_own_topic_answers():
    """The corpus's own topic is not an absent subject, however common it is.

    "the Orders Service" is in a ninth of the dev corpus's claims. A question
    that names it has found its subject, and the answer comes back.
    """
    claims = _corpus(14, "Mind Palace is the memory system this corpus describes")
    claims.append(
        _claim(
            "orders",
            "orders.service",
            "The Orders Service runs the batch scheduler and the billing export",
        )
    )
    embedder = FakeEmbedder(default=0.55)
    scores = _order("Who is paged when the Orders Service breaks?", claims, embedder)
    assert scores, "a question about the corpus's own topic still answers"
