# Mind Palace

[![Release](https://img.shields.io/github/v/release/raunakdey-07/mind-palace)](https://github.com/raunakdey-07/mind-palace/releases)
[![CI](https://img.shields.io/github/actions/workflow/status/raunakdey-07/mind-palace/ci.yml?branch=main&label=CI)](https://github.com/raunakdey-07/mind-palace/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue)](LICENSE)

[Current release: v0.9.1](docs/release-map.md) · [Changelog](CHANGELOG.md)

> **Install `0.9.0`.** `v0.9.1` is prepared but not tagged or published.

**Memory for AI applications that remembers what changed, what was true when, and
why to believe the recorded state.**

Add Mind Palace to an application, record a fact, and get back an answer with its
source, the time it was true, and the exact characters that support it. Export a
receipt and verify it on a machine that has never heard of Mind Palace.

```text
remember   a statement becomes memory, with evidence
recall     an answer, its source, and the time it was true
explain    the characters that support it, and what replaced it
history    the supersession chain, oldest first
receipt    what was returned, plus the digest of the whole response
verify     offline: no database, no model, no network
```

---

## Install

Expect **5.9 GB and 79–202 s**. `torch` pulls the CUDA runtime; most of it is
unused without a GPU. Nothing else in the install comes close. To skip it, set
`MIND_PALACE_LEXICAL=1` before step 4 and no model is ever downloaded — a
deliberate trade-off, not an equal substitute
([runtime.md](docs/runtime.md#model-free-lexical-mode)).

```bash
git clone https://github.com/raunakdey-07/mind-palace.git && cd mind-palace
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
pip install -e .

# Three values compose will not invent. It names only the first missing one per
# run, so setting two leaves you one failure away from reading the wrong thing.
# .env.example holds all of them with what each is for.
export MIND_PALACE_RUNTIME_PASSWORD=choose-a-local-password
export MIND_PALACE_CURSOR_SECRET=$(python -c "import secrets;print(secrets.token_urlsafe(32))")
export MIND_PALACE_INGEST_ROOTS="$PWD"
docker compose up -d postgresql

export DATABASE_URL=postgresql://mpadmin:secret@localhost:5432/mindpalace
python -m alembic -c migrations/alembic.ini upgrade head
```

No API key, no account, no LLM. The first `recall` downloads the embedding model
once, about 90 MB, then works offline.

## First command

```console
$ mindpalace init
Mind Palace is ready.
  corpus: default

$ mindpalace remember "Production uses PostgreSQL." --key datastore
Remembered.
  version: 5aae989d3134 (new)
  corpus:  default
  path:    memory/datastore-aa7e5b234e1d.md

$ mindpalace recall "What database does production use?"
Production uses PostgreSQL.
   source: memory/datastore-aa7e5b234e1d.md @ 5aae989d3134
   time:   true as of 2026-10-14 00:00 UTC

Ask for the basis with: mindpalace explain "<question>"
```

`--key` is how you say two statements are about the same fact. Remembering the
same key with new text **supersedes** the old value, which is what makes "what
changed" answerable. Omit it and each statement is its own memory:

```console
$ mindpalace remember "The datastore is now distributed CockroachDB." --key datastore
Remembered.
  version: 062167c43040 (modified)
  corpus:  default
  path:    memory/datastore-aa7e5b234e1d.md

$ mindpalace history datastore
current now: The datastore is now distributed CockroachDB.
```

`history` shows the chain, oldest first, and `explain` gives the exact characters
that support a claim. There is no `--as-of` example here on purpose: memory is
stamped when it is recorded, so a time-travel query dated before the write
returns nothing, and a worked example would have to invent a timestamp.

A question the corpus does not hold gets a refusal, not a guess:

```console
$ mindpalace recall "What is our incident response SLA?"
No memory matched that question.
  NO_RELEVANT_MEMORY

Only authored claims are answers. A document with no `claims:` block
in its frontmatter is stored and searchable, but contributes nothing
here. See docs/authoring.md to author claims in it.
```

## Documents and claims

`remember` is for facts you have in hand. A directory of Markdown you already
have goes through `mindpalace ingest-repo ./docs`, and the `claims:` block in its
frontmatter is the authoring interface: it says what to remember, and the
evidence quote must appear **verbatim** in the body. Nothing is inferred, because
inference is where memory systems quietly start lying about their evidence.

```markdown
---
title: Deployment architecture
claims:
  - key: architecture.datastore
    value: PostgreSQL
    claim: The primary datastore is PostgreSQL.
    evidence: The primary datastore is PostgreSQL.
---

# Deployment architecture

The primary datastore is PostgreSQL. It runs in three availability zones.
```

`ingest-repo` runs in-process; no server needs starting. The path must be inside
`MIND_PALACE_INGEST_ROOTS`. A document with no `claims:` block is stored and
searchable but answers nothing, and comes back named in the ingest output as
`no_claims_declared`.

**[docs/authoring.md](docs/authoring.md) is the reference**: the three shapes, the
exact-quote rule, and both rejections with their messages.

From Python, with no server and no subprocess:

```python
from mindpalace_sdk import MindPalace

client = MindPalace("default")
client.remember("Support answers within one business day.", key="support.sla")
memory = client.recall("What is the support SLA?").current_memories[0]
print(f"{memory.claim}  [{memory.status}]")
```

## What it does not do

- **It does not decide what is true.** It records what was written, attributed.
  Correctness of the original source is never established — only its provenance.
- **It does not infer claims from prose.** A document with no `claims:` block
  answers nothing. That is the central design choice, and it is a limitation too:
  getting a document to answer means writing claims for it.
- **It is not a vector store.** Retrieval is one stage of a pipeline that ends in
  provenance, not the product.
- **It does not authenticate its own history.** A receipt proves internal
  consistency. Trust requires an anchor you pinned yourself, outside this system.
- **An agent cannot call `remember` over MCP.** The 15 MCP tools have no
  `remember`; the only document write is `sync(corpus, path)`. An agent writes a
  document with claims and syncs it. `remember` exists on the CLI, the SDK and
  REST ([why](docs/authoring.md#how-an-agent-writes-claims)).
- **The install is 5.9 GB**, and a writer of this size in your terminal is
  unfashionable. `MIND_PALACE_LEXICAL=1` removes the model and most of the
  weight, and costs you meaning-based matching.
- **`--as-of` queries need a timestamp you actually wrote at.** Memory is stamped
  when it is recorded, so a time-travel query dated before the write returns
  nothing. There is no backdating.
- **Retrieval quality is measured on small frozen sets**, not on your corpus.
  See [evaluation](docs/evaluation/) for what was actually measured, including
  the questions the current system gets wrong.

## Reading order

Three documents, and then stop.

| | | |
|---|---|---|
| 1 | This file | what it is, and what it refuses to do |
| 2 | [Quickstart](docs/quickstart.md) | the commands, expanded; every `$ ` line in it runs in CI |
| 3 | [Authoring memory](docs/authoring.md) | how to write a document that answers |

## Everything else — later

| | |
|---|---|
| [Local runtime and model-free mode](docs/runtime.md) | the warm process, and running with no model |
| [Deployment and authentication](docs/deployment.md) | health, observability, credentials, multi-tenancy |
| [Security](docs/security.md) | threat model, credential handling, what is logged |
| [Errors](docs/errors.md) | every code the CLI and API return |
| [Deletion and erasure](docs/deletion-and-erasure.md) | removing a person's data |
| [Operational memory feed](docs/feed.md) | ordering, cursors, consistency |
| [Providers](docs/providers.md) | LLM and embedding providers |
| [Architecture and the memory model](docs/architecture/) | how the layers fit; why memory beyond RAG |
| [Surfaces](docs/quickstart.md#the-full-walkthrough) | CLI, SDK, REST, MCP, and what each can reach |
| [Status](docs/STATUS.md) | what is measured, what is outstanding |
| [Changelog](CHANGELOG.md) | every release, and every fix |
| [Release map](docs/release-map.md) | milestone to version |
| [Evaluation](docs/evaluation/) | the frozen benchmarks, and their results |
| [Progress](docs/PROGRESS.md) | one line per phase; [full history](docs/internal/progress-history.md) |
| [Examples](examples/) | the product in one file |
| [Withdrawn](docs/withdrawn/README.md) | material deliberately removed, and why |

## Contributing

```bash
python -m venv .venv && . .venv/bin/activate && pip install -r requirements-dev.txt
pre-commit install
pytest -q
```

The suite needs PostgreSQL with pgvector; `docker compose up -d postgresql` is
enough. It will not run against an unreachable database — it fails instead of
skipping everything.

## License

Apache 2.0. See [LICENSE](LICENSE).