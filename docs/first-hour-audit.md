# The first-hour audit

What a developer who has never seen this repository meets, in the order they meet
it, measured by following [the README](../README.md) literally in a container with
nothing preinstalled: `python:3.12-slim`, `git`, and a PostgreSQL server with
pgvector obtained the way the README says to obtain one. No knowledge from earlier
sessions was used; every step below is one the README states.

Reproduce with the commands in each section.

## Two blockers, both fixed

The documented path did not work. Neither failure was visible from the repository.

### 1. The wheel did not contain `mindpalace_errors`

```
$ mindpalace init
Traceback (most recent call last):
  File "/work/.venv/bin/mindpalace", line 3, in <module>
    from cli.main import app
  ...
ModuleNotFoundError: No module named 'mindpalace_errors'
```

`mindpalace_sdk.py` imports it unconditionally and `api/main.py` imports it four
times, but `pyproject.toml`'s `py-modules` did not list it. A built wheel contained
seven top-level modules and not this one.

Nothing failed locally because the suite runs from the repository root, where the
working directory is on `sys.path`. **Importable from the source tree** and
**declared to ship** are different facts, and only the first was being checked.

Fixed, and `tests/test_wheel_contents.py` now walks the shipped tree's AST and
fails if a module the code imports is not declared.

### 2. The default corpus refused every write

```
$ mindpalace remember "The production datastore changed from SQLite to PostgreSQL." --key datastore
Mind Palace could not complete that.

Why: (sqlalchemy.dialects.postgresql.asyncpg.CheckViolationError) new row for
relation "memory_documents" violates check constraint "memory_documents_check"
```

`corpora.id` is `CHAR(64)`. The default corpus's id is 56 zeroes followed by
`default` — **63 characters** — so PostgreSQL blank-pads it on the way in and hands
the padding back on the way out.

The database derives document identity itself:

```sql
CHECK (id = encode(sha256(convert_to(rtrim(corpus_id::text) || ':' || path,
                                       'UTF8')), 'hex'))
```

`rtrim` strips the padding. `memory_document_id` hashed the padded string. The two
agreed for every corpus whose id is a 64-character digest — that is every corpus
created from a name — and disagreed by one byte for the default corpus, the only
corpus whose id is not 64 characters long.

Fixed at the boundary that introduces the padding. `tests/test_default_corpus_is_writable.py`
reproduces the constraint in Python and fails with the same
`CheckViolationError` when the derivation is reverted.

## Timings

| Step | Time | Notes |
|---|---:|---|
| `python -m venv` + `pip install -r requirements.txt` | **202 s** | produces a **5.9 GB** venv — see stumble 1 |
| `pip install -e .` | 5 s | |
| `alembic upgrade head` | 1 s | provisions pgvector, pgcrypto and pg_trgm itself |
| `mindpalace init` | <1 s | |
| `mindpalace remember` | 1 s | no model needed |
| **first `mindpalace recall`** | **13 s** | downloads the 88 MB embedding model |
| second `recall` | 0.93 s | |
| `explain`, warm | 0.96 s | |

**From `pip install` to first correct recall: about 221 s**, of which 202 s is the
dependency install. **After the dependency install is cached, about 19 s.**

The README says "roughly 30 seconds" for the model download. Measured here at
13 s on a warm network; the README's figure is not wrong, but it is the only
number in the install section that is not a rounding of something measured on
this machine.

## Stumbles, ranked by how likely a new developer is to stop here

### 1. `requirements.txt` installs 5.9 GB, most of it CUDA

```
$ du -sh .venv
5.9G    .venv
```

`torch` pulls `nvidia-cudnn`, `nvidia-cublas`, `cuda-toolkit` and `triton` — about
5 GB of GPU libraries on a machine with no GPU. The product path uses `torch` on
CPU, and `MIND_PALACE_LEXICAL=1` does not use it at all.

Nothing in the README mentions this. A developer on a laptop with modest disk sees
a download that takes minutes and a directory they do not recognise. This is the
single biggest thing in the first hour.

### 2. The MCP tool arguments are not documented anywhere

The README says:

> An agent asks `memory_recall` for an answer and `memory_explain` when it wants to
> know why the answer is trustworthy.

It does not say what to pass. The actual shape is two levels deep and both levels
are required:

```json
{"request": {"corpus": "default", "query": "Which datastore did we choose?"}}
```

Asking with the obvious `{"question": "..."}` returns:

```
Error executing tool memory_recall: 1 validation error for memory_recallArguments
request   Field required
```

and adding `request` but not `corpus` returns the same complaint about `corpus`.
An integrator wiring an agent from the documentation alone cannot get a call
right without either guessing or reading the server's tool schema. The schema
*is* discoverable through `tools/list`, which is the right protocol design — but
the README should say so and show the shape.

### 3. The bare Python image has neither `git` nor `psql`

`python:3.12-slim` has no `git`, and the README's first command is `git clone`. A
developer starting from a container needs to know that before they are surprised.
`psql` is *not* needed for the documented path — the migrations and the CLI both
speak to the database over the wire — so only `git` matters, and only for the
clone.

### 4. A warm runtime hides a code change

While testing the fix for blocker 2, the CLI kept failing after the source was
correct and the Python API had been verified working. `mindpalace remember` on the
CLI goes through `_remember_via_runtime`, a long-lived background process that had
loaded the old module. `MIND_PALACE_RUNTIME=0 mindpalace remember ...` passed
immediately.

`mindpalace runtime stop` then `mindpalace remember ...` also passed, after which
the runtime restarted with the new code and stayed correct.

A developer changing the code and re-running a command may be talking to a process
that has not reloaded. The error message says nothing about this. This is a real
trap and it cost the audit more time than any other stumble except the two blockers.

### 5. `docker compose` is presented as an alternative, not a requirement

The README's install block offers

```bash
docker compose up -d postgresql          # or: podman-compose up -d postgresql
```

The `docker-compose.yml` in the repository also defines `ollama` and `backend`
services. Starting the whole file rather than the one service pulls an LLM runtime
that the documented path never uses.

## Integration from the documentation alone

**Python SDK — works unaided.** The README's ten-line snippet ran verbatim,
including the receipt digest, with no changes:

```
$ python /tmp/sdk_demo.py
We will use PostgreSQL as the primary datastore. memory/decision.datastore-0d715ec3d74b.md CURRENT
2b5804d80176e7fb9d07bbe8b0b5f76b3307c4ed50bf879477c99c65140a1972
```

12 s, including the model load. Two things it did not tell the reader: the first
call prints `Loading weights` and an unauthenticated-Hugging-Face warning to
stderr, both of which look like failures and are not.

**MCP — works, but only after a step the documentation does not give.** Once the
argument shape is right, `memory_recall` and `memory_explain` return the same
authoritative memory the CLI and the SDK return. Fifteen tools are advertised.
`mindpalace-mcp` is on `PATH` after install; the README does not say to verify
that, and it is the thing that breaks if the wheel is wrong — which it was.

## What this audit does not cover

* A real `git clone` from GitHub. The audit used the working tree, so a file that
  is missing from the repository but present on this machine would not have shown
  up here. (One such class of problem did show up separately, and is fixed: five
  documentation pages were untracked and shipped as dead links.)
* macOS, Windows, or any platform other than this Linux container.
* A slow or metered network. The 202 s install was against a warm, unthrottled
  registry; a real first-time download of 5.9 GB would be much longer, and the
  README does not warn about that.
* The `server` deployment profile, credentials, and multi-tenancy. The audit ran
  entirely in the documented `local` default.
## Dogfood: this repository's own documentation, as memory

Eleven of this repository's documentation files were archived into one corpus
using the documented authoring interface -- a `claims:` block whose `claim` text
must appear verbatim in the document -- and then asked ten questions a maintainer
of this project would actually ask. Twelve claims were authored across the eleven
documents; all twelve were archived with evidence.

| | n | which |
|---|---:|---|
| correct | 5 | Q1, Q2, Q5, Q6, Q10 |
| abstained | 1 | Q9 |
| wrong | 3 | Q3, Q7, Q8 |
| **wrong, confidently** | 1 | Q4 |

The three wrong answers and the confident-wrong one are worth reading:

* **Q3, "if two users must not see each other's memory, what should I do?"**
  answered with a SQLSTATE line from the withdrawn-feature record. The correct
  answer -- one corpus per confidentiality boundary -- is in `when-to-use.md` and
  was not returned.
* **Q7, "what SQLSTATE caused the remember 503?"** answered with the sentence
  saying the endpoint returned 503, which is the symptom, not the cause. The
  42501 answer was returned for Q3 instead: both questions pulled from the same
  region of the same document and the ranking put the wrong span on the wrong
  question.
* **Q8, "is cryptographic erasure implemented?"** answered with a sentence
  describing what cryptographic erasure *would* do. The document's own heading
  says it is not implemented.
* **Q4, "does deleting a corpus remove the data from backups?"** answered
  confidently from the cryptographic-erasure section. A reader would take away a
  statement about backups that does not answer whether ordinary erasure reaches
  backups. This is the one **confident-wrong** answer.

**This is not comparable to the held-out numbers and must not be read as such.**
Twelve claims across eleven documents is an extremely sparse fact set -- one or
two facts per document -- against questions written to paraphrase rather than
quote. The held-out set asks 157 questions of 157 authored facts and reports one
confident-wrong; this asks ten questions of twelve facts and reports one. Same
proportion, wildly different confidence intervals, and this sample is far too
small to mean anything on its own.

It is still worth recording, because it is the only time the product has been
pointed at its own documentation, and because Q4 is exactly the failure a
maintainer would find unacceptable: a confidently wrong answer about whether
customer data left the building.

## What the dogfood exposed in the authoring interface

Three defects, all of them silent:

1. **`parse_markdown` discards malformed frontmatter without a word.** The first
   attempt at this corpus generated YAML that `python-frontmatter` could not
   parse. `parse_markdown` catches every exception and returns `{}`, so the
   `claims:` block vanished, `sync` reported `added=11, success=True`, and the
   archive contained 11 documents and **0 claims**. Nothing anywhere said why.
   The same silent path would swallow a typo in a real document.

2. **A claim that does not appear verbatim is a hard error with a good
   message.** `claim[0] text must appear in the supporting chunk; semantic
   inference is not permitted` is exactly right, and it is worth keeping.

3. **`sync` collapses every per-document failure into `document_ingestion_failed`.**
   The real reason -- a missing `value:` field, a non-verbatim claim -- is
   available only by calling the ingestion service directly. An author syncing a
   directory gets a count and no diagnosis.
