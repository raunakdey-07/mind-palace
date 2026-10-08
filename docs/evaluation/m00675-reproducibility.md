# M006.75 Reproducibility

Public release: `v0.5.0`

## Integrity finding

The earlier discrepancy came from selecting the wrong policy suite. The
repository contains:

* `eval/results/m00675-development-final.json`: development `n=64`, `exact=47`
* `eval/results/m00675-heldout.json`, policy `A`: held-out `n=60`, `exact=30`
* `eval/results/m00675-heldout.json`, policy `frozen`: held-out `n=60`,
  `exact=47`

The 30-versus-47 discrepancy is an **EVALUATOR/POLICY SUITE SELECTION** error:
the latest report read diagnostic policy `A` rows, while the 47 result reads
frozen-policy rows. A second, narrower historical discrepancy is
**SOURCE_REVISION**: the stored raw result has different
`memory_relevance.py` and `memory_query_benchmark.py` hashes from the current
working tree. Two fresh current-source runs agree on the per-question result,
so the old raw artifact is historical evidence, not the current canonical
artifact. The held-out files themselves retain the same frozen dataset hash:
`77e05babeeb0dd62d08a03865728d8ffffe50c20b57066566ab865c2dc37757a`.

The current-source frozen-policy rerun also preserved all recorded safety
invariants:

| Invariant | Result |
|---|---:|
| provenance | 60/60 |
| budget | 60/60 |
| conflict closure | 60/60 |
| current authority | 60/60 |
| temporal scope | 60/60 |
| status | 60/60 |
| false-current authority claims | 0 |
| execution failures | 0 |

## Frozen inputs

The held-out questions and policy are hash-checked by the evaluator and by
`scripts/benchmark/m00675.py`. Source hashes for the evaluator and authority
pipeline are included in the canonical result artifact. The embedding model is
the local cached `sentence-transformers/all-MiniLM-L6-v2` artifact and offline
mode is required.

The benchmark uses the existing rollback-only PostgreSQL schema mechanism. It
does not trust pre-existing application rows, but it still requires a
dedicated PostgreSQL URL so that benchmark execution cannot silently fall back
to a developer database.

## Reproduction

```bash
pytest --cache-clear tests/test_memory_heldout_labels.py
./scripts/benchmark/m00675.sh --verify
DATABASE_URL=postgresql+psycopg://USER:PASSWORD@localhost:5432/benchmark \
  ./scripts/benchmark/m00675.sh
```

`--verify` is read-only. It checks the published artifact without overwriting it.
Use the database-backed command only when you intend to create a new result.

Run the command three times in the same environment and twice after restarting
the PostgreSQL service. Compare `result_sha256` in `eval/m00675/result.json`.
Timing fields are intentionally excluded from the canonical hash.

## Artifact drift measured at `v0.9.0` **[v0.9.0]**

The 47/60 figure above is what `eval/m00675/result.json` records. Re-measuring at
`v0.9.0` shows it no longer reproduces, and the drift **predates this release**.

Measured on a dedicated PostgreSQL database, frozen policy, the artifact's own
canonicalisation (so the comparison uses exactly the fields the release gate
hashes, not a set chosen after the fact):

| source tree | exact | abstention | execution failures | all 7 safety invariants |
|---|---:|---:|---:|---|
| `eval/m00675/result.json` (recorded) | 47/60 | 55/60 | 0 | pass |
| `v0.8.0` (`34b3d2c`) | **45/60** | 55/60 | 0 | pass |
| `v0.9.0` (working tree) | **45/60** | 55/60 | 0 | pass |

Two separate facts, and the difference between them is the point:

1. **`v0.9.0` changed nothing.** All 60 canonical per-question decisions are
   identical between `v0.8.0` and `v0.9.0`. No benchmark question, policy,
   evaluator semantic, tokenizer or retrieval threshold was touched by this
   release, and that is now measured rather than asserted.
2. **The recorded artifact drifted 4 questions before this release.** The four are
   `heldout-history-paired-cutovers`, `heldout-history-ledger-continuity`,
   `heldout-current-cache-pressure-isolation` and `heldout-current-artifact-authority`.
   In each the expected subject is still recalled (`missing: []`, recall 1.0) and the
   failure is *extra* keys admitted alongside it — `security.auth`, and
   `architecture.delivery` + `data.primary`. The safety invariants still pass at
   60/60; what moved is relevance precision.

The artifact's recorded `source_fingerprint` (`b8e2bc82…`) matches **no commit** in
the last 40, so `m00675.sh --verify` has been failing its source check before this
release too. The frozen *inputs* are intact: the dataset hash
(`77e05bab…`) and the policy hash both verify unchanged, which is the check that
actually protects benchmark semantics.

**Deliberately not done here.** The artifact was not regenerated and the 47/60 was
not overwritten: a re-run is a new research result, and publishing one as if it were
the frozen `M006.75` number would misreport a research result as a product gate. The
drift is recorded here instead, with the reproduction above.

**What it most likely is.** The acceptance gate changed in this release line to test
overlap against full claim terms rather than terms reduced by the set shared with
every candidate, which admits more candidates. `docs/STATUS.md` records the same
change as holding the frozen *memory* benchmark at 1.000; that is a different
benchmark, and it does not contradict this one. The extras here are the F2 symptom
("answered from an unrelated key") reappearing in a different shape — more keys
rather than one wrong key. Whether that trade is worth 2 questions on the held-out
set is a research question, and it is **not** settled here.

## Quality result

The currently supported current-source frozen-policy held-out result is:

```text
exact/full: 47/60
current: 6/10
historical: 5/10
temporal: 10/10
multi-topic: 9/10
conflict/provenance: 7/10
abstention: 10/10
abstention checks: 55/60
provenance: 60/60
conflict closure: 60/60
budget: 60/60
false-current promotions: 0
execution failures: 0
```

The canonical artifact for the corrected release source tree hashes to:

```text
881530c7c6e7ba29fb38eb5b27609071463f733c6a8cd173aeff04173a5d8dc8
```

The bounded Memory Pack implementation was corrected after full-suite
validation. The correction changes the source fingerprint and therefore the
canonical artifact hash, but repeated runs produced identical per-question
decisions, category metrics, and safety results. The earlier
`c1d4126bf0d89ff472b7f049dc1f41987930d106333dede8990bb29e28518819` artifact
must not be used for the corrected source tree. The publication artifact
includes verified model and dependency fingerprints while excluding
machine-specific platform metadata.

The 47/64 development result must not be substituted for the held-out result.
No retrieval tuning or Jev comparison is authorized by this finding.
