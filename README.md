# Mind Palace

[![Release](https://img.shields.io/github/v/release/raunakdey-07/mind-palace)](https://github.com/raunakdey-07/mind-palace/releases)
[![CI](https://img.shields.io/github/actions/workflow/status/raunakdey-07/mind-palace/ci.yml?branch=main&label=CI)](https://github.com/raunakdey-07/mind-palace/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue)](LICENSE)

[Current release: v0.9.1](docs/release-map.md) · [Changelog](CHANGELOG.md)

> **The path below installs `0.9.1` from this branch, not `0.9.0` from PyPI.**
> `v0.9.1` is prepared but not tagged or published. To install the last tagged
> release instead, clone the `v0.9.0` tag rather than the default branch.

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

## Quickstart

For wiring Mind Palace into a coding agent so it tracks your project's decisions and
runbooks. These commands run in CI against a built wheel (`scripts/cleanroom_quickstart.py`).

```bash
# The default install is ONNX, not torch: cold `recall` 1.4 s, install 90 MB.
git clone https://github.com/raunakdey-07/mind-palace.git && cd mind-palace
python -m venv .venv && . .venv/bin/activate && pip install -e '.[cli,mcp]'
export MIND_PALACE_RUNTIME_PASSWORD=choose-a-local-password
export MIND_PALACE_CURSOR_SECRET=$(python -c "import secrets;print(secrets.token_urlsafe(32))")
export MIND_PALACE_INGEST_ROOTS="$PWD"
docker compose up -d postgresql
export DATABASE_URL=postgresql://mpadmin:secret@localhost:5432/mindpalace
python -m alembic -c migrations/alembic.ini upgrade head
mindpalace init

# Ingest a plain ADR. No frontmatter, no `claims:` block: headings, list items and
# `Key: Value` lines become claims, each quoted from the file it came from.
# docs/adr/001-datastore.md is the worked example; point this at your own.
mindpalace ingest-repo ./docs/adr --corpus project

# Recall. An ambiguous answer prints the claims it could not separate, with ids.
mindpalace recall "What is the primary datastore?" --corpus project

# Record what replaces it. `--topic-key` retires what is live under that key, across
# files: the new statement is a different document from the old one.
mindpalace remember "The primary datastore is PostgreSQL 16." \
  --corpus project --key architecture.datastore --topic-key architecture.datastore

# What is true now, and what it displaced.
mindpalace recall "What is the primary datastore?" --corpus project
mindpalace history "" --corpus project

# Verify the answer offline: no database, no model, no network.
mindpalace receipt "What is the primary datastore?" --corpus project -o receipt.json
mindpalace verify receipt.json
```

The first `recall` builds an ONNX session: about 1.4 s, then 0.3 s. A background
runtime holds it and the connection pool warm after that;
`mindpalace runtime status` says whether one is up. `MIND_PALACE_RUNTIME=0` turns
it off, and `MIND_PALACE_LEXICAL=1` turns the model off entirely.

Your agent gets the same commands over MCP, with the same `match_status` and
`candidates` on the answer and the same `supersedes_claim_id` on the write, which
takes an id prefix or a phrase from the claim's own text. A document can say so
itself: `supersedes: 0001.md` in its frontmatter. Point the client at
`mindpalace-mcp` with `DATABASE_URL` and `MIND_PALACE_INGEST_ROOTS` set; the JSON
for OpenCode, Zed and Claude Code is in [connecting an agent](docs/mcp.md).

`recall` and `remember` also report a `query_id`. Grade a wrong answer; it appends
to `~/.mind-palace/logs/queries.jsonl` and changes nothing else.

```bash
mindpalace feedback <query_id> --verdict wrong --note "meant the cache"
mindpalace feedback-summary
```

## Documents and claims

`remember` is for facts you have in hand. A directory of Markdown you already have
goes through `mindpalace ingest-repo ./docs`, and there are two ways for a document
to become memory.

**Plain Markdown needs no annotation.** A heading path scopes what follows it, and
the claims under it are its list items, its numbered entries and its `Key: Value`
lines. Every one is quoted back from the file it came from, so the evidence is the
document's own bytes:

```markdown
# ADR 001: primary datastore

- The primary datastore is PostgreSQL 15.
- Writes go through the repository layer.

## Status

Status: Accepted
```

**A `claims:` block is the authoring interface** when you want to name the keys
yourself. It says what to remember, and the evidence quote must appear **verbatim**
in the body:

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

A `claims:` block wins outright: if a document has one, the structural reader does
not run on it. An author's key is a decision, and the tool does not second-guess it.

What is extracted is never inferred. Nothing is summarised, reworded or merged; a
claim is a line that was in the file, and its evidence is a character range of the
archived chunk. See [docs/authoring.md](docs/authoring.md) for the exact-quote rule
and both rejections with their messages.

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
- **It does not infer claims from prose.** A claim is always a line that was in a
  document or a statement you were told, quoted back. What the structural reader
  extracts, it does not write.
- **It is not a vector store.** Retrieval is one stage of a pipeline that ends in
  provenance, not the product.
- **It does not authenticate its own history.** A receipt proves internal
  consistency. Trust requires an anchor you pinned yourself, outside this system.
- **A supersession retires one claim.** `--supersedes` names it, or `--topic-key`
  retires whatever is live under that key, and either works across files. Where two
  documents disagree under one key and neither is retired, recall reports the
  disagreement rather than picking one.
- **A `claims:` block is YAML, and YAML is strict.** A backtick, a colon-space or
  a leading `@` in an evidence quote discards the whole block. Quote the value.
  The ingest now says `frontmatter_unparseable` instead of `no_claims_declared`.
- **An agent can write memory, and that is a risk.** `memory_remember` is one
  of 16 MCP tools and the first that writes. Without `file=` the evidence for the
  claim is the statement itself, so it proves what the agent was told and not that
  it is true ([why, and the review it needs](docs/authoring.md#memory_remember)).
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
python -m venv .venv && . .venv/bin/activate
pip install -e '.[cli,mcp,dev]'
pytest -q
```

The suite needs PostgreSQL with pgvector; `docker compose up -d postgresql` is
enough. It will not run against an unreachable database — it fails instead of
skipping everything.

## License

Apache 2.0. See [LICENSE](LICENSE).