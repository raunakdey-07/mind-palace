# Five-minute path performance

Two different questions, measured two different ways.

**How fast is the work?** — `scripts/five_minute_probe.py`, around the service call,
in a process that has already paid the import. This is the retrieval and projection
cost.

**How fast is the journey?** — each documented command as its own process, which is
what a person in a terminal actually pays. This is where the cost of being a fresh
process shows up.

Everything here was measured on mains power. Numbers taken on battery elsewhere in
this repository's history are 3 to 4× worse and are not comparable; the environment
is recorded with each figure rather than assumed.

---

# Part 1: the journey

One command per process, same corpus, median of three. The two columns differ in
exactly one thing: whether a local runtime is running.

| command | in-process | with runtime |
|---|---|---|
| `mindpalace init` | 0.63 s | 0.62 s |
| `mindpalace remember` | 0.65 s | 0.64 s |
| `mindpalace recall` | 6.10 s | **0.32 s** |
| `mindpalace explain` | 6.77 s | **0.32 s** |
| `mindpalace history` | 0.83 s | 0.31 s |
| `mindpalace receipt` | 6.10 s | 0.33 s |
| `mindpalace verify` | 0.75 s | 0.55 s |
| SDK `recall`, fresh process | 5.92 s | 0.66 s |
| MCP `memory_recall`, fresh process | 6.47 s | 6.45 s |

MCP does not move, and should not: an MCP server is a long-lived process and pays
the import once, so there is nothing for a runtime to save.

## Where 6.10 s actually went

Profiling with `-X importtime`, not guessing:

| phase | cost |
|---|---|
| interpreter start | 0.038 s |
| CLI and api imports | 0.515 s |
| database connection | +0.190 s |
| `import torch` | 1.679 s |
| `import transformers` | +0.982 s |
| `import sentence_transformers` | +2.660 s |
| model construct and first embed | +0.486 s |
| **the memory work** | **0.061 s** |

89% of a one-shot recall was Python import and model construction for a command that
exited 61 ms later. `torch` and `transformers` together are 2.66 s of it;
`sentence_transformers` adds 2.66 s on top of those, which is the part that is not
obvious. Loading the weights is only 0.49 s, so the cost is almost entirely import.

Nothing about ranking, storage or the network is in that list. No retrieval work
would have changed it.

## Two attempts at fixing it without a runtime

Both were tried, both measured, and both are recorded because the reasoning matters
more than the outcome.

**Write the cache from the read side only, and never let a write embed.** This is
what shipped, and it works: a corpus authored through `remember` no longer
re-encodes itself on every read (2,106 ms for the first read, 151 ms for the second
at 50 statements). It does not help the *first* read of a process, which is the one
in the table above.

**Let `remember` fill the cache when the model is already resident.** Correct in
principle, and it did nothing: every `mindpalace remember` is a fresh process, so a
residency test never fires and the read cache stays permanently cold.

**Let `remember` fill the cache when the model is already on disk.** This worked and
was rejected anyway. `import sentence_transformers` is 5.32 s whether or not the
weights are cached, so a write that merely *reaches* for the model still costs
eleven seconds, on the first command a newcomer runs. It trades a 61 ms service call
for an 11 s import (measured on battery; 5.3 s on mains) and makes the first
impression worse.

**Skip the model when lexical discovery looks confident enough.** The most
attractive option, because it would remove the import from *every* command rather
than from all but the first. Measured on a ten-fact corpus across sixteen question
shapes: fifteen of sixteen byte-identical. The sixteenth was an abstention the
semantic ranking did not make. See
[operations](../operations.md#why-not-a-lexical-fast-path).

## What the runtime does, and what it costs

A background process holding the model and a warm connection pool, reached over a
Unix domain socket. The CLI uses it when it is there and does not care when it is
not; a command that finds no runtime runs in-process exactly as before and starts
one, so the warm-up happens during `init` or `remember` and is finished before the
user types `recall`.

The floor is not the runtime: it is a fresh Python process, a socket round trip and
61 ms of memory work. 0.32 s is what that costs, and the 0.19 s of CLI import is the
largest single remaining item.

---

# Part 2: the work

```bash
export DATABASE_URL=postgresql://mpadmin:secret@localhost:5432/mindpalace
python scripts/five_minute_probe.py --statements 200  --repetitions 20
python scripts/five_minute_probe.py --statements 1000 --repetitions 20
```

The probe writes into a random schema and drops it afterwards. Nothing survives the
run, and no corpus is left behind.

`remember`, `recall` and `explain` are timed around the service call itself, on the
same connection the application would use. `verify` is timed in a **separate
interpreter** with no `DATABASE_URL` and no network, because portability is the
property being claimed and measuring it in-process would not test it.

`recall` and `explain` are four questions each: one per topic in the probe corpus.
`remember` writes a key that does not exist yet, so each is a real insert rather than
the idempotent no-op path.

Wall clock for the service call, milliseconds. 200 remembered statements, 20
repetitions:

| operation | n | p50 | p95 |
|---|---|---|---|
| remember | 20 | 10.79 | 11.46 |
| recall | 80 | 92.64 | 107.75 |
| explain | 80 | 93.78 | 105.20 |
| verify | 20 | 0.82 | 0.93 |

1000 remembered statements, 20 repetitions:

| operation | n | p50 | p95 |
|---|---|---|---|
| remember | 20 | 13.11 | 15.57 |
| recall | 80 | 987.62 | 1295.75 |
| explain | 80 | 808.54 | 1192.71 |
| verify | 20 | 0.84 | 1.02 |

Bulk ingest, for scale: 200 statements in 2.14 s (10.7 ms each), 1000 in 13.92 s
(13.9 ms each).

## What these numbers mean

**`verify` is not a database operation.** Under 1 ms at both sizes, in a process that
never opened a socket. A receipt is a file; this is the property that makes it
portable, and it is why verification needs no database, no model and no network.

**`remember` is flat.** 10.8 ms at 200 statements and 13.1 ms at 1000, because a
write touches one document regardless of how much else the corpus holds. It is on
the critical path of every ingestion pipeline and it does not degrade.

**`recall` scales linearly with the corpus, and that is the real finding.** 93 ms at
200 statements, 988 ms at 1000. The cost is not ranking and it is not the embedding
model: a read that had to encode claims writes those vectors back to the L2 cache, so
a warm corpus embeds only the question. The cost is the authoritative projection,
which loads the corpus's version graph to decide what is true, what changed and what
conflicts before relevance narrows anything. That work is the guarantee, not overhead
to be trimmed.

Before the cache write-back, a corpus authored through `remember` — which
deliberately does not embed — re-encoded itself on every read: 3.2 s per recall at
200 statements. The encoder had already computed those vectors; throwing them away
was the defect.

For the corpora this product is aimed at — a team's decisions, a service's
operational facts, an agent's working memory — the 200-statement row is the relevant
one, and every operation there is under 110 ms at p95. At 1000 statements recall is
around 1 s, usable interactively and slow enough that a larger corpus wants the
`context()` surface or a snapshot. The corpus is memory, not a log; if yours is
measured in millions of rows, this is not the tool yet, and the number is stated so
you can decide that yourself.

## What these numbers do not mean

They are not a latency guarantee. One machine, one corpus shape, no concurrent load,
mains power. Corpus shape matters more than corpus size: the probe's statements are
short, single-claim documents, the cheapest possible shape. Hardware matters.
Nothing here has been measured under concurrency, and no claim is made about
throughput.

Every latency figure in this repository's history before v0.9.0 was taken on battery,
where the same probe is 3 to 4× slower. Compare within a condition, not across them.

The retrieval benchmarks under [evaluation](../evaluation/) measure semantics and
ranking quality. They do not measure latency, and this document does not measure
quality. They are different claims.
