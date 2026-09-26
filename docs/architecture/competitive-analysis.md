# Competitive analysis

This is the compatibility index for the post-release architecture review. The
current capability matrix and landscape notes are maintained in
[`docs/competitive/capability-matrix.md`](../competitive/capability-matrix.md)
and [`docs/competitive/landscape.md`](../competitive/landscape.md).

The review covers Graphiti, Mem0 OSS, Letta/MemFS, MemPalace, Laya, and
conventional RAG or vector-memory systems. It records architecture and published
implementation boundaries. It does not rank systems or claim a head-to-head
result, because no external system was run against the Mind Palace corpus.

The pinned review references are:

- Graphiti: `ba4a9cb32495b6864160616f8dfa2b898f4a500c`, `graphiti-core` 0.30.2
- Mem0 OSS: `94c3fe9f238f3dbf29c9ce98643bd71eb13077cd`, `mem0ai` 2.2.1
- Laya: `23a17522aa4942da6cce53a995a275760320b691`, tag `v0.3.20`
- MemPalace: `8c4865f70c49b6346c53474a9e5684c5f17d3fa9`, `mempalace` 3.10.0,
  branch `develop`
- Letta current source: `letta-ai/letta-code` at
  `5e420db9f2871bc3106ba14372d4f8d94c5e63ae`, `letta` 0.33.2
- Letta documentation mirror: `691c48b202a1efa3dddbe585d5cebe709cfee202`

The main conclusion is narrower than “Mind Palace stores verbatim memory” or
“MemPalace does not preserve raw experience.” Both claims are too broad.
MemPalace stores verbatim conversation drawers by default. Letta's MemFS stores
Markdown and YAML files in a git repository. The difference that survives the
review is the authority and evidence contract:

- Mind Palace claims are authored in source material and must appear literally
  in an archived chunk with character offsets.
- Supersession, conflicts, validity windows, and snapshot membership are
  explicit archive state.
- The authoritative read path does not depend on live embeddings or a live
  index.
- L2 search artifacts can be deleted and rebuilt from L0 without creating new
  observations.

Graphiti's temporal graph, Mem0's inferred memories, Letta's agent-authored
files, and MemPalace's verbatim drawers solve related problems with different
tradeoffs. The matrix records those differences without treating them as a
leaderboard.

## Boundary of the review

The following questions remain open and are not answered by the repository:

- whether Graphiti retains raw episode text verbatim at the pinned revision;
- the internal granularity of Mem0's history store;
- the full Letta memory implementation behind its documentation;
- MemPalace's backend internals and complete contradiction-checking path;
- whether any external system provides a bounded machine-readable output
  contract comparable to Memory Pack.

The required final synthesis is in
[`durable-memory-substrate-report.md`](durable-memory-substrate-report.md).

Those unknowns are marked as unknown in the capability matrix. They are not
filled with feature-name comparisons.
