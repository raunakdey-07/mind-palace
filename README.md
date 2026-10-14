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

## The five-minute path

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

Commands are fast from the second one onwards — a background runtime keeps the
model warm, and `recall` drops from 6.2 s to 0.63 s. If one feels slow,
`mindpalace runtime status` says whether a runtime is running and whether its
model is loaded. `MIND_PALACE_RUNTIME=0` turns it off everywhere.

Export what you were told, and verify it somewhere else:

```console
$ mindpalace receipt "What database does production use?" -o receipt.json
VERIFICATION
  receipt available: yes (1 receipt(s))
  authoritative digest: 2c3077bd8d76627348c569dc5bd2227a08696b0aa54bf3f733f71f5abd3b1aa8
  export and verify offline with:
    mindpalace receipt "What database does production use?" -o receipt.json
    mindpalace verify receipt.json
  Authenticity is not established without a trust anchor you pinned yourself.

$ mindpalace verify receipt.json
VERIFIED
  receipts checked: 1
```

`verify` needs no database, no model and no network. It proves the answer was not
altered on the way out; it does **not** prove the original source was true.

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

```console
$ mkdir -p ./docs && printf '%s\n' '---' 'title: Deployment architecture' 'claims:' '  - key: architecture.datastore' '    value: PostgreSQL' '    claim: The primary datastore is PostgreSQL.' '    evidence: The primary datastore is PostgreSQL.' '---' '' '# Deployment architecture' '' 'The primary datastore is PostgreSQL. It runs in three availability zones.' > ./docs/deployment.md

$ mindpalace ingest-repo ./docs --corpus readmedemo
[OK] sync: +1 ~0 =0 -0 !0, 1 chunks

$ mindpalace recall "What is the primary datastore?" --corpus readmedemo
The primary datastore is PostgreSQL.
   source: deployment.md @ 178141e34f2d
   time:   true as of 2026-10-14 06:54 UTC
```

The file that wrote is this, and it is what the author types:

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

**Those `$ ` lines are executed on every push**, from a built wheel, by
`scripts/cleanroom_quickstart.py` in the "Clean install from the built wheel" job.
Paraphrase the evidence in them and the job fails.

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

## What is measured

Two held-out numbers, and neither is superseded by the other:

| benchmark | result | what it is |
|---|---|---|
| M006.75 frozen held-out | **45/60 (75.0%)** exact on current source | the frozen benchmark, safety invariants at 60/60 |
| independently authored, 157 questions | **131/157 (83.4%)** | a generalisation figure, and a known limitation |

Both are reported because reporting only the better one would be the dishonest
choice. This release changed no benchmark input, policy, evaluator semantic,
tokenizer or threshold, and that is measured: all 60 canonical per-question
decisions are identical to `v0.8.0`. [The reproducibility report](docs/evaluation/m00675-reproducibility.md)
records one discrepancy this release found and did not paper over.

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
| [Examples](examples/) | the product in one file: [decision memory](examples/decision-memory/), [project log](examples/project-log/) |
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