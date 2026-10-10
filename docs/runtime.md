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

MCP is unchanged and correctly so: an MCP server is a long-lived process that pays
the import once.

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

The speed comes entirely from not importing the model stack: `import torch` is
1.68 s, `import transformers` 1.20 s, and `sentence_transformers` adds 2.66 s on top
of those. `tests/test_m016_lexical_mode.py` asserts both halves — that the imports do
not happen, and that the command is measurably faster because of it — so the mode
cannot quietly become a slower way of doing the same thing.

### What it is good for

- A lightweight or offline machine where a 1.35 GB model install and a 6 s first
  recall are not acceptable.
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

`sentence-transformers` remains a declared dependency, so `pip install mindpalace-os`
keeps ranking semantically by default. That was a deliberate decision not to move it
into an optional extra: doing so would mean an existing user upgrading a plain install
silently lost semantic ranking — a surprising behaviour change for the majority case.

What the dependency actually costs, measured on this machine:

| | installed size |
|---|---:|
| the Mind Palace wheel | **227 KB** (was 425 KB until `v0.9.1`, which stopped shipping `scripts/`) |
| `mindpalace-os` installed | 2.1 MB |
| `sentence-transformers` + `torch` + `transformers` + numpy/scipy/scikit-learn | **1,347 MB** |
| cached `all-MiniLM-L6-v2` weights | 88 MB |

The product is about 2 MB and the model stack is about 1.35 GB — a ratio of roughly
670 to 1. That is the whole reason a model-free mode is worth having, and also the
reason it is opt-in: semantic ranking is the better default, and most users should
have it without asking.

What *is* guaranteed, and tested, is that nothing in the package needs the model stack
merely to be imported or to run a command. `tests/test_m016_model_free_package.py`
blocks `torch`, `transformers` and `sentence_transformers` at the import hook and
checks that `--help`, `init`, `recall` and `explain` all still work. So the light path
is real today, and revisiting the dependency model later would be a packaging change
rather than a code change.
