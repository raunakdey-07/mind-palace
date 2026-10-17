# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Raunak Dey

"""Ingestion service for Markdown processing."""

from __future__ import annotations

import heapq
import logging
import time
from pathlib import Path
from typing import Optional

from sqlalchemy import text

from api.services.corpora import DEFAULT_CORPUS_ID
from api.services.db import session_scope
from api.services.embedder import Embedder
from api.services.ingest_paths import require_permitted
from api.services import markdown_claims
from api.services.parser import (
    chunk_with_heading_paths,
    extract_sections_with_paths,
    extract_title,
    parse_markdown,
)
from api.services.repository import (
    check_manifest,
    content_hash,
    delete_chunks_for_doc,
    insert_chunks,
    update_manifest,
    upsert_document,
)

logger = logging.getLogger(__name__)

# Fallback document type when frontmatter is missing or invalid.
DEFAULT_DOC_TYPE = "note"
MAX_SYNC_ERRORS = 50
#: Below this many written rows a sync cannot have moved the planner off a
#: heuristic estimate, so statistics are not worth the ANALYZE.
STATISTICS_REFRESH_MINIMUM = 200


def _sync_error(path: str, exc: Exception) -> str:
    """Return a bounded, non-sensitive sync failure description."""
    reason = getattr(exc, "reason", None) or type(exc).__name__
    return f"{path}: {reason}"


def _reference_targets(rels: list[str], reference: str) -> str | None:
    """The file a path-shaped supersession reference names, or None if it names none.

    The matching rules are `_targets_for_reference`'s: exact first, then a unique
    path suffix, because an author writes the shortest thing that identifies a
    document. `0001-use-pg15.md` and `examples/adr/0001-use-pg15.md` are one
    document, and two files called `0001.md` in different directories are an
    ambiguous reference rather than a match.

    A claim key is not a path, so it cannot be resolved from filenames here. The
    document holding a claim is not knowable before the documents are read, which
    is why ordering covers references and leaves keys to the per-document path.
    """
    from api.services.markdown_claims import looks_like_path

    if not looks_like_path(reference):
        return None
    needle = reference.replace("\\", "/").casefold().lstrip("./")
    for rel in rels:
        if rel.casefold() == needle:
            return rel
    suffixed = [rel for rel in rels if rel.casefold().endswith(f"/{needle}")]
    return suffixed[0] if len(suffixed) == 1 else None


def _supersession_order(repo_path: Path, md_files: list[Path]) -> list[str]:
    """Order `md_files` so every referenced document is ingested before the one naming it.

    Returns relative paths in a new order, every input included exactly once.

    Sorting alone gets this wrong often enough to matter. A superseder sorts ahead
    of its target whenever the shorter name sorts first, because `-` is below `r`,
    so `ad-002.md` precedes `adr/0001.md`. Ingesting the superseder first leaves
    its reference pointing at a document the archive has not seen, and the
    supersession silently does not happen -- or, before the archive learned to
    report that, aborts the whole sync.

    That is an accident of `strcmp`, not a mistake by the author, so the archive
    fixes the order rather than asking anyone to rename a file to suit it. Both
    directions are ordered the same way: a document that *names* another is
    ingested after the one it names, because a reference resolves against what is
    already there.

    A cycle -- A supersedes B while B supersedes A -- leaves both in their sorted
    positions rather than dropping either. The per-document path then reports each
    unresolved reference, which is the honest description of what happened.
    """
    rels = [str(md_file.relative_to(repo_path)) for md_file in md_files]
    by_rel = {str(md_file.relative_to(repo_path)): md_file for md_file in md_files}

    # referenced -> referencing, so a Kahn pass emits the referenced document first.
    successors: dict[str, set[str]] = {rel: set() for rel in rels}
    indegree: dict[str, int] = {rel: 0 for rel in rels}
    for rel in rels:
        try:
            metadata, body = parse_markdown(by_rel[rel].read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            # Unreadable here and unreadable below; the loop records the failure
            # for this path, and a file with no visible body declares nothing.
            continue
        supersedes, superseded_by = markdown_claims.supersession_references(metadata, body)
        for reference in (*supersedes, *superseded_by):
            target = _reference_targets(rels, reference)
            if target is None or target == rel or rel in successors[target]:
                continue
            successors[target].add(rel)
            indegree[rel] += 1

    ready = [rel for rel in rels if indegree[rel] == 0]
    heapq.heapify(ready)
    ordered: list[str] = []
    while ready:
        rel = heapq.heappop(ready)
        ordered.append(rel)
        for follower in successors[rel]:
            indegree[follower] -= 1
            if indegree[follower] == 0:
                heapq.heappush(ready, follower)
    # Whatever a cycle left behind, in the order it came in.
    ordered.extend(sorted(set(rels) - set(ordered)))
    return ordered


class IngestionService:
    """Service for ingesting Markdown documents."""

    def __init__(self, *, memory_enabled: bool = False, require_embeddings: bool = True):
        self.embedder = Embedder()
        self.memory_enabled = memory_enabled
        # `remember` records authoritative memory and sets this False, so a first
        # memory does not depend on a model download. Bulk ingestion leaves it True:
        # silently accepting a directory of documents and producing a corpus that
        # cannot be searched would be worse than failing.
        self.require_embeddings = require_embeddings

    def _embed_records(self, records: list[dict]) -> tuple[str | None, int | None]:
        """Embed chunk records, or skip the model entirely when the caller allows it.

        Embeddings are L2: `rehydrate` rebuilds them from the archive, and the read
        path already treats them as an optimization. So when the caller has said it
        does not need them, the model is never loaded -- not attempted-and-caught,
        which would still block on a first-run download -- and a missing model
        costs retrieval quality and nothing else. The authoritative claim and its
        evidence are still recorded.

        Returns the (model_name, dimension) to persist. Both are None when no
        vector was produced, which the schema already permits.
        """
        if not self.require_embeddings:
            logger.info("embedding_skipped", extra={"chunks": len(records)})
            return None, None
        embeddings = self.embedder.embed([r["text"] for r in records])
        if len(embeddings) != len(records):
            raise ValueError("embedding count does not match chunk count")
        for record, embedding in zip(records, embeddings):
            record["embedding"] = embedding
        return self.embedder.model_name, self.embedder.dimension

    async def _ingest_content(
        self,
        db,
        content: str,
        path: str,
        corpus_id: str,
        owner_principal_id: str | None = None,
        *,
        supersedes_claim_id: str | None = None,
        topic_key: str | None = None,
    ) -> dict:
        """Shared ingestion pipeline for one parsed document.

        The caller owns the session lifetime. Returns a result dict with
        ``success``, ``message``, and (on success) ``document_id``/``chunk_count``.
        """
        from api.services import memory

        async with db.begin():
            await memory.lock_corpus(db, corpus_id)
            metadata, body = parse_markdown(content)
            # Plain Markdown with no authored `claims:` block used to be indexed as
            # text and nothing else, so recall abstained on every file in the
            # corpus. Three conditions could turn the memory path on and none of
            # them did: an ADR has no `claims:` block, and on a fresh corpus there
            # is no archive for `memory.enabled()` to find. The fourth is here --
            # the document has structure worth reading.
            use_memory = (
                self.memory_enabled
                or "claims" in metadata
                or await memory.enabled(db, corpus_id)
                or markdown_claims.has_claims(body)
            )
            if use_memory:
                result = await self._ingest_memory_content(
                    db,
                    content,
                    path,
                    corpus_id,
                    owner_principal_id,
                    supersedes_claim_id=supersedes_claim_id,
                    topic_key=topic_key,
                )
            else:
                result = await self._ingest_live_content(
                    db, content, path, corpus_id, owner_principal_id
                )
        logging.getLogger(__name__).info(
            "document_ingested",
            extra={
                "corpus_id": corpus_id,
                "document_id": result.get("document_id"),
                "event": result.get("event"),
                "memory_enabled": use_memory,
            },
        )
        return result

    async def _ingest_live_content(
        self,
        db,
        content: str,
        path: str,
        corpus_id: str,
        owner_principal_id: str | None = None,
    ) -> dict:
        # Warnings are collected here, not only on the memory path. This is the
        # path `ingest-repo` takes, and it was the one that reported an
        # unparseable `claims:` block as `no_claims_declared` -- which reads as
        # "you wrote no claims" when the truth is "your claims were discarded".
        # The parser already produced the warning, with a line and a reason code;
        # it was simply never asked for them.
        metadata, body, frontmatter_warnings = parse_markdown(content, collect_warnings=True)
        doc_hash = content_hash(body)

        # Every return below carries the warnings, including the two that do no
        # work. A re-sync of an unchanged broken document must say it is still
        # broken, or the author sees silence on every run after the first.
        if not body.strip():
            return {
                "success": True,
                "warnings": [w.as_dict() for w in frontmatter_warnings],
                "message": f"Skipped empty document: {path}",
                "chunk_count": 0,
            }

        if await check_manifest(db, path, doc_hash, corpus_id):
            return {
                "success": True,
                "warnings": [w.as_dict() for w in frontmatter_warnings],
                "message": "Document unchanged (manifest), skipped",
                "event": "UNCHANGED",
                "chunk_count": 0,
            }

        doc_id = await upsert_document(
            db,
            extract_title(metadata),
            path,
            body,
            metadata,
            corpus_id=corpus_id,
        )
        if not doc_id:
            return {
                "success": True,
                "message": "Document unchanged, skipped",
                "chunk_count": 0,
            }

        # Replace stale chunks with the freshly computed ones.
        await delete_chunks_for_doc(db, corpus_id, doc_id, commit=False)

        sections = extract_sections_with_paths(body)
        chunks = chunk_with_heading_paths(sections)
        chunk_records = [
            {
                "text": c["text"],
                "order_index": i,
                "heading_path": c["heading_path"],
                "token_count": c["token_count"],
            }
            for i, c in enumerate(chunks)
        ]
        embedding_model, embedding_dimension = self._embed_records(chunk_records)

        doc_type = metadata.get("type") or metadata.get("document_type") or DEFAULT_DOC_TYPE
        tags = metadata.get("tags", [])

        count = await insert_chunks(
            db,
            corpus_id,
            doc_id,
            doc_type,
            tags,
            chunk_records,
            embedding_model,
            embedding_dimension,
            "1.0",
            commit=False,
        )

        await update_manifest(db, path, doc_hash, doc_id, count, corpus_id, commit=False)

        return {
            "success": True,
            "warnings": [w.as_dict() for w in frontmatter_warnings],
            "document_id": doc_id,
            "chunk_count": count,
            "message": f"Ingested {count} chunks",
        }

    async def _ingest_memory_content(
        self,
        db,
        content: str,
        path: str,
        corpus_id: str,
        owner_principal_id: str | None = None,
        *,
        supersedes_claim_id: str | None = None,
        topic_key: str | None = None,
    ) -> dict:
        """One caller-owned transaction for the archive and live-index projection."""
        from api.services import memory

        await memory.lock_corpus(db, corpus_id)
        metadata, body, frontmatter_warnings = parse_markdown(
            content, path=path, collect_warnings=True
        )
        for warning in frontmatter_warnings:
            # A document whose frontmatter was not usable is a *different*
            # document from the one the author wrote, and the difference decides
            # whether it produced memory. Logging it here, at the only place that
            # knows the path, is what makes it visible without the caller having
            # to re-read the file to find out why.
            logging.getLogger(__name__).warning(
                "frontmatter_problem",
                extra={"corpus_id": corpus_id, **warning.as_dict()},
            )
        chunks = chunk_with_heading_paths(extract_sections_with_paths(body))
        records = [{**chunk, "order_index": i} for i, chunk in enumerate(chunks)]
        # What the document says about its own history, in frontmatter or in a body
        # line. Read even when the document carries an authored `claims:` block,
        # because declaring a supersession is a statement about the document, not a
        # claim for the archive to extract.
        declared_supersedes, _superseded_by = markdown_claims.supersession_references(
            metadata, body
        )
        # Validate before embedding or any live mutation. No inferred claims.
        # The third element is the authored claims, and until now it was
        # discarded -- so nothing downstream could say how much memory a document
        # actually produced. `sync` reported success on a document whose claims
        # block had never parsed. Carrying it through is what lets the caller
        # report documents *and* claims instead of a file count.
        _meta, _archived, claims = memory.validate_source(path, content, metadata, records)
        if not claims and "claims" not in metadata and not frontmatter_warnings:
            # Plain Markdown. An ADR, a runbook, a DECISIONS.md: no `claims:`
            # block, and therefore nothing for recall to answer from. The
            # structural reader fills that gap deterministically, with no model
            # and no inference, and each claim it produces is a line of the file
            # quoted back.
            #
            # It is a fallback and not a default, because an authored `claims:`
            # block is the author naming their own keys and this has no business
            # producing a second opinion beside it.
            #
            # And never on a document whose frontmatter did not parse. When that
            # happens `parse_markdown` hands back the whole raw file as the body,
            # frontmatter included, because there is no way to tell where the
            # block was supposed to end. Reading structure out of that produces
            # claims from the discarded `claims:` block itself -- the author gets
            # memory fabricated from what was thrown away, with the
            # `frontmatter_unparseable` warning still sitting underneath it. The
            # warning is the honest answer and it is already reported.
            derived, extraction_warnings = markdown_claims.claims_for(
                metadata, body, records, path=path
            )
            for warning in extraction_warnings:
                logging.getLogger(__name__).warning(
                    "claim_extraction_problem",
                    extra={"corpus_id": corpus_id, **warning.as_dict()},
                )
                # Same dict shape as a frontmatter warning, so every consumer of
                # the ingestion report reads one list instead of two.
                frontmatter_warnings.append(warning)
            if derived and derived["claims"]:
                # Re-validate against the augmented metadata. The extractor only
                # emits evidence it has already located in an archived chunk, so
                # this cannot fail on the quote rule -- but the archive is the
                # only thing allowed to decide that, not this function.
                metadata = {**metadata, **derived}
                _meta, _archived, claims = memory.validate_source(path, content, metadata, records)
            elif not extraction_warnings:
                logging.getLogger(__name__).info(
                    "no_structural_claims",
                    extra={"corpus_id": corpus_id, "path": path},
                )
        previous = await memory._latest(db, corpus_id, memory.memory_document_id(corpus_id, path))
        fingerprint = memory._hash(content, metadata)
        live = (
            await db.execute(
                text("SELECT id FROM documents WHERE corpus_id = :c AND path = :p"),
                {"c": corpus_id, "p": path},
            )
        ).scalar_one_or_none()
        if (
            previous
            and previous["event"] != "DELETED"
            and previous["fingerprint"] == fingerprint
            and live
        ):
            return {
                "success": True,
                "warnings": [w.as_dict() for w in frontmatter_warnings],
                "document_id": live,
                "chunk_count": 0,
                "claim_count": len(claims),
                "version_id": previous["id"],
                "event": "UNCHANGED",
                "message": "Document unchanged, skipped",
            }

        embedding_model, embedding_dimension = self._embed_records(records)
        doc_id = await upsert_document(
            db,
            extract_title(metadata),
            path,
            body,
            metadata,
            corpus_id=corpus_id,
            force=True,
        )
        await delete_chunks_for_doc(db, corpus_id, doc_id, commit=False)
        doc_type = metadata.get("document_type") or metadata.get("type") or DEFAULT_DOC_TYPE
        if doc_type not in ("project", "kaggle", "note", "paper"):
            doc_type = DEFAULT_DOC_TYPE
        tags = metadata.get("tags", [])
        if isinstance(tags, str):
            tags = [tag.strip() for tag in tags.split(",")]
        count = await insert_chunks(
            db,
            corpus_id,
            doc_id,
            doc_type,
            tags,
            records,
            embedding_model,
            embedding_dimension,
            "1.0",
            commit=False,
        )
        version = await memory.record_version(
            db,
            corpus_id,
            path,
            doc_id,
            content,
            metadata,
            records,
            owner_principal_id,
            supersedes_claim_id=supersedes_claim_id,
            topic_key=topic_key,
            supersedes_refs=declared_supersedes,
        )
        await update_manifest(
            db, path, content_hash(content), doc_id, count, corpus_id, commit=False
        )
        # L2 follows L1. Populate the claim representation cache here rather
        # than during a read, so a query never writes and a read path that
        # cannot write still answers correctly, just more slowly.
        await self._cache_claim_vectors(db, corpus_id, [version["id"]])
        # A supersession reference that resolved to nothing is reported here
        # rather than raised, so the document lands with the claims it does state
        # and the edge that did not happen is on the record. Same dict shape as
        # every other ingestion warning, so one reader handles them all. The
        # reference goes in `field` because it is an identifier -- a path or a
        # claim key -- and without it the warning cannot be acted on.
        report = [w.as_dict() for w in frontmatter_warnings]
        for unresolved in version.get("supersession_unresolved") or []:
            logging.getLogger(__name__).warning(
                "supersession_unresolved",
                extra={"corpus_id": corpus_id, "path": path, "reference": unresolved.reference},
            )
            report.append(
                {
                    "code": "supersession_unresolved",
                    "path": path,
                    "line": None,
                    "field": unresolved.reference,
                }
            )
        return {
            "success": True,
            "warnings": report,
            "document_id": doc_id,
            "chunk_count": count,
            "claim_count": len(claims),
            "version_id": version["id"],
            "event": version["event"],
            "message": f"Ingested {count} chunks with version history",
        }

    async def _cache_claim_vectors(self, db, corpus_id: str, version_ids: list[str]) -> int:
        """Best-effort fill of the L2 claim representation cache.

        A failure here is not an ingestion failure: the claim is already
        authoritative, and the cache can always be rebuilt.

        Skipped when the caller did not require embeddings. A first memory must not
        import the model: `import sentence_transformers` measures 5.3 s on mains
        machine, and paying that on the first command a newcomer runs is worse than
        a slower ranking. `memory_query` warms this cache from the read side instead,
        from vectors it has already computed, so a corpus authored without
        embeddings is encoded once rather than on every query.
        """
        if not self.require_embeddings:
            return 0
        try:
            from api.services.claim_embeddings import pending, store

            wanted, texts = await pending(db, corpus_id, list(version_ids))
            if not wanted:
                return 0
            vectors = dict(zip(wanted, self.embedder.embed(texts)))
            return await store(
                db,
                corpus_id,
                wanted,
                vectors,
                self.embedder.model_name,
                self.embedder.dimension,
                getattr(self.embedder, "version", "ingest"),
                commit=False,
            )
        except Exception as exc:  # noqa: BLE001 - the cache is L2, never a contract
            logger.warning(
                "claim_embedding_cache_fill_failed error=%s",
                type(exc).__name__,
                # No traceback: a failed embedding write carries the claim text
                # in the constraint-violation detail, which would put a
                # customer's memory in the log. See `unexpected_error` in
                # api/main.py for the same reasoning at full length.
            )
            return 0

    async def ingest_file(
        self,
        content: str,
        path: Optional[str] = None,
        corpus_id: str = DEFAULT_CORPUS_ID,
        owner_principal_id: str | None = None,
        supersedes_claim_id: str | None = None,
        topic_key: str | None = None,
    ) -> dict:
        """Ingest a single Markdown file into the given corpus.

        `owner_principal_id` makes the document private to that principal inside
        a corpus its peers also hold. Omitted, the document is shared with
        everyone holding the corpus, which is the default and the documented
        shape. The owner must come from the authenticated principal, never from
        the request body -- a client that chose its own owner could mark a
        document as another principal's.

        `supersedes_claim_id` and `topic_key` are the cross-document supersession
        arguments; see `memory.record_version`. Both apply to this write only.
        """
        async with session_scope() as db:
            return await self._ingest_content(
                db,
                content,
                path or "unknown",
                corpus_id,
                owner_principal_id,
                supersedes_claim_id=supersedes_claim_id,
                topic_key=topic_key,
            )

    async def ingest_repo(
        self,
        repo_path: str,
        corpus_id: str = DEFAULT_CORPUS_ID,
    ) -> dict:
        """Batch ingest all Markdown files from a repository into a corpus."""
        return await self.sync_repo(repo_path, corpus_id, delete_removed=False)

    async def sync_repo(
        self,
        repo_path: str,
        corpus_id: str = DEFAULT_CORPUS_ID,
        delete_removed: bool = True,
    ) -> dict:
        """Synchronize a corpus with a source directory.

        Reconciles indexed state against the source snapshot:

        - added:     new files are ingested
        - changed:   modified files are reprocessed, stale chunks replaced
        - unchanged: skipped via manifest content-hash match
        - deleted:   files removed from the source are removed from the index
                     (when ``delete_removed`` is true)

        Returns a machine-readable sync summary. A failed document never
        leaves a false 'successfully indexed' state.

        `repo_path` is checked against `MIND_PALACE_INGEST_ROOTS` here rather
        than by each caller. The CLI and the two routes each checked it
        themselves, which left the SDK's `sync()` and the MCP `sync` tool -- both
        of which arrive here directly -- reading outside the configured roots.
        One guard at the chokepoint covers all four surfaces.
        """
        start = time.perf_counter()
        # The roots check the callers were doing individually. Returns the
        # resolved path, so a symlink or a `..` is collapsed before the walk.
        repo_path = Path(require_permitted(repo_path))
        if not repo_path.is_dir():
            return {"success": False, "message": f"Path not found: {repo_path}"}

        added = changed = unchanged = deleted = failed = 0
        total_chunks = 0
        total_claims = 0
        warnings: list[dict] = []
        # One entry per document, always. A summary that only counts successes
        # cannot answer "did my document land?", which is the question somebody
        # asks after a sync that reported success.
        documents: list[dict] = []
        errors: list[str] = []
        md_files = sorted(repo_path.rglob("*.md"))
        source_paths: set[str] = set()

        for rel_path in _supersession_order(repo_path, md_files):
            source_paths.add(rel_path)
            try:
                content = (repo_path / rel_path).read_text(encoding="utf-8")
                async with session_scope() as db:
                    known = await self._path_known(db, corpus_id, rel_path)

                state = "changed" if known else "added"
                async with session_scope() as db:
                    result = await self._ingest_content(db, content, rel_path, corpus_id)

                for warning in result.get("warnings", []):
                    warnings.append({**warning, "path": rel_path})
                    if len(errors) < MAX_SYNC_ERRORS:
                        errors.append(f"{rel_path}: {warning['code']}")

                if not result["success"]:
                    failed += 1
                    reason = str(result.get("message") or "ingestion failed")
                    if len(errors) < MAX_SYNC_ERRORS:
                        errors.append(f"{rel_path}: {reason}")
                    documents.append(
                        {
                            "path": rel_path,
                            "outcome": "failed",
                            "reason": "ingestion_failed",
                            "claims": 0,
                            "chunks": 0,
                        }
                    )
                    logging.getLogger(__name__).warning(
                        "document_ingestion_failed",
                        extra={"corpus_id": corpus_id, "path": rel_path, "reason": reason},
                    )
                    continue

                if result.get("event") == "UNCHANGED":
                    unchanged += 1
                    outcome = "unchanged"
                elif state == "added":
                    added += 1
                    outcome = "ingested"
                else:
                    changed += 1
                    outcome = "ingested"
                claims_for_doc = int(result.get("claim_count") or 0)
                total_claims += claims_for_doc
                total_chunks += result.get("chunk_count", 0)
                entry = {
                    "path": rel_path,
                    "outcome": outcome,
                    "reason": None,
                    "claims": claims_for_doc,
                    "chunks": result.get("chunk_count", 0),
                }
                # A document that parsed cleanly but declared no claims is the
                # exact shape of the dogfood failure: indexed as text, invisible
                # to recall. Saying so is the whole point of this field.
                if not claims_for_doc and not result.get("warnings"):
                    entry["reason"] = "no_claims_declared"
                    if len(errors) < MAX_SYNC_ERRORS:
                        errors.append(f"{rel_path}: no_claims_declared")
                documents.append(entry)
            except Exception as e:
                failed += 1
                reason = _sync_error(rel_path, e)
                if len(errors) < MAX_SYNC_ERRORS:
                    errors.append(reason)
                documents.append(
                    {
                        "path": rel_path,
                        "outcome": "failed",
                        "reason": type(e).__name__,
                        "claims": 0,
                        "chunks": 0,
                    }
                )
                logging.getLogger(__name__).warning(
                    "document_ingestion_failed",
                    extra={
                        "corpus_id": corpus_id,
                        "path": rel_path,
                        "error_type": type(e).__name__,
                        "reason": reason,
                    },
                )

        if delete_removed:
            async with session_scope() as db:
                async with db.begin():
                    from api.services import memory

                    await memory.lock_corpus(db, corpus_id)
                    use_memory = self.memory_enabled or await memory.enabled(db, corpus_id)
                    removed = await self._delete_stale_paths(
                        db, corpus_id, source_paths, memory_enabled=use_memory
                    )
            deleted = removed

        await self._refresh_planner_statistics(corpus_id, added + changed + deleted)

        duration_ms = int((time.perf_counter() - start) * 1000)
        # `success` now means what a reader assumes it means: every document
        # either landed or said why it did not. A clean document that declared no
        # claims is not a failure -- plain prose is legitimate -- but it is not
        # silently equal to one that produced memory either, so it is counted and
        # named.
        return {
            "success": failed == 0 and not warnings,
            "corpus_id": corpus_id,
            "added": added,
            "changed": changed,
            "unchanged": unchanged,
            "deleted": deleted,
            "failed": failed,
            "documents": len(documents),
            "claim_count": total_claims,
            "without_claims": sum(1 for d in documents if d["reason"] == "no_claims_declared"),
            "document_results": documents,
            "warnings": warnings,
            "chunk_count": total_chunks,
            "duration_ms": duration_ms,
            "message": (
                f"sync: +{added} ~{changed} ={unchanged} -{deleted} "
                f"!{failed}, {total_chunks} chunks"
            ),
            "errors": errors,
        }

    @staticmethod
    async def _path_known(db, corpus_id: str, path: str) -> bool:
        result = await db.execute(
            text("SELECT 1 FROM documents WHERE corpus_id = :c AND path = :p"),
            {"c": corpus_id, "p": path},
        )
        return result.first() is not None

    async def _refresh_planner_statistics(self, corpus_id: str, touched: int) -> None:
        """Collect statistics once a bulk sync has written enough to change a plan.

        PostgreSQL only ever guesses how many rows a table holds, and it guesses
        badly when it has no statistics at all. ``reltuples = -1`` means "never
        analysed", and on that basis the planner estimated about 4 rows where a
        1,000-version corpus holds 1,000. It then chose an index on ``corpus_id``
        alone and applied ``version_id`` as a filter, discarding 999 of every 1,000
        rows: 1,079,038 shared buffers where 9,190 were needed, a 117x difference.
        The returned rows were byte-identical either way, so this is a cost
        problem, not a correctness one.

        Autovacuum fixes this on its own in about a minute on a committed corpus
        (measured: it fired unaided at t+55s with stock settings), so this is not
        urgent. It matters where autovacuum cannot keep up or does not run: a bulk
        load, a restore, or a database with autovacuum disabled. Right after a sync
        the rows are committed and the estimates are certainly wrong, which is
        exactly when a developer is most likely to run the query that exposes it.

        The corpus lock is taken deliberately. ANALYZE does not deadlock against the
        advisory lock used for writes, but a concurrent ANALYZE from another session
        was observed writing ``reltuples = 0`` while 200 rows were still
        uncommitted, so the two must be serialised or the statistics collected here
        will be wrong in the other direction.

        Bounded: once per sync, not once per document. Non-fatal: a failure to
        refresh statistics must never fail an otherwise successful ingest, because
        statistics affect cost only and never what a query returns. Observable: the
        outcome is logged either way.
        """
        if touched <= STATISTICS_REFRESH_MINIMUM:
            return
        tables = (
            "memory_versions",
            "memory_documents",
            "memory_chunks",
            "memory_claims",
            "memory_evidence",
        )
        try:
            async with session_scope() as db:
                async with db.begin():
                    from api.services import memory

                    await memory.lock_corpus(db, corpus_id)
                    for table in tables:
                        await db.execute(text(f"ANALYZE {table}"))
            logging.getLogger(__name__).info(
                "planner_statistics_refreshed",
                extra={
                    "corpus_id": corpus_id,
                    "event": "PLANNER_STATISTICS_REFRESHED",
                    "touched": touched,
                    "tables": len(tables),
                },
            )
        except Exception as exc:
            # Statistics are an optimisation. Losing them costs speed, not truth.
            logging.getLogger(__name__).warning(
                "planner_statistics_refresh_failed",
                extra={
                    "corpus_id": corpus_id,
                    "event": "PLANNER_STATISTICS_REFRESH_FAILED",
                    "touched": touched,
                    "error_type": type(exc).__name__,
                    "reason": _sync_error("ANALYZE", exc),
                },
            )

    @staticmethod
    async def _delete_stale_paths(
        db, corpus_id: str, keep_paths: set[str], *, memory_enabled: bool = False
    ) -> int:
        """Delete indexed documents, chunks, and manifest entries whose source
        paths no longer exist in the source directory.

        Also purges orphaned manifest rows (manifest entry survives but the
        document is gone) — a stale manifest must never cause a file to be
        reported as 'unchanged' when it is not actually indexed.
        """
        result = await db.execute(
            text("SELECT id, path FROM documents WHERE corpus_id = :c"),
            {"c": corpus_id},
        )
        stale = [(row[0], row[1]) for row in result.fetchall() if row[1] not in keep_paths]
        for doc_id, path in stale:
            if memory_enabled:
                from api.services import memory

                await memory.record_deletion(db, corpus_id, path)
            await delete_chunks_for_doc(db, corpus_id, doc_id, commit=False)
            # `documents.id` is a global primary key, so both deletes name the
            # corpus as well. The ids came from a corpus-scoped select above;
            # repeating the predicate means a change to that select cannot turn
            # this into a cross-corpus delete.
            await db.execute(
                text("DELETE FROM documents WHERE id = :d AND corpus_id = :c"),
                {"d": doc_id, "c": corpus_id},
            )
            await db.execute(
                text("DELETE FROM ingestion_manifest WHERE doc_id = :d AND corpus_id = :c"),
                {"d": doc_id, "c": corpus_id},
            )

        # Orphaned manifests: no matching document (doc_id NULL or dangling).
        keep_list = sorted(keep_paths) or ["__none__"]
        await db.execute(
            text("""
                DELETE FROM ingestion_manifest m
                WHERE m.corpus_id = :c
                  AND (m.path <> ALL(:keep))
                  AND NOT EXISTS (SELECT 1 FROM documents d WHERE d.id = m.doc_id)
            """),
            {"c": corpus_id, "keep": keep_list},
        )

        return len(stale)
