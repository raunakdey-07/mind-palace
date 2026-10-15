# Reviewer Guide: M007.1 Memory Decisions

This guide is for the generated review package only. It does not identify any
correct answer for a particular item.

## What to judge

For each review item, judge the candidate independently against the question.
Record separate judgments for:

- relevance;
- subject compatibility;
- temporal applicability;
- evidence sufficiency;
- authority compatibility;
- conflict status;
- overall decision.

Use the explicit enum values in `schema.json`. Preserve `unknown`, `uncertain`,
and `abstain` whenever the supplied evidence does not establish a conclusion.

## Evidence boundary

Use only the question, candidate claim, source path, source text/evidence
available in the package, stage, and any explicit lifecycle metadata supplied.
If a required document version, timestamp, validity interval, supersession
link, or conflict context is not present, record that fact as unavailable and
use uncertainty rather than guessing.

Do not inspect or use any released evaluation output, retrieval ordering,
model-generated interpretation, selection status, expected answer, or hidden
ground-truth label.

## Temporal semantics

Observation time is not validity time. A later observation does not make an
older claim current, and a newer document does not automatically supersede an
earlier claim. Use `not_temporal` when the question has no temporal constraint.

## Supersession and conflicts

A superseded claim is not current merely because it is similar. A conflict is
not resolved merely because one source is newer. Record unresolved alternatives
and an explicit conflict status. Use the overall decision `abstain` when no
safe, supported conclusion is available.

## Review identity

Use a stable pseudonymous reviewer ID such as `reviewer-01`. Do not include
personal information. A second reviewer may review a subset later; disagreement
must be preserved rather than hidden.

## Field definitions

- `relevance`: whether the candidate addresses the question's information need.
- `subject_compatibility`: whether the candidate refers to the requested entity/topic.
- `temporal_applicability`: whether the claim applies under the supplied temporal scope.
- `evidence_sufficiency`: whether the supplied evidence supports the claim.
- `authority_compatibility`: whether the supplied memory state permits the claim in the requested context.
- `conflict_status`: whether the candidate participates in a recorded or unsupported conflict.
- `overall_decision`: `accept`, `reject`, or `abstain`, supported by the fields above.

## Ambiguous authoritative context

When multiple authoritative records match, retain the ambiguity in your rationale.
Do not choose a record because it is newer, observed later, or appears in a
different stage. If the context does not establish the applicable record, use
`uncertain` or `abstain` where the schema permits it.

## Generic worked example

For a fictional question about a fictional component, a candidate that clearly
matches the component and is directly quoted by supplied evidence may be
marked relevant, subject-compatible, and evidence-sufficient. If the context
does not say whether the candidate was current at the requested time, temporal
applicability remains uncertain. This example does not describe any review item.

## Output

Submit one JSONL file with one record per assigned review ID. Use a stable
pseudonymous reviewer ID and a real ISO-8601 review timestamp. Do not edit the
frozen review package.

Validate a submission with:

```bash
./venvmp/bin/python eval/m007_m008/run_program.py validate-reviewer \
  eval/m007/adjudication/reviewer_A.jsonl
```

Validation checks structure and package coverage only. It does not judge whether
your answer is correct.
