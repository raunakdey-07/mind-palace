# SPDX-License-Identifier: Apache-2.0
"""Deterministic claim extraction from plain Markdown, with no model involved.

A document that carries no authored `claims:` block used to be archived as text and
contributed nothing to memory, so `recall` abstained on it. That is the shape most
decisions and runbooks actually arrive in: an ADR, a runbook, a `DECISIONS.md`,
a `README.md`. Nothing about those files is missing that a structural reader
cannot recover.

This module is that reader. It produces the same claim dictionaries an author
would have written by hand, so they take exactly the same path through
`memory._prepare`: exact-substring evidence, real character offsets, real
supersession. Nothing here infers, summarises or paraphrases. Every claim is a
line that was in the file, quoted back.

## What becomes a claim

Under a heading path, in this order:

* `- `, `* ` and `+ ` bullets whose text is a declarative sentence.
* `1. ` numbered items, which is how a decision log writes its entries.
* `Key: Value` lines -- `Status: Accepted`, `Superseded-by: ADR-003`. These are
  the field an ADR is read for, and they are the line most worth keeping exact.

## One statement, however many lines it is wrapped over

A writer who keeps a source narrow wraps a long sentence:

    - A machine that will not take a model at all. The default install is 216 MB and a
      cold recall is about 1.4 s, so this is a narrower case than it was at v0.9.1,
      when a 1.35 GB install and a 6 s first recall were the price of ranking.

That is one statement, and it is the statement that carries the fact. Reading it
as three lines and archiving the first one as a claim kept "The default install
is 216 MB and a" -- the number and nothing that says what it is. The
continuation lines are part of the claim, so the extractor reads a statement as
its first line plus every continuation line that belongs to it.

A statement ends at a blank line, at the next statement, at a heading, at a
table, at a fenced code block, or at a declaration. Those are the places where
the writer stopped writing one thing and started writing another.

The `claim` text and the `value` are the statement with its line breaks
collapsed to single spaces, because a claim is a sentence and a sentence does
not carry the source's column layout. The `evidence` quote is the raw slice,
with its line breaks and its indentation intact, because evidence has to be
verifiable against the file byte for byte.

## What does not

Fenced code, indented code, tables, HTML comments, link reference definitions,
horizontal rules, and block quotes. A code block holds example commands that are
not decisions; quoting one as a claim would put an unverified line into the
archive with the same standing as a signed-off decision.

A bare heading is the context for what follows it, not a claim on its own, and
the first `# title` of a document is its name rather than a statement.

## Identity, and why it is positional

A claim's key is `md:<heading path>:<ordinal>`, where the ordinal counts the
extracted claims under that heading path from zero.

Positional, not derived from the text, and that is the whole point. A key derived
from the text would change when the text changed, and the old claim would then
never be superseded -- it would simply be joined by a second, unrelated claim
under a second key. Positional identity means editing a bullet supersedes it in
place, which is what `history` shows and what a reader expects.

The cost is that inserting a bullet renumbers the ones after it, so those claims
supersede each other. That is the honest reading: the document did change under
them. Keys are scoped by their heading path, so a bullet that moves to a
different section gets a different key, which is also correct.

## When it does not run

Only as a fallback. A document with an authored `claims:` block is archived
exactly as written, and the extractor never sees it -- an author's key is a
decision the tool has no business second-guessing.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

#: How much of a claim's own text is quoted when the line is too long to quote
#: whole. Long enough to identify the claim, short enough to keep a pack readable.
QUOTE_LIMIT = 240

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_BULLET = re.compile(r"^[-*+]\s+(?!\[[ xX]\])(.+)$")
_NUMBERED = re.compile(r"^\d{1,3}[.)]\s+(.+)$")
_FIELD = re.compile(r"^(?P<key>[A-Za-z][A-Za-z0-9 _-]{0,40}):\s*(?P<value>\S.*)$")
_RULE = re.compile(r"^\s{0,3}(?:-{3,}|\*{3,}|_{3,})\s*$")
_FENCE = re.compile(r"^\s{0,3}(?:```|~~~)")
_TABLE = re.compile(r"^\s*\|")
_DEFINITION = re.compile(r"^\s*\[[^\]]+\]:\s*\S")
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)

#: A line too short to be a statement. A lone "Yes." or "N/A" carries no fact.
MIN_CLAIM_CHARS = 12

#: A bullet is declarative when it reads as an assertion rather than a pointer.
#: Short fragments, pure links and pure code references are structure, not facts.
_MAX_LINKS = 2

#: The two spellings an author may use, and what each means. `supersedes` points
#: backwards at the document this one replaces; `superseded_by` points forward at
#: the replacement. Both are read, because an ADR that has already been replaced
#: is exactly the one whose history should say so.
_SUPERSEDES = "supersedes"
_SUPERSEDED_BY = "superseded_by"

#: A supersession declaration, and only a declaration. Anchored at the start of
#: the line after an optional list marker, optionally bold, and with no inline
#: code in the pattern at all. An earlier version tolerated a backtick around the
#: directive so that `` `Supersedes:` `` would read as one; that is what let the
#: documentation of this feature break it. `docs/authoring.md` explains the
#: syntax in running prose, so a line there begins with a literal
#: `` `supersedes:` `` and the scanner took the rest of the sentence as a reference
#: to retire, then refused to ingest the document. Inline code is prose here, so
#: the pattern no longer recognizes it, and a mid-sentence mention never matches.
#: Indentation is allowed because a nested list item carries some.
_DECLARATION = re.compile(
    r"^\s*(?:[-*+]\s+)?(?:\*\*|__)?"
    r"(?P<name>supersedes|superseded[\s_-]?by)"
    r"(?:\*\*|__)?\s*:\s*(?P<value>\S.*?)\s*$",
    re.IGNORECASE,
)
_LINK_TARGET = re.compile(r"^\[[^\]]*\]\(([^)]+)\)\s*$")
#: A line that opens a new sentence. Used to end a `Key: Value` statement.
_NEW_SENTENCE = re.compile(r"^[A-Z]")
#: A reference that names a document rather than a claim key.
_PATHISH = re.compile(r"[/\\]|\.md\b", re.IGNORECASE)

#: Values that mean "there is no such document". An ADR template almost always
#: carries `Superseded-by: none`, and treating that as a reference would fail
#: every decision record that has not been replaced yet -- which is all of them,
#: at the moment it matters most.
_NO_REFERENCE = frozenset({"none", "n/a", "na", "-", "--", "null", "nil", "tbd", "unknown"})


def supersession_references(metadata: dict, body: str) -> tuple[list[str], list[str]]:
    """The documents this one replaces, and the documents that replace it.

    Returns `(supersedes, superseded_by)` as lists of reference strings, in the
    order written. A reference is a path (`adr/0001-use-pg15.md`, or just
    `0001-use-pg15.md`) or a claim key (`architecture.postgres`); which one it is
    does not matter here, only whether the reference resolves.

    Read from frontmatter under `supersedes` / `superseded_by`, and from a body
    line of the same name. Frontmatter accepts a YAML list; the body takes one
    reference per line, because a comma is a legal character in a path and
    splitting on it would resolve to two documents that do not exist.

    No model, no inference, no guess about numbering. `Supersedes: 0002` is a
    reference exactly as written. Deciding that "0002" means
    `adr-0002-something.md` is the kind of cleverness that retires the wrong claim,
    and the archive reports that mistake as history forever.
    """
    out: dict[str, list[str]] = {_SUPERSEDES: [], _SUPERSEDED_BY: []}
    for name, raw in metadata.items():
        target = _canonical_name(str(name))
        if target is None:
            continue
        for value in raw if isinstance(raw, list) else [raw]:
            cleaned = _clean(str(value))
            if cleaned:
                out[target].append(cleaned)

    for _number, raw in _iter_lines(_HTML_COMMENT.sub("", body)):
        declaration = _DECLARATION.match(raw)
        if not declaration:
            continue
        target = _canonical_name(declaration.group("name"))
        if target is None:
            continue
        cleaned = _clean(declaration.group("value"))
        if cleaned and cleaned not in out[target]:
            out[target].append(cleaned)
    return out[_SUPERSEDES], out[_SUPERSEDED_BY]


def is_declaration(raw: str) -> bool:
    """Is this line a supersession declaration rather than a claim?

    Used by `extract` to keep `Supersedes: adr-001.md` out of the claim set. It
    states a fact about the document, not about the world, and the same way the
    document's own `#` title is context: a caller recalling "what does the ADR
    decide" should not be handed the line saying which ADR it replaces.
    """
    return bool(_DECLARATION.match(raw))


def looks_like_path(reference: str) -> bool:
    """Does this reference name a document rather than a claim key?

    A key cannot contain a slash or end in `.md`, and an authored key never does.
    The test is on the shape rather than on whether the document exists, because
    a reference that does not resolve has to fail as a missing reference and not
    as an unknown key.
    """
    return bool(_PATHISH.search(reference))


def _canonical_name(name: str) -> str | None:
    """`supersedes` / `superseded_by`, or None when the name is neither.

    None rather than a default, because a default here is how `title:` ends up
    read as a supersession reference.
    """
    folded = name.strip().casefold().replace("-", "_").replace(" ", "_")
    if folded.startswith(_SUPERSEDED_BY):
        return _SUPERSEDED_BY
    return _SUPERSEDES if folded.startswith(_SUPERSEDES) else None


def _clean(value: str) -> str:
    value = value.strip().strip("`").strip()
    link = _LINK_TARGET.match(value)
    if link:
        value = link.group(1).strip()
    value = value.strip("<>\"'`").strip()
    return "" if value.casefold() in _NO_REFERENCE else value


class ExtractionWarning:
    """One line that looked like a claim but could not be archived.

    The contract is the same as `parser.FrontmatterWarning`: path, line, reason
    code. No content, because a line of somebody's document is theirs, and an
    ingestion report is not a place to echo it back.

    `NO_QUOTE` is the one that matters most. It fires when the extracted line
    could not be located in an archived chunk, which happens when a chunk
    boundary falls inside the line. The claim is dropped rather than stored with
    evidence nobody can verify, and this says which line was lost.
    """

    NO_QUOTE = "claim_extraction_no_quote"
    __slots__ = ("path", "line", "field", "code")

    def __init__(
        self, code: str, *, path: str = "", line: int | None = None, field: str = ""
    ) -> None:
        self.code = code
        self.path = path
        self.line = line
        self.field = field

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "path": self.path, "line": self.line}

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        where = f" {self.path}" if self.path else ""
        if self.line:
            where += f":{self.line}"
        return f"<ExtractionWarning {self.code}{where}>"


def _slug(heading_path: str) -> str:
    """A readable, filesystem-safe stem for a heading path.

    The digest keeps two headings that reduce to the same slug distinct, so
    moving a claim cannot silently merge it with an unrelated one.
    """
    import re as _re

    stem = _re.sub(r"[^a-z0-9]+", "-", heading_path.casefold()).strip("-")[:48].strip("-")
    digest = hashlib.sha256(heading_path.encode("utf-8")).hexdigest()[:8]
    return f"{stem or 'section'}-{digest}"


def _declarative(text: str) -> bool:
    """Whether a line reads as a statement rather than as structure."""
    stripped = text.strip()
    if len(stripped) < MIN_CLAIM_CHARS:
        return False
    if stripped.startswith("```") or stripped.startswith("~~~"):
        return False
    # A line that is mostly a link is navigation.
    if stripped.count("](") > _MAX_LINKS:
        return False
    return True


def _iter_lines(body: str):
    """Yield `(line_number, line)` for lines that are prose.

    Everything structural is skipped here rather than by the caller, so a fenced
    block cannot contribute a bullet that happens to look like a decision.
    """
    for number, line, kind in _scan(body):
        if kind == "prose":
            yield number, line


def _scan(body: str):
    """Yield `(line_number, line, kind)` for every physical line of the body.

    `kind` is `prose` for a line that could be part of a statement, `heading`
    for a Markdown heading, and `block` for everything structural: a fenced code
    block, an indented code block, a table, a horizontal rule, or a link
    reference definition.

    The kinds exist so a statement can *end* at a block instead of running over
    it. A bullet that wraps across a table is two things, and the second one is
    not a continuation of the first.
    """
    in_fence = False
    fence_marker = ""
    for number, line in enumerate(body.splitlines(), 1):
        fence = _FENCE.match(line)
        if fence:
            marker = fence.group(0).strip()[:3]
            if not in_fence:
                in_fence, fence_marker = True, marker
            elif line.strip().startswith(fence_marker):
                in_fence, fence_marker = False, ""
            yield number, line, "block"
            continue
        if in_fence:
            yield number, line, "block"
            continue
        if _RULE.match(line) or _TABLE.match(line) or _DEFINITION.match(line):
            yield number, line, "block"
            continue
        # Four leading spaces is an indented code block.
        if line.startswith("    ") or line.startswith("\t"):
            yield number, line, "block"
            continue
        if _HEADING.match(line):
            yield number, line, "heading"
            continue
        yield number, line, "prose"


def _starts_statement(line: str, current: list[tuple[int, str]] | None) -> bool:
    """Whether this line opens a statement rather than continuing one.

    A bullet, a numbered item and a `Key: Value` field each open one. A field
    counts because a document that writes `Status:` and `Owner:` on consecutive
    lines is making two statements, and joining them would produce a claim that
    says neither.

    The exception is a field-shaped line *inside* a wrapped bullet. Prose in a
    bullet holds colons -- "Row-level security does not contain this: the
    process is the thing that sets the tenant scope" -- and reading that as a
    field would cut the bullet in half and throw away the rest of the
    sentence. An indented line under a bullet belongs to that bullet.
    """
    if _BULLET.match(line) or _NUMBERED.match(line):
        return True
    if not _FIELD.match(line.strip()):
        return False
    if current and _is_list_item(current) and line[:1].isspace():
        return False
    return True


def _is_list_item(lines: list[tuple[int, str]]) -> bool:
    """Whether this statement is a bullet or a numbered item."""
    return bool(_BULLET.match(lines[0][1]) or _NUMBERED.match(lines[0][1]))


def _is_field_statement(lines: list[tuple[int, str]]) -> bool:
    """Whether this statement is a `Key: Value` field."""
    first = lines[0][1]
    if _BULLET.match(first) or _NUMBERED.match(first):
        return False
    return bool(_FIELD.match(first.strip()))


def _opens_a_sentence(line: str) -> bool:
    """Whether this line starts a new sentence.

    A `Key: Value` field is a sentence inside a paragraph, not a list entry,
    so it ends where its sentence ends. A line that opens a new sentence is
    the next statement, and folding it into the field would turn one fact
    into a paragraph.
    """
    return bool(_NEW_SENTENCE.match(line))


def _statement(lines: list[tuple[int, str]]) -> tuple[str, str, str] | None:
    """The `(claim, value, raw)` of one logical statement, or None.

    `lines` is the statement's first line followed by its continuation lines.
    The claim and the value are the statement with its line breaks collapsed to
    single spaces; `raw` is the verbatim slice, with the line breaks and the
    indentation left in place, because evidence has to be verifiable against
    the file byte for byte.

    None means the statement is not a claim: a declaration, or a line that is
    not shaped like one.
    """
    first = lines[0][1]
    bullet = _BULLET.match(first)
    numbered = _NUMBERED.match(first)
    field = None
    if bullet or numbered:
        match = bullet or numbered
        offset = match.start(1)
    else:
        field = _FIELD.match(first.strip())
        if not field:
            return None
        if is_declaration(first):
            # A statement about this document, not about the world. Keeping it
            # would let `recall` answer "which ADR replaces this one" and call
            # the answer a decision.
            return None
        offset = first.find(f"{field.group('key').strip()}: {field.group('value').strip()}")

    pieces = [line[offset:] if index == 0 else line for index, (_n, line) in enumerate(lines)]
    raw = "\n".join(pieces).rstrip()
    normalized = [" ".join(piece.split()) for piece in pieces]
    claim = " ".join(piece for piece in normalized if piece)
    if not claim:
        return None
    if field is None:
        return claim, claim, raw
    # `Key: Value`: the value is the statement, the key says which one it is.
    head = normalized[0]
    value = head.split(": ", 1)[1] if ": " in head else head
    tail = [piece for piece in normalized[1:] if piece]
    value = " ".join([value] + tail) if tail else value
    return claim, value, raw


def _limit_quote(quote: str) -> str:
    """The quote, bounded so a pack stays readable, and still exact.

    A statement wrapped over several lines is cut on a line boundary, so the
    evidence is a whole number of source lines. A single line is cut on a word
    boundary. Either way the result is a substring of the source, which is the
    only property that matters: evidence that cannot be located in the document
    is not evidence.
    """
    if len(quote) <= QUOTE_LIMIT:
        return quote
    window = quote[:QUOTE_LIMIT]
    if "\n" in window:
        cut = window.rfind("\n")
        if cut > QUOTE_LIMIT // 2:
            return window[:cut]
    cut = window.rfind(" ")
    return window[:cut] if cut > QUOTE_LIMIT // 2 else window


def extract(body: str, chunks: list[dict], *, path: str = "") -> tuple[list[dict], list[dict]]:
    """Return `(claims, warnings)` for a Markdown body.

    `chunks` are the archived chunks, in order. Each claim is verified to be
    quotable from one of them before it is returned, because a claim whose
    evidence does not resolve is not a claim: the archive rejects it, and a
    rejection at this point is silent, which is the failure this exists to avoid.
    """
    haystacks = [c.get("text") or "" for c in chunks]
    claims: list[dict] = []
    warnings: list[ExtractionWarning] = []
    headings: list[tuple[int, str]] = []
    ordinals: dict[str, int] = {}
    seen_claims: set[str] = set()
    seen_quotes: set[tuple[str, str]] = set()

    pending: list[tuple[int, str]] = []
    pending_path = ""

    def close() -> None:
        """Turn the statement that just ended into a claim, if it is one."""
        nonlocal pending
        if not pending:
            return
        lines = pending
        pending = []
        statement = _statement(lines)
        if statement is None:
            return
        text, value, raw = statement
        if not _declarative(text):
            return
        # Two identical statements under one heading are one statement written
        # twice, not two facts. Keep one so re-ingesting an unchanged file is a
        # no-op.
        signature = (pending_path, text)
        if signature in seen_quotes:
            return
        # The whole statement has to be quotable, not just the part that fits
        # the quote limit: a statement that straddles two chunks has no
        # evidence anybody can check, and a claim whose evidence does not
        # resolve is a claim the archive rejects.
        if not raw or not any(raw in hay for hay in haystacks):
            warnings.append(
                ExtractionWarning(ExtractionWarning.NO_QUOTE, path=path, line=lines[0][0])
            )
            return
        seen_quotes.add(signature)

        key_base = f"md:{_slug(pending_path)}"
        ordinal = ordinals.get(key_base, 0)
        ordinals[key_base] = ordinal + 1
        key = f"{key_base}:{ordinal}"
        if key in seen_claims:
            return
        seen_claims.add(key)
        claims.append({"key": key, "claim": text, "value": value, "evidence": _limit_quote(raw)})

    for number, raw, kind in _scan(_HTML_COMMENT.sub("", body)):
        if kind == "heading":
            close()
            heading = _HEADING.match(raw)
            level = len(heading.group(1))
            title = heading.group(2).strip()
            if level == 1 and not headings and not claims:
                # The document's own name. It scopes everything below it and
                # states nothing, so it is context rather than a claim.
                headings = [(level, title)]
                continue
            while headings and headings[-1][0] >= level:
                headings.pop()
            headings.append((level, title))
            continue
        if kind == "block":
            close()
            continue
        if not raw.strip():
            close()
            continue
        if _starts_statement(raw, pending):
            close()
            pending = [(number, raw)]
            pending_path = " > ".join(title for _level, title in headings)
            continue
        if pending:
            if _is_field_statement(pending) and _opens_a_sentence(raw):
                # The field's sentence ended. What follows is a new sentence,
                # and a field is one sentence, not a paragraph.
                close()
                continue
            # A wrapped continuation of the statement above it.
            pending.append((number, raw))
    close()

    claims.sort(key=lambda claim: claim["key"])
    return claims, warnings


def claims_for(metadata: dict, body: str, chunks: list[dict], *, path: str = ""):
    """The claims a document should archive: its own, or the ones its structure gives.

    Returns `None` when the author wrote a `claims:` block. `None` means "do not
    touch it", not "there are none": an authored key is a decision, and inventing
    a second one beside it would be the extractor overruling the author.
    """
    if "claims" in metadata:
        return None
    claims, warnings = extract(body, chunks, path=path)
    return {"claims": claims}, warnings


def has_claims(body: str) -> bool:
    """Whether this body has any claim-shaped structure, without building them.

    Used to decide whether a document takes the memory path at all. That decision
    is the reason plain Markdown used to produce nothing: a corpus with no
    archive yet had `memory.enabled()` false, no document carried a `claims:`
    block, so every Markdown file was indexed as text and recall abstained on all
    of it. An ADR is the case this exists for, and it never opted in.

    Cheap on purpose, and only reached while the corpus holds no memory: once one
    claim lands, `memory.enabled()` is true and this is not called again.
    """
    return bool(extract(body, _chunks(body))[0])


def _chunks(body: str) -> list[dict]:
    """The archived chunks a body will produce, for quote resolution."""
    from api.services.parser import chunk_with_heading_paths, extract_sections_with_paths

    return [
        {**chunk, "order_index": index}
        for index, chunk in enumerate(chunk_with_heading_paths(extract_sections_with_paths(body)))
    ]
