"""Markdown parser: extracts YAML frontmatter and body content."""

from __future__ import annotations

from typing import tuple

import frontmatter


def parse_markdown(raw: str) -> tuple[dict, str]:
    """Parse a Markdown file into (metadata_dict, body_text).

    Uses `python-frontmatter` to extract YAML frontmatter.
    Falls back to empty metadata and the full raw text if parsing fails.
    """
    try:
        post = frontmatter.loads(raw)
        metadata = dict(post.metadata) if post.metadata else {}
        body = post.content.strip()
    except Exception:
        metadata = {}
        body = raw.strip()

    # Normalize common frontmatter fields
    if "tags" in metadata and isinstance(metadata["tags"], list):
        metadata["tags"] = [str(t) for t in metadata["tags"]]
    if "date" in metadata and isinstance(metadata["date"], str):
        from datetime import date

        try:
            metadata["date"] = date.fromisoformat(metadata["date"])
        except (ValueError, TypeError):
            pass

    return metadata, body


def extract_title(metadata: dict, fallback: str = "Untitled") -> str:
    """Return the title from metadata, or a fallback."""
    return str(metadata.get("title") or fallback)
