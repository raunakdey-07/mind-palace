"""Text chunking: splits Markdown into semantically coherent chunks."""

from __future__ import annotations

from typing import List

from langchain.text_splitter import RecursiveCharacterTextSplitter

DEFAULT_CHUNK_SIZE = 512
DEFAULT_CHUNK_OVERLAP = 64


def chunk_by_headings(text: str, max_chars: int = 1200) -> List[str]:
    """Split text by Markdown headings (#, ##, ###).

    Each chunk is a contiguous section under a heading.
    If a section exceeds max_chars, it is further split by size.
    """
    sections: List[str] = []
    current_section: List[str] = []
    current_size = 0

    for line in text.split("\n"):
        if line.startswith("#"):
            # Start a new section
            if current_section:
                sections.append("\n".join(current_section))
            current_section = [line]
            current_size = len(line)
        else:
            current_section.append(line)
            current_size += len(line) + 1  # +1 for newline
            if current_size > max_chars and len(current_section) > 1:
                sections.append("\n".join(current_section[:-1]))
                current_section = [current_section[-1]]
                current_size = len(current_section[0])

    if current_section:
        sections.append("\n".join(current_section))

    return [s.strip() for s in sections if s.strip()]


def chunk_by_size(
    text: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> List[str]:
    """Split text into fixed-size chunks with overlap using LangChain."""
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=["\n\n", "\n", ". ", " ", ""],
    )
    return splitter.split_text(text)


def chunk_document(
    text: str,
    strategy: str = "headings",
    **kwargs,
) -> List[str]:
    """Chunk a document using the chosen strategy.

    Strategies:
        - "headings": split by Markdown headings (default)
        - "size": fixed-size chunks with overlap
    """
    if strategy == "headings":
        return chunk_by_headings(text, **kwargs)
    return chunk_by_size(text, **kwargs)
