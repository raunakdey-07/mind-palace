# Archive

Scripts that produced a result once and have no callers. They are kept because
their numbers are cited, not because anything runs them.

Moved here 2026-10-14 (Session 12). Each had **zero** references outside its own
file — not in `.github/workflows/ci.yml`, not in `tests/`, not in any document.
Running one is `python scripts/archive/<name>.py` with the benchmark
requirements installed.

| Script | Lines | What it measured |
|---|---|---|
| `m0115_scaling.py` | 157 | how recall latency moves with corpus size |
| `m012_narrowing.py` | 290 | whether narrowing the candidate set costs recall |
| `m012_closure.py` | 174 | closure over a memory pack |
| `m0116_claim_cache.py` | 196 | claim-cache hit rate |
| `context_product_demo.py` | 94 | a context window demo |

If a number here needs re-measuring, move the script back rather than copying it.
