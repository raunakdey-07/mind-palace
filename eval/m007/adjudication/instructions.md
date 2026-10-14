# M007.1 Blind Reviewer Instructions

Review one question/candidate pair at a time using only the question, candidate
claim, source document/version, evidence quote, validity interval, claim key,
supersession relationship, and conflict context.

## Allowed evidence

- the question's information need
- the candidate claim and authored key
- the archived source document and version
- the quoted evidence
- explicit validity and lifecycle metadata
- explicit conflict and supersession metadata

## Forbidden evidence

Do not inspect or use:

- released benchmark outputs
- current system selection or rejection
- model-generated numerical features
- lexical feature value
- selection status
- any model decision

Judge each dimension independently:

1. relevance: relevant, irrelevant, or uncertain;
2. subject compatibility: compatible, incompatible, or uncertain;
3. temporal applicability: applicable, not_applicable, uncertain, or not_temporal;
4. evidence sufficiency: sufficient, insufficient, uncertain, or conflicting;
5. authority compatibility: current, historical, superseded, conflicted, deleted,
   restored, uncertain, or not_applicable;
6. overall decision: accept, reject, or abstain.

The overall decision must be justified by the individual dimensions in the
rationale. Do not resolve ambiguity by choosing a label. Use `uncertain` or
`abstain` when the evidence does not establish a decision.

Use pseudonymous reviewer IDs and do not include personal information. A second
reviewer may independently review a subset later; do not simulate agreement.
