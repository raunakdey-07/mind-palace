"""The repository's own documents survive their own ingestion.

Both failures here were found by dogfooding, not by a test, and both are silent in
the way that matters. A document that fails to ingest is reported as `!1` in a sync
summary nobody reads, and a document that ingests while dropping one claim looks
exactly like one that did not.

* `docs/authoring.md` explains the supersession syntax in running prose, so lines in
  it begin with a literal `` `Supersedes:` ``. The declaration scanner tolerated
  inline code around the directive, took the rest of the sentence as a reference to
  retire, and refused the document. The documentation of the feature broke the
  feature. The declaration is now anchored: an inline-code mention is prose.

* `README.md` produced `claim_extraction_no_quote` and lost a claim. The chunker
  broke a long paragraph on the last *space* rather than the last *newline*, so a
  section boundary landed inside a bullet and the archived chunks held only
  fragments of it. Evidence must be verbatim out of a chunk, so the claim was
  dropped. The chunker now breaks on a line boundary, and a quote is a slice of
  the raw source line by construction.

These tests read the real files. A rewritten README is the input, and a synthetic
fixture would go stale the moment somebody edited a paragraph.
"""

import os

import pytest

from api.services.markdown_claims import is_declaration, supersession_references
from api.services.parser import (
    chunk_with_heading_paths,
    extract_sections_with_paths,
    parse_markdown,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: The four operator and architecture documents this corpus is built from.
DOGFOOD_DOCS = ("README.md", "docs/authoring.md", "docs/runtime.md", "docs/mcp.md")


def _archive(path: str):
    """The claims and warnings the archive would produce for one real document."""
    from api.services.markdown_claims import claims_for

    metadata, body = parse_markdown(_read(path))
    records = [
        {**chunk, "order_index": i}
        for i, chunk in enumerate(chunk_with_heading_paths(extract_sections_with_paths(body)))
    ]
    result = claims_for(metadata, body, records, path=path)
    if result is None:  # an authored `claims:` block; the extractor is not consulted
        return [], []
    derived, warnings = result
    return derived.get("claims", []), warnings


def _read(path: str) -> str:
    full = os.path.join(ROOT, path)
    if not os.path.exists(full):
        pytest.skip(f"{path} is not in this checkout")
    with open(full, encoding="utf-8") as handle:
        return handle.read()


def _source_of(path: str) -> str:
    with open(os.path.join(ROOT, path), encoding="utf-8") as handle:
        return handle.read()


# --------------------------------------------------------------------------
# docs/authoring.md: a line that talks about the directive is not a directive
# --------------------------------------------------------------------------


def test_the_authoring_guide_produces_no_warning_at_all():
    """The document that documents supersession is the one that broke it."""
    claims, warnings = _archive("docs/authoring.md")
    assert [w.as_dict() for w in warnings] == []
    assert len(claims) > 10, "the guide must still produce its claims, not just stop failing"


def test_prose_that_mentions_the_directive_declares_nothing():
    """The regression itself: line 383 of docs/authoring.md.

    The sentence is one clause of running prose. Reading the rest of it as a
    reference is how the document came to name a claim key that has never existed.

    The guide declares nothing about itself, and the assertion is that rather than
    "some declarations": its one `supersedes:` line is the `0001-use-postgres15.md`
    worked example, inside a ```` ```markdown ```` fence, which `_iter_lines`
    already skips. Before the fix this file named four phantom references, the
    first of which aborted the ingest.
    """
    _metadata, body = parse_markdown(_source_of("docs/authoring.md"))
    assert supersession_references({}, body) == ([], [])


@pytest.mark.parametrize(
    "line",
    [
        "`supersedes:` in frontmatter, or a `Supersedes:` line in the body",
        '`Supersedes: 0002` does not mean "whatever file has 0002 in the name"',
        "A declaration line is not a claim. `Supersedes: adr-001.md` says something",
        "the `superseded-by:` line for the forward direction",
    ],
)
def test_an_inline_code_mention_is_prose(line):
    assert not is_declaration(line)
    assert supersession_references({}, line) == ([], [])


@pytest.mark.parametrize(
    "line, value",
    [
        ("Supersedes: adr/0001.md", "adr/0001.md"),
        ("supersedes: 0001.md", "0001.md"),
        ("- Supersedes: 0001.md", "0001.md"),
        ("* Supersedes: 0001.md", "0001.md"),
        ("+ Supersedes: 0001.md", "0001.md"),
        ("**Supersedes**: 0001.md", "0001.md"),
        ("__Supersedes__: 0001.md", "0001.md"),
        ("  Superseded-by: adr/0002.md", "adr/0002.md"),
        ("Superseded_by: adr/0002.md", "adr/0002.md"),
    ],
)
def test_the_three_real_spellings_still_parse(line, value):
    """Anchoring is only a fix if the declarations that worked still work.

    The list marker includes `+` because `_BULLET` accepts it. A `+ Supersedes:`
    line that parses as a declaration is consistent; one that parses as a claim is
    a supersession nobody retires, quietly.
    """
    supersedes, superseded_by = supersession_references({}, line)
    found = supersedes or superseded_by
    assert found == [value], f"{line!r} should declare {value!r}, got {found!r}"


def test_a_declaration_in_frontmatter_needs_no_body_line():
    """Frontmatter is a different reader and must keep working."""
    supersedes, superseded_by = supersession_references(
        {"supersedes": "adr/0001.md", "superseded_by": ["adr/0002.md"]}, ""
    )
    assert supersedes == ["adr/0001.md"]
    assert superseded_by == ["adr/0002.md"]


# --------------------------------------------------------------------------
# README.md: every quote is a slice of the raw source
# --------------------------------------------------------------------------


def test_the_readme_produces_no_warning_at_all():
    """It reported `claim_extraction_no_quote` and silently lost a claim."""
    claims, warnings = _archive("README.md")
    assert [w.as_dict() for w in warnings] == []
    assert len(claims) >= 11


@pytest.mark.parametrize("path", DOGFOOD_DOCS)
def test_every_quote_is_an_exact_slice_of_the_raw_source(path):
    """The guarantee, checked against the file rather than against a fixture.

    `_quote_for` locates the quote in the source line rather than reconstructing
    it, so a claim cannot be archived with evidence the document does not contain.
    """
    source = _source_of(path)
    claims, _warnings = _archive(path)
    for claim in claims:
        quote = claim["evidence"]
        assert quote, f"{path}: {claim['key']} has empty evidence"
        assert quote in source, f"{path}: {claim['key']} quotes text the file does not contain"


@pytest.mark.parametrize("path", DOGFOOD_DOCS)
def test_no_chunk_boundary_falls_inside_a_quotable_line(path):
    """The chunker broke paragraphs on a space, so a chunk held half a bullet.

    This is the cause of the dropped claim rather than a symptom of it: evidence is
    located in an archived chunk, and a chunk that starts mid-line cannot supply
    the quote for that line no matter what the extractor does.
    """
    _metadata, body = parse_markdown(_source_of(path))
    chunks = [c["text"] for c in chunk_with_heading_paths(extract_sections_with_paths(body))]
    claims, _warnings = _archive(path)
    unquotable = [
        claim["key"] for claim in claims if not any(claim["evidence"] in chunk for chunk in chunks)
    ]
    assert unquotable == []
