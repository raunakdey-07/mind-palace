# M006.75 Frozen Held-out Benchmark

This benchmark measures the frozen natural-language memory query policy over
the 60-question held-out set. It is not a general RAG, factuality, agent
memory, or general intelligence benchmark.

The authoritative frozen inputs are:

* `eval/memory_questions_heldout.yaml`
* `eval/memory_query_policy.json`
* the source hashes recorded in `result.json`

Run from a dedicated PostgreSQL database:

```bash
DATABASE_URL=postgresql+psycopg://USER:PASSWORD@localhost:5432/benchmark \
  ./scripts/benchmark/m00675.sh
```

The command fails closed when `DATABASE_URL` is absent, validates both frozen
input hashes, source files, dependencies, and the local model snapshot, runs
the existing evaluator, and writes a canonical result that
excludes timing fields from its equality hash. Do not use the normal developer
database as a benchmark fixture.

Verify an existing artifact without a database:

```bash
PYTHON=/path/to/python ./scripts/benchmark/m00675.sh --verify
```

Metrics are defined by the existing evaluator:

* `exact` is exact declared-label correctness.
* `fully_correct` additionally requires abstention, topic, and authored-key
  checks.
* temporal, status, provenance, conflict-closure, authority, and budget
  checks are reported separately.
* no timestamp, duration, process ID, or temporary path contributes to the
  canonical result hash.

The evaluator emits both policy `A` and policy `frozen` rows. This artifact
selects only the frozen policy. Policy `A` is a diagnostic comparison and
scores 30/60. The stored artifact's frozen policy records **47/60**. The bounded
Memory Pack correctness fix changed the source fingerprint but did not change
any frozen-policy decision, category result, or safety invariant. The stored
canonical artifact hash is
`881530c7c6e7ba29fb38eb5b27609071463f733c6a8cd173aeff04173a5d8dc8`. The separate
development artifact reports 47/64 and must not be substituted for the held-out
result. Historical raw artifacts may differ in individual questions when their
source fingerprints do not match the current working tree.

## Recorded artifact versus current-source result **[v0.9.0]**

Two numbers, and they are not interchangeable:

| | exact | abstention | execution failures | all 7 safety invariants |
|---|---:|---:|---:|---|
| `result.json` as recorded (historical) | 47/60 | 55/60 | 0 | pass |
| current source, re-measured | **45/60** | 55/60 | 0 | pass |

The stored figure is what the artifact says. The current-source figure is what
today's tree produces, and it is 45/60 — a drift that predates v0.9.0, in which
four questions recall the expected subject and additionally admit extra keys. The
stored `source_fingerprint` matches no commit, and `--verify` has been failing its
source check since before v0.9.0; the **frozen inputs verify unchanged**, which is
the check that protects benchmark semantics.

`v0.9.0` itself changed nothing here: re-running the suite under both source trees
gives 60 of 60 identical canonical per-question decisions.

The artifact was deliberately **not** regenerated. A re-run is new research, and
publishing one under the frozen M006.75 name would misreport research as a product
gate. Measurement and reproduction:
[docs/evaluation/m00675-reproducibility.md](../../docs/evaluation/m00675-reproducibility.md).
