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

    # Title fallback: a Markdown file may carry YAML frontmatter for other keys
    # (e.g. `supersedes:`) while naming itself only through its first `# H1`
    # heading. Treat that heading as the title rather than rejecting the
    # document with `frontmatter_missing_title`.
    if not raw_metadata.get("title"):
        for line in body.splitlines():
            match = re.match(r"^#\s+(.+?)\s*#*\s*$", line.strip())
            if match:
                raw_metadata = dict(raw_metadata)
                raw_metadata["title"] = match.group(1).strip()
                break

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


#: A line that opens a list item.
#:
#: The chunker uses it to choose where a long paragraph may be cut. A cut
#: inside a wrapped bullet leaves the bullet in two chunks, and the claim the
#: extractor reads out of it has no quotable evidence: the archive needs the
#: whole statement in one piece, and a statement that straddles a boundary has
#: none.
_LIST_ITEM = re.compile(r"^(?:[-*+]\s+|\d{1,3}[.)]\s+)")

#: A `Key: Value` line. The extractor reads it as a statement of its own, and
#: the chunker treats it as a boundary for the same reason.
_FIELD_LINE = re.compile(r"^(?P<key>[A-Za-z][A-Za-z0-9 _-]{0,40}):\s*(?P<value>\S.*)$")

#: A line that opens a Markdown table row.
_TABLE_ROW = re.compile(r"^\s*\|")

#: A table cell made only of dashes, colons and spaces. The row of them under a
#: table's header is the separator, and it is what tells a header row from a
#: row of data.
_SEPARATOR_CELL = re.compile(r"^[\s:\-]+$")

#: Words a chunk must not end on.
#:
#: A chunk that stops on "and" reads as a sentence that broke off, and the claim
#: the extractor reads out of it inherits the cut. The writer wrapped the
#: sentence, so the cut moves back to the end of the last complete phrase.
_CHUNK_TRAILING_WORDS = frozenset(
    """a an the and or but nor so yet for of in on at by with from to into onto
    over under as than about after before between through during without within
    upon per via""".split()
)


def table_cells(line: str) -> list[str]:
    """The cells of one Markdown table row.

    A row is written between pipes, and the pipes at the ends are decoration:
    `| a | b |` has two cells, not four. A cell may hold a pipe of its own, and
    a table that carries one is rare enough that splitting on every pipe is the
    honest reading: a cell that claims to hold two columns is a malformed row,
    and dropping the row is better than inventing a column.
    """
    text = line.strip()
    if text.startswith("|"):
        text = text[1:]
    if text.endswith("|"):
        text = text[:-1]
    return [cell.strip() for cell in text.split("|")]


def is_table_separator(line: str) -> bool:
    """Whether this row is the `| --- | --- |` line under a table's header.

    The separator is what makes a table a table. A line that starts with a pipe
    on its own is a paragraph that happens to contain one.
    """
    cells = table_cells(line)
    return bool(cells) and all(_SEPARATOR_CELL.match(cell) for cell in cells)


def render_table_row(header: list[str], row: list[str]) -> str:
    """The declarative form of one table row.

    `<label>: <Column Header>: <Cell Value>; <Column Header>: <Cell Value>`

    The row's first cell is the label the row is about, and every other cell is
    named by the header above it. A cell with no header has no name to be
    given, and a cell with no value says nothing, so both are left out rather
    than turned into a dangling colon.

    This is the form the archive holds as the claim, because a row of pipes is
    not a sentence: `| 6.15 s | 0.40 s |` says two numbers and nothing about
    what they measure. The name of the column is the whole of what turns the
    row into a fact.
    """
    label = row[0] if row else ""
    pairs = []
    for index, value in enumerate(row[1:], 1):
        if not value:
            continue
        name = header[index] if index < len(header) else ""
        if not name:
            continue
        pairs.append(f"{name}: {value}")
    if not pairs:
        return label
    body = "; ".join(pairs)
    return f"{label}: {body}" if label else body


def read_table(lines: list[str]) -> tuple[list[str], list[list[str]]]:
    """The header cells and the data rows of one table.

    `lines` is the table's own lines: the header row, the separator under it,
    and the data rows after that. A table with no separator is not a table,
    and the caller does not call this with one.
    """
    header = table_cells(lines[0])
    return header, [table_cells(line) for line in lines[2:]]


def _statement_cuts(text: str) -> list[int]:
    """The offsets in `text` where a statement begins.

    A statement is a list item or a `Key: Value` field, and it runs until the
    next one. A field inside a wrapped bullet is not a statement: prose in a
    bullet holds colons, and reading that as a field would cut the bullet in
    half.

    The chunker cuts here and the extractor reads whole statements, so the two
    agree on where a statement is. When they disagree, a statement ends up
    split across two chunks and its claim has no quotable evidence.
    """
    cuts: list[int] = []
    offset = 0
    in_list_item = False
    for line in text.split("\n"):
        if _LIST_ITEM.match(line):
            cuts.append(offset)
            in_list_item = True
        elif _FIELD_LINE.match(line.strip()) and not (in_list_item and line[:1].isspace()):
            cuts.append(offset)
            in_list_item = False
        offset += len(line) + 1
    return cuts


def _last_statement_cut(text: str, floor: int) -> int:
    """The last statement boundary in `text` past `floor`, or -1."""
    best = -1
    for offset in _statement_cuts(text):
        if offset > floor:
            best = offset
    return best


def estimate_tokens(text: str) -> int:
    """Rough token estimate (4 chars ≈ 1 token)."""

    return max(1, len(text) // 4)


def chunk_with_heading_paths(
    sections: list[tuple[str, str]],
    max_chars: int = 1200,
) -> list[dict[str, Any]]:
    """Convert sections into chunks with heading_path metadata.

    A Markdown table is chunked apart from the prose around it. Its rows go in
    verbatim, so a row's evidence is a byte-exact slice of the document, and
    the rendered form of each row goes in beside them, so the claim the
    extractor reads out of a row names the columns it came from -- and so the
    claim's own wording is in the chunk, which the archive requires before it
    will archive a claim at all.

    A table too long for one chunk is split by row, and every chunk carries the
    header, because a row that lost the header above it does not say which
    column it belongs to.
    """

    chunks: list[dict[str, Any]] = []

    for heading_path, content in sections:
        if not content.strip() and not heading_path:
            continue

        for kind, payload in _split_tables(content):
            if kind == "table":
                chunks.extend(_table_chunks(heading_path, payload, max_chars))
            else:
                chunks.extend(_chunk_text(heading_path, payload, max_chars))

    return chunks


def _split_tables(content: str) -> list[tuple[str, Any]]:
    """Split a section's text into prose segments and table blocks.

    A table block is the list of its own lines, header first. The prose around
    it keeps its blank lines, so the paragraphs on either side are still the
    paragraphs they were.
    """
    lines = content.split("\n")
    segments: list[tuple[str, Any]] = []
    buffer: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        following = lines[index + 1] if index + 1 < len(lines) else ""
        if _TABLE_ROW.match(line) and _TABLE_ROW.match(following) and is_table_separator(following):
            block = [line, following]
            index += 2
            while index < len(lines) and _TABLE_ROW.match(lines[index]):
                block.append(lines[index])
                index += 1
            if buffer:
                segments.append(("text", "\n".join(buffer)))
                buffer = []
            segments.append(("table", block))
            continue
        buffer.append(line)
        index += 1
    if buffer:
        segments.append(("text", "\n".join(buffer)))
    return segments


def _table_chunks(heading_path: str, lines: list[str], max_chars: int) -> list[dict[str, Any]]:
    """The chunks for one table.

    The table's own lines go in verbatim, so a row's evidence is a byte-exact
    slice of the document. The rendered rows go in with them, so the claim read
    out of a row names its columns and so the claim's wording is in the chunk.

    Every chunk carries the header. A row that lost the header above it does
    not say which column it belongs to, and the rendered form only reads
    against the header that named it.
    """
    header, rows = read_table(lines)
    header_lines = lines[:2]

    groups: list[tuple[list[str], list[str]]] = []
    raw_group: list[str] = []
    rendered_group: list[str] = []
    size = len("\n".join(header_lines))
    for index, row in enumerate(rows):
        raw = lines[2 + index]
        rendered = render_table_row(header, row)
        addition = len(raw) + len(rendered) + 2
        if raw_group and size + addition > max_chars:
            groups.append((raw_group, rendered_group))
            raw_group, rendered_group = [], []
            size = len("\n".join(header_lines))
        raw_group.append(raw)
        rendered_group.append(rendered)
        size += addition
    if raw_group:
        groups.append((raw_group, rendered_group))

    chunks: list[dict[str, Any]] = []
    for raw_lines, rendered_lines in groups:
        text = "\n".join([*header_lines, *raw_lines, *rendered_lines])
        chunks.append(
            {
                "text": text,
                "heading_path": heading_path,
                "token_count": estimate_tokens(text),
            }
        )
    return chunks


def _trim_trailing_word(text: str) -> str:
    """Move a cut back past the conjunction or preposition it stopped on.

    A chunk boundary that lands on "PDF and" splits a wrapped sentence in
    half, and the extractor reads the half that carries no fact. The cut moves
    back to the end of the last complete phrase instead.
    """
    words = list(re.finditer(r"[A-Za-z]+", text))
    if not words or words[-1].group(0).casefold() not in _CHUNK_TRAILING_WORDS:
        return text
    space = text.rfind(" ", 0, words[-1].start())
    return text[:space] if space > 0 else text


def _chunk_text(heading_path: str, content: str, max_chars: int) -> list[dict[str, Any]]:
    """The chunks for a run of prose."""
    chunks: list[dict[str, Any]] = []

    if not content.strip() and not heading_path:
        return chunks

    if len(content) <= max_chars:
        chunks.append(
            {
                "text": content,
                "heading_path": heading_path,
                "token_count": estimate_tokens(content),
            }
        )
        return chunks

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
            # A cut before a statement, not inside one. A bullet wrapped
            # over three lines is one statement, and a chunk boundary in
            # the middle of it leaves the claim with no quotable evidence.
            statement = _last_statement_cut(cut, max_chars // 2)
            newline = cut.rfind("\n")
            if statement > 0:
                cut = para[:statement]
            elif newline > max_chars // 2:
                cut = para[:newline]
            else:
                # Prefer breaking at a sentence/word boundary within the window.
                boundary = max(cut.rfind(". "), cut.rfind(" "))
                if boundary > max_chars // 2:
                    cut = para[: boundary + 1]
            # And never on a conjunction or a preposition. A chunk that stops
            # on "PDF and" holds half a sentence, and the claim the extractor
            # reads out of it is that half.
            trimmed = _trim_trailing_word(cut)
            if len(trimmed) > max_chars // 2:
                cut = trimmed
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
