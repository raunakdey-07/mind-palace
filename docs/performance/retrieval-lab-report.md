# Retrieval reliability with a small embedder — this round's measurements

> ## READ THIS BEFORE ANY NUMBER BELOW
>
> **Arm A in this lab is not the product path.** It ranks with the shipped
> relevance scorer but grades at **rank 1** and has **no lexical fallback**. The
> product's own benchmark, using its own grader and its documented
> lexical-then-semantic fallback, reports **194/202**. This lab's arm `shipped-rank1` reports
> **117/202 correct** (151 answered, 33 of them wrong).
>
> So every figure below -- the harm matrix, the **36.4% ceiling**, every
> confident-wrong rate, every recall@k -- was measured **against arm `shipped-rank1`, not
> against Mind Palace**. They are properties of that arm under that grader.
>
> **They are NOT ESTABLISHED for the product.**
>
> The data is kept rather than deleted, because the differences are informative
> and because removing measurements is how a project loses the ability to notice
> it was wrong. What is wrong is the framing, and this banner is the correction.
>
> `docs/performance/retrieval-lab-protocol.md` is the frozen protocol, and
> `docs/STATUS.md` carries the numbers that *are* the product's.


Every number below was produced by a command run in this session. The artifacts
are `retrieval-lab-dev.json`, `retrieval-lab-heldout-v1.json`,
`retrieval-lab-gitderived.json` and `retrieval-lab-calibration.json`. The lab
protocol was frozen before any reported run
(`retrieval-lab-protocol.freeze.json`, sha256
`ff61071c08dcf0d846ea6febce45f2d92b3f7789c49dd642b5dbed2f4c3e128d`).

## The headline

**Partly.** A 22.7 M-parameter embedder plus a 22.7 M cross-encoder ranks well
and separates answerable from absent almost perfectly. It cannot be made
*reliable* at useful coverage, and the reason is not the gate.

| | value |
|---|---|
| best arm | **`fusion-rerank`** — fusion plus the shipped cross-encoder |
| dev recall@1 | 0.733 |
| dev MRR | 0.852 |
| dev answerable-vs-absent AUROC | 0.9961 |
| **max coverage at ≤2% confident-wrong** | **36.4%** |
| max coverage at ≤10% confident-wrong | 45.5% |
| proposed target | ≤2% confident-wrong at ≥70% coverage |

The target is **not reachable**, and the protocol says to report the ceiling
rather than move the target. It is 36.4%, not 70%.

## What the numbers say the limit is

A logistic gate fitted on nine features — cosine top-1 and margin, BM25 top-1
and margin, fused rank, cross-encoder score and margin, whether the top passage
is claim-backed, and whether three arms agree — reaches **14.2% coverage at ≤2%
confident-wrong**. That is *worse* than simply ranking by the raw
cross-encoder score (36.4%).

So the four hand-tuned gate parameters are not the bottleneck. **The features
are.** Everything available from a small embedder, combined and weighted by a
model fitted on 202 dev rows, is worse than one number from the reranker.

## Per-slice, because the average is not the story

recall@1, dev, 202 questions (176 answerable, 26 absent):

| slice | n | `shipped-rank1` | `bm25` | `semantic` | `rrf` | `fusion-rerank` | `passage` |
|---|---:|---:|---:|---:|---:|---:|---:|
| current | 30 | 0.933 | 0.600 | 0.933 | 0.767 | **1.000** | 0.567 |
| historical | 30 | 0.967 | 0.933 | 0.967 | 0.933 | **1.000** | 0.600 |
| provenance | 26 | 0.808 | 0.731 | 0.808 | 0.731 | **1.000** | 0.846 |
| adversarial | 19 | 0.444 | 0.278 | 0.444 | 0.278 | 0.667 | **0.944** |
| multi-topic | 23 | 0.696 | 0.087 | 0.696 | 0.348 | 0.217 | **0.739** |
| temporal | 13 | 0.538 | 0.385 | 0.538 | 0.385 | **0.615** | 0.385 |
| relationship | 34 | 0.471 | 0.441 | 0.471 | 0.412 | **0.500** | 0.176 |
| abstention | 25 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |

Three things fall out, and none of them is flattering to the shipped default:

1. **`relationship` is broken for every arm** (0.176–0.500). "Which service
   owns X" is the same class that produced the held-out v1 `F2` and `F9`
   failures. No ranking strategy touches it, which makes it a *representation*
   limit: the corpus does not encode ownership in a way a retriever can match.
2. **E collapses on `multi-topic`** (0.217, against 0.696 for the shipped arm)
   while being perfect on `current`, `historical` and `provenance`. The reranker
   sharpens single-answer questions and blurs multi-answer ones.
3. **F wins `adversarial` (0.944) and `multi-topic` (0.739)** and loses badly on
   `current` (0.567). Passage retrieval is not a better retriever; it is a
   *different* one, and the two fail on disjoint slices.

`conflict` has n=2 and is reported as "too few questions to report a rate"
rather than as a number.

## Why arm `shipped-rank1` and arm `semantic` are identical where it counts

At equal coverage they produce the same four-way breakdown: 100 correct, 23
wrong, 0 false answers, 53 abstained. The shipped relevance gate's topic
decomposition and `relative` filter change *which* keys rank where, but not
which questions get answered wrong.

## What F changes, and what it costs

Arm F indexes passages so body text can answer. 17 of 72 dev passages carry no
authored claim, and the dogfood's confident-wrong answer was exactly a body-text
answer. F produced **15 unbacked top-1 answers** on dev — body text winning
outright. It never promotes one to a claim, and that count is reported next to
every F result.

F is not a replacement. On the burned held-out v1 it fixes **12 of the 26**
questions the shipped system gets wrong, the most of any arm — but its
confident-wrong rate at equal coverage is 0.350 against E's 0.195.

| arm | fixes of 26 known-wrong |
|---|---:|
| `passage` | 12 |
| `semantic` | 9 |
| `shipped-rank1` | 6 |
| `rrf` | 4 |
| `bm25` | 2 |
| any arm | 13 |
| every arm | 1 |

Only **one** of the 26 is fixed by all five. They are not variations on one
system; they fail differently.

## Two measurement errors I made and corrected

**BM25 was measured wrong.** Scoring documents as *sets* gives every term
tf = 1. On claim representations averaging 8.6 distinct terms that produces
exact score ties — three different keys all at 6.91 for "what is the current
primary datastore". Ties broken by corpus order are not a ranking. With real
term frequency, recall@1 went from **0.029 to 0.528**. The first number was an
artefact of the inputs.

**`pip install mindpalace-os` is not 13 MB.** Last round reported it as 13 MB
with no alembic. The measurement was wrong: the virtualenv activation had
silently failed and pip installed into the base container venv, so the timing
was 0 s and the `du` read an empty directory. Measured properly: **211 s and
5.9 GB**, because the wheel declares `sentence-transformers`, which pulls torch.

## What is not established

* **Sealed v4.** Decision D2 was left unresolved and the only model credential
  has a `$0.00` ceiling. No independent author exists. Nothing here is
  evidence of generalisation.
* **Arm G, embedder vs pipeline.** `BAAI/bge-small-en-v1.5` is not in the local
  HuggingFace cache and `HF_HUB_OFFLINE=1`. Whether the ceiling is the
  embedder or the pipeline is **NOT ESTABLISHED**, and the cross-encoder result
  makes the question sharper rather than answering it: E is a 22.7 M reranker,
  not a 33 M embedder, so the comparison the round wanted was not run.
* **git-derived set.** Built and frozen: 234 questions, 214 with passage gold,
  20 absent, 1399 passages from 5 tags × 3 files. Passage-level only, because
  **0 of 66** of this repository's documents carry an authored `claims:` block.
  Arm F scores `passage_recall@1 = 0.0093` on it — a real and poor result, from
  a template-shaped set that is narrow by construction.
* **L2 rebuild digest.** Not re-verified this session. The frozen
  `baseline-packs.json` and the `eval/m00675` artifact were not touched.
* **Downstream value.** No model is reachable.


---

## Addendum, this round: retrieval harm, and what the "fixes" number hid

Last round reported how many questions each arm **fixes**. That is half a
number, and the half that flatters. Measured both ways against the shipped
default (arm `shipped-rank1`, correct on **117** of 202 dev questions at 85% coverage).

> ### INVALID — the harm matrix below, "every arm is net negative", and the old "43 questions differ" breakdown
>
> **Corrected 2026-10-13 (Phase 0b). Data kept; the framing is withdrawn.**
>
> **What the matrix was actually computed against.** Its own artifact says so.
> `docs/performance/retrieval-lab-harm-dev.json` carries
> `"baseline": "arm `shipped-rank1`, the shipped scorer"`, `"n": 202` and
> **`"baseline_correct": 151`**. That 151 is *not* 151 correct.
>
> `scripts/retrieval_lab/harm.py:60` defines the cell predicate:
>
> ```python
> def correct(run, question) -> bool:
>     answered = bool(run.scores) and run.scores[0] >= THRESHOLD
>     if question.answerable:
>         return bool(answered and run.keys[:1] == [question.key])
>     return not answered
> ```
>
> The `return not answered` branch makes **abstaining on an absent question
> count as correct**. So the four cells partition 202 questions on
> **"not wrong"**, a mixture of top-1 correctness and abstention behaviour, not
> on correctness.
>
> **The arithmetic, from the artifact's own `detail` rows, recomputed this
> session.** For every arm, `unchanged_correct + broken` — the number of
> questions arm `shipped-rank1` did not get wrong — is:
>
> | arm | unchanged_correct | broken | arm `shipped-rank1` "not wrong" |
> |---|---:|---:|---:|
> | `bm25` | 112 | 39 | **151** |
> | `semantic` | 141 | 10 | **151** |
> | `rrf` | 24 | 127 | **151** |
> | `fusion-rerank` | 119 | 32 | **151** |
> | `passage` | 103 | 48 | **151** |
>
> Five independent rows all landing on exactly 151 is not a coincidence; it is
> the baseline being reused. And 151 decomposes, against
> `retrieval-lab-dev.json` `arms.A.overall.at_threshold`
> (`answered: 178`, `correct: 127`, `wrong: 49`, `false_answer: 2`,
> `abstained_correctly: 24`), as **127 top-1-correct + 24 correctly abstained**.
>
> **So a `broken` cell means "arm `shipped-rank1` did not get this wrong, and the arm did" —
> which includes "arm `shipped-rank1` correctly abstained on an absent question and the arm
> answered it wrongly". A `fixed` cell includes the reverse.** The matrix
> measures a blend of two different failures, so "arm `fusion-rerank` breaks 32" cannot be
> read as 32 questions of knowledge the shipped system had and the reranker
> destroyed.
>
> **The correction made in Session 7 was itself wrong and is replaced here.**
> It claimed the matrix "was unaffected and was always right, because it is
> internally consistent: its own row for arm `bm25` reads 112 + 47 + 4 + 39 = 202,
> which puts arm `shipped-rank1` at 112 + 4 = 116 correct — consistent with the artifact's
> 117". Three errors in one sentence: `112 + 4` is arm **B's** count, not arm
> A's; arm `shipped-rank1`'s is `112 + 39 = 151`; and 116 is not "consistent with" 117 in a
> sentence whose subject is a count. Session 7 used the internal-consistency
> check that this project has relied on all along, and applied it to the wrong
> sum. The check was right; the arithmetic was not.
>
> **What arm `shipped-rank1` actually scores**, from `docs/performance/retrieval-lab-dev.json`:
>
> | operating point | threshold | answered | correct | wrong | on an absent q |
> |---|---:|---:|---:|---:|---:|
> | at 0.30 (shipped) | 0.3 | 178 | **127** | 49 | 2 |
> | at 85% coverage | 0.570753 | 151 | **117** | 33 | 1 |
> | at 70% coverage | 0.621167 | 123 | 100 | 23 | 0 |
>
> **"Every arm is net negative" is NOT ESTABLISHED as a correctness claim.** It
> is a true statement about a metric whose units are "not wrong". Recomputing
> it on correctness alone is Phase 5b, against an arm `shipped-rank1` that is the product
> path rather than this one.
>
> **The "43 questions differ" breakdown is INVALID.** It was recorded in
> `docs/PROGRESS.md` at commit `cc9ec5c` (Session 3): *"reconciled exactly: 43
> questions differ and the lab is right on zero that the benchmark calls wrong.
> 26 are top-1-vs-top-k (23 at rank 2, 3 at rank 4), 17 are the lexical-fallback
> path."* 43 = 194 − 151, so it was computed on the same false 151. The real gap
> is **194 − 117 = 77**. The 34 questions it never looked at are exactly the 33
> arm `shipped-rank1` answers marked wrong plus the 1 answer given on an absent question. It
> is kept here because it is a real prior claim about a real number, and a
> number that was wrong once should be traceable to where it was wrong.
>
> **The gap is not explained in this document.** Explaining which 77 questions
> they are, per question, is Phase 5a.

| arm | fixed | BROKEN | net | both right | both wrong |
|---|---|---|---|---|---|
| `bm25` | 4 | **39** | -35 | 112 | 47 |
| `semantic` | 0 | **10** | -10 | 141 | 51 |
| `rrf` | 2 | **127** | -125 | 24 | 49 |
| `fusion-rerank` | 14 | **32** | -18 | 119 | 37 |
| `passage` | 16 | **48** | -32 | 103 | 35 |

**INVALID as a statement about correctness — see the correction above. These are
counts of "not wrong", where a correct abstention on an absent question counts
as a win. The numbers are reproduced exactly and are not disputed; what they
mean is withdrawn.**

**Every arm is net negative at the shipped 0.30 threshold.** Arm E fixes 14 and
breaks 32. Arm F fixes 16 and breaks 48. The arm that looked best on the
one-sided count is the second worst here.

> **NOT ESTABLISHED as a correctness claim.** As written above this sentence is
> true of the mixed "not wrong" metric and is not evidence that any arm is worse
> than the shipped default at answering. The `both right` column is named for
> correctness and the `both wrong` column is not; the table does not separate
> them.

### Broken, per slice

> **INVALID on the same basis.** The per-slice split inherits the mixed metric.
> One row is arithmetically impossible under a correctness reading and shows it:
> arm `fusion-rerank` "fixes" 2 and "breaks" 1 on `abstention` — a slice made of absent
> questions, where "breaking" means answering something that should have been
> abstained on, and "fixing" means abstaining where the shipped arm answered.

| slice | n | `bm25` fix/broken | `semantic` | `rrf` | `fusion-rerank` | `passage` |
|---|---:|---|---|---|---|---|
| current | 30 | 1/11 | 0/0 | 0/28 | 2/1 | 2/13 |
| historical | 30 | 0/1 | 0/0 | 0/29 | 1/5 | 1/12 |
| multi-topic | 23 | 1/15 | 0/0 | 0/16 | **1/12** | 1/0 |
| relationship | 34 | 1/2 | 0/0 | 0/16 | 2/2 | 2/12 |
| provenance | 26 | 1/3 | 0/0 | 0/21 | 5/5 | 1/0 |
| abstention | 25 | 0/1 | 0/9 | 2/0 | 2/0 | 0/7 |
| temporal | 13 | 0/2 | 0/0 | 0/7 | 1/1 | 0/2 |
| adversarial | 19 | 0/3 | 0/1 | 0/8 | 0/5 | **9/1** |

Two mechanisms, not two stories:

* **Arm E's multi-topic collapse is measured, not inferred**: on that slice it
  fixes 1 and breaks 12. The shipped default already answered them; the
  cross-encoder collapses a multi-answer question to its single best passage and
  the threshold then admits that passage confidently. It is a *precision*
  regression on exactly the slice where the shipped gate was doing the work.
* **Arm D breaks 127 of 202.** Its reciprocal-rank-fusion scores top out near
  2/61, so at a 0.30 threshold D abstains on almost everything the default
  answered. That is a units mismatch between an arm and a gate, not a property
  of fusion -- and it is why the lab reports equal-coverage operating points as
  well. D is still not competitive on ranking (MRR 0.756 against E's 0.852).

Arm F is the only arm with a slice where it is genuinely ahead: **adversarial,
9 fixed against 1 broken**. That is a real effect and the most promising single
result in the study. It is also net −32 overall.

## Cross-validation of the ceiling

Five-fold CV on dev, grouped by authored claim key so paraphrases of one fact
never straddle a fold (202 questions in 107 fact-groups, mean 1.9 paraphrases
each).

| | max coverage at ≤2% confident-wrong |
|---|---:|
| fit on all of dev | 14.2% |
| **out-of-fold, pooled** | **14.2%** |
| per fold | 8.8 / 24.2 / 33.3 / 51.7 / 13.6 % |

The pooled estimate does not inflate. The per-fold spread is wide at n≈40 per
fold, which is the honest reason not to read any single fold.

**The 36.4% ceiling is a different number and is not cross-validated.** It comes
from ranking by the raw cross-encoder score at a threshold chosen on dev — no
model is fitted, but the *threshold* is selected on the same data, and that
selection optimism is unquantified here. Treat 36.4% as an upper bound and
14.2% as the cross-validated figure for the fitted rule.

## Reconciling "22 wrong answers"

The brief's 22 = 14 + 8. Both halves are **confident-wrong only**, excluding
abstentions, consistently:

| | answered wrong | abstained when answerable | total failures |
|---|---:|---:|---:|
| held-out v1 (157 q) | 14 | 12 | **26** |
| frozen M006.75 (60 q) | 8 | 5 | **13** |
| total | **22** | 17 | **39** |

So 22 is a coherent number — it is the count of *wrong answers given*, which is
the damaging kind — but it is not a count of all failures. Every count this
round uses is the 26 or the 39, and is labelled which.
