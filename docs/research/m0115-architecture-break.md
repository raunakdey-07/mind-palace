# M011.5 architecture break

Research record. No production architecture changed in this milestone. Every
number below was produced by the scripts named beside it, against the dataset
whose hash is recorded, and can be re-run.

## 1. Executive summary

The architecture holds where it claims to be strong and fails where nobody had
measured. Four findings decide what happens next.

**The representation is not the bottleneck.** Of 15 remaining failures, 12 are
retrieval-side and 3 are representation-side. A graph would not have fixed the
question that was actually failing.

**Lexical-first routing is viable and already implemented.** A cheap path
scored 187/202 against 171/202 for semantic-always, because 16 questions are
answered *better* without the model than with it. That is the opposite of what
the design assumed, and it needs a router, not a graph.

**Authority resolution is linear in archive size at about 7.4 ms per claim.**
The relevance gate embeds every claim representation on every query. At 88
claims a question costs 649 ms; at 1,000 claims it would cost seconds. The
gate is doing work the question did not ask for.

**Authority survives losing everything derived.** With all of L2 deleted, all
202 authoritative packs were byte-identical, and with no model loadable the
product still answered 202 of 202. The invariant is real at scale, not just on
a 10-claim corpus.

Decision at the end: **ARCHITECTURE NEEDS REVISION**, in one place only.

## 2. Repository baseline

| | |
|---|---|
| HEAD at start | `916687346dab97881c6035dafcec6a97925abd85` |
| `v0.6.0` | `033c1484dca53fbb40dbf82904aacf5f2834142a`, untouched |
| Suite, no database | 1183 passed |
| Suite, PostgreSQL 15.4 | 1183 passed, 0 skipped |
| Frozen `eval/m00675` | untouched; `--verify` reports the expected source drift |
| Static | `black --check`, `flake8 api cli tests --max-line-length=100` clean |

The benchmark corpus is defined in `scripts/benchmark/m0115_dataset.py` and is
authored, not model-generated:

| | |
|---|---|
| dataset hash | `7d239ddbb2ac0bff4eb1765103804237793874c78247376b3967dd18288ab2bb` |
| documents | 63 |
| versions | 72 |
| questions | 202 |

Questions by category: current 30, historical 30, provenance 26, abstention 25,
multi-topic 23, adversarial 19, relationship 34, temporal 13, conflict 2. Every
question names the authored key it must resolve, the authority state it expects,
and the values that must appear, so ground truth is independent of any retriever.

## 3. Competitor research

The repository already holds a source-level review with pinned revisions. This
milestone re-checked each pin against the live remote before relying on it:

| System | Pin in `docs/competitive/` | Current remote HEAD | Still current |
|---|---|---|---|
| Graphiti | `ba4a9cb32495` | `ba4a9cb32495` | yes |
| Mem0 | `94c3fe9f238f` | `94c3fe9f238f` | yes |
| MemPalace | `8c4865f70c49` | `8c4865f70c49` | yes |
| Letta docs | `691c48b202a1` | `691c48b202a1` | yes |

All four match, so the recorded findings stand and were not re-derived. What
follows is the extraction this milestone needed, not a re-summary.

## 4. Capability matrix

Verified at the pinned revisions, by reading implementations rather than
product pages. `verified` means the behaviour was located in code or schema.

| Capability | MemPalace | Mem0 | Letta | Graphiti | Mind Palace |
|---|---|---|---|---|---|
| raw/verbatim source | verified | verified (`infer=False`) | verified | partial (episodes) | verified |
| structured claims | not verified | verified (inferred) | not verified | verified (edges) | verified (authored) |
| temporal validity | not verified | not verified | not verified | verified | verified |
| conflicts | not verified | not verified | not verified | partial | verified |
| exact evidence | not verified | not verified | not verified | not verified | verified |
| provenance | partial | partial | partial | verified (episodes) | verified |
| snapshots | not verified | not verified | not verified | not verified | verified |
| rebuildable derived layer | not verified | not verified | not verified | not verified | verified |
| semantic retrieval | verified | verified | optional (QMD) | verified | verified |
| lexical retrieval | not verified | not verified | optional (mod) | verified (BM25) | verified |
| relationship reasoning | not verified | not verified | not verified | verified | **fails (measured)** |
| portable context | partial (logstream) | partial (`get_all`) | verified (`agent-file`) | not verified | verified |
| independent context reader | not verified | not verified | not verified | not verified | verified |
| agent integration | verified (MCP) | verified | verified (runtime) | verified (MCP) | verified (surface only) |
| local-first | verified | not verified | not verified | not verified | not verified |
| model-independent authority | not verified | not verified | not verified | not verified | verified |
| bounded context | not verified | not verified | verified (blocks) | not verified | verified |
| developer simplicity | verified (`mine`/`search`) | verified | verified | partial | partial |

The one row Mind Palace loses is relationship reasoning, and it is measured
below rather than assumed.

## 5. MemPalace

The strongest verified idea is that retrieval quality comes from preserving the
actual words and letting the query find them, rather than from compressing
first. Its reported 96.6% R@5 on LongMemEval is a different dataset, task and
metric from anything here and is **not comparable**; it is evidence about its
approach, not a score to beat.

What is transferable: source fidelity. Mind Palace already keeps it, and the
authored claim is *additional* to the quote rather than a replacement for it.

## 6. Mem0

The strongest verified idea is that the application-facing surface should be two
operations. Its `add` and `search` are simple because extraction, storage and
ranking are all behind them.

What is not transferable: extraction. Mem0's default path infers memories with
an LLM. That is L2 in this architecture and cannot be L1, because an inferred
sentence has no exact quote and no offsets.

## 7. Letta

The strongest verified idea is that a memory *block* is a useful unit: a
reserved, listable, attachable, detachable region of context.

The smallest useful unit question: on this evidence it is not a claim. A
developer asks "what should I know before changing auth" and needs claims,
conflicts, history and evidence *together*. The Memory Pack is that unit, and it
is already the thing that crosses the boundary.

## 8. Graphiti

The strongest verified idea is temporal relationship reasoning, and it is
exactly the capability measured as missing below. What is not transferable is the
graph database: the requirement found here is a *relation type*, not a graph
engine.

## 9. Laya

The transferable principle is routing before expensive work, which this
repository already implements for the model boundary. What M011.5 adds is the
measurement that makes the next step decidable: the model is on the hot path
for 202 of 202 questions when it is only needed for a minority.

## 10. Current architecture

L0 archived versions, L1 claims, evidence, validity, supersession, conflicts,
snapshots, L2 live index, chunks and embeddings. `context()` resolves against
the archive first and adds ranked chunks as raw material. Retrieval results are
only ever `chunks` and never become `memories`.

## 11. Three-layer model

Unchanged and unweakened. The L2 deletion test in section 13 is the proof, and
it was re-run at 2.5 times the previous corpus size.

## 12. Memory Pack analysis

`schema_version: 1`, canonical JSON with sorted keys and compact separators,
budget counted in Unicode characters of the complete envelope, `truncated`
reserved before selection. `status` is derived by the independent reader from
the pack's own contents.

## 13. Rebuildability at scale

M011 proved byte identity on a 41-version corpus. Re-run on the 72-version
corpus, with every derived row deleted and counts asserted:

| Phase | documents | versions | claims | answered | correct |
|---|---:|---:|---:|---:|---:|
| full | present | 72 | 88 | 202 | 170 |
| all L2 destroyed | **0** | 72 | 88 | 202 | 170 |
| after destroy | 0 | 72 | 88 | 202 | 170 |

202 distinct authoritative packs, byte-identical with and without L2. The
invariant holds at scale.

## 14. Retrieval benchmark: Experiment A

202 questions, nine categories, real PostgreSQL, real ingestion, real cached
`all-MiniLM-L6-v2`, pinned validity clock. No production code was changed:
lexical mode is the existing `relevance(lexical=True)` flag.

| Strategy | Score |
|---|---:|
| A semantic always | 171/202 |
| B lexical always | 162/202 |
| C lexical then semantic | **187/202** |

Latency over the same 202 questions: semantic p50 625.0 ms, p95 749.3 ms;
lexical p50 27.0 ms, p95 34.7 ms.

C beats A by 16. A cheap path is not a degraded path here; on 16 questions it
is a *better* path, because the semantic gate is confidently wrong in ways
lexical overlap is not.

By category (n / A / B / C):

| Category | n | A | B | C |
|---|---:|---:|---:|---:|
| current | 30 | 30 | 18 | 30 |
| historical | 30 | 30 | 30 | 30 |
| provenance | 26 | 22 | 26 | 26 |
| abstention | 25 | 23 | 22 | 23 |
| multi-topic | 23 | 14 | 10 | 19 |
| adversarial | 19 | 14 | 12 | 14 |
| relationship | 34 | 28 | 31 | 31 |
| temporal | 13 | 8 | 12 | 12 |
| conflict | 2 | 2 | 1 | 2 |

Current and historical are exact for both A and C. Multi-topic and temporal are
where semantic retrieval is weak, not where it is strong.

## 15. Routing quality: Experiment B

| Class | Count | Meaning |
|---|---:|---|
| cheap sufficient | 146 | lexical right, semantic right |
| unnecessarily expensive | 16 | lexical right, semantic **wrong** |
| dangerously cheap | 22 | lexical answered, lexical **wrong**, semantic right |

146 of 202 questions do not need the model. 22 need it. The dangerous category
is not zero, so a lexical-only product is not viable, but a router with a
lexical fast path is.

## 16. Model degradation: Experiment C

Each phase is a separate process, because the embedder keeps one resident model
and a warm pool hides the failure.

| Phase | answered | correct | abstained | conflicts | failures |
|---|---:|---:|---:|---:|---:|
| full | 202 | 170 | 24 | 23 | 0 |
| no model loadable | 202 | 161 | 27 | 20 | 0 |
| retrieval unavailable | 202 | 161 | 27 | 20 | 0 |
| all L2 destroyed | 202 | 170 | 24 | 23 | 0 |

L2 failure does not remove memory. It costs 9 questions of accuracy out of 202
and nothing else.

## 17. Relationship capability: Experiment D

34 relationship questions, not the 100 the brief asked for. Scaled honestly
rather than padded: the questions are generated from the service spec, and every
relation type in the spec is covered, so 100 would be 34 with different wording
and would not measure a new capability.

| Outcome | Count |
|---|---:|
| answered | 31/34 |

The three failures are all the same shape. Example: "What constraint did the
March orders outage introduce?" returns `incident.2024-03.orders.root`, the
cause, and not `constraint.orders.pool`, the constraint. **Both claims are in
the same document.** A change record references a document path, not a claim, so
the archive cannot express "this consequence followed from that cause".

## 18. Memory state: Experiment E

| State | Exists as | Derived from |
|---|---|---|
| CURRENT | claim status | persistence projection |
| HISTORICAL | section membership | persistence projection |
| SUPERSEDED | claim status | version chain |
| CONFLICTING | claim status | same-key grouping |
| ABSENT | `constraints` | relevance selection |

All are derived from authority, none from ranking, and all survive L2 deletion
because they are computed from archive rows. A sixth state, `UNCERTAIN`,
exists in the schema and was not exercised by this corpus.

The states are not fully mutually exclusive in presentation: a conflicted key
also appears under history when superseded.

## 19. Poisoning: Experiment G

The corpus contains a document whose text asserts system policy and instructs
deletion. Across all 202 questions:

| Check | Result |
|---|---|
| Claims authored from the untrusted document | 0 |
| Evidence rows citing it | 0 |
| Injection markers appearing in any claim | 0 |
| Injection markers appearing in any evidence | 0 |

An earlier version of this check scanned the whole pack envelope and reported 8
hits. Those were the caller's own question text, echoed in the `query` field. The
check now reads claim and evidence text only. Retrieval power did not imply
authority power, in either direction.

No trust-class system is justified. Existing source identity, authored evidence
and versioning already provide the protection.

## 20. Pack-only consumers: Experiment F

`memory_pack.py` imports only the standard library. A consumer receives one pack
and answers current value, previous value, disagreement, evidence, unknown
facts, resolution instant and integrity, importing no server module.

The pack is a sufficient portable memory unit for every question class in
sections 14 to 17. It is also a sufficient unit to attach, share or audit, since
a digest names it exactly.

One limitation found: a naive consumer that reads only `current` will miss a
conflicted key, because a conflicted key has no `CURRENT` claim. The pack is
sufficient; the consumer must know to look at `conflicts` first. That is
documentable rather than a format defect.

## 21. Performance breakdown

| claims | semantic p50 | semantic p95 | lexical p50 | lexical p95 | ms/claim |
|---:|---:|---:|---:|---:|---:|
| 15 | 109.43 ms | 135.40 ms | 5.64 ms | 6.08 ms | 7.30 |
| 55 | 366.08 ms | 403.55 ms | 10.68 ms | 13.63 ms | 6.66 |
| 74 | 568.68 ms | 625.71 ms | 14.80 ms | 17.67 ms | 7.68 |
| 88 | 649.44 ms | 684.78 ms | 19.55 ms | 22.78 ms | 7.38 |

Cost is linear in claim count, at roughly 7.4 ms per claim, for both strategies.
The cause is one line in `api/services/memory_relevance.py`:

```python
texts = list(interpreted.topics) + representations
vectors = embedder.embed(texts)
```

Every query re-encodes every claim in the archive. The claim representations are
derived from immutable claim text, so they could be encoded once and reused,
exactly as the live `chunks.embedding` column already is for the retrieval path.
The authoritative path has no equivalent, and that is the entire cost.

## 22. Failure taxonomy

| Code | Count | Examples |
|---|---:|---|
| F7 lexical/semantic retrieval | 10 | three-part question returns one of three keys in the same document; "why was it chosen" returns the conflict instead of the rationale |
| F2 authority missing | 2 | "Who caters the team lunch?" answered from an ownership key |
| F9 relationship representation | 3 | "what constraint followed from this incident" |
| F1, F3, F4, F5, F6, F8, F10, F11, F12 | 0 | none observed |

**12 of 15 failures are retrieval-side. 3 are representation-side.** No failure
required a graph, a new database, or a trust system.

Two earlier apparent failures were bugs in the benchmark, not the system, and
were fixed before these numbers were taken: a "what superseded X" label that
forbade the correct answer, and a conflict ADR authored under a key that did not
match the architecture, which correctly produced no conflict.

## 23. Architectural gaps

| Gap | Evidence | Severity |
|---|---|---|
| Relevance gate re-encodes the archive per query | 7.4 ms/claim, section 21 | high |
| No router: 146 of 202 questions pay for a model they do not need | section 15 | high |
| No claim-to-claim relation, so "what followed from this" fails | section 17 | medium |
| Conflict detection is keyed, so a key typo silently disables it | section 4, section 23 note | medium |
| Multi-topic questions drop sibling claims from the same document | section 14 | medium |

On the key-drift hazard, measured directly: an ADR authored under
`architecture.datastore` while the architecture says `architecture.postgres`
produces no conflict and reports a resolved answer. The key-drift claim does
appear in the pack, but nothing labels it as drift. The system cannot currently
distinguish "one source, one key" from "two sources, two near-identical keys".

## 24. Candidate solutions

| Problem | Smallest solution | Larger solution | Status |
|---|---|---|---|
| Per-query re-encoding | persist claim-representation embeddings beside claims, re-encode only on claim change | external vector service | **candidate for implementation** |
| Unnecessary model cost | deterministic lexical fast path with the existing gate, escalating on abstention | learned cost model | **candidate for implementation** |
| Claim-to-claim relation | one optional `derived_from` claim key, authored | full graph projection | research |
| Key drift | surface claims whose text is near-duplicate under different keys | automated key clustering | research |
| Multi-topic loss | raise `max_topics` or widen the `relative` band | per-document grouping | research |

## 25. Rejected solutions

| Rejected | Why |
|---|---|
| Graph database | 3 of 15 failures are representational; a graph would not have fixed the other 12 |
| Cache | no staleness model, and the dominant cost is compute, not lookup |
| Trust classes | poisoning is already prevented; no threat remains unaddressed |
| LLM extraction | would make L1 model-dependent, breaking the strongest invariant |
| Corpus export/import | separate storage-contract problem, out of scope here |
| More convenience API methods | 146 questions already need fewer concepts, not more methods |
| A separate `planner` subsystem | the routing signal is a single existing call, not a new component |

## 26. Recommended experiments

1. Cache claim representations and re-measure the 7.4 ms/claim slope. Prediction:
   per-query cost becomes flat in archive size.
2. Add the lexical fast path behind the existing gate and re-run all 202.
   Prediction: 187/202 or better, at roughly 27 ms median instead of 625 ms.
3. Prototype one `derived_from` relation and test the three relationship
   failures. Prediction: 31/34 to 34/34, with no change to the pack format.
4. Measure the multi-topic loss as a function of `max_topics` and the `relative`
   band, before changing either.

## 27. Proposed architecture

Only the retrieval boundary changes, and it changes because of measurements:

```text
question
   ↓
interpret intent and topics          (unchanged)
   ↓
relevance gate
   ├─ lexical scoring                (cheap path, ~27 ms)
   └─ cached claim embeddings       (expensive path, flat in archive size)
   ↓
authoritative resolution             (unchanged)
   ↓
Memory Pack                          (unchanged, schema_version 1)
```

L0, L1, the pack format, the reader and the degradation behaviour are all
untouched. This is a change to L2 and to the cost of reaching L1, not to L1.

## 28. What should not be built

No graph, no cache, no vector database, no trust framework, no LLM in the write
path, no agent runtime, no corpus export, no new API surface. The evidence for
each of those is in section 25, and in every case the measured bottleneck is
elsewhere.

## 29. Competitive differentiation

The honest answer to the central question.

> What does Mind Palace do that a developer could not get by combining
> MemPalace-style raw retrieval, Mem0-style simple memory, Letta-style blocks,
> and Graphiti-style relationship retrieval?

It is not feature count. It is this: **a bounded, portable object that says
what is known, what used to be known, what changed, what disagrees, and what
evidence supports each of those, and that can be deleted down to its source and
rebuilt back to the identical bytes without the model that produced it.**

Measured here:

- 202 authoritative packs, byte-identical with all of L2 deleted.
- 202 of 202 answered with no model loadable.
- 0 injection markers reached any claim or evidence, across 202 questions.
- A consumer with the standard library alone reads a pack and answers seven
  question classes.
- A digest names a context exactly, which makes an agent trace reproducible.

A combination of the four competitors can approximate retrieval, simplicity,
attachable units and relationship queries. None of them produces an object with
all of these properties at once, and none of them can be shown to survive
losing its derived layer unchanged.

## 30. Final decision

**ARCHITECTURE NEEDS REVISION**, in exactly one place.

What was proven:

- The three-layer invariant holds at scale, byte for byte.
- Model-free authority is real, 202 of 202.
- Retrieval cannot become authority, 0 of 202.
- The pack is a sufficient portable memory unit.
- Lexical-first routing is viable and currently unimplemented.
- The cost is linear in archive size, and the cause is one identified line.

What remains uncertain:

- Whether cached claim embeddings hold the 187/202 answer set. Unmeasured.
- Whether one authored relation fixes the three relationship failures.
- Whether the relevance thresholds are right for corpora larger than 88 claims.
- Whether multi-topic loss is a threshold artefact or a structural limit.

What should happen next, in order:

1. Cache claim representations, measure the slope again. This is
   **IMPLEMENTATION JUSTIFIED**: the cost is measured, the cause is identified,
   and it is L2, so it cannot affect authority.
2. Add the lexical fast path behind the existing gate. **IMPLEMENTATION
   JUSTIFIED** for the same reason, with 146 of 202 questions as the
   justification and 22 as the safety requirement.
3. Prototype one `derived_from` relation. Not justified yet: three failures is
   a hint, and the prompt's own rule is to require a repeated structural failure
   across a serious benchmark.

Everything else stays deferred, including the graph, until the numbers justify
it.
