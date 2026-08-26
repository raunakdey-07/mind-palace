# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Raunak Dey

"""Markdown parser: extracts YAML frontmatter, body content, and heading structure."""

from __future__ import annotations

import datetime
import re
from typing import Any

import frontmatter
from pydantic import BaseModel, ConfigDict, Field, ValidationError


class FrontmatterSchema(BaseModel):
    """Validated frontmatter schema for Mind Palace documents."""

    title: str = Field(..., min_length=1)
    date: datetime.date
    tags: list[str] = Field(default_factory=list)
    document_type: str = Field(
        default="note",
        pattern=r"^(kaggle|project|note|paper)$",
    )
    competition: str = Field(default="")
    status: str = Field(default="active")
    git_repo: str = Field(default="")
    notebook: str = Field(default="")
    summary: str = Field(default="")

    model_config = ConfigDict(extra="allow")


def parse_markdown(raw: str) -> tuple[dict[str, Any], str]:
    """
    Backward-compatible parser.

    Returns:
        (metadata_dict, body_text)

    If no valid frontmatter exists, returns ({}, body).
    """

    try:
        post = frontmatter.loads(raw)
        raw_metadata = dict(post.metadata) if post.metadata else {}
        body = post.content.strip()
    except Exception:
        return {}, raw.strip()

    # No frontmatter present
    if not raw_metadata:
        return {}, body

    try:
        validated = FrontmatterSchema(**raw_metadata)
        metadata = validated.model_dump()
    except ValidationError:
        # Preserve legacy permissive behavior
        metadata = raw_metadata

    return metadata, body


def extract_title(metadata: dict[str, Any] | str) -> str:
    """
    Backward-compatible title extractor.

    Accepts either:
    - a metadata dict (legacy tests)
    - a raw markdown string
    """

    if isinstance(metadata, dict):
        title = metadata.get("title")
        return str(title) if title else "Untitled"

    if isinstance(metadata, str):
        meta, body = parse_markdown(metadata)

        title = meta.get("title")
        if title:
            return str(title)

        for line in body.splitlines():
            if line.startswith("# "):
                return line[2:].strip()

    return "Untitled"


def extract_sections_with_paths(text: str) -> list[tuple[str, str]]:
    """Extract sections with their full heading path."""

    lines = text.splitlines()
    sections: list[tuple[str, str]] = []

    current_heading_stack: list[tuple[int, str]] = []
    current_content: list[str] = []

    def flush_section() -> None:
        if current_heading_stack or current_content:
            heading_path = " > ".join(h for _, h in current_heading_stack)
            content = "\n".join(current_content).strip()

            if content or heading_path:
                sections.append((heading_path, content))

    for line in lines:
        heading_match = re.match(r"^(#{1,6})\s+(.+)$", line)

        if heading_match:
            flush_section()

            level = len(heading_match.group(1))
            heading_text = heading_match.group(2).strip()

            while current_heading_stack and current_heading_stack[-1][0] >= level:
                current_heading_stack.pop()

            current_heading_stack.append((level, heading_text))
            current_content = []
        else:
            current_content.append(line)

    flush_section()
    return sections


def estimate_tokens(text: str) -> int:
    """Rough token estimate (4 chars ≈ 1 token)."""

    return max(1, len(text) // 4)


def chunk_with_heading_paths(
    sections: list[tuple[str, str]],
    max_chars: int = 1200,
) -> list[dict[str, Any]]:
    """Convert sections into chunks with heading_path metadata."""

    chunks: list[dict[str, Any]] = []

    for heading_path, content in sections:
        if not content.strip() and not heading_path:
            continue

        if len(content) <= max_chars:
            chunks.append(
                {
                    "text": content,
                    "heading_path": heading_path,
                    "token_count": estimate_tokens(content),
                }
            )
            continue

        paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()]
        current_chunk = ""

        for para in paragraphs:
            # A single paragraph longer than max_chars must itself be split,
            # otherwise one huge paragraph produces an oversized chunk.
            while len(para) > max_chars:
                if current_chunk:
                    chunks.append(
                        {
                            "text": current_chunk,
                            "heading_path": heading_path,
                            "token_count": estimate_tokens(current_chunk),
                        }
                    )
                    current_chunk = ""
                cut = para[:max_chars]
                # Prefer breaking at a sentence/word boundary within the window.
                boundary = max(cut.rfind(". "), cut.rfind("\n"), cut.rfind(" "))
                if boundary > max_chars // 2:
                    cut = para[: boundary + 1]
                chunks.append(
                    {
                        "text": cut.strip(),
                        "heading_path": heading_path,
                        "token_count": estimate_tokens(cut),
                    }
                )
                para = para[len(cut) :].lstrip()

            candidate = para if not current_chunk else f"{current_chunk}\n\n{para}"

            if len(candidate) <= max_chars:
                current_chunk = candidate
            else:
                if current_chunk:
                    chunks.append(
                        {
                            "text": current_chunk,
                            "heading_path": heading_path,
                            "token_count": estimate_tokens(current_chunk),
                        }
                    )
                current_chunk = para

        if current_chunk:
            chunks.append(
                {
                    "text": current_chunk,
                    "heading_path": heading_path,
                    "token_count": estimate_tokens(current_chunk),
                }
            )

    return chunks
