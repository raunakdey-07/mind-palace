"""Markdown parser: extracts YAML frontmatter, body content, and heading structure."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Tuple

import frontmatter
from pydantic import BaseModel, Field, ValidationError


class FrontmatterSchema(BaseModel):
    """Validated frontmatter schema for Mind Palace documents."""

    title: str = Field(..., min_length=1)
    date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    tags: List[str] = Field(default_factory=list)
    document_type: str = Field(default="note", pattern=r"^(kaggle|project|note|paper)$")
    competition: str = Field(default="")
    status: str = Field(default="active")
    git_repo: str = Field(default="")
    notebook: str = Field(default="")
    summary: str = Field(default="")

    class Config:
        extra = "allow"  # Allow additional fields


@dataclass
class ParsedDocument:
    """Parsed Markdown document with metadata and structured content."""

    frontmatter: FrontmatterSchema
    body: str
    sections: List[Tuple[str, str]]  # (heading_path, content)


def parse_markdown(raw: str) -> Tuple[FrontmatterSchema, str, List[Tuple[str, str]]]:
    """Parse a Markdown file into validated frontmatter, body, and sections.

    Returns:
        (frontmatter, body_text, sections)
        sections = list of (heading_path, content) tuples
    """
    try:
        post = frontmatter.loads(raw)
        raw_metadata = dict(post.metadata) if post.metadata else {}
        body = post.content.strip()
    except Exception:
        raw_metadata = {}
        body = raw.strip()

    # Validate frontmatter
    try:
        frontmatter_obj = FrontmatterSchema(**raw_metadata)
    except ValidationError as e:
        # Provide helpful error message
        errors = "; ".join(f"{err['loc'][0]}: {err['msg']}" for err in e.errors())
        raise ValueError(f"Invalid frontmatter: {errors}")

    # Extract sections with heading paths
    sections = extract_sections_with_paths(body)

    return frontmatter_obj, body, sections


def extract_sections_with_paths(text: str) -> List[Tuple[str, str]]:
    """Extract sections with their full heading path (e.g., '## Methods > ### Data Collection')."""
    lines = text.split("\n")
    sections: List[Tuple[str, str]] = []
    current_heading_stack: List[Tuple[int, str]] = []  # (level, heading_text)
    current_content: List[str] = []

    def flush_section():
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

            # Pop stack to current level
            while current_heading_stack and current_heading_stack[-1][0] >= level:
                current_heading_stack.pop()

            current_heading_stack.append((level, heading_text))
            current_content = []
        else:
            current_content.append(line)

    flush_section()
    return sections


def chunk_with_heading_paths(
    sections: List[Tuple[str, str]], max_chars: int = 1200
) -> List[dict]:
    """Convert sections into chunks with heading_path metadata."""
    chunks = []
    for heading_path, content in sections:
        if not content.strip() and not heading_path:
            continue

        # If section is small enough, keep as one chunk
        if len(content) <= max_chars:
            chunks.append(
                {
                    "text": content,
                    "heading_path": heading_path,
                    "token_count": estimate_tokens(content),
                }
            )
        else:
            # Split large sections by paragraphs
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
