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
