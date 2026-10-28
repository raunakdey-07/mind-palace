# M018 sealed spec audit

Source: `benchmarks/agency_readiness/sealed_spec.json` (version 1.0.0).
Authored by another model (Gemini), supplied by the repo owner, copied from
`/tmp` by an earlier agent run. No human has reviewed it. It is independent of
this implementing agent but NOT independent of the Stage B design: its traps
mirror the features being built. Report here; fix nothing.

## 1. Gold strings per brand, by category

### northstar
standing_prohibitions:
- curated
- carbon-neutral
- 100% eco-friendly
- revolutionary
- guilt-free

superseded_v1_markers:
- enthusiastic, playful tone
- 65% recycled polyester
- Instagram is the primary channel

unapproved_draft_markers:
- try a playful tone again
- 50% flash sale countdown
- TikTok meme giveaway

required_voice_descriptors:
- warm, a little dry, and confident
- plain language

approved_specs:
- 80% GRS-certified recycled polyester
- first-time buyers aged 20-30

### voltedge
standing_prohibitions:
- effortless
- miracle recovery
- clinically proven cure

superseded_v1_markers:
- casual weekend jogger tone
- 12mm heel drop

unapproved_draft_markers:
- crypto athlete sponsorship
- overnight muscle gain

required_voice_descriptors:
- high-cadence, kinetic, and precise

approved_specs:
- 8mm heel drop
- Pebax supercritical foam

### hearth_and_loom
standing_prohibitions:
- hotel-grade luxury
- stain-proof forever
- fast-fashion home

superseded_v1_markers:
- machine-washable on hot
- synthetic microfiber blend

unapproved_draft_markers:
- influencer unboxing blitz
- BOGO clearance weekend

required_voice_descriptors:
- tactile, unhurried, and rooted in craft

approved_specs:
- 100% long-staple Belgian flax
- cold gentle wash only

## 2. Leakage analysis

Substring overlap between superseded/draft markers and approved/prohibition/descriptor/spec strings:

- voltedge `clinical...` in `clinically proven cure` is not a marker; markers are standalone phrases. No marker appears to be a substring of another gold string within a brand.
- northstar `curated` is a substring of nothing else in northstar gold.
- hearth `stain-proof forever`: `stain-proof` appears nowhere else in gold.

Short markers (< 8 characters, normalized):
- voltedge `effortless` (9 chars) — ok
- northstar `curated` (7 chars) — below 8, common word, matches by chance risk
- northstar `revolutionary` — ok
- All other markers are > 8 chars.

Risk: `curated` (7 chars) will match marketing copy by chance if used standalone.
`miracle recovery` (voltedge prohibition) — `miracle` alone is common.

Cross-category leakage within a brand: none found where a draft/superseded marker is a substring of an approved string.

## 3. Fixture template coverage per required trap

Not yet populated — fixture generator (Step 4) not yet built. Template mapping to be added:
- T1 state by headings (vocabulary + non-vocabulary)
- T2 state by frontmatter status/supersedes; v1/v2 same path
- T3 mixed bullets + prose + table, hard-wrapped 72, docx or pdf

Trap coverage (to be confirmed after generator):
- approved_prohibition_do_not_use_curated
- approved_prose_we_draft_emails_in_plain_language
- descriptive_mustard_yellow_packaging
- v2_top_of_list_rule_insertion
- hard_wrapped_prose_paragraphs
- mixed_bullet_prose_table_document
- descriptive_voice_no_modal_verb_warm_dry_confident
- unapproved_september_brainstorm_draft

## 4. Prompt distribution

Per brand: 8 prompts each (northstar NS-01..NS-08, voltedge VE-01..VE-08, hearth_and_loom HL-01..HL-08). Total 24.

Per task_type (count across all brands):
- creative_generation: 16 (NS-01,02,03,04,07,08; VE-01,02,05,06,07,08; HL-01,02,05,06,07,08)
- performance_analysis: 4 (NS-05; VE-03; HL-04) [history-allowed]
- compliance_review: 4 (NS-06; VE-04; HL-03)

## 5. Suspicious findings

- NS-01 contains the approved spec string "first-time buyers aged 20-30" verbatim in the prompt itself: "targeting first-time buyers aged 20-30." This means NS-01's spec_recall is trivially 1.0 from the prompt alone for every condition that echoes the prompt (all conditions include the prompt). Not a leak of the marker itself, but spec_recall for NS-01 is inflated across all conditions.
- No fixture documents exist yet (Step 4 not done), so cannot verify gold strings appear in fixtures, or that filler contains none of them.
- The 4 history-allowed prompts are `performance_analysis` plus VE-06 (creative_generation). VE-06 asks about "legacy model" heel drop — surfacing `12mm heel drop` (superseded) there is correct behaviour.
- Duplicate prompts: none detected (all 24 prompt texts are distinct).
- METRICS.md defines `violation_rate` over 20 non-history prompts; 4 history-allowed prompts excluded from headline violation. This must be clearly stated in results.

## Decisions to flag for the repo owner

- The spec defines no metric definitions; METRICS.md supplies them.
- `curated` is 7 chars and will match by chance; consider dropping or requiring cue context.
- NS-01 spec string appears in the prompt; spec_recall informational only, excluded from headline criterion.
