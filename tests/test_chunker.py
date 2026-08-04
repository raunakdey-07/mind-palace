"""Tests for the text chunker."""

from api.services.chunker import chunk_by_headings, chunk_by_size, chunk_document


def test_chunk_by_headings():
    text = """# Main Title

Some intro text.

## Section One

Content of section one.

## Section Two

Content of section two."""
    chunks = chunk_by_headings(text)
    assert len(chunks) >= 2
    assert any("Section One" in c for c in chunks)
    assert any("Section Two" in c for c in chunks)


def test_chunk_by_size():
    text = " ".join(["word"] * 600)
    chunks = chunk_by_size(text, chunk_size=200, chunk_overlap=50)
    assert len(chunks) >= 2
    assert all(len(c) <= 200 for c in chunks)


def test_chunk_document_default():
    text = "# Heading\n\nSome content here."
    chunks = chunk_document(text)
    assert len(chunks) >= 1


def test_chunk_document_headings_strategy():
    text = "# H1\n\nContent A.\n\n## H2\n\nContent B."
    chunks = chunk_document(text, strategy="headings")
    assert len(chunks) >= 2


def test_chunk_empty_text():
    chunks = chunk_by_headings("")
    assert chunks == []
