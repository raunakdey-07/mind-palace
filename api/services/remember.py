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

from api.services.corpora import (
    DEFAULT_CORPUS_ID,
    get_corpus_by_name,
    validate_corpus_name,
)
from api.services.ingestion import IngestionService
from api.services import similarity

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


#: Cosine at or above which a cached vector counts as "the same fact". 0.86 is a
#: paraphrase with the same subject and no new content, which is what a
#: contradiction usually looks like when both sides were written deliberately.
CONFLICT_VECTOR_THRESHOLD = 0.86

#: Weighted overlap at or above which a stored claim is worth showing back to the
#: writer. 0.60 on the idf measure in `api/services/similarity`, which is the same
#: 0.60 the raw measure used: dropping the boilerplate shrank what fires, not where
#: the line sits. Measured over 2,000 claims from twelve templates, raw overlap
#: fires on 166 per query and the weighted measure on 105, and both find the
#: query's own claim on 40 of 40 samples. See that module for the table and for why
#: raising the threshold instead is the wrong repair.
CONFLICT_HINT_THRESHOLD = 0.60

#: How many near-duplicates a write reports. Three is enough to recognise a
#: decision that already exists and is enough to see a cluster around one topic.
CONFLICT_HINT_LIMIT = 3


class RememberError(ValueError):
    """A remembered statement that cannot become a memory."""


def _threshold() -> float:
    import os

    try:
        value = float(os.getenv("MEMORY_CONFLICT_HINT", CONFLICT_HINT_THRESHOLD))
    except ValueError:
        return CONFLICT_HINT_THRESHOLD
    return value if 0.0 <= value <= 1.0 else CONFLICT_HINT_THRESHOLD


def _vector_threshold() -> float:
    import os

    try:
        value = float(os.getenv("MEMORY_CONFLICT_VECTOR", CONFLICT_VECTOR_THRESHOLD))
    except ValueError:
        return CONFLICT_VECTOR_THRESHOLD
    return value if 0.0 <= value <= 1.0 else CONFLICT_VECTOR_THRESHOLD


def _hint_row(claim, score, channel) -> dict:
    return {
        "claim_id": claim["id"],
        "key": claim["key"],
        "claim": claim["claim"],
        "version_id": claim["version_id"],
        "score": round(score, 4),
        "channel": channel,
        "hint": f"Possible supersession or conflict with claim {claim['id']}",
    }


def _names(claim_id: str, references: set[str]) -> bool:
    """Whether one of the references names this claim.

    A reference may be a full claim id or the prefix of one -- the twelve
    characters `recall` prints -- and a prefix that names a claim is the same
    claim.
    """
    return any(claim_id == reference or claim_id.startswith(reference) for reference in references)


async def related_active(
    statement: str, corpus_id: str, *, exclude_ids: set[str] | None = None
) -> list[dict]:
    """Live claims that say roughly what `statement` says, for the writer to judge.

    The archive cannot tell whether a new claim contradicts, duplicates or
    supersedes an existing one, because "roughly the same" is not a rule it can
    apply. What it can do is say which live claims are close enough that the
    author should look, so an agent writing over MCP can link or retire them in
    the same turn instead of leaving two contradictory answers in the corpus.

    Two channels. The vector one answers whenever it can; the lexical one is the
    fallback for when it cannot, and every row says which one produced it.

    **Vector**, over an HNSW index of every cached claim vector. A full-corpus
    nearest-neighbour lookup, so nothing is excluded for being old, and it is the
    only channel that finds a contradiction phrased differently: "the datastore is
    PostgreSQL" and "the datastore is Postgres 15" share three tokens and are the
    same claim. It runs only when an embedding model is already resident in this
    process, because a first memory must not pay for a five-second import.

    **Lexical**, over every active claim, unbounded. It is the fallback rather than
    a co-equal channel because of what it does on a corpus where claims share
    vocabulary. Measured over 2,000 synthetic claims that differ only in a serial
    number, a 0.60 token-overlap threshold fired on **168 of 2,000** claims for any
    given query, and the nearest-neighbour search returned **2.95** well-separated
    matches. Reporting three of 168 is noise presented as a conflict; the vector
    channel is both cheaper (a 3.8 ms index scan against a 9.5 ms full scan in
    Python) and more precise.

    So lexical runs only when there is nothing to run: no resident model, or no
    cached vector for anything in this corpus. That is also the case where it is
    the only evidence there is, so the hint is present exactly when the alternative
    is absent, rather than absent exactly when it is most needed.

    Never raises. The write it annotates has already succeeded, and failing it now
    would turn a hint into a gate.
    """
    from api.services import memory

    words = similarity.terms(statement)
    if not words:
        return []
    from api.services.db import session_scope

    try:
        async with session_scope() as db:
            found = await _vector_channel(memory, db, statement, corpus_id, exclude_ids)
            if found:
                return _rank(found)
            active = await memory._active_claims(db, corpus_id, limit=None)
    except Exception:  # noqa: BLE001 - a hint must never fail the write it annotates
        return []

    threshold = _threshold()
    # Weighted against the whole live set, so a word every claim carries stops
    # counting as evidence that any one of them is about this statement.
    frequencies = similarity.document_frequencies(similarity.terms(c["claim"]) for c in active)
    total = len(active)
    for claim in active:
        # A claim the caller already named is settled. Suggesting it back would be
        # telling them to do the thing they just did. The name may be a prefix
        # of the id -- that is the form `recall` prints -- so the comparison is
        # a prefix comparison.
        if exclude_ids and _names(claim["id"], exclude_ids):
            continue
        overlap = similarity.weighted_overlap(
            words, similarity.terms(claim["claim"]), frequencies, total
        )
        if overlap >= threshold:
            found[claim["id"]] = _hint_row(claim, overlap, "lexical")
    return _rank(found)


def _rank(found: dict[str, dict]) -> list[dict]:
    ordered = sorted(found.values(), key=lambda item: (-item["score"], item["claim_id"]))
    return ordered[:CONFLICT_HINT_LIMIT]


async def _vector_channel(memory, db, statement, corpus_id, exclude_ids) -> dict[str, dict]:
    """The cached-vector half of the hint, or nothing when no embedder is available.

    Gated on `Embedder.loaded`, not merely on the class existing. `remember`
    deliberately does not load the model: importing `sentence_transformers` costs
    about five seconds and a first memory on a fresh machine would otherwise cost a
    model download as well. So the vector channel runs when something else in this
    process already paid for the load -- the runtime, a `recall`, an ingest -- and
    is skipped otherwise. That is the same rule the write path already follows for
    the document itself.

    Every row it produces carries `channel: "vector"` so a caller can tell which
    half answered. An embedder that fails to load is a missing channel, not a
    failed write.
    """
    from api.services.claim_embeddings import representation_hash, representation_of
    from api.services.embedder import Embedder

    embedder = Embedder()
    if not embedder.loaded:
        return {}
    try:
        # The same representation shape `representation` builds for a claim, so the
        # query vector lands in the same space as the cached claim vectors.
        vector = embedder.embed([representation_of(statement, "", "")])[0]
        rows = await memory._nearest_active_claims(
            db,
            corpus_id,
            vector,
            model=embedder.model_name,
            dimension=embedder.dimension,
            limit=CONFLICT_HINT_LIMIT,
        )
    except Exception:  # noqa: BLE001 - see the docstring; a channel, not a gate
        return {}

    threshold = _vector_threshold()
    out: dict[str, dict] = {}
    for row in rows:
        if exclude_ids and _names(row["id"], exclude_ids):
            continue
        if row["similarity"] < threshold:
            continue
        # The cached vector is only good for the text it was made from. Recomputing
        # the hash from the row's own current fields is the same check
        # `load_cached` does, and it is why a claim edited since its last
        # embedding cannot be reported with a score computed from the old text.
        current = representation_hash(representation_of(row["claim"], row["key"], row["path"]))
        if current != row["representation_hash"]:
            continue
        claim = {
            "id": row["id"],
            "key": row["key"],
            "claim": row["claim"],
            "version_id": row["version_id"],
        }
        out[row["id"]] = _hint_row(claim, row["similarity"], "vector")
    return out


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

    Resolution still goes through the authorization choke point, including for the
    `default` short circuit: `default` is a namespace like any other, and a
    credential that was not granted it must not be able to write into it by
    naming it. In `required` mode the implicit `default` therefore does not act as
    a shared tenant namespace -- it has to be granted.
    """
    from api.services.corpora import get_or_create_corpus
    from api.services.db import session_scope

    from api.services.authz import require_corpus
    from api.services.db import current_principal

    validate_corpus_name(corpus)
    require_corpus(current_principal(), corpus, write=True)
    if corpus_id is not None:
        return corpus_id
    if corpus == "default":
        async with session_scope() as db:
            row = await get_corpus_by_name(db, corpus)
        return row["id"] if row else DEFAULT_CORPUS_ID
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
    supersedes_claim_id: str | None = None,
    topic_key: str | None = None,
) -> dict:
    """Record one statement as authoritative memory. Returns the write result.

    The result carries the identity that was written -- `key`, `path`,
    `version_id` and `event` -- so a caller never has to recompute it.

    `file` reads the statement from a Markdown file instead of an argument. A file
    that already carries authored `claims:` is archived under its own path with
    those claims intact, so importing a structured document never discards richer
    memory the author wrote down. A file with neither an authored block nor any
    structure to read -- a single paragraph -- is treated as a statement.

    `supersedes_claim_id` names a claim in this corpus that the new one replaces,
    wherever it lives. `topic_key` supersedes whatever is currently active under
    that key instead, which is how `adr-002.md` retires `adr-001.md` without
    either file being edited.

    `corpus_id` lets a caller that already resolved the corpus skip the lookup.
    """
    from api.services import telemetry

    # One recorder, so every surface that calls `remember` is logged without each
    # of them remembering to. See `api/services/query_log.py` for what is kept.
    from api.services.query_log import Call

    call = Call("remember", statement or "", corpus=corpus)
    with telemetry.span("memory.remember", corpus=corpus, from_file=file is not None):
        corpus_id = await ensure_corpus(corpus, corpus_id)

        if file is not None:
            from api.services.markdown_claims import extract
            from api.services.parser import (
                chunk_with_heading_paths,
                extract_sections_with_paths,
                parse_markdown,
            )

            content = _read(file)
            metadata, body, problems = parse_markdown(content, path=file, collect_warnings=True)
            document_path = _document_path(file)
            if "claims" in metadata:
                # An authored document: keep every claim it declares, under a
                # project-relative path so the stored identity does not carry the
                # absolute layout of the machine that happened to import it.
                return await _write(
                    content,
                    document_path,
                    corpus_id,
                    key=None,
                    supersedes_claim_id=supersedes_claim_id,
                    topic_key=topic_key,
                )
            structural, _warnings = (
                extract(
                    body,
                    [
                        {**chunk, "order_index": index}
                        for index, chunk in enumerate(
                            chunk_with_heading_paths(extract_sections_with_paths(body))
                        )
                    ],
                    path=document_path,
                )
                # Never on a document whose frontmatter did not parse: `parse_markdown`
                # returns the whole raw file as the body in that case, so reading
                # structure out of it builds claims from the discarded block.
                if not problems
                else ([], [])
            )
            if structural:
                # An ADR, a runbook, a DECISIONS.md. Its decisions are in its
                # structure, so it is archived as a document with those claims
                # rather than collapsed into one statement that says nothing the
                # file did not already say in more detail.
                return await _write(
                    content,
                    document_path,
                    corpus_id,
                    key=None,
                    supersedes_claim_id=supersedes_claim_id,
                    topic_key=topic_key,
                )
            statement = content

        if statement is None:
            raise RememberError(
                "nothing to remember; pass a statement, "
                'for example: mindpalace remember "Production uses PostgreSQL."'
            )

        statement = validate_statement(statement)
        resolved_key, path = resolve(statement, key)
        # Read the archive BEFORE the write, not after. Afterwards the claim just
        # written is itself active and is always its own nearest neighbour, which
        # is a hint about nothing.
        related = await related_active(
            statement, corpus_id, exclude_ids={supersedes_claim_id} if supersedes_claim_id else None
        )
        written = await _write(
            render_statement(statement, resolved_key),
            path,
            corpus_id,
            resolved_key,
            supersedes_claim_id=supersedes_claim_id,
            topic_key=topic_key or key,
        )
        if related:
            written["related_active_claims"] = related
        call.log(
            match_status="confident",
            returned_claim_ids=[],
            note=f"event={written.get('event')}",
        )
        written["query_id"] = call.query_id
        return written


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
    """Read a local file, refusing anything the archive cannot store as text.

    The ingest-root check lives here rather than at each caller because every
    `file=` caller -- the CLI, `POST /api/memory/remember`, the SDK and the MCP
    tool -- routes through this one function. Two surfaces enforced
    MIND_PALACE_INGEST_ROOTS (`/api/ingest/repo` and `/api/corpora/{name}/sync`)
    and this one did not, so a path the deployment refused came back through the
    other door. One guard here closes all four.
    """
    from api.services.ingest_paths import IngestPathRefused, require_permitted

    try:
        file = str(require_permitted(file))
    except IngestPathRefused as exc:
        # Translated rather than re-raised: `remember` raises RememberError, and
        # a caller catching that one type should not have to know about a second.
        # The message is `require_permitted`'s, which names no allowed root.
        raise RememberError(str(exc)) from exc

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


async def _write(
    content: str,
    path: str,
    corpus_id: str,
    key: str | None,
    *,
    supersedes_claim_id: str | None = None,
    topic_key: str | None = None,
) -> dict:
    """Hand one rendered document to the ingestion service, with its identity.

    `require_embeddings=False`: a first memory must not depend on a model
    download. Ranking degrades to lexical; the claim and its evidence are recorded
    either way, and `mindpalace reindex` fills the vectors in later.
    """
    from api.services.memory import SupersessionTargetUnresolved

    try:
        written = await IngestionService(memory_enabled=True, require_embeddings=False).ingest_file(
            content=content,
            path=path,
            corpus_id=corpus_id,
            supersedes_claim_id=supersedes_claim_id,
            topic_key=topic_key,
        )
    except SupersessionTargetUnresolved as exc:
        # A caller who named a claim is retiring that claim. If the reference
        # names nothing, or names two, the write is refused and the error says
        # which reference failed -- rather than landing a claim with no edge,
        # which reads as a fresh fact and leaves the old one standing.
        raise RememberError(str(exc)) from exc
    return {"key": key, "path": path, **written}
