"""Ingestion must not report success when it stored nothing.

## The failure

During the first-hour audit this repository's own documentation was archived into
a corpus and then asked ten questions. The sync reported:

```
added=11 changed=0 unchanged=0
success=True
```

Eleven documents, and **zero claims** in the archive. Every question abstained.

The cause was that the frontmatter generator had emitted YAML PyYAML could not
parse -- an unquoted scalar containing a colon. `parse_markdown` caught the
exception and returned `{}`, which is indistinguishable from a document that has
no frontmatter, so the `claims:` block vanished. The documents were still
archived, as text, and were still searchable through the lexical index. The
author saw `success=True`.

A failure that reports success is worse than a failure that reports failure,
because it is trusted.

## What is asserted here

* `parse_markdown` reports *why* it could not use the frontmatter, with a stable
  reason code, a line number, and **no content** -- the error message from PyYAML
  embeds the offending YAML and must never be propagated.
* A document with no frontmatter at all is **not** a warning. Absence is not a
  defect, and a diagnostic that fires on ordinary input is one people learn to
  ignore.
* `sync` reports documents *and* claims, marks `success` false when any document
  had a frontmatter problem, and gives a per-document outcome with a reason code.
* A document that declares no claims is flagged `no_claims_declared` rather than
  being silently counted as a success. Plain prose is legitimate, so this is not
  a failure -- but it is also not the same thing as a document that produced
  memory, and only one of those two answers the question somebody asked.
"""

from __future__ import annotations

import os

import pytest

from api.services.parser import FrontmatterWarning, parse_markdown

pytestmark = [pytest.mark.asyncio]

#: Unquoted scalars containing a colon. PyYAML rejects this; a hand-written
#: claims block hits it constantly.
BROKEN = (
    "---\n"
    "title: doc.md\n"
    "claims:\n"
    "  - key: a.1\n"
    "    claim: The team moved: off the earlier engine\n"
    "    value: The team moved\n"
    "    evidence: The team moved\n"
    "---\n"
    "\n"
    "# Body\n\nThe team moved off the earlier engine.\n"
)


def test_broken_frontmatter_is_reported_not_swallowed():
    metadata, _body, warnings = parse_markdown(BROKEN, path="doc.md", collect_warnings=True)
    assert metadata == {}, "unparseable frontmatter must not be half-applied"
    assert [w.code for w in warnings] == [FrontmatterWarning.UNPARSEABLE]
    assert warnings[0].path == "doc.md"
    assert isinstance(warnings[0].line, int) and warnings[0].line >= 1


def test_the_warning_never_carries_document_content():
    """The reason code and the line, never the text.

    PyYAML's own message quotes the offending line. Frontmatter is where a
    developer puts titles and summaries, so a diagnostic that echoes it is a
    channel for whatever was in there -- and the existing redaction suite treats
    that as a defect, not a style preference.
    """
    _m, _b, warnings = parse_markdown(BROKEN, path="doc.md", collect_warnings=True)
    rendered = repr(warnings[0].as_dict()) + repr(warnings[0])
    for leak in ("The team moved", "off the earlier engine", "claims", "scan", "alias"):
        assert leak not in rendered, f"the warning leaked document content: {leak!r}"
    assert set(warnings[0].as_dict()) == {"code", "path", "line", "field"}


def test_the_two_argument_form_still_works():
    """Every existing caller wants `(metadata, body)`; the warnings are opt-in."""
    result = parse_markdown(BROKEN, path="doc.md")
    assert len(result) == 2
    assert result[0] == {}


def test_a_plain_markdown_file_is_not_a_warning():
    """Otherwise every Markdown file in the world is flagged and the flag is noise."""
    _m, _b, warnings = parse_markdown("# Just markdown\n", path="plain.md", collect_warnings=True)
    assert warnings == []


def test_valid_frontmatter_produces_no_warnings():
    good = (
        "---\ntitle: doc.md\nclaims:\n  - key: a.1\n    claim: The team moved\n"
        "    value: The team moved\n    evidence: The team moved\n---\n\nbody The team moved\n"
    )
    metadata, _b, warnings = parse_markdown(good, path="doc.md", collect_warnings=True)
    assert metadata and metadata["claims"]
    assert warnings == []


def test_claims_that_are_not_a_list_is_reported():
    odd = "---\ntitle: x\nclaims: not-a-list\n---\n\nbody\n"
    _m, _b, warnings = parse_markdown(odd, path="odd.md", collect_warnings=True)
    assert [w.code for w in warnings] == [FrontmatterWarning.CLAIMS_NOT_A_LIST]


def test_a_missing_title_is_reported():
    """`title` is the one required field, so its absence is worth a line."""
    untitled = "---\nsummary: something\n---\n\nbody\n"
    _m, _b, warnings = parse_markdown(untitled, path="u.md", collect_warnings=True)
    assert [w.code for w in warnings] == [FrontmatterWarning.MISSING_TITLE]


@pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="no DATABASE_URL")
async def test_sync_of_unparseable_documents_reports_zero_claims_and_fails(tmp_path):
    """The exact dogfood failure, reproduced.

    Before the fix this returned `success=True` with eleven documents archived and
    no claims at all. The assertion is deliberately on the *summary*, not on the
    per-document detail: a caller who reads only the summary must still be able
    to tell that nothing became memory.
    """
    from sqlalchemy import text

    from api.services import authz
    from api.services import db as db_module
    from api.services.corpora import corpus_id_for_name
    from api.services.ingestion import IngestionService

    for index in range(11):
        (tmp_path / f"doc{index:02d}.md").write_text(BROKEN.replace("doc.md", f"doc{index:02d}.md"))

    corpus_id = corpus_id_for_name("test_unparseable_sync")
    async with db_module.admin_session_scope() as db:
        with db_module.principal_scope(authz.ADMIN_PRINCIPAL):
            await db.execute(
                text("INSERT INTO corpora (id, name) VALUES (:i, :n) ON CONFLICT DO NOTHING"),
                {"i": corpus_id, "n": "test_unparseable_sync"},
            )
        await db.commit()

    service = IngestionService(memory_enabled=True, require_embeddings=False)
    summary = await service.sync_repo(str(tmp_path), corpus_id, delete_removed=False)

    assert summary["documents"] == 11
    assert summary["claim_count"] == 0, "unparseable frontmatter cannot produce claims"
    assert (
        summary["success"] is False
    ), "a sync that stored eleven documents and no memory must not report success"
    assert summary["failed"] == 0, "the documents were archived; they are not failures"
    assert len(summary["warnings"]) == 11
    assert {w["code"] for w in summary["warnings"]} == {FrontmatterWarning.UNPARSEABLE}
    assert all(w["path"].startswith("doc") for w in summary["warnings"])
    assert len(summary["document_results"]) == 11
    assert all(d["claims"] == 0 for d in summary["document_results"])


@pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="no DATABASE_URL")
async def test_sync_flags_documents_that_declare_no_claims(tmp_path):
    """Legitimate prose is not a failure, but it is not silently equal to memory."""
    from sqlalchemy import text

    from api.services import authz
    from api.services import db as db_module
    from api.services.corpora import corpus_id_for_name
    from api.services.ingestion import IngestionService

    (tmp_path / "prose.md").write_text("---\ntitle: prose.md\n---\n\nSome notes, no claims.\n")
    corpus_id = corpus_id_for_name("test_no_claims_sync")
    async with db_module.admin_session_scope() as db:
        with db_module.principal_scope(authz.ADMIN_PRINCIPAL):
            await db.execute(
                text("INSERT INTO corpora (id, name) VALUES (:i, :n) ON CONFLICT DO NOTHING"),
                {"i": corpus_id, "n": "test_no_claims_sync"},
            )
        await db.commit()

    service = IngestionService(memory_enabled=True, require_embeddings=False)
    summary = await service.sync_repo(str(tmp_path), corpus_id, delete_removed=False)

    assert summary["documents"] == 1
    assert summary["claim_count"] == 0
    assert summary["without_claims"] == 1
    assert summary["failed"] == 0
    assert summary["document_results"][0]["reason"] == "no_claims_declared"
