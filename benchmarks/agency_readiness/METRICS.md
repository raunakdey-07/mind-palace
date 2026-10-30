# Metrics (M018)

## Normalization
N(x): Unicode NFKC, casefold, drop apostrophes and quotes, replace every other
character that is not a letter, digit or % with a space, collapse whitespace.

Presence: N(gold) occurs in N(context) as a whole-token sequence (pad both with
spaces and search). A gold item cut mid-sentence by a chunk or budget boundary
is NOT present. Same rule for every condition.

## Gold items
- standing_prohibitions: bare terms/phrases; recalled only if term occurs in a
  context sentence with a prohibition cue (do not, never, avoid, prohibited,
  must not, cannot), OR in a list item whose nearest preceding heading/list-intro
  contains such a cue or prohibited/banned/forbidden. Bare occurrence in marketing
  copy does not count.
- required_voice_descriptors, approved_specs: presence of string.
- superseded_v1_markers, unapproved_draft_markers: presence of string.

## Per-prompt metrics
- rule_recall = fraction of brand standing_prohibitions present.
- voice_recall = fraction of required_voice_descriptors present.
- spec_recall = fraction of approved_specs present (informational/guard).
- violation = 1 if ANY superseded_v1_markers or unapproved_draft_markers string present anywhere.
- violation_rate = mean of violation over the 20 non-history prompts.
- history-allowed prompts: NS-05, VE-03, HL-04, VE-06. Report history_recall.

## Headline: rule_recall, violation_rate. Guards: voice_recall, spec_recall.
Guard worse if mean(D)-mean(C+) < -0.05.

## Statistics
Unit = prompt (24). Wilson 95% intervals for proportions. Paired bootstrap seed
20261028, 10000 resamples, percentile interval, for D minus C+. Also per-brand
rows. Criterion additionally requires D >= C+ on both headline metrics in >=75%
of brands (3 brands => all 3).

## Pre-registered criterion
D_auto beats C+ if paired-bootstrap interval of difference excludes 0 in
favourable direction on BOTH rule_recall and violation_rate, AND D >= C+ on
both headline metrics in >=75% of brands, AND neither guard is worse.
Otherwise release notes say "no measured advantage over a metadata-filtered baseline".
