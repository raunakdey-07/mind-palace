"""Audit regression tests (M018).

Each test asserts a behaviour that is wrong on main and is fixed on
m018/extraction-state. They are marked xfail(strict=True) so the suite is
green here and a later fix forces the marker off. Every test must fail on
main with an assertion about the behaviour named in its title -- not an
ImportError, AttributeError, KeyError, or fixture/setup error.
"""

from __future__ import annotations

import pytest

from api.services.context_packer import classify_memory_role, pack_context
from api.services.markdown_claims import detect_info_state, extract


@pytest.mark.xfail(
    strict=True,
    reason="MIND_PALACE_EXTRACTION_STATE_PROHIBITION_STATE",
)
def test_approved_prohibition_curated():
    """A prohibition sentence under an Approved heading is state=approved,
    not rejected.

    On main detect_info_state() keys off "do not use" in the body and
    classifies it as "rejected" even though metadata says approved.
    """
    body = '## Approved\n\nDo not use the words "curated" or "bespoke."\n'
    meta = {"status": "approved"}
    assert detect_info_state(meta, body) == "approved"


@pytest.mark.xfail(
    strict=True,
    reason="MIND_PALACE_EXTRACTION_STATE_PROSE_DRAFT",
)
def test_approved_prose_plain_language():
    """Plain-language prose is not draft state.

    On main detect_info_state() keys off "draft" in "We draft emails every
    Monday." and classifies it as "draft" even though it is a plain
    declarative statement.
    """
    assert detect_info_state({}, "We draft emails every Monday.") != "draft"


@pytest.mark.xfail(
    strict=True,
    reason="MIND_PALACE_EXTRACTION_STATE_DESCRIPTIVE_FACT",
)
def test_descriptive_mustard_yellow():
    """Descriptive colour text is not classified as "rule".

    On main classify_memory_role() matches "must" inside "mustard-yellow"
    and classifies it as "rule" even though it is a descriptive statement.
    """
    assert classify_memory_role("Primary colour: mustard-yellow.", "Specs") != "rule"


@pytest.mark.xfail(
    strict=True,
    reason="MIND_PALACE_EXTRACTION_STATE_HARD_WRAPPED_PROSE",
)
def test_hard_wrapped_prose():
    """A hard-wrapped paragraph produces one intact claim.

    On main the extractor splits wrapped lines instead of joining them
    into a single paragraph, so "216 MB" and its full sentence are split
    across multiple claims.
    """
    para = (
        "A machine that will not take a model at all. The default install is 216 MB and a\n"
        "cold recall is about 1.4 s, so this is a narrower case than it was at v0.9.1."
    )
    body = para + "\n"
    chunks = [{"text": para.replace("\n", " "), "order_index": 0}]
    claims, _ = extract(body, chunks)
    full_sentence = (
        "The default install is 216 MB and a cold recall is about 1.4 s, "
        "so this is a narrower case than it was at v0.9.1."
    )
    intact = [c for c in claims if "216 MB" in c["claim"]]
    assert len(intact) == 1, f"expected exactly one claim holding '216 MB', got {len(intact)}"
    assert full_sentence in intact[0]["claim"], intact[0]["claim"]


@pytest.mark.xfail(
    strict=True,
    reason="MIND_PALACE_EXTRACTION_STATE_MIXED_BULLET_PROSE_TABLE",
)
def test_mixed_bullet_prose_table():
    """Prose alongside bullets and tables is extracted as prose claims.

    On main prose text between bullets and tables is dropped instead of
    being extracted as a claim.
    """
    body = (
        "## Section\n\n- bullet one is here for testing\n\n"
        "This is a prose sentence that must be extracted too.\n\n"
        "| a | b |\n|---|---|\n| 1 | 2 |\n"
    )
    chunks = [{"text": body, "order_index": 0}]
    claims, _ = extract(body, chunks)
    modes = {c.get("extraction_mode") for c in claims}
    assert any("prose" in c.get("key", "") or c.get("extraction_mode") == "prose" for c in claims)


@pytest.mark.xfail(
    strict=True,
    reason="MIND_PALACE_EXTRACTION_STATE_VOICE_NOT_EXAMPLE",
)
def test_descriptive_voice_no_modal():
    """A voice statement with no modal verbs is retrievable and styled.

    On main classify_memory_role() classifies voice statements as "fact"
    instead of "example", so they are absent from style_examples in pack
    output for the query "write a launch tweet".
    """
    from datetime import datetime, timezone

    from api.models.memory import Claim, MemoryResponse

    # Build a MemoryResponse with a voice claim, as the real retrieval path
    # would produce after extraction.
    claim = Claim(
        id="voice-claim-1",
        key="voice",
        value="Our voice is warm, a little dry, and confident.",
        claim="Our voice is warm, a little dry, and confident.",
        status="CURRENT",
        version_id="voice-ver-1",
        path="voice.md",
        observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        evidence_ids=[],
    )
    memory = MemoryResponse(
        query="write a launch tweet",
        corpus="voice-corpus",
        current_memories=[claim],
    )

    # Run through the real pack path (memory= triggers _assemble_authoritative,
    # which classifies each claim via classify_memory_role and populates
    # task_context["style_examples"]).
    pack = pack_context("write a launch tweet", [], memory=memory, budget_tokens=8000)
    tc = pack.task_context or {}
    style_examples = tc.get("style_examples", [])
    voice_in_style = any("warm" in s.lower() for s in style_examples)
    assert voice_in_style, (
        f"voice sentence not in style_examples ({style_examples}); "
        f"on main classify_memory_role returns 'fact' not 'example'"
    )


@pytest.mark.xfail(
    strict=True,
    reason="MIND_PALACE_EXTRACTION_STATE_SEPTEMBER_WITHHELD",
)
def test_unapproved_september_brainstorm():
    """Brainstorm claims are draft, absent from default pack, counted in withheld_counts.

    On main draft claims are not withheld from the pack and there is no
    withheld_counts field on the pack output.
    """
    body = (
        "## Brainstorming\n\n- Try a playful tone again.\n\n"
        "## Approved\n\n- Do not use the word curated in any copy.\n"
    )
    chunks = [{"text": body, "order_index": 0}]
    claims, _ = extract(body, chunks)

    # (a) brainstorm claims have info_state draft
    brainstorm_claims = [c for c in claims if "playful tone" in c["claim"]]
    assert brainstorm_claims, "expected a brainstorm claim"
    # detect_info_state with the brainstorming section body returns "draft"
    state = detect_info_state({}, "## Brainstorming\n\n- Try a playful tone again.\n")
    assert state == "draft", f"brainstorm section state is {state!r}, expected 'draft'"

    # (b) draft claims are absent from the default pack
    results = [
        type(
            "Result",
            (),
            {
                "text": c["claim"],
                "source_path": "brainstorm.md",
                "doc_id": "bs-doc",
                "source_title": None,
                "heading_path": None,
                "source_document_type": None,
                "score": 1.0,
            },
        )()
        for c in claims
    ]
    pack = pack_context("tone", results, budget_tokens=8000)
    draft_in_pack = any("playful tone" in c.text for c in pack.chunks)
    assert not draft_in_pack, "draft claim should be withheld from default pack"

    # (c) pack reports withheld_counts
    pack_dict = pack.to_dict()
    assert pack_dict.get("withheld_counts") is not None, (
        "pack output does not report withheld_counts"
    )


@pytest.mark.xfail(
    strict=True,
    reason="MIND_PALACE_EXTRACTION_STATE_V2_SUPERSESSION_EDGES",
)
def test_v2_top_of_list_insertion():
    """Inserting one rule at position 1 in v2 adds no supersession edges
    between unchanged rules below it.

    On main ordinal keys create spurious supersession edges for unchanged
    claims when a new rule is inserted at the top.
    """
    import asyncio

    from api.services.db import session_scope
    from api.services.corpora import create_corpus, get_corpus_by_name
    from api.services.ingestion import IngestionService

    async def run():
        async with session_scope() as db:
            await create_corpus(db, "audit-v2", "audit v2 corpus")
            await db.commit()
            corpus_row = await get_corpus_by_name(db, "audit-v2")
            corpus_id = corpus_row["id"]
            svc = IngestionService(require_embeddings=False)

            v1_body = (
                "# Rules\n\n"
                "- Rule one: the primary datastore is PostgreSQL.\n"
                "- Rule two: backups run nightly.\n"
                "- Rule three: access is role-based.\n"
            )
            await svc.ingest_file(v1_body, path="audit-v2/rules.md", corpus_id=corpus_id)
            await db.commit()

            v2_body = (
                "# Rules\n\n"
                "- Inserted rule at position one: never log secrets.\n"
                "- Rule one: the primary datastore is PostgreSQL.\n"
                "- Rule two: backups run nightly.\n"
                "- Rule three: access is role-based.\n"
            )
            await svc.ingest_file(v2_body, path="audit-v2/rules.md", corpus_id=corpus_id)
            await db.commit()

            async def version_ids():
                r = await db.execute(
                    sa_text(
                        "SELECT id FROM memory_versions WHERE corpus_id = :c "
                        "ORDER BY version_number"
                    ),
                    {"c": corpus_id},
                )
                return [row[0] for row in r.fetchall()]

            from sqlalchemy import text as sa_text

            vids = await version_ids()
            assert len(vids) == 2, f"expected 2 versions, got {vids}"
            v1_version_id, v2_version_id = vids[0], vids[1]

            async def claim_rows(version_id):
                r = await db.execute(
                    sa_text(
                        "SELECT key, claim, supersedes_id FROM memory_claims "
                        "WHERE corpus_id = :c AND version_id = :v ORDER BY key"
                    ),
                    {"c": corpus_id, "v": version_id},
                )
                return r.fetchall()

            v1_by_content = {
                claim: (key, sid) for key, claim, sid in await claim_rows(v1_version_id)
            }
            v2_by_content = {
                claim: (key, sid) for key, claim, sid in await claim_rows(v2_version_id)
            }

            for content in ("Rule one", "Rule two", "Rule three"):
                matching_v1 = [c for c in v1_by_content if c.startswith(content)]
                matching_v2 = [c for c in v2_by_content if c.startswith(content)]
                assert len(matching_v1) == 1, (
                    f"expected 1 v1 claim for {content!r}, got {len(matching_v1)}"
                )
                assert len(matching_v2) == 1, (
                    f"expected 1 v2 claim for {content!r}, got {len(matching_v2)}"
                )
                _, v1_sid = v1_by_content[matching_v1[0]]
                _, v2_sid = v2_by_content[matching_v2[0]]
                assert v1_sid == v2_sid, (
                    f"{content!r} gained a supersession edge on v2 insert"
                )

            # memory_supersessions: count rows on v2 whose supersedes_id points
            # at an unchanged rule -- must be 0.
            r = await db.execute(
                sa_text(
                    "SELECT count(*) FROM memory_claims "
                    "WHERE corpus_id = :c AND version_id = :v AND supersedes_id IS NOT NULL"
                ),
                {"c": corpus_id, "v": v2_version_id},
            )
            assert r.scalar() == 0, f"expected 0 supersession edges on v2, got {r.scalar()}"

    asyncio.run(run())
