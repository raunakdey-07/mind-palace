"""The five defects a real run against this repository's own documents found.

Every one of these was found by running the product against `docs/` and reading
the flight recorder, not by a test. The point of this file is that the next run
does not have to find them again.

The five:

1. A bullet wrapped over three lines was archived as its first line, so the
   claim was "The default install is 216 MB and a" and the rest of the
   sentence was gone.
2. A response of three claims reported a margin of `score - 0.0` and called it
   a clear answer, because the shortlist was taken from the top of the whole
   ranking rather than from the claims that were returned.
3. A question about a subject the corpus does not have answered with claims
   about the project's own name.
4. Every command asked the Hub about a model it already had, and printed a
   notice about tokens because of it.
5. The flight recorder recorded one claim id beside a response that displayed
   three.
"""

from __future__ import annotations

import socket
from datetime import datetime, timezone
from math import sqrt
from uuid import uuid4

import pytest

from api.models.memory import (
    Claim,
    Evidence,
    MemoryRequest,
    MemoryResponse,
    State,
)
from api.services import memory_public, memory_query, memory_relevance
from api.services.markdown_claims import extract
from api.services.parser import (
    chunk_with_heading_paths,
    extract_sections_with_paths,
)

try:
    from tests.test_security import _db_available
except ModuleNotFoundError as exc:  # pragma: no cover - direct invocation
    if exc.name not in {"tests", "tests.test_security"}:
        raise
    from test_security import _db_available

requires_db = pytest.mark.skipif(not _db_available(), reason="no reachable DATABASE_URL")

CLOCK = datetime(2024, 6, 15, 12, tzinfo=timezone.utc)


def chunks_for(body: str) -> list[dict]:
    return [
        {**chunk, "order_index": index}
        for index, chunk in enumerate(chunk_with_heading_paths(extract_sections_with_paths(body)))
    ]


# ---------------------------------------------------------------------------
# 1. A wrapped statement is one claim
# ---------------------------------------------------------------------------

WRAPPED_BULLET = """# Runtime

- A machine that will not take a model at all. The default install is 216 MB and a
  cold recall is about 1.4 s, so this is a narrower case than it was at v0.9.1,
  when a 1.35 GB install and a 6 s first recall were the price of ranking.
- A container with no GPU and no outbound network.
"""

WRAPPED_NUMBERED = """# Security

1. **Row-level security — migration `008`.** Every tenant-owned table carries a
   policy keyed on `corpus_id`, with `ENABLE` and `FORCE`. `chunks` has no
   corpus column, so its policy reaches the corpus through the document it
   belongs to.
"""

WRAPPED_FIELD = """# Database roles

`mindpalace_owner` holding `BYPASSRLS` is deliberate and is the honest limit of
this design: whoever holds it can read every tenant's rows. It is a maintenance
credential, not an application credential.
"""


def test_a_wrapped_bullet_is_one_claim_and_not_three():
    """The claim is the sentence, not the line the sentence starts on.

    Cutting it at the first newline kept "The default install is 216 MB and a"
    and threw away the rest, which is where the rest of the fact is.
    """
    claims, warnings = extract(WRAPPED_BULLET, chunks_for(WRAPPED_BULLET))
    assert warnings == []
    assert len(claims) == 2
    assert claims[0]["claim"] == (
        "A machine that will not take a model at all. The default install is 216 MB and a\n"
        "  cold recall is about 1.4 s, so this is a narrower case than it was at v0.9.1,\n"
        "  when a 1.35 GB install and a 6 s first recall were the price of ranking."
    )


def test_the_claim_value_carries_no_line_breaks():
    """A value is a short form, and a short form does not carry the layout."""
    claims, _ = extract(WRAPPED_BULLET, chunks_for(WRAPPED_BULLET))
    for claim in claims:
        assert "\n" not in claim["value"]


def test_the_quote_keeps_the_line_breaks_and_the_indentation():
    """Evidence has to be verifiable against the file, byte for byte.

    The quote is the raw slice, so it still has the line breaks the writer
    used. That is what makes `quote in source` true, and it is the only
    property that matters about evidence.
    """
    claims, _ = extract(WRAPPED_BULLET, chunks_for(WRAPPED_BULLET))
    quote = claims[0]["evidence"]
    assert "\n" in quote
    assert "  cold recall is about 1.4 s" in quote
    assert quote in WRAPPED_BULLET
    # And it is quotable out of an archived chunk, which is what the archive
    # checks before it accepts a claim.
    assert any(quote in chunk["text"] for chunk in chunks_for(WRAPPED_BULLET))


def test_a_wrapped_numbered_item_is_one_claim():
    claims, warnings = extract(WRAPPED_NUMBERED, chunks_for(WRAPPED_NUMBERED))
    assert warnings == []
    assert len(claims) == 1
    assert claims[0]["claim"].startswith("**Row-level security — migration `008`.**")
    assert claims[0]["claim"].rstrip().endswith("belongs to.")
    assert claims[0]["evidence"] in WRAPPED_NUMBERED


def test_a_wrapped_field_is_one_claim():
    """A `Key: Value` line in prose is a statement, and it wraps like one."""
    claims, warnings = extract(WRAPPED_FIELD, chunks_for(WRAPPED_FIELD))
    assert warnings == []
    assert len(claims) == 1
    assert claims[0]["value"] == (
        "whoever holds it can read every tenant's rows. It is a maintenance "
        "credential, not an application credential."
    )


def test_a_statement_is_not_split_across_two_chunks():
    """A chunk boundary inside a statement leaves its claim with no evidence.

    The archive needs the whole statement in one piece, and the chunker has to
    agree with the extractor about where a statement begins.
    """
    body = "# Long\n\n" + "".join(
        f"- Statement number {n} carries a fact that is long enough to matter.\n"
        for n in range(120)
    )
    chunks = chunks_for(body)
    claims, warnings = extract(body, chunks)
    assert warnings == []
    assert len(claims) == 120
    for claim in claims:
        assert any(claim["evidence"] in chunk["text"] for chunk in chunks)


def test_a_field_inside_a_wrapped_bullet_stays_in_the_bullet():
    """Prose in a bullet holds colons. Reading one as a field cuts the bullet.

    "Row-level security does not contain this: the process is the thing that
    sets the tenant scope" is one sentence in a bullet, not a field and a
    sentence.
    """
    body = (
        "# Threat model\n\n"
        "* An attacker who controls the application process **and** holds a\n"
        "  credential with `read_write` on a corpus **can read and write that\n"
        "  corpus.** Row-level security does not contain this: the process is\n"
        "  the thing that sets the tenant scope.\n"
    )
    claims, warnings = extract(body, chunks_for(body))
    assert warnings == []
    assert len(claims) == 1
    assert claims[0]["claim"].endswith("the thing that sets the tenant scope.")


@requires_db
async def test_a_resync_after_the_extractor_changes_updates_the_claims(monkeypatch):
    """A file whose bytes did not change still has to be read again.

    The extractor changed what a wrapped bullet means, so the file's SHA-256
    did not move and a raw-hash check would have skipped every file in the
    corpus. The version fingerprint covers the extracted claims, so the
    re-sync sees the difference and re-reads the document.
    """
    from api.services import markdown_claims
    from api.services.corpora import create_corpus
    from api.services.db import session_scope
    from api.services.ingestion import IngestionService

    body = "# Runtime\n\n- The default install is 216 MB and a\n  cold recall is 1.4 s.\n"
    path = "test/dogfood-resync.md"
    service = IngestionService()

    # A corpus of its own. The default corpus is shared, and a corpus that
    # holds memory history routes ingestion down a different path, which is
    # another test's premise.
    name = "dogfood-resync-" + uuid4().hex[:8]
    async with session_scope() as db:
        corpus = await create_corpus(db, name, "dogfood resync")
    corpus_id = corpus["id"]

    async with session_scope() as db:
        first = await service._ingest_content(db, body, path, corpus_id)
    assert first["success"]
    assert first.get("event") != "UNCHANGED"

    # The same file, read by an extractor that says something different. This
    # is what the old extractor produced: the first line of the bullet.
    real = markdown_claims.claims_for

    def other(metadata, text, chunks, *, path=""):
        derived, warnings = real(metadata, text, chunks, path=path)
        if derived and derived.get("claims"):
            first = derived["claims"][0]
            derived = {
                "claims": [
                    {
                        "key": first["key"],
                        "claim": "The default install is 216 MB and a",
                        "value": "The default install is 216 MB and a",
                        "evidence": "The default install is 216 MB and a",
                    }
                ]
            }
        return derived, warnings

    monkeypatch.setattr(markdown_claims, "claims_for", other)
    async with session_scope() as db:
        second = await service._ingest_content(db, body, path, corpus_id)
    assert second["success"]
    assert second.get("event") != "UNCHANGED", "the unchanged file was skipped"


# ---------------------------------------------------------------------------
# 2 and 5. The margin, the shortlist and the recorded claim ids
# ---------------------------------------------------------------------------


def _claim(identifier, key, value, evidence="e1"):
    return Claim(
        id=identifier,
        key=key,
        value=value,
        claim=f"{key} is {value}",
        status="CURRENT",
        version_id="a" * 64,
        path="adr.md",
        observed_at=CLOCK,
        evidence_ids=[evidence],
    )


def _evidence(identifier, text):
    return Evidence(
        id=identifier,
        claim_id=identifier,
        version_id="a" * 64,
        document_id="d" * 64,
        path="adr.md",
        source_hash="f" * 64,
        chunk_id="c" * 64,
        heading=None,
        text=text,
        start_offset=0,
        end_offset=len(text),
        observed_at=CLOCK,
    )


THREE = [
    _claim("a", "install", "216 MB", "e1"),
    _claim("b", "backend", "onnxruntime", "e2"),
    _claim("c", "cold recall", "1.4 s", "e3"),
]
QUOTES = [
    _evidence("e1", "The default install is 216 MB."),
    _evidence("e2", "The backend is onnxruntime."),
    _evidence("e3", "A cold recall is 1.4 s."),
]


def _adjudicate(scored, claims, evidence_rows):
    result = MemoryResponse(query="q", corpus="c", current_memories=claims)
    full = MemoryResponse(query="q", corpus="c", evidence=evidence_rows)
    memory_query.adjudicate(full, result, scored, memory_relevance.RelevancePolicy.configured())
    return result


def test_the_margin_is_measured_against_a_runner_up_the_caller_can_see():
    """The bug: three claims returned, one in the shortlist, margin `score - 0`.

    The shortlist was the top of the whole ranking, and the two keys above the
    response's own were not in the response. Filtering that shortlist to the
    response left one entry, and one entry has no runner-up, so the margin came
    out as the score itself and the answer was called clear.
    """
    scored = {
        "install": 0.50,
        "backend": 0.49,
        "cold recall": 0.48,
        "somewhere else": 0.99,
        "somewhere else again": 0.98,
    }
    result = _adjudicate(scored, THREE, QUOTES)
    assert [c.key for c in result.candidates] == ["install", "backend", "cold recall"]
    assert result.match_margin == pytest.approx(0.01)
    assert result.match_status == "ambiguous"


def test_a_shortlist_of_one_is_still_a_shortlist_of_one():
    """One claim, one key: the margin is the score and nothing competes."""
    scored = {"install": 0.50, "somewhere else": 0.99}
    result = _adjudicate(scored, THREE[:1], QUOTES[:1])
    assert [c.key for c in result.candidates] == ["install"]
    assert result.match_margin == pytest.approx(0.50)


def test_the_flight_recorder_records_the_claims_that_were_returned():
    """The row described the ranking, not the answer.

    A response that displayed three claims recorded one id, so a verdict filed
    against the answer was graded against a claim the caller never saw.
    """
    response = MemoryResponse(query="q", corpus="c", current_memories=THREE)
    outcome = memory_public._logged_outcome(response)
    assert outcome["returned_claim_ids"] == ["a", "b", "c"]
    assert outcome["top_scores"] == []


def test_the_flight_recorder_still_records_a_ranked_response():
    """The ranking signal is still recorded, and it is still the ranking."""
    result = _adjudicate({"install": 0.50, "backend": 0.49, "cold recall": 0.48}, THREE, QUOTES)
    outcome = memory_public._logged_outcome(result)
    assert outcome["top_scores"] == [0.5, 0.49, 0.48]
    assert outcome["returned_claim_ids"] == ["a", "b", "c"]
    assert outcome["match_status"] == "ambiguous"


# ---------------------------------------------------------------------------
# 3. A question about a subject the corpus does not have
# ---------------------------------------------------------------------------


class FakeEmbedder:
    """Prescribed cosine scores against [1, 0], independent of language meaning."""

    def __init__(self, scores=None, default=1.0):
        self.scores = scores or {}
        self.default = default
        self.calls = []

    def embed(self, texts):
        self.calls.append(list(texts))
        scores = [self.scores.get(text, self.default) for text in texts[1:]]
        return [[1.0, 0.0]] + [[score, sqrt(1 - score**2)] for score in scores]


def _corpus_claim(identifier, key, text, path="docs/runtime.md"):
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


def _corpus_response(claims):
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


def _many_claims(prefix, count, text):
    """`count` claims that all carry the same words, so those words are common."""
    return [
        _corpus_claim(f"{prefix}{n}", f"{prefix}.{n}", f"{text} number {n}.") for n in range(count)
    ]


def test_a_subject_the_corpus_barely_mentions_does_not_answer():
    """The corpus's best answer to the question is about something else.

    "Kafka" is in the corpus, in one claim that the ranking puts far below
    the claims that matched on the project's own name. The question is about
    Kafka, and the corpus's best answer to it is not, so the honest answer is
    that the corpus does not have one.
    """
    claims = _many_claims("own", 120, "Mind Palace is the memory system this corpus describes")
    claims = claims + [
        _corpus_claim(
            "kafka",
            "feed.kafka",
            "Kafka, CDC, or message-broker semantics;",
            path="docs/feed.md",
        )
    ]
    response = _corpus_response(claims)
    embedder = FakeEmbedder(
        scores={"Kafka, CDC, or message-broker semantics; feed.kafka docs/feed.md": 0.39},
        default=0.52,
    )
    request = MemoryRequest(corpus="test", query="How is Kafka configured in Mind Palace?")
    scores, _, _ = memory_relevance.relevance(
        response,
        request,
        "current",
        embedder,
        memory_relevance.RelevancePolicy.configured(),
    )
    assert scores == {}, "the corpus's best answer is not about the subject"


def test_a_question_about_a_subject_the_corpus_has_still_answers():
    """The gate is about an absent subject, not about being harder to reach."""
    claims = _many_claims("own", 120, "Mind Palace is the memory system this corpus describes")
    claims = claims + [
        _corpus_claim(
            "kafka",
            "feed.kafka",
            "Kafka is configured with twelve partitions in this deployment.",
            path="docs/feed.md",
        )
    ]
    response = _corpus_response(claims)
    embedder = FakeEmbedder(default=0.52)
    request = MemoryRequest(corpus="test", query="How is Kafka configured in Mind Palace?")
    scores, _, _ = memory_relevance.relevance(
        response,
        request,
        "current",
        embedder,
        memory_relevance.RelevancePolicy.configured(),
    )
    assert "feed.kafka" in scores


def test_a_question_the_corpus_answers_strongly_is_not_an_abstention():
    """The gate is about the subject, not about the score.

    "Which architecture decisions apply?" names a word the corpus never uses,
    and the answer is still the architecture decisions.
    """
    claims = _many_claims("own", 120, "The architecture decisions are recorded here")
    response = _corpus_response(claims)
    embedder = FakeEmbedder(default=0.9)
    request = MemoryRequest(corpus="test", query="Which architecture decisions apply?")
    scores, _, _ = memory_relevance.relevance(
        response,
        request,
        "current",
        embedder,
        memory_relevance.RelevancePolicy.configured(),
    )
    assert scores, "a question the corpus answers well is not an abstention"


# ---------------------------------------------------------------------------
# 4. The Hub is not asked about a model that is already on disk
# ---------------------------------------------------------------------------


def test_the_model_is_loaded_from_the_local_cache(monkeypatch):
    """A warm cache does not need the network, and the network is the fallback.

    The loader asked the Hub about the model and the tokenizer on every
    command, which cost a round trip each time and brought back a notice
    about tokens.
    """
    from api.services import embedder

    calls = []

    def fake_download(repo, filename, **kwargs):
        calls.append((filename, kwargs.get("local_files_only")))
        return f"/cache/{filename}"

    import huggingface_hub

    monkeypatch.setattr(huggingface_hub, "hf_hub_download", fake_download)
    monkeypatch.setattr(
        embedder, "_load_tokenizer", lambda repo, *, local_files_only: _FakeTokenizer()
    )
    monkeypatch.setattr(
        embedder._OnnxBackend, "_session", property(lambda self: _FakeSession()), raising=False
    )
    try:
        embedder._OnnxBackend("all-MiniLM-L6-v2")
    except Exception:
        pass
    assert calls, "the model file is resolved through the hub"
    assert calls[0][1] is True, "the local cache is tried first"


class _FakeTokenizer:
    def enable_truncation(self, **kwargs):
        return None

    def enable_padding(self, **kwargs):
        return None


class _FakeSession:
    def get_inputs(self):
        return []

    def get_outputs(self):
        return []


def test_the_onnx_loader_makes_no_network_request(monkeypatch):
    """Nothing in the load may open a socket."""
    from api.services import embedder

    def refuse(self, address):
        raise OSError(f"network access is not allowed: {address}")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    try:
        backend = embedder._OnnxBackend("all-MiniLM-L6-v2", local_files_only=True)
    except OSError as error:  # pragma: no cover - the model is not cached here
        pytest.fail(f"the loader reached the network: {error}")
    except Exception:
        # The model is not on this machine; the point of the test is that the
        # failure was not a network attempt.
        return
    assert backend is not None


def test_the_hub_notice_is_silenced_while_the_model_loads(monkeypatch):
    """The notice was about rate limits, on a tool that never needed the Hub."""
    import logging

    from api.services import embedder

    seen = []

    class Recorder(logging.Handler):
        def emit(self, record):
            seen.append(record)

    handler = Recorder()
    logger = logging.getLogger("huggingface_hub.utils._http")
    logger.addHandler(handler)
    try:
        with embedder._quiet_hub():
            logger.warning("Warning: You are sending unauthenticated requests to the HF Hub.")
        assert seen == [], "the notice reached the handler"
    finally:
        logger.removeHandler(handler)


def test_the_quiet_hub_restores_the_logging_levels(monkeypatch):
    """A loader that silences a logger has to give it back."""
    import logging

    from api.services import embedder

    logger = logging.getLogger("huggingface_hub")
    before = logger.level
    with embedder._quiet_hub():
        assert logger.level == logging.ERROR
    assert logger.level == before
