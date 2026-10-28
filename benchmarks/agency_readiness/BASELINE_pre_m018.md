# BASELINE_pre_m018 — sealed agency-readiness benchmark (repaired harness, fair fixtures)

This is the REPAIRED baseline (final valid sealed run). Previous runs archived as
`results_baseline_INVALID.json`, `results_baseline_INVALID2.json`, `results_baseline_INVALID3.json`.
See `sealed_runs.log` (1 valid entry, 3 invalidated).

Token budget: 1200 words (one tokenizer for every condition).
Tokenizer: whitespace words (`len(text.split())`).
Embedder: {"class": "Embedder", "backend": "torch", "model_name": "all-MiniLM-L6-v2", "dimension": 384} (onnxruntime NOT installed; torch backend; real MiniLM, not SHA-256 stub).
Sanity check: cos("warm friendly tone", "approachable voice")=0.4399 > cos("warm friendly tone", "quarterly tax filing")=-0.0479 by 0.4878 (>=0.2, PASS).
Unit: prompt (24). History-allowed prompts (excluded from headline violation_rate):
NS-05, VE-03, HL-04, VE-06.
Conditions: A (no memory), B (static dump, 5 orderings), B+ (approved-only static, 5 orderings),
B* (brand-guide only), C (raw RAG over all docs), C+ (raw RAG + document-level filter),
D (Mind Palace build_context after real ingestion).
B and B+ report mean/min/max over 5 orderings (neutral-name alphabetical, reverse, 3 seeded shuffles).
Headline metrics: rule_recall, violation_rate. Guards: voice_recall, spec_recall.
Statistics: Wilson 95% intervals for proportions; paired bootstrap (seed 20261028,
10,000 resamples, percentile interval) for D minus C+.

## Per-condition means (20 non-history prompts)
condition | rule_recall | Wilson95 rule | violation_rate | Wilson95 viol | voice_recall | spec_recall
A_no_memory | 0.0000 | [0.0000,0.1611] | 0.0000 | [0.0000,0.1611] | 0.0000 | 0.0250
B_static_truncated | 0.1287 | [0.0414,0.3356] | 0.3400 | [0.1739,0.5577] | 0.0000 | 0.0000
B_plus_approved_only | 0.1427 | [0.0485,0.3520] | 0.2100 | [0.0867,0.4268] | 0.0000 | 0.0000
B_star_brand_guide | 0.0000 | [0.0000,0.1611] | 0.0000 | [0.0000,0.1611] | 0.0000 | 0.0250
C_raw_rag | 0.2033 | [0.0827,0.4196] | 0.6000 | [0.3866,0.7812] | 0.5250 | 0.2500
C_plus_filtered | 0.2367 | [0.1033,0.4549] | 0.2500 | [0.1119,0.4687] | 0.5250 | 0.2750
D_mind_palace | 0.0000 | [0.0000,0.1611] | 0.2000 | [0.0807,0.4160] | 0.0000 | 0.1000

## Paired bootstrap — D minus C+ (seed 20261028, 10,000 resamples, percentile interval)
rule_recall: observed -0.2367, 95% CI [-0.3667,-0.1200] -> excludes 0 in favourable direction: NO
violation_rate: observed -0.0500, 95% CI [-0.2000,0.1000] -> excludes 0 in favourable direction: NO (lower violation is better)

Guard voice_recall D-C+: -0.5250 (worse if < -0.05)
Guard spec_recall D-C+: -0.1750 (worse if < -0.05)

## Per-brand headline metrics (non-history prompts)
brand | C rule | C+ rule | D rule | D>=C+ rule | C viol | C+ viol | D viol
northstar | 0.200 | 0.200 | 0.000 | False | 0.714 | 0.714 | 0.429
voltedg | 0.000 | 0.000 | 0.000 | True | 0.000 | 0.000 | 0.000
hearth_and_loom | 0.381 | 0.381 | 0.000 | False | 0.000 | 0.000 | 0.000

## Pre-registered criterion verdict
> D_auto beats C+ if the paired-bootstrap interval of the difference excludes 0 in
> the favourable direction on BOTH rule_recall and violation_rate, AND D >= C+ on both
> headline metrics in at least 75% of brands, AND neither guard is worse.

- rule_recall D-C+ CI excludes 0 favourable: NO (CI [-0.3667,-0.1200])
- violation_rate D-C+ CI excludes 0 favourable: NO (CI [-0.2000,0.1000])
- D >= C+ on both headlines in >=75% of brands: 1/3 brands -> NO
- Neither guard worse: voice -0.5250, spec -0.1750 -> NO

**Verdict: pre-registered criterion NOT met.**
Release-notes position: "no measured advantage over a metadata-filtered baseline."

Note: D returns empty/near-empty context on this baseline (main product without the Phase 3
trap fixes and state read path), so its low violation_rate is an artefact of returning nothing,
not better filtering. This is an honest baseline showing where D stands on v0.9.3 + migration 015.

## Structural fixture check
Under every B ordering, <=50% of each brand's gold items fall within the first 1200 words.
Structural check passed on this run (no assertion failure).

## Per-template gold coverage
Each fixture document is tagged with a template (T1/T2/T3/prohibition/voice/spec/filler/draft).
Gold items are spread: prohibitions in >=3 docs, voice in >=2 docs, specs in >=2 docs,
each doc 300-800 words, 12 filler docs (>=6000 tokens), neutral names (no 00_/zz_/volume).
C and C+ index all current-version docs (incl. draft/superseded); C+ applies document-level filter.