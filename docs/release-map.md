# Milestone and release map

Milestone identifiers describe engineering and evaluation work. Semantic
version tags describe public repository releases. A milestone is not renamed
when it is included in a release; the mapping below is the authoritative
relationship between the two numbering systems.

| Milestone | Scope | Public release |
|---|---|---|
| M004 | Persistent versioned memory foundation | `v0.2.0` |
| M005 | Public REST, Python SDK, MCP, and CLI memory interfaces | `v0.4.0` |
| M006 | Persistent-memory evaluation infrastructure | `v0.5.0` |
| M006.5 | Natural-language memory query and relevance layer | `v0.5.0` |
| M006.75 | Frozen reproducible memory-query benchmark | `v0.5.0` |
| M007 / M007.1 | Independent-adjudication handoff infrastructure | `v0.5.1` |
| M009 | Durable corpus-scoped operational memory feed | `v0.6.0` |
| M010 | Unified authoritative `context()` product surface | `v0.7.0` |
| M011 | Memory Pack portability and rebuildability proof | `v0.7.0` |
| M011.5 | Retrieval architecture experiments and rejected rewrites | `v0.7.0` |
| M012 / M012.3 | Claim-representation cache, cached-query fix, release gate | `v0.7.0` |
| M013 | Retrieval research: measured arms and rejected rewrites | `v0.8.0` |
| M014 / M014.1 | Verifiable Memory: portable proofs, receipt contract, CLI and packaging | `v0.8.0` |
| M014.2 | Memory Receipt, the three-property trust model, historical receipts | `v0.8.0` |
| M015 / M015.1 | Receipts on every public surface, cross-surface equivalence, release | `v0.8.0` |
| M015.2 | The five-minute memory path | `v0.9.0` |
| M016 | Instant recall, explicit model-free mode, real-project dogfooding | `v0.9.0` |
| M016.1 | Final limitation audit: reproducible CI, a smaller wheel, Python 3.15 evaluated | `v0.9.1` |

### The trust boundary, stated here

Verification establishes integrity and recorded provenance. It does not
establish that the original source was factually correct, and it does not
establish authenticity unless a digest pinned out of band is supplied. This is
repeated in [the changelog](../CHANGELOG.md), in the README, and on every verdict
Mind Palace prints, because it is the easiest thing for a release to overclaim.

### Why M015.2 and M016 are one release

They are one milestone for a product, and were
consolidated into a single public release rather than shipped as `v0.9.0` and
`v0.10.0`.

```
        easy to start
              +
        fast repeated reads
              +
        inspectable and verifiable output
              +
        real project dogfooding
              =
        one thing a developer decides to keep
```

Splitting them would have put the *fast* half in a release without the *usable*
half — a runtime that makes 6 s commands fast is not interesting if the first
command still needs a corpus flag. One release means one install, one tag, and one
version whose changelog tells a single story. No earlier release was changed, moved
or re-tagged.

### Why the five-minute path is M015.2 and not M016

It was developed under the working name "M015", which is already a released
identifier in this table — it shipped as `v0.8.0` and is immutable. Rather than
leave two different milestones called M015, the unreleased five-minute work follows
the existing `M015` / `M015.1` pattern for the same release line and is recorded as
`M015.2`.

## Current release

**The latest tagged public release is `v0.9.0`** (`git ls-remote --tags origin` stops
there; the newest tag in the repository is `v0.9.0` at `b0e516e`).

`v0.9.1` is **prepared but not released**. `pyproject.toml` says `0.9.1` and
`CHANGELOG.md` has a `[v0.9.1]` entry, but no `v0.9.1` tag exists and nothing has
been published under that version. Do not `pip install mindpalace==0.9.1`; the
installable release is `v0.9.0`.

`v0.9.0` is therefore the release in which a developer can adopt Mind Palace —
seven commands that take a statement and a question, answers that arrive fast, outputs
that can be inspected and proved afterwards, and a decision log that works on a real
project. `v0.9.1` is what is meant to make that claim checkable from a clean
machine, and the unreleased work on top of it is a security change: authorization
implemented and enforced for shared deployments, row-level security on every
tenant-owned table, corpus erasure, and a tenant-isolation suite that runs against a
least-privileged database role.

`v0.9.1` is a patch on `v0.9.0`: no memory semantics, no retrieval behaviour, no
public response field, and no benchmark input changed.

### What v0.9.1 changed

**CI can now prove it.** The `tests` job installed `sentence-transformers` but never
the weights, and ran pytest with `HF_HUB_OFFLINE=1`, so a clean runner had the package
and not the model: 41 tests failed on `OSError: We couldn't connect to ...`, and
`--maxfail=1` reported only the first. A developer machine already had the cache,
which is what hid it. Both models are now provisioned at pinned revisions, verified
through the application's own classes with the network disabled before any test runs,
and cached as an accelerator only. `tests/test_ci_model_provisioning.py` fails if that
provisioning is deleted, weakened, unpinned or duplicated into one job but not the
other.

**The wheel stopped shipping the research tooling.** `scripts/` had been in the
distribution since `v0.6.0`: 54 files, 590 KB of 1,328 KB unpacked, none of them
imported at runtime. The wheel is 425 KB / 129 files → **227 KB / 74**.

**Python 3.15 lazy imports were evaluated and not adopted.** See
[the conclusion](#python-315-lazy-imports-were-evaluated-and-not-adopted) below.

### What v0.9.0 changed

**The five-minute path.** `mindpalace init`, `remember`, `recall`, `explain`,
`history`, `receipt`, `verify`. Default corpus, so no scoping to learn. Idempotent
writes, so a retry after a lost response converges instead of duplicating. Explicit
abstention, so an unfamiliar question returns nothing rather than a guess.

**Instant repeated reads.** A local runtime keeps the embedding model and the database
pool warm, and the CLI uses it without the user choosing to. Measured on mains power,
one command per process:

| | before | after |
|---|---|---|
| `mindpalace recall` | 6.17 s | **0.63 s** |
| `mindpalace explain` | 6.23 s | **0.67 s** |
| `mindpalace receipt` | 6.21 s | 0.62 s |
| SDK `recall`, `runtime=True` | 5.11 s | **0.51 s** |

Measured on the `sentence-transformers` backend, which was the default through
v0.9.1 and is now the `[torch]` extra.

The cause was not retrieval. 89% of a one-shot recall was importing the embedding
stack and loading a model for 61 ms of memory work.

**An explicit model-free mode.** `MIND_PALACE_LEXICAL=1` reads through the lexical
relevance rung that already existed as the no-model degradation: **0.63 s** instead
of **6.17 s**, with `torch`, `transformers` and `sentence_transformers` never
imported at all. The lexical figure is the same whichever backend is installed;
the 6.17 s is not, and on the default ONNX backend a cold `recall` is 1.42 s. It is
documented as a deliberate trade-off, not a substitute — see
[operations](runtime.md#model-free-lexical-mode).

**Verifiable memory, carried forward.** Receipts, offline verification, exact-substring
evidence, temporal state, Memory Packs and cross-surface parity all unchanged.

### Python 3.15 lazy imports were evaluated and not adopted

Measured on CPython **3.15.0rc1** (3.15.0 final was not released at the time of
writing; the repository requires `>=3.11` and that is unchanged).

| | normal | narrow lazy prototype | `-X lazy_imports=all` |
|---|---:|---:|---:|
| `mindpalace --help` | 335 ms | 335 ms | 187 ms |
| `mindpalace init` | 846 ms | 933 ms | **crashes** |
| `runtime status` | 194 ms | 227 ms | **crashes** |
| lexical `recall` | 844 ms | 872 ms | **crashes** |

Three findings, in order of how much they matter:

1. **The semantic cold path cannot benefit, and the reason is structural.** The cost
   is the model stack, measured at **5.61 s** of import on mains power, against a CLI
   import graph of **0.11 s**. The model is therefore ~95% of the non-interpreter
   cost of a cold semantic read, and it is *already deferred* — `sentence_transformers`
   is imported inside `Embedder._get_model()`, not at module load, which is verified by
   inspecting `sys.modules` after constructing an `Embedder`. Lazy imports cannot
   remove a cost that is already lazy, and cannot remove it from the first operation
   that needs a vector. The addressable surface is ~2% of the path that matters.
2. **Global lazy mode is actively harmful.** `-X lazy_imports=all` breaks real
   commands with `AttributeError: type object 'array' has no attribute 'ARRAY'` inside
   SQLAlchemy's PostgreSQL dialect — a partial module initialisation caused by
   deferring third-party imports. Reproduced; the same import succeeds under normal
   mode and under a `__lazy_modules__ = ["sqlalchemy"]` declaration, so the failure
   needs a specific combination rather than naming one module.
3. **The narrow prototype bought nothing.** Declaring `fastapi`, `sqlalchemy`,
   `pydantic`, `httpx` and `uvicorn` lazy left `--help` unchanged and made everything
   else slightly slower.

The experiment succeeded as an experiment. Nothing shipped.

### What v0.9.0 did not change

Memory semantics are untouched. Claim identities, evidence, provenance, temporal
validity, supersession, conflicts and abstention are byte-identical between the
runtime and the in-process path across ordinary, weakly related, absent, temporal,
conflict and multi-topic queries — `tests/test_m016_runtime_equivalence.py`.

Memory Pack stays at version 1. Receipt stays at version 1. No migration.

No benchmark question, policy, evaluator semantic, tokenizer or retrieval threshold
was touched by this release, and that is measured rather than asserted: re-running the
frozen held-out suite under both source trees gives **60 of 60 identical canonical
per-question decisions** between `v0.8.0` and `v0.9.0`.

- **M006.75 frozen held-out benchmark** — 60 scenarios, **45/60 (75.0%)** exact,
  55/60 abstention-exact, 0 execution failures, and **all seven safety invariants at
  60/60**. The safety metrics are 1.000; the retrieval accuracy is 45/60. Those are
  different things and this release reports them separately.
- **M013 held-out v1** — 157 independently authored questions, **131/157 (83.4%)**.
  A generalisation figure and a research limitation, not a product target, and not
  superseded by the frozen benchmark above.

One discrepancy is reported rather than fixed: the frozen artifact records 47/60,
which the current source no longer reproduces and did not already reproduce at
`v0.8.0`. Its frozen inputs verify unchanged, and it was deliberately not
regenerated. See
[the reproducibility report](evaluation/m00675-reproducibility.md).

One retrieval behaviour changed in this release, and it remains the only retrieval
change since M013: the acceptance gate's overlap test runs against full claim terms
instead of terms reduced by the set shared with every other candidate. Shared terms
cannot discriminate between candidates, so ranking still uses the reduced set; but
they are not evidence that a question is off-topic.

Upgrade from `v0.8.0` is additive. The MCP tool `memory_query` is kept alongside
`memory_recall`; both call the same service and return the same object.

## Numbering rules

- Use `M###` or `M###.#` for development milestones and evaluation reports.
- Use semantic versioning (`vMAJOR.MINOR.PATCH`) for public releases.
- Record the public release in each milestone document instead of renaming
  historical milestone identifiers.
- A milestone marked **Unreleased** must not be described as stable product
  functionality.
