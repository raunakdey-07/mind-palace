# Held-out v2 — protocol

**Status:** PROTOCOL FROZEN. Question set NOT YET POPULATED.
**Frozen at:** commit `7983caf8d8728a2d7f5f1e1a0c0d5e5b1f2a3c4` (branch `milestone/v0.8.0-dev`)
**Author of record:** this protocol is machine-checkable; the *questions* must be
supplied by the project owner. See §8.

---

## 1. Why this set exists

Held-out v1 (157 questions, hash `13030d897faabe4be…`) is **no longer blind**. Its
26 failures drove three conclusions that now shape the architecture:

1. the candidate-recall ceiling is 0.8643 on v1 (`evidence/212`);
2. 17 of 19 unreachable cases had no lexical bridge (`evidence/212`);
3. the cause of that was a tokeniser defect, `[a-z][a-z0-9]+`, which discarded
   every numeric identifier (`evidence/213`).

The tokeniser fix was **discovered by reading v1**. Therefore any number measured on
v1 after that discovery is contaminated by construction and cannot be reported as
generalisation. v2 exists to provide a set that predates the fix and has never been
inspected.

This is the reason the tokeniser fix is **not yet shipped**, despite measuring
+10 rescued / 0 regressed on v1.

## 2. Corpus identity

| Field | Value |
|---|---|
| Corpus | `scripts/benchmark/m0115_dataset.py::build_corpus` — unchanged from v1 |
| Corpus hash | `9cdecda73cbdc8ae` (with question set) / corpus-only hash to be recorded at freeze |

The corpus is **deliberately unchanged**. Changing it would confound a wording
effect with a document effect and would make v1 and v2 incomparable. v2 tests
generalisation of *question wording*, which is the open question.

If a second corpus is added later it must be a **separate** evaluation with its own
hash, never mixed into v2.

## 3. Question schema

```python
Question(
    qid: str,                  # "hv2-<category>-<nn>", unique across the set
    category: str,             # one of §5
    text: str,                 # the question as a user would type it
    key: str | None,           # authored key the answer must resolve to, or None
    expect_state: str,         # CURRENT | HISTORICAL | CONFLICTING | ABSENT
    expect_values: tuple,      # substrings that must appear in the pack
    forbid_values: tuple,      # must NOT be the sole current answer
    gold_evidence_ids: tuple, # canonical evidence ids, or () if not yet mapped
    answerable: bool,          # False => scored as UNSCORABLE for recall
    note: str,
)
```

`Question` reuses `m0115_dataset.Question` so the scorer is identical. The two
extra fields (`gold_evidence_ids`, `answerable`) live in a parallel mapping in the
v2 module so the shared dataclass is not modified.

## 4. Gold requirements

**Retrieval gold is the authored key.** Keys are stable across runs and corpus
shapes; claim ids are content-addressed and change on every ingest, so they are
recorded as evidence provenance, not as the retrieval label.

| Expectation | Requirement |
|---|---|
| `answerable=True` | must have `key` set, and the key must exist in `build_corpus()` |
| `expect_state="ABSENT"` | `key is None`; scored on abstention only, never recall |
| `expect_state="CONFLICTING"` | key must be one with a known contradiction in the corpus |
| gold evidence | `gold_evidence_ids` populated by running the scorer once **before** freeze |

A question whose gold cannot be defended is marked `answerable=False` and reported
as **UNSCORABLE**. It is never assigned an invented label.

## 5. Categories and target counts

| Category | Target | What it probes |
|---|---:|---|
| `temporal_state` | 4 | as-of / valid-at state |
| `version_update` | 4 | what changed between versions |
| `why_decision` | 5 | rationale for an authored decision |
| `supersession` | 3 | what replaced what |
| `conflict` | 3 | contradictory sources |
| `cross_document` | 3 | facts spanning two documents |
| `relationship` | 3 | dependency / ownership chains |
| `identifier` | 3 | version numbers, incident ids, tier ids |
| `negative_evidence` | 2 | a named fact that is *not* recorded |
| `abstention` | 2 | plausible-sounding but unanswerable |
| **total** | **~32** | |

The `identifier` category exists because of the tokeniser finding. It is the only
category whose wording may quote an exact token, and it must probe identifiers the
dev set does **not** quote in that form.

## 6. Leakage rules

1. **No tuning against v2.** No threshold, candidate K, RRF constant, router rule,
   tokeniser, or prompt may be chosen by looking at v2 results.
2. **Configuration freeze precedes the v2 run.** The chosen configuration is
   committed and its hash recorded *before* v2 is executed.
3. **One run.** v2 is executed once for the frozen comparison. A rerun requires a
   new explicitly defined evaluation cycle, recorded as such.
4. **v2 is never added to.** A change to any question invalidates the hash and
   requires a new version tag (`v3`).
5. **The author of the questions must not be the retrieval implementation.** No
   generated question is authored by, or derived from, retrieval code.

## 7. Metrics

Identical to the frozen protocol in `docs/research/m009-retrieval-protocol.md`:

- Recall@1, Recall@5, Recall@10
- MRR, nDCG@5 (binary relevance — one gold key; no invented grades)
- abstention accuracy on `ABSENT` questions
- candidate count
- **candidate recall ceiling** = 1 − (gold in neither gate / answerable questions)

Bootstrap 95% CIs: percentile, 2000 iterations, **seed 12345**, so every run
reproduces its own intervals.

Latency is reported but is **not** a claim while the envelope is NOT ESTABLISHED.

## 8. Population procedure — OWNER-AUTHORED, NOT MACHINE-GENERATED

**Machine-generated questions are not acceptable here.** A retriever that helps
write its own test set cannot be evaluated by it, and the project's own rules
already forbid inventing "user-authored" questions.

The owner supplies the ~32 questions. To make that unambiguous, this repository
ships the **scaffolding only**:

- `scripts/benchmark/m013_heldout_v2.py` — schema, hash, freeze, scorer wiring,
  and `build_questions()` that returns `[]` until populated;
- explicit placeholders below, one per category.

```text
PLACEHOLDER — hv2-temporal_state-01 .. 04   (owner)
PLACEHOLDER — hv2-version_update-01 .. 04   (owner)
PLACEHOLDER — hv2-why_decision-01   .. 05   (owner)
PLACEHOLDER — hv2-supersession-01  .. 03   (owner)
PLACEHOLDER — hv2-conflict-01      .. 03   (owner)
PLACEHOLDER — hv2-cross_document-01 .. 03  (owner)
PLACEHOLDER — hv2-relationship-01  .. 03   (owner)
PLACEHOLDER — hv2-identifier-01    .. 03   (owner)
PLACEHOLDER — hv2-negative_evidence-01 .. 02 (owner)
PLACEHOLDER — hv2-abstention-01    .. 02   (owner)
```

**Draft guidance for the owner.** Indirect wording is the point: ask for the
*reason* without reusing the authored words ("what justified picking X" when the
claim says "was chosen for Y"), cross temporal boundaries, and quote identifiers in
forms the corpus does not use verbatim. Avoid restating a dev question with a
synonym — that tests paraphrase, which is a different and easier failure.

## 9. Freeze procedure

1. Owner populates `hv2_QUESTIONS` in `scripts/benchmark/m013_heldout_v2.py`.
2. Every question is validated: `answerable=False` unless the key exists in the
   corpus; `ABSENT` requires `key is None`.
3. `dataset_hash()` is computed over corpus + questions.
4. The hash is written to `docs/performance/heldout-v2-freeze.json` and committed.
5. The **retrieval configuration** is frozen and committed, with its own hash.
6. Only then is v2 executed, once.

```bash
PYTHONPATH=scripts/benchmark venvmp/bin/python scripts/benchmark/m013_heldout_v2.py freeze \
  --out docs/performance/heldout-v2-freeze.json
PYTHONPATH=scripts/benchmark venvmp/bin/python scripts/benchmark/m013_heldout_v2.py verify
```

`verify` re-computes the hash and fails if the set changed after freezing.

## 10. Interpretation rules

- v2 measures **unseen generalisation**; v1 measures diagnosis; dev measures
  development. They are reported separately and never combined.
- A tokenizer or architecture change is validated on **v2**, not v1.
- If v2 shows no improvement, that is a **negative result to preserve**, not a
  reason to re-tune against it.

## 11. Current status

| Item | State |
|---|---|
| Protocol | frozen (this document) |
| Scaffolding | `m013_heldout_v2.py` |
| Questions | **0 / ~32 — awaiting owner** |
| Freeze hash | not computed; `freeze` refuses on an empty set |
| Tokeniser fix | measured on v1 (+10/0), **not shipped**, blocked on this gate |