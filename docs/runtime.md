# The local runtime, and model-free mode

Two ways to make Mind Palace fast without a GPU, and an explicit account of what
each one costs.

The background runtime keeps the embedding model and connection pool warm, so the
second command onwards is fast. Model-free mode removes the model stack entirely.
They are independent: the runtime runs in lexical mode, and a one-shot command
works either way.

For the read interface itself, see [the operational memory feed](feed.md). For
network deployment, see [authentication and deployment](deployment.md).

## The local runtime

Optional, and invisible in normal use. It exists because of one measured fact: on
mains power a one-shot `mindpalace recall` from a fresh process costs 6.53 s, of which
61 ms is memory work and the rest is `import torch`, `import transformers` and
`import sentence_transformers` (5.32 s together) plus model construction (0.49 s).

The runtime is a transport over the existing `memory_public.execute` and `remember`.
It runs the same code in a longer-lived process, so what it returns is byte-identical
to the in-process path: same claims, evidence, temporal state, conflicts,
abstention, receipt digest and Memory Pack digest. That is
`tests/test_m016_runtime_equivalence.py`.

| | before | after |
|---|---|---|
| `mindpalace recall` | 6.17 s | 0.63 s |
| `mindpalace explain` | 6.23 s | 0.67 s |
| `mindpalace receipt` | 6.21 s | 0.62 s |
| SDK `recall`, `runtime=True` | 5.11 s | 0.51 s |

Measured on the `sentence-transformers` backend, which was the default through
v0.9.1 and is now the `[torch]` extra. On the default ONNX backend the same
before/after gap starts from 1.42 s rather than 6.17 s, so the absolute saving is
smaller and the reason is different: there is far less import to amortise.

MCP is unchanged and correctly so: an MCP server is a long-lived process that pays
the import once.

## The ONNX backend

ONNX Runtime is the default. `onnxruntime`, `tokenizers` and `huggingface-hub` are
declared dependencies, and `sentence-transformers` is the `[torch]` extra.

The runtime amortises the model load across commands. The backend removes it: the
same published MiniLM weights run under onnxruntime with no torch in the process.
Measured on this machine, one-shot, `MIND_PALACE_RUNTIME=0`:

| | `sentence-transformers` | onnxruntime |
|---|---|---|
| `import` | 6.15 s | 0.40 s |
| model + first encode | 4.69 s | 1.00 s |
| `mindpalace recall` | 7.63 s | 1.42 s |
| `mindpalace receipt -o receipt.json` | 7.57 s | 1.19 s |
| `mindpalace ingest-repo` | 7.77 s | 1.35 s |
| the embedding stack, installed | 5.1 GB | 90 MB |

The last row is the stack each column names and nothing else: `onnxruntime`,
`tokenizers` and `huggingface-hub` against `sentence-transformers`, `torch`,
`transformers` and the numeric stack. The whole default install is larger, because
FastAPI, SQLAlchemy, asyncpg and `mcp`'s tree come too — 216 MB across 79
distributions for `pip install -e '.[cli,mcp]'`, measured.

`mindpalace --help` is 0.25 s either way; it never loads a model.

The two backends are interchangeable, which is what makes this safe. The archive
stores vectors and records the model name and dimension, never the backend, so a
corpus written under one is read by the other. Measured over claim text, a query,
a key, a path, SQL, an empty string, 2,000 characters, non-ASCII and a truncation
boundary: **minimum cosine 0.99999833**, maximum absolute difference 3.45e-04.
`api/services/embedder.py` documents it and
`tests/test_embedder_backend_equivalence.py` asserts it per text, because a cosine
of 0.98 would pass any check that only confirmed the vectors are vectors, and
would silently re-rank every corpus in the field.

`MEMORY_EMBEDDER=torch` forces the old path, which is how the two are compared on a
machine that has both. Without `onnxruntime` installed, the fallback is what you
get and nothing about the product changes except latency.

The runtime still earns its keep on the parts ONNX does not address: the
connection pool, and paying the session build once instead of once per command.

### Commands

```console
$ mindpalace runtime status
Runtime is not running.
  commands still work; each one starts its own runtime if you want a warm one
  start it with: mindpalace runtime start

$ mindpalace runtime start --wait
Runtime starting. It loads the model in the background.
  runtime: ready  model: loaded  up: 6s

$ mindpalace runtime status --json
{ "state": "ready", "pid": 1234, "model": "loaded", "protocol": 1, ... }
```

A command that finds no runtime runs in-process exactly as before and starts one in
the background. The warm-up is triggered by `init` and `remember`, which are already
fast, so by the time the user reaches for `recall` the model is resident.

### Security boundary

- A Unix domain socket, never a TCP port. `$XDG_RUNTIME_DIR/mindpalace`, or
  `~/.cache/mindpalace`. The directory is `0700` and the socket is `0600`, so the
  filesystem is the authorisation boundary.
- Every request carries a token read from a `0600` file next to the socket. Never a
  command-line argument, which any local user could read out of `ps`. The token is a
  second check on top of the socket permissions, so a socket that ends up somewhere
  more permissive does not become an open door.
- Loopback only in principle and Unix-socket-only in practice. There is no code path
  that binds a TCP port.
- Nothing about memory content is logged. The runtime's own output is discarded; the
  five product spans go through the product's telemetry, which never carries content.
- `MIND_PALACE_STATE_DIR` overrides the location, which is how the test suite gets a
  runtime of its own without touching a developer's.

### Lifecycle

| state | meaning |
|---|---|
| `starting` | bound, model not yet loaded |
| `loading` | importing the model |
| `ready` | serving |
| `stopping` | finishing an in-flight request |
| `failed` | could not start; `status` carries the reason |

- **Idle exit.** 15 minutes with no requests. It does not outlive the session that
  created it.
- **Stale sockets.** A runtime that was killed leaves its socket file. The next
  command sees `ECONNREFUSED`, removes it and starts a fresh one. A user never sees a
  stale socket as an error.
- **Single instance.** `bind()` refuses to take another live runtime's socket.
- **Protocol version.** A runtime speaks `PROTOCOL = 1` and refuses a request that
  asks for a different one, with an instruction to restart it. This is what stops a
  runtime from an older build serving a newer build's request with the old semantics.
- **No model, no silent different answer.** If the model cannot load, the runtime
  records `model: unavailable` and the reason, and keeps serving. A query then takes
  the same path it would have taken without a runtime, which for a genuinely missing
  model is the same `memory_unavailable` / 503 the CLI already produces. There is no
  new fallback and no lexical substitution.

### Turning it off

```bash
export MIND_PALACE_RUNTIME=0     # CLI, SDK and runtime command alike
mindpalace runtime stop          # just this one
```

The runtime is never required. On a platform without `AF_UNIX` it is not started at
all. `MindPalace(runtime=True)` is opt-in for the same reason: a long-running
application has the model resident, so a runtime would only add a socket hop and a
second copy of the model.

### A rejected alternative

The other way to remove the 5.3 s import from *every* command rather than from all
but the first — a lexical fast path, without a background process at all — was
measured and rejected. It is written up in full under
[model-free / lexical mode](#model-free-lexical-mode) below, because the reason it
failed is about relevance and abstention rather than about processes.

## Model-free / lexical mode

```bash
MIND_PALACE_LEXICAL=1 mindpalace recall "What datastore does production use?"
```

Means: **deliberately use the model-free lexical relevance path.**

This is not a second retriever. `memory_query` already falls back to lexical relevance
whenever the embedding model cannot be loaded, and this mode takes that branch on
purpose. One implementation, one set of acceptance gates, one set of authoritative
semantics. The mode is announced once on stderr, so it never contaminates an answer, a
JSON document, or a pipe:

```console
$ MIND_PALACE_LEXICAL=1 mindpalace recall "What datastore does production use?"
retrieval: lexical (model-free, MIND_PALACE_LEXICAL=1)
The production datastore changed from SQLite to PostgreSQL.
   source: memory/datastore-aa7e5b234e1d.md @ 5aae989d3134
   time:   true as of 2026-10-08 03:40 UTC
```

### Measured, not asserted

In-process, cold process, runtime disabled, same corpus:

| | semantic | `MIND_PALACE_LEXICAL=1` |
|---|---|---|
| `recall` | 6.17 s | **0.63 s** |
| `explain` | 6.23 s | **0.67 s** |
| `history` | 0.81 s | 0.63 s |
| `torch` / `transformers` / `sentence_transformers` imported | yes | **none** |

The semantic column is the `[torch]` extra, measured as it stood at v0.9.1 when it
was the default. It is not what a default install does now: onnxruntime builds the
same session in about 0.4 s, so a cold `recall` is 1.42 s rather than 6.17 s. The
lexical column is unaffected by that and is the same number either way.

The speed comes entirely from not importing the model stack: `import torch` is
1.68 s, `import transformers` 1.20 s, and `sentence_transformers` adds 2.66 s on top
of those. `tests/test_m016_lexical_mode.py` asserts both halves — that the imports do
not happen, and that the command is measurably faster because of it — so the mode
cannot quietly become a slower way of doing the same thing.

The bound on that second half was 2x when this was written and is 1.33x now,
because the saving is the model load and the default load is about 0.4 s rather
than six. Measured on the default backend: 1.71 s against 0.65 s here, and 0.83 s
against 0.46 s on a GitHub runner. The import-table half is the one that gates.

### What it is good for

- A machine that will not take a model at all. The default install is 216 MB and a
  cold recall is about 1.4 s, so this is a narrower case than it was at v0.9.1,
  when a 1.35 GB install and a 6 s first recall were the price of ranking.
- A container with no GPU and no outbound network.
- A CI job that needs authoritative memory but not semantic ranking.
- Anyone who wants predictable, explainable token-overlap ranking and can live with
  the vocabulary matching that implies.

### What it costs

**It does not always select the same keys as semantic mode, and that is not a bug to be
fixed.** Measured on a ten-fact corpus across sixteen question shapes, the two modes
agreed on fifteen. The difference was an abstention:

```text
"When did we last change the backup schedule?"
    semantic -> ['Backups run nightly at 02:00 UTC and are retained for 35 days.']
    lexical   -> [] ['NO_RELEVANT_MEMORY']
```

Semantic ranking answers from meaning; lexical ranking needs shared vocabulary. So
lexical mode is best understood as the model-free rung of the same relevance gate
rather than a replacement scorer. Nothing was weakened to make the two agree — an
answer that differs only in what it declines to say is still a wrong answer.

`tests/test_m016_lexical_mode.py` asserts this divergence deliberately. If a future
change makes the modes agree here, that test should be changed with a reason rather
than left to fail or quietly deleted.

#### The sharpest version of the same limitation

Ranking scores the terms that **discriminate between candidates** — a term every
candidate carries cannot tell them apart, so it is removed before scoring. That is
correct for ranking, and it has a consequence worth knowing before you rely on this
mode:

```console
$ MIND_PALACE_LEXICAL=1 mindpalace recall "What datastore does production use?"
No memory matched that question.
  NO_RELEVANT_MEMORY
```

Two `datastore` memories exist — one superseded, one current — so `datastore` is a term
*every* candidate carries, it is removed, and lexical ranking has nothing left to score.
Ask with a term that actually distinguishes them and it answers:

```console
$ MIND_PALACE_LEXICAL=1 mindpalace recall "When did we switch to CockroachDB?"
The production datastore is now distributed CockroachDB.
```

This is a property of lexical ranking that predates v0.9.0 and is unchanged by it;
`api/services/memory_relevance.py` scores on the reduced term set in both. It is also
why the CI step for this mode asserts that a *discriminating* question is answered,
rather than only that the command exits successfully — a mode that abstained on
everything would otherwise pass.

### Interacting with the runtime

A lexical read **bypasses the runtime**. A runtime exists to hold the model, and a
developer who asked for model-free reads does not want that process in the path.
Bypassing it needs no protocol change and no second implementation: the same
`execute` runs in the caller's own process. `mindpalace runtime status` continues to
report on the runtime honestly; it simply is not involved in that read.

### Why not a lexical fast path

An obvious alternative: rank lexically first, and load the semantic model only when
that looks insufficient. No background process, no second mode, and it would remove
the 5.3 s import from *every* command rather than from all but the first. It was
measured on a ten-fact corpus across sixteen question shapes, and on fifteen it was
byte-identical to semantic ranking. The sixteenth was not:

```text
"When did we last change the backup schedule?"
    semantic -> ['Backups run nightly at 02:00 UTC and are retained for 35 days.']
    lexical   -> [] ['NO_RELEVANT_MEMORY']
```

A question semantic ranking answers, lexical ranking abstains on — and shipping it
would have made `NO_RELEVANT_MEMORY` depend on whether the model happened to be
cached on the machine, which is the acceptance coupling the M013 work exists to
prevent, reappearing somewhere new. On 15 of 16 questions it looked safe, which is
precisely why it was worth writing down rather than discarding.

An explicit mode is honest about the trade-off; an automatic one hides it. This mode
is the same rung of the same gate, taken on purpose, so a user who wants model-free
reads opts in and knows exactly what they are getting.

### The model dependency itself

`pip install mindpalace-os` installs onnxruntime, tokenizers and huggingface-hub,
which is what the default read path uses. `sentence-transformers` is the `[torch]`
extra. It was a declared dependency until this change, on the grounds that moving it
would make an existing user's plain install silently lose semantic ranking. The
measurement above is why that reasoning no longer holds: the fallback produces the
same vectors to a cosine of 0.99999833, so switching does not change any ranking,
and the install drops from gigabytes to megabytes.

What each stack costs, measured on this machine:

| | installed size |
|---|---:|
| the Mind Palace wheel | **227 KB** (was 425 KB until `v0.9.1`, which stopped shipping `scripts/`) |
| `pip install -e '.[cli,mcp]'`, ONNX backend, 79 distributions | **216 MB** |
| `sentence-transformers` + `torch` + `transformers` + numpy/scipy/scikit-learn | **1,347 MB** |
| cached `all-MiniLM-L6-v2` weights | 88 MB |

The product is about 2 MB and the torch stack is about 1.35 GB — a ratio of roughly
670 to 1. That is the whole reason a model-free mode is worth having, and also the
reason it is opt-in: semantic ranking is the better default, and most users should
have it without asking.

What *is* guaranteed, and tested, is that nothing in the package needs the model stack
merely to be imported or to run a command. `tests/test_m016_model_free_package.py`
blocks `torch`, `transformers` and `sentence_transformers` at the import hook and
checks that `--help`, `init`, `recall` and `explain` all still work. So the light path
is real today, and revisiting the dependency model later would be a packaging change
rather than a code change.

## The query log

Every `recall` and `remember` appends one JSON object to
`~/.mind-palace/logs/queries.jsonl`: timestamp, operation, the query,
`match_status`, the top three scores, the claim ids returned, and latency. Writes
one line per call, keyed by a `query_id` that both the response and the log carry.

**On by default on a local connection, off on a scoped one.** A tenant-scoped
connection is a server deployment, and logging there is somebody's retention
policy to set. `MIND_PALACE_QUERY_LOG=0` turns it off everywhere;
`MIND_PALACE_QUERY_LOG=/path/to/file.jsonl` moves it; `MIND_PALACE_DATA_DIR`
moves the directory it lives in.

**No memory content is recorded.** Not the claim text, not the source path, not
the corpus. The claim ids are 64 hex characters that carry nothing on their own,
and a log sitting in the same home directory as the database it describes is
exactly where content ends up by accident.

Grade a call with the `query_id` a response reported:

```console
$ mindpalace feedback b062424783ba --verdict wrong --note "meant the cache"
recorded 'wrong' against the query of 2026-10-16T19:56:52+00:00

$ mindpalace feedback-summary
log:  /home/you/.mind-palace/logs/queries.jsonl
calls: 214    graded: 37

  abstained_wrongly    4
  right               26
  wrong                7

by match_status:
  ambiguous     9 graded,    3 not right
  confident    28 graded,    8 not right
```

The breakdown by `match_status` is the part worth reading. If `ambiguous` answers
are graded about as often wrong as `confident` ones, the near-tie signal is
carrying no information and the margin threshold is wrong. If they are graded worse
which is the expectation. Then the shortlist beside an ambiguous answer is doing
work that a single top score could not.

Writing a row never fails a query. A full disk costs some telemetry and nothing
else.
