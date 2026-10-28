# BASELINE_pre_m018 — sealed agency-readiness benchmark (v0.9.3 + migration 015)

Token budget: 1200 (spec). Tokenizer: whitespace (`len(text.split())`). Unit: prompt (24).
History-allowed prompts (excluded from headline violation_rate): NS-05, VE-03, HL-04, VE-06.
D = Mind Palace build_context after real ingestion, running on `main` WITHOUT the Phase 3 trap fixes
(`detect_info_state` body-substring scanner still present; `classify_memory_role` without word boundaries;
no `info_state` read path in the packer). That is the honest baseline: D is v0.9.3 + 015 as shipped.

## Per-condition means (20 non-history prompts)
condition | rule_recall | violation_rate | voice_recall | spec_recall | rule_recall Wilson 95% | violation Wilson 95%
A_no_memory | 0.0000 | 0.0000 | 0.0000 | 0.0250 | [0.0000,0.1611] | [0.0000,0.1611]
B_static_truncated | 1.0000 | 0.3500 | 1.0000 | 1.0000 | [0.8389,1.0000] | [0.1812,0.5671]
B_plus_approved_only | 1.0000 | 0.3500 | 1.0000 | 1.0000 | [0.8389,1.0000] | [0.1812,0.5671]
C_raw_rag | 0.4867 | 0.3500 | 0.6750 | 0.6750 | [0.2882,0.6895] | [0.1812,0.5671]
C_plus_filtered | 0.4867 | 0.3500 | 0.6750 | 0.6750 | [0.2882,0.6895] | [0.1812,0.5671]
D_mind_palace | 0.0000 | 0.0000 | 0.0000 | 0.0000 | [0.0000,0.1611] | [0.0000,0.1611]

## Headline criterion check (pre-registered)
Paired bootstrap, seed 20261028, 10,000 resamples, percentile interval, D minus C+.
- rule_recall D-C+: observed -0.4867, 95% CI [-0.6100,-0.3733] -> excludes 0 in favourable direction: NO (D is worse)
- violation_rate D-C+: observed -0.3500, 95% CI [-0.5500,-0.1500] -> excludes 0 in favourable direction: YES (lower violation)

D returns an **empty context** on every prompt (rule_recall 0.0000) because the baseline code has no
`info_state` read path and the body-substring state scanner misclassifies the drafted/rejected material.
Lower violation_rate for D is therefore an artefact of returning nothing, not of better filtering.

## Per-brand rule_recall / violation (non-history), D vs C+
- northstar C_plus_filtered: rule_recall=0.4857 violation_rate=0.8571 (n=7)
- northstar D_mind_palace: rule_recall=0.0000 violation_rate=0.0000 (n=7)
- voltedge C_plus_filtered: rule_recall=0.3333 violation_rate=0.1667 (n=6)
- voltedge D_mind_palace: rule_recall=0.0000 violation_rate=0.0000 (n=6)
- hearth_and_loom C_plus_filtered: rule_recall=0.6190 violation_rate=0.0000 (n=7)
- hearth_and_loom D_mind_palace: rule_recall=0.0000 violation_rate=0.0000 (n=7)

## Per-prompt table
prompt | brand | task_type | history_allowed | A_rule | B_rule | C_rule | C+_rule | D_rule | A_viol | B_viol | C_viol | C+_viol | D_viol
NS-01 | northstar | creative_generation | False | 0.00 | 1.00 | 1.00 | 0.40 | 0.40 | 0.00 | 0 | 1 | 1 | 1 | 1 | 0
NS-02 | northstar | creative_generation | False | 0.00 | 1.00 | 1.00 | 0.40 | 0.40 | 0.00 | 0 | 1 | 1 | 1 | 1 | 0
NS-03 | northstar | creative_generation | False | 0.00 | 1.00 | 1.00 | 0.40 | 0.40 | 0.00 | 0 | 1 | 1 | 1 | 1 | 0
NS-04 | northstar | creative_generation | False | 0.00 | 1.00 | 1.00 | 0.60 | 0.60 | 0.00 | 0 | 1 | 1 | 1 | 1 | 0
NS-05 | northstar | performance_analysis | True | 0.00 | 1.00 | 1.00 | 0.60 | 0.60 | 0.00 | 0 | 1 | 1 | 1 | 1 | 0
NS-06 | northstar | compliance_review | False | 0.00 | 1.00 | 1.00 | 0.80 | 0.80 | 0.00 | 0 | 1 | 1 | 1 | 1 | 0
NS-07 | northstar | creative_generation | False | 0.00 | 1.00 | 1.00 | 0.20 | 0.20 | 0.00 | 0 | 1 | 1 | 0 | 0 | 0
NS-08 | northstar | creative_generation | False | 0.00 | 1.00 | 1.00 | 0.60 | 0.60 | 0.00 | 0 | 1 | 1 | 1 | 1 | 0
VE-01 | voltedge | creative_generation | False | 0.00 | 1.00 | 1.00 | 0.33 | 0.33 | 0.00 | 0 | 0 | 0 | 0 | 0 | 0
VE-02 | voltedge | creative_generation | False | 0.00 | 1.00 | 1.00 | 0.33 | 0.33 | 0.00 | 0 | 0 | 0 | 0 | 0 | 0
VE-03 | voltedge | performance_analysis | True | 0.00 | 1.00 | 1.00 | 0.00 | 0.00 | 0.00 | 0 | 0 | 0 | 0 | 0 | 0
VE-04 | voltedge | compliance_review | False | 0.00 | 1.00 | 1.00 | 0.67 | 0.67 | 0.00 | 0 | 0 | 0 | 0 | 0 | 0
VE-05 | voltedge | creative_generation | False | 0.00 | 1.00 | 1.00 | 0.33 | 0.33 | 0.00 | 0 | 0 | 0 | 0 | 0 | 0
VE-06 | voltedge | creative_generation | True | 0.00 | 1.00 | 1.00 | 0.33 | 0.33 | 0.00 | 0 | 0 | 0 | 0 | 0 | 0
VE-07 | voltedge | creative_generation | False | 0.00 | 1.00 | 1.00 | 0.00 | 0.00 | 0.00 | 0 | 0 | 0 | 1 | 1 | 0
VE-08 | voltedge | creative_generation | False | 0.00 | 1.00 | 1.00 | 0.33 | 0.33 | 0.00 | 0 | 0 | 0 | 0 | 0 | 0
HL-01 | hearth_and_loom | creative_generation | False | 0.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0.00 | 0 | 0 | 0 | 0 | 0 | 0
HL-02 | hearth_and_loom | creative_generation | False | 0.00 | 1.00 | 1.00 | 0.33 | 0.33 | 0.00 | 0 | 0 | 0 | 0 | 0 | 0
HL-03 | hearth_and_loom | compliance_review | False | 0.00 | 1.00 | 1.00 | 0.33 | 0.33 | 0.00 | 0 | 0 | 0 | 0 | 0 | 0
HL-04 | hearth_and_loom | performance_analysis | True | 0.00 | 1.00 | 1.00 | 0.00 | 0.00 | 0.00 | 0 | 0 | 0 | 0 | 0 | 0
HL-05 | hearth_and_loom | creative_generation | False | 0.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0.00 | 0 | 0 | 0 | 0 | 0 | 0
HL-06 | hearth_and_loom | creative_generation | False | 0.00 | 1.00 | 1.00 | 0.33 | 0.33 | 0.00 | 0 | 0 | 0 | 0 | 0 | 0
HL-07 | hearth_and_loom | creative_generation | False | 0.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0.00 | 0 | 0 | 0 | 0 | 0 | 0
HL-08 | hearth_and_loom | creative_generation | False | 0.00 | 1.00 | 1.00 | 0.33 | 0.33 | 0.00 | 0 | 0 | 0 | 0 | 0 | 0

## Verdict
The pre-registered criterion is **not met**: the rule_recall paired-bootstrap CI excludes 0 in the
unfavourable direction (D is far worse than C+), and D >= C+ holds in 0 of 3 brands. Neither guard is
worse only because D returns empty context. Release notes state: "no measured advantage over a
metadata-filtered baseline."