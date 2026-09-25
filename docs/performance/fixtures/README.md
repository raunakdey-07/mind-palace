# Performance fixtures

Fixture inputs remain in their existing source locations so the benchmark
harness and released evidence keep stable paths:

- `../../../eval/memory_benchmarks.yaml`
- `../../../examples/evaluation/corpus/`
- `../../../migrations/versions/001_initial_schema.py` through `005_multiple_evidence.py`

Do not copy generated database contents into this directory. The M006.75
fixture fingerprint is deliberately bounded to migrations `001` through `005`.
Migration `006_snapshot_membership_seal.py` is a post-release schema follow-up
and is excluded from that frozen research identity.
