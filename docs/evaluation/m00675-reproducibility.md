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
DATABASE_URL=postgresql+psycopg://USER:PASSWORD@localhost:5432/benchmark \
  ./scripts/benchmark/m00675.sh
```

Run the command three times in the same environment and twice after restarting
the PostgreSQL service. Compare `result_sha256` in `eval/m00675/result.json`.
Timing fields are intentionally excluded from the canonical hash.

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

The portable canonical artifact for the corrected release source tree hashes to:

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
