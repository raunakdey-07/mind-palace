# Portability audit

Scope: moving Mind Palace data and deployments between places. Archive
export/import, Memory Pack as a portable artifact, the documented meaning of
"portable", and the dependencies that decide whether a second installation
behaves like the first.

Companion document: [developer-experience.md](../product/developer-experience.md).

## Summary of the position

Mind Palace has portable interfaces, not a portable archive. Every adapter
speaks the same typed contract, and that part holds up under inspection. What
does not exist is any way to move the archive itself between PostgreSQL
instances. This is a stated non-goal, consistently documented, and not a defect.
The gap worth naming is narrower: the one artifact that looks like an export
format is not one, and the published hash that is called portable is portable
only within a single dependency resolution.

## Export and import do not exist

I searched the full tree for any export, import, dump, or interchange path
across Python, Markdown, and configuration files. The only hits for "export" as
a data verb are shell `export` statements in documentation, and the corpus
fixture files under `content_eval/` that happen to be about export features in
other projects. There is no `export` function, no `import` entry point, no
archive serializer, and no CLI or REST route for either.

The project documents this in five separate places, and they agree:

| Source | Statement |
|---|---|
| `README.md` line 19 | "model-independent interfaces, not a promised archive export/import tool" |
| `examples/WHY_MEMORY.md` line 9 | "not a promised archive export/import or erasure facility" |
| `docs/performance/comparison/report.md` line 96 | "There is no archive export and import format" |
| `docs/architecture/competitive-analysis.md` line 99 | "Portable archive export and import: Not implemented" |
| `docs/performance/comparison/report.md` line 419 | lists it under deferred work |

So the absence is intentional and honestly described. Nothing here needs
fixing, and I am not proposing an export format as bounded work. Building one
means deciding claim identity across databases, ID stability, supersession-chain
semantics, conflict closure, evidence offset validity, and snapshot seal
re-verification. That is a project, not a patch.

The practical consequence for a developer is worth stating plainly: **the
PostgreSQL database is the archive.** Moving a corpus means moving the
database, and the only mechanism available is `pg_dump` and `pg_restore`
against a server that has the same extensions. `migrations/versions/001_initial_schema.py`
provisions `vector`, `pgcrypto`, and `pg_trgm`, so a target server needs
pgvector available. Nothing in the repository automates or documents that
transfer. The new `mindpalace reindex --corpus NAME` command rebuilds only the
derived live projection from the archive; it is not an archive export. Moving
the authoritative archive still requires a controlled `pg_dump` and
`pg_restore` against a server with the same extensions and migrations.

## Memory Pack is a read projection, not an archive format

The pack format is deliberately narrower than a portable corpus. It is a
bounded response for one query, not a lossless interchange format.

Memory Pack is the closest thing to an export, and it is worth being precise
about what it is.

`bounded_pack` in `api/services/memory_public.py` takes a complete projected
`MemoryResponse` and greedily selects claims, changes, conflicts, and uncertain
memories until the canonical JSON envelope fits a character budget. Units and
properties, all verified by running it:

| Property | Measured or documented behavior |
|---|---|
| Budget unit | Unicode characters of the complete canonical JSON envelope, not tokens and not bytes |
| Valid range | 512 to 128000, default 8000 |
| Truncation flag | `truncated=true` whenever anything was dropped |
| Atomicity | a claim keeps all its evidence, or is dropped whole; conflict alternatives stay together or are omitted together |
| Quotes | never sliced |
| Sources | derived only from retained evidence |
| Rejected input | `422 invalid_budget` when the empty envelope does not fit |

A pack from a real two-claim corpus came back at 2493 characters with
`truncated=false` and both claims intact. A pack from an empty corpus came back
at 350 characters with `truncated=false` and nothing in it.

Five properties keep a pack from being usable as a portable archive.

**It is lossy by design and the loss is not distinguishable from absence at
the consumer.** The README states plainly that "`truncated=true` warns that
omissions are not evidence of absence". But a consumer holding a
`truncated=false` 350-character pack from an empty corpus receives exactly the
same shape as a consumer holding a complete pack from a small corpus. Both say
`truncated=false`. Nothing in the envelope distinguishes "complete" from
"complete and empty".

**It carries a validity clock that is not reproducible across calls.** The
README is explicit that "Independent wall-clock calls are not promised
byte-identical: the default validity clock and live state can change." Byte
identity needs the same frozen state, validity cutoff, selectors, and budget.
Query packs additionally need the same question, intent, embedding model, and
runtime.

**A small budget does not bound the cost of reading.** The README says "A small
pack budget does not bound archive-read cost; history loading is currently
unbounded", and `docs/performance/REPORT.md` line 83 records the per-candidate
reserialization as a deferred item. A pack is bounded in output and unbounded in
input work.

**There is no consumer.** Nothing accepts a pack as input. Feeding
`canonical_json()` back into `MemoryResponse.model_validate` would work as a
round trip, but no code path does this, and no route, CLI command, or SDK
method takes a pack file.

**It is a projection of a query result, not a corpus.** Selection runs over
conflict closure and change sets computed for one request. A pack answers "what
was relevant to this question, within this budget, at this instant".

Reproducibility is genuinely strong where the README claims it is, and
`docs/evaluation/m00675-reproducibility.md` records the evidence: provenance
60/60, budget 60/60, conflict closure 60/60, temporal scope 60/60, status 60/60,
zero false-current promotions, zero execution failures. The weakness is not in
the determinism. It is that determinism is scoped to one frozen state on one
installation, which is a narrower claim than the word "portable" suggests.

## The published portable artifact is not portable

`docs/evaluation/m00675-reproducibility.md` line 84 calls a value "The portable
canonical artifact for the corrected release source tree":

```text
881530c7c6e7ba29fb38eb5b27609071463f733c6a8cd173aeff04173a5d8dc8
```

`build_artifact` in `scripts/benchmark/m00675.py` computes that hash over an
artifact that embeds two things a second machine will not reproduce:

```python
"python": platform.python_version(),
...
"dependencies": dependency_manifest(),
```

`dependency_manifest()` pins nine exact version strings: `asyncpg`, `numpy`,
`psycopg2-binary`, `pytest`, `sentence-transformers`, `sqlalchemy`, `torch`,
`transformers`, `pyyaml`. The recorded artifact was produced on Python 3.14.7
with `torch 2.13.0`. The hash changes if any of those ten values differ.

Nothing in the repository constrains them. `requirements.txt` uses `>=` for
every direct dependency except `asyncpg`, which alone is pinned, at `0.30.0`.
`torch` is not a direct requirement at all; it arrives transitively through
`sentence-transformers`, so its version is whatever the resolver picks. The
recorded `torch 2.13.0` in this checkout came out of a resolution made on one
machine at one time.

The Docker path makes this concrete. `requirements-docker.txt` pins
`torch==2.14.0+cpu` from the PyTorch CPU index. The recorded artifact needs
`2.13.0`. A container built from the current Dockerfile cannot reproduce the
published hash and will fail `verify_artifact` at the dependency comparison on
line 223.

So the honest description is narrower than the current wording: the hash is
reproducible on a machine that resolves to the same Python patch level and the
same nine dependency versions, and the repository does not make that
reproducible, it only detects the difference. The detection works, which is
worth something. I confirmed it fires: `./scripts/benchmark/m00675.sh --verify`
exits 1 with "BENCHMARK INTEGRITY FAILURE: source fingerprint mismatch", because
three of the six `SOURCES` files differ from the checked-in result after
post-release commit `1cef0fa`.

That failure is a drift record, not a portability defect, and the audit was told
not to modify M009 or evaluation evidence. The read-only `--verify` mode is now
documented in the Reproduction section of that file, so a developer can check the
published artifact before running the default `--clean` mode, which overwrites it.

## Deployment portability

### The container image boundary is correct and correctly documented

`docs/operations.md` lines 191 to 198 describe exactly what the image contains
and what it does not, and the `Dockerfile` matches. The image copies `api`,
`migrations`, and `content`, installs `requirements-docker.txt`, and runs as UID
`10001` with `HF_HOME=/tmp/huggingface`. It excludes `cli`, `mindpalace_sdk`,
`mcp_server`, `scripts`, `tests`, `docs`, `examples`, and twelve research-only
service modules.

The exclusion of research modules is the one that could have broken the image,
since `api/services/benchmark.py` imports `confidence` and `evaluation`, and
`quality_gate.py` imports `confidence`, all of which `.dockerignore` removes. I
checked this with a static import-closure walk over the retained set. 27 modules
are reachable from `api.main`, and every intra-`api` import resolves to a file
the image keeps. The research modules import each other, never the reverse, so
the image cannot fail with an `ImportError` from these exclusions. This is a
well-executed packaging decision.

One inconsistency worth noting but not worth changing: `api/__init__.py`,
`api/routers/__init__.py`, and `api/services/__init__.py` exist, but
`api/models/` has no `__init__.py` and resolves as a PEP 420 namespace package.
It works, because the project root is on `sys.path` in both the image and an
editable install. It would break under a zip-safe or differently-rooted install.

### Undocumented model dependencies in a network-restricted image

The image ships no model weights and pre-downloads anything. Two models are
reachable at runtime:

| Model | Trigger | Operator action |
|---|---|---|
| `all-MiniLM-L6-v2` (`Embedder`) | sync, semantic search, `memory.query` | Provision the model cache or allow the first download. |
| `RERANKER_MODEL` (default `cross-encoder/ms-marco-MiniLM-L-6-v2`) | `rerank=true` on `/api/search` | Provision the optional reranker model when reranking is enabled. |

`Reranker` now follows the same lazy boundary as `Embedder`. `Reranker()`
does not construct a `CrossEncoder`; the first non-empty `score()` call does so
behind a lock. A missing model returns a sanitized `503` on the live retrieval
routes rather than a provider traceback. The authoritative memory query path
keeps its `503 memory_unavailable` contract.

### Dependency floors are wide and unpinned

Both `pyproject.toml` and `requirements.txt` use `>=` floors with no upper
bounds. `sentence-transformers>=2.5.0` is the consequential one, because it
brings `torch` and `transformers` with it and both influence embedding
output. This checkout resolves to `sentence-transformers 5.7.0`,
`transformers 5.14.1`, and `torch 2.13.0`. `langchain-text-splitters>=1.0.0`
matters for a different reason: chunk boundaries determine evidence offsets and
`source_hash`, so a splitter change would alter archive identity for identical
input.

The design already accounts for the model half of this.
`Embedder.version` returns `f"{model_name}:{dimension}"` and that string is
persisted beside every chunk, precisely so vectors from different models are
never silently mixed. Chunking identity has no equivalent guard, and chunking is
not recorded per document.

### Version metadata can disagree with itself

On this checkout, `pip list` and `pip show` report `mindpalace-os 0.1.0` from a
stale `mindpalace_os-0.1.0.dist-info`, while
`importlib.metadata.version("mindpalace-os")` returns `0.6.0` from the in-tree
`mindpalace_os.egg-info/PKG-INFO`, because the project root on `sys.path` takes
precedence. `pyproject.toml` declares `0.6.0`.

This is a local environment artifact rather than a repository defect, but it
has a structural cause worth naming: `scripts/release/check_versioning.py`
verifies release metadata by reading `pyproject.toml`, `CHANGELOG.md`,
`docs/release-map.md`, and the evaluation documents as text. It never asks the
installed distribution what version it is, so no check in the repository can
report this disagreement. The release gate is correct about the source of
truth and blind to the installed one.

## What would actually make the archive portable

Not bounded, and listed only so the shape of the work is on record.

1. A stable corpus-scoped export format that carries claim identity, supersession
   chains, conflict groups, evidence with offsets, and validity windows, with a
   version field and a content hash.
2. An import path that re-verifies evidence offsets against re-chunked source
   text, because offsets are only valid for the exact chunker that produced them.
3. A decision on snapshot seals across the boundary. Migration `006` seals
   membership at commit time; an imported snapshot would arrive already sealed,
   and `memory_snapshot_seals_immutable` would prevent the local database from
   establishing that the seal is genuine.
4. A stated stance on writer identity and trust, which
   `docs/performance/comparison/report.md` line 419 lists alongside export as
   absent.

Until that exists, the accurate statement for a user is: the interfaces move,
the database is the archive, and `pg_dump` against a pgvector-enabled server is
the transfer mechanism. That is worth writing into the operations guide as a
paragraph. It is not worth building in this milestone.
