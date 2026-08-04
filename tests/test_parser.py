"""Tests for the Markdown parser."""

from api.services.parser import extract_title, parse_markdown


def test_parse_frontmatter_and_body(sample_markdown):
    metadata, body = parse_markdown(sample_markdown)
    assert metadata["title"] == "Test Project"
    assert metadata["date"].year == 2024
    assert "test" in metadata["tags"]
    assert "ai" in metadata["tags"]
    assert "Introduction" in body
    assert "Methods" in body


def test_parse_without_frontmatter():
    raw = "# Just a heading\n\nSome content here."
    metadata, body = parse_markdown(raw)
    assert metadata == {}
    assert "Just a heading" in body


def test_extract_title_with_metadata():
    metadata = {"title": "My Project"}
    assert extract_title(metadata) == "My Project"


def test_extract_title_without_metadata():
    metadata = {}
    assert extract_title(metadata) == "Untitled"


def test_parse_empty_content():
    metadata, body = parse_markdown("")
    assert metadata == {}
    assert body == ""
