# Retrieval reliability with a small embedder — this round's measurements

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
| best arm | **E** — fusion plus the shipped cross-encoder |
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

| slice | n | A shipped | B BM25 | C semantic | D RRF | E +rerank | F passage |
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

## Why arm A and arm C are identical where it counts

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
| F (passage) | 12 |
| C (semantic) | 9 |
| A (shipped) | 6 |
| D (RRF) | 4 |
| B (BM25) | 2 |
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
default (arm A, correct on 151 of 202 dev questions):

| arm | fixed | BROKEN | net | both right | both wrong |
|---|---|---|---|---|---|
| B | 4 | **39** | -35 | 112 | 47 |
| C | 0 | **10** | -10 | 141 | 51 |
| D | 2 | **127** | -125 | 24 | 49 |
| E | 14 | **32** | -18 | 119 | 37 |
| F | 16 | **48** | -32 | 103 | 35 |

**Every arm is net negative at the shipped 0.30 threshold.** Arm E fixes 14 and
breaks 32. Arm F fixes 16 and breaks 48. The arm that looked best on the
one-sided count is the second worst here.

### Broken, per slice

| slice | n | B fix/broken | C | D | E | F |
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
