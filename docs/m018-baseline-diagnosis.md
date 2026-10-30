# M018 baseline diagnosis

Source: `scripts/diagnose_baseline.py` (dev set only; not frozen). Run against
`main` product code (migration 015, trap fixes NOT present) on a fresh
`mindpalace` database. The sealed_spec.json and METRICS.md are byte-identical to
the committed versions; no definition changed.

## Finding 1 — D scored exactly 0.0 on every metric (CAUSE: fixture defect)

Every brand: `ingested_docs=N total_claims=0`. All 14/11/11 files carry a
`frontmatter_missing_title` warning because the fixture generator writes
frontmatter without a `title` field. `title` is required by
`api/services/parser.py` (`title: str = Field(..., min_length=1)`).

Ingestion skips the structural prose extractor whenever
`frontmatter_warnings` is non-empty:

```python
if not claims and "claims" not in metadata and not frontmatter_warnings:
    derived, extraction_warnings = markdown_claims.claims_for(...)
```

Result: **zero claims archived**, so `build_context` resolves
`no_relevant_memory` and returns an empty context for every prompt. D's zero
scores are therefore an artefact of the fixtures, not of the product. Chunks
are still created and embedded (55/52/52 chunks, all with embeddings), so C
and C+ retrieve fine; only D is affected.

Fix: add `title` to every fixture's frontmatter (fixture_generator.py), then
re-ingest and confirm claims > 0.

## Finding 2 — C and C+ identical to 4 decimals (NOT a defect; expected)

C+ filters by document-level frontmatter status only
(`draft`/`rejected`/`superseded`). Section-level states (`## Brainstorming`,
`## Superseded` inside an approved document) are deliberately invisible to C+
per the sealed spec. All fixture documents except `september_brainstorm.md`
have frontmatter `status: approved`, so C+ removes only the single draft
document's chunk (voltedge: chunks_kept=51, removed=1). That removed draft
chunk is low-relevance for VE-04 and not in the top-10, so C and C+ contexts
coincide. This matches the spec: C+ is a document-level metadata filter, and
D's structural advantage is excluding section-level stale material. No fix
needed; report it as expected.

## Finding 3 — B/B+ score 1.0 trivially (CAUSE: fixture ordering defect)

For every brand, 0/N gold items sit beyond the first 1200 words of the B
concatenation (northstar 0/15, voltedge 0/10, hearth_and_loom 0/10). The
6000-token `volume_guide.md` filler is concatenated LAST (alphabetical sort
puts `volume_guide.md` after the gold docs), so all gold items fall within the
first ~920 words and B truncates at 1200 words capturing everything.

Fix: regenerate/reorder fixtures so the 6000-token filler leads the
concatenation and ≥70% of gold items sit beyond the first 1200 words in the
fixed order (e.g. filler-first ordering in evaluate.py, and/or rename the
filler doc to sort first).

## Finding 4 — voltedge rule_recall pattern (NOT a brand-key mismatch)

Directory and spec brand key are both `voltedge` (no spelling mismatch). The
apparent anomaly (voltedg C=0.0 in the old baseline) is explained by Finding 1:
D is empty for ALL brands (0 claims), so voltedge D=0.0 like everything else.
VE-04 C actually retrieves prohibited-phrase chunks (diagnosis top-10 shows
"curated", "clinically proven cure", "miracle recovery"); the old baseline's
0.0 was swallowed-error noise. No brand-key fix needed.

## Finding 5 — Budget accounting mismatch (harness defect)

The harness counts tokens with `len(text.split())` (whitespace words) but D
enforces its own budget with `CHARS_PER_TOKEN = 4` (chars//4). Conditions are
not compared at an equal, named budget. Fix: use ONE token-counting function
for every condition, post-truncate D to that budget, report D's pre-truncation
size, and name the tokenizer. Whitespace words is acceptable offline (no
tiktoken guaranteed); document the choice.

## Finding 6 — dev and sealed fixtures are the same files (harness defect)

`main()` calls `write_fixtures(spec)` which regenerates `dev_fixtures/` with
the sealed seed on every run, so dev fixtures == sealed fixtures. The work
order requires them to differ (dev seed + filler variation vs fixed sealed
seed). Fix: generate `dev_fixtures/` with a dev seed and `sealed_fixtures/`
with the sealed seed; the sealed run consumes `sealed_fixtures/`.

## Finding 7 — No per-template results (harness defect)

The previous `results_baseline.json` reported per-prompt/per-condition but not
per-template. Fix: tag each fixture doc with its template (T1/T2/T3) and report
per-template + per-brand tables.

## Additional — swallowed exceptions (harness defect)

`run_brand` wraps retrieval and D in `try/except: ... = [] / "[D ERROR...]"`.
This hides failures and produced silent empty contexts. Fix: remove blanket
`except` blocks; fail loudly on ingestion, retrieval, or D errors.

## Additional — `withheld_counts` absent on main (expected)

`ContextPack.withheld_counts` exists only on `m018/extraction-state`, not on
`main`. The baseline on main correctly has no withheld_counts field; report
that as a baseline limitation, not an error.


## Run A1 findings — benchmark evidence before changes (dev set)

Source: `scripts/a1_diagnostic.py`, run against dev_fixtures on `main` (migration 015).
No product code changed; harness-only branch.

### Chunk census (per brand, current harness ingests ONE version per file)

| Brand | Total chunks | T1 | T2 | T3 | prohibition | spec | voice | volume | Draft chunks | v1 versions |
|---|---|---|---|---|---|---|---|---|---|---|
| northstar | 55 | 5 | 1 | 3 | 5 | 2 | 2 | 37 | 0 | none |
| voltedge | 52 | 5 | 1 | 3 | 3 | 2 | 1 | 37 | 0 | none |
| hearth_and_loom | ~52 | 5 | 1 | 3 | 3 | 2 | 1 | 37 | 0 | none |

- ONE version per file ingested (current on-disk version only); **NO separate v1 versions indexed**.
- Draft file `september_brainstorm.md` IS indexed as chunks.
- `documents.status` column does NOT reflect frontmatter draft status (shows 0 draft chunks); C+ filter correctly maps `source_path`→frontmatter status from fixture meta.

### C vs C+ per prompt

- **rule_recall IDENTICAL for C and C+ on every prompt, every brand.** C+ never improves rule recall.
- C+ removes a chunk only when a draft document's chunk is in the top-k (e.g. voltedge VE-01/02/04/06/07, removed=1 each), reducing `violation` 1→0 on those prompts. Northstar and hearth_and_loom: removed=0 everywhere.
- Superseded markers live in `t1_headings.md` (document status `approved`); section-level state is invisible to C+ by spec design, so superseded content passes through C+ unchanged.
- **Verdict:** C and C+ are not discriminative on rule_recall; to make the versioning advantage testable, T2 must ingest a v1 (superseded) version then a v2 (approved) version so C+ filters superseded v1 chunks.

### B / B+ per-ordering (gold word positions vs 1200-word cut)

- **hearth_and_loom:** fixed 6/10 beyond 1200; **reverse 0/10 beyond** (all gold within first 1200 words); shuffle1 6/10; shuffle2 4/10; shuffle3 5/10.
- **voltedg:** gold at word positions 7–992 in every ordering (all within first 1200) → B rule_recall = 1.0 for voltedge under every ordering.
- **northstar:** (crashed before printing; gold similarly concentrated — see Finding 1 below.)
- **Verdict:** B rule_recall varies wildly by ordering and brand because gold is not spread. B is not a fair static baseline.

### B+ violation vs B violation
B+ violation (0.38) > B violation (0.20) in the old baseline. Cause: B+ excludes superseded documents AND uses `---` separators (more overhead → fewer words fit within 1200), changing which markers fall within the cut. Composition-dependent; will be re-measured after A2 fixes.

### onnxruntime contradiction
`onnxruntime` is NOT installed in this environment; the embedder falls back to the **torch** backend (sentence-transformers MiniLM). The benchmark uses **real embeddings** (not the SHA-256 hash stub), but via the torch backend, not ONNX as some comments claim. Documented honestly.

### Tests at tag v0.9.3 (pre-existing check)
Verified at `v0.9.3` (cb04ccb): `test_the_model_is_loaded_from_the_local_cache` and `test_the_session_is_told_to_optimise_the_graph` fail identically (missing onnxruntime; both directly instantiate/import onnxruntime). `test_m016_runtime_lifecycle` group is pre-existing environmental (subprocess runtime, needs DB). None are regressions from harness changes (harness touches zero product/test files except new ones).

### Fixes required (A2)
1. Spread gold across multiple docs (prohibitions ≥3 docs, voice ≥2, specs ≥2); each doc 300–800 words.
2. Neutral-volume filler: ≥10 docs of 300–800 words, ≥6000 tokens total, neutral names (no `00_`/`zz_`), no gold strings; delete `00_volume_guide.md`.
3. Structural check: under every B ordering, ≤50% of each brand's gold within first 1200 words; assert in harness.
4. B orderings: neutral-name alphabetical, reverse, 3 seeded shuffles; report mean/min/max.
5. Add B* (operator-curated static: brand-guide doc only) outside criterion.
6. C and C+ index everything; T2 v1→v2 so C+ filters superseded chunks.
7. Single tokenizer; print max context size per condition; assert ≤ budget.
