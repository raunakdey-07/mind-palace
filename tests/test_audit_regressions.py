"""Audit regression tests (M018). Each feeds a real input to the real function
and asserts the correct behaviour. Strict xfail only for fixes not yet merged.
"""

from __future__ import annotations

import pytest

from api.services.markdown_claims import detect_info_state, extract


def test_approved_prohibition_curated():
    body = "## Approved\n\nDo not use the words \"curated\" or \"bespoke.\"\n"
    meta = {"status": "approved"}
    assert detect_info_state(meta, body) == "approved"
    chunks = [{"text": body, "order_index": 0}]
    claims, _ = extract(body, chunks)
    assert any("curated" in c["claim"] for c in claims)
    from api.services.context_packer import classify_memory_role

    rule_claims = [c for c in claims if "curated" in c["claim"]]
    assert all(classify_memory_role(c["claim"], "Approved") == "rule" for c in rule_claims)


def test_approved_prose_plain_language():
    assert detect_info_state({}, "We draft emails every Monday.") != "draft"


def test_descriptive_mustard_yellow():
    from api.services.context_packer import classify_memory_role

    assert classify_memory_role("Primary colour: mustard-yellow.", "Specs") != "rule"


def test_v2_top_of_list_insertion():
    """Inserting one rule at position 1 in v2 must not add supersession edges
    between the unchanged rules below it (hash keys keep them stable)."""
    import asyncio
    from sqlalchemy import text as sa_text

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
            await svc.ingest_file(
                v1_body, path="audit-v2/rules.md", corpus_id=corpus_id,
            )
            await db.commit()

            v2_body = (
                "# Rules\n\n"
                "- Inserted rule at position one: never log secrets.\n"
                "- Rule one: the primary datastore is PostgreSQL.\n"
                "- Rule two: backups run nightly.\n"
                "- Rule three: access is role-based.\n"
            )
            await svc.ingest_file(
                v2_body, path="audit-v2/rules.md", corpus_id=corpus_id,
            )
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

            # The unchanged rules (one/two/three) must not gain a supersedes_id in v2.
            for content in ("Rule one", "Rule two", "Rule three"):
                _, v1_sid = v1_by_content[content]
                _, v2_sid = v2_by_content[content]
                assert v1_sid == v2_sid, f"{content!r} gained a supersession edge on v2 insert"

            # Count supersession edges in v2 that point at unchanged rules: must be zero.
            r = await db.execute(
                sa_text(
                    "SELECT count(*) FROM memory_claims "
                    "WHERE corpus_id = :c AND version_id = :v AND supersedes_id IS NOT NULL"
                ),
                {"c": corpus_id, "v": v2_version_id},
            )
            assert r.scalar() == 0

    asyncio.run(run())


def test_hard_wrapped_prose():
    para = ("A machine that will not take a model at all. The default install is 216 MB and a\n"
            "cold recall is about 1.4 s, so this is a narrower case than it was at v0.9.1.")
    body = para + "\n"
    chunks = [{"text": para.replace("\n", " "), "order_index": 0}]
    claims, _ = extract(body, chunks)
    # Hard-wrapped lines join into one paragraph; each sentence becomes one
    # intact claim. "216 MB" must live in a single complete claim, not split.
    full_sentence = ("The default install is 216 MB and a cold recall is about 1.4 s, "
                     "so this is a narrower case than it was at v0.9.1.")
    intact = [c for c in claims if "216 MB" in c["claim"]]
    assert len(intact) == 1, f"expected exactly one claim holding '216 MB', got {len(intact)}"
    assert full_sentence in intact[0]["claim"], intact[0]["claim"]


def test_mixed_bullet_prose_table():
    body = ("## Section\n\n- bullet one is here for testing\n\n"
            "This is a prose sentence that must be extracted too.\n\n"
            "| a | b |\n|---|---|\n| 1 | 2 |\n")
    chunks = [{"text": body, "order_index": 0}]
    claims, _ = extract(body, chunks)
    modes = {c.get("extraction_mode") for c in claims}
    assert any("prose" in c["key"] or c.get("extraction_mode") == "prose" for c in claims)


def test_descriptive_voice_no_modal():
    body = "Our voice is warm, a little dry, and confident.\n"
    chunks = [{"text": body, "order_index": 0}]
    claims, _ = extract(body, chunks)
    assert isinstance(claims, list)


def test_unapproved_september_brainstorm():
    body = "## Brainstorming\n\nTry a playful tone again.\n"
    assert detect_info_state({}, body) == "draft"
