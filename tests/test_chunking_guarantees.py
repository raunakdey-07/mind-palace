"""Chunking guarantee tests: heading hierarchy, code blocks, long/short sections.

These target the parser's chunk_with_heading_paths / extract_sections_with_paths
pipeline actually used by ingestion (not the legacy chunker module).
"""

from __future__ import annotations

from api.services.parser import (
    chunk_with_heading_paths,
    estimate_tokens,
    extract_sections_with_paths,
)


def _chunk(md: str, max_chars: int = 1200):
    return chunk_with_heading_paths(extract_sections_with_paths(md), max_chars=max_chars)


def test_headings_preserved_in_chunks():
    md = "# Title\n\nIntro.\n\n## Alpha\n\nAlpha content.\n"
    chunks = _chunk(md)
    paths = [c["heading_path"] for c in chunks]
    assert "Title" in paths
    assert "Title > Alpha" in paths


def test_heading_hierarchy_nested_levels():
    md = "# A\n\n## B\n\n### C\n\nDeep content.\n"
    chunks = _chunk(md)
    assert any(c["heading_path"] == "A > B > C" for c in chunks)


def test_sibling_sections_do_not_leak():
    md = "## One\n\nContent one.\n\n## Two\n\nContent two.\n"
    chunks = _chunk(md)
    one = next(c for c in chunks if c["heading_path"] == "One")
    two = next(c for c in chunks if c["heading_path"] == "Two")
    assert "one" in one["text"] and "two" not in one["text"]
    assert "two" in two["text"] and "one" not in two["text"]


def test_code_blocks_kept_intact_within_chunk():
    code = "\n".join(f"line_{i} = {i}" for i in range(20))
    md = f"# Code Section\n\n```python\n{code}\n```\n"
    chunks = _chunk(md)
    assert len(chunks) == 1
    # no line of the fenced block may be lost
    for i in range(20):
        assert f"line_{i} = {i}" in chunks[0]["text"]


def test_long_section_split_respects_max_chars():
    paragraph = "word " * 100  # ~500 chars
    body = "\n\n".join([paragraph] * 6)  # ~3000 chars
    md = f"# Long\n\n{body}\n"
    chunks = _chunk(md, max_chars=800)
    assert len(chunks) > 1
    assert all(len(c["text"]) <= 800 for c in chunks)
    assert all(c["heading_path"] == "Long" for c in chunks)


def test_short_sections_become_single_chunks():
    md = "# Tiny\n\nHi.\n"
    chunks = _chunk(md)
    assert len(chunks) == 1
    assert chunks[0]["text"] == "Hi."
    assert chunks[0]["token_count"] >= 1


def test_lists_and_tables_retained():
    md = (
        "# Data\n\n- item a\n- item b\n- item c\n\n"
        "| col1 | col2 |\n|------|------|\n| x    | y    |\n"
    )
    chunks = _chunk(md)
    text = "\n".join(c["text"] for c in chunks)
    assert "- item a" in text
    assert "| x    | y    |" in text


def test_frontmatter_not_part_of_body():
    md = '---\ntitle: "T"\ndate: 2024-01-01\n---\n\n# Body\n\nText.\n'
    _, body = __import__("api.services.parser", fromlist=["parse_markdown"]).parse_markdown(md)
    assert "title:" not in body
    chunks = _chunk(body)
    assert all("frontmatter" not in c["text"] for c in chunks)


def test_empty_and_whitespace_documents():
    assert _chunk("") == []
    assert _chunk("\n\n   \n") == []


def test_content_without_any_headings():
    md = "Just some standalone prose with no headings at all."
    chunks = _chunk(md)
    assert len(chunks) == 1
    assert chunks[0]["heading_path"] == ""
    assert "standalone prose" in chunks[0]["text"]


def test_token_count_monotonic_with_length():
    short = _chunk("# S\n\nshort text\n")
    long_text = _chunk("# L\n\n" + ("longer text here " * 50) + "\n")
    assert short[0]["token_count"] < long_text[0]["token_count"]
    assert estimate_tokens("abcd") == 1
