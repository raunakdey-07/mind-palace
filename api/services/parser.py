# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Raunak Dey

"""Markdown parser: extracts YAML frontmatter, body content, and heading structure."""

from __future__ import annotations

import datetime
import re
from typing import Any

import frontmatter
from pydantic import BaseModel, ConfigDict, Field, ValidationError


class FrontmatterWarning:
    """A frontmatter problem worth telling somebody about, with nothing leaked.

    The contract is narrow and deliberate: **path, line, field, reason code**. No
    content, no value, no snippet of the document. Frontmatter is where a developer
    puts titles and summaries; a diagnostic that echoes it back into a log, an
    HTTP error, or a sync report is a channel for whatever was in it, and the
    existing redaction tests treat that as a defect rather than a style choice.

    The reason codes are stable strings because they are what a CI job matches on
    and what an integrator greps for. Prose is for humans; code is for machines.
    """

    #: The frontmatter block could not be parsed as YAML at all.
    UNPARSEABLE = "frontmatter_unparseable"
    #: Parsed, but `title` is missing or empty and the schema requires it.
    MISSING_TITLE = "frontmatter_missing_title"
    #: Parsed, but a field failed its schema constraint. `field` names it.
    INVALID_FIELD = "frontmatter_invalid_field"
    #: Parsed and validated, but `claims` is not a list.
    CLAIMS_NOT_A_LIST = "claims_not_a_list"

    __slots__ = ("path", "line", "field", "code")

    def __init__(
        self, code: str, *, path: str = "", line: int | None = None, field: str = ""
    ) -> None:
        self.code = code
        self.path = path
        self.line = line
        self.field = field

    def as_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "path": self.path,
            "line": self.line,
            "field": self.field,
        }

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        where = f" {self.path}" if self.path else ""
        if self.line:
            where += f":{self.line}"
        field = f" [{self.field}]" if self.field else ""
        return f"<FrontmatterWarning {self.code}{where}{field}>"


def _frontmatter_error_line(raw: str) -> int | None:
    """Best-effort line number for a YAML error, without parsing further.

    PyYAML's ScannerError carries `problem_mark.line`; anything else does not, and
    a wrong line number is worse than none. Returns None rather than guessing.
    """
    mark = getattr(raw, "problem_mark", None)
    line = getattr(mark, "line", None)
    # +1 for the opening `---` line that YAML never sees.
    return line + 1 if isinstance(line, int) else None


class FrontmatterSchema(BaseModel):
    """Validated frontmatter schema for Mind Palace documents."""

    title: str = Field(..., min_length=1)
    # Optional, and deliberately so. It was required here while every consumer
    # already guarded for absence (`metadata.get("date")`, `if d["date"]`), so the
    # requirement bought nothing and cost a warning on every document that
    # omitted it. A diagnostic that fires on most inputs is a diagnostic people
    # learn to skip, which is the failure this whole change exists to remove.
    date: datetime.date | None = None
    tags: list[str] = Field(default_factory=list)
    # Free-form, and deliberately so. This was a four-value enum
    # (kaggle|project|note|paper) while the write path accepted anything --
    # `metadata.get("document_type")` with no check in
    # `api/services/ingestion.py`. The mismatch was invisible because
    # schema-validation failures were swallowed; surfacing them revealed that
    # the project's own test corpus writes `document_type: design`, which the
    # enum rejected.
    #
    # Constraining a field the product does not constrain buys nothing and costs
    # a warning on every document using a value the system would have stored
    # anyway. If a closed set is wanted, it belongs on the column.
    document_type: str = "note"
    competition: str = Field(default="")
    status: str = Field(default="active")
    git_repo: str = Field(default="")
    notebook: str = Field(default="")
    summary: str = Field(default="")

    model_config = ConfigDict(extra="allow")


def parse_markdown(
    raw: str, *, path: str = "", collect_warnings: bool = False
) -> tuple[dict[str, Any], str] | tuple[dict[str, Any], str, list[FrontmatterWarning]]:
    """Parse a document, optionally reporting why its frontmatter was not used.

    Returns `(metadata, body)`, or `(metadata, body, warnings)` when
    `collect_warnings` is set. `warnings` is empty for a document with no
    frontmatter -- absence is not a defect, and a report that flags every plain
    Markdown file teaches people to ignore it.

    The return shape varies because every existing caller wants the two-tuple and
    changing it would mean auditing the whole tree for a diagnostic. The
    opt-in is deliberate.

    ## Why this is not silent any more

    `except Exception: return {}, raw.strip()` swallowed *every* frontmatter
    problem. During the first-hour audit that produced the worst failure this
    project has had: eleven documents synced, `added=11, success=True`, and zero
    claims in the archive, because the `claims:` block was YAML that PyYAML
    rejected. Nothing said so. An author reading the summary would conclude their
    document had been indexed, and it had -- as text, with no memory in it.

    A failure that reports success is worse than a failure that reports failure,
    because it is trusted.
    """
    warnings: list[FrontmatterWarning] = []

    try:
        post = frontmatter.loads(raw)
        raw_metadata = dict(post.metadata) if post.metadata else {}
        body = post.content.strip()
    except Exception as exc:
        # The exception's own message embeds the offending YAML text. It is never
        # propagated; only the reason code and the line number are.
        warnings.append(
            FrontmatterWarning(
                FrontmatterWarning.UNPARSEABLE, path=path, line=_frontmatter_error_line(exc)
            )
        )
        result = ({}, raw.strip())
        return (*result, warnings) if collect_warnings else result

    # No frontmatter present. Not a warning: a Markdown file is allowed to be
    # Markdown, and flagging this would make every warning meaningless.
    if not raw_metadata:
        result = ({}, body)
        return (*result, warnings) if collect_warnings else result

    try:
        validated = FrontmatterSchema(**raw_metadata)
        # Drop keys the author did not write. `model_dump()` materialises every
        # schema default, so a document with only a title came back carrying
        # `date: None`, `tags: []`, `status: "active"` and six more. Callers --
        # including the benchmark corpus loader -- distinguish "the author said
        # this" from "this is a default", and materialising defaults erases that
        # distinction for every document at once.
        metadata = {k: v for k, v in validated.model_dump().items() if k in raw_metadata}
    except ValidationError as exc:
        # Preserve legacy permissive behavior -- an unknown or oddly typed field
        # must not stop a document being archived -- but say that it happened.
        error = exc.errors()[0] if exc.errors() else {}
        location = (error.get("loc") or ("",))[0]
        code = (
            FrontmatterWarning.INVALID_FIELD
            if location != "title"
            else FrontmatterWarning.MISSING_TITLE
        )
        warnings.append(
            FrontmatterWarning(code, path=path, field=str(location) if location else "")
        )
        metadata = raw_metadata

    claims = metadata.get("claims")
    if claims is not None and not isinstance(claims, list):
        warnings.append(
            FrontmatterWarning(FrontmatterWarning.CLAIMS_NOT_A_LIST, path=path, field="claims")
        )

    result = (metadata, body)
    return (*result, warnings) if collect_warnings else result


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
                # Break at a line boundary when there is one in the window. The
                # claim extractor has to quote evidence back verbatim out of an
                # archived chunk, and a chunk that starts or ends mid-line cannot
                # supply the quote for that line -- the claim is dropped with
                # `claim_extraction_no_quote` instead. Splitting on the last space
                # rather than the last newline put section boundaries inside
                # bullets, and every bullet on that boundary lost its evidence. A
                # sentence or word boundary is the fallback for a single line that
                # is longer than the whole window, which cannot be helped.
                newline = cut.rfind("\n")
                if newline > max_chars // 2:
                    cut = para[:newline]
                else:
                    # Prefer breaking at a sentence/word boundary within the window.
                    boundary = max(cut.rfind(". "), cut.rfind(" "))
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
