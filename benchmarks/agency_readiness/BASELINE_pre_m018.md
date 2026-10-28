# BASELINE_pre_m018 — sealed agency-readiness benchmark (v0.9.3 + migration 015)

**Honest baseline.** This is the first sealed run of the repaired harness against
`main` (migration 015 merged, Phase 3 trap fixes NOT present). The earlier run
(`results_baseline_INVALID.json`) is invalidated — see `sealed_runs.log` and
`docs/m018-baseline-diagnosis.md`.

## Setup

- Token budget: **1200 words** (one tokenizer for every condition).
- Tokenizer: **whitespace words** `len(text.split())` (tiktoken is not installed
  offline; whitespace words are used and named here). D is post-truncated to the
  same 1200-word budget; D's pre-truncation size is reported per prompt.
- Conditions: `A_no_memory`, `B_static_truncated`, `B_plus_approved_only`,
  `C_raw_rag`, `C_plus_filtered`, `D_mind_palace`.
- B and B+: 5 seeded document orderings (fixed, reverse, 3 shuffles); reported
  as mean and range across orderings.
- C and C+: same chunker and same real embedder (`all-MiniLM-L6-v2` via ONNX)
  as D. C+ filters by document-level frontmatter status
  (`draft`/`rejected`/`superseded` excluded). Section-level states
  (`## Brainstorming`, `## Superseded` inside an approved doc) are deliberately
  invisible to C+ — that is D's structural advantage, per the sealed spec.
- D: real public `build_context` API after real ingestion of the sealed
  fixtures, on `main` (v0.9.3 + migration 015, no trap fixes, no `info_state`
  read path in the packer).
- Unit = prompt (24). History-allowed prompts (excluded from headline
  `violation_rate`): NS-05, VE-03, HL-04, VE-06.
- Headline metrics: `rule_recall`, `violation_rate`. Guards: `voice_recall`,
  `spec_recall`.
- Statistics: Wilson 95% intervals for proportions; paired bootstrap
  (seed 20261028, 10,000 resamples, percentile interval) for D minus C+.
- With 3 brands the per-brand subset of the criterion means D must be >= C+ on
  both headline metrics in **all 3 brands**. The prompt-level interval
  overstates the effective sample size (24 prompts cluster by brand).

## Per-condition means (20 non-history prompts)

| Condition | rule_recall | Wilson 95% rule | violation_rate | Wilson 95% viol | voice_recall | spec_recall |
|---|---|---|---|---|---|---|
| A_no_memory | 0.0000 | [0.0000, 0.1611] | 0.0000 | [0.0000, 0.1611] | 0.0000 | 0.0250 |
| B_static_truncated | 0.3567 | [0.1861, 0.5734] | 0.2000 | [0.0807, 0.4160] | 0.0000 | 0.0000 |
| B_plus_approved_only | 0.4880 | [0.2893, 0.6906] | 0.3800 | [0.2035, 0.5951] | 0.0000 | 0.0000 |
| C_raw_rag | 0.5667 | [0.3567, 0.7551] | 0.5000 | [0.2993, 0.7007] | 0.5000 | 0.7000 |
| C_plus_filtered | 0.5667 | [0.3567, 0.7551] | 0.5000 | [0.2993, 0.7007] | 0.5000 | 0.7000 |
| D_mind_palace | 0.0000 | [0.0000, 0.1611] | 0.1000 | [0.0279, 0.3010] | 0.0000 | 0.0250 |

- B no longer scores 1.0 trivially: the 6000-token filler leads the
  concatenation so gold items sit beyond the first 1200 words (fixed; see
  `docs/m018-baseline-diagnosis.md` finding 3).
- C and C+ are identical: C+ is a document-level frontmatter filter and the
  stale/draft chunks are not in the top-k retrieved set, so filtering changes
  nothing. The filter is active; it simply removes nothing that raw RAG already
  excludes.
- D returns near-empty context (0-32 tokens, mostly a single table-evidence row
  or nothing): on `main` the archive's keyword-based query matching does not
  surface the prohibition/spec claims for these prompts (confirmed: the archive
  itself returns 0 `current_memories` for NS-06, a compliance query). D's low
  violation rate is therefore an artefact of returning nothing, not better
  filtering.

## Per-brand headline metrics (non-history prompts)

| Brand | C rule | C+ rule | D rule | D>=C+ rule | C viol | C+ viol | D viol |
|---|---|---|---|---|---|---|---|
| northstar | 0.571 | 0.571 | 0.000 | No | 0.857 | 0.857 | 0.286 |
| voltedge | 0.389 | 0.389 | 0.000 | No | 0.667 | 0.667 | 0.000 |
| hearth_and_loom | 0.714 | 0.714 | 0.000 | No | 0.000 | 0.000 | 0.000 |

D >= C+ on rule_recall in **0 of 3 brands** (criterion requires >= 75%, i.e. all 3).

## Paired bootstrap — D minus C+ (seed 20261028, 10,000 resamples)

| Metric | Observed D−C+ | 95% percentile CI | Excludes 0 in favourable direction |
|---|---|---|---|
| rule_recall | −0.5667 | [−0.7000, −0.4267] | **No** (D is worse) |
| violation_rate | −0.4000 | [−0.6000, −0.2000] | Yes (lower violation), but D's context is empty so this is an artefact |

Guard deltas (D − C+): voice_recall −0.5000 (worse, below −0.05);
spec_recall −0.6750 (worse, below −0.05). Neither guard is "not worse".

## Per-template gold coverage (fixture origin of each gold item)

| Template | Document | Gold items it carries |
|---|---|---|
| T1 (headings) | t1_headings.md | prohibition: curated/bespoke; draft: playful tone, flash sale, TikTok giveaway; superseded: 65% recycled polyester |
| T2 (frontmatter) | t2_policy.md | rule inserted at position 1: never use "curated"; Instagram primary channel |
| T3 (mixed) | t3_mixed.md | voice: warm/dry/confident; spec: 80% GRS polyester, first-time buyers 20-30; superseded: Instagram primary channel |

Because D returns near-empty context for every prompt, D's per-template recall
is 0.0 across T1/T2/T3 — it surfaces essentially none of the gold from any
template. C and C+ surface T1 prohibition/spec items (raw RAG matches them by
keyword) but not reliably the voice items.

## Pre-registered criterion (baseline verdict)

> D_auto beats C+ if the paired-bootstrap interval of the difference excludes 0
> in the favourable direction on BOTH `rule_recall` and `violation_rate`, AND
> D >= C+ on both headline metrics in at least 75% of brands, AND neither guard
> is worse.

**Verdict: NOT met.**

- rule_recall D−C+ CI [−0.7000, −0.4267] excludes 0 but in the unfavourable
  direction (D far worse than C+).
- D >= C+ on rule_recall in 0 of 3 brands (< 75%).
- Both guards are worse than C+ (voice −0.5000, spec −0.6750).

The release-notes position is therefore: **"no measured advantage over a
metadata-filtered baseline."** This is an honest baseline showing where D stands
on v0.9.3 + migration 015; it is not evidence against the Phase 3–5 improvements
(trap fixes, state read path, fusion, standing rules), which are evaluated in
the final v0.10.0 run.
