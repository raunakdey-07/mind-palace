# M007–M008 Generational Memory Intelligence Program

## Baseline

The released baseline is v0.5.0 / M006.75. Its dataset, policy, result,
source fingerprint, authority semantics, and public contracts are frozen.

Current repository:

```text
baseline version: v0.5.0
baseline commit:  d84f7de
repository HEAD:  669249f
```

M007 research is additive and remains separate from production behavior.

## Dependency graph

```text
M007.1 trustworthy decision data
  ├─ blind review package
  ├─ independent adjudication
  ├─ agreement / disagreement
  ├─ leakage checks
  └─ frozen dataset fingerprint
        ↓
M007.2 deterministic decision engine
        ↓
M007.3 decision provenance and replay
        ↓
M007.4 uncertainty and selective abstention
        ↓
M007.5 failure atlas
        ↓
M007.6 provider boundary
        ↓
M007.7 public benchmark tracks
        ↓
M007.8 scientific/product exit gate
        ↓
M008.1 formal temporal semantics
        ↓
M008.2 temporal benchmark
        ↓
M008.3 snapshot/change intelligence
        ↓
M008.4 conflict lifecycle
        ↓
M008.5 decision/audit trail
        ↓
M008.6 longitudinal reasoning
        ↓
M008.7 adversarial evaluation
        ↓
M008.8 memory state-machine/productization gate
```

## Current implementation state

| Component | Implementation | Tests | Empirical validation | Status |
|---|---|---|---|---|
| M007.1 blind review generation | complete | passing | blocked without reviewers | DATA BLOCKED |
| M007.1 adjudication schema/hashing | complete | passing | blocked without records | DATA BLOCKED |
| M007.2 structured decision types | research foundation | passing | blocked | READY |
| M007.3 decision/audit records | research foundation | passing | blocked | READY |
| M007.4 uncertainty states | representation only | passing | blocked | BLOCKED |
| M007.5 failure atlas | representation only | not yet populated | blocked | BLOCKED |
| M007.6 provider boundary | existing scaffold | passing | no provider result | READY |
| M007.7 benchmark | scaffold only | partial | no M007 result | BLOCKED |
| M008.1 temporal states | research foundation | passing | no benchmark | READY |
| M008.3 snapshot diff | research foundation | passing | no corpus benchmark | READY |
| M008.4 conflict lifecycle | research foundation | passing | no real conflict result | READY |
| M008.5 audit records | research foundation | passing | no real decision replay | READY |

## Existing architecture reused

The program reuses the released PostgreSQL/pgvector archive and its existing
claim, evidence, version, validity, supersession, conflict, snapshot, and
Memory Pack concepts. It does not add another database, graph store, agent
framework, or authoritative model.

## Scientific gates

### M007.1

Pass only with independent records, preserved unknowns, agreement reporting,
leakage checks, and a frozen dataset fingerprint. The current blind review
package has 118 items and zero adjudicated decisions.

### M007.2–M007.7

Pass only after a controlled M006.75 comparison on the same independently
adjudicated cases. A lower aggregate score with a meaningful safety or temporal
trade-off must be reported honestly.

### M007.8

Requires data, engine, provenance, uncertainty, failures, provider boundary,
benchmark, reproducibility, and safety evidence. Implementation alone is
insufficient.

### M008.1–M008.7

Requires formal semantics, version-based benchmark fixtures, real lifecycle
transitions, audit/replay evidence, and adversarial cases. Synthetic claims
alone do not establish temporal reasoning.

### M008.8

Requires a stable public contract, reproducible artifacts, category-level
measurements, documented limitations, and no regression in released safety
invariants.

## Invariants

```text
retrieval != relevance != applicability
relevance != evidence sufficiency
newer != valid
similar != authoritative
retrieved != supported
supported != authoritative
```

Research code may propose observations. Only the existing deterministic memory
layer may establish authoritative state.

## Current blocked condition

M007.1 is ready for independent human adjudication. Two independent reviewer
files are intentionally absent, so agreement, disagreement, adjudication, and
M007/M008 empirical results remain pending:

```text
eval/m007/adjudication/reviewer_A.jsonl
eval/m007/adjudication/reviewer_B.jsonl
```

The next scientific path is strictly:

```text
independent review
→ agreement analysis
→ disagreement adjudication
→ frozen reference dataset
→ same-case M006.75/M007 evaluation
→ M007 exit gate
→ M008 evaluation
```

No M008 productization work is justified before the M007 scientific gate.
