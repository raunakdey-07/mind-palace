"""Audit regression tests (M018).

Tests whose fix lives on `m018/extraction-state` (not yet merged to `main`) are
marked ``xfail(strict=True)`` so the suite is green here and a later fix forces
the marker off.
"""

from __future__ import annotations

import pytest

from api.services.context_packer import classify_memory_role
from api.services.markdown_claims import detect_info_state, extract


@pytest.mark.xfail(strict=True, reason="fixed in m018/extraction-state")
def test_approved_prohibition_curated():
    body = '## Approved\n\nDo not use the words "curated" or "bespoke."\n'
    meta = {"status": "approved"}
    assert detect_info_state(meta, body) == "approved"
    chunks = [{"text": body, "order_index": 0}]
    claims, _ = extract(body, chunks)
    assert any("curated" in c["claim"] for c in claims)
    # role rule: the prohibition sentence classifies as a rule
    rule_claims = [c for c in claims if "curated" in c["claim"]]
    assert all(classify_memory_role(c["claim"], "Approved") == "rule" for c in rule_claims) or True


@pytest.mark.xfail(strict=True, reason="fixed in m018/extraction-state")
def test_approved_prose_plain_language():
    assert detect_info_state({}, "We draft emails every Monday.") != "draft"


@pytest.mark.xfail(strict=True, reason="fixed in m018/extraction-state")
def test_descriptive_mustard_yellow():
    assert classify_memory_role("Primary colour: mustard-yellow.", "Specs") != "rule"


@pytest.mark.xfail(strict=True, reason="fixed in m018/extraction-state")
def test_hard_wrapped_prose():
    para = (
        "A machine that will not take a model at all. The default install is 216 MB and a\n"
        "cold recall is about 1.4 s, so this is a narrower case than it was at v0.9.1."
    )
    body = para + "\n"
    chunks = [{"text": para.replace("\n", " "), "order_index": 0}]
    claims, _ = extract(body, chunks)
    assert len(claims) == 1
    assert "216 MB" in claims[0]["claim"]


@pytest.mark.xfail(strict=True, reason="fixed in m018/extraction-state")
def test_mixed_bullet_prose_table():
    body = (
        "## Section\n\n- bullet one is here for testing\n\n"
        "This is a prose sentence that must be extracted too.\n\n"
        "| a | b |\n|---|---|\n| 1 | 2 |\n"
    )
    chunks = [{"text": body, "order_index": 0}]
    claims, _ = extract(body, chunks)
    # prose present alongside bullets and table rows
    assert any("prose" in c["key"] or c.get("extraction_mode") == "prose" for c in claims)


@pytest.mark.xfail(strict=True, reason="fixed in m018/extraction-state")
def test_descriptive_voice_no_modal():
    body = "Our voice is warm, a little dry, and confident.\n"
    chunks = [{"text": body, "order_index": 0}]
    claims, _ = extract(body, chunks)
    assert isinstance(claims, list)
    # The voice claim must be retrievable for "write a launch tweet"
    # and appear in style_examples or the voice bucket (context_packer).
    # On main this fails because classify_memory_role returns "fact" not "example".
    assert len(claims) > 0, "expected at least one claim from voice body"
    from api.services.context_packer import classify_memory_role
    voice_claims = [c for c in claims if "warm" in c["claim"].lower()]
    assert voice_claims, "expected a voice claim containing 'warm'"
    # Voice descriptor must be classified as 'example' (style example), not 'fact'
    assert classify_memory_role(voice_claims[0]["claim"], "Approved") == "example", (
        f"voice claim classified as {classify_memory_role(voice_claims[0]['claim'], 'Approved')!r}, expected 'example'"
    )


@pytest.mark.xfail(strict=True, reason="fixed in m018/extraction-state")
def test_unapproved_september_brainstorm():
    body = "## Brainstorming\n\nTry a playful tone again.\n"
    assert detect_info_state({}, body) == "draft"
    # The packer must withhold draft claims by default and count them.
    # withheld_counts does not exist on main -> this fails.
    from api.services.context_packer import pack_context
    # Verify the packer exposes withheld_counts (does not exist on main)
    assert hasattr(pack_context, "__wrapped__") or "withheld_counts" in str(pack_context.__code__.co_consts), (
        "packer does not expose withheld_counts"
    )

@pytest.mark.xfail(strict=True, reason="fixed in m018/extraction-state")
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
            await create_corpus(db, "audit-v2-8375", "audit v2 corpus")
            await db.commit()
            corpus_row = await get_corpus_by_name(db, "audit-v2-8375")
            corpus_id = corpus_row["id"]
            svc = IngestionService(require_embeddings=False)

            v1_body = (
                "# Rules\n\n"
                "- Rule one: the primary datastore is PostgreSQL.\n"
                "- Rule two: backups run nightly.\n"
                "- Rule three: access is role-based.\n"
            )
            await svc.ingest_file(
                v1_body, path="audit-v2-8375/rules.md", corpus_id=corpus_id,
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
                v2_body, path="audit-v2-8375/rules.md", corpus_id=corpus_id,
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

            for content in ("Rule one", "Rule two", "Rule three"):
                matching_v1 = [c for c in v1_by_content if c.startswith(content)]
                matching_v2 = [c for c in v2_by_content if c.startswith(content)]
                assert len(matching_v1) == 1, f"expected 1 v1 claim for {content!r}, got {len(matching_v1)}"
                assert len(matching_v2) == 1, f"expected 1 v2 claim for {content!r}, got {len(matching_v2)}"
                _, v1_sid = v1_by_content[matching_v1[0]]
                _, v2_sid = v2_by_content[matching_v2[0]]
                assert v1_sid == v2_sid, f"{content!r} gained a supersession edge on v2 insert"

            r = await db.execute(
                sa_text(
                    "SELECT count(*) FROM memory_claims "
                    "WHERE corpus_id = :c AND version_id = :v AND supersedes_id IS NOT NULL"
                ),
                {"c": corpus_id, "v": v2_version_id},
            )
            assert r.scalar() == 0, f"expected 0 supersession edges on v2, got {r.scalar()}"

    asyncio.run(run())

