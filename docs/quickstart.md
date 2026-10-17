# Quickstart

Commands only. Each one is the command, not a description of it. The README
explains why; this is for the person who already read it and wants the list.

```bash
# 1. Install. Expect about 216 MB; onnxruntime is the default backend.
git clone https://github.com/raunakdey-07/mind-palace.git && cd mind-palace
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
pip install -e .

# 2. Start PostgreSQL. Three values compose will not invent; copy them from
#    .env.example if you would rather set them in a file.
export MIND_PALACE_RUNTIME_PASSWORD=choose-a-local-password
export MIND_PALACE_CURSOR_SECRET=$(python -c "import secrets;print(secrets.token_urlsafe(32))")
export MIND_PALACE_INGEST_ROOTS="$PWD"
docker compose up -d postgresql

# 3. Point at it and apply the schema.
export DATABASE_URL=postgresql://mpadmin:secret@localhost:5432/mindpalace
python -m alembic -c migrations/alembic.ini upgrade head

# 4. Write one fact and ask about it.
mindpalace init
mindpalace remember "Production uses PostgreSQL." --key datastore
mindpalace recall "What database does production use?"
```

The first `recall` downloads the embedding model, about 90 MB, once. After that
it works offline. To never download it, set `MIND_PALACE_LEXICAL=1` before step 4
— see [runtime.md](runtime.md#model-free-lexical-mode).

## Getting a document in

`remember` is for facts. Documents need a `claims:` block in their frontmatter,
and that is the whole authoring interface — [authoring.md](authoring.md) is the
reference, and it is the third thing worth reading.

```bash
mkdir -p ./docs
cat > ./docs/deployment.md <<'EOF'
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
EOF

mindpalace ingest-repo ./docs
mindpalace recall "What is the primary datastore?"
```

Both commands run in-process. Nothing needs a server started.

## When it answers nothing

A document becomes memory from its headings, list items and `Key: Value` lines, or
from an authored `claims:` block. Prose with none of those is stored and searchable
but answers nothing, and comes back named in the output as `no_claims_declared`. An
authored `evidence:` quote must appear verbatim in the document.
[authoring.md](authoring.md) has the rejection messages and what causes each one.

## What to read next

Nothing. That is the whole first hour. When you need more, the
[README](../README.md#documentation) table says which one.
---

## The full walkthrough

Moved out of README.md on 2026-10-14 when it was cut from 883 lines to about
200. Every `$ ` line above is extracted and run against a built wheel by
scripts/cleanroom_quickstart.py, in the "Clean install from the built wheel" CI job.

`requirements.txt` installs the onnxruntime backend and takes about 216 MB. Adding
the `[torch]` extra makes it roughly 1.35 GB instead, to load the same weights
through sentence-transformers; see [the runtime](../docs/runtime.md#the-onnx-backend)
for why the default is the smaller one. To download no model weights at all, set
`MIND_PALACE_LEXICAL=1` from the start: the product then never imports a model, at
the cost of needing shared vocabulary between question and record, which is
[model-free mode](../docs/runtime.md#model-free-lexical-mode).

One thing does arrive on its own: the first `recall` downloads the embedding model
(about 90 MB, once, to the Hugging Face cache) the way a browser fetches a font.
That needs network on that first call and roughly 30 seconds. After that it works
offline. If you would rather it never arrives, set `MIND_PALACE_LEXICAL=1` and Mind
Palace reads with no model at all — see
[model-free mode](../docs/runtime.md#model-free-lexical-mode).

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

<!-- `mindpalace-os` is NOT on PyPI. Checked 2026-10-13: pypi.org/pypi/
     mindpalace-os/json returns 404 and the simple index returns 404, so the
     bare `pip install mindpalace-os` fails today. It is the distribution's
     declared name (pyproject.toml), not a published one. The command above
     installs the same wheel from the repository instead, which is what actually
     works. Note also that `mind-palace` on PyPI is a different project -- a
     note-taking app at 0.1.0 -- so the hyphenated name is not a fallback.
     Publishing the distribution is the owner's call, not a documentation fix. -->

```console
$ pip install "mindpalace-os @ git+https://github.com/raunakdey-07/mind-palace.git"
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
CLI and the SDK do, with the same receipt. OpenCode, Zed and Claude Code each take
a different shape of the same block; [connecting an agent](mcp.md) has all three,
what comes back, and the two argument mistakes that cost a debugging session.

The arguments are one object deep, and **both** `request` and `request.corpus` are
required. This is the shape that works, and the shape an agent will get wrong if
it is not shown:

```json
{
  "name": "memory_recall",
  "arguments": {
    "request": {
      "corpus": "default",
      "query": "Which datastore did we choose?"
    }
  }
}
```

Passing `{"question": "..."}` instead fails with
`1 validation error ... request Field required`, and so does passing `request`
without `corpus`. The full argument schema — including `as_of`, `intent` and
`budget` — is available from the server itself:

```bash
mindpalace-mcp   # then ask the client for tools/list, or read inputSchema
```

Fifteen tools are advertised. `memory_recall`, `memory_explain`, `memory_current`,
`memory_history`, `memory_changes`, `memory_evidence`, `memory_as_of` and
`memory_replay` all take that same `request` object; `search`, `sync_repo`,
`list_corpora`, `memory_pack` and `memory_snapshot` take their own.

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

- [`examples/decision-memory/`](../examples/decision-memory/) — the whole product in one
  file, from `remember` to `verify`.
- [`examples/project-log/`](../examples/project-log/) — this project's own decision log
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
because a path would let any caller with a credential read a file relative to the
server and recall its contents. A credential says which corpora you may reach; it
does not say you may read the server's filesystem.

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
## Connecting an agent over MCP

There is no `mindpalace mcp` command. The server is an executable named
`mindpalace-mcp`, installed by the `[mcp]` extra, and it speaks JSON-RPC over
stdin/stdout — a client starts it, you do not type at it. It used to print
nothing at all for `--help`; it prints usage now.

```console
$ pip install 'mindpalace-os[mcp]'

$ mindpalace credential issue my-agent -c default -r read_write
$ export MIND_PALACE_MCP_CREDENTIAL=mpk_...

$ mindpalace-mcp --help
```

`MIND_PALACE_MCP_CREDENTIAL` takes the raw `mpk_...` secret. Mint one with
`mindpalace credential issue <name> -c <corpus> -r read_write`; the corpus and
role must match what the agent will touch. It is an environment variable rather
than a tool argument on purpose — a caller that could pass its own credential
would need no authorization at all.

An agent can then write memory with the `memory_remember` tool, whose arguments
are `statement`, `corpus`, and optionally `key` and `file`
([authoring.md](authoring.md#memory_remember)).
