"""Plain Markdown becomes memory, and re-ingesting it versions the source.

The bug this covers: an ADR, a runbook or a DECISIONS.md was chunked, stored and
searchable, and produced zero claims — so `recall` abstained on it. Nothing about
those files is missing that a structural reader cannot recover.

Two properties matter more than the extraction itself:

* every claim is quotable. Evidence that does not resolve to a character range of
  an archived chunk is not evidence, and the archive rejects it.
* identity is positional. A key derived from the claim text would change on every
  edit and leave the old claim un-superseded, joined by a second claim under a
  second key. That is the difference between "a decision changed" and "two
  decisions exist".
"""

import pytest

from api.services.markdown_claims import supersession_references, extract, has_claims
from api.services.parser import (
    chunk_with_heading_paths,
    extract_sections_with_paths,
    parse_markdown,
)

ADR = """# ADR 001: primary datastore

- The primary datastore is PostgreSQL 15.
- Writes go through the repository layer.
- We do not keep a second write path.

## Status

Status: Accepted
Superseded-by: none
Owner: platform team

## Consequences

1. Connection pooling happens in one place.
2. Backups are managed by the platform team.
"""


def chunks_for(body):
    return [
        {**chunk, "order_index": index}
        for index, chunk in enumerate(chunk_with_heading_paths(extract_sections_with_paths(body)))
    ]


@pytest.fixture
def claims():
    found, warnings = extract(ADR, chunks_for(ADR), path="adr-001.md")
    assert warnings == [], "the worked example must extract cleanly"
    return found


def test_every_declarative_line_becomes_a_claim(claims):
    texts = [c["claim"] for c in claims]
    assert "The primary datastore is PostgreSQL 15." in texts
    assert "Status: Accepted" in texts
    assert "Connection pooling happens in one place." in texts


def test_a_supersession_declaration_is_not_a_claim(claims):
    """It states a fact about this document, not about the world.

    `Superseded-by: adr-004.md` in the corpus is the thing that makes recall able
    to say this ADR is history. Returning it as a claim would let a question about
    what the ADR *decides* be answered with the line saying which ADR replaces it.
    """
    texts = [c["claim"] for c in claims]
    assert not any(text.casefold().startswith("superseded-by") for text in texts)
    assert not any(text.casefold().startswith("supersedes:") for text in texts)


def test_a_declaration_reads_as_a_reference_not_as_a_placeholder():
    """`Superseded-by: none` is the common case and must not resolve to anything."""
    backward, forward = supersession_references({}, ADR)
    assert backward == []
    # `none` is a placeholder, so nothing is recorded, and the line still does not
    # become a claim.
    assert forward == []


def test_a_key_value_line_keeps_its_value_separate(claims):
    """`Status: Accepted` is a claim about Accepted, not about the string."""
    status = next(c for c in claims if c["claim"] == "Status: Accepted")
    assert status["value"] == "Accepted"


def test_the_quote_is_a_substring_of_an_archived_chunk(claims):
    haystacks = [c["text"] for c in chunks_for(ADR)]
    for entry in claims:
        assert any(entry["evidence"] in hay for hay in haystacks), entry


def test_keys_are_scoped_by_heading_path():
    """The same sentence under two headings is two different facts."""
    line = "- Shared statement about the datastore."
    body = f"# T\n\n{line}\n\n## A\n\n{line}\n"
    found, _ = extract(body, chunks_for(body))
    assert len({c["key"] for c in found}) == 2


def test_editing_a_bullet_supersedes_it_in_place():
    """Identity is positional, so the key survives an edit. That is the point."""
    before = ADR.replace("PostgreSQL 15", "PostgreSQL 16")
    old = {c["key"]: c for c in extract(ADR, chunks_for(ADR))[0]}
    new = {c["key"]: c for c in extract(before, chunks_for(before))[0]}
    shared = set(old) & set(new)
    changed = [k for k in shared if old[k]["claim"] != new[k]["claim"]]
    assert len(changed) == 1, "only the edited bullet should change"
    assert "PostgreSQL 16" in new[changed[0]]["claim"]


def test_unchanged_text_produces_identical_claims():
    """Re-ingesting an unchanged file must be a no-op, or every sync writes a version."""
    assert extract(ADR, chunks_for(ADR))[0] == extract(ADR, chunks_for(ADR))[0]


def test_code_and_quotes_are_not_claims():
    body = (
        "# T\n\n"
        "- A real decision about the datastore.\n\n"
        "```\n"
        "- not a claim, it is an example command\n"
        "```\n\n"
        "> - not a claim either\n\n"
        "    - indented code, not a claim\n"
    )  # noqa: E501
    found, _ = extract(body, chunks_for(body))
    assert [c["claim"] for c in found] == ["A real decision about the datastore."]


def test_a_table_row_is_a_claim_and_says_which_column_it_is():
    """A row of pipes is not a sentence. The column name is what makes it a fact."""
    body = (
        "# T\n\n"
        "| | `sentence-transformers` | onnxruntime |\n"
        "|---|---|---|\n"
        "| `import` | 6.15 s | 0.40 s |\n"
    )
    found, _ = extract(body, chunks_for(body))
    assert [c["claim"] for c in found] == [
        "`import`: `sentence-transformers`: 6.15 s; onnxruntime: 0.40 s"
    ]


def test_a_table_quote_is_the_row_the_document_wrote():
    """The evidence has to be findable in the file byte for byte."""
    body = (
        "# T\n\n"
        "| | `sentence-transformers` | onnxruntime |\n"
        "|---|---|---|\n"
        "| `import` | 6.15 s | 0.40 s |\n"
    )
    found, _ = extract(body, chunks_for(body))
    assert [c["evidence"] for c in found] == ["| `import` | 6.15 s | 0.40 s |"]


def test_a_line_that_only_holds_a_pipe_is_not_a_table():
    """Without the separator row under it, a pipe is a paragraph that holds one."""
    body = "# T\n\n- A real decision about the datastore.\n\n| a | b |\n"
    found, _ = extract(body, chunks_for(body))
    assert [c["claim"] for c in found] == ["A real decision about the datastore."]


def test_the_document_title_is_context_not_a_claim():
    """An H1 with nothing above it is the document's name and states nothing."""
    found, _ = extract(ADR, chunks_for(ADR))
    assert not any(c["claim"] == "ADR 001: primary datastore" for c in found)


def test_a_short_fragment_is_not_a_claim():
    body = "# T\n\n- Yes.\n- A sentence that actually says something about storage.\n"
    found, _ = extract(body, chunks_for(body))
    assert [c["claim"] for c in found] == ["A sentence that actually says something about storage."]


def test_an_authored_claims_block_is_never_second_guessed():
    from api.services.markdown_claims import claims_for

    metadata, _body = parse_markdown(
        "---\n"
        "title: t\n"
        "claims:\n"
        "  - key: my.key\n"
        "    claim: c\n"
        "    value: v\n"
        "    evidence: c\n"
        "---\n\n"
        "# T\n\n"
        "- a bullet\n"
    )
    assert claims_for(metadata, "- a bullet", []) is None


def test_prose_with_no_structure_produces_nothing_and_says_so():
    body = "Just a paragraph. No list, no heading, no field."
    found, warnings = extract(body, chunks_for(body))
    assert found == []
    assert warnings == []
    assert has_claims(body) is False


def test_a_line_that_cannot_be_quoted_is_dropped_and_named():
    """A chunk boundary inside a line leaves no quotable evidence.

    The claim is dropped rather than stored with evidence nobody can verify, and
    the loss is reported with a line number and no document content.
    """
    line = "- A decision about the primary datastore and its replication setup."
    body = f"# T\n\n{line}\n"
    # Force the boundary: an archived chunk that ends mid-line, so the claim text
    # is not a substring of anything that was stored.
    chunks = [{"order_index": 0, "text": "A decision about the primary d"}]
    found, warnings = extract(body, chunks, path="t.md")
    assert found == []
    assert len(warnings) == 1
    assert warnings[0].code == "claim_extraction_no_quote"
    assert warnings[0].line == 3
    assert "decision" not in str(warnings[0].as_dict())
