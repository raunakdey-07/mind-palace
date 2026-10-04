# M013 — Measured Retrieval & Agentic Memory Engineering

**Truth Ledger.** Written before any feature work, per the milestone rule that no
change is justified by intuition. Every claim below is either backed by a tracked
artifact or explicitly labelled unverified.

Branch `milestone/v0.8.0-dev`. Baseline commit `d825f06`.

---

## Verified facts

Backed by tracked evidence. Each names its artifact.

| Fact | Evidence |
|---|---|
| Authoritative output is byte-identical across the ANALYZE fix: 202/202 packs, set digest `5a3b8049…` | `evidence/195-verify-packs-after-commit.txt` |
| The benchmark harness is deterministic: two captures at `dbdc35f` reproduce `5a3b8049…` exactly | `evidence/200-harness-determinism.txt` |
| The pack comparator is not vacuous: mutated claim text, validity, conflict record and ordering each produce a non-zero exit naming the question | `evidence/04-mutation-M1..M4.txt` |
| Full suite passes: **1206 tests** | `evidence/` + suite runs |
| Dev benchmark: semantic 178/202, lexical 164/202, lexical→semantic **194/202** | `m0115-experiments.json` |
| Held-out v1: **131/157 (83.4%)**, failures `{F2:5, F7:19, F9:2}` | `m0130-heldout.json` |
| Held-out v1 is scored once and then analysed; its failures have shaped diagnosis | `docs/research/m009-retrieval-protocol.md` |
| The L2 rebuild **invariant** holds (12/12 at all three phases) but the **absolute digest is not run-stable** | `evidence/197-l2-digest-instability.txt` |
| The archive-load plan cliff is caused by *absent* planner statistics, not stale ones; ANALYZE gives 58.1x at 500 versions, 104.6x at 1000, ~1.3x at 1500/2000 | `evidence/183-cliff-reverified-summary.txt`, `196-scale-check.txt` |
| The regression test fails for the intended reason when the hook is stubbed out | `evidence/194-mutation-proof.txt` |
| Every artifact cited by STATUS.md is tracked; the checker is non-vacuous and passes | `scripts/check_evidence.py`, `evidence/171-…` |

### Two structural findings that shape this milestone

Read from source, not assumed:

1. **`rank_claims` in `api/services/memory_query.py` is dead code.** It implements
   `lexical`, `embedding` and `hybrid` (RRF, k=60), but its only callers are
   `tests/test_memory_query.py` and `memory_query_benchmark.py`. Production
   `select()` calls `relevance()` directly, which has exactly two modes —
   `lexical=True` (token overlap) or embedding. **Fused hybrid retrieval does not
   exist in the production path today.** Anything called "hybrid" in prior
   experiments was the unused helper.

2. **There is no PostgreSQL full-text search.** `grep -ri "tsvector\|tsquery"
   migrations/` returns zero hits. Keyword matching is pure-Python set intersection
   in `memory_relevance.relevance()`, scored per candidate in Python, not in the
   database. So "keyword retrieval" today is neither PostgreSQL-native nor index-
   assisted.

These mean the milestone's headline comparison (vector / keyword / hybrid / RRF /
rerank) is **not** measuring existing configurations for most rows — it is
building them. That is the honest framing.

---

## Measured: the retrieval matrix

`evidence/210-retrieval-matrix.txt`, artifacts `m013-retrieval-matrix-{dev,heldout}.json`.
Gold is the authored key; ABSENT questions are scored on abstention only.

| Configuration | R@1 | R@5 | R@10 | MRR | nDCG@5 | abstention | mean candidate keys |
|---|---:|---:|---:|---:|---:|---:|---:|
| **dev (175 keyed, 26 abstention)** | | | | | | | |
| current | 0.722 | 0.869 | 0.869 | 0.791 | 0.811 | 0.923 | 1.81 |
| semantic | 0.722 | 0.869 | 0.869 | 0.791 | 0.811 | 0.923 | 1.81 |
| lexical | 0.500 | 0.790 | 0.795 | 0.621 | 0.663 | 0.885 | 2.57 |
| hybrid (RRF) | 0.722 | 0.869 | 0.869 | 0.791 | 0.811 | 0.923 | 1.81 |
| semantic→lexical | 0.722 | 0.869 | 0.869 | 0.791 | 0.811 | 0.923 | 1.81 |
| **held-out v1 (140 keyed, 16 abstention)** | | | | | | | |
| current | 0.582 | 0.808 | 0.808 | 0.682 | 0.714 | 0.938 | 2.36 |
| semantic | 0.582 | 0.808 | 0.808 | 0.682 | 0.714 | 0.938 | 2.36 |
| lexical | 0.291 | 0.660 | 0.709 | 0.454 | 0.500 | 0.938 | 3.77 |
| hybrid (RRF) | 0.582 | 0.808 | 0.808 | 0.682 | 0.714 | 0.938 | 2.36 |
| semantic→lexical | 0.582 | 0.808 | 0.808 | 0.682 | 0.714 | 0.938 | 2.36 |

Bootstrap 95% CIs (2000 iterations, seed 12345). For dev `current`, R@1
CI = [0.653, 0.784]; for held-out R@1 CI = [0.497, 0.667]. The dev-to-held-out
drop is large and the intervals do not overlap, which is the generalisation gap in
its clearest form.

**Retrieval quality, not just answer accuracy:** semantic retrieval finds the gold
key 87.4% of the time on dev and 81.4% on held-out, ahead of lexical at 80.0% and
71.4%. Held-out R@1 of 0.582 against dev 0.722 is the number that matters.

### Two findings that redirect the milestone

**1. Fusion as implemented is inert, but fusion in principle is not.**
`hybrid` and `semantic→lexical` are *bit-identical* to `semantic` on both datasets.
My first hypothesis was that the gate prunes to ~2 keys so RRF has nothing to
reorder. **That hypothesis was wrong**, and the diagnostic
(`m013_fusion_diagnostic.py`) disproved it:

| | dev | held-out |
|---|---:|---:|
| semantic misses (gold absent) | 22 | 26 |
| **recoverable by fusion** (gold present as a *lexical-only* key) | **16** | **7** |
| mean semantic candidates | 1.81 | 2.35 |
| mean lexical candidates | 2.52 | 3.80 |
| mean union candidates | 3.03 | 4.28 |

So the union is meaningfully larger than either gate alone, and on **16 of 22**
dev misses (and 7 of 26 held-out misses) the gold key is already in the lexical
set but was pruned by the semantic gate. **Fusion cannot help while it is applied
*after* the gate has discarded the candidates it would recover.** The measurement
identifies a real, bounded opportunity: fuse *before* pruning, or union the two
gates' candidate sets and then rank. That is the next experiment.

**2. Semantic retrieval costs ~1,400 ms p50; lexical costs ~1 ms.** Same corpus,
same process, same warm cache. The embedding of uncached claims dominates. This is
consistent with prior findings that the relevance gate, not the inner product, is
the cost. Any fusion design must account for this: naive fusion roughly doubles an
already-slow path.

### Not established

- Whether pre-gate fusion actually improves end-to-end quality. The diagnostic
  establishes *headroom*, not *achievability*. No fusion change has been made.
- Graded relevance for nDCG@5. With one gold key, nDCG@5 reduces to a rank
  discount. That is the honest form; no graded labels were invented.
- Cross-encoder reranking has not been measured. No reranker exists in the tree.

---

## Measured: pre-gate fusion (the headroom test)

`evidence/211-pregate-fusion.txt`, artifacts `m013-pregate-fusion-{dev,heldout}.json`.
The union arms admit a key if **either** gate would have, then rank by RRF. This is
the change under test; `semantic_only` is the shipped behaviour.

| arm | dev R@1 | dev R@5 | dev MRR | held-out R@1 | held-out R@5 | held-out MRR |
|---|---:|---:|---:|---:|---:|---:|
| lexical only | 0.503 | 0.794 | 0.625 | 0.293 | 0.664 | 0.457 |
| **semantic only (shipped)** | **0.726** | **0.874** | **0.796** | **0.586** | **0.814** | **0.687** |
| union, RRF k=10 | 0.651 | **0.966** | 0.788 | 0.457 | **0.843** | 0.621 |
| union, RRF k=60 | 0.651 | **0.966** | 0.788 | 0.471 | **0.843** | 0.628 |

Bootstrap 95% CIs (seed 12345) are in the artifact and evidence file. RRF k made
almost no difference: k=10 and k=60 agree to within one question on both sets.

**What fusion buys:** recall at depth. R@5 rises 0.874 → 0.966 on dev and
0.814 → 0.843 on held-out. Unrecoverable misses fall from 22 to 6 on dev.

**What fusion costs:** precision at rank 1. R@1 *falls* on both sets (0.726 →
0.651 dev; 0.586 → 0.471 held-out), and MRR falls with it. The reason is visible in
the candidate counts: the union carries 3.03 keys on dev and 4.28 on held-out
against semantic's 1.81 and 2.35, and the extra keys are inserted near the top,
displacing the correct one from first place.

### The honest read: this is a recall/precision trade, not a win

Fusion as measured **is not a default**. It trades rank-1 accuracy for rank-5
recall, and the shipped behaviour is already the better rank-1 ranker. Anyone
needing more than ~3 facts from one query would gain; anyone needing the single
best fact would lose.

Two findings that matter more than the trade itself:

1. **The two datasets disagree about how much is recoverable.** Dev has 16
   recoverable misses out of 175; held-out has only 7 out of 140, with **19 of
   140 questions where the gold key is in neither gate at all**. Fusion cannot
   reach those 19 by any means. That is the generalisation limit, and it is not a
   ranking problem.

2. **Dev overstates the opportunity.** 16 recoverable vs 7 recoverable is the same
   phenomenon as 194/202 vs 131/157. Tuning fusion on dev would have produced a
   confident recommendation built on a set that does not generalise.

### Decision: REJECT fusion as the default, on this evidence

Not because fusion is useless, but because the default serves the common case
(best single fact) better, and the held-out set — the one that generalises — shows
only 7 recoverable cases and 19 that no fusion can reach.

The R@5 gain is real and worth keeping as an **opt-in breadth mode**, which is a
future decision rather than a change to make now. Before that, a reranker on the
union candidate set is the more promising experiment: it would let the extra
candidates compete on relevance rather than on rank fusion, which is precisely
what fails here.

No production retrieval code has been changed. All of the above is measured through
the shipped `relevance()` gate; the union arms are harness-side.

---

## Measured: the candidate-recall ceiling (the milestone's central question)

`evidence/212-candidate-ceiling.txt`, `m013_candidate_ceiling.py`.
This asks *why* the remaining questions are unreachable, and answers with measurement
rather than intuition.

| | dev (175 keyed) | held-out (140 keyed) |
|---|---:|---:|
| reachable by **both** gates | 124 | 93 |
| reachable by **semantic only** | 29 | 21 |
| reachable by **lexical only** (fusion can recover) | 16 | 7 |
| **UNREACHABLE** (neither gate) | 6 | 19 |
| **candidate recall ceiling** | **0.9657** | **0.8643** |

Of the unreachable cases:

| | dev | held-out |
|---|---:|---:|
| gold claim missing from the projection entirely | **0** | **0** |
| **zero lexical bridge** to the gold claim | 2 | **17** |
| lexical bridge present but still gated out | 4 | 2 |

### What this settles

**The candidate-recall ceiling is 0.8643 on held-out.** No reranker, no RRF, no
fusion can exceed it, because reranking reorders candidates that were generated.
The 19 unreachable questions are **not a ranking problem**.

**The cause is vocabulary, and it is measured, not inferred.** In 17 of the 19,
the query and the gold claim share **zero** content terms after the shipped
stopword and stemming pass. Examples:

| question | gold claim | shared terms |
|---|---|---:|
| "Shipping 0.6 introduced what?" | "Release 0.6 added the durable operational feed." | 0 |
| "What rule did the 2024-07 incident put in place?" | "The notifier requires an idempotency key per message." | 0 |
| "What went wrong in 2024-07 that affected notifier?" | "The July duplicate SMS incident was caused by non-idempotent consumers." | 0 |

"introduced" vs "added", "put in place" vs "requires", "went wrong" vs "caused". The
question and the authored text are about the same fact in different words. The
lexical arm *cannot* bridge this by construction, and the semantic arm did not
either.

**Nothing is missing from the archive.** `gold_missing_from_projection` is 0 in
both datasets: every gold key exists in projected memory. This is a *representation
and query-vocabulary* gap, not a data gap.

### Why dev overstates this, again

Dev's ceiling is 0.9657 against held-out's 0.8643, and dev has only 2 zero-bridge
cases against held-out's 17. **The development questions are phrased in vocabulary
that overlaps the authored text; the unseen ones are not.** This is the same
phenomenon as 194/202 against 131/157, and it is now explained rather than merely
observed. Any retrieval tuning done against dev will keep looking effective while
the actual defect is untouched.

### What follows, in order of measured expected value

1. **A deterministic query-side bridge is the highest-value change.** 17 of 19
   unreachable cases need *some* link between "introduced" and "added", "rule" and
   "constraint", "went wrong" and "caused". That is a lexical-normalisation and
   concept-bridge problem, and it is testable without an LLM.
2. **Reranking cannot help here.** It ranks candidates; 17 of the failures never
   produce a candidate. Reranking is still worth measuring for the 21
   semantic-only and 7 lexical-only cases, but it cannot move the ceiling.
3. **Dev must not be used to validate the fix.** The held-out set is already
   analysed, so a v2 protocol is required before claiming improvement.

Do not interpret "zero lexical bridge" as licence to add an LLM query expander.
The cheapest test is deterministic: measure whether a documented synonym set over
the corpus's own vocabulary recovers these before considering anything heavier.

---

## Measured: the cause is a tokeniser defect, not a vocabulary gap

`evidence/213-numeric-tokens.txt`, `m013_numeric_tokens.py`.

Inspecting the 17 zero-bridge cases showed something sharper than missing synonyms.
`memory_relevance.terms()` tokenises with `[a-z][a-z0-9]+`, which **requires a
leading letter**. Every version number and incident identifier is therefore
discarded before any comparison happens:

```
"Shipping 0.6 introduced what?"                  -> ['introduc', 'shipp']
"What rule did the 2024-07 incident put in place?" -> ['incident', 'place', 'put', 'rule']
```

A question naming a specific release becomes indistinguishable from one naming any
release. This is a defect in the shipped tokeniser, not a property of the question
wording.

### Two variants rejected by measurement before the third was adopted

| tokeniser | held-out | dev | why |
|---|---:|---:|---|
| `[a-z0-9]+` | **+10** | **−4** | admits the bare pronoun "I", which enters the query denominator with nothing to match |
| `[a-z0-9][a-z0-9]+` | +3 | 0 | keeps a 2-char minimum but splits "0.6" into "0" and "6" |
| `[a-z][a-z0-9]+\|\d+(?:\.\d+)?` + mutual-presence gate | **+10** | **0** | **adopted** |

The gate is the important half: an identifier is dropped from the denominator
unless it appears on **both** sides. Without it, a question mentioning "2024-03"
against a claim that merely says "orders service" pays for a year it can never
match. That single detail is the whole difference between +10/−4 and +10/0.

### Result

| | held-out | dev |
|---|---:|---:|
| gold reachable at the shipped 0.30 floor, before | 97 (0.6929) | 162 (0.9257) |
| after | **107 (0.7643)** | 162 (0.9257) |
| rescued / regressed | **+10 / 0** | 0 / 0 |

All ten rescues are attributable to identifiers (`0.6`, `2024`, `07`, tier numbers).
No question on either dataset loses reachability. Lexical recall at the gate floor
rises **0.6929 → 0.7643** on held-out with zero cost anywhere.

### What this means for the milestone

The candidate-recall ceiling was diagnosed as 0.8643 with "vocabulary, not
ranking" as the cause. **The ceiling is partly a tokeniser bug.** Fixing it moves
the ceiling without any new architecture, dependency, or model. This is exactly
the kind of change the evidence should select over a reranker or a hybrid stack,
and it is why the ceiling analysis was worth doing before the reranking experiment.

Still not established, and deliberately not claimed:

- **No production change has been made.** This is a measured candidate against the
  shipped tokeniser.
- Held-out v1 is analysed, so this number is a *development* result until held-out
  v2 exists. It cannot be reported as generalisation.
- The fix targets identifier-bearing questions specifically. The remaining
  zero-bridge cases are genuine paraphrase gaps and are untouched by it.

---

## Measured: PostgreSQL FTS arm — built, measured, REJECTED

`evidence/221-fts-arm.txt`, `m013_fts_arm.py`.

Built for real: a GIN `tsvector` index over key + claim + value + path, on a
**separate derived table**. Not a column on `memory_claims`, because PostgreSQL
forbids a subquery in an index expression and `path` lives on `memory_documents`.
No authoritative table was altered; `ANALYZE` ran before measuring so the planner
was not itself the variable. Index size 48 kB.

| arm | dev R@1 | dev R@5 | held-out R@1 | held-out R@5 | mean candidates |
|---|---:|---:|---:|---:|---:|
| **python lexical (shipped)** | **0.514** | **0.897** | **0.321** | **0.793** | 80.0 |
| FTS `english` | 0.320 | 0.371 | **0.000** | **0.000** | 1.13 / 0.0 |
| FTS `simple` | 0.000 | 0.000 | 0.000 | 0.000 | 0.0 |

**Rejected.** But the reason is more useful than the number, and it was verified
rather than assumed.

### The mechanism

`plainto_tsquery` ANDs every token the configuration does not discard:

```
simple   'What is the current primary datastore?' -> 'what'&'is'&'the'&'current'&'primari'&'datastor'
english  'What is the current primary datastore?' -> 'current'&'primari'&'datastor'
```

`english` removes stopwords and stems, which is why it works at all. But AND
semantics still requires *every* remaining token to appear in the claim. Measured
against the built index:

```
'primary datastore'          -> 'primari' & 'datastor'           ->  6 matches
'current primary datastore'  -> 'current' & 'primari' & 'datastor' -> 0 matches
```

**One word — "current" — present in the question and absent from the claim — takes
six matches to zero.** A question is not a bag of terms drawn from the document
that answers it, and any arm that mechanically translates a question into an AND
query inherits that.

Lifting the AND was tested too: `websearch_to_tsquery` (OR semantics) also returned
0 matches on the same queries, so the blocker is not only the operator.

### What this means for the milestone

FTS here is not a weaker ranker, it is a **non-functional candidate generator for
natural-language questions**. Making it work would require question-aware query
construction — stopwords tuned to questions rather than documents, or OR-then-rank
with a relevance floor. That is real engineering, and this evidence does not
justify it: the shipped Python lexical arm already reaches R@5 0.897 / 0.793.

This also retires the hybrid-RRF question in its PostgreSQL form. RRF was rejected
earlier because it traded R@1 for R@5; combining a *non-functional* lexical arm with
a semantic arm cannot improve on that.

### Not established

- Whether question-aware FTS query construction would work here. Not tested; not
  justified on this evidence.
- Latency is reported for context only and is not a claim while the envelope is NOT
  ESTABLISHED.

---

## EXECUTED: held-out v2, and the tokenizer verdict

`evidence/230-tokenizer-v2-generalization.txt`. Frozen at `b6ff4f7`, corpus hash
`9cdecda7`, question hash `5cf098908d1a0ead…`, 32 questions across all ten
categories. Frozen and committed **before** any benchmark ran.

### Baseline on genuinely unseen questions

| metric | point | 95% CI (seeded bootstrap) |
|---|---:|---|
| R@1 | 0.4286 | [0.250, 0.607] |
| R@5 | 0.6429 | [0.464, 0.821] |
| R@10 | 0.6429 | [0.464, 0.821] |
| MRR | 0.5298 | [0.357, 0.691] |
| nDCG@5 | 0.1896 | [0.132, 0.243] |
| candidate recall ceiling | 0.6429 | — |
| abstention accuracy (4) | 0.500 | — |

28 answerable, 4 abstention, 10 unreachable. The 10 unreachable are dominated by
indirect why/identifier questions: 4 of 5 `why_decision`, 2 of 3 `identifier`, and
one each from temporal_state, supersession and cross_document. **The weakness v2
was built to probe is real and reproduces on unseen data.**

### The tokenizer verdict: REJECTED

| dataset | shipped lexical recall | candidate | rescued | regressed |
|---|---:|---:|---:|---:|
| dev (175) | 0.9314 | 0.9314 | 0 | 0 |
| **v2 blind (32)** | **0.5714** | **0.5714** | **0** | **0** |

**The +10 improvement measured on v1 does not generalize.** Lexical candidate recall
is identical in both arms on both datasets.

The fix is not broken. Tokenisation verified token by token — `"2024-11"` yields no
digits under the shipped pattern and `[11, 2024, …]` under the candidate. The
defect is real and the candidate repairs it.

**The benefit was an artefact of how v1 was worded.** In v2 the identifier questions
carry content words that already reach the claim ("constraint", "deployment",
"release"), so the numeric token adds nothing. The v1 rescues were the opposite
shape:

```
'Shipping 0.6 introduced what?'    tokens = [introduc, shipp]
'What went wrong in 2024-03 ...'    tokens = [affect, order, went, wrong]
```

No content word from the gold claim. The identifier was the *sole* bridge — exactly
the case the fix addresses, and exactly the case a naturally-worded question rarely
is.

**So the v1 result measured a property of the question set used to discover it, not
a property of the retrieval system.** This is the clearest justification yet for the
project's own rule: a benchmark that has informed a fix can no longer measure it.

### Decision: DO NOT SHIP

Null on unseen data, and it would add a parameter plus a gating rule to production
for no measured benefit. Per the shipping criterion — *"if v2 shows no improvement,
revert/reject"* — it is rejected.

Recorded rather than erased: the tokenizer defect is genuine, and a future set
containing identifier-only questions would benefit. No such set exists outside the
one that produced the finding.

### What v2 says the remaining problem actually is

The blind ceiling of 0.6429 is the number that matters, and it is a **representation
and query-understanding** problem, not a tokenisation one. Ten questions are
unreachable because the question and the gold claim share no retrievable signal:

- `hv2-why_decision-02` "Why was the old caching layer replaced rather than kept?" →
  `"Redis was chosen for its native TTL and pub/sub support."` — no shared content term
- `hv2-temporal_state-04` "Which component handles background messaging in production?" →
  `"The job queue is RabbitMQ."` — the gold claim does not contain "messaging" or "component"

These need the authored representation or the query to carry the relationship, which
is a larger change than a tokeniser edit and is the next thing to characterise.

---

## EXECUTED: the representation-gap study

`evidence/240-representation-gap.txt`, `m013_representation_gap.py`.

For every blind failure, the question asked is narrow: **is the information the
question needs already in the authoritative corpus, just not on the claim that
answers it?** Four deterministic representations, all built from data already in
L0/L1, no model:

| representation | v1 reachable | v2 reachable |
|---|---:|---:|
| claim only (shipped) | 0/19 | 2/10 |
| + evidence | 8/19 | 3/10 |
| + document context | 8/19 | 4/10 |
| + evidence + context | **8/19** | **4/10** |

**Deterministic re-projection of data already in the archive closes 10 of 29 blind
failures, with no LLM.** The information was authoritative all along; it was exposed
on the evidence row and the document rather than on the claim sentence.

Note for accuracy: **evidence is L1, not L2.** `memory_evidence` rows are written
inside `record_version` alongside the claim, and the rebuildability invariant covers
them. So a representation that reads evidence is reading authoritative state, which
is a stronger position than reading derived state.

### The residual splits into two mechanisms needing different answers

**Mechanism 1 — the identifier is authored, but never tokenised.** The author *did*
encode it, in the key and the path:

```
ho-chg-inc-2024-07  'What went wrong in 2024-07 that affected notifier?'
   key  incident.2024-07.notifier.root   key overlap ['notifier']
   path ops/incidents/2024-07-notifier-duplicate-sms.md
ho-ev-inc-2024-03  'What rule did the 2024-03 incident put in place?'
   path ops/incidents/2024-03-orders-connection-pool-exhaustion.md
```

This is the tokenizer defect, confined to questions where the identifier is the
*only* bridge. 9 of 19 v1, 2 of 10 v2.

**Mechanism 2 — the vocabulary exists nowhere in the authoritative record.**

```
hv2-temporal_state-04 'Which component handles background messaging in production?'
   claim 'The job queue is RabbitMQ.'
   query terms [background, component, handle, messag, production] — none appear
hv2-why_decision-02 'Why was the old caching layer replaced rather than kept?'
   claim 'Redis was chosen for its native TTL and pub/sub support.'
```

No deterministic re-projection can close these, because the words are not in L0/L1.
4 of 5 v2 `why_decision` failures are here. 2 of 19 v1, 6 of 10 v2.

### Counts

| set | closed deterministically | identifier-only | vocabulary absent |
|---|---:|---:|---:|
| v1 (19) | 8 | 9 | 2 |
| v2 (10) | 2 | 2 | 6 |

### What this rules out

- **Not an LLM-extraction problem for most of the gap.** Deterministic re-projection
  closes 10 of 29 blind failures by itself.
- **Not purely a retrieval problem.** Mechanism 2 is unreachable by *any* ranking,
  because no arm can propose a candidate whose text shares no term with the query.
- An LLM would only address mechanism 2, and would do it by generating vocabulary
  that is **not in the corpus**. That is a representation-authoring decision, not a
  retrieval one, and it must be chosen explicitly rather than drifted into.

### EXECUTED: the deterministic representation was implemented and rejected

`evidence/250-contextual-l2-gate.txt`.

Implemented exactly the measured change — representation = claim + key + path + the
claim's own authoritative evidence, versioned, deterministic, with
`representation_hash` covering the rule version so old embeddings cannot be reused.

- Full suite **1225 passed** (1206 + 19 new tests)
- flake8 / black clean
- **Authoritative gate: FAILED — 63 of 202 questions differ**, digest `5a3b8049…`
  → `ceae31b0…`

The failure is semantic, not cosmetic. Folding evidence into the representation
raises what clears the relevance floor, so an **abstention** question now returns
memories instead of declining:

```
abs-sso   baseline: runbook.identity.escalation, service.identity.depends_on,
                    service.identity.owner, service.identity.tier
          current : service.identity.tier, service.identity.depends_on,
                    service.identity.owner
```

### The trap worth remembering

| | dev benchmark |
|---|---:|
| before | 194/202 |
| after | **197/202** |

**The score improved by 3 while 63 questions' authoritative output changed,
including abstention.** Had the criterion been the aggregate, this would have
shipped as a 1.5% win that quietly answers unanswerable questions. Reverted.

### What this establishes

The study's 10/29 is **not reachable by this route without weakening abstention.**
The gate's floor was calibrated against the narrower representation, so widening
the searchable text widens what is accepted.

A future attempt must either keep evidence out of the *lexical* gate and use it
for embedding only — smaller, and leaves abstention thresholds untouched — or
recalibrate abstention deliberately and re-prove it on the abstention class, which
is a semantic decision, not a performance one. Option (a) is the obvious next
attempt and was not tried here.

### EXECUTED: evidence-aware embedding is DISPROVEN (score-only diagnostic)

`evidence/260-semantic-channel-diagnostic.txt`.

Before implementing anything, the semantic channel was measured in isolation:
`S_old = cos(query, claim+key+path)` against
`S_new = cos(query, claim+key+path+evidence)`, 88 candidates, nothing selected on
the new score.

| dataset | gold newly clears floor | mean margin change | gold up / down |
|---|---:|---:|---:|
| dev | 0 | **−0.0031** | 81 / **94** |
| v1 | 1 | **−0.0036** | 49 / **91** |
| v2 | 0 | **−0.0020** | 10 / **18** |

**Evidence-aware embedding makes the semantic channel slightly worse.** Gold
scores fall more often than they rise on every dataset, and the gold-vs-nearest-
negative margin moves negative on all three.

The mechanism is dilution. Appending evidence lengthens every candidate, and a
sentence embedding compresses longer inputs toward the mean, so all similarities
converge and separation is lost.

### The structural finding, which matters more

The brief's premise — that the lexical gate is a separate acceptance stage that can
be held fixed — **is not what the code does**:

```
memory_relevance.py:274-282   score():  lexical -> token overlap
                                  else    -> cosine(topic, claim)
memory_relevance.py:301-303   gate E:
    accepted = value >= 0.30 and (overlap or value >= 0.65 or isclose(value, 0.30))
```

In production (`lexical=False`) `value` **is** the semantic cosine, and it **is**
the acceptance threshold. `overlap` is lexical overlap computed from the *same*
`representations` list. The representation is the input to both the cosine that
decides acceptance and the lexical term that also decides it.

**There is no way to enrich the embedding while holding the acceptance boundary
fixed, because the acceptance boundary is the embedding.** This is why the earlier
contextual-L2 change moved 63 packs, and why separating two functions cannot work:
they were never separate.

That also refines the earlier diagnosis: the 63-pack regression was not only
`overlap` widening — the cosine floor itself moved.

### Decision: REJECT

Milestone case B — semantic scores do not improve and no true candidates become
reachable. Production retrieval is untouched.

The offline study's 10/29 was measured on **lexical token overlap**, where extra
terms genuinely add signal. It does not transfer to a bi-encoder, where extra text
dilutes. The two channels must not be conflated again.

---

## EXECUTED: the acceptance boundary characterised — decision B

`evidence/270-acceptance-boundary.txt`, `m013_acceptance_boundary.py`. Research
only; the gate was transcribed from source and evaluated, not changed.

### The constants have no documented rationale

`docs/research/retrieval-evaluation.md:138` lists 0.30 / 0.90 / 0.65 as chosen
configuration with no justification, and is itself stale (it says vectors are
recomputed per query with no cache, untrue since `8c5891f`). No commit, test or
research note states why these numbers. **They are unjustified constants.**

### Confusion matrix

| dataset | TP | FP | TN | FN | absent false accepts | correct abstentions |
|---|---:|---:|---:|---:|---:|---:|
| dev | 170 | 6 | 0 | 0 | 2 | 24 |
| v1 | 129 | 9 | 3 | 0 | 1 | 15 |
| v2 | 21 | 4 | 3 | 0 | 2 | 2 |

**FN is zero everywhere.** The gate never rejects the gold claim. Every failure is
a false positive or a false abstention — it is permissive, not selective.

### Absolute cosine does not separate gold from non-gold

| dataset | gold median | negative median | gold below best negative |
|---|---:|---:|---:|
| dev | 0.6373 | 0.1642 | **95.4%** |
| v1 | 0.5846 | 0.1540 | **97.8%** |
| v2 | 0.5135 | 0.1445 | **100%** |

Gold ranks well (median ~4x the negative median) but sits deep inside the negative
range: 42% (dev), 63% (v1), 28% (v2) of negatives score above the worst gold.

### 0.65 is vestigial

| dataset | recall @0.30 | recall @0.65 |
|---|---:|---:|
| dev | 1.000 | 0.568 |
| v1 | 0.992 | 0.315 |
| v2 | 1.000 | 0.185 |

It only survives in production because `overlap` is an OR-escape, so **the gate's
real work is done by lexical token overlap.** Cosine contributes ranking and a floor
that never fires negatively.

### Margin is not a separator

Margin medians 0.049 dev / 0.029 v1 / 0.029 v2, and compresses on unseen data
(v2 max 0.0852 vs dev max 0.3346). Separation degrades as the corpus hardens —
the opposite of a reliable acceptance signal.

### Decision B — ranking and acceptance must be separated

Cosine is a usable **ranking** signal and a poorly calibrated **acceptance**
signal. Using it as both is why every representation experiment moved 63 packs.

No threshold is proposed. A single-threshold replacement was swept and only trades
FP against FN along one dataset-sensitive curve; with FN=0 today and an explicit
preference for abstention over unsupported output, any replacement needs an
objective the project has not yet written down.

**The vocabulary gap remains an authoring problem, not a threshold problem.**

---

## Known limitations

- **Held-out v1 is no longer blind.** Its 26 failures drove diagnosis. Any number
  produced by tuning against it is a development number. Held-out v2 protocol
  required before any generalisation claim.
- **34 known failures are unclassified**: 26 held-out v1 + 8 dev. They are *not*
  all retrieval failures; several are representation gaps (no authored relation
  exists) or benchmark ambiguity.
- **L2 absolute digest instability** — harness defect, predates this work. Quote
  the invariant, never the digest.
- **Retrieval is not separated from authority by an interface.** `relevance()`
  returns key scores that `select()` consumes; the boundary is real but implicit.
  There is no test asserting that a retrieval change cannot alter authority.
- **Latency envelope is NOT ESTABLISHED.** The cause of ~2.5x run-to-run wall-clock
  variance has never been identified. No number should be called a latency
  guarantee. Prefer deterministic metrics (shared buffers, call counts, rank).
- **Extraction does not exist.** Claims are authored in document front matter
  (`m0115_dataset._claim`) or by the ingestion parser. No automated extraction,
  no LLM claim proposal. Consequence: arbitrary real-world prose cannot become
  authoritative knowledge without an authoring step.
- **No FTS index, no reranker, no router, no OTel** in the current tree.
- **Ingestion is synchronous and per-document transactional.** Idempotence and
  retry behaviour have not been measured.

---

## Rejected hypotheses — preserved, not erased

| Candidate | Result | Why it matters |
|---|---|---|
| Vectorised relevance scorer | 202/202 packs identical, **1.00x–1.07x** end to end | The inner product was never the cost. Kept behind a flag for the equivalence proof |
| Set-based archive-load rewrite | Buffer reads fell 1,057,320 → 2,475, but a **generic-plan cliff made it 12x worse** | Proves plans must be measured through the production driver, not psql |
| Naive candidate narrowing before projection | **170/202** equivalent answers | Currency, conflict and change records depend on state narrowing discards |
| Per-document current-version pointer | **3.6 ms of a 20,894 ms** query | Addressed a symptom that was not the bottleneck |
| Omitting chunk text (`chunk_text=False`) | Payload reduction only | The precedent `version_text=False` followed: kept for payload, **not claimed as a speedup** |
| Bloom-filter pushdown | Folded into the narrowing work | Superseded by the narrowing result |
| Graph storage for relationships | Not justified | Relationship failures are representation gaps, not a missing graph |
| Rewriting the archive-load query to dodge the plan cliff | Seven variants, all byte-identical, **none removes it** | With the search space narrowed to index scans only, the planner still chose wrong. Estimation failure, not search failure |
| `default_statistics_target` to fix the cliff | Byte-identical plan at every size | Inert: it only applies at the next collection, and nothing collected |
| Hybrid / RRF fusion as the default retrieval configuration **[M013]** | R@5 0.874 → 0.966 dev, 0.814 → 0.843 held-out, but R@1 **falls** 0.726 → 0.651 dev and 0.586 → 0.471 held-out | A recall/precision trade, not a win. The union inserts extra keys near the top and displaces the correct one from first place. Opt-in breadth mode is the plausible future use |
| "Fusion is inert because the gate prunes to ~2 keys" **[M013]** | **Wrong, and disproved.** The union is 3.03 keys on dev; on 16 of 22 dev misses the gold key was already in the lexical set but pruned by the semantic gate | Recorded because it was my own first hypothesis and it was wrong. The measurement that killed it is `m013_fusion_diagnostic.py` |
| Numeric tokeniser variant `[a-z0-9]+` **[M013]** | held-out +10, **dev −4** | Admits the bare pronoun "I" into the query denominator |
| Numeric tokeniser variant `[a-z0-9][a-z0-9]+` **[M013]** | held-out +3, dev 0 | Splits "0.6" into "0" and "6", losing the version identity |
| "The 17 zero-bridge failures are a vocabulary gap" **[M013]** | **Mostly wrong.** They are a tokeniser defect: `[a-z][a-z0-9]+` discards every version and incident identifier | The adopted variant recovers 10 of 10 with zero regressions |
| The tokenizer fix generalises **[M013]** | **No. Rejected.** +10 rescued on held-out v1, **0 rescued / 0 regressed on frozen held-out v2**. Lexical recall identical in both arms on both datasets | The v1 benefit measured how v1 was *worded*, not the retrieval system. In v2 identifier questions carry content words that already reach the claim, so the numeric token adds nothing |
| An LLM-derived L2 representation is needed for the representation gap **[M013]** | **Not justified yet.** Deterministic evidence + document context closes 8/19 v1 and 2/10 v2 blind failures with no model | The remaining 8 failures are vocabulary absent from L0/L1 entirely. An LLM would invent it, which is an authoring decision, not a retrieval one |
| Deterministic contextual L2 representation (claim + key + path + evidence) **[M013]** | **Implemented and REJECTED.** 63 of 202 authoritative packs changed; an abstention question started answering | The dev score *rose* 194→197, so an aggregate criterion would have shipped a semantic regression as a win. Evidence in the representation widens what clears the relevance floor, which was calibrated against the narrower text |
| Evidence-aware embedding with an evidence-free lexical gate **[M013]** | **REJECTED on a score-only diagnostic; never implemented.** Gold scores fell more often than they rose on all three sets; margin change −0.0031 dev, −0.0036 v1, −0.0020 v2 | Also structurally impossible as specified: in production `value` IS the semantic cosine AND the acceptance threshold, so the acceptance boundary cannot be held fixed while the embedding changes. Appending evidence dilutes a sentence embedding rather than sharpening it |
| The 0.30 / 0.65 relevance constants have a documented rationale **[M013]** | **No.** The only mention lists them as configuration without justification, and is stale. FN is 0 across all three sets, so the 0.30 floor never rejects gold | 0.65 alone gives recall 0.19–0.57; it survives only via the `overlap` OR-escape. The gate's real work is done by lexical token overlap |
| A single cosine threshold can replace the current gate **[M013]** | **REJECTED.** 95–100% of gold scores fall below the best negative for their own question, so no threshold separates returnable from not-returnable | The sweep only trades FP against FN along one dataset-sensitive curve, and the product's cost asymmetry (abstain > unsupported memory) has never been written down as a decision |
| PostgreSQL full-text search as the lexical arm **[M013]** | **Non-functional on natural-language questions.** R@5 0.371 dev / 0.000 held-out against the shipped Python lexical arm's 0.897 / 0.793 | `plainto_tsquery` ANDs every token, so one word present in the question and absent from the claim takes 6 matches to 0. OR semantics (`websearch_to_tsquery`) also returned 0 |

**Explicitly retracted:** the earlier claim that a "loaded host" or "2.4x–2.8x
background load" explained latency variance. A diagnostic showed 21% utilisation
and 87–94% idle CPU. The cause remains **not established**. Do not resurrect it.

---

## The separation this milestone must preserve

```
L0  source / verbatim          immutable
L1  authoritative knowledge    claims, evidence, validity,
                               supersession, conflicts, snapshots
L2  derived retrieval          embeddings, caches, indexes, ranking
```

Retrieval may **reorder** candidates. It may never create a claim, alter validity,
erase a conflict, rewrite provenance, or suppress a contradiction. A reranker
reorders. It does not decide truth.

The test for this milestone is `202/202` byte-identical packs across every change
that touches ranking.
