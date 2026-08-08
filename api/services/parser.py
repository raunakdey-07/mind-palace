"""Markdown parser: extracts YAML frontmatter, body content, and heading structure."""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass

import frontmatter
from pydantic import BaseModel, ConfigDict, ValidationError


class FrontmatterSchema(BaseModel):
    title: str | None = None
    date: datetime.date | None = None
    tags: list[str] = []
    status: str | None = None
    summary: str | None = None

    model_config = ConfigDict(extra="allow")


def extract_title(metadata: dict) -> str:
    title = metadata.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()
    return "Untitled"


@dataclass
class ParsedDocument:
    """Parsed Markdown document with metadata and structured content."""

    frontmatter: FrontmatterSchema
    body: str
    sections: list[tuple[str, str]]  # (heading_path, content)


def parse_markdown(raw: str) -> tuple[dict, str]:
    """Parse a Markdown file into validated frontmatter and body text."""

    try:
        post = frontmatter.loads(raw)
        raw_metadata = dict(post.metadata) if post.metadata else {}
        body = post.content.strip()
    except Exception:  # noqa: BLE001
        raw_metadata = {}
        body = raw.strip()

    # No frontmatter: preserve previous behavior expected by tests
    if not raw_metadata:
        return {}, body

    try:
        frontmatter_obj = FrontmatterSchema(**raw_metadata)
    except ValidationError as e:
        errors = "; ".join(f"{err['loc'][0]}: {err['msg']}" for err in e.errors())
        raise ValueError(f"Invalid frontmatter: {errors}") from e

    metadata = frontmatter_obj.model_dump(exclude_none=True)
    return metadata, body


def extract_sections_with_paths(text: str) -> list[tuple[str, str]]:
    """
    Extract sections with their full heading path.

    Example:
        ## Methods > ### Data Collection
    """

    lines = text.split("\n")
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


def chunk_with_heading_paths(
    sections: list[tuple[str, str]], max_chars: int = 1200
) -> list[dict]:
    """Convert sections into chunks with heading_path metadata."""

    chunks: list[dict] = []

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
            if len(current_chunk) + len(para) + 2 <= max_chars:
                current_chunk += ("\n\n" if current_chunk else "") + para
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


def estimate_tokens(text: str) -> int:
    """Rough token estimate (4 chars ≈ 1 token)."""

    return max(1, len(text) // 4)
