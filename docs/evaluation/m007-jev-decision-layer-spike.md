# M007 Jev Decision-Layer Research Spike

Public release: Unreleased research

## Hypothesis

An optional structured decision provider may make bounded retrieval routing and
uncertainty explicit, but it must not become an authority over Mind Palace
history. The experiment therefore tests a decision boundary over already
discovered authored candidates, not Jev as a replacement for retrieval or
memory semantics.

## Current pipeline

```text
query
  -> interpretation                 deterministic; validates selectors
  -> candidate discovery/relevance  probabilistic embeddings + deterministic gates
  -> subject/key selection          deterministic key propagation
  -> temporal resolution             authoritative persistence projection
  -> authority/state resolution      authoritative persistence projection
  -> conflict handling               authoritative conflict closure
  -> evidence selection              authored evidence references
  -> Memory Pack                     bounded deterministic projection
```

Relevance is probabilistic/ranking input only. Similarity is not truth,
confidence is not evidence, and ranking is not supersession.

## Decision point selected

The smallest experiment is **bounded candidate routing plus explicit
abstention**. A provider receives query text, opaque candidate IDs, relevance
features, and lexical-overlap features. It may select one candidate or abstain.
It cannot see an authority field or write to the archive.

Subject decomposition, temporal routing, and budget allocation remain outside
the experiment because they would either duplicate the frozen M006.75 policy
or make safety attribution ambiguous.

## Architecture

```text
Application
    |
    v
Mind Palace candidate discovery
    |
    v
DecisionProvider (deterministic offline or optional Jev adapter)
    |
    v
Mind Palace resolver
    |-- temporal state
    |-- CURRENT/history/supersession
    |-- conflicts
    |-- authored evidence
    `-- bounded Memory Pack
```

**Decision models may influence retrieval selection, but only Mind Palace
determines memory state.**

The implementation is `api.services.memory_decision_spike`. It is not imported
by production query execution and has no Jev or network dependency.

## Provider contract

`DecisionProvider` exposes typed `choice`, `score`, and `probability` methods.
`DeterministicDecisionProvider` is repeatable and CI-safe. `JevDecisionProvider`
is only an injected duck-typed adapter; a Jev SDK/API is deliberately not a
dependency of this repository.

The trace returned by `bounded_decision` is an evaluation artifact, not an
authored memory claim. A future M007.x decision trace could support debugging,
reproducibility, policy comparison, and drift detection, but no persistent
schema is justified by this spike.

## Development and frozen evaluation

The existing development corpus contains 40 additional hand-authored questions
plus the original benchmark workload (79 labeled cases), and the spike adds
offline adversarial provider tests. Together they cover current, historical,
temporal, multi-topic, conflict, provenance, unrelated, near-neighbor, and
prompt-injection cases. The frozen 60-question set remains unchanged and is
used only for final comparison.

The offline deterministic experiment is a boundary/safety experiment, not a
claim that a heuristic emulates Jev. A real Jev run requires an external client
and should be added as an optional adapter job, never as a CI prerequisite.

## Results and interpretation

M006.75 remains the production baseline: 47/60 frozen exact/full correctness,
10/10 temporal, 10/10 abstention, zero false-current promotions, zero
provenance failures, zero budget failures, and deterministic repeated decisions.

The deterministic provider tests demonstrate explicit confidence, bounded
abstention, repeatability, and prompt-injection-safe treatment of corpus text.
On a shadow replay of the unchanged frozen rows, the provider made 120 typed
decisions (two per question), selected an expected subject key in 54/60 cases,
and abstained on 16 cases, including 6 relevant cases. This is not an end-to-end
quality improvement: the shadow input only contained the baseline's already
selected keys, and its false-negative abstention behavior is unacceptable.
It does not establish that Jev improves retrieval quality. No production
pipeline change or frozen-artifact change is warranted from this spike alone.

## Differentiation

Mind Palace's differentiated substrate is the authoritative, portable archive:
authored claims, immutable evidence, validity intervals, supersession,
historical reconstruction, conflict preservation, snapshots/replay,
deletion/restoration lifecycle, and REST/SDK/MCP/CLI access. A decision model
can be swapped or removed without changing those semantics.

That boundary is materially different from treating a decision model as a
memory store or using it as a generated-answer reranker.

## Recommendation

**INCONCLUSIVE — KEEP AS RESEARCH SPIKE**

The provider boundary is promising for explicit uncertainty and policy
observability, but no Jev-backed quality or cost improvement has been measured.
Keep M006.75 deterministic and consider an optional Jev comparison only after a
real offline/replayable Jev client and a separately frozen development trace
format are available.
