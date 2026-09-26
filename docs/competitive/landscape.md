# Competitive landscape

Read with [`capability-matrix.md`](capability-matrix.md), which carries the
pinned revisions and the per-axis findings. This document says what each system
is actually for, where the overlap with Mind Palace is real, and where it is
not. The revisions it rests on were read on 2026-09-26.

## The honest starting point

Two of the four systems here already do things this repository describes as
differentiators. MemPalace stores verbatim text, runs on the user's machine
unless they opt in to the shared-brain hub, and publishes a benchmark program
with per-question result files and a held-out split. Letta's MemFS keeps memory
in a git repository, so every edit is a commit with a recorded author and a
recorded point in the history. Saying Mind Palace is unusual for verbatim
storage or for being self-hosted would be false, and this repository should not
imply it.

The difference that survives the review is narrower and more specific: who
decides what a stored statement means, and whether a reader can inspect the
evidence for it. Mind Palace's claims are authored by the source document, each
one must appear literally in an archived chunk, and each carries a quote with
character offsets that a database trigger checks at insert time. Every one of
the other four systems either extracts its statements with a model or lets an
agent write them. That is an architectural difference, stated as architecture
and not as quality.

## Graphiti

Graphiti builds a temporal knowledge graph and is the most direct comparison on
the temporal axis. Every fact is an edge with a validity window, old facts are
invalidated rather than deleted, and a search can filter on `valid_at`,
`invalid_at`, `expired_at` and `created_at`. The provenance story is episodes:
the raw data as ingested, with edges pointing back at the episode UUIDs that
produced them. That is real lineage. It is not an offset-level evidence
ledger, and the README does not claim one.

The cost side is what separates it from this repository. Graphiti needs a graph
database, one of Neo4j, FalkorDB or Neptune with OpenSearch, and an LLM to do
extraction at ingestion time, with OpenAI as the default. That makes it a
server-shaped system with a per-token bill, where Mind Palace is a
PostgreSQL-and-pgvector service that performs no inference and treats its
embedding model as an optional extension loaded on first use.

The transferable idea is the explicit validity window on a derived statement.
Mind Palace already has that on authored claims. What Graphiti does that this
repository does not is attach the window to a statement a model derived, which
is worth reading before anyone proposes a derived-memory layer.

## Mem0

Mem0 sits between an application and its model. The documented write path is
context lookup, LLM fact extraction, deduplication and embedding, then entity
extraction. What gets stored by default is a set of extracted facts rather than
the transcript, and the documentation is direct about it: pass `infer=False`
when you need the raw content stored exactly as provided.

Two details are worth crediting. The extraction path is additive, so a new
fact lands without silently rewriting an old one, and correction is an explicit
update or delete. That is the same instinct as retaining conflicts rather than
resolving them, arrived at from a different direction. The other is that Mem0's
own documentation warns that benchmark scores can be moved by retrieval
strategy or context size alone, which is why it reports a constrained top-200
retrieval budget and a token count beside every score.

The line between the open-source tree and the Platform tree matters when
comparing. Graph memory, memory expiration, memory decay, memory export,
temporal reasoning and webhooks are all documented under Platform. The
open-source library and server cover metadata filtering, reranking, async
operations, multimodal input, custom instructions, a REST API and an
OpenAI-compatible surface. Any comparison has to name which side of that line
it is measuring.

## Letta and MemFS

Letta's memory model is the most different of the four, and the clearest to
read. MemFS is a git repository owned by the agent, projected onto whatever
computer the agent is running on as an ordinary checkout. Memory is Markdown
with YAML frontmatter, and a memory label is a path: the memory called
`system/persona` is the file `system/persona.md`. Files under `system/` load
into the system prompt every turn. Everything else stays out of context, but
the file tree is always present, so the agent navigates by name and reads what
it needs.

Versioning comes from git rather than from a schema. Every memory edit is a
commit, which gives history, conflict resolution and a clean line between saved
and unsaved work. Background subagents called dreaming run in git worktrees so
they can consolidate memory without blocking the main agent, and the docs say
the memory workflow backs up the repository before splitting or merging files.
This is a versioned corpus in the plainest sense, and it is the answer to the
question this repository had previously left open about whether a git-backed
Letta memory implementation exists.

Two limits are documented rather than hidden. MemFS ships no semantic or
vector index, so finding memory depends on the agent's own file tools, with an
optional search mod that needs a separate engine for anything beyond keyword
matching. And shared memory repositories are described as a cloud feature, with
self-hosted deployments sharing memory by pointing agents at their own git
remote.

An earlier audit in this repository recorded that the reviewed Letta
documentation did not establish a git-backed MemFS implementation, a versioned
Markdown corpus, or a consolidation contract. All three are now established and
the finding is superseded. The snapshot and replay question stays open: git
gives versioned history, but nothing in the material read describes a capture
operation that fixes a reference set and answers later questions from it.

## MemPalace

MemPalace is the closest neighbour in this set, and the comparison is most
useful precisely because the overlap is large.

Its organizing metaphor is a building. People and projects become wings, topics
become rooms, and the original text lives in drawers. Halls are categories
within a wing, tunnels connect the same room name across wings, and closets
are meant to be a summary layer that points back to the drawer. The storage
default is verbatim drawer text with no summarization, and the retrieval layer
is a pluggable backend interface with ChromaDB as the current default and
pgvector, Qdrant, Milvus and exact SQLite and Rust backends available.

Several things are done better than this repository does them, and the
documentation is honest about the trade-offs. The write-ahead log is its own
side-effect-free module with sensitive fields redacted. The exporter streams
paginated batches so memory stays bounded regardless of palace size, and
refuses to write through a symlink using `O_NOFOLLOW` where the platform
provides it. Backups are pruned to a configured count after every write, which
was added after a real palace accumulated hundreds of gigabytes beside a few
hundred megabytes of live data. Room routing is deterministic, scoring folder
path, then filename, then keywords, so the taxonomy does not depend on a model
call. The logstream is an append-only event log with verbatim bodies, SHA-256
verification, a monotonic `seq` cursor and artifacts up to 4 MiB, and it is
described as durable before realtime.

The AAAK result is the most useful single fact in the review. It is a lossy,
irreversible abbreviation layer, the project says so itself, it scored 84.2%
R@5 against 96.6% for raw verbatim mode, and it is not the default. A system
that measured its own compression and reported the regression rather than
shipping the number as a feature is doing benchmark work the same way this
repository is.

Contradiction detection is the clearest gap. The documentation carries an
experimental warning and states that the code holds the temporal graph
primitives but not a complete contradiction-checking tool exposed through the
CLI or MCP server. The examples show intended behaviour rather than a shipped
command path.

Where MemPalace and Mind Palace actually part company: MemPalace files
statements by time, and the retrieval surface is a search over drawers. Mind
Palace holds claims, evidence spans, conflict groups, status resolution from
two clocks, snapshot and replay, a bounded output contract and a corpus-scoped
operational feed. Neither of those sets is a ranking. They are different
contracts, and a user choosing between them is choosing which contract they
need.

## What a fair comparison would require

Nothing in this repository has been run against any of these systems, so every
statement above is about architecture. A head-to-head result would need five
things that do not exist yet.

A shared corpus with the same source documents for every system, since three of
the four do not ingest documents the same way. An agreed labelling protocol,
because the metrics differ: MemPalace publishes retrieval recall, Mem0 publishes
end-to-end QA accuracy, and this repository publishes a frozen 60-question
exact-or-full result. These cannot share a column, and MemPalace has already
published and then retracted a table that tried.

A reproducible runner per system, pinned the way
[`capability-matrix.md`](capability-matrix.md) pins sources, with the embedding
model and the model used for extraction recorded for each. Several of these
systems call a model at write time, so the write path and the read path have to
be costed and versioned separately.

Questions that discriminate between the contracts rather than between the
scores. Whether a superseded statement is still readable, whether a conflicting
pair is returned together or not at all, whether a statement can be traced to an
exact span, whether output stays inside a stated budget, and whether a captured
state can be replayed. These are the questions the memory model already
distinguishes, and they are answerable without an LLM in the loop.

Finally, an admission of what each system is for. Graphiti is for temporal
graphs. Mem0 is for agent conversation memory. Letta is for stateful agents
whose memory the agent itself maintains. MemPalace is for local verbatim recall
over projects and conversations. Mind Palace is for a corpus whose assertions
are written by a person and must be inspectable afterwards. A comparison that
pretends those are the same job produces numbers nobody can act on.

## Open questions this review did not close

- Whether Graphiti keeps raw episode text verbatim, and what its benchmark
  scores are.
- Whether Mem0's history store records anything finer than record-level CRUD
  events.
- Whether MemPalace's knowledge graph carries per-fact validity bounds the way
  Graphiti's edges do. Its documentation mentions temporal graph primitives and
  a planned contradiction checker; the graph module itself was not read.
- Whether Letta bounds the file tree it places in the system prompt. The
  documents describe the tree as always present without giving a limit.
- Whether any of these four systems has a bounded machine-readable output
  contract comparable to the Memory Pack. Nothing in the reviewed material
  described one.
