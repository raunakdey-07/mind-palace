# Mind Palace

[![Release](https://img.shields.io/github/v/release/raunakdey-07/mind-palace)](https://github.com/raunakdey-07/mind-palace/releases)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![CI](https://img.shields.io/github/raunakdey-07/mind-palace/actions/workflows/ci.yml/badge.svg)](https://github.com/raunakdey-07/mind-palace/actions/workflows/ci.yml)

[Current release: v0.9.0](docs/release-map.md) · [Changelog](CHANGELOG.md)

**Memory for AI applications that remembers what changed, what was true when, and
why to believe the recorded state.**

> Add Mind Palace to an AI application, give it persistent memory, ask what it knows
> now or what it knew earlier, inspect the evidence behind a memory, and export a
> receipt that can be verified independently later.

Record a fact. Ask a question. Get an answer with its source and the time it was
true. Ask why, and get the exact characters that support it. Export a receipt and
verify it on a machine that has never heard of Mind Palace.

```text
remember   a statement becomes memory, with evidence
recall     an answer, its source, and the time it was true
explain    the characters that support it, and what replaced it
history    the supersession chain, oldest first
receipt    what was returned, plus the digest of the whole response
verify     offline: no database, no model, no network
```

Every command is fast from the second one onwards. A background runtime keeps the
embedding model warm, and the commands use it without you having to know:

| command | first ever | afterwards |
|---|---|---|
| `mindpalace recall "..."` | 6.2 s | **0.63 s** |
| `mindpalace explain "..."` | 6.2 s | **0.67 s** |
| `mindpalace receipt "..."` | 6.2 s | **0.62 s** |

If a command feels slow, `mindpalace runtime status` says whether a runtime is
running and whether its model is loaded. It exits by itself after 15 minutes of
inactivity, and `MIND_PALACE_RUNTIME=0` turns it off everywhere.

Prefer no model at all? `MIND_PALACE_LEXICAL=1` reads through the model-free lexical
relevance path — 0.63 s instead of 6.17 s, and `torch`, `transformers` and
`sentence_transformers` are never imported. It is a deliberate trade-off, not an
equivalent substitute: it needs shared vocabulary, and it will decline a question
that semantic ranking would answer from meaning alone.
[How it works and when to use it](docs/operations.md#model-free-lexical-mode).

---

## What is different, and why it matters

Two distinctions do the work. They are the reason this is not a vector store with a
chat interface bolted on.

```text
semantic similarity   ≠   memory authority
retrieval             ≠   historical truth
```

**Similarity is not authority.** A search index answers "what text looks like your
question". Mind Palace answers "what is true, what replaced it, and on what
evidence" — and it will decline rather than guess when nothing it knows is relevant.
A claim is authoritative because a human wrote it and the archive recorded its
provenance, not because it scored well.

**Retrieval is not history.** A vector index returns the nearest documents and has no
opinion about last month. Every memory here carries validity, supersession and
conflicts, so `--as-of` returns what was true then — and the old answer stays
readable after the fact.

Concretely, that means:

| | |
|---|---|
| **evidence** | the exact character span of the archived text that supports a claim |
| **provenance** | which document and version a claim came from |
| **time** | validity intervals, and `--as-of` to ask what was true at an instant |
| **supersession** | what replaced what, oldest first, never overwritten |
| **conflicts** | two live claims are reported as a conflict, not silently merged |
| **verification** | a receipt anyone can check later, offline, with no database |

Evaluation is real and reported honestly, and the numbers are not the flattering
ones. The frozen M006.75 held-out benchmark scores **45/60 (75.0%)** exact on the
current source, with every safety invariant at **60/60** and zero execution failures.
The broader generalisation figure, from an independently authored 157-question
held-out set, is **131/157 (83.4%)** — a generalisation number and a known
limitation. Neither is superseded by the other. This release changed no benchmark
input, policy, evaluator semantic, tokenizer or threshold, and that is measured:
all 60 canonical per-question decisions are identical to `v0.8.0`. See
[the reproducibility report](docs/evaluation/m00675-reproducibility.md), which also
records one discrepancy this release found and deliberately did not paper over.

---

## The five-minute path

### 1. Install

Mind Palace needs persistent storage. PostgreSQL with pgvector is the only
dependency.

```bash
git clone https://github.com/raunakdey-07/mind-palace.git
cd mind-palace
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
pip install -e .
docker compose up -d postgresql          # or: podman-compose up -d postgresql
export DATABASE_URL=postgresql://mpadmin:secret@localhost:5432/mindpalace
python -m alembic -c migrations/alembic.ini upgrade head
```

That is the whole setup. No API key, no account, no embedding model, no LLM.

### 2. Check it

```console
$ mindpalace init
Mind Palace is ready.
  corpus: default

Record your first memory:

  mindpalace remember "Production uses PostgreSQL."

Then ask about it:

  mindpalace recall "What database does production use?"
```

### 3. Remember

A statement becomes authoritative memory with exact-substring evidence, through
the same archive a synced document uses.

```console
$ mindpalace remember "The production datastore changed from SQLite to PostgreSQL." --key datastore
Remembered.
  version: 5aae989d3134 (new)
  corpus:  default
  path:    memory/datastore-aa7e5b234e1d.md
```

`--key` is how you tell Mind Palace that two statements are about the same fact.
Remembering the same key with new text supersedes the old value — that is what
`history` shows you.

Remembering the same statement twice changes nothing:

```console
$ mindpalace remember "The production datastore changed from SQLite to PostgreSQL." --key datastore
Already remembered. Nothing changed, so no new version was recorded.
  version: 5aae989d3134
  corpus:  default
```

Identity is derived from content, so a retry after a lost response converges
instead of duplicating. The filename is the key plus a digest of the key, so two
different facts can never reduce to one document and silently supersede each
other.

### 4. Recall

```console
$ mindpalace recall "What datastore does production use?"
The production datastore changed from SQLite to PostgreSQL.
   source: memory/datastore-aa7e5b234e1d.md @ 5aae989d3134
   time:   true as of 2026-10-08 03:40 UTC

Ask for the basis with: mindpalace explain "<question>"
```

Answer, source, and when it was true — that is the default. `--json` gives the
canonical response if a program wants it instead of a person.

Mind Palace never invents an answer. A question the archive does not cover
abstains rather than guessing:

```console
$ mindpalace recall "What is our incident response SLA?"
No memory matched that question.
  NO_RELEVANT_MEMORY

Nothing was invented: Mind Palace records only what was remembered.
```

### 5. Explain

```console
$ mindpalace explain "What datastore does production use?"
ANSWER
  The production datastore changed from SQLite to PostgreSQL.

TIME
  answer true at:       2026-10-08 03:41 UTC

EVIDENCE
  memory/datastore-aa7e5b234e1d.md > The production datastore changed from SQLite to PostgreSQL.
    "The production datastore changed from SQLite to PostgreSQL."
    characters 0-59 of version 5aae989d3134

HISTORY
  2026-10-08 03:40 UTC  recorded  The production datastore changed from SQLite to PostgreSQL.

VERIFICATION
  receipt available: yes (1 receipt(s))
  authoritative digest: 2c3077bd8d76627348c569dc5bd2227a08696b0aa54bf3f733f71f5abd3b1aa8
  export and verify offline with:
    mindpalace receipt "What datastore does production use?" -o receipt.json
    mindpalace verify receipt.json
  Authenticity is not established without a trust anchor you pinned yourself.

This explains the recorded provenance and temporal basis for the memory. It does not establish that the original source was factually correct.
```

`characters 0-59` is the whole claim, and it is an exact span of the archived
text — not a paraphrase, and not a summary of one.

### 6. History

Memory is not only what is true now. It is what was true when.

```console
$ mindpalace remember "The production datastore is now distributed CockroachDB." --key datastore
Remembered.
  version: 062167c43040 (modified)
  corpus:  default
  path:    memory/datastore-aa7e5b234e1d.md

$ mindpalace history datastore
2026-10-08 03:40 UTC  recorded  The production datastore changed from SQLite to PostgreSQL.
2026-10-08 03:41 UTC  The production datastore changed from SQLite to PostgreSQL.
            superseded by  The production datastore is now distributed CockroachDB.

current now: The production datastore is now distributed CockroachDB.
```

And the answer before the change is still reachable:

```console
$ mindpalace recall "What datastore does production use?" \
    --as-of 2026-10-08T03:40:59Z
The production datastore changed from SQLite to PostgreSQL.
   source: memory/datastore-aa7e5b234e1d.md @ 5aae989d3134
   time:   true as of 2026-10-08 03:40 UTC
```

### 7. Receipt and verify

```console
$ mindpalace receipt "What datastore does production use?" -o receipt.json
Receipt written to receipt.json
Verify it anywhere with: mindpalace verify receipt.json

$ mindpalace verify receipt.json
VERIFIED

Integrity:      verified
Provenance:     verified
Temporal state: verified
Supersession:   verified (1 receipt(s))
Authenticity:   not established (supply --trusted-digest to check it)

This means the receipt still matches the memory it describes. It does not mean the original claim was factually true.
```

Verification needs no server, no database, no embedding model and no network.
Hand the file to someone who has never heard of Mind Palace:

```console
$ pip install mindpalace-os
$ mindpalace-proof verify receipt.json
VERIFIED

Integrity:
  authoritative artifact: MATCH
  receipt: MATCH

Provenance:
  claim: MATCH
  evidence: MATCH
  document: MATCH

Temporal state:
  valid_at: MATCH
  supersession: MATCH

Trust:
  authenticity: NOT ESTABLISHED (no trust anchor supplied)

query: What datastore does production use?
receipts checked: 1
memory pack digest: 2c3077bd8d76627348c569dc5bd2227a08696b0aa54bf3f733f71f5abd3b1aa8

This means the artifact still represents the state the receipt describes.
It does not establish that the original source was factually correct.
```

The receipt file carries the response it describes, so no `--pack` and no second
artifact are needed. `mindpalace-proof` is `argparse` over the standard library,
which is why it works on a bare install.

Tampering is caught and named:

```console
$ mindpalace verify tampered-receipt.json
REJECTED

Reason:
  the artifact no longer matches the digest the receipt recorded

The receipt describes memory that has since changed, or the artifact has been altered.
Re-issue it with: mindpalace receipt "<question>" -o receipt.json
```

### 8. Connect an agent

MCP is nearly invisible. Point an agent at `mindpalace-mcp` and it can recall and
explain the same memory:

```console
$ mindpalace-mcp
```

```json
{
  "mcpServers": {
    "mind-palace": {
      "command": "mindpalace-mcp",
      "env": { "DATABASE_URL": "postgresql://mpadmin:secret@localhost:5432/mindpalace" }
    }
  }
}
```

An agent asks `memory_recall` for an answer and `memory_explain` when it wants to
know why the answer is trustworthy. Both return the same authoritative memory the
CLI and the SDK do, with the same receipt.

---

## From Python

```python
from mindpalace_sdk import MindPalace

client = MindPalace()                              # no configuration required

client.remember("We will use PostgreSQL as the primary datastore.", key="decision.datastore")
result = client.recall("Which datastore did we choose?")

for memory in result.current_memories:
    print(memory.claim, memory.path, memory.status)

explained = client.explain("Which datastore did we choose?")
print(explained.receipt["memory_pack_digest"])     # why it was returned, provably
```

Two complete runnable versions:

- [`examples/decision-memory/`](examples/decision-memory/) — the whole product in one
  file, from `remember` to `verify`.
- [`examples/project-log/`](examples/project-log/) — this project's own decision log
  as memory. The questions a contributor actually asks, answered with the evidence
  attached, and honestly abstained on where nothing was decided.

### Against a service someone else runs

```python
client = MindPalace(base_url="http://127.0.0.1:8000")
```

Everything above works unchanged over HTTP.

### Sharing the warm runtime with a script

```python
client = MindPalace(runtime=True)     # opt in; off by default
```

Optional, and rarely needed: a long-running application has the model resident
already, so this would only add a socket hop. It is for short-lived scripts that
would otherwise pay the model import per process. Every result is identical either
way, which is asserted rather than assumed.

---

## What it does not do

Stated up front, because the trust boundary is part of the product:

- It does not decide what is **true**. It records what was asserted, with the
  source that asserted it. `VERIFIED` means the receipt still matches the memory
  it describes — never that the claim was correct.
- It does not infer memory. Nothing is extracted from text automatically. Every
  claim is authored and every claim carries the exact characters that support it.
- It does not need an LLM, and it does not need an embedding model to record,
  answer, or verify memory. Ranking uses one when it is available and degrades to
  lexical matching when it is not.
- **Corpus content is data, not instructions.** Retrieved text is never promoted
  into trusted system instructions. Anything that can write a document can put text
  into a recall response, so a model that reads one without that distinction is a
  prompt-injection surface. Enforcing that boundary is the consuming application's
  job, not this one's. The rest of the security posture —
  [what is *not* tamper-proof, authenticated, or an access-control system](docs/operations.md#security-posture)
  — is written down rather than assumed.

---

## Progressive disclosure

The product is three layers, and you stop at the one you need.

| Level | You need | Commands |
|---|---|---|
| **Simple** | to use memory | `remember`, `recall` |
| **Explainable** | to trust it | `explain`, `history`, `recall --explain` |
| **Verifiable** | to hand it to someone else | `receipt`, `verify` |
| **Agent-native** | to let an agent use it | MCP |
| **Production** | to run it | idempotent writes, auth, telemetry |

Nothing below the line you are standing on is required to use the layer above it.
Claim IDs, evidence IDs, validity intervals, Memory Pack internals, pgvector,
retrieval policies and receipt digests are all real and all progressive: they
appear when you ask for them, and never in the first five minutes.

---

## How it works

Everything above is a thin surface over one model. Read this section when you
want the guarantees rather than the commands.

```text
L0  source and verbatim versions      immutable
L1  authoritative knowledge            claims, evidence, validity,
                                        supersession, conflicts, snapshots
L2  derived retrieval                 embeddings, chunks, caches, indexes
```

L2 may be deleted and rebuilt without changing a single byte of authoritative
memory. `mindpalace reindex` rebuilds it from the archive. That separation is why
a first memory works with no model and why a receipt verifies with no model.

A remembered statement becomes one document under `memory/` carrying one authored
claim. The claim text is the statement; the evidence quote is the same characters,
so the archive's exact-substring rule can always re-verify them against the stored
bytes. Version identity derives from content, which is what makes an identical
write idempotent.

### The guarantees

- **Authority.** Every claim has at least one evidence span, enforced by the
  database, and each span must be an exact substring of an archived chunk.
- **Append-only.** Version, claim, evidence and snapshot tables reject `UPDATE`,
  `DELETE` and `TRUNCATE` at the trigger level. History is not editable.
- **Idempotency.** One corpus-scoped advisory lock plus a content fingerprint.
  The same write twice, concurrently, or after a lost response resolves to one
  version. No queue, no distributed lock.
- **Supersession.** Remembering the same key again supersedes the previous claim;
  the old one stays readable with its own identity.
- **Temporal.** `as_of` reconstructs the archive as it was, so a question asked
  before a change is answered with the value that was true then.
- **Abstention.** A question the archive does not cover returns nothing with a
  stated constraint. Nothing is generated.

---

## Surfaces

One service, four doors. They cannot disagree, and there is a permanent test that
proves it.

| Surface | Entry point |
|---|---|
| CLI | `mindpalace remember \| recall \| explain \| history \| receipt \| verify` |
| Python SDK | `MindPalace().remember() / .recall() / .explain() / .history()` |
| REST | `POST /api/memory/remember`, `POST /api/memory/query`, … |
| MCP | `memory_recall`, `memory_explain`, and the archive operations |

Every one returns the same authoritative memory, the same evidence, the same
temporal state and the same receipt.

`POST /api/memory/remember` takes a statement and nothing else. Reading a file is
a decision for a trusted operator at their own machine — `mindpalace remember
--file` and the SDK's `file=` do it, and the HTTP endpoint deliberately does not,
because it has no authentication and a path would let any caller read a file
relative to the server and recall its contents.

### The full memory API

The five commands above are the product. The rest exists when you need it:

```bash
mindpalace memory current     --corpus C --query "..."   # what is true now
mindpalace memory changes     --corpus C                 # what changed
mindpalace memory evidence    --corpus C --claim-id ID   # why one claim holds
mindpalace memory as-of       --corpus C --as-of T       # what was true at T
mindpalace memory snapshot    --corpus C                 # freeze a state
mindpalace memory replay      --corpus C --snapshot-id ID # read a frozen state
mindpalace memory pack        --corpus C                 # bounded context for a model
mindpalace memory feed        --corpus C                 # durable change feed
```

`mindpalace memory query` exposes the full operation: intent selection, an
explicit budget, and selectors such as `--claim-id` and `--snapshot-id`.

---

## Errors

Failures answer what happened, why, and what to run.

```console
$ mindpalace recall "..."   # with the database stopped
Mind Palace needs persistent storage, and it could not reach it.

Why: it could not connect to PostgreSQL at localhost:5432.

What to do:
  docker compose up -d postgresql

Then check it works:
  python -m alembic -c migrations/alembic.ini upgrade head
  mindpalace init
```

`--verbose` appends the underlying exception. It is never the only thing you see,
and never the first thing.

---

## Production

- **Idempotent writes.** Same write twice, concurrently, or after a lost response:
  one version, one claim, one identity. Proven by test.
- **Authentication.** Local development needs none, and the quickstart never asks
  for one. For a shared deployment see
  [Production authentication](docs/operations.md#authentication).
- **Observability.** Optional OpenTelemetry instrumentation on `memory.remember`,
  `memory.recall`, `memory.explain`, `memory.receipt`, `memory.verify`,
  `runtime.start` and `runtime.model_load`. It is off unless you install and
  configure it, and it never carries memory content.
- **Performance.** Measured, not claimed. See
  [performance](docs/performance/REPORT.md) and
  [the five-minute path](docs/performance/five-minute-path.md).

---

## Documentation

| | |
|---|---|
| [First-use audit](docs/product/first-use-audit.md) | the problems earlier releases fixed, and how they were reproduced |
| [Developer experience](docs/product/developer-experience.md) | every documented path, verified |
| [Decision memory example](examples/decision-memory/) | the whole product in one file |
| [Project decision log example](examples/project-log/) | a real project's decisions, as memory |
| [Operations](docs/operations.md) | deployment, the local runtime, model-free mode, security posture, authentication, observability |
| [Status](docs/STATUS.md) | what is measured, what is outstanding |
| [Release map](docs/release-map.md) | milestone to version |
| [Architecture](docs/architecture/) | how the layers fit |
| [Evaluation](docs/evaluation/) | the frozen benchmarks and their results |

## Contributing

```bash
make setup        # virtualenv, dependencies, editable install
make dev          # start PostgreSQL
make migrate      # apply the schema
make test         # the full suite, against real PostgreSQL
```

The suite needs a PostgreSQL URL: set `DATABASE_URL` or `MEMORY_TEST_DATABASE_URL`.
It creates a random schema per test and rolls it back; no test writes to your
production schema.

## License

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).

<!--
Everything below this line is reference material. The five-minute path above is
complete on its own; start there.
-->

---

# Reference

## Why memory beyond RAG?

Retrieval returns the passage that best matches a query. It does not know that the
passage was wrong last month, that a newer document supersedes it, or that two
sources disagree. Mind Palace answers a different question: *what was recorded,
when, and on what basis?*

The corpus owns the memory. An application asks a question and receives claims
with status, validity, evidence and conflicts, not chunks to re-rank. That is why
an answer can be exported, checked, and defended without the retrieval stack.

## Core memory model

A **claim** is one authored statement with a key, a validity window and a status.
Each claim carries **evidence**: exact character spans of one immutable document
version, verified by the database. When a claim is replaced, the archive records
**supersession** rather than overwriting, so both the old and new values remain
addressable and the transition is dated.

Two claims that are current at the same time, on the same key, with different
values, form a **conflict**. Mind Palace reports both with their sources instead of
picking one.

```text
              ┌──────────────┐
   NEW ──────▶│   CURRENT    │──── superseded by ──┐
              └──────┬───────┘                    │
                     │ evidence                   │
                     ▼                            ▼
            document version ──────────────►  new version
              (immutable)                       (immutable)
```

## Verifiable Memory

A **Memory Receipt** records what Mind Palace actually returned to one application,
for one query, at one historical state: the claim identity and version, the
document version and path that carried it, its evidence with offsets, its validity
window, its supersession lineage, the authoritative digest, and an embedded proof.

A **Memory Pack** is the bounded, portable slice of authoritative memory the
receipt covers. It is a plain JSON artifact: model-independent, and readable with
`memory_pack.py`, which is standard-library-only.

Verification establishes **integrity and recorded provenance** — that the artifact
still represents the state the receipt describes, and that no covered record has
been altered since. It does **not** establish that the original source was
factually correct. A digest alone does not establish **authenticity**: a party able
to replace both the artifact and the receipt can recompute the digests. Supply
`--trusted-digest` with a value pinned out of band and authenticity becomes
checkable. `verify` states the unestablished case explicitly rather than letting a
digest imply more than it shows.

```bash
# The dependency-free verifier, for environments without the CLI extras.
mindpalace-proof verify receipt.json --pack memory-pack.json
mindpalace-proof explain receipt.json --pack memory-pack.json
```

Existing v0.8.0 receipts remain verifiable. The receipt schema is version 1 and
Memory Pack is version 1; neither changed.

## Corpora

A corpus is a namespace of memory. The default is `default`, which exists from the
first migration, so the first command never has to mention one. Pass `--corpus`
(or `corpus=`) to scope memory explicitly; that is the multi-tenant boundary, and
it is the only reason to know the word.

```console
$ mindpalace corpora
default
decision-memory-1f4a9c02
```

## Ingesting documents

`remember` is for facts. `sync` is for a directory of Markdown you already have,
which is where authored claims live:

```markdown
---
title: Deployment architecture
date: 2026-02-01
claims:
  - key: architecture.datastore
    value: PostgreSQL
    claim: The primary datastore is PostgreSQL.
    evidence: The primary datastore is PostgreSQL.
---

# Deployment architecture

The primary datastore is PostgreSQL. It runs in three availability zones.
```

```bash
mindpalace ingest-repo ./docs
```

The `claims:` block is the authoring interface: it says what to remember, and the
evidence quote must appear verbatim in the document. Nothing is inferred, because
inference is where memory systems quietly start lying about their evidence.

## Retrieval

Retrieval is an optimization on top of the archive, not the product. The archive
decides what is true, what changed, what conflicts and what is absent; ranking only
adds raw source material, and is skipped when no embedding model is available. A
missing model downgrades recall from semantic to lexical to authoritative-only, and
says on stderr that it did — a silent fallback would be indistinguishable from a
semantic answer, and the two select different keys. It never removes the answer.
`MIND_PALACE_LEXICAL=1` chooses that rung on purpose:
[model-free mode](docs/operations.md#model-free-lexical-mode).

## Project structure

```text
api/            FastAPI app, services, and the authoritative memory model
  models/       the public v1 contract; no persistence models exposed
  routers/      REST adapters
  services/     memory archive, ingestion, retrieval, relevance
cli/            Typer CLI, rendering, error text
migrations/     Alembic migrations
memory_pack.py  dependency-free portable memory reader
memory_receipt.py  the receipt contract, standard library only
memory_proof.py    proof construction and verification
memory_proof_cli.py standalone verifier, no optional dependencies
mindpalace_sdk.py  the SDK
mcp_server.py   MCP server
tests/          the suite, against real PostgreSQL
examples/       runnable examples
docs/           status, operations, research, release notes
scripts/        benchmarks, probes, release checks
```

## Evaluation

The retrieval and memory benchmarks are frozen artifacts with published results,
run offline against deterministic fixtures. They test semantics, not
question-answering quality, and the reports say so on their face.

```bash
mkdir -p eval/results
python -m cli.main eval memory --repetitions 20 --save eval/results/memory.json
```

See [docs/evaluation/](docs/evaluation/) for what each benchmark measures and what
its numbers do not claim.
