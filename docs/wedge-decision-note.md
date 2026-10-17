# Wedge decision note

**2026-10-17. §5.2 is overtaken.** This note recommended building the independent
question set first and *not* building an extractor. A structural extractor now
exists (`api/services/markdown_claims.py`) and runs as a fallback when a document
carries no authored `claims:` block. It needs no model, so §4's "not established:
the cost, because pricing it needs a credential" is answered by measurement rather
than by a budget: plain Markdown with list items, numbered entries and `Key: Value`
lines now produces claims with exact-substring evidence, and the exact-quote rule
is unchanged. §2.1 rows 2 and 3 are fixed for the Markdown row; a conversation
transcript is prose and still produces nothing, which is the case §4 says an
extractor of this kind would not reach either. Everything below is the note as
written on 2026-10-13.

**2026-10-13. A recommendation, not an implementation.** Nothing in this note is
applied. No route, command, tool, schema field, default or tag changes because of
it. The owner decides.

Every number here came from a command run in the session named beside it. Where
something is not established it says so rather than estimating.

---

## 1. The candidate wedge

**What is true now, what changed, why, and proof of it — for decisions and facts
kept in git-tracked documents.**

Not conversation memory. Conversation memory is the thing three competitors are
already good at, it needs an extractor this project does not have, and it is the
one input shape where the product currently stores text and answers nothing
(§2, measured).

## 2. What is established

### 2.1 Ingestion inventory — Phase 4a, this session

`scripts/wedge/ingestion_inventory.py`, one disposable corpus, one document per
shape, `DATABASE_URL` pointed at a throwaway PostgreSQL 15.4 container.
"Passages" means rows in `memory_evidence`, read through
`api/services/memory.py:evidence`. "Recall" means the product's own `query`
operation against that corpus.

| input shape | claims written | passages | chunks | recall |
|---|---:|---:|---:|---|
| document with `claims:` frontmatter | **2** | **2** | 1 | answered, correct |
| plain markdown, no frontmatter | **0** | **0** | 1 | **abstained** |
| five-session conversation transcript | **0** | **0** | 6 | **abstained** |

Three findings, in order of how much they cost.

**(a) A document with no authored claims is stored and cannot answer anything.**
This is not a threshold tuning problem. `passages_stored` is 0 for two of three
shapes: there is no evidence row, so there is nothing for a claim to point at, so
`current` returns nothing and `query` abstains.

**(b) The transcript's answer was in the text, verbatim, and still abstained.**
Session 5 of the transcript reads *"Checkout retries a failed payment twice
before giving up."* The question asked was *"How many times does checkout retry
a failed payment?"* The document was chunked into 6 chunks, the chunk containing
that sentence was stored, and the product still returned nothing. A retrieval
system that ranks chunks would have answered. This one ranks **claims**, and
there were none.

**(c) The authoring rule is stricter than the documentation suggests, and it is
correct.** `api/services/memory.py:validate_source` requires that the claim text
appear verbatim in an archived chunk ("semantic inference is not supported") and
that `evidence` be an exact substring of one. Two drafts of the frontmatter
document were **rejected** before the third was accepted:

| attempt | rejected because |
|---|---|
| `evidence: "We moved checkout onto PostgreSQL in March."` against a body saying "We moved checkout onto PostgreSQL in March. Payment retries are capped…" | that *is* a substring — rejected on the **claim** side, because the claim read "The checkout service uses PostgreSQL." and those exact words are not in the chunk |
| paraphrased evidence | not an exact substring |
| accepted | claim text and evidence both verbatim in the chunk |

That rule is the product's whole trust claim and it should not be relaxed. The
cost is authoring, and §4 prices it.

### 2.2 The benchmark — Phase 0b, this session

The product's own benchmark reports **194/202** on the development set with its
own grader (`docs/performance/m0115-experiments.json`,
`summary.strategies.C_fallback`). The retrieval lab's own arm reports **117/202
correct** on the same 202 questions. The gap is **77 questions**.

That number is recorded here and **not explained**. The previous session
explained a gap of 43, computed as 194 − 151, where 151 was the number of
questions the lab arm *answered* rather than the number it got right. The
harm matrix underneath it was computed against a `correct()` predicate that
counts *abstaining on an absent question* as correct, so its baseline "151
correct" is 127 top-1-correct plus 24 correct abstentions — a metric whose units
are "not wrong", not "correct". The whole matrix, the "every arm is net
negative" claim and the "43 questions differ" breakdown are marked
**INVALID / NOT ESTABLISHED** in
`docs/performance/retrieval-lab-dev.json`, with the report since removed.

Explaining which 77 questions they are, per question, is Phase 5a and it needs
the lab measuring the product path. Anything said before that would be a guess,
and the guess is exactly what went wrong last time.

## 3. What an independent question set needs

The wedge's credibility rests on questions **we did not write**. A question set
authored by the same person who built the retriever, graded by a heuristic they
wrote, is a measurement of their own beliefs. Four properties, and what each one
buys:

| property | why | status |
|---|---|---|
| gold comes from a source we do not control | a supersession link in somebody else's ADR is either true or it is not, and we did not write it | **design only**, §5 |
| every gold answer is an exact quote from a public document | it is checkable by a script with no model in it, and a wrong gold is caught by running the check | feasible; the exact-quote rule already exists as a database trigger |
| answers are graded by string containment against that quote, not by a model | removes the grader as a source of disagreement and removes the only API key the evaluation would need | feasible |
| the set is frozen before it is scored, with a digest | a set edited after seeing results is not a set | the mechanism exists (`docs/performance/*freeze*.json`) |

**Without this, the wedge has no evidence.** Every number in §2 is on a question
set this repository wrote. The most valuable thing the next phase can produce is
not a better score; it is a gold set whose provenance is somebody else's.

## 4. The cost of an extractor that keeps the exact-quote rule

This is the number that decides the wedge, and it is the one number **not
established**.

**Not established: the cost of extracting claims from unstructured text under
the exact-quote rule.** No measurement exists, because no extractor exists. What
can be said from what was observed today:

* The rule is *satisfiable* on a real document. One frontmatter document was
  ingested and answered correctly, with both quotes landing in the same chunk.
* The rule is *not satisfiable by paraphrase*. Two of three authored drafts were
  rejected, and both rejections were the rule working correctly.
* The rule forces the claim text to be the document's words. An extractor that
  normalises a sentence, resolves a pronoun, or joins two clauses produces a
  claim that is **true** and **rejected**. So the extractor's output is
  constrained to a subset of the sentences already in the document — which is
  the point, and which is also why "extract the facts" is the wrong description
  of the job.

The shape of the cost, stated as a hypothesis and **not as a measurement**:
an LLM extractor would be asked to return, for each claim, a verbatim substring
of the input, with byte offsets. The verification step is already implemented
and already free (`validate_source` plus the trigger). The expensive part is the
generation step, and the failure mode is not "wrong answer" but "unverifiable
output", which the current pipeline rejects loudly.

**Pricing this needs a model.** The only credential this project has carries a
`$0.00` ceiling (recorded in Session 6 and Session 7, both `NOT ESTABLISHED`).
So the honest statement is: *the cost is one model call per claim candidate plus
a verbatim check per candidate, and nobody has measured either number.*

## 5. Recommendation

**Build the independent question set first. Do not build the extractor.**

The ordering is not a preference; it follows from what §2 measured and what §3
requires.

1. **The wedge's risk is not ingestion, it is evidence.** A wedge for
   "what changed and why" is only interesting if somebody else agrees that the
   change list is right. Today the only question sets we have are ones we wrote,
   and our own benchmark disagrees with our own lab by 77 questions. Shipping
   the wedge on that evidence would be shipping a claim we cannot defend.
2. **The extractor is the expensive, irreversible decision.** It changes what a
   document *is*: a set of authored assertions becomes a set of machine-extracted
   ones, and the exact-quote rule — the product's central trust property —
   becomes a check the extractor has to pass rather than a property of the input.
   That should not be done before there is a question set that can tell whether
   the extractor helped or hurt.
3. **Ingestion of authored documents already works.** §2.1 row 1: 2 claims, 2
   passages, answered and correct. A wedge aimed at git-tracked documents with
   authored claims needs **no new ingestion code at all**. The remaining gap is
   entirely on the evaluation side.

### What to do next, in order

| # | step | cost | decides |
|---|---|---|---|
| 1 | Phase 5a: make the lab call the real product path, graded by the benchmark's own grader, and account for all 77 questions | no model, no network | whether our own two numbers are about the same system |
| 2 | Stand up the ADR supersession question set (§5 design in this note's follow-on) | scraping three public repos; no model | whether the wedge has independent evidence at all |
| 3 | Only then price the extractor | model access | whether conversation input is ever worth supporting |

Step 1 is free and blocks the credibility of everything else. Step 2 is bounded
and needs no model. Step 3 is the only one that needs a credential, and it is
last on purpose.

## 6. What this note does not establish

* **Blocked: LongMemEval (Phase 4c).** Requires a dataset fetch and a
  `workflow_dispatch`-only workflow; not started this session. No per-category
  counts, no baseline. **NOT DONE.**
* **NOT DONE:** the three ADR repositories with explicit "superseded by" status.
  This note states the *requirements* a gold set must meet (§3) and does not name
  repositories, because naming repositories whose supersession pairs have not
  been counted would repeat the error this whole session exists to stop.
* **NOT ESTABLISHED:** extractor cost, in calls or in currency (§4).
* **NOT ESTABLISHED:** whether any of this changes the 194/202 figure. Nothing
  here is a product change, so nothing here should.