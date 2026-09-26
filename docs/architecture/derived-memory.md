# Derived memory

Scope: the boundary between what a source document asserts and what a model
infers, and what the external review says about how other systems draw that
line. Evidence for the Mind Palace side is in `api/services/memory.py`,
`api/services/memory_public.py`, `api/services/memory_query.py`,
`migrations/versions/004_memory.py`, and the two documents beside this one,
[`memory-model.md`](memory-model.md) and
[`provenance-model.md`](provenance-model.md). Evidence for the external side is
in [`../competitive/capability-matrix.md`](../competitive/capability-matrix.md),
with pins in [`../competitive/landscape.md`](../competitive/landscape.md).

## The line as it stands

A claim in this system is authored. It arrives in source front matter, it
carries a key, a value, prose and optional `[valid_from, valid_until)` bounds,
and it must be supported by at least one exact quote with character offsets into
a specific archived chunk. The database checks the quote against the chunk on
insert, checks that the offsets describe the span the quote occupies, and
refuses to commit a claim that ended up without evidence.

`memory._prepare` adds the constraint that decides the whole question. If the
claim text does not appear in a chunk that supports it, the source is rejected
with the message that semantic inference is not supported. That is not a
tuning choice. It is the reason status resolution is decidable at all:
`CURRENT` means the latest in-window source assertion that no other active
source contradicts, and a model-inferred sentence would break that definition,
because nothing in the archive would say who wrote it or when they believed it.

## How the other systems sit against that line

Graphiti derives its statements with a model and then makes the derivation
auditable a different way. Edges carry a validity window and list the episode
UUIDs that produced them, so a reader can walk a fact back to the raw data that
produced it without the fact itself being a literal span in that data. Old facts
are invalidated, not deleted, so the history stays queryable.

Mem0 derives by default and says so. The documented write path is LLM fact
extraction, and the documentation tells you to pass `infer=False` when the raw
content is what you need. The extraction path is additive, so a new fact does
not silently rewrite an old one, and correction goes through an explicit update
or delete.

MemPalace keeps the storage default verbatim and puts its model work in
separate, optional paths: general extraction into five memory types, entity
detection, closets, fact checking, and the AAAK abbreviation layer. It also
publishes the cost of its one irreversible path. AAAK is lossy, scored 84.2%
R@5 against 96.6% for raw verbatim mode, and is not the default.

Letta inverts the question. The agent is the author. Memory is Markdown files
the agent writes with ordinary file tools and commits, so the provenance is a
git commit made by a named agent rather than a span inside a source document.
That is legitimate authorship of a different kind, and it is not comparable to a
claim whose evidence offsets a database can verify.

## What a derived layer would have to carry

Nothing in this system infers claims today, and this document does not propose
that it start. If a derived layer is ever added, four things are the minimum,
and each one has a precedent in the reviewed systems.

**Provenance to a source, not just to a chunk.** Graphiti's edge-to-episode
link is the model. A derived statement would need to point at the document and
version it came from, at minimum, so a reader can go back to the source and
check the inference by hand. An offset-level span is the stronger form and is
what this repository already enforces for authored claims.

**A validity window that is separate from filing time.** Graphiti carries
`valid_at`, `invalid_at` and `expired_at` per edge. A derived statement with
only a creation time is a claim about nothing in particular, because the
inference may have been true when written and false since.

**A status that cannot be confused with an authored one.** `CURRENT` is
defined over authored assertions. A derived statement needs its own terminal
state, and relevance ranking must not be able to promote it, for the same
reason `memory_query.select` cannot promote an out-of-window claim today.

**An explicit cost.** Every one of these systems calls a model on at least one
path. Graphiti needs an LLM to ingest. Mem0 extracts by default. MemPalace runs
models for general extraction, entity detection and closets. A derived layer
would make a model call part of the write path, which is a different
operational shape from a service that performs no inference and loads its
embedding model lazily on first semantic use.

## What this does not settle

Whether a derived layer is worth building for a corpus whose documents are
written by a person who can be asked to state the assertion directly. The
review found no evidence that any of the four systems answers that question,
and the repository has no measurement that would answer it either.

The frozen held-out evaluation of the authored path does not speak to this. A
derived layer would need its own labelled questions, its own abstention checks,
and its own false-promotion counter, because a model-inferred statement promoted
to current is the failure mode that matters most.

The knowledge graph in `api/services/memory_reasoning.py` exists for research
and is excluded from the API image. It is not a derived-memory layer and does
not feed the released query path.

The current L2 rebuild path is `api/services/rehydrate.py`, invoked by
`mindpalace reindex`. It writes only live projection rows and never appends an
archive version.
