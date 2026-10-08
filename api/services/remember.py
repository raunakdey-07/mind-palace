# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Raunak Dey

"""The product write path: one statement becomes one authoritative memory.

`remember` is deliberately a thin composition, not a second ingestion stack. It
renders the statement as a memory document with an authored claim, then hands it
to the existing `IngestionService` memory path. Everything that makes the archive
trustworthy -- exact-substring evidence, fingerprints, supersession, the corpus
advisory lock, the append-only triggers -- is reached through the same code, so
there is no way for a simple write to bypass a guarantee.

The claim key is the only new idea, and it exists because supersession needs one.
Authoring the same key twice is how memory records that a fact changed; authoring
without a key is how memory records an unrelated fact. Both land in the same
archive.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date
from pathlib import Path

from api.services.corpora import DEFAULT_CORPUS_ID, validate_corpus_name
from api.services.ingestion import IngestionService

#: Where simple memories live. A reserved prefix, so a remembered fact is never
#: confused with a synced source document, and never collides with one.
MEMORY_PATH_PREFIX = "memory/"

#: The one limit on a statement. The REST contract validates against the same
#: value, imported from here, so there is a single number to change.
MAX_STATEMENT = 4000

#: A key becomes a filename, so it is reduced to characters that are safe in a
#: path and stable across platforms.
_SLUG_UNSAFE = re.compile(r"[^a-z0-9._-]+")

#: How much of the key is readable in the path. The rest is a digest of the key,
#: because two different keys must never reduce to one document: merging them
#: would supersede one claim with an unrelated one.
_SLUG_LENGTH = 48


class RememberError(ValueError):
    """A remembered statement that cannot become a memory."""


def normalise(statement: str) -> str:
    """One canonical form for a statement: whitespace collapsed, leading `#` removed.

    The archive requires evidence to be an exact substring of an archived chunk,
    and a YAML scalar cannot carry the line breaks of a multi-line statement
    verbatim. Normalising once, here, means the frontmatter quote, the document
    body and the claim text are byte-identical, so the offsets the archive records
    always describe the bytes that were actually stored.

    Leading `#` is stripped because the document body starts with a heading of its
    own: a statement that opened with `#` would be chunked as a second heading, and
    the text the archive was told to quote would end up in a different chunk than
    the claim. A statement is a fact, not a document -- a file that deserves its
    structure should carry authored `claims:`, which `remember --file` preserves.
    """
    return " ".join(statement.split()).lstrip("#").strip()


def claim_key(statement: str, key: str | None = None) -> str:
    """The authored key for a statement.

    With an explicit key, that key. Without one, a hash of the statement, so the
    same sentence is always the same memory and two different sentences never
    supersede each other by accident.
    """
    if key is not None:
        key = key.strip()
        if not key:
            raise RememberError(
                "a claim key cannot be blank; omit it to let Mind Palace choose one"
            )
        return key
    from api.services.memory import _hash

    return "text:" + _hash(statement)


def memory_path(key: str) -> str:
    """One document per key, which is what makes a rewrite a supersession."""
    slug = _SLUG_UNSAFE.sub("-", key.lower()).strip("-.")[:_SLUG_LENGTH].strip("-.") or "memory"
    # The digest keeps distinct keys distinct even when they share a prefix, so one
    # fact can never silently supersede a different one.
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]
    return f"{MEMORY_PATH_PREFIX}{slug}-{digest}.md"


def _title(statement: str) -> str:
    """A short human label for the document, derived from the statement itself.

    This is a label, not memory: it is never authoritative and never used to
    answer a question. The claim text is the memory.
    """
    first = re.sub(r"^#+\s*", "", statement)
    first = first.split(". ")[0].strip() or "Memory"
    return (first[:80] + "...") if len(first) > 83 else first


def _yaml_scalar(value: str) -> str:
    """Quote a scalar so a statement containing `:`, `#` or a quote survives."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def render_statement(statement: str, key: str) -> str:
    """Render a statement as a memory document with exactly one authored claim.

    The claim text is the statement, and the evidence quote is the same text: the
    archive requires evidence to be an exact substring of an archived chunk, so
    quoting the sentence itself is both the simplest honest choice and the one
    that can always be re-verified against the stored bytes. The caller is
    expected to have passed the statement through `normalise`, so that the quote,
    the body and the claim are identical strings.
    """
    title = _title(statement)
    return (
        "---\n"
        f"title: {_yaml_scalar(title)}\n"
        f"date: {date.today().isoformat()}\n"
        "document_type: note\n"
        "claims:\n"
        f"  - key: {_yaml_scalar(key)}\n"
        f"    claim: {_yaml_scalar(statement)}\n"
        f"    value: {_yaml_scalar(statement)}\n"
        f"    evidence: {_yaml_scalar(statement)}\n"
        "---\n\n"
        f"# {title}\n\n"
        f"{statement}\n"
    )


def validate_statement(statement: str) -> str:
    """Reject an unusable statement with a message that says what to do."""
    statement = normalise(statement)
    if not statement:
        raise RememberError(
            "the statement is empty; remember something, "
            'for example: mindpalace remember "Production uses PostgreSQL."'
        )
    if len(statement) > MAX_STATEMENT:
        raise RememberError(
            f"the statement is {len(statement)} characters; the limit is {MAX_STATEMENT}. "
            "Remember a shorter fact, or use --file to archive a longer document."
        )
    return statement


def resolve(statement: str, key: str | None = None) -> tuple[str, str]:
    """The identity one statement resolves to: its claim key and its document path.

    One function, because three call sites recomputing this is three places for
    the key and the path to disagree -- and a caller that retries with the wrong
    key creates a second memory instead of recognising the first.
    """
    resolved = claim_key(statement, key)
    return resolved, memory_path(resolved)


async def ensure_corpus(corpus: str, corpus_id: str | None = None) -> str:
    """Resolve a corpus to its id, creating it if this is its first write.

    A first memory should not require a separate "create a namespace" step. The
    default corpus already exists, created by migration `002`, so this is a
    no-op for the common case.
    """
    from api.services.corpora import get_or_create_corpus
    from api.services.db import session_scope

    validate_corpus_name(corpus)
    if corpus_id is not None:
        return corpus_id
    if corpus == "default":
        return DEFAULT_CORPUS_ID
    async with session_scope() as db:
        row = await get_or_create_corpus(db, corpus)
    return row["id"]


async def remember(
    statement: str | None = None,
    *,
    corpus: str = "default",
    corpus_id: str | None = None,
    key: str | None = None,
    file: str | None = None,
) -> dict:
    """Record one statement as authoritative memory. Returns the write result.

    The result carries the identity that was written -- `key`, `path`,
    `version_id` and `event` -- so a caller never has to recompute it.

    `file` reads the statement from a Markdown file instead of an argument. A file
    that already carries authored `claims:` is archived under its own path with
    those claims intact, so importing a structured document never discards richer
    memory the author wrote down. A file without claims is treated as a statement.

    `corpus_id` lets a caller that already resolved the corpus skip the lookup.
    """
    from api.services import telemetry

    with telemetry.span("memory.remember", corpus=corpus, from_file=file is not None):
        corpus_id = await ensure_corpus(corpus, corpus_id)

        if file is not None:
            from api.services.parser import parse_markdown

            content = _read(file)
            metadata, _ = parse_markdown(content)
            document_path = _document_path(file)
            if "claims" in metadata:
                # An authored document: keep every claim it declares, under a
                # project-relative path so the stored identity does not carry the
                # absolute layout of the machine that happened to import it.
                return await _write(content, document_path, corpus_id, key=None)
            statement = content

        if statement is None:
            raise RememberError(
                "nothing to remember; pass a statement, "
                'for example: mindpalace remember "Production uses PostgreSQL."'
            )

        statement = validate_statement(statement)
        resolved_key, path = resolve(statement, key)
        return await _write(
            render_statement(statement, resolved_key), path, corpus_id, resolved_key
        )


def _document_path(file: str) -> str:
    """Where a file's memory lives: project-relative when it can be, namespaced always.

    A stored path is part of a memory's identity, so an absolute path would tie a
    document to one machine's directory layout, and a bare basename would collide
    between two `notes.md` in different directories.
    """
    path = Path(file)
    try:
        relative = path.resolve().relative_to(Path.cwd().resolve())
        candidate = relative.as_posix()
        if not candidate.startswith(".."):
            return candidate
    except (ValueError, OSError):
        pass
    digest = hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()[:12]
    return f"{MEMORY_PATH_PREFIX}{_SLUG_UNSAFE.sub('-', path.stem.lower())}-{digest}.md"


def _read(file: str) -> str:
    """Read a local file, refusing anything the archive cannot store as text."""
    try:
        content = Path(file).read_text(encoding="utf-8")
    except OSError as exc:
        raise RememberError(f"cannot read {file}: {exc.strerror or exc}") from exc
    except (UnicodeDecodeError, ValueError) as exc:
        raise RememberError(
            f"{file} is not text Mind Palace can store: {exc}. "
            "Remember a statement, or a Markdown file."
        ) from exc
    if not content.strip():
        raise RememberError(f"{file} is empty; there is nothing to remember")
    return content


async def _write(content: str, path: str, corpus_id: str, key: str | None) -> dict:
    """Hand one rendered document to the ingestion service, with its identity.

    `require_embeddings=False`: a first memory must not depend on a model
    download. Ranking degrades to lexical; the claim and its evidence are recorded
    either way, and `mindpalace reindex` fills the vectors in later.
    """
    written = await IngestionService(memory_enabled=True, require_embeddings=False).ingest_file(
        content=content, path=path, corpus_id=corpus_id
    )
    return {"key": key, "path": path, **written}
